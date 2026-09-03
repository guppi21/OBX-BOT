import uuid
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Tuple, List, Optional
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, func

from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.database.models.submission_audit_log import SubmissionAuditLog
from packages.database.models.task_audit_log import TaskAuditLog
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus, ReferenceType
from packages.shared.exceptions import (
    TaskNotFoundError,
    TaskNotActiveError,
    TaskExpiredError,
    InvalidRewardPoolError,
    RewardPoolExhaustedError,
    SubmissionNotFoundError,
    DuplicateSubmissionError,
    InvalidSubmissionStatusError,
    UnauthorizedAdminError,
)
from packages.shared.logging import get_logger
from apps.obx_tasks.services.obx_client import OBXCoreClient

logger = get_logger("obx.tasks.service")

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _normalize_dt(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _validate_proof_url(url: str) -> None:
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("proof_url must be a valid HTTP or HTTPS URL.")


def _sanitize_x_username(username: str) -> str:
    cleaned = username.strip().lstrip("@")
    if not cleaned or not re.match(r"^[A-Za-z0-9_]{1,50}$", cleaned):
        raise ValueError("Invalid X username. Must contain 1-50 alphanumeric characters or underscores.")
    return cleaned


def validate_and_save_proof_image(
    file_bytes: bytes,
    original_filename: str,
    submission_id: uuid.UUID | str,
    upload_dir: Optional[str] = None,
    max_size: Optional[int] = None,
) -> str:
    """Validates an uploaded image and safely writes it to local proof storage."""
    from packages.shared.config import get_settings
    settings = get_settings()
    max_bytes = max_size or settings.PROOF_MAX_FILE_SIZE_BYTES
    target_dir = Path(upload_dir or settings.PROOF_UPLOAD_DIR)

    if len(file_bytes) > max_bytes:
        raise ValueError(f"Uploaded proof image exceeds maximum allowed size ({max_bytes / (1024*1024):.1f} MB).")

    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image type '{ext}'. Allowed types: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}")

    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{str(submission_id)}_{uuid.uuid4().hex[:8]}{ext}"
    dest_path = target_dir / safe_name
    dest_path.write_bytes(file_bytes)

    return str(dest_path)


