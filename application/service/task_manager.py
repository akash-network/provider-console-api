from typing import List, Dict, Any
from datetime import datetime
from application.utils.logger import log
from application.model.task import Task, TaskStatus
from application.data.action_repository import (
    insert_action,
    find_action,
    update_task_status,
    update_action_status,
    update_action_time,
)


class TaskManager:
    def __init__(self):
        self.tasks: Dict[str, Dict[str, Task]] = {}

    def create_action(
        self, action_id: str, action_name: str, tasks: List[Task]
    ) -> None:
        """
        Create a new action with the given tasks.
        """
        action_data = {
            "_id": action_id,
            "name": action_name,
            "start_time": None,
            "end_time": None,
            "status": TaskStatus.NOT_STARTED.value,
            "tasks": [
                {
                    "name": task.name,
                    "description": task.description,
                    "status": TaskStatus.NOT_STARTED.value,
                    "start_time": None,
                    "end_time": None,
                }
                for task in tasks
            ],
        }
        insert_action(action_data)
        self.tasks[action_id] = {task.name: task for task in tasks}

    async def run_action(self, action_id: str) -> None:
        """
        Run all tasks for the given action.
        """
        action = find_action(action_id)
        if not action:
            raise ValueError(f"Action {action_id} not found")

        start_time = datetime.utcnow()
        self._update_action_time(action_id, start_time=start_time)

        for task_data in action["tasks"]:
            task_name = task_data["name"]
            await self._run_task(action_id, task_name)

            if self.tasks[action_id][task_name].status == TaskStatus.FAILED:
                self._update_action_status(action_id, TaskStatus.FAILED.value)
                break

        if all(
            task.status == TaskStatus.COMPLETED
            for task in self.tasks[action_id].values()
        ):
            self._update_action_status(action_id, TaskStatus.COMPLETED.value)
        self._update_action_time(action_id, end_time=datetime.utcnow())

    async def _run_task(self, action_id: str, task_name: str) -> None:
        """
        Run a single task and update its status.
        """
        start_time = datetime.utcnow()
        self._update_task_status(
            action_id, task_name, TaskStatus.IN_PROGRESS.value, start_time=start_time
        )

        try:
            task = self.tasks[action_id][task_name]
            await task.run()
            end_time = datetime.utcnow()
            status = (
                TaskStatus.COMPLETED.value
                if task.status == TaskStatus.COMPLETED
                else TaskStatus.FAILED.value
            )
            self._update_task_status(
                action_id,
                task_name,
                status,
                error_message=task.error_message,
                end_time=end_time,
            )
        except Exception as e:
            end_time = datetime.utcnow()
            log.error(f"Error in task {task_name}: {str(e)}")
            self._update_task_status(
                action_id,
                task_name,
                TaskStatus.FAILED.value,
                error_message=str(e),
                end_time=end_time,
            )

    def get_action_status(self, action_id: str) -> Dict[str, Any]:
        """
        Get the status of an action and its tasks.
        """
        try:
            action = find_action(action_id)
            if not action:
                raise ValueError(f"Action {action_id} not found")

            tasks = [
                {
                    "title": task["name"],
                    "description": task["description"],
                    "status": task["status"],
                    "start_time": task["start_time"],
                    "end_time": task["end_time"],
                }
                for task in action["tasks"]
            ]

            return {
                "id": action_id,
                "name": action["name"],
                "status": action["status"],
                "start_time": action["start_time"],
                "end_time": action["end_time"],
                "tasks": tasks,
            }
        except ValueError as ve:
            log.error(f"ValueError in get_action_status: {str(ve)}")
            raise
        except Exception as e:
            log.error(f"Unexpected error in get_action_status: {str(e)}")
            raise RuntimeError(
                f"An error occurred while fetching action status: {str(e)}"
            )

    def _update_task_status(
        self,
        action_id: str,
        task_name: str,
        status: str,
        error_message: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> None:
        """
        Update the status of a task in the database.
        """
        update_data = {"$set": {f"tasks.$[elem].status": status}}
        if error_message:
            update_data["$set"]["tasks.$[elem].error_message"] = error_message
        if start_time:
            update_data["$set"]["tasks.$[elem].start_time"] = start_time
        if end_time:
            update_data["$set"]["tasks.$[elem].end_time"] = end_time

        update_task_status(action_id, update_data, task_name)
        self._update_action_status(action_id)

    def _update_action_time(
        self, action_id: str, start_time: datetime = None, end_time: datetime = None
    ) -> None:
        """
        Update the start or end time of an action in the database.
        """
        update_data = {}
        if start_time:
            update_data["start_time"] = start_time
        if end_time:
            update_data["end_time"] = end_time

        if update_data:
            update_action_time(action_id, update_data)

    def _update_action_status(self, action_id: str, status: str = None) -> None:
        """
        Update the status of an action in the database.
        """
        if status:
            update_action_status(action_id, status)
        else:
            action = find_action(action_id)
            task_statuses = [task["status"] for task in action["tasks"]]

            if TaskStatus.FAILED.value in task_statuses:
                new_status = TaskStatus.FAILED.value
            elif TaskStatus.IN_PROGRESS.value in task_statuses:
                new_status = TaskStatus.IN_PROGRESS.value
            elif all(status == TaskStatus.COMPLETED.value for status in task_statuses):
                new_status = TaskStatus.COMPLETED.value
            else:
                new_status = TaskStatus.NOT_STARTED.value

            update_action_status(action_id, new_status)
