from pydantic import BaseModel, model_validator, field_validator
from typing import List, Optional, Literal
from typing import Optional
from fastapi import UploadFile
from base64 import b64decode
import io


class Node(BaseModel):
    hostname: str
    username: str
    port: int = 22
    password: Optional[str] = None
    keyfile: Optional[UploadFile] = None
    passphrase: Optional[str] = None
    install_gpu_drivers: bool = False

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


class Attribute(BaseModel):
    key: str
    value: str


class Pricing(BaseModel):
    cpu: Optional[float] = None
    memory: Optional[float] = None
    storage: Optional[float] = None
    gpu: Optional[float] = None
    persistentStorage: Optional[float] = None
    ipScalePrice: Optional[float] = None
    endpointBidPrice: Optional[float] = None


class Config(BaseModel):
    domain: Optional[str] = None
    organization: Optional[str] = None
    email: Optional[str] = None


class Provider(BaseModel):
    attributes: List[Attribute]
    pricing: Pricing
    config: Config


class Wallet(BaseModel):
    key_id: str
    wallet_phrase: Optional[str] = None
    override_seed: Optional[bool] = False
    import_mode: Literal["auto", "manual"]


class ProviderBuildInput(BaseModel):
    wallet: Wallet
    nodes: List[Node]
    provider: Provider
