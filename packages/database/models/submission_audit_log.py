import uuid
from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, Text, DateTime, ForeignKey, Boolean, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from packages.database.base import Base


class SubmissionAuditLog(Base):
    """Durable historical audit log for every task submission review and media cleanup action."""
    __tablename__ = "submission_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_submissions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    discord_user_id: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    admin_id: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    previous_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    new_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    reward_amount: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    obx_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    proof_media_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    proof_media_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<SubmissionAuditLog id={self.id} sub={self.submission_id} "
            f"action={self.action} status={self.previous_status}->{self.new_status} by={self.admin_id}>"
        )
