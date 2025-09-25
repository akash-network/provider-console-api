from typing import Dict
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.datastructures import UploadFile
from base64 import b64decode
import io


from application.model.machine_input import ControlMachineInput
from application.utils.dependency import verify_token
from application.utils.logger import log
from application.utils.ssh_utils import get_ssh_client
from application.service.provider_service import ProviderService

router = APIRouter()


def decode_keyfile(keyfile_data: str) -> str:
    return keyfile_data.split(",")[1]

# Helper functions
def decode_keyfile_to_uploadfile(keyfile_data: str) -> UploadFile:
    decoded_content = b64decode(decode_keyfile(keyfile_data))
    return UploadFile(filename="keyfile", file=io.BytesIO(decoded_content))


@router.post("/letsencrypt-jwt/status", include_in_schema=False)
async def letsencrypt_jwt_status(
    data: Dict, wallet_address: str = Depends(verify_token)
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
        letsencrypt_jwt_status = await provider_service.get_letsencrypt_jwt_status(ssh_client)

        return {
            "message": "Letsencrypt jwt status retrieved successfully",
            "letsencrypt_jwt_status": letsencrypt_jwt_status,
        }
    except Exception as e:
        log.error(f"Error retrieving letsencrypt jwt status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while retrieving letsencrypt jwt status: {str(e)}",
                    "error_code": "PRV_003",
                },
            },
        )


@router.post("/letsencrypt-jwt/enable", include_in_schema=False)
async def enable_letsencrypt_jwt(
    data: Dict, wallet_address: str = Depends(verify_token)
):
    try:
        control_machine = data["control_machine"]
        
        # Extract provider info and email from the request data
        provider_info = data.get("provider_info", {})
        email = data.get("email", "")

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)
        ssh_client = get_ssh_client(control_machine_input)

        provider_service = ProviderService()
        await provider_service.enable_letsencrypt_jwt(ssh_client, provider_info, email)

        return {"message": "Letsencrypt jwt enabled successfully"}
    except Exception as e:
        log.error(f"Error enabling letsencrypt jwt: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while enabling letsencrypt jwt: {str(e)}",
                    "error_code": "PRV_003",
                },
            },
        )
