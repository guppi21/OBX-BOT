import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    BigInteger, Integer, String, Text, Boolean, DateTime, ForeignKey,
    Enum as SQLEnum, UniqueConstraint, CheckConstraint, func, Uuid
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from packages.database.base import Base
from packages.shared.enums import AuctionType, AuctionStatus


class Auction(Base):
    __tablename__ = "auctions"
    __table_args__ = (
        CheckConstraint("total_slots > 0", name="total_slots_positive"),
        CheckConstraint("allocated_slots >= 0", name="allocated_slots_non_negative"),
        CheckConstraint("allocated_slots <= total_slots", name="allocated_lte_total_slots"),
        CheckConstraint("price_or_min_bid > 0", name="price_or_min_bid_positive"),
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
    reward_title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    auction_type: Mapped[AuctionType] = mapped_column(
        SQLEnum(AuctionType, name="auction_type_enum", native_enum=False),
        nullable=False,
        index=True,
    )
    total_slots: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    allocated_slots: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    price_or_min_bid: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    status: Mapped[AuctionStatus] = mapped_column(
        SQLEnum(AuctionStatus, name="auction_status_enum", native_enum=False),
        default=AuctionStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    external_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    project_x_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    preview_x_handle: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    preview_x_display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    preview_x_avatar_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    preview_x_banner_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    preview_x_bio: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    preview_image_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    image_url: Mapped[str | None] = mapped_column(
        String(1024),
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
    bids: Mapped[List["AuctionBid"]] = relationship(
        "AuctionBid",
        back_populates="auction",
        cascade="all, delete-orphan",
    )
    claims: Mapped[List["AuctionClaim"]] = relationship(
        "AuctionClaim",
        back_populates="auction",
        cascade="all, delete-orphan",
    )
    audit_logs: Mapped[List["AuctionAuditLog"]] = relationship(
        "AuctionAuditLog",
        back_populates="auction",
        cascade="all, delete-orphan",
    )

    @property
    def remaining_slots(self) -> int:
        return max(0, self.total_slots - self.allocated_slots)

    def __repr__(self) -> str:
        return f"<Auction id={self.id} title='{self.title}' type={self.auction_type} status={self.status}>"


class AuctionBid(Base):
    __tablename__ = "auction_bids"
    __table_args__ = (
        UniqueConstraint("auction_id", "discord_user_id", name="uq_auction_bids_user"),
        CheckConstraint("bid_amount > 0", name="bid_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    auction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auctions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    discord_user_id: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    bid_amount: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    is_winner: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        default=None,
    )
    is_settled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    placed_at: Mapped[datetime] = mapped_column(
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
    auction: Mapped["Auction"] = relationship(
        "Auction",
        back_populates="bids",
    )

    def __repr__(self) -> str:
        return f"<AuctionBid id={self.id} auction={self.auction_id} user={self.discord_user_id} amount={self.bid_amount}>"


class AuctionClaim(Base):
    __tablename__ = "auction_claims"
    __table_args__ = (
        UniqueConstraint("auction_id", "discord_user_id", name="uq_auction_claims_user"),
        CheckConstraint("price_paid > 0", name="price_paid_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    auction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auctions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    discord_user_id: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    price_paid: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    obx_transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )

    # Relationships
    auction: Mapped["Auction"] = relationship(
        "Auction",
        back_populates="claims",
    )

    def __repr__(self) -> str:
        return f"<AuctionClaim id={self.id} auction={self.auction_id} user={self.discord_user_id} price={self.price_paid}>"


class AuctionAuditLog(Base):
    __tablename__ = "auction_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    auction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auctions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    changed_by: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    auction: Mapped["Auction"] = relationship(
        "Auction",
        back_populates="audit_logs",
    )

    def __repr__(self) -> str:
        return f"<AuctionAuditLog id={self.id} auction={self.auction_id} action={self.action}>"
