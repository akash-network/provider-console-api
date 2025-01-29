import tempfile
import os
from fabric import Connection
from invoke.exceptions import UnexpectedExit, AuthFailure
from typing import Union, Tuple
from fastapi import status
from application.exception.application_error import ApplicationError
from application.model.machine_input import ControlMachineInput, WorkerNodeInput
from application.utils.logger import log
from application.utils.redis import get_redis_client
from application.config.mongodb import logs_collection

# Constants
SSH_TIMEOUT: int = 60
LOCAL_ADDR: Tuple[str, int] = ("", 0)


# Custom exception classes
class SSHAuthenticationError(ApplicationError):
    def __init__(self, message: str):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="AUTH_001",
            payload={
                "error": "Authentication Failed",
                "message": f"Authentication failed: {message}",
            },
        )


class SSHConnectionError(ApplicationError):
    def __init__(self, message: str):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="SSH_001",
            payload={
                "error": "SSH Connection Error",
                "message": f"SSH connection error: {message}",
            },
        )


def get_ssh_client(
    machine_input: Union[ControlMachineInput, WorkerNodeInput]
) -> Connection:
    """Establish an SSH connection to the specified machine."""
    log.info(f"Establishing SSH connection to {machine_input.hostname}")
    try:
        connection_params = _prepare_connection_params(machine_input)
        connect_kwargs = {
            "timeout": SSH_TIMEOUT,
        }

        if "key_filename" in connection_params:
            connect_kwargs["key_filename"] = connection_params["key_filename"]
            if "passphrase" in connection_params:
                connect_kwargs["passphrase"] = connection_params["passphrase"]
        elif "password" in connection_params:
            connect_kwargs["password"] = connection_params["password"]

        connection = Connection(
            host=connection_params["hostname"],
            user=connection_params["username"],
            port=connection_params["port"],
            connect_kwargs=connect_kwargs,
        )
        # Test the connection
        connection.open()
        log.info(f"SSH connection established to {machine_input.hostname}")
        return connection
    except AuthFailure as auth_ex:
        log.error(
            f"SSH authentication failed for {machine_input.hostname}: {str(auth_ex)}"
        )
        raise SSHAuthenticationError(str(auth_ex))
    except Exception as e:
        log.error(f"SSH connection error for {machine_input.hostname}: {str(e)}")
        raise SSHConnectionError(str(e))


def _prepare_connection_params(
    input: Union[ControlMachineInput, WorkerNodeInput]
) -> dict:
    """Prepare SSH connection parameters based on the input."""
    log.debug(f"Preparing SSH connection parameters for {input.hostname}")
    connection_params = {
        "hostname": input.hostname,
        "port": input.port,
        "username": input.username,
        "timeout": SSH_TIMEOUT,
    }

    if input.keyfile:
        temp_file = _handle_keyfile(input.keyfile)
        connection_params["key_filename"] = temp_file.name
        if input.passphrase:
            connection_params["passphrase"] = input.passphrase
    elif input.password:
        connection_params["password"] = input.password
    else:
        log.error("Neither keyfile nor password provided for SSH connection")
        raise ApplicationError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="AUTH_002",
            payload={
                "error": "Authentication Error",
                "message": "Either keyfile or password must be provided",
            },
        )

    return connection_params


def _handle_keyfile(keyfile) -> tempfile.NamedTemporaryFile:
    """Handle the SSH keyfile by creating a temporary file."""
    log.debug("Handling SSH keyfile")
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    try:
        keyfile.file.seek(0)
        content = keyfile.file.read()
        temp_file.write(content)
        temp_file.close()
        log.debug(f"SSH keyfile saved to temporary file: {temp_file.name}")
        return temp_file
    except Exception as e:
        log.error(f"Error handling SSH keyfile: {str(e)}")
        os.unlink(temp_file.name)
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="KEY_001",
            payload={
                "error": "Keyfile Error",
                "message": f"Error handling keyfile: {str(e)}",
            },
        )


def run_ssh_command(
    connection: Connection,
    command: str,
    check_exit_status: bool = True,
    task_id: str = None,
    **kwargs,
) -> Tuple[str, str]:
    """Run an SSH command and return the output."""
    try:
        redis_client = get_redis_client()

        result = connection.run(command, warn=not check_exit_status, **kwargs)
        stdout_str = result.stdout.strip()
        stderr_str = result.stderr.strip()

        # Stream logs to Redis and MongoDB if task_id is provided
        if task_id:

            # Prepare logs for MongoDB
            logs_to_append = []

            if stdout_str:
                for line in result.stdout.splitlines():
                    # Add to Redis stream
                    redis_client.xadd(f"task:{task_id}", {"stdout": line})
                    # Prepare for MongoDB
                    logs_to_append.append(
                        {
                            "type": "stdout",
                            "message": line,
                        }
                    )

            if stderr_str:
                for line in result.stderr.splitlines():
                    # Add to Redis stream
                    redis_client.xadd(f"task:{task_id}", {"stderr": line})
                    # Prepare for MongoDB
                    logs_to_append.append(
                        {
                            "type": "stderr",
                            "message": line,
                        }
                    )

            # Update MongoDB document with new logs
            if logs_to_append:
                logs_collection.update_one(
                    {"task_id": task_id},
                    {
                        "$push": {"logs": {"$each": logs_to_append}},
                        "$setOnInsert": {"task_id": task_id},
                    },
                    upsert=True,
                )

        return stdout_str, stderr_str
    except UnexpectedExit as e:
        error_message = e.result.stderr if e.result.stderr != "" else str(e)
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="SSH_004",
            payload={
                "error": "SSH Command Failed",
                "message": f"Command '{command}' failed with error: {error_message}",
            },
        )


def connect_to_worker_node(
    control_ssh_client: Connection, worker_input: WorkerNodeInput
) -> Connection:
    """Establish an SSH connection to the worker node through the control node."""
    log.info(
        f"Establishing SSH connection to worker node({worker_input.hostname}) through control node({control_ssh_client.host})"
    )
    try:
        transport = control_ssh_client.transport
        dest_addr = (worker_input.hostname, worker_input.port)
        local_addr = LOCAL_ADDR
        channel = transport.open_channel("direct-tcpip", dest_addr, local_addr)

        connection_params = _prepare_connection_params(worker_input)
        connect_kwargs = {"timeout": SSH_TIMEOUT, "sock": channel}

        if "key_filename" in connection_params:
            connect_kwargs["key_filename"] = connection_params["key_filename"]
            if "passphrase" in connection_params:
                connect_kwargs["passphrase"] = connection_params["passphrase"]
        elif "password" in connection_params:
            connect_kwargs["password"] = connection_params["password"]

        connection = Connection(
            host=connection_params["hostname"],
            user=connection_params["username"],
            port=connection_params["port"],
            connect_kwargs=connect_kwargs,
        )

        # Test the connection
        connection.open()
        log.info(f"SSH connection established to worker node {worker_input.hostname}")
        return connection
    except AuthFailure as auth_ex:
        log.error(
            f"SSH authentication failed for worker node {worker_input.hostname}: {str(auth_ex)}"
        )
        raise SSHAuthenticationError(str(auth_ex))
    except Exception as e:
        log.error(
            f"SSH connection error for worker node {worker_input.hostname}: {str(e)}"
        )
        raise SSHConnectionError(str(e))
