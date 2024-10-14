from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status, Depends
from pydantic import ValidationError

from application.exception.application_error import ApplicationError
from application.model.provider_build_input import ProviderBuildInput
from application.service.akash_cluster_service import AkashClusterService
from application.service.wallet_service import WalletService
from application.utils.logger import log
from application.utils.dependency import verify_token


router = APIRouter()


@router.post("/build-provider")
async def build_provider(
    background_tasks: BackgroundTasks,
    input_data: ProviderBuildInput,
    wallet_address: str = Depends(verify_token),
):
    try:
        wallet_service = WalletService()

        # Import wallet
        wallet_import_result = wallet_service.import_wallet(
            input_data.nodes[0], input_data.wallet
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
