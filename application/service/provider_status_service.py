import json
from base64 import b64decode
from fastapi.datastructures import UploadFile
import io
import asyncio
import requests
import urllib3

from fastapi import status
from concurrent.futures import TimeoutError

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.model.machine_input import ControlMachineInput
from application.utils.ssh_utils import get_ssh_client, run_ssh_command
from application.utils.logger import log


def get_node_url(chain_id):
    return (
        Config.AKASH_NODE_STATUS_CHECK
        if chain_id == Config.CHAIN_ID
        else Config.AKASH_NODE_STATUS_CHECK_TESTNET
    )


async def check_provider_status(chain_id: str, wallet_address: str, command_type: str):
    def setup_ssh_connection():
        # Setup SSH connection details
        decoded_content = b64decode(Config.PROVIDER_CHECK_SSH_PRIVATE_KEY)
        keyfile = UploadFile(filename="keyfile", file=io.BytesIO(decoded_content))

        return ControlMachineInput(
            hostname=Config.PROVIDER_CHECK_SSH_HOST,
            username=Config.PROVIDER_CHECK_SSH_USER,
            port=Config.PROVIDER_CHECK_SSH_PORT,
            keyfile=keyfile,
        )

    def execute_ssh_command(machine_input):
        ssh_client = get_ssh_client(machine_input)
        try:
            node = get_node_url(chain_id)

            if command_type == "on_chain":
                command = f"provider-services query provider get {wallet_address} --node {node} --output json"
            elif command_type == "online":
                command = f"provider-services status {wallet_address} --node {node}"
            else:
                raise ValueError("Invalid command_type")

            stdout, _ = run_ssh_command(ssh_client, command, True, timeout=25)
            return json.loads(stdout)
        finally:
            ssh_client.close()

    try:
        # Setup connection details
        machine_input = await asyncio.to_thread(setup_ssh_connection)

        # Execute SSH command in separate thread
        provider_details = await asyncio.to_thread(execute_ssh_command, machine_input)

        return provider_details

    except TimeoutError:
        log.warning(f"Timeout error checking provider status for {wallet_address}")
        return False if command_type == "online" else None

    except ApplicationError as ae:
        error_message = str(ae.payload["message"]).lower()

        # Return False for online status check or specific error messages
        if command_type == "online" or any(
            msg in error_message for msg in ["address not found", "unknown query path"]
        ):
            return False

        # Re-raise other application errors
        raise ae

    except Exception as e:
        log.error(f"Error checking provider status: {e}")
        if command_type == "online":
            return False
        elif command_type == "on_chain":
            if "provider: address not found" in str(e):
                return None
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="PROVIDER_001",
            payload={
                "error": "Provider Status Check Error",
                "message": str(e),
            },
        )


async def check_on_chain_provider_status(chain_id: str, wallet_address: str):
    try:
        return await check_provider_status(chain_id, wallet_address, "on_chain")
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


async def check_provider_online_status(chain_id: str, wallet_address: str):
    try:
        return await check_provider_status(chain_id, wallet_address, "online")
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

async def check_provider_online_status_v2(chain_id: str, provider_uri: str):
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    try:
        response = requests.get(f"{provider_uri}/status", verify=False, timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Error checking provider online status v2: {e}")
        return False