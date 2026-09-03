import uuid
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, CheckConstraint, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from packages.database.base import Base


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        CheckConstraint("available_balance >= 0", name="ck_wallets_available_balance_non_negative"),
        CheckConstraint("locked_balance >= 0", name="ck_wallets_locked_balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    available_balance: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
    locked_balance: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
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
    user: Mapped["User"] = relationship(
        "User",
        back_populates="wallet",
    )

    @property
    def total_balance(self) -> int:
        return self.available_balance + self.locked_balance

    def __repr__(self) -> str:
        return (
            f"<Wallet id={self.id} user_id={self.user_id} "
            f"available={self.available_balance} locked={self.locked_balance}>"
        )
