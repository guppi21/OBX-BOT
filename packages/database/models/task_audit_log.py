import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from packages.database.base import Base


class TaskAuditLog(Base):
    __tablename__ = "task_audit_logs"

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
    changed_by: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    field_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    old_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    new_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Backward compatibility property
    @property
    def previous_value(self) -> str | None:
        return self.old_value

    # Relationships
    task: Mapped["Task"] = relationship(
        "Task",
        back_populates="audit_logs",
    )

    def __repr__(self) -> str:
        return (
            f"<TaskAuditLog id={self.id} task_id={self.task_id} "
            f"field={self.field_name} old='{self.old_value}' new='{self.new_value}' by={self.changed_by}>"
        )
