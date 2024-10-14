from pydantic import BaseModel, field_validator, model_validator
from typing import Optional
from fastapi import UploadFile
import socket
import re
import ipaddress


class ControlMachineInput(BaseModel):
    hostname: str
    username: str
    port: int = 22
    password: Optional[str] = None
    keyfile: Optional[UploadFile] = None
    passphrase: Optional[str] = None

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
        return values

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v):
        def is_public_ip(ip):
            try:
                return not ipaddress.ip_address(ip).is_private
            except ValueError:
                return False

        # Check if it's a valid IP address
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", v):
            if not is_public_ip(v):
                raise ValueError(
                    "Invalid or non-public IP address: must be a valid public IP"
                )
            return v

        # If not an IP, try to resolve the domain
        try:
            ip = socket.gethostbyname(v)
            if not is_public_ip(ip):
                raise ValueError("Domain must resolve to a public IP address")
            return v
        except socket.gaierror:
            raise ValueError("Invalid hostname: unable to resolve to an IP address")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v

    class Config:
        arbitrary_types_allowed = True


class WorkerNodeInput(BaseModel):
    hostname: str
    username: str
    port: int = 22
    password: Optional[str] = None
    keyfile: Optional[UploadFile] = None
    passphrase: Optional[str] = None

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
        return values

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v):
        def is_private_ip(ip):
            try:
                return ipaddress.ip_address(ip).is_private
            except ValueError:
                return False

        # Check if it's a valid IP address
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", v):
            if not is_private_ip(v):
                raise ValueError(
                    "Invalid or non-private IP address: must be a valid private IP"
                )
            return v

        # If not an IP, try to resolve the domain
        try:
            ip = socket.gethostbyname(v)
            if not is_private_ip(ip):
                raise ValueError("Domain must resolve to a private IP address")
            return v
        except socket.gaierror:
            raise ValueError("Invalid hostname: unable to resolve to an IP address")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v

    class Config:
        arbitrary_types_allowed = True
