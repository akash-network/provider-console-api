from base64 import b64decode
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, SecretStr, field_validator, model_validator


class CloudflareConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_token: SecretStr


class CloudDnsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    service_account_json: SecretStr  # raw JSON or base64-encoded JSON

    @field_validator("service_account_json", mode="before")
    @classmethod
    def _normalize_b64(cls, v):
        if not isinstance(v, str):
            return v
        raw = v.strip()
        if raw.startswith("{"):
            return raw
        try:
            decoded = b64decode(raw).decode()
        except Exception as exc:
            raise ValueError(
                "service_account_json must be raw JSON (starting with '{') "
                "or base64-encoded JSON"
            ) from exc
        if not decoded.startswith("{"):
            raise ValueError(
                "service_account_json must be raw JSON (starting with '{') "
                "or base64-encoded JSON"
            )
        return decoded


class CertManagerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acme_email: Optional[EmailStr] = None
    use_staging: bool = False
    dns_provider: Literal["cloudflare", "clouddns"]
    cloudflare: Optional[CloudflareConfig] = None
    clouddns: Optional[CloudDnsConfig] = None

    @model_validator(mode="after")
    def _exactly_one_provider_block(self):
        if self.dns_provider == "cloudflare":
            if self.cloudflare is None or self.clouddns is not None:
                raise ValueError("dns_provider=cloudflare requires `cloudflare` block and no `clouddns` block")
        else:
            if self.clouddns is None or self.cloudflare is not None:
                raise ValueError("dns_provider=clouddns requires `clouddns` block and no `cloudflare` block")
        return self
