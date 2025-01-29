from typing import Dict, Any
from datetime import datetime
from application.utils.logger import log
from application.config.mongodb import actions_collection


def insert_action(action_data: Dict[str, Any]) -> None:
    """
    Insert a new action into the database.
    """
    try:
        actions_collection.insert_one(action_data)
    except Exception as e:
        log.error(f"Error inserting action: {str(e)}")
        raise


def find_action(action_id: str) -> Dict[str, Any]:
    """
    Find an action by its ID.
    """
    try:
        action = actions_collection.find_one({"_id": action_id})
        if not action:
            raise ValueError(f"Action {action_id} not found")
        return action
    except Exception as e:
        log.error(f"Error finding action: {str(e)}")
        raise


def update_task_status(
    action_id: str, update_data: Dict[str, Any], task_name: str
) -> None:
    """
    Update the status of a task in an action.
    """
    try:
        actions_collection.update_one(
            {"_id": action_id},
            update_data,
            array_filters=[{"elem.name": task_name}],
        )
    except Exception as e:
        log.error(f"Error updating task status: {str(e)}")
        raise


def update_action_time(action_id: str, update_data: Dict[str, datetime]) -> None:
    """
    Update the start or end time of an action.
    """
    try:
        actions_collection.update_one({"_id": action_id}, {"$set": update_data})
    except Exception as e:
        log.error(f"Error updating action time: {str(e)}")
        raise


def update_action_status(action_id: str, status: str) -> None:
    """
    Update the status of an action.
    """
    try:
        actions_collection.update_one({"_id": action_id}, {"$set": {"status": status}})
    except Exception as e:
        log.error(f"Error updating action status: {str(e)}")
        raise
