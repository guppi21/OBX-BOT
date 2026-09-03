import uuid
from datetime import datetime
from sqlalchemy import BigInteger, String, Text, DateTime, ForeignKey, Enum as SQLEnum, UniqueConstraint, Boolean, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from packages.database.base import Base
from packages.shared.enums import SubmissionStatus


class TaskSubmission(Base):
    __tablename__ = "task_submissions"
    __table_args__ = (
        UniqueConstraint("task_id", "discord_user_id", name="uq_submissions_task_user"),
    )

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
    discord_user_id: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    x_username: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    proof_url: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )
    proof_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    proof_screenshot_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    status: Mapped[SubmissionStatus] = mapped_column(
        SQLEnum(SubmissionStatus, name="submission_status_enum", native_enum=False),
        default=SubmissionStatus.PENDING,
        index=True,
        nullable=False,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    reward_amount: Mapped[int | None] = mapped_column(
        BigInteger,
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

    # Relationships
    task: Mapped["Task"] = relationship(
        "Task",
        back_populates="submissions",
    )

    def __repr__(self) -> str:
        return (
            f"<TaskSubmission id={self.id} task_id={self.task_id} "
            f"user={self.discord_user_id} status={self.status}>"
        )
