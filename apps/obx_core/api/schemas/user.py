from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class UserResponse(BaseModel):
    id: UUID
    discord_user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CreateUserRequest(BaseModel):
    discord_user_id: str = Field(..., min_length=1, max_length=64, description="Discord Snowflake User ID")
