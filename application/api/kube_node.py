from fastapi import APIRouter, Depends, HTTPException, status
import io
from base64 import b64decode
from fastapi.datastructures import UploadFile


from application.utils.dependency import verify_token
from application.utils.logger import log
from application.model.machine_input import ControlMachineInput
from application.service.k3s_service import K3sService
from application.utils.ssh_utils import get_ssh_client
from typing import Dict

router = APIRouter()

def decode_keyfile(keyfile_data: str) -> str:
    return keyfile_data.split(",")[1]

# Helper functions
def decode_keyfile_to_uploadfile(keyfile_data: str) -> UploadFile:
    decoded_content = b64decode(decode_keyfile(keyfile_data))
    return UploadFile(filename="keyfile", file=io.BytesIO(decoded_content))

@router.post("/nodes")
async def list_nodes(data: Dict, wallet_address: str = Depends(verify_token)):
    try:
        log.info(f"Listing nodes for wallet address: {wallet_address}")
        control_machine = data["control_machine"]

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)
        ssh_client = get_ssh_client(control_machine_input)

        k3s_service = K3sService()
        return k3s_service.list_nodes(ssh_client)
    except Exception as e:
        log.error(f"Unexpected error during listing nodes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e
