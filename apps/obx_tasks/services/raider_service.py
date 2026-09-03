import re
import logging
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, select

from packages.database.models.raider_profile import RaiderProfile
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.raider_service")


def normalize_twitter_input(raw: str) -> Tuple[str, str]:
    """Normalize user input into (twitter_handle, twitter_profile_url).

    Accepts:
      - @username
      - username
      - x.com/username
      - twitter.com/username
      - https://x.com/username
      - https://twitter.com/username

    Normalizes duplicate @ symbols.
    Returns:
      (twitter_handle without @, f"https://x.com/{twitter_handle}")
    Raises:
      ValueError if input cannot be parsed or contains invalid characters.
    """
    if not raw or not isinstance(raw, str) or not raw.strip():
        raise ValueError("Please provide an X (Twitter) username or profile URL.")

    text = raw.strip()
    text = text.strip("\"'<>[]()")

    # Handle domain paths
    for domain in ["x.com/", "twitter.com/", "fxtwitter.com/", "vxtwitter.com/", "fixupx.com/"]:
        if domain in text.lower():
            idx = text.lower().find(domain) + len(domain)
            text = text[idx:].strip()
            break

    # Strip query parameters, anchors, and slashes
    text = text.split("?")[0].split("#")[0].strip()
    text = text.strip("/")

    # Strip duplicate or leading @ symbols
    text = text.lstrip("@").strip()

    if not text:
        raise ValueError("Invalid X username or URL.")

    # Validate handle characters: alphanumeric and underscores (1 to 30 chars)
    if not re.match(r"^[A-Za-z0-9_]{1,30}$", text):
        raise ValueError(
            f"Invalid X handle '{text}'. Usernames must contain only letters, numbers, and underscores (1-30 characters)."
        )

    twitter_handle = text
    twitter_profile_url = f"https://x.com/{twitter_handle}"
    return twitter_handle, twitter_profile_url


class RaiderService:
    def __init__(self, session: Session):
        self.session = session

    def get_raider_profile(self, discord_user_id: str) -> Optional[RaiderProfile]:
        """Fetch a user's Raider profile by Discord User ID."""
        if not discord_user_id:
            return None
        return (
            self.session.query(RaiderProfile)
            .filter_by(discord_user_id=str(discord_user_id))
            .first()
        )

    def get_profile_by_handle(self, twitter_handle: str) -> Optional[RaiderProfile]:
        """Fetch a Raider profile by Twitter handle (case-insensitive)."""
        if not twitter_handle:
            return None
        clean_handle = twitter_handle.strip().lstrip("@")
        return (
            self.session.query(RaiderProfile)
            .filter(func.lower(RaiderProfile.twitter_handle) == clean_handle.lower())
            .first()
        )

    def set_raider_twitter(
        self,
        discord_user_id: str,
        raw_input: str,
        avatar_url: Optional[str] = None,
        admin_override: bool = False,
    ) -> RaiderProfile:
        """Register or update a user's X/Twitter account.

        Prevents duplicate registration of the same handle across different Discord users
        unless admin_override is explicitly True.
        """
        if not discord_user_id or not str(discord_user_id).strip():
            raise ValueError("Discord User ID is required.")

        handle, profile_url = normalize_twitter_input(raw_input)

        # Enforce uniqueness across Discord users (case-insensitive)
        existing_handle_holder = (
            self.session.query(RaiderProfile)
            .filter(func.lower(RaiderProfile.twitter_handle) == handle.lower())
            .first()
        )

        if existing_handle_holder and str(existing_handle_holder.discord_user_id) != str(discord_user_id):
            if not admin_override:
                raise ValueError(
                    f"The X handle @{handle} is already registered to another member. "
                    "If this is your account, please contact an administrator."
                )
            else:
                logger.warning(
                    "Admin override: transferring X handle @%s from %s to %s",
                    handle,
                    existing_handle_holder.discord_user_id,
                    discord_user_id,
                )
                self.session.delete(existing_handle_holder)
                self.session.flush()

        profile = (
            self.session.query(RaiderProfile)
            .filter_by(discord_user_id=str(discord_user_id))
            .first()
        )

        if profile:
            profile.twitter_handle = handle
            profile.twitter_profile_url = profile_url
            if avatar_url:
                profile.twitter_avatar_url = avatar_url
        else:
            profile = RaiderProfile(
                discord_user_id=str(discord_user_id),
                twitter_handle=handle,
                twitter_profile_url=profile_url,
                twitter_avatar_url=avatar_url,
            )
            self.session.add(profile)

        self.session.commit()
        self.session.refresh(profile)
        logger.info("Saved Raider X account for user %s: @%s", discord_user_id, handle)
        return profile

    def remove_raider_twitter(self, discord_user_id: str) -> bool:
        """Remove a user's registered Twitter account."""
        profile = self.get_raider_profile(discord_user_id)
        if not profile:
            return False
        self.session.delete(profile)
        self.session.commit()
        logger.info("Removed Raider X account for user %s", discord_user_id)
        return True

    def list_raiders(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[RaiderProfile], int]:
        """List registered raider profiles."""
        total = self.session.query(func.count(RaiderProfile.id)).scalar() or 0
        profiles = (
            self.session.query(RaiderProfile)
            .order_by(RaiderProfile.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        return profiles, total
