from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status, Depends
from pydantic import ValidationError
from typing import Dict
import io
from base64 import b64decode
from fastapi.datastructures import UploadFile

from application.exception.application_error import ApplicationError
from application.model.provider_build_input import ProviderBuildInput
from application.model.machine_input import ControlMachineInput
from application.service.akash_cluster_service import AkashClusterService
from application.service.provider_service import ProviderService
from application.utils.ssh_utils import get_ssh_client
from application.service.wallet_service import WalletService
from application.utils.logger import log
from application.utils.dependency import verify_token
from application.service.upgrade_service import UpgradeService


router = APIRouter()


def decode_keyfile(keyfile_data: str) -> str:
    return keyfile_data.split(",")[1]


# Helper functions
def decode_keyfile_to_uploadfile(keyfile_data: str) -> UploadFile:
    decoded_content = b64decode(decode_keyfile(keyfile_data))
    return UploadFile(filename="keyfile", file=io.BytesIO(decoded_content))


def process_provider_build_input(data: Dict) -> ProviderBuildInput:
    try:
        # Process nodes
        if "nodes" in data:
            for node in data["nodes"]:
                if "keyfile" in node and node["keyfile"]:
                    node["keyfile"] = decode_keyfile(node["keyfile"])

        # Create ProviderBuildInput object
        return ProviderBuildInput(**data)
    except ValidationError as e:
        log.error(f"Validation error encountered: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "error": {
                    "message": "The provided configuration is invalid.",
                    "error_code": "VAL_005",
                    "details": [
                        {"field": error["loc"][0], "message": error["msg"]}
                        for error in e.errors()
                    ],
                },
            },
        )
    except Exception as e:
        log.error(f"Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": "An unexpected error occurred during processing.",
                    "error_code": "VAL_006",
                },
            },
        )


@router.post("/build-provider")
async def build_provider(
    background_tasks: BackgroundTasks,
    data: Dict,
    wallet_address: str = Depends(verify_token),
):
    try:
        input_data = process_provider_build_input(data)
        wallet_service = WalletService()

        # Import wallet
        wallet_import_result = wallet_service.import_wallet(
            input_data.nodes[0], input_data.wallet, wallet_address
        )
        if not wallet_import_result.get("success", False):
            raise ApplicationError(
                payload={
                    "message": f"Failed to import wallet: {wallet_import_result.get('message', 'Unknown error')}",
                    "error_code": "WAL_001",
                }
            )
        akash_cluster_service = AkashClusterService()

        # Generate action_id
        action_id = str(uuid4())

        # Add background task
        akash_cluster_service = AkashClusterService()
        background_tasks.add_task(
            akash_cluster_service.create_akash_cluster,
            action_id,
            input_data,
            wallet_address,
        )

        # Immediately return response
        return {
            "message": "Provider build process started successfully",
            "action_id": action_id,
        }
    except ApplicationError as ae:
        log.error(f"Error during provider build: {ae.payload}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "error": {
                    "message": ae.payload.get("message", "Error during provider build"),
                    "error_code": ae.error_code,
                },
            },
        )
    except ValidationError as ve:
        log.error(f"Validation error in provider build input: {ve}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "error": {
                    "message": "Invalid provider build input",
                    "error_code": "VAL_006",
                    "details": [
                        {"field": error["loc"][0], "message": error["msg"]}
                        for error in ve.errors()
                    ],
                },
            },
        )
    except Exception as e:
        log.error(f"Unexpected error during provider build: {str(e)}")
        raise ApplicationError(
            payload={
                "message": f"An error occurred during provider build process: {str(e)}",
                "error_code": "PRV_001",
            }
        )


@router.get("/build-provider-status/{action_id}")
async def get_build_provider_status(action_id: str):
    try:
        akash_cluster_service = AkashClusterService()
        return akash_cluster_service.get_action_status(action_id)
    except Exception as e:
        log.error(f"Error retrieving action status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.post("/update-provider-attributes")
async def update_provider_attributes(
    data: Dict,
    wallet_address: str = Depends(verify_token),
):
    try:
        control_machine = data["control_machine"]
        attributes = data["attributes"]

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)

        action_id = str(uuid4())
        akash_cluster_service = AkashClusterService()
        await akash_cluster_service.update_provider_attributes(
            action_id, control_machine_input, attributes, wallet_address
        )

        return {"message": "Provider attributes update process started successfully"}
    except Exception as e:
        log.error(f"Error updating provider attributes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.post("/get-provider-pricing")
async def get_provider_pricing(
    data: Dict,
    wallet_address: str = Depends(verify_token),
):
    try:
        control_machine = data["control_machine"]

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)
        ssh_client = get_ssh_client(control_machine_input)

        provider_service = ProviderService()
        pricing = await provider_service.get_provider_pricing(ssh_client)

        return {
            "message": "Provider pricing retrieved successfully",
            "pricing": pricing,
        }
    except Exception as e:
        log.error(f"Error retrieving provider pricing: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while retrieving provider pricing: {str(e)}",
                    "error_code": "PRV_003",
                },
            },
        )


