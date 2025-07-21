import secrets
from datetime import datetime, timedelta, timezone
from fastapi import status
from typing import Optional

from application.exception.application_error import ApplicationError
from application.model.api_key import ApiKeyResponse
from application.data.api_key_repository import (
    create_api_key,
    get_api_key_by_id,
    get_api_key_by_wallet_address,
    delete_api_key,
    get_api_key_by_key_value,
    update_last_used,
    check_api_key_exists,
)
from application.utils.logger import log


class ApiKeyService:
    def __init__(self):
        self.api_key_length = 64
        self.api_key_prefix = "akash_"

    def generate_api_key(self) -> str:
        """Generate a secure API key."""
        # Generate a random string
        random_bytes = secrets.token_bytes(self.api_key_length // 2)
        api_key = self.api_key_prefix + random_bytes.hex()
        return api_key

    def create_api_key(self, wallet_address: str) -> ApiKeyResponse:
        """Create a new API key."""
        try:
            # Check if wallet address already has an API key
            if check_api_key_exists(wallet_address):
                raise ApplicationError(
                    status_code=status.HTTP_409_CONFLICT,
                    error_code="API_KEY_001",
                    payload={
                        "error": "Wallet Address Already Exists",
                        "message": "An API key already exists for this wallet address",
                    },
                )

            # Generate API key
            api_key_value = self.generate_api_key()
            
            # Calculate expiration date (1 year from now)
            created_at = datetime.now(timezone.utc)
            expires_at = created_at + timedelta(days=365)
            
            # Create the document
            api_key_doc = {
                "wallet_address": wallet_address,
                "api_key": api_key_value,
                "is_active": True,
                "created_at": created_at,
                "last_used_at": None,
                "expires_at": expires_at,
            }

            # Save to database
            api_key_id = create_api_key(api_key_doc)
            
            # Return the response
            return ApiKeyResponse(
                id=api_key_id,
                wallet_address=api_key_doc["wallet_address"],
                api_key=api_key_doc["api_key"],
                is_active=api_key_doc["is_active"],
                created_at=api_key_doc["created_at"],
                last_used_at=api_key_doc["last_used_at"],
                expires_at=api_key_doc["expires_at"],
            )

        except ApplicationError:
            raise
        except Exception as e:
            log.error(f"Error creating API key: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="API_KEY_004",
                payload={
                    "error": "Internal Server Error",
                    "message": "Failed to create API key",
                },
            ) from None

    def get_api_key(self, api_key_id: str) -> ApiKeyResponse:
        """Get an API key by ID."""
        try:
            api_key_doc = get_api_key_by_id(api_key_id)
            if not api_key_doc:
                raise ApplicationError(
                    status_code=status.HTTP_404_NOT_FOUND,
                    error_code="API_KEY_002",
                    payload={
                        "error": "API Key Not Found",
                        "message": f"API key with ID {api_key_id} not found",
                    },
                )

            return ApiKeyResponse(**api_key_doc)

        except ApplicationError:
            raise
        except Exception as e:
            log.error(f"Error retrieving API key: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="API_KEY_005",
                payload={
                    "error": "Internal Server Error",
                    "message": "Failed to retrieve API key",
                },
            )

    def get_api_key_by_wallet(self, wallet_address: str) -> ApiKeyResponse:
        """Get an API key by wallet address."""
        try:
            api_key_doc = get_api_key_by_wallet_address(wallet_address)
            if not api_key_doc:
                raise ApplicationError(
                    status_code=status.HTTP_404_NOT_FOUND,
                    error_code="API_KEY_002",
                    payload={
                        "error": "API Key Not Found",
                        "message": f"API key for wallet address {wallet_address} not found",
                    },
                )

            return ApiKeyResponse(**api_key_doc)

        except ApplicationError:
            raise
        except Exception as e:
            log.error(f"Error retrieving API key by wallet: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="API_KEY_005",
                payload={
                    "error": "Internal Server Error",
                    "message": "Failed to retrieve API key",
                },
            )

    def delete_api_key(self, api_key_id: str) -> bool:
        """Delete an API key."""
        try:
            return delete_api_key(api_key_id)

        except ApplicationError:
            raise
        except Exception as e:
            log.error(f"Error deleting API key: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="API_KEY_007",
                payload={
                    "error": "Internal Server Error",
                    "message": "Failed to delete API key",
                },
            )

    
    def validate_api_key(self, api_key_value: str) -> Optional[str]:
        """Validate an API key and return the wallet address if valid."""
        try:
            api_key_doc = get_api_key_by_key_value(api_key_value)
            if not api_key_doc:
                return None

            # Check if API key is active
            if not api_key_doc.get("is_active", False):
                return None

            # Check if API key has expired
            expires_at = api_key_doc.get("expires_at")
            if expires_at and datetime.utcnow() > expires_at:
                return None

            # Update last used timestamp
            update_last_used(api_key_doc["id"])

            return api_key_doc["wallet_address"]

        except Exception as e:
            log.error(f"Error validating API key: {str(e)}")
            return None 