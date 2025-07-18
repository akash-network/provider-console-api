from uuid import uuid4

from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks
from typing import Dict
import io
from base64 import b64decode
from fastapi.datastructures import UploadFile

from application.model.machine_input import ControlMachineInput
from application.service.akash_cluster_service import AkashClusterService
from application.service.persistent_storage_service import PersistentStorageService
from application.utils.logger import log
from application.utils.dependency import verify_token


router = APIRouter()


# Helper functions
def decode_keyfile_to_uploadfile(keyfile_data: str) -> UploadFile:
    keyfile_content = keyfile_data.split(",")[1]
    decoded_content = b64decode(keyfile_content)
    return UploadFile(filename="keyfile", file=io.BytesIO(decoded_content))


@router.post("/get-unformatted-drives", dependencies=[Depends(verify_token)], include_in_schema=False)
async def get_unformatted_drives(data: Dict):
    try:
        control_machine = data["control_machine"]

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)

        persistent_storage_service = PersistentStorageService()
        unformatted_drives = persistent_storage_service.get_unformatted_drives(
            control_machine_input
        )

        return {"unformatted_drives": unformatted_drives}
    except Exception as e:
        log.error(f"Error getting unformatted drives: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while getting unformatted drives: {str(e)}",
                    "error_code": "STG_002",
                },
            },
        )


@router.post("/persistent-storage", include_in_schema=False)
async def persistent_storage(
    background_tasks: BackgroundTasks,
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

        # Generate action_id
        action_id = str(uuid4())

        storage_info = data["storage_info"]

        # Add background task
        akash_cluster_service = AkashClusterService()
        background_tasks.add_task(
            akash_cluster_service.create_persistent_storage,
            action_id,
            control_machine_input,
            storage_info,
            wallet_address,
        )

        # Immediately return response
        return {
            "message": "Persistent storage creation process started successfully",
            "action_id": action_id,
        }
    except Exception as e:
        log.error(f"Error getting unformatted drives: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error": {
                    "message": f"An error occurred while getting unformatted drives: {str(e)}",
                    "error_code": "STG_002",
                },
            },
        )
