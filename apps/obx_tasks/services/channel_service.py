import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from packages.database.models.channel_config import GuildConfig, PublishedMessage
from packages.shared.exceptions import TaskError
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.channel_service")


class ChannelConfigError(TaskError):
    """Exception for channel routing and configuration errors."""
    pass


CHANNEL_KEYS = {
    "tasks": "tasks_channel_id",
    "leaderboard": "leaderboard_channel_id",
    "auctions": "auctions_channel_id",
    "winners": "winners_channel_id",
    "admin": "admin_channel_id",
    "economy": "economy_channel_id",
}


class ChannelService:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create_guild_config(self, guild_id: str) -> GuildConfig:
        """Fetch or initialize persistent channel configuration for a guild."""
        if not guild_id or not str(guild_id).strip():
            raise ChannelConfigError("Guild ID is required.")

        guild_id_str = str(guild_id).strip()
        config = self.session.query(GuildConfig).filter_by(guild_id=guild_id_str).first()
        if not config:
            config = GuildConfig(guild_id=guild_id_str)
            self.session.add(config)
            self.session.commit()
            self.session.refresh(config)
            logger.info("Initialized GuildConfig for guild: %s", guild_id_str)
        return config

    def update_guild_channel(
        self,
        guild_id: str,
        channel_key: str,
        channel_id: Optional[str],
        updated_by: str,
    ) -> GuildConfig:
        """Update a specific destination channel for a guild feature."""
        clean_key = channel_key.lower().strip()
        if clean_key not in CHANNEL_KEYS:
            raise ChannelConfigError(f"Invalid channel key '{channel_key}'. Must be one of {list(CHANNEL_KEYS.keys())}")

        config = self.get_or_create_guild_config(guild_id)
        field_name = CHANNEL_KEYS[clean_key]

        channel_id_val = str(channel_id).strip() if channel_id else None
        setattr(config, field_name, channel_id_val)
        config.updated_at = datetime.now(timezone.utc)
        config.updated_by = str(updated_by)

        self.session.commit()
        self.session.refresh(config)
        logger.info("Updated GuildConfig [%s] %s -> %s by %s", guild_id, field_name, channel_id_val, updated_by)
        return config

    def get_published_message(
        self,
        guild_id: str,
        feature_type: str,
        source_id: str = "DEFAULT",
    ) -> Optional[PublishedMessage]:
        """Fetch tracking record for a published Discord message."""
        return (
            self.session.query(PublishedMessage)
            .filter_by(
                guild_id=str(guild_id),
                feature_type=feature_type.upper(),
                source_id=str(source_id),
            )
            .first()
        )

    def record_published_message(
        self,
        guild_id: str,
        feature_type: str,
        channel_id: str,
        message_id: str,
        source_id: str = "DEFAULT",
    ) -> PublishedMessage:
        """Record or update published message ID to prevent duplicate announcements."""
        record = self.get_published_message(guild_id, feature_type, source_id)
        now = datetime.now(timezone.utc)

        if record:
            record.channel_id = str(channel_id)
            record.message_id = str(message_id)
            record.updated_at = now
        else:
            record = PublishedMessage(
                guild_id=str(guild_id),
                feature_type=feature_type.upper(),
                source_id=str(source_id),
                channel_id=str(channel_id),
                message_id=str(message_id),
                created_at=now,
                updated_at=now,
            )
            self.session.add(record)

        self.session.commit()
        self.session.refresh(record)
        return record

    def delete_published_message(
        self,
        guild_id: str,
        feature_type: str,
        source_id: str = "DEFAULT",
    ) -> bool:
        """Remove a published message tracking record."""
        record = self.get_published_message(guild_id, feature_type, source_id)
        if record:
            self.session.delete(record)
            self.session.commit()
            return True
        return False
