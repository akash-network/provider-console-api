from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import ValidationError
import io
from base64 import b64decode
from fastapi.datastructures import UploadFile


from application.utils.dependency import verify_token
from application.utils.logger import log
from application.model.machine_input import ControlMachineInput
from application.model.add_node_input import AddNodeInput
from application.service.akash_cluster_service import AkashClusterService
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

def process_add_node_input(data: Dict) -> AddNodeInput:
    try:
        # Process nodes
        if "nodes" in data:
            for node in data["nodes"]:
                if "keyfile" in node and node["keyfile"]:
                    node["keyfile"] = decode_keyfile(node["keyfile"])

        if "control_machine" in data:
            control_machine = data["control_machine"]
            if "keyfile" in control_machine and control_machine["keyfile"]:
                control_machine["keyfile"] = decode_keyfile(control_machine["keyfile"])

        return AddNodeInput(**data)
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


@router.post("/kube/nodes")
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


@router.post("/kube/add-nodes")
async def add_nodes(background_tasks: BackgroundTasks, data: Dict, wallet_address: str = Depends(verify_token)):
    try:
        log.info(f"Adding nodes for wallet address: {wallet_address}")
        input_data = process_add_node_input(data)

        action_id = str(uuid4())
        akash_cluster_service = AkashClusterService()
        background_tasks.add_task(akash_cluster_service.add_nodes, action_id, input_data.control_machine, input_data.nodes, input_data.existing_nodes, wallet_address)
        return {
            "message": "Nodes adding process started successfully.",
            "action_id": action_id,
        }
    except Exception as e:
        log.error(f"Unexpected error during adding nodes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e


@router.post("/kube/remove-node")
async def remove_node(background_tasks: BackgroundTasks, data: Dict, wallet_address: str = Depends(verify_token)):
    try:
        log.info(f"Removing node for wallet address: {wallet_address}")
        control_machine = data["control_machine"]
        node = data["node"]
        node_internal_ip = node["internal_ip"]
        node_name = node["name"]
        node_type = node["type"]

        # Decode keyfile
        if "keyfile" in control_machine and control_machine["keyfile"]:
            control_machine["keyfile"] = decode_keyfile_to_uploadfile(
                control_machine["keyfile"]
            )

        control_machine_input = ControlMachineInput(**control_machine)
        
        action_id = str(uuid4())
        akash_cluster_service = AkashClusterService()
        background_tasks.add_task(akash_cluster_service.remove_nodes, action_id, control_machine_input, node_internal_ip, node_name, node_type, wallet_address)
        return {
            "message": "Node removal process started successfully.",
            "action_id": action_id,
        }
    except Exception as e:
        log.error(f"Unexpected error during removing node: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e
