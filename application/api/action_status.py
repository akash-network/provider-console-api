from fastapi import APIRouter, HTTPException, status, Depends
from application.service.akash_cluster_service import AkashClusterService
from application.utils.logger import log
from application.utils.dependency import verify_token
from application.data.wallet_addresses import get_all_action_details

router = APIRouter()


@router.get("/action/status/{action_id}", include_in_schema=False)
async def get_action_status(
    action_id: str, wallet_address: str = Depends(verify_token)
):
    try:
        return AkashClusterService().get_action_status(action_id)
    except Exception as e:
        log.error(f"Error retrieving action status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e


@router.get("/actions", include_in_schema=False)
async def get_action_ids(wallet_address: str = Depends(verify_token)):
    try:
        return {"actions": get_all_action_details(wallet_address)}
    except Exception as e:
        log.error(f"Error fetching action IDs for wallet address {wallet_address}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching action IDs",
        ) from e
