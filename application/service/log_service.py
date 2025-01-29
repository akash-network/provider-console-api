import asyncio
import json
import time
from typing import AsyncGenerator

from application.utils.redis import get_redis_client
from application.utils.logger import log
from application.config.mongodb import logs_collection


class LogService:
    def __init__(self):
        self.redis_client = get_redis_client()

    async def _initialize_stream(self, redis_key: str) -> bool:
        """Initialize Redis stream with an initial message"""
        try:
            await asyncio.to_thread(
                self.redis_client.xadd,
                redis_key,
                {"init": "true"},
                maxlen=10000,
                approximate=True,
            )
            return True
        except Exception as e:
            print(f"Error creating stream: {str(e)}")
            return False

    async def _setup_consumer_group(self, redis_key: str, group_name: str) -> bool:
        """Create and verify consumer group"""
        try:
            # Create consumer group
            await asyncio.to_thread(
                self.redis_client.xgroup_create,
                redis_key,
                group_name,
                "0",
                mkstream=True,
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                print(f"Error creating consumer group: {str(e)}")
                return False

        # Verify group creation
        try:
            groups = await asyncio.to_thread(self.redis_client.xinfo_groups, redis_key)
            return any(g["name"] == group_name for g in groups)
        except Exception as e:
            print(f"Error verifying group: {str(e)}")
            return False

    async def _process_message(self, message: dict) -> dict | None:
        """Process and format a single message"""
        if "init" in message:
            return None

        if "stdout" in message:
            return {"type": "stdout", "message": message["stdout"]}
        elif "stderr" in message:
            return {"type": "stderr", "message": message["stderr"]}

        return None

    async def _acknowledge_message(
        self, redis_key: str, group_name: str, message_id: str
    ):
        """Acknowledge message processing"""
        await asyncio.to_thread(
            self.redis_client.xack, redis_key, group_name, message_id
        )

    async def get_redis_logs(
        self, task_id: str, wallet_address: str
    ) -> AsyncGenerator[str, None]:
        """
        Stream logs from Redis for a specific task, handling both stdout and stderr
        Using consumer groups for reliable message delivery
        """
        redis_key = f"task:{task_id}"
        group_name = f"group:{task_id}"
        consumer_name = f"{wallet_address}"

        # Initialize stream and consumer group
        if not await self._initialize_stream(redis_key):
            yield "Error initializing stream\n\n"
            return

        if not await self._setup_consumer_group(redis_key, group_name):
            yield "Error setting up consumer group\n\n"
            return

        # Main message processing loop
        while True:
            try:
                entries = await asyncio.to_thread(
                    self.redis_client.xreadgroup,
                    group_name,
                    consumer_name,
                    {redis_key: ">"},
                    count=100,
                    block=5000,
                )

                if entries:
                    for stream, messages in entries:
                        for message_id, message in messages:
                            log_entry = await self._process_message(message)

                            if log_entry:
                                await self._acknowledge_message(
                                    redis_key, group_name, message_id
                                )
                                yield f"{json.dumps(log_entry)}\n\n"
                else:
                    yield ""  # Heartbeat

            except Exception as e:
                print(f"Error reading stream: {str(e)}")
                yield f"Error reading stream: {str(e)}\n\n"
                break

            await asyncio.sleep(0.1)

    def get_mongo_logs(self, task_id: str) -> list[str]:
        """
        Fetch archived logs from MongoDB for a specific task
        """
        try:
            query = {"task_id": task_id}
            log_document = logs_collection.find_one(query)

            logs = []

            if log_document and "logs" in log_document:
                for log_entry in log_document["logs"]:
                    formatted_log = {
                        "type": log_entry["type"],
                        "message": log_entry["message"],
                    }
                    logs.append(formatted_log)

            return logs

        except Exception as e:
            log.error(f"Error reading MongoDB logs: {str(e)}")
            raise Exception(f"Error reading logs: {str(e)}")
