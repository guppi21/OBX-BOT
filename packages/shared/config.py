import re
from functools import lru_cache
from typing import List, Union, Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _validate_snowflake_id(name: str, value: Optional[str]) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    val_str = str(value).strip()
    if not val_str.isdigit():
        raise ValueError(f"Invalid {name}: '{val_str}' must be a numeric Discord snowflake ID.")
    return val_str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/obx_economy",
        description="Database connection URL for SQLAlchemy synchronous engine",
    )
    ENVIRONMENT: str = Field(
        default="development",
        description="Application environment (development, test, production)",
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level",
    )
    API_HOST: str = Field(
        default="0.0.0.0",
        description="API Host",
    )
    API_PORT: int = Field(
        default=8000,
        description="OBX Core API Port",
    )

    # Phase 2A & 2B: Social Tasks & Discord Bot Settings
    DISCORD_BOT_TOKEN: str = Field(
        default="placeholder_token",
        description="Discord Bot OAuth2 Token",
    )
    DISCORD_GUILD_ID: Optional[str] = Field(
        default=None,
        description="Optional Discord Guild ID for instant slash command registration",
    )
    DISCORD_TASK_CHANNEL_ID: Optional[str] = Field(
        default=None,
        description="Optional channel ID for persistent OBX Task Dashboard",
    )
    DISCORD_AUCTION_CHANNEL_ID: Optional[str] = Field(
        default=None,
        description="Optional channel ID for persistent OBX Auctions",
    )
    DISCORD_LEADERBOARD_CHANNEL_ID: Optional[str] = Field(
        default=None,
        description="Optional channel ID for persistent OBX Leaderboard",
    )
    DISCORD_WINNERS_CHANNEL_ID: Optional[str] = Field(
        default=None,
        description="Optional channel ID for OBX Winner Announcements",
    )
    DISCORD_ADMIN_LOG_CHANNEL_ID: Optional[str] = Field(
        default=None,
        description="Optional channel ID for OBX Admin Logs",
    )
    DISCORD_ADMIN_ROLE_ID: Optional[str] = Field(
        default=None,
        description="Single Discord role ID permitted for admin task commands",
    )
    DISCORD_ADMIN_ROLE_IDS: List[str] = Field(
        default_factory=list,
        description="List of Discord role IDs permitted for admin task commands",
    )
    DISCORD_TASK_ALERTS_ROLE_ID: Optional[str] = Field(
        default=None,
        description="Optional Discord role ID to mention for new task alerts",
    )
    RAID_ROLE_ID: Optional[str] = Field(
        default=None,
        description="Unified universal membership role ID for ⚡ OBX Raider",
    )
    RAID_JOIN_CHANNEL_ID: Optional[str] = Field(
        default=None,
        description="Discord channel ID for the #⚡・join-raid onboarding channel",
    )
    ENABLE_EVERYONE_ANNOUNCEMENTS: bool = Field(
        default=False,
        description="Legacy toggle (now replaced by RAID_ROLE_ID mentions)",
    )

    # OBX Core URLs and Tokens (supporting canonical and alternative names)
    OBX_CORE_API_URL: str = Field(
        default="http://localhost:8000",
        description="Base URL for OBX Core API",
    )
    OBX_CORE_URL: Optional[str] = Field(
        default=None,
        description="Alias for OBX_CORE_API_URL",
    )
    OBX_CORE_INTERNAL_AUTH_TOKEN: str = Field(
        default="development_secret_token_123",
        description="Shared internal authentication token for inter-service calls",
    )
    INTERNAL_API_TOKEN: Optional[str] = Field(
        default=None,
        description="Alias for OBX_CORE_INTERNAL_AUTH_TOKEN",
    )
    TASK_SERVICE_PORT: int = Field(
        default=8001,
        description="Task Service API Port",
    )

    # Phase 2F: Proof Image Uploads and Retention
    PROOF_UPLOAD_DIR: str = Field(
        default="storage/proof_uploads",
        description="Directory for storing uploaded task proof images",
    )
    PROOF_RETENTION_MINUTES: int = Field(
        default=0,
        description="Retention period in minutes for proof images after review (0 = delete immediately on review finalization)",
    )
    PROOF_MAX_FILE_SIZE_BYTES: int = Field(
        default=8 * 1024 * 1024,
        description="Maximum allowed proof image upload size in bytes (default 8MB)",
    )

    @field_validator("DISCORD_GUILD_ID", mode="before")
    @classmethod
    def validate_guild_id(cls, v: Optional[str]) -> Optional[str]:
        return _validate_snowflake_id("DISCORD_GUILD_ID", v)

    @field_validator("DISCORD_TASK_CHANNEL_ID", "DISCORD_AUCTION_CHANNEL_ID", "DISCORD_LEADERBOARD_CHANNEL_ID", "DISCORD_WINNERS_CHANNEL_ID", "DISCORD_ADMIN_LOG_CHANNEL_ID", "RAID_JOIN_CHANNEL_ID", mode="before")
    @classmethod
    def validate_channel_ids(cls, v: Optional[str], info) -> Optional[str]:
        return _validate_snowflake_id(info.field_name, v)

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+psycopg2://", 1)
            elif v.startswith("postgresql://") and not v.startswith("postgresql+"):
                return v.replace("postgresql://", "postgresql+psycopg2://", 1)
        return v

    @field_validator("DISCORD_ADMIN_ROLE_ID", "RAID_ROLE_ID", "DISCORD_TASK_ALERTS_ROLE_ID", mode="before")
    @classmethod
    def validate_role_ids(cls, v: Optional[str], info) -> Optional[str]:
        return _validate_snowflake_id(info.field_name, v)

    @field_validator("DISCORD_ADMIN_ROLE_IDS", mode="before")
    @classmethod
    def parse_admin_role_ids(cls, v: Union[str, List[str], None]) -> List[str]:
        if isinstance(v, str):
            parts = [x.strip() for x in v.split(",") if x.strip()]
            for p in parts:
                _validate_snowflake_id("DISCORD_ADMIN_ROLE_IDS", p)
            return parts
        if isinstance(v, list):
            res = [str(x).strip() for x in v if str(x).strip()]
            for p in res:
                _validate_snowflake_id("DISCORD_ADMIN_ROLE_IDS", p)
            return res
        return []

    @model_validator(mode="after")
    def merge_aliases_and_roles(self) -> "Settings":
        # Merge OBX_CORE_URL and OBX_CORE_API_URL
        if self.OBX_CORE_URL:
            self.OBX_CORE_API_URL = self.OBX_CORE_URL
        else:
            self.OBX_CORE_URL = self.OBX_CORE_API_URL

        # Merge INTERNAL_API_TOKEN and OBX_CORE_INTERNAL_AUTH_TOKEN
        if self.INTERNAL_API_TOKEN:
            self.OBX_CORE_INTERNAL_AUTH_TOKEN = self.INTERNAL_API_TOKEN
        else:
            self.INTERNAL_API_TOKEN = self.OBX_CORE_INTERNAL_AUTH_TOKEN

        # Merge DISCORD_ADMIN_ROLE_ID into DISCORD_ADMIN_ROLE_IDS
        if self.DISCORD_ADMIN_ROLE_ID and self.DISCORD_ADMIN_ROLE_ID not in self.DISCORD_ADMIN_ROLE_IDS:
            self.DISCORD_ADMIN_ROLE_IDS.append(self.DISCORD_ADMIN_ROLE_ID)
        elif not self.DISCORD_ADMIN_ROLE_ID and self.DISCORD_ADMIN_ROLE_IDS:
            self.DISCORD_ADMIN_ROLE_ID = self.DISCORD_ADMIN_ROLE_IDS[0]

        return self

    def validate_for_discord_bot(self) -> None:
        """Validates critical settings required to launch the Discord bot and outputs friendly messages."""
        errors = []
        if not self.DISCORD_BOT_TOKEN or self.DISCORD_BOT_TOKEN in ("placeholder_token", "your_discord_bot_token_here"):
            errors.append(
                "• DISCORD_BOT_TOKEN is missing or set to placeholder. "
                "Please generate a bot token in Discord Developer Portal and set DISCORD_BOT_TOKEN in .env."
            )
        if not self.DATABASE_URL:
            errors.append("• DATABASE_URL is missing. Please set DATABASE_URL in .env.")

        if errors:
            err_msg = "\n".join(["\n❌ Discord Bot Startup Configuration Error(s):"] + errors)
            raise ValueError(err_msg)

        from packages.shared.logging import get_logger
        cfg_logger = get_logger("obx.config")
        if not self.RAID_ROLE_ID:
            cfg_logger.warning("⚠️ RAID_ROLE_ID is not configured in environment/.env. Universal ⚡ OBX Raider gating will default to open.")
        if not self.RAID_JOIN_CHANNEL_ID:
            cfg_logger.warning("⚠️ RAID_JOIN_CHANNEL_ID is not configured in environment/.env. #join-raid channel deployment will be skipped.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
