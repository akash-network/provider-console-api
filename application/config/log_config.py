from pydantic import BaseModel
from typing import Dict, Any

from .config import Config


class LogConfig(BaseModel):
    # Logging configuration to be set for the server

    LOGGER_NAME: str = Config.APP_NAME
    LOG_FORMAT: str = "[%(asctime)s][%(name)s][%(levelname)s]: %(message)s"
    LOG_LEVEL: str = Config.LOG_LEVEL

    # Logging config
    version: int = 1
    disable_existing_loggers: bool = False
    formatters: Dict[str, Dict[str, Any]] = {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": LOG_FORMAT,
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    }
    handlers: Dict[str, Dict[str, Any]] = {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
    }
    loggers: Dict[str, Dict[str, Any]] = {
        LOGGER_NAME: {"handlers": ["default"], "level": LOG_LEVEL},
    }
