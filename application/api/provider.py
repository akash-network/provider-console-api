from fastapi import APIRouter, status, HTTPException, Depends
from application.utils.dependency import verify_token
from application.service.provider_status_service import (
    check_on_chain_provider_status,
    check_provider_online_status,
)
from application.utils.logger import log
from application.exception.application_error import ApplicationError

router = APIRouter()


@router.get("/provider/status/onchain")
async def provider_onchain_status_get(
    chainid: str, wallet_address: str = Depends(verify_token)
):
    try:
        provider_details = await check_on_chain_provider_status(chainid, wallet_address)
        return {"provider": False if provider_details is False else provider_details}
    except ApplicationError as ae:
        raise HTTPException(
            status_code=ae.status_code,
            detail={
                "error_code": ae.error_code,
                "error": ae.payload["error"],
                "message": ae.payload["message"],
            },
        )
    except Exception as e:
        log.error(
            f"Unexpected error in provider_onchain_status_get: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "PROVIDER_004",
                "error": "Unexpected Error",
                "message": f"An unexpected error occurred: {str(e)}",
            },
        )


@router.get("/provider/status/online")
async def provider_online_status_get(
    chainid: str, wallet_address: str = Depends(verify_token)
):
    try:
        provider_online_status = await check_provider_online_status(chainid, wallet_address)
        return {"online": False if provider_online_status is False else True}
    except ApplicationError as ae:
        raise HTTPException(
            status_code=ae.status_code,
            detail={
                "error_code": ae.error_code,
                "error": ae.payload["error"],
                "message": ae.payload["message"],
            },
        )
    except Exception as e:
        log.error(f"Unexpected error in provider_online_status_get: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "PROVIDER_005",
                "error": "Unexpected Error",
                "message": f"An unexpected error occurred: {str(e)}",
            },
        )


async def get_provider_status(
    status_type: str, check_function, chainid: str, wallet_address: str
):
    log.info(f"Getting {status_type} status for wallet address ({wallet_address})")
    try:
        provider_details = check_function(chainid, wallet_address)
        return {"provider": provider_details if provider_details else False}
    except ApplicationError as ae:
        raise ae
    except Exception as e:
        log.error(f"Error getting {status_type} provider status: {e}")
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code=f"PROVIDER_00{'2' if status_type == 'on_chain' else '3'}",
            payload={
                "error": "Provider Status Check Error",
                "message": f"Error getting {status_type} provider status: {e}",
            },
        )
