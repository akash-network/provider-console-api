from fastapi import APIRouter, HTTPException, status, Depends, Query
from datetime import date
from typing import Dict, Any

from application.service.provider_earnings_service import ProviderEarningsService
from application.utils.api_key_auth import verify_api_key
from application.utils.logger import log
from application.exception.application_error import ApplicationError
from application.model.provider_earnings import ProviderEarningsResponse, ErrorResponse

router = APIRouter()
provider_earnings_service = ProviderEarningsService()


@router.get(
    "/provider-earnings",
    response_model=ProviderEarningsResponse,
    responses={
        200: {
            "description": "Provider earnings data retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "earnings": {
                            "totalUAktEarned": 198821549.899183,
                            "totalUUsdcEarned": 62549.30588271079,
                            "totalUUsdEarned": 523063470.03441215
                        }
                    }
                }
            }
        },
        401: {
            "description": "Unauthorized - Invalid or missing API key",
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "example": {
                        "error": "Invalid API Key",
                        "message": "The provided API key is invalid, expired, or inactive",
                        "error_code": "API_KEY_401"
                    }
                }
            }
        },
        404: {
            "description": "Not Found - No earnings data found",
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "example": {
                        "error": "Provider Earnings Not Found",
                        "message": "No earnings data found for wallet in the specified date range",
                        "error_code": "EARNINGS_004"
                    }
                }
            }
        },
        502: {
            "description": "Bad Gateway - Internal service error",
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "example": {
                        "error": "Internal Service Error",
                        "message": "Failed to fetch provider earnings from internal service",
                        "error_code": "EARNINGS_006"
                    }
                }
            }
        },
        503: {
            "description": "Service Unavailable - Internal service is unavailable",
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "example": {
                        "error": "Service Unavailable",
                        "message": "Internal service is currently unavailable",
                        "error_code": "EARNINGS_008"
                    }
                }
            }
        },
        504: {
            "description": "Gateway Timeout - Internal service request timed out",
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "example": {
                        "error": "Gateway Timeout",
                        "message": "Internal service request timed out",
                        "error_code": "EARNINGS_007"
                    }
                }
            }
        },
        500: {
            "description": "Internal Server Error",
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "example": {
                        "error": "Internal Server Error",
                        "message": "An unexpected error occurred while fetching provider earnings",
                        "error_code": "EARNINGS_001"
                    }
                }
            }
        }
    },
    tags=["Provider Earnings"],
    summary="Get provider earnings for a wallet address and date range",
    description="""
This endpoint returns provider earnings for a specific wallet address and date range.

- **Authentication:** Requires a valid API key in the `X-API-Key` header.
- **Query Parameters:**
    - `from_date` (YYYY-MM-DD): Start date (inclusive)
    - `to_date` (YYYY-MM-DD): End date (inclusive)
- **Returns:** Provider earnings data as returned by the internal service.
"""
)
async def get_provider_earnings(
    from_date: date = Query(..., description="Start date in YYYY-MM-DD format"),
    to_date: date = Query(..., description="End date in YYYY-MM-DD format"),
    wallet_address: str = Depends(verify_api_key),
) -> ProviderEarningsResponse:
    """
    Get provider earnings for a specific wallet address and date range.
    """
    try:
        earnings_data = provider_earnings_service.get_provider_earnings(
            wallet_address=wallet_address,
            from_date=from_date,
            to_date=to_date
        )
        return earnings_data
    except HTTPException:
        raise
    except ApplicationError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.to_dict()
        )
    except Exception as e:
        log.error(f"Unexpected error in get_provider_earnings endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred while fetching provider earnings",
            },
        ) 