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

FEATURE_CHANNEL_KEYWORDS: Dict[str, List[str]] = {
    "admin": [
        "admin-logs", "admin-log", "admin_logs", "admin_log", "admin-hub",
        "obx-admin", "admin", "mod-logs", "mod-log", "bot-logs", "audit", "obx-logs",
    ],
    "winners": [
        "winners", "winner", "results", "result", "auction-results", "wl-winners",
        "obx-winners", "proof-of-win",
    ],
    "auctions": [
        "auctions", "auction", "whitelist", "wl-auctions", "wl-sale", "bids",
        "obx-auctions", "market", "marketplace",
    ],
    "tasks": [
        "tasks", "task", "missions", "mission", "bounties", "bounty",
        "raid-tasks", "raids", "obx-tasks", "daily-tasks", "quest-board", "quests", "earn-obx",
    ],
    "leaderboard": [
        "leaderboard", "leaderboards", "top-raiders", "top-earners", "ranking",
        "rankings", "scoreboard", "ranks", "obx-leaderboard",
    ],
    "economy": [
        "economy", "wallet", "balances", "bank", "vault",
    ],
}


def score_channel_match(channel_name: Any, keywords: List[str]) -> int:
    """Score how well a channel name matches a list of keywords.
    Returns:
        100: Exact match after stripping emojis/symbols (e.g. 'tasks', '🏆-tasks' -> 'tasks')
        80: Exact token match within hyphenated/spaced parts (e.g. 'obx-tasks' has token 'tasks')
        50: Prefix or suffix match (e.g. 'tasks-hub', 'daily-tasks')
        20: Substring match (e.g. 'missions' in 'active-missions-v2')
        0: No match
    """
    if not channel_name:
        return 0
    raw_name = getattr(channel_name, "name", channel_name)
    if not isinstance(raw_name, str):
        raw_name = str(raw_name)

    import re
    clean = re.sub(r'[^a-z0-9]+', '-', raw_name.lower()).strip('-')
    tokens = [t for t in clean.split('-') if t]

    best_score = 0
    for kw in keywords:
        kw_clean = re.sub(r'[^a-z0-9]+', '-', kw.lower()).strip('-')
        kw_tokens = [t for t in kw_clean.split('-') if t]

        if clean == kw_clean:
            return 100

        if len(kw_tokens) == 1 and kw_tokens[0] in tokens:
            best_score = max(best_score, 80)
        elif len(kw_tokens) > 1 and kw_clean in clean:
            best_score = max(best_score, 80)
        elif clean.startswith(kw_clean + "-") or clean.endswith("-" + kw_clean):
            best_score = max(best_score, 50)
        elif kw_clean in clean:
            best_score = max(best_score, 20)

    return best_score



