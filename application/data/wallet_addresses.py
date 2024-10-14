from typing import List, Dict, Optional
from fastapi import status
from pymongo import UpdateOne
from application.config.mongodb import wallet_addresses_collection
from application.exception.application_error import ApplicationError
from application.utils.logger import log


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


def store_wallet_action_mapping(wallet_address: str, action_id: str):
    try:
        update_operation = UpdateOne(
            {"wallet_address": wallet_address},
            {
                "$addToSet": {"action_ids": action_id},
                "$setOnInsert": {"wallet_address": wallet_address},
            },
            upsert=True,
        )
        result = wallet_addresses_collection.bulk_write([update_operation])
        log.info(
            f"Stored wallet address {wallet_address} and action ID {action_id} mapping. "
            f"Modified: {result.modified_count}, Upserted: {result.upserted_count}"
        )
    except Exception as e:
        handle_db_error("storing wallet address and action ID mapping", e)


def get_latest_action_id(wallet_address: str) -> Optional[str]:
    try:
        result = wallet_addresses_collection.find_one(
            {"wallet_address": wallet_address}, {"action_ids": {"$slice": -1}}
        )

        latest_action_id = result.get("action_ids", [None])[0] if result else None
        log.info(
            f"{'Retrieved' if latest_action_id else 'No'} latest action ID for wallet address {wallet_address}"
        )
        return latest_action_id
    except Exception as e:
        handle_db_error(
            f"retrieving latest action ID for wallet address {wallet_address}", e
        )


def get_all_action_details(wallet_address: str) -> List[Dict[str, str]]:
    try:
        pipeline = [
            {"$match": {"wallet_address": wallet_address}},
            {"$unwind": "$action_ids"},
            {
                "$lookup": {
                    "from": "actions",
                    "localField": "action_ids",
                    "foreignField": "_id",
                    "as": "action",
                }
            },
            {"$unwind": "$action"},
            {
                "$project": {
                    "_id": 0,
                    "id": "$action_ids",
                    "name": "$action.name",
                    "status": "$action.status",
                    "start_time": "$action.start_time",
                    "end_time": "$action.end_time",
                }
            },
            {"$sort": {"start_time": -1}},
        ]

        result = list(wallet_addresses_collection.aggregate(pipeline))
        log.info(
            f"Retrieved {len(result)} action details for wallet address {wallet_address}"
        )
        return result
    except Exception as e:
        handle_db_error(
            f"retrieving action details for wallet address {wallet_address}", e
        )
