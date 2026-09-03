import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from packages.shared.enums import SubmissionStatus


class CreateSubmissionRequest(BaseModel):
    discord_user_id: str = Field(..., min_length=1, max_length=64, description="Discord Snowflake User ID")
    x_username: str = Field(..., min_length=1, max_length=64, description="X / Twitter handle")
    proof_url: str = Field(..., min_length=1, max_length=1024, description="URL linking to proof of task completion")
    proof_text: str = Field(..., min_length=1, description="Text explanation of proof")
    proof_screenshot_url: str | None = Field(default=None, max_length=1024, description="Optional image/screenshot URL")


class ApproveSubmissionRequest(BaseModel):
    reviewer_discord_id: str = Field(..., min_length=1, max_length=64, description="Discord ID of reviewing admin")


class RejectSubmissionRequest(BaseModel):
    reviewer_discord_id: str = Field(..., min_length=1, max_length=64, description="Discord ID of reviewing admin")
    reason: str = Field(..., min_length=1, max_length=500, description="Reason for submission rejection")


class SubmissionResponse(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    discord_user_id: str
    x_username: str
    proof_url: str
    proof_text: str
    proof_screenshot_url: str | None
    status: SubmissionStatus
    submitted_at: datetime
    reviewed_by: str | None
    reviewed_at: datetime | None
    rejection_reason: str | None
    reward_amount: int | None
    obx_transaction_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class SubmissionsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    submissions: list[SubmissionResponse]
