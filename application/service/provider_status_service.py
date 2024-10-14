import io
import json
import base64
from fastapi import status
import paramiko
from concurrent.futures import TimeoutError

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.utils.logger import log


def create_ssh_client():
    ssh_private_key = Config.PROVIDER_CHECK_SSH_PRIVATE_KEY
    private_key = paramiko.RSAKey(
        file_obj=io.StringIO(base64.b64decode(ssh_private_key).decode())
    )

    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_client.connect(
        hostname=Config.PROVIDER_CHECK_SSH_HOST,
        username=Config.PROVIDER_CHECK_SSH_USER,
        port=Config.PROVIDER_CHECK_SSH_PORT,
        pkey=private_key,
    )
    return ssh_client


def execute_ssh_command(ssh_client, command, timeout=30):
    try:
        stdin, stdout, stderr = ssh_client.exec_command(command, timeout=timeout)
        result = stdout.read().decode("utf-8").strip()
        error = stderr.read().decode("utf-8").strip()

        if error:
            raise Exception(f"SSH command error: {error}")

        if not result:
            raise Exception("Empty response from SSH command")

        try:
            return json.loads(result)
        except json.JSONDecodeError:
            raise Exception(f"Invalid JSON response: {result}")
    except paramiko.SSHException as e:
        if "timed out" in str(e).lower():
            raise TimeoutError(f"SSH command timed out after {timeout} seconds")
        raise Exception(f"SSH command failed: {str(e)}")


def get_node_url(chain_id):
    return (
        Config.AKASH_NODE_STATUS_CHECK
        if chain_id == Config.CHAIN_ID
        else Config.AKASH_NODE_STATUS_CHECK_TESTNET
    )


def check_provider_status(chain_id: str, wallet_address: str, command_type: str):
    try:
        ssh_client = create_ssh_client()
        node = get_node_url(chain_id)

        if command_type == "on_chain":
            command = f"provider-services query provider get {wallet_address} --node {node} --output json"
        elif command_type == "online":
            command = f"provider-services status {wallet_address} --node {node}"
        else:
            raise ValueError("Invalid command_type")

        provider_details = execute_ssh_command(
            ssh_client, command, timeout=30 if command_type == "on_chain" else 18
        )
        ssh_client.close()
        return provider_details
    except TimeoutError:
        log.warning(f"Timeout error checking provider status for {wallet_address}")
        return False if command_type == "online" else None
    except ApplicationError as ae:
        raise ae
    except Exception as e:
        log.error(f"Error checking provider status: {e}")
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="PROVIDER_001",
            payload={
                "error": "Provider Status Check Error",
                "message": str(e),
            },
        )


def check_on_chain_provider_status(chain_id: str, wallet_address: str):
    try:
        return check_provider_status(chain_id, wallet_address, "on_chain")
    except ApplicationError as ae:
        raise ae
    except Exception as e:
        log.error(f"Error checking on-chain provider status: {e}")
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="PROVIDER_002",
            payload={
                "error": "On-Chain Provider Status Check Error",
                "message": str(e),
            },
        )


def check_provider_online_status(chain_id: str, wallet_address: str):
    try:
        return check_provider_status(chain_id, wallet_address, "online")
    except ApplicationError as ae:
        raise ae
    except Exception as e:
        log.error(f"Error checking provider online status: {e}")
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="PROVIDER_003",
            payload={
                "error": "Provider Online Status Check Error",
                "message": str(e),
            },
        )
