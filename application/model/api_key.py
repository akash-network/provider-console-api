from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ApiKeyResponse(BaseModel):
    id: str
    wallet_address: str
    api_key: str
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]

    class Config:
        from_attributes = True