@router.post("/update-provider-pricing")
async def update_provider_pricing(
    data: Dict,
    wallet_address: str = Depends(verify_token),
):
    try:
        control_machine = data["control_machine"]
        pricing = data["pricing"]

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)

        action_id = str(uuid4())
        akash_cluster_service = AkashClusterService()
        await akash_cluster_service.update_provider_pricing(
            action_id, control_machine_input, pricing, wallet_address
        )

        return {
            "message": "Provider pricing update process started successfully",
            "action_id": action_id,
        }
    except Exception as e:
        log.error(f"Error updating provider pricing: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred during provider pricing update: {str(e)}",
                    "error_code": "PRV_002",
                },
            },
        )


@router.post("/update-provider-domain")
async def update_provider_domain(
    data: Dict,
    wallet_address: str = Depends(verify_token),
):
    try:
        control_machine = data["control_machine"]
        domain = data["domain"]

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)

        action_id = str(uuid4())
        akash_cluster_service = AkashClusterService()
        await akash_cluster_service.update_provider_domain(
            action_id, control_machine_input, domain, wallet_address
        )

        return {
            "message": "Provider domain update process started successfully",
            "action_id": action_id,
        }
    except Exception as e:
        log.error(f"Error updating provider domain: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred during provider domain update: {str(e)}",
                    "error_code": "PRV_002",
                },
            },
        )


@router.post("/update-provider-email")
async def update_email(
    data: Dict,
    wallet_address: str = Depends(verify_token),
):
    try:
        control_machine = data["control_machine"]
        email = data["email"]

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)

        action_id = str(uuid4())
        akash_cluster_service = AkashClusterService()

        await akash_cluster_service.update_provider_email(
            action_id, control_machine_input, email, wallet_address
        )

        return {
            "message": "Provider email update process started successfully",
            "action_id": action_id,
        }
    
    except Exception as e:
        log.error(f"Error updating provider email: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred during provider email update: {str(e)}",
                    "error_code": "PRV_002",
                },
            },
        )


@router.post("/upgrade-status")
async def check_upgrade(
    machine_input: Dict, wallet_address: str = Depends(verify_token)
) -> Dict:
    """Check if network upgrade is needed by comparing current and deployed versions"""
    try:
        control_machine = machine_input["control_machine"]
        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)

        ssh_client = get_ssh_client(control_machine_input)
        upgrade_service = UpgradeService()
        return await upgrade_service.check_upgrade_status(ssh_client)
    except Exception as e:
        log.error(f"Error checking network upgrade status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while checking network upgrade status: {str(e)}",
                    "error_code": "PRV_005",
                },
            },
        )


@router.post("/network/upgrade")
async def upgrade_network(
    background_tasks: BackgroundTasks,
    machine_input: Dict, wallet_address: str = Depends(verify_token)
) -> Dict:
    try:
        control_machine = machine_input["control_machine"]
        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)

        action_id = str(uuid4())
        akash_cluster_service = AkashClusterService()
        background_tasks.add_task(
            akash_cluster_service.upgrade_network,
            action_id,
            control_machine_input,
            wallet_address
        )

        return {
            "message": "Network upgrade process started successfully",
            "action_id": action_id,
        }
    except Exception as e:
        log.error(f"Error upgrading network: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while upgrading the network: {str(e)}",
                    "error_code": "PRV_006",
                },
            },
        )

@router.post("/provider/upgrade")
async def upgrade_provider(
    background_tasks: BackgroundTasks,
    machine_input: Dict, wallet_address: str = Depends(verify_token)
) -> Dict:
    try:
        control_machine = machine_input["control_machine"]
        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)  

        action_id = str(uuid4())
        akash_cluster_service = AkashClusterService()
        background_tasks.add_task(
            akash_cluster_service.upgrade_provider,
            action_id,
            control_machine_input,
            wallet_address
        )

        return {
            "message": "Provider upgrade process started successfully",
            "action_id": action_id,
        }
    except Exception as e:
        log.error(f"Error upgrading provider: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while upgrading the provider: {str(e)}",
                    "error_code": "PRV_007",
                },
            },
        )


@router.post("/restart-provider")
async def restart_provider(
    data: Dict,
    wallet_address: str = Depends(verify_token),
):
    try:
        control_machine = data["control_machine"]

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)
        ssh_client = get_ssh_client(control_machine_input)

        provider_service = ProviderService()
        await provider_service.restart_provider_service(ssh_client)

        return {"message": "Provider restart process started successfully"}
    except Exception as e:
        log.error(f"Error restarting provider: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while restarting the provider: {str(e)}",
                    "error_code": "PRV_004",
                },
            },
        )