class ChannelService:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create_guild_config(self, guild_id: str) -> GuildConfig:
        """Fetch or initialize persistent channel configuration for a guild."""
        if not guild_id or not str(guild_id).strip():
            raise ChannelConfigError("Guild ID is required.")

        guild_id_str = str(guild_id).strip()
        config = self.session.query(GuildConfig).filter_by(guild_id=guild_id_str).first()
        changed = False
        if not config:
            config = GuildConfig(guild_id=guild_id_str)
            self.session.add(config)
            changed = True

        from packages.shared.config import get_settings
        settings = get_settings()
        is_target_guild = (settings.DISCORD_GUILD_ID is not None and str(guild_id_str) == str(settings.DISCORD_GUILD_ID))

        if is_target_guild:
            if settings.DISCORD_TASK_CHANNEL_ID and config.tasks_channel_id != str(settings.DISCORD_TASK_CHANNEL_ID):
                config.tasks_channel_id = str(settings.DISCORD_TASK_CHANNEL_ID)
                changed = True
            if settings.DISCORD_AUCTION_CHANNEL_ID and config.auctions_channel_id != str(settings.DISCORD_AUCTION_CHANNEL_ID):
                config.auctions_channel_id = str(settings.DISCORD_AUCTION_CHANNEL_ID)
                changed = True
            if settings.DISCORD_LEADERBOARD_CHANNEL_ID and config.leaderboard_channel_id != str(settings.DISCORD_LEADERBOARD_CHANNEL_ID):
                config.leaderboard_channel_id = str(settings.DISCORD_LEADERBOARD_CHANNEL_ID)
                changed = True
            if settings.DISCORD_WINNERS_CHANNEL_ID and config.winners_channel_id != str(settings.DISCORD_WINNERS_CHANNEL_ID):
                config.winners_channel_id = str(settings.DISCORD_WINNERS_CHANNEL_ID)
                changed = True
            if settings.DISCORD_ADMIN_LOG_CHANNEL_ID and config.admin_channel_id != str(settings.DISCORD_ADMIN_LOG_CHANNEL_ID):
                config.admin_channel_id = str(settings.DISCORD_ADMIN_LOG_CHANNEL_ID)
                changed = True
            if settings.RAID_ROLE_ID and config.task_alerts_role_id != str(settings.RAID_ROLE_ID):
                config.task_alerts_role_id = str(settings.RAID_ROLE_ID)
                changed = True

        if changed:
            self.session.commit()
            self.session.refresh(config)
            logger.info("Initialized/Synchronized GuildConfig for guild: %s", guild_id_str)
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

    def auto_discover_guild_channels(self, guild: Any, overwrite: bool = False) -> Dict[str, str]:
        """Auto-discover relevant channels and raid role in a Discord guild and store in GuildConfig.

        Matches channels against keywords for:
        - admin (admin-logs, mod-logs, etc.)
        - winners (winners, results, etc.)
        - auctions (auctions, whitelist, etc.)
        - tasks (tasks, missions, bounties, etc.)
        - leaderboard (leaderboard, rankings, etc.)
        - economy (economy, wallet, balances, etc.)
        And discovers the raider role.

        Returns a dictionary of discovered mappings (feature_name -> channel/role name).
        """
        if not guild or not hasattr(guild, "id"):
            return {}

        guild_id_str = str(guild.id).strip()
        config = self.get_or_create_guild_config(guild_id_str)
        text_channels = getattr(guild, "text_channels", None)
        if text_channels is None and hasattr(guild, "channels"):
            text_channels = [c for c in guild.channels if getattr(c, "type", None) == 0 or getattr(c, "__class__", None).__name__ == "TextChannel" or hasattr(c, "send")]
        elif text_channels is None:
            text_channels = []

        discovered: Dict[str, str] = {}
        assigned_channel_ids = set()

        feature_order = ["admin", "winners", "auctions", "tasks", "leaderboard", "economy"]

        for feat in feature_order:
            field_name = CHANNEL_KEYS.get(feat)
            if not field_name:
                continue

            current_val = getattr(config, field_name, None)
            if current_val and not overwrite:
                existing_ch = guild.get_channel(int(current_val)) if hasattr(guild, "get_channel") else None
                if existing_ch:
                    assigned_channel_ids.add(str(existing_ch.id))
                    continue

            keywords = FEATURE_CHANNEL_KEYWORDS.get(feat, [])
            best_ch = None
            best_score = 0

            for ch in text_channels:
                ch_id_str = str(ch.id)
                if ch_id_str in assigned_channel_ids:
                    continue

                ch_name = getattr(ch, "name", "")
                score = score_channel_match(ch_name, keywords)
                if score > best_score:
                    best_score = score
                    best_ch = ch

            if best_ch and best_score >= 20:
                setattr(config, field_name, str(best_ch.id))
                assigned_channel_ids.add(str(best_ch.id))
                discovered[feat] = getattr(best_ch, "name", str(best_ch.id))
                logger.info(
                    "Auto-discovered channel for '%s' in guild '%s': #%s (score=%d)",
                    feat, getattr(guild, "name", guild_id_str), getattr(best_ch, "name", best_ch.id), best_score,
                )

        # Auto-discover raider role
        if not config.task_alerts_role_id or overwrite:
            try:
                from apps.obx_tasks.bot.announcement_service import resolve_raider_role
                r_id, r_obj = resolve_raider_role(guild)
                if r_id:
                    config.task_alerts_role_id = str(r_id)
                    discovered["role"] = getattr(r_obj, "name", str(r_id))
                    logger.info(
                        "Auto-discovered raid role in guild '%s': @%s (ID: %s)",
                        getattr(guild, "name", guild_id_str), getattr(r_obj, "name", r_id), r_id,
                    )
            except Exception as r_err:
                logger.debug("Could not auto-discover raid role for guild '%s': %s", guild_id_str, r_err)

        if discovered:
            config.updated_at = datetime.now(timezone.utc)
            config.updated_by = "AUTO_DISCOVERY"
            self.session.commit()
            self.session.refresh(config)

        return discovered

