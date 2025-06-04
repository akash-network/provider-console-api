from fastapi import APIRouter, status, HTTPException, Depends
from application.utils.dependency import verify_token
from application.service.provider_status_service import (
    check_provider_online_status_v2,
)
from application.utils.logger import log

router = APIRouter()


@router.get("/provider/status/v2/online")
async def provider_online_status_v2_get(
    chainid: str, provider_uri: str, wallet_address: str = Depends(verify_token)
):
    try:
        provider_online_status = await check_provider_online_status_v2(
            chainid, provider_uri
        )
        return {"online": False if provider_online_status is False else True}
    except Exception as e:
        log.error(f"Unexpected error in provider_online_status_v2_get: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "PROVIDER_006",
                "error": "Unexpected Error",
                "message": f"An unexpected error occurred: {str(e)}",
            },
        )
