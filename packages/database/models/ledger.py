import uuid
from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, ForeignKey, Enum as SQLEnum, CheckConstraint, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from packages.database.base import Base
from packages.shared.enums import TransactionType


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    amount: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    transaction_type: Mapped[TransactionType] = mapped_column(
        SQLEnum(TransactionType, name="transaction_type_enum", native_enum=False),
        index=True,
        nullable=False,
    )
    reference_type: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    reference_id: Mapped[str | None] = mapped_column(
        String(255),
        index=True,
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="ledger_entries",
    )

    def __repr__(self) -> str:
        return (
            f"<LedgerEntry id={self.id} user_id={self.user_id} "
            f"type={self.transaction_type} amount={self.amount} key={self.idempotency_key}>"
        )
