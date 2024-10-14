from base64 import b64decode
import io
from typing import Tuple

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import ValidationError

from application.exception.application_error import ApplicationError
from application.model.machine_input import ControlMachineInput, WorkerNodeInput
from application.service.cluster_node_service import ClusterNodeService
from application.utils.general import success_response
from application.utils.logger import log

router = APIRouter()


# Helper functions
def decode_keyfile(keyfile_data: str) -> UploadFile:
    keyfile_content = keyfile_data.split(",")[1]
    decoded_content = b64decode(keyfile_content)
    return UploadFile(filename="keyfile", file=io.BytesIO(decoded_content))


def handle_validation_error(error: ValidationError, error_code: str) -> HTTPException:
    log.error(f"Validation error encountered: {str(error)}")
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "status": "error",
            "error": {
                "message": "The provided configuration is invalid.",
                "error_code": error_code,
                "details": [
                    {"field": error["loc"][0], "message": error["msg"]}
                    for error in error.errors()
                ],
            },
        },
    )


def handle_unexpected_error(error: Exception, error_code: str) -> HTTPException:
    log.error(f"Unexpected error: {str(error)}")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "status": "error",
            "error": {
                "message": "An unexpected error occurred during processing.",
                "error_code": error_code,
            },
        },
    )


# Input processing functions
async def get_control_machine_input(data: dict) -> ControlMachineInput:
    try:
        if "keyfile" in data and data["keyfile"]:
            data["keyfile"] = decode_keyfile(data["keyfile"])
        return ControlMachineInput(**data)
    except ValidationError as e:
        raise handle_validation_error(e, "VAL_001")
    except Exception as e:
        raise handle_unexpected_error(e, "VAL_002")


async def get_control_and_worker_input(
    data: dict,
) -> Tuple[ControlMachineInput, WorkerNodeInput]:
    try:
        control_data = data.get("control_machine", {})
        worker_data = data.get("worker_node", {})

        if "keyfile" in control_data and control_data["keyfile"]:
            control_data["keyfile"] = decode_keyfile(control_data["keyfile"])
        if "keyfile" in worker_data and worker_data["keyfile"]:
            worker_data["keyfile"] = decode_keyfile(worker_data["keyfile"])

        return ControlMachineInput(**control_data), WorkerNodeInput(**worker_data)
    except ValidationError as e:
        raise handle_validation_error(e, "VAL_003")
    except Exception as e:
        raise handle_unexpected_error(e, "VAL_004")


# Route handlers
@router.post("/verify/control-machine")
async def verify_control_machine(data: dict):
    input_data = await get_control_machine_input(data)
    log.info(f"Received verification request for hostname: {input_data.hostname}")

    cluster_node_service = ClusterNodeService()
    try:
        verify_connection_result = (
            await cluster_node_service.verify_control_machine_connection(input_data)
        )
        log.info(f"Successfully connected to {input_data.hostname}")
        return success_response(verify_connection_result)
    except ApplicationError as ae:
        log.error(f"Error during control machine verification: {ae.payload}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "error": {
                    "message": ae.payload.get(
                        "message", "Error during control machine verification"
                    ),
                    "error_code": ae.error_code,
                },
            },
        )
    except Exception as e:
        raise handle_unexpected_error(e, "VER_001")


@router.post("/verify/control-and-worker")
async def verify_control_and_worker(data: dict):
    control_input, worker_input = await get_control_and_worker_input(data)
    log.info(
        f"Received verification request for control machine: {control_input.hostname} and worker node: {worker_input.hostname}"
    )

    cluster_node_service = ClusterNodeService()
    try:
        log.info(
            f"Proceeding to verify worker connection through control machine: {control_input.hostname}"
        )
        worker_system_info = await cluster_node_service.verify_worker_connection(
            control_input, worker_input
        )
        log.info(
            f"Successfully connected to worker node: {worker_input.hostname} through control machine"
        )
        return success_response(worker_system_info)
    except ApplicationError as ae:
        log.error(
            f"Error during control machine or worker node verification: {ae.payload}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "error": {
                    "message": ae.payload.get("message", "Error during verification"),
                    "error_code": ae.payload.get("error_code", "VER_002"),
                },
            },
        )
    except Exception as e:
        raise handle_unexpected_error(e, "VER_003")
