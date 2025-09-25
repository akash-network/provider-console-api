import requests
from datetime import date
from typing import Dict, Any

from application.config.config import Config
from application.utils.logger import log
from application.exception.application_error import ApplicationError
from application.model.provider_earnings import ProviderEarningsResponse, EarningsData
from fastapi import status


class ProviderEarningsService:
    def __init__(self):
        self.console_api_base_url = Config.CONSOLE_API_BASE_URL
        self.timeout = 30

    def get_provider_earnings(
        self, wallet_address: str, from_date: date, to_date: date
    ) -> ProviderEarningsResponse:
        """
        Fetch provider earnings from internal API.

        Args:
            wallet_address: The wallet address to fetch earnings for
            from_date: Start date for the earnings period
            to_date: End date for the earnings period

        Returns:
            ProviderEarningsResponse: Wrapped provider earnings data

        Raises:
            ApplicationError: For various error conditions
        """
        try:
            # Validate date range
            self._validate_date_range(from_date, to_date)

            # Format dates
            from_date_str = from_date.strftime("%Y-%m-%d")
            to_date_str = to_date.strftime("%Y-%m-%d")

            # Construct URL and parameters
            internal_url = (
                f"{self.console_api_base_url}/v1/provider-earnings/{wallet_address}"
            )
            params = {"from": from_date_str, "to": to_date_str}

            log.info(
                f"Fetching provider earnings for wallet {wallet_address} from {from_date_str} to {to_date_str}"
            )

            # Make request to internal API
            earnings_data = self._make_internal_api_request(
                internal_url, params, wallet_address
            )

            # The response structure is: {"earnings": {"totalUAktEarned": ..., "totalUUsdcEarned": ..., "totalUUsdEarned": ...}}
            # We pass it through as-is
            return ProviderEarningsResponse(
                earnings=EarningsData(**earnings_data["earnings"])
            )

        except ApplicationError:
            raise
        except Exception as e:
            log.error(f"Unexpected error in get_provider_earnings: {e}", exc_info=True)
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="EARNINGS_001",
                payload={
                    "error": "Internal Server Error",
                    "message": "Failed to fetch provider earnings",
                },
            ) from e

    def _validate_date_range(self, from_date: date, to_date: date) -> None:
        """Validate the date range."""
        if from_date > to_date:
            raise ApplicationError(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="EARNINGS_002",
                payload={
                    "error": "Invalid Date Range",
                    "message": "From date must be before or equal to to date",
                },
            )

        # Check if date range is not too large
        date_diff = (to_date - from_date).days
        if date_diff > 365:
            raise ApplicationError(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="EARNINGS_003",
                payload={
                    "error": "Date Range Too Large",
                    "message": "Date range cannot exceed 365 days",
                },
            )

    def _make_internal_api_request(
        self, url: str, params: Dict[str, str], wallet_address: str
    ) -> Dict[str, Any]:
        """Make request to internal API with proper error handling."""
        try:
            response = requests.get(url, params=params, timeout=self.timeout)

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                raise ApplicationError(
                    status_code=status.HTTP_404_NOT_FOUND,
                    error_code="EARNINGS_004",
                    payload={
                        "error": "Provider Earnings Not Found",
                        "message": f"No earnings data found for wallet {wallet_address} in the specified date range",
                    },
                )
            elif response.status_code == 400:
                # Pass through validation errors from internal API
                try:
                    error_detail = response.json()
                except ValueError:
                    error_detail = {"message": "Bad request from internal service"}
                raise ApplicationError(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    error_code="EARNINGS_005",
                    payload=error_detail,
                )
            else:
                log.error(
                    f"Internal API error: {response.status_code} - {response.text}"
                )
                raise ApplicationError(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    error_code="EARNINGS_006",
                    payload={
                        "error": "Internal Service Error",
                        "message": f"Failed to fetch provider earnings from internal service (Status: {response.status_code})",
                    },
                )

        except requests.Timeout:
            log.error(f"Timeout while calling internal API for wallet {wallet_address}")
            raise ApplicationError(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                error_code="EARNINGS_007",
                payload={
                    "error": "Gateway Timeout",
                    "message": "Internal service request timed out",
                },
            )
        except requests.ConnectionError:
            log.error(
                f"Connection error while calling internal API for wallet {wallet_address}"
            )
            raise ApplicationError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                error_code="EARNINGS_008",
                payload={
                    "error": "Service Unavailable",
                    "message": "Internal service is currently unavailable",
                },
            )
        except requests.RequestException as e:
            log.error(
                f"Request exception while calling internal API for wallet {wallet_address}: {str(e)}"
            )
            raise ApplicationError(
                status_code=status.HTTP_502_BAD_GATEWAY,
                error_code="EARNINGS_009",
                payload={
                    "error": "Request Error",
                    "message": "Failed to communicate with internal service",
                },
            )
