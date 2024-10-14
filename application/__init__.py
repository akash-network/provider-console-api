from logging.config import dictConfig

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .config.config import Config
from .config.log_config import LogConfig
from .exception.application_error import ApplicationError

dictConfig(LogConfig().model_dump())

app = FastAPI()


@app.exception_handler(ApplicationError)
async def application_error_handler(ae: ApplicationError):
    return JSONResponse(
        status_code=ae.status_code,
        content={"status": "error", "error_message": f"{ae.payload}"},
    )


origins = Config.PROVIDER_CONSOLE_FRONTEND_URL.split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS", "DELETE"],
    allow_headers=["*"],
)


def create_app() -> FastAPI:
    """Construct the core application."""

    from .api import (
        action_status,
        provider_build,
        provider,
        verify,
    )

    routers = [
        action_status,
        provider_build,
        provider,
        verify,
    ]

    for router in routers:
        app.include_router(router.router)

    return app
