from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error message")
    code: str = Field(..., description="Machine-readable error code")
    details: dict | None = Field(default=None, description="Optional extra error details")


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str
