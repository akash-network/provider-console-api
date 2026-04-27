from base64 import b64decode
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, SecretStr, model_validator


class CloudflareConfig(BaseModel):
    api_token: SecretStr


class CloudDnsConfig(BaseModel):
    project: str
    service_account_json: SecretStr  # raw JSON or base64-encoded JSON

    @model_validator(mode="after")
    def _decode_if_base64(self):
        # Accept either raw JSON or base64-encoded JSON; normalize to raw JSON.
        raw = self.service_account_json.get_secret_value().strip()
        if not raw.startswith("{"):
            try:
                decoded = b64decode(raw).decode()
                if decoded.startswith("{"):
                    object.__setattr__(self, "service_account_json", SecretStr(decoded))
            except Exception:
                pass
        return self


class CertManagerInput(BaseModel):
    acme_email: Optional[EmailStr] = None
    use_staging: bool = False
    dns_provider: Literal["cloudflare", "clouddns"]
    cloudflare: Optional[CloudflareConfig] = None
    clouddns: Optional[CloudDnsConfig] = None

    class Config:
        extra = "forbid"

    @model_validator(mode="after")
    def _exactly_one_provider_block(self):
        if self.dns_provider == "cloudflare":
            if self.cloudflare is None or self.clouddns is not None:
                raise ValueError("dns_provider=cloudflare requires `cloudflare` block and no `clouddns` block")
        else:
            if self.clouddns is None or self.cloudflare is not None:
                raise ValueError("dns_provider=clouddns requires `clouddns` block and no `cloudflare` block")
        return self