class TaskService:
    def __init__(self, session: Session):
        self.session = session

    def create_task(
        self,
        title: str,
        description: str,
        task_type: TaskType | str,
        target_url: str,
        reward_per_user: int,
        total_reward_pool: int,
        created_by: str,
        platform: str = "X",
        status: TaskStatus = TaskStatus.ACTIVE,
        starts_at: Optional[datetime] = None,
        ends_at: Optional[datetime] = None,
        proof_required: bool = True,
        allow_image_proof: bool = True,
        notification_type: str = "DEFAULT",
        custom_notification_template: Optional[str] = None,
        preview_platform: Optional[str] = None,
        preview_author: Optional[str] = None,
        preview_title: Optional[str] = None,
        preview_description: Optional[str] = None,
        preview_image_url: Optional[str] = None,
        preview_source: Optional[str] = None,
        preview_status: Optional[str] = None,
        preview_fetched_at: Optional[datetime] = None,
        preview_author_override: Optional[str] = None,
        preview_title_override: Optional[str] = None,
        preview_text_override: Optional[str] = None,
        preview_image_override: Optional[str] = None,
        required_actions: Optional[str] = None,
    ) -> Task:
        """Creates a new social task with reward pool validation."""
        if not title or not title.strip():
            raise ValueError("Task title must be a non-empty string.")
        if not target_url or not target_url.strip():
            raise ValueError("Target URL must be a non-empty string.")
        if reward_per_user <= 0:
            raise InvalidRewardPoolError("reward_per_user must be a positive integer (> 0).")
        if total_reward_pool < reward_per_user:
            raise InvalidRewardPoolError(
                f"total_reward_pool ({total_reward_pool}) cannot be smaller than reward_per_user ({reward_per_user})."
            )
        if starts_at and ends_at and ends_at <= starts_at:
            raise ValueError("ends_at must be strictly after starts_at.")

        if isinstance(task_type, str):
            task_type = TaskType(task_type)
        if isinstance(status, str):
            status = TaskStatus(status)

        # Derive preview metadata via sync extraction if not provided
        if not preview_platform or not preview_author or not preview_description:
            from apps.obx_tasks.services.url_preview_service import UrlPreviewService
            try:
                extracted = UrlPreviewService._sync_fetch_preview(target_url.strip())
            except Exception:
                extracted = UrlPreviewService._fallback_metadata(target_url.strip())

            preview_platform = preview_platform or extracted.platform
            if not preview_author:
                if extracted.handle and extracted.author and extracted.handle != extracted.author:
                    preview_author = f"{extracted.author}\n   {extracted.handle}"
                else:
                    preview_author = extracted.author or extracted.handle
            preview_title = preview_title or extracted.title
            preview_description = preview_description or extracted.description
            preview_image_url = preview_image_url or extracted.image_url
            preview_source = preview_source or extracted.source
            preview_status = preview_status or extracted.status
            preview_fetched_at = preview_fetched_at or extracted.fetched_at

        task = Task(
            title=title.strip(),
            description=description.strip(),
            platform=platform,
            task_type=task_type,
            target_url=target_url.strip(),
            reward_per_user=reward_per_user,
            total_reward_pool=total_reward_pool,
            distributed_reward=0,
            status=status,
            proof_required=proof_required,
            allow_image_proof=allow_image_proof,
            notification_type=notification_type or "DEFAULT",
            custom_notification_template=custom_notification_template,
            preview_platform=preview_platform,
            preview_author=preview_author,
            preview_title=preview_title,
            preview_description=preview_description,
            preview_image_url=preview_image_url,
            preview_source=preview_source,
            preview_status=preview_status,
            preview_fetched_at=preview_fetched_at,
            preview_author_override=preview_author_override,
            preview_title_override=preview_title_override,
            preview_text_override=preview_text_override,
            preview_image_override=preview_image_override,
            required_actions=required_actions.strip() if required_actions else None,
            starts_at=starts_at,
            ends_at=ends_at,
            created_by=str(created_by).strip(),
        )
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)

        # Record initial audit entry
        initial_log = TaskAuditLog(
            task_id=task.id,
            changed_by=str(created_by).strip(),
            field_name="creation",
            old_value=None,
            new_value=f"Task created with pool={total_reward_pool}, reward={reward_per_user}, status={status.value}",
        )
        self.session.add(initial_log)
        self.session.commit()

        logger.info(
            "Task created: ID=%s, Title='%s', Reward=%d OBX, Pool=%d OBX, Status=%s",
            task.id,
            task.title,
            task.reward_per_user,
            task.total_reward_pool,
            task.status,
        )
        return task

    def get_task(self, task_id: uuid.UUID | str) -> Task:
        """Retrieves a task by ID or raises TaskNotFoundError."""
        if isinstance(task_id, str):
            try:
                task_id = uuid.UUID(task_id)
            except ValueError:
                raise TaskNotFoundError(str(task_id))

        task = self.session.execute(
            select(Task).where(Task.id == task_id)
        ).scalar_one_or_none()

        if not task:
            raise TaskNotFoundError(str(task_id))
        return task

    def list_tasks(
        self,
        status: Optional[TaskStatus | str] = None,
        task_type: Optional[TaskType | str] = None,
        platform: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Task], int]:
        """Lists tasks with optional filtering and pagination."""
        query = select(Task)

        if status:
            if isinstance(status, str):
                status = TaskStatus(status)
            query = query.where(Task.status == status)

        if task_type:
            if isinstance(task_type, str):
                task_type = TaskType(task_type)
            query = query.where(Task.task_type == task_type)

        if platform:
            query = query.where(Task.platform == platform)

        total = self.session.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar() or 0

        tasks = self.session.execute(
            query.order_by(Task.created_at.desc()).offset(offset).limit(limit)
        ).scalars().all()

        return list(tasks), total

    def update_task_status(self, task_id: uuid.UUID | str, status: TaskStatus | str, changed_by: str = "system") -> Task:
        """Updates a task's status with audit log."""
        return self.edit_task(task_id=task_id, changed_by=changed_by, status=status)

    def edit_task(
        self,
        task_id: uuid.UUID | str,
        changed_by: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        target_url: Optional[str] = None,
        task_type: Optional[TaskType | str] = None,
        platform: Optional[str] = None,
        total_reward_pool: Optional[int] = None,
        reward_per_user: Optional[int] = None,
        status: Optional[TaskStatus | str] = None,
        starts_at: Optional[datetime] = None,
        ends_at: Optional[datetime] = None,
        proof_required: Optional[bool] = None,
        allow_image_proof: Optional[bool] = None,
        notification_type: Optional[str] = None,
        custom_notification_template: Optional[str] = None,
        cancellation_reason: Optional[str] = None,
        preview_author_override: Optional[str] = None,
        preview_title_override: Optional[str] = None,
        preview_text_override: Optional[str] = None,
        preview_image_override: Optional[str] = None,
        required_actions: Optional[str] = None,
    ) -> Task:
        """Edits task configuration with pool validations and audit logging.
        
        Validations:
        - total_reward_pool cannot be reduced below distributed_reward.
        - reward_per_user must be > 0.
        - remaining_reward_pool must always remain >= 0.
        - Reward pool expansion does NOT automatically reactivate a COMPLETED task.
        - Status changes MUST remain explicit admin actions.
        - Historical approved submissions and balances are NEVER modified.
        """
        if isinstance(task_id, str):
            try:
                task_id = uuid.UUID(task_id)
            except ValueError:
                raise TaskNotFoundError(str(task_id))

        task = self.session.execute(
            select(Task)
            .where(Task.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

        if not task:
            raise TaskNotFoundError(str(task_id))

        audit_entries = []

        # Validate total_reward_pool change
        if total_reward_pool is not None:
            if total_reward_pool < task.distributed_reward:
                raise InvalidRewardPoolError(
                    f"Cannot reduce total_reward_pool to {total_reward_pool} OBX below already distributed amount of {task.distributed_reward} OBX."
                )
            if total_reward_pool != task.total_reward_pool:
                audit_entries.append(
                    TaskAuditLog(
                        task_id=task.id,
                        changed_by=str(changed_by).strip(),
                        field_name="total_reward_pool",
                        old_value=str(task.total_reward_pool),
                        new_value=str(total_reward_pool),
                    )
                )
                task.total_reward_pool = total_reward_pool

        # Validate reward_per_user change
        if reward_per_user is not None:
            if reward_per_user <= 0:
                raise InvalidRewardPoolError("reward_per_user must be a positive integer (> 0).")
            if reward_per_user != task.reward_per_user:
                audit_entries.append(
                    TaskAuditLog(
                        task_id=task.id,
                        changed_by=str(changed_by).strip(),
                        field_name="reward_per_user",
                        old_value=str(task.reward_per_user),
                        new_value=str(reward_per_user),
                    )
                )
                task.reward_per_user = reward_per_user

        # Validate text / metadata changes
        if title is not None and title.strip() and title.strip() != task.title:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="title",
                    old_value=task.title,
                    new_value=title.strip(),
                )
            )
            task.title = title.strip()

        if description is not None and description.strip() and description.strip() != task.description:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="description",
                    old_value=task.description,
                    new_value=description.strip(),
                )
            )
            task.description = description.strip()

        if target_url is not None and target_url.strip() and target_url.strip() != task.target_url:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="target_url",
                    old_value=task.target_url,
                    new_value=target_url.strip(),
                )
            )
            task.target_url = target_url.strip()
            from apps.obx_tasks.services.url_preview_service import UrlPreviewService
            fb = UrlPreviewService._fallback_metadata(task.target_url)
            task.preview_platform = fb.platform
            task.preview_author = fb.author
            task.preview_title = fb.title
            task.preview_description = None
            task.preview_image_url = None
            task.preview_fetched_at = fb.fetched_at

        if task_type is not None:
            if isinstance(task_type, str):
                task_type = TaskType(task_type)
            if task_type != task.task_type:
                audit_entries.append(
                    TaskAuditLog(
                        task_id=task.id,
                        changed_by=str(changed_by).strip(),
                        field_name="task_type",
                        old_value=task.task_type.value,
                        new_value=task_type.value,
                    )
                )
                task.task_type = task_type

        if platform is not None and platform.strip() and platform.strip() != task.platform:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="platform",
                    old_value=task.platform,
                    new_value=platform.strip(),
                )
            )
            task.platform = platform.strip()

        if starts_at is not None and starts_at != task.starts_at:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="starts_at",
                    old_value=str(task.starts_at),
                    new_value=str(starts_at),
                )
            )
            task.starts_at = starts_at

        if ends_at is not None and ends_at != task.ends_at:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="ends_at",
                    old_value=str(task.ends_at),
                    new_value=str(ends_at),
                )
            )
            task.ends_at = ends_at

        # Validate proof and notification settings
        if proof_required is not None and proof_required != task.proof_required:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="proof_required",
                    old_value=str(task.proof_required),
                    new_value=str(proof_required),
                )
            )
            task.proof_required = proof_required

        if allow_image_proof is not None and allow_image_proof != task.allow_image_proof:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="allow_image_proof",
                    old_value=str(task.allow_image_proof),
                    new_value=str(allow_image_proof),
                )
            )
            task.allow_image_proof = allow_image_proof

        if notification_type is not None and notification_type.strip() != task.notification_type:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="notification_type",
                    old_value=task.notification_type,
                    new_value=notification_type.strip(),
                )
            )
            task.notification_type = notification_type.strip()

        if custom_notification_template is not None and custom_notification_template != task.custom_notification_template:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="custom_notification_template",
                    old_value=task.custom_notification_template,
                    new_value=custom_notification_template,
                )
            )
            task.custom_notification_template = custom_notification_template

        if cancellation_reason is not None and cancellation_reason != task.cancellation_reason:
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="cancellation_reason",
                    old_value=task.cancellation_reason,
                    new_value=cancellation_reason,
                )
            )
            task.cancellation_reason = cancellation_reason

        # Admin preview overrides
        if preview_author_override is not None:
            task.preview_author_override = preview_author_override
        if preview_title_override is not None:
            task.preview_title_override = preview_title_override
        if preview_text_override is not None:
            task.preview_text_override = preview_text_override
        if preview_image_override is not None:
            task.preview_image_override = preview_image_override

        if required_actions is not None and required_actions.strip() != (task.required_actions or ""):
            audit_entries.append(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(changed_by).strip(),
                    field_name="required_actions",
                    old_value=task.required_actions,
                    new_value=required_actions.strip() if required_actions else None,
                )
            )
            task.required_actions = required_actions.strip() if required_actions else None

        # Validate explicit status change ONLY
        if status is not None:
            if isinstance(status, str):
                status = TaskStatus(status)
            if status != task.status:
                audit_entries.append(
                    TaskAuditLog(
                        task_id=task.id,
                        changed_by=str(changed_by).strip(),
                        field_name="status",
                        old_value=task.status.value,
                        new_value=status.value,
                    )
                )
                task.status = status

        # Save audit logs
        for log_entry in audit_entries:
            self.session.add(log_entry)

        self.session.commit()
        self.session.refresh(task)

        logger.info(
            "Task %s edited by %s: Pool=%d, Reward=%d, Status=%s (%d audit entries)",
            task.id,
            changed_by,
            task.total_reward_pool,
            task.reward_per_user,
            task.status.value,
            len(audit_entries),
        )
        return task

    def update_task_preview(
        self,
        task_id: uuid.UUID | str,
        preview_platform: Optional[str] = None,
        preview_author: Optional[str] = None,
        preview_title: Optional[str] = None,
        preview_description: Optional[str] = None,
        preview_image_url: Optional[str] = None,
        preview_source: Optional[str] = None,
        preview_status: Optional[str] = None,
        preview_author_override: Optional[str] = None,
        preview_title_override: Optional[str] = None,
        preview_text_override: Optional[str] = None,
        preview_image_override: Optional[str] = None,
    ) -> Task:
        t_uuid = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id))
        task = self.session.query(Task).filter_by(id=t_uuid).first()
        if not task:
            raise TaskNotFoundError(f"Task with ID {task_id} not found.")
        if preview_platform is not None:
            task.preview_platform = preview_platform
        if preview_author is not None:
            task.preview_author = preview_author
        if preview_title is not None:
            task.preview_title = preview_title
        if preview_description is not None:
            task.preview_description = preview_description
        if preview_image_url is not None:
            task.preview_image_url = preview_image_url
        if preview_source is not None:
            task.preview_source = preview_source
        if preview_status is not None:
            task.preview_status = preview_status
        if preview_author_override is not None:
            task.preview_author_override = preview_author_override
        if preview_title_override is not None:
            task.preview_title_override = preview_title_override
        if preview_text_override is not None:
            task.preview_text_override = preview_text_override
        if preview_image_override is not None:
            task.preview_image_override = preview_image_override
        task.preview_fetched_at = datetime.now(timezone.utc)
        self.session.commit()
        self.session.refresh(task)
        return task

    async def refresh_task_preview(self, task_id: uuid.UUID | str) -> Tuple[Task, Any]:
        """Re-runs the URL preview extraction pipeline and updates stored metadata."""
        t_uuid = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id))
        task = self.session.query(Task).filter_by(id=t_uuid).first()
        if not task:
            raise TaskNotFoundError(f"Task with ID {task_id} not found.")

        from apps.obx_tasks.services.url_preview_service import UrlPreviewService
        meta = await UrlPreviewService.fetch_preview(task.target_url, task_id=str(task.id))

        task.preview_platform = meta.platform
        author_repr = meta.author
        if meta.handle and meta.handle != meta.author:
            author_repr = f"{meta.author}\n   {meta.handle}" if meta.author else meta.handle
        task.preview_author = author_repr
        task.preview_title = meta.title
        task.preview_description = meta.description
        task.preview_image_url = meta.image_url
        task.preview_source = meta.source
        task.preview_status = meta.status
        task.preview_fetched_at = datetime.now(timezone.utc)

        self.session.commit()
        self.session.refresh(task)
        return task, meta

    def cancel_task(
        self,
        task_id: uuid.UUID | str,
        cancelled_by: str,
        reason: Optional[str] = None,
        pending_action: Optional[str] = None,
    ) -> Task:
        """Cancels a task, preventing new submissions and handling pending submissions per admin choice.
        
        pending_action:
        - "APPROVE": Approve eligible pending submissions within remaining pool
        - "REJECT": Reject all pending submissions
        - "REVIEW" / None: Leave pending for individual review
        """
        if isinstance(task_id, str):
            try:
                task_id = uuid.UUID(task_id)
            except ValueError:
                raise TaskNotFoundError(str(task_id))

        task = self.session.execute(
            select(Task)
            .where(Task.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

        if not task:
            raise TaskNotFoundError(str(task_id))

        if task.status == TaskStatus.CANCELLED:
            logger.info("Task %s is already CANCELLED. Idempotent return.", task.id)
            return task

        old_status = task.status.value
        task.status = TaskStatus.CANCELLED
        clean_reason = reason.strip() if reason and reason.strip() else None
        if clean_reason:
            task.cancellation_reason = clean_reason

        # Record audit entries
        self.session.add(
            TaskAuditLog(
                task_id=task.id,
                changed_by=str(cancelled_by).strip(),
                field_name="status",
                old_value=old_status,
                new_value=TaskStatus.CANCELLED.value,
            )
        )
        if clean_reason:
            self.session.add(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(cancelled_by).strip(),
                    field_name="cancellation_reason",
                    old_value=None,
                    new_value=clean_reason,
                )
            )
        if pending_action:
            self.session.add(
                TaskAuditLog(
                    task_id=task.id,
                    changed_by=str(cancelled_by).strip(),
                    field_name="pending_disposition",
                    old_value=None,
                    new_value=pending_action.upper(),
                )
            )

        # Handle pending submissions
        pending_subs = self.session.execute(
            select(TaskSubmission)
            .where(
                TaskSubmission.task_id == task.id,
                TaskSubmission.status == SubmissionStatus.PENDING,
            )
        ).scalars().all()

        action_upper = (pending_action or "").upper()
        if action_upper == "APPROVE":
            for sub in pending_subs:
                if task.remaining_reward_pool >= task.reward_per_user:
                    try:
                        self.approve_submission(sub.id, reviewer_discord_id=str(cancelled_by).strip())
                    except Exception as exc:
                        logger.warning("Failed to auto-approve sub %s during cancellation: %s", sub.id, exc)
                else:
                    try:
                        self.reject_submission(
                            sub.id,
                            reviewer_discord_id=str(cancelled_by).strip(),
                            rejection_reason="Task cancelled and reward pool exhausted.",
                        )
                    except Exception as exc:
                        logger.warning("Failed to reject sub %s during cancellation: %s", sub.id, exc)
        elif action_upper == "REJECT":
            rejection_text = f"Task cancelled by administrator: {clean_reason or 'No reason provided.'}"
            for sub in pending_subs:
                try:
                    self.reject_submission(
                        sub.id,
                        reviewer_discord_id=str(cancelled_by).strip(),
                        rejection_reason=rejection_text,
                    )
                except Exception as exc:
                    logger.warning("Failed to reject sub %s during cancellation: %s", sub.id, exc)

        self.session.commit()
        self.session.refresh(task)
        logger.info(
            "Task %s cancelled by %s (Reason='%s', PendingAction='%s')",
            task.id,
            cancelled_by,
            clean_reason,
            pending_action,
        )
        return task

    def safe_delete_task(
        self,
        task_id: uuid.UUID | str,
        deleted_by: str,
    ) -> bool:
        """Safely deletes an unused task with no financial or submission history.
        Blocks deletion if the task has approved submissions, pending submissions,
        or ledger activity.
        """
        if isinstance(task_id, str):
            try:
                task_id = uuid.UUID(task_id)
            except ValueError:
                raise TaskNotFoundError(str(task_id))

        task = self.session.execute(
            select(Task)
            .where(Task.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

        if not task:
            raise TaskNotFoundError(str(task_id))

        if task.distributed_reward > 0:
            raise ValueError("This task cannot be deleted because it has distributed reward payments. Use Cancel / Archive instead.")

        total_subs = self.session.execute(
            select(func.count()).select_from(TaskSubmission).where(TaskSubmission.task_id == task.id)
        ).scalar() or 0

        if total_subs > 0:
            raise ValueError(f"This task cannot be deleted because it has {total_subs} submission(s) recorded. Use Cancel / Archive instead.")

        from packages.database.models.ledger import LedgerEntry
        has_ledger = self.session.execute(
            select(func.count()).select_from(LedgerEntry).where(LedgerEntry.reference_id == str(task.id))
        ).scalar() or 0
        if has_ledger > 0:
            raise ValueError("This task cannot be deleted because it has financial ledger history. Use Cancel / Archive instead.")

        from packages.database.models.channel_config import PublishedMessage
        published = self.session.execute(
            select(PublishedMessage).where(PublishedMessage.source_id == str(task.id))
        ).scalars().all()
        for pm in published:
            self.session.delete(pm)

        self.session.delete(task)
        self.session.commit()
        logger.info("Task %s safely deleted by %s", task_id, deleted_by)
        return True

    def get_task_audit_logs(
        self,
        task_id: uuid.UUID | str,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[TaskAuditLog], int]:
        """Retrieves paginated audit log entries for a task."""
        if isinstance(task_id, str):
            try:
                task_id = uuid.UUID(task_id)
            except ValueError:
                return [], 0

        query = select(TaskAuditLog).where(TaskAuditLog.task_id == task_id)

        total = self.session.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar() or 0

        logs = self.session.execute(
            query.order_by(TaskAuditLog.changed_at.desc()).offset(offset).limit(limit)
        ).scalars().all()

        return list(logs), total

    def submit_task(
        self,
        task_id: uuid.UUID | str,
        discord_user_id: str,
        x_username: str,
        proof_url: str,
        proof_text: Optional[str] = None,
        proof_screenshot_url: Optional[str] = None,
    ) -> TaskSubmission:
        """Submits user proof for a social task."""
        if not discord_user_id or not str(discord_user_id).strip():
            raise ValueError("discord_user_id must be provided.")
        if not x_username or not x_username.strip():
            raise ValueError("x_username must be provided.")
        if not proof_url or not proof_url.strip():
            raise ValueError("proof_url must be provided.")

        _validate_proof_url(proof_url.strip())
        clean_x_handle = _sanitize_x_username(x_username)

        task = self.get_task(task_id)
        now = datetime.now(timezone.utc)

        # Check if task is active
        if task.status != TaskStatus.ACTIVE:
            raise TaskNotActiveError(str(task.id), task.status.value)

        # Check task time constraints
        starts_at = _normalize_dt(task.starts_at)
        ends_at = _normalize_dt(task.ends_at)

        if starts_at and now < starts_at:
            raise TaskNotActiveError(str(task.id), f"Starts at {starts_at}")
        if ends_at and now > ends_at:
            task.status = TaskStatus.EXPIRED
            self.session.commit()
            raise TaskExpiredError(str(task.id))

        # Check if pool is already exhausted
        if task.remaining_reward_pool < task.reward_per_user:
            task.status = TaskStatus.COMPLETED
            self.session.commit()
            raise RewardPoolExhaustedError(str(task.id), task.remaining_reward_pool, task.reward_per_user)

        # Check for duplicate submission
        existing = self.session.execute(
            select(TaskSubmission).where(
                TaskSubmission.task_id == task.id,
                TaskSubmission.discord_user_id == str(discord_user_id).strip(),
            )
        ).scalar_one_or_none()

        if existing:
            raise DuplicateSubmissionError(str(task.id), str(discord_user_id))

        clean_proof_text = proof_text.strip() if proof_text and proof_text.strip() else "Proof Link"
        submission = TaskSubmission(
            task_id=task.id,
            discord_user_id=str(discord_user_id).strip(),
            x_username=clean_x_handle,
            proof_url=proof_url.strip(),
            proof_text=clean_proof_text,
            proof_screenshot_url=proof_screenshot_url.strip() if proof_screenshot_url else None,
            status=SubmissionStatus.PENDING,
        )
        self.session.add(submission)
        try:
            self.session.commit()
            self.session.refresh(submission)
            logger.info(
                "Task submission created: ID=%s, Task=%s, User=%s, X=%s",
                submission.id,
                task.id,
                discord_user_id,
                clean_x_handle,
            )
            return submission
        except IntegrityError:
            self.session.rollback()
            raise DuplicateSubmissionError(str(task.id), str(discord_user_id))

    def get_submission(self, submission_id: uuid.UUID | str) -> TaskSubmission:
        """Retrieves a submission by ID or raises SubmissionNotFoundError."""
        if isinstance(submission_id, str):
            try:
                submission_id = uuid.UUID(submission_id)
            except ValueError:
                raise SubmissionNotFoundError(str(submission_id))

        submission = self.session.execute(
            select(TaskSubmission)
            .options(joinedload(TaskSubmission.task))
            .where(TaskSubmission.id == submission_id)
        ).scalar_one_or_none()

        if not submission:
            raise SubmissionNotFoundError(str(submission_id))
        return submission

    def list_submissions(
        self,
        task_id: Optional[uuid.UUID | str] = None,
        status: Optional[SubmissionStatus | str] = None,
        discord_user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[TaskSubmission], int]:
        """Lists task submissions with optional filters."""
        query = select(TaskSubmission).options(joinedload(TaskSubmission.task))

        if task_id:
            if isinstance(task_id, str):
                try:
                    task_id = uuid.UUID(task_id)
                except ValueError:
                    return [], 0
            query = query.where(TaskSubmission.task_id == task_id)

        if status:
            if isinstance(status, str):
                status = SubmissionStatus(status)
            query = query.where(TaskSubmission.status == status)

        if discord_user_id:
            query = query.where(TaskSubmission.discord_user_id == str(discord_user_id).strip())

        total = self.session.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar() or 0

        submissions = self.session.execute(
            query.order_by(TaskSubmission.submitted_at.desc()).offset(offset).limit(limit)
        ).scalars().all()

        return list(submissions), total

    def approve_submission(
        self,
        submission_id: uuid.UUID | str,
        reviewer_discord_id: str,
        obx_client: Optional[OBXCoreClient] = None,
    ) -> TaskSubmission:
        """Atomically approves a task submission, checks reward pool, and credits OBX through OBX Core."""
        client = obx_client or OBXCoreClient(session=self.session)

        if isinstance(submission_id, str):
            try:
                submission_id = uuid.UUID(submission_id)
            except ValueError:
                raise SubmissionNotFoundError(str(submission_id))

        logger.info("[APPROVAL] Submission approval started: ID=%s by Reviewer=%s", submission_id, reviewer_discord_id)
        submission = self.session.execute(
            select(TaskSubmission)
            .where(TaskSubmission.id == submission_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

        if not submission:
            raise SubmissionNotFoundError(str(submission_id))

        if submission.status == SubmissionStatus.APPROVED:
            logger.info("Submission %s is already approved. Returning idempotently.", submission.id)
            return submission

        if submission.status != SubmissionStatus.PENDING:
            raise InvalidSubmissionStatusError(
                str(submission.id),
                submission.status.value,
                "approve",
            )

        # Anti-self-approval rule
        if str(reviewer_discord_id).strip() == str(submission.discord_user_id).strip():
            raise UnauthorizedAdminError("Administrators cannot approve their own task submissions.")

        task = self.session.execute(
            select(Task)
            .where(Task.id == submission.task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()

        if task.remaining_reward_pool < task.reward_per_user:
            task.status = TaskStatus.COMPLETED
            self.session.commit()
            logger.warning(
                "Reward pool exhausted for Task %s: Remaining=%d, Required=%d",
                task.id,
                task.remaining_reward_pool,
                task.reward_per_user,
            )
            raise RewardPoolExhaustedError(
                str(task.id),
                task.remaining_reward_pool,
                task.reward_per_user,
            )

        idempotency_key = f"task_reward:{task.id}:{submission.id}"
        logger.info(
            "Attempting OBX reward distribution: User=%s, Amount=%d OBX, Key=%s",
            submission.discord_user_id,
            task.reward_per_user,
            idempotency_key,
        )

        try:
            # Credit with commit=False to keep the atomic transaction open
            tx_id = client.credit(
                discord_user_id=submission.discord_user_id,
                amount=task.reward_per_user,
                reference_type=ReferenceType.TASK_REWARD.value,
                reference_id=str(task.id),
                idempotency_key=idempotency_key,
                description=f"Reward for task: {task.title}",
                commit=False,
            )
            logger.info("OBX reward successful: Tx ID=%s", tx_id)
        except Exception as exc:
            self.session.rollback()
            logger.error("OBX reward failed for submission %s: %s", submission.id, exc)
            raise

        task.distributed_reward += task.reward_per_user
        if task.remaining_reward_pool < task.reward_per_user:
            task.status = TaskStatus.COMPLETED
            logger.info("Task %s is now COMPLETED (pool exhausted).", task.id)

        submission.status = SubmissionStatus.APPROVED
        submission.reviewed_by = str(reviewer_discord_id).strip()
        submission.reviewed_at = datetime.now(timezone.utc)
        submission.reward_amount = task.reward_per_user
        submission.obx_transaction_id = tx_id
        logger.info(
            "[APPROVAL] Submission marked approved: ID=%s, User=%s, Reward=+%d OBX",
            submission.id,
            submission.discord_user_id,
            submission.reward_amount,
        )

        # Record durable submission audit entry
        audit_log = SubmissionAuditLog(
            task_id=task.id,
            submission_id=submission.id,
            discord_user_id=submission.discord_user_id,
            admin_id=str(reviewer_discord_id).strip(),
            action="APPROVE",
            previous_status=SubmissionStatus.PENDING.value,
            new_status=SubmissionStatus.APPROVED.value,
            reward_amount=task.reward_per_user,
            rejection_reason=None,
            obx_transaction_id=tx_id,
            proof_media_deleted=False,
            proof_media_deleted_at=None,
        )
        self.session.add(audit_log)

        # Single atomic commit for Task, Submission, Wallet, LedgerEntry, and SubmissionAuditLog
        self.session.commit()
        self.session.refresh(submission)
        logger.info(
            "[APPROVAL] Reward transaction committed: Tx ID=%s, User=%s, Amount=+%d OBX",
            tx_id,
            submission.discord_user_id,
            submission.reward_amount,
        )

        # Trigger proof media cleanup if retention policy is immediate (0)
        try:
            self.cleanup_proof_media(submission_id=submission.id)
        except Exception as cl_err:
            logger.warning("Proof media cleanup failed for submission %s: %s", submission.id, cl_err)

        return submission

    def reject_submission(
        self,
        submission_id: uuid.UUID | str,
        reviewer_discord_id: str,
        rejection_reason: str,
    ) -> TaskSubmission:
        """Rejects a pending task submission with a given reason."""
        if not rejection_reason or not rejection_reason.strip():
            raise ValueError("Rejection reason must be provided.")

        if isinstance(submission_id, str):
            try:
                submission_id = uuid.UUID(submission_id)
            except ValueError:
                raise SubmissionNotFoundError(str(submission_id))

        submission = self.session.execute(
            select(TaskSubmission)
            .where(TaskSubmission.id == submission_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

        if not submission:
            raise SubmissionNotFoundError(str(submission_id))

        if submission.status != SubmissionStatus.PENDING:
            raise InvalidSubmissionStatusError(
                str(submission.id),
                submission.status.value,
                "reject",
            )

        submission.status = SubmissionStatus.REJECTED
        submission.reviewed_by = str(reviewer_discord_id).strip()
        submission.reviewed_at = datetime.now(timezone.utc)
        submission.rejection_reason = rejection_reason.strip()

        # Record durable submission audit entry
        audit_log = SubmissionAuditLog(
            task_id=submission.task_id,
            submission_id=submission.id,
            discord_user_id=submission.discord_user_id,
            admin_id=str(reviewer_discord_id).strip(),
            action="REJECT",
            previous_status=SubmissionStatus.PENDING.value,
            new_status=SubmissionStatus.REJECTED.value,
            reward_amount=None,
            rejection_reason=rejection_reason.strip(),
            obx_transaction_id=None,
            proof_media_deleted=False,
            proof_media_deleted_at=None,
        )
        self.session.add(audit_log)

        self.session.commit()
        self.session.refresh(submission)
        logger.info(
            "Submission rejected: ID=%s, Reviewer=%s, Reason='%s'",
            submission.id,
            reviewer_discord_id,
            rejection_reason,
        )

        # Trigger proof media cleanup if retention policy is immediate (0)
        try:
            self.cleanup_proof_media(submission_id=submission.id)
        except Exception as cl_err:
            logger.warning("Proof media cleanup failed for submission %s: %s", submission.id, cl_err)

        return submission

    def auto_expire_tasks(self) -> List[Task]:
        """Automatically transitions active tasks past their deadline to EXPIRED."""
        now = datetime.now(timezone.utc)
        expired_tasks = self.session.execute(
            select(Task)
            .where(
                Task.status == TaskStatus.ACTIVE,
                Task.ends_at.is_not(None),
                Task.ends_at < now,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars().all()

        for task in expired_tasks:
            task.status = TaskStatus.EXPIRED
            audit_log = TaskAuditLog(
                task_id=task.id,
                changed_by="SYSTEM",
                field_name="status",
                old_value=TaskStatus.ACTIVE.value,
                new_value=TaskStatus.EXPIRED.value,
            )
            self.session.add(audit_log)
            logger.info("Task %s automatically marked EXPIRED (Deadline passed at %s)", task.id, task.ends_at)

        if expired_tasks:
            self.session.commit()
            for t in expired_tasks:
                self.session.refresh(t)

        return list(expired_tasks)

    def cleanup_proof_media(
        self,
        submission_id: Optional[uuid.UUID | str] = None,
        retention_minutes: Optional[int] = None,
    ) -> int:
        """Deletes temporary proof images for finalized submissions according to retention policy."""
        from packages.shared.config import get_settings
        retention = retention_minutes if retention_minutes is not None else get_settings().PROOF_RETENTION_MINUTES
        now = datetime.now(timezone.utc)

        query = (
            select(TaskSubmission)
            .where(
                TaskSubmission.status.in_([SubmissionStatus.APPROVED, SubmissionStatus.REJECTED]),
                TaskSubmission.proof_media_deleted == False,
                TaskSubmission.proof_screenshot_url.is_not(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        if submission_id:
            sub_uuid = uuid.UUID(str(submission_id)) if isinstance(submission_id, str) else submission_id
            query = query.where(TaskSubmission.id == sub_uuid)

        submissions = self.session.execute(query).scalars().all()
        cleaned_count = 0

        for sub in submissions:
            # Check retention period relative to reviewed_at
            reviewed_at = _normalize_dt(sub.reviewed_at) or now
            if retention > 0 and now < reviewed_at + timedelta(minutes=retention):
                continue

            file_path = sub.proof_screenshot_url
            if file_path:
                try:
                    p = Path(file_path)
                    if p.exists() and p.is_file():
                        p.unlink()
                        logger.info("Deleted proof image file for submission %s: %s", sub.id, file_path)
                except Exception as exc:
                    logger.warning("Could not delete proof media file %s for submission %s: %s", file_path, sub.id, exc)

            sub.proof_media_deleted = True
            sub.proof_media_deleted_at = now

            # Record audit log
            audit_entry = SubmissionAuditLog(
                task_id=sub.task_id,
                submission_id=sub.id,
                discord_user_id=sub.discord_user_id,
                admin_id=sub.reviewed_by or "SYSTEM",
                action="MEDIA_CLEANUP",
                previous_status=sub.status.value,
                new_status=sub.status.value,
                reward_amount=sub.reward_amount,
                rejection_reason=sub.rejection_reason,
                obx_transaction_id=sub.obx_transaction_id,
                proof_media_deleted=True,
                proof_media_deleted_at=now,
            )
            self.session.add(audit_entry)
            cleaned_count += 1

        if cleaned_count > 0:
            self.session.commit()
            logger.info("Cleaned up proof media for %d finalized submission(s).", cleaned_count)

        return cleaned_count

    def get_submission_audit_logs(
        self,
        submission_id: Optional[uuid.UUID | str] = None,
        task_id: Optional[uuid.UUID | str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[SubmissionAuditLog], int]:
        """Retrieves paginated submission review and media cleanup audit log entries."""
        query = select(SubmissionAuditLog)

        if submission_id:
            sub_uuid = uuid.UUID(str(submission_id)) if isinstance(submission_id, str) else submission_id
            query = query.where(SubmissionAuditLog.submission_id == sub_uuid)

        if task_id:
            task_uuid = uuid.UUID(str(task_id)) if isinstance(task_id, str) else task_id
            query = query.where(SubmissionAuditLog.task_id == task_uuid)

        total = self.session.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar() or 0

        logs = self.session.execute(
            query.order_by(SubmissionAuditLog.created_at.desc()).offset(offset).limit(limit)
        ).scalars().all()

        return list(logs), total
