import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from packages.database.base import Base


class RaiderProfile(Base):
    __tablename__ = "raider_profiles"

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
    twitter_handle: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    twitter_profile_url: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    twitter_avatar_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
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

    @property
    def x_handle(self) -> str:
        return self.twitter_handle

    @x_handle.setter
    def x_handle(self, val: str) -> None:
        self.twitter_handle = val

    @property
    def x_profile_url(self) -> str:
        return self.twitter_profile_url

    @x_profile_url.setter
    def x_profile_url(self, val: str) -> None:
        self.twitter_profile_url = val

    @property
    def x_avatar_url(self) -> str | None:
        return self.twitter_avatar_url

    @x_avatar_url.setter
    def x_avatar_url(self, val: str | None) -> None:
        self.twitter_avatar_url = val

    def __repr__(self) -> str:
        return f"<RaiderProfile discord_user_id={self.discord_user_id} twitter_handle={self.twitter_handle}>"
