import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from packages.database.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    discord_user_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
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
    wallet: Mapped["Wallet"] = relationship(
        "Wallet",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="joined",
    )
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(
        "LedgerEntry",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="LedgerEntry.created_at.asc()",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} discord_user_id={self.discord_user_id}>"
