import uuid
from datetime import datetime
from sqlalchemy import BigInteger, String, Text, DateTime, Enum as SQLEnum, CheckConstraint, func, Uuid, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from packages.database.base import Base
from packages.shared.enums import TaskStatus, TaskType, TaskPlatform


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("reward_per_user > 0", name="reward_per_user_positive"),
        CheckConstraint("total_reward_pool >= reward_per_user", name="total_reward_pool_gte_reward_per_user"),
        CheckConstraint("distributed_reward >= 0", name="distributed_reward_non_negative"),
        CheckConstraint("distributed_reward <= total_reward_pool", name="distributed_lte_total_reward_pool"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(
        String(32),
        default=TaskPlatform.X.value,
        nullable=False,
    )
    task_type: Mapped[TaskType] = mapped_column(
        SQLEnum(TaskType, name="task_type_enum", native_enum=False),
        nullable=False,
        index=True,
    )
    target_url: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )
    reward_per_user: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    total_reward_pool: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    distributed_reward: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
    status: Mapped[TaskStatus] = mapped_column(
        SQLEnum(TaskStatus, name="task_status_enum", native_enum=False),
        default=TaskStatus.DRAFT,
        index=True,
        nullable=False,
    )
    proof_required: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    allow_image_proof: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    notification_type: Mapped[str] = mapped_column(
        String(16),
        default="DEFAULT",
        nullable=False,
    )
    custom_notification_template: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    preview_platform: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    preview_author: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    preview_title: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    preview_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    preview_image_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    preview_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    preview_source: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    preview_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    preview_author_override: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    preview_title_override: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    preview_text_override: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    preview_image_override: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    required_actions: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    submissions: Mapped[list["TaskSubmission"]] = relationship(
        "TaskSubmission",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskSubmission.submitted_at.desc()",
    )
    audit_logs: Mapped[list["TaskAuditLog"]] = relationship(
        "TaskAuditLog",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskAuditLog.changed_at.desc()",
    )

    @property
    def remaining_reward_pool(self) -> int:
        return self.total_reward_pool - self.distributed_reward

    @property
    def max_approvals(self) -> int:
        return self.total_reward_pool // self.reward_per_user if self.reward_per_user > 0 else 0

    @property
    def approved_count(self) -> int:
        return self.distributed_reward // self.reward_per_user if self.reward_per_user > 0 else 0

    def __repr__(self) -> str:
        return (
            f"<Task id={self.id} title='{self.title}' status={self.status} "
            f"pool={self.distributed_reward}/{self.total_reward_pool}>"
        )
