import sys
import os
import signal
import asyncio
import argparse
import time
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Configure SSL certificates for macOS Python environments
try:
    import certifi
    if "SSL_CERT_FILE" not in os.environ:
        os.environ["SSL_CERT_FILE"] = certifi.where()
except ImportError:
    pass

from packages.shared.config import get_settings
from packages.shared.logging import setup_logging, get_logger
from packages.database.session import get_engine
from apps.obx_tasks.bot.client import create_discord_bot
from sqlalchemy import text

logger = get_logger("obx.tasks.bot.runner")


def main():
    parser = argparse.ArgumentParser(description="OBX Discord Bot Runner")
    parser.add_argument("--mode", default="live", help="Bot execution mode (e.g. live, dev)")
    parser.add_argument("--port", default=None, help="Port binding for healthchecks/Railway")
    args, unknown = parser.parse_known_args()

    if args.port and "PORT" not in os.environ:
        os.environ["PORT"] = str(args.port)

    setup_logging()
    settings = get_settings()

    logger.info("OBX Discord bot starting (mode=%s, port=%s)...", args.mode, args.port or os.environ.get("PORT", "N/A"))

    # 1. Configuration Validation
    try:
        settings.validate_for_discord_bot()
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    # 2. Database Connectivity & Migration Check (with cold-start retry)
    engine = get_engine()
    connected = False
    for attempt in range(1, 6):
        try:
            with engine.connect() as conn:
                try:
                    result = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
                    logger.info("Database connection successful (Alembic migration head: %s)", result or "003_task_audit_logs")
                except Exception:
                    from packages.database.base import Base
                    Base.metadata.create_all(engine)
                    logger.info("Database connection successful (Initialized schema from metadata)")
                try:
                    conn.execute(text("ALTER TABLE raider_profiles ADD COLUMN IF NOT EXISTS twitter_avatar_url VARCHAR(1024)"))
                    conn.commit()
                except Exception:
                    pass
                connected = True
                break
        except Exception as exc:
            if attempt < 5:
                logger.warning("Database connection attempt %s/5 failed (%s). Retrying in 2s...", attempt, exc)
                time.sleep(2)
            else:
                logger.error("Database connection failure after 5 attempts: %s", exc)
                sys.exit(1)

    if settings.DISCORD_GUILD_ID:
        logger.info("Configured test guild ID: %s (guild-only command sync enabled)", settings.DISCORD_GUILD_ID)
    else:
        logger.info("No DISCORD_GUILD_ID set. Slash commands will synchronize globally.")

    if settings.DISCORD_ADMIN_ROLE_IDS:
        logger.info("Configured admin role IDs: %s", settings.DISCORD_ADMIN_ROLE_IDS)
    else:
        logger.warning("No DISCORD_ADMIN_ROLE_ID configured. Server administrator permission check active.")

    if settings.RAID_ROLE_ID:
        logger.info("Configured universal RAID_ROLE_ID: %s", settings.RAID_ROLE_ID)
    else:
        logger.warning("No RAID_ROLE_ID configured in environment/.env! Raider role gating will default open.")

    if settings.RAID_JOIN_CHANNEL_ID:
        logger.info("Configured RAID_JOIN_CHANNEL_ID: %s", settings.RAID_JOIN_CHANNEL_ID)
    else:
        logger.warning("No RAID_JOIN_CHANNEL_ID configured in environment/.env! Join raid onboarding card will not be deployed.")

    bot = create_discord_bot()

    def handle_shutdown(signum, frame):
        logger.info("OBX Discord bot received shutdown signal (%s). Shutting down cleanly...", signum)
        try:
            get_engine().dispose()
            logger.info("Database connection pool disposed.")
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        bot.run(settings.DISCORD_BOT_TOKEN)
    except Exception as exc:
        logger.error("Discord connection failure: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
