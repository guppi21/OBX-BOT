import uuid
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from packages.database.session import get_db
from packages.shared.enums import TaskStatus, TaskType
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.api.schemas import (
    CreateTaskRequest,
    UpdateTaskRequest,
    TaskResponse,
    TasksListResponse,
    TaskAuditLogResponse,
    TaskAuditLogsListResponse,
)
from apps.obx_tasks.api.auth import verify_admin_token

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    request: CreateTaskRequest,
    db: Session = Depends(get_db),
    _is_admin: bool = Depends(verify_admin_token),
):
    """Creates a new social task (Admin only)."""
    service = TaskService(db)
    task = service.create_task(
        title=request.title,
        description=request.description,
        task_type=request.task_type,
        target_url=request.target_url,
        reward_per_user=request.reward_per_user,
        total_reward_pool=request.total_reward_pool,
        created_by=request.created_by,
        platform=request.platform,
        status=request.status,
        starts_at=request.starts_at,
        ends_at=request.ends_at,
    )
    return task


@router.get("", response_model=TasksListResponse, status_code=status.HTTP_200_OK)
def list_tasks(
    status: TaskStatus | None = Query(None, description="Filter by task status"),
    task_type: TaskType | None = Query(None, description="Filter by task type"),
    platform: str | None = Query(None, description="Filter by platform"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Lists social tasks with optional filtering and pagination."""
    service = TaskService(db)
    tasks, total = service.list_tasks(
        status=status,
        task_type=task_type,
        platform=platform,
        limit=limit,
        offset=offset,
    )
    return TasksListResponse(
        total=total,
        limit=limit,
        offset=offset,
        tasks=[TaskResponse.model_validate(t) for t in tasks],
    )


@router.get("/{task_id}", response_model=TaskResponse, status_code=status.HTTP_200_OK)
def get_task(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Retrieves detailed information for a single task."""
    service = TaskService(db)
    task = service.get_task(task_id)
    return task


@router.patch("/{task_id}", response_model=TaskResponse, status_code=status.HTTP_200_OK)
def update_task(
    task_id: uuid.UUID,
    request: UpdateTaskRequest,
    db: Session = Depends(get_db),
    _is_admin: bool = Depends(verify_admin_token),
):
    """Updates a task configuration, pool sizing, reward amount, or status with audit log (Admin only)."""
    service = TaskService(db)
    task = service.edit_task(
        task_id=task_id,
        changed_by=request.admin_id,
        title=request.title,
        description=request.description,
        target_url=request.target_url,
        task_type=request.task_type,
        platform=request.platform,
        total_reward_pool=request.total_reward_pool,
        reward_per_user=request.reward_per_user,
        status=request.status,
        starts_at=request.starts_at,
        ends_at=request.ends_at,
    )
    return task


@router.get("/{task_id}/audit-logs", response_model=TaskAuditLogsListResponse, status_code=status.HTTP_200_OK)
def get_task_audit_logs(
    task_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _is_admin: bool = Depends(verify_admin_token),
):
    """Retrieves configuration change audit history for a task (Admin only)."""
    service = TaskService(db)
    logs, total = service.get_task_audit_logs(task_id=task_id, limit=limit, offset=offset)
    return TaskAuditLogsListResponse(
        total=total,
        limit=limit,
        offset=offset,
        audit_logs=[TaskAuditLogResponse.model_validate(log) for log in logs],
    )
