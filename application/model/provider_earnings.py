from pydantic import BaseModel
from typing import Optional


class EarningsData(BaseModel):
    """Model for the earnings data structure."""
    totalUAktEarned: float
    totalUUsdcEarned: float
    totalUUsdEarned: float
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "totalUAktEarned": 198821549.899183,
                "totalUUsdcEarned": 62549.30588271079,
                "totalUUsdEarned": 523063470.03441215
            }
        }


class ProviderEarningsResponse(BaseModel):
    """Response model for provider earnings data."""
    earnings: EarningsData
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "earnings": {
                    "totalUAktEarned": 198821549.899183,
                    "totalUUsdcEarned": 62549.30588271079,
                    "totalUUsdEarned": 523063470.03441215
                }
            }
        }


class ErrorResponse(BaseModel):
    """Standard error response model."""
    error: str
    message: str
    error_code: Optional[str] = None
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "error": "Invalid Date Range",
                "message": "From date must be before or equal to to date",
                "error_code": "EARNINGS_002"
            }
        } 