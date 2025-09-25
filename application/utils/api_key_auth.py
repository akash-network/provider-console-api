from typing import Optional
from fastapi import Header, HTTPException, status, Depends
from application.service.api_key_service import ApiKeyService
from application.utils.logger import log

api_key_service = ApiKeyService()


def verify_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    """Verify API key and return the associated wallet address."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "API Key Required",
                "message": "X-API-Key header is required",
            },
        )

    try:
        wallet_address = api_key_service.validate_api_key(x_api_key)
        if not wallet_address:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "Invalid API Key",
                    "message": "The provided API key is invalid, expired, or inactive",
                },
            )

        log.info(f"API key authentication successful for wallet: {wallet_address}")
        return wallet_address

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Unexpected error during API key verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred during authentication",
            },
        )
