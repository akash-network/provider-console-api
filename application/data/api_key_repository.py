from typing import Dict, Optional
from bson import ObjectId
from application.config.mongodb import provider_console_db
from application.exception.application_error import ApplicationError
from application.utils.logger import log
from fastapi import status


# Collection reference
api_keys_collection = provider_console_db["api_keys"]


def handle_db_error(operation: str, error: Exception):
    log.error(f"Error {operation}: {str(error)}")
    raise ApplicationError(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code="DB_ERROR",
        payload={
            "error": "Database Error",
            "message": f"Error {operation}: {str(error)}",
        },
    )


def create_api_key(api_key_data: Dict) -> str:
    """Create a new API key in the database."""
    try:
        # Check if wallet address already exists
        existing_key = api_keys_collection.find_one(
            {"wallet_address": api_key_data["wallet_address"]}
        )
        if existing_key:
            raise ApplicationError(
                status_code=status.HTTP_409_CONFLICT,
                error_code="API_KEY_001",
                payload={
                    "error": "Wallet Address Already Exists",
                    "message": "An API key already exists for this wallet address",
                },
            )

        # Insert the new API key
        result = api_keys_collection.insert_one(api_key_data)
        log.info(f"Created API key with ID: {result.inserted_id}")
        return str(result.inserted_id)
    except ApplicationError:
        raise
    except Exception as e:
        handle_db_error("creating API key", e)


def get_api_key_by_id(api_key_id: str) -> Optional[Dict]:
    """Get an API key by its ID."""
    try:
        api_key = api_keys_collection.find_one({"_id": ObjectId(api_key_id)})
        if api_key:
            api_key["id"] = str(api_key["_id"])
            del api_key["_id"]
        return api_key
    except Exception as e:
        handle_db_error(f"retrieving API key with ID {api_key_id}", e)


def get_api_key_by_wallet_address(wallet_address: str) -> Optional[Dict]:
    """Get an API key by wallet address."""
    try:
        api_key = api_keys_collection.find_one({"wallet_address": wallet_address})
        if api_key:
            api_key["id"] = str(api_key["_id"])
            del api_key["_id"]
        return api_key
    except Exception as e:
        handle_db_error(f"retrieving API key for wallet address {wallet_address}", e)


def delete_api_key(api_key_id: str) -> bool:
    """Delete an API key."""
    try:
        result = api_keys_collection.delete_one({"_id": ObjectId(api_key_id)})
        
        if result.deleted_count == 0:
            raise ApplicationError(
                status_code=status.HTTP_404_NOT_FOUND,
                error_code="API_KEY_003",
                payload={
                    "error": "API Key Not Found",
                    "message": f"API key with ID {api_key_id} not found",
                },
            )
        
        log.info(f"Deleted API key with ID: {api_key_id}")
        return True
    except ApplicationError:
        raise
    except Exception as e:
        handle_db_error(f"deleting API key with ID {api_key_id}", e)


def check_api_key_exists(wallet_address: str) -> bool:
    """Check if an API key exists for a wallet address."""
    try:
        return api_keys_collection.count_documents({"wallet_address": wallet_address}) > 0
    except Exception as e:
        handle_db_error(f"checking if API key exists for wallet address {wallet_address}", e) 
