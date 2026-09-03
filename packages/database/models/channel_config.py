import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    DateTime,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import TypeDecorator, CHAR

from packages.database.base import Base


class GUID(TypeDecorator):
    """Platform-independent GUID type."""
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        else:
            return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if not isinstance(value, uuid.UUID):
            return uuid.UUID(str(value))
        return value


class GuildConfig(Base):
    """Persistent Discord channel routing configuration per guild."""
    __tablename__ = "guild_configs"

    guild_id = Column(String(32), primary_key=True, nullable=False)
    tasks_channel_id = Column(String(32), nullable=True)
    leaderboard_channel_id = Column(String(32), nullable=True)
    auctions_channel_id = Column(String(32), nullable=True)
    winners_channel_id = Column(String(32), nullable=True)
    admin_channel_id = Column(String(32), nullable=True)
    economy_channel_id = Column(String(32), nullable=True)
    task_alerts_role_id = Column(String(32), nullable=True)

    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_by = Column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<GuildConfig guild_id={self.guild_id} tasks={self.tasks_channel_id} lb={self.leaderboard_channel_id}>"


class PublishedMessage(Base):
    """Tracks published dashboard/announcement messages to prevent duplicate spam and support live edits."""
    __tablename__ = "published_messages"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    guild_id = Column(String(32), nullable=False, index=True)
    feature_type = Column(String(64), nullable=False, index=True)
    source_id = Column(String(64), nullable=False, default="DEFAULT", index=True)
    channel_id = Column(String(32), nullable=False)
    message_id = Column(String(32), nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("guild_id", "feature_type", "source_id", name="uq_published_msg_guild_feature_src"),
    )

    def __repr__(self) -> str:
        return f"<PublishedMessage guild={self.guild_id} feature={self.feature_type} msg={self.message_id}>"
