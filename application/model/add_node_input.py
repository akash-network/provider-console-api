from pydantic import BaseModel, model_validator, field_validator
from typing import List, Optional, Literal
from typing import Optional
from fastapi import UploadFile
from base64 import b64decode
import io


class Node(BaseModel):
    hostname: str
    username: str
    port: Optional[int] = 22
    password: Optional[str] = None
    keyfile: Optional[UploadFile] = None
    passphrase: Optional[str] = None
    install_gpu_drivers: bool = False
    is_control_plane: bool = False

    class Config:
        extra = "forbid"
        arbitrary_types_allowed = True

    @model_validator(mode="before")
    @classmethod
    def validate_auth_method(cls, values):
        password = values.get("password")
        keyfile = values.get("keyfile")
        if password and keyfile:
            raise ValueError(
                "Authentication conflict: Both password and keyfile provided. Please use only one method."
            )
        if not password and not keyfile:
            raise ValueError(
                "Authentication required: Either password or keyfile must be provided."
            )
        if keyfile:
            try:
                # Extract base64 part if it's a data URL
                if keyfile.startswith("data:"):
                    keyfile = keyfile.split(",")[1]
                # Decode base64 content
                decoded_content = b64decode(keyfile)
                # Convert to UploadFile
                values["keyfile"] = UploadFile(
                    filename="keyfile", file=io.BytesIO(decoded_content)
                )
            except Exception as e:
                raise ValueError(f"Invalid keyfile format: {str(e)}")
        return values

    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v


class AddNodeInput(BaseModel):
    nodes: List[Node]
    existing_nodes: List[dict]
    control_machine: Node
