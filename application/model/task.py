import asyncio
from enum import Enum
from typing import Callable, Any

from application.utils.logger import log


class TaskStatus(Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Task:
    def __init__(
        self,
        task_id: str,
        name: str,
        description: str,
        func: Callable,
        *args: Any,
        **kwargs: Any,
    ):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.func = func
        self.args = args + (task_id,)
        self.kwargs = kwargs
        self.status = TaskStatus.NOT_STARTED
        self.error_message = None

    async def run(self):
        self.status = TaskStatus.IN_PROGRESS
        try:
            if asyncio.iscoroutinefunction(self.func):
                await self.func(*self.args, **self.kwargs)
            else:
                await asyncio.to_thread(self.func, *self.args, **self.kwargs)
            self.status = TaskStatus.COMPLETED
        except Exception as e:
            log.error(f"Error in task {self.name}: {str(e)}")
            self.status = TaskStatus.FAILED
            self.error_message = str(e)
