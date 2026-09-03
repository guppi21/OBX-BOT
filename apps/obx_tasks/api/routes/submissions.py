import uuid
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from packages.database.session import get_db
from packages.shared.enums import SubmissionStatus
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.api.schemas import (
    CreateSubmissionRequest,
    ApproveSubmissionRequest,
    RejectSubmissionRequest,
    SubmissionResponse,
    SubmissionsListResponse,
)
from apps.obx_tasks.api.auth import verify_admin_token

router = APIRouter(tags=["Submissions"])


@router.post("/tasks/{task_id}/submissions", response_model=SubmissionResponse, status_code=status.HTTP_201_CREATED)
def submit_proof(
    task_id: uuid.UUID,
    request: CreateSubmissionRequest,
    db: Session = Depends(get_db),
):
    """Submits completion proof for a social task."""
    service = TaskService(db)
    submission = service.submit_task(
        task_id=task_id,
        discord_user_id=request.discord_user_id,
        x_username=request.x_username,
        proof_url=request.proof_url,
        proof_text=request.proof_text,
        proof_screenshot_url=request.proof_screenshot_url,
    )
    return submission


@router.get("/tasks/{task_id}/submissions", response_model=SubmissionsListResponse, status_code=status.HTTP_200_OK)
def list_task_submissions(
    task_id: uuid.UUID,
    status: SubmissionStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _is_admin: bool = Depends(verify_admin_token),
):
    """Lists submissions for a task (Admin only)."""
    service = TaskService(db)
    subs, total = service.list_submissions(
        task_id=task_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return SubmissionsListResponse(
        total=total,
        limit=limit,
        offset=offset,
        submissions=[SubmissionResponse.model_validate(s) for s in subs],
    )


@router.post("/submissions/{submission_id}/approve", response_model=SubmissionResponse, status_code=status.HTTP_200_OK)
def approve_submission(
    submission_id: uuid.UUID,
    request: ApproveSubmissionRequest,
    db: Session = Depends(get_db),
    _is_admin: bool = Depends(verify_admin_token),
):
    """Approves a task submission and credits OBX through OBX Core (Admin only)."""
    service = TaskService(db)
    submission = service.approve_submission(
        submission_id=submission_id,
        reviewer_discord_id=request.reviewer_discord_id,
    )
    return submission


@router.post("/submissions/{submission_id}/reject", response_model=SubmissionResponse, status_code=status.HTTP_200_OK)
def reject_submission(
    submission_id: uuid.UUID,
    request: RejectSubmissionRequest,
    db: Session = Depends(get_db),
    _is_admin: bool = Depends(verify_admin_token),
):
    """Rejects a task submission with a rejection reason (Admin only)."""
    service = TaskService(db)
    submission = service.reject_submission(
        submission_id=submission_id,
        reviewer_discord_id=request.reviewer_discord_id,
        rejection_reason=request.reason,
    )
    return submission


@router.get("/users/{discord_user_id}/submissions", response_model=SubmissionsListResponse, status_code=status.HTTP_200_OK)
def get_user_submissions(
    discord_user_id: str,
    status: SubmissionStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Retrieves submission history for a Discord user."""
    service = TaskService(db)
    subs, total = service.list_submissions(
        discord_user_id=discord_user_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return SubmissionsListResponse(
        total=total,
        limit=limit,
        offset=offset,
        submissions=[SubmissionResponse.model_validate(s) for s in subs],
    )
