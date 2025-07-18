from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from application.service.log_service import LogService
from application.utils.dependency import verify_token

router = APIRouter()
log_service = LogService()


@router.get("/tasks/logs/{task_id}", include_in_schema=False)
async def stream_task_logs(task_id: str, wallet_address: str = Depends(verify_token)):
    """
    Stream logs for a specific task from Redis with heartbeat
    """

    async def event_generator():
        try:
            async for log in log_service.get_redis_logs(task_id, wallet_address):
                print(f"log: {log}")
                if log:
                    yield f"data: {log}\n\n"
                else:
                    # Send heartbeat every time there's no new log
                    yield f":\n\n"
        except Exception as e:
            raise HTTPException(
                status_code=404, detail=f"Error streaming logs: {str(e)}"
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/tasks/logs/archive/{task_id}", include_in_schema=False)
def get_active_task_logs(task_id: str, wallet_address: str = Depends(verify_token)):
    """
    Get archived logs for a specific task from MongoDB
    """
    try:
        logs = log_service.get_mongo_logs(task_id)
        return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching logs: {str(e)}")
