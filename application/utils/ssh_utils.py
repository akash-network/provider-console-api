import paramiko
import tempfile
import os
from typing import Union, Tuple
from fastapi import status
from application.exception.application_error import ApplicationError
from application.model.machine_input import ControlMachineInput, WorkerNodeInput
from application.utils.logger import log

# Constants
SSH_TIMEOUT: int = 30
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
) -> paramiko.SSHClient:
    """Establish an SSH connection to the specified machine."""
    log.info(f"Establishing SSH connection to {machine_input.hostname}")
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connection_params = _prepare_connection_params(machine_input)
        ssh_client.connect(**connection_params)
        log.info(f"SSH connection established to {machine_input.hostname}")
        return ssh_client
    except paramiko.AuthenticationException as auth_ex:
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
    ssh_client: paramiko.SSHClient, command: str, check_exit_status: bool = True
) -> Tuple[str, str]:
    """Run an SSH command and return the output."""
    log.debug(f"Running SSH command: {command}")
    stdin, stdout, stderr = ssh_client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()

    stdout_str = stdout.read().decode("utf-8").strip()
    stderr_str = stderr.read().decode("utf-8").strip()

    if check_exit_status and exit_status != 0:
        log.error(f"SSH command failed: {command}. Error: {stderr_str}")
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="SSH_003",
            payload={
                "error": "SSH Command Failed",
                "message": f"Command '{command}' failed with error: {stderr_str}",
            },
        )

    log.debug(f"SSH command completed with status {exit_status}: {command}")
    return stdout_str, stderr_str


def connect_to_worker_node(
    control_ssh_client: paramiko.SSHClient, worker_input: WorkerNodeInput
) -> paramiko.SSHClient:
    """Establish an SSH connection to the worker node through the control node."""
    log.info(
        f"Establishing SSH connection to worker node {worker_input.hostname} through control node"
    )
    try:
        transport = control_ssh_client.get_transport()
        dest_addr = (worker_input.hostname, worker_input.port)
        local_addr = LOCAL_ADDR
        channel = transport.open_channel("direct-tcpip", dest_addr, local_addr)

        worker_ssh_client = paramiko.SSHClient()
        worker_ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connection_params = _prepare_connection_params(worker_input)
        connection_params["sock"] = channel

        worker_ssh_client.connect(**connection_params)

        log.info(f"SSH connection established to worker node {worker_input.hostname}")
        return worker_ssh_client
    except paramiko.AuthenticationException as auth_ex:
        log.error(
            f"SSH authentication failed for worker node {worker_input.hostname}: {str(auth_ex)}"
        )
        raise ApplicationError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="AUTH_003",
            payload={
                "error": "Worker Authentication Failed",
                "message": f"Authentication failed for worker node: {str(auth_ex)}",
            },
        )
    except Exception as e:
        log.error(
            f"SSH connection error for worker node {worker_input.hostname}: {str(e)}"
        )
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="SSH_004",
            payload={
                "error": "Worker SSH Connection Error",
                "message": f"SSH connection error for worker node: {str(e)}",
            },
        )
