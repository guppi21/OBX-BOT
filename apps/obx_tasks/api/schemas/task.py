import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from packages.shared.enums import TaskStatus, TaskType, TaskPlatform


class CreateTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255, description="Task Title")
    description: str = Field(..., min_length=1, description="Task detailed description and requirements")
    platform: str = Field(default=TaskPlatform.X.value, max_length=32)
    task_type: TaskType = Field(..., description="Type of social action required")
    target_url: str = Field(..., min_length=1, max_length=1024, description="Target post/URL to interact with")
    reward_per_user: int = Field(..., gt=0, description="OBX reward granted per approved submission")
    total_reward_pool: int = Field(..., gt=0, description="Total OBX allocated for this task's reward pool")
    created_by: str = Field(..., min_length=1, max_length=64, description="Discord User ID of task creator")
    status: TaskStatus = Field(default=TaskStatus.ACTIVE)
    starts_at: datetime | None = Field(default=None)
    ends_at: datetime | None = Field(default=None)


class UpdateTaskRequest(BaseModel):
    admin_id: str = Field(default="admin_api", min_length=1, max_length=64, description="Admin identifier performing the update")
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    target_url: str | None = None
    task_type: TaskType | None = None
    platform: str | None = None
    total_reward_pool: int | None = Field(default=None, gt=0)
    reward_per_user: int | None = Field(default=None, gt=0)
    status: TaskStatus | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class TaskResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    platform: str
    task_type: TaskType
    target_url: str
    reward_per_user: int
    total_reward_pool: int
    distributed_reward: int
    remaining_reward_pool: int
    max_approvals: int
    approved_count: int
    status: TaskStatus
    starts_at: datetime | None
    ends_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TasksListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    tasks: list[TaskResponse]


class TaskAuditLogResponse(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    changed_by: str
    field_name: str
    old_value: str | None = None
    new_value: str | None = None
    changed_at: datetime

    model_config = {"from_attributes": True}


class TaskAuditLogsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    audit_logs: list[TaskAuditLogResponse]
