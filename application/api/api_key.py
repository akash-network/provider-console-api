from fastapi import APIRouter, HTTPException, status, Depends

from application.model.api_key import ApiKeyResponse
from application.service.api_key_service import ApiKeyService
from application.utils.dependency import verify_token
from application.utils.logger import log
from application.exception.application_error import ApplicationError

router = APIRouter()
api_key_service = ApiKeyService()


@router.post("/api-key", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_api_key(
    wallet_address: str = Depends(verify_token),
):
    """Create a new API key for the authenticated wallet address."""
    try:
        api_key = api_key_service.create_api_key(wallet_address)
        return api_key

    except HTTPException:
        raise
    except ApplicationError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.to_dict()
        )
    except Exception as e:
        log.error(f"Unexpected error in create_api_key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred while creating the API key",
            },
        )


@router.delete("/api-key/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
async def delete_api_key(
    api_key_id: str,
    wallet_address: str = Depends(verify_token),
):
    """Delete an API key."""
    try:
        # First get the API key to check ownership
        api_key = api_key_service.get_api_key(api_key_id)
        
        # Ensure the authenticated user can only delete their own API keys
        if api_key.wallet_address != wallet_address:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "Forbidden",
                    "message": "You can only delete your own API keys",
                },
            )

        api_key_service.delete_api_key(api_key_id)

    except HTTPException:
        raise
    except ApplicationError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.to_dict()
        )
    except Exception as e:
        log.error(f"Unexpected error in delete_api_key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred while deleting the API key",
            },
        )


@router.get("/api-key", response_model=ApiKeyResponse, include_in_schema=False)
async def get_api_key_by_wallet(
    wallet_address: str = Depends(verify_token),
):
    """Get an API key by wallet address."""
    try:
        api_key = api_key_service.get_api_key_by_wallet(wallet_address)
        return api_key

    except HTTPException:
        raise
    except ApplicationError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.to_dict()
        )
    except Exception as e:
        log.error(f"Unexpected error in get_api_key_by_wallet: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred while retrieving the API key",
            },
        )
