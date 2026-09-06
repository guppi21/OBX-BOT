import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple


def parse_duration_or_datetime(val: Optional[str]) -> Optional[datetime]:
    """Parse relative duration (e.g. '30m', '2h', '24h', '3d', '7d') or explicit UTC datetime."""
    if not val or not val.strip():
        return None
    val = val.strip().lower()
    now = datetime.now(timezone.utc)

    # Check relative duration format
    m = re.match(r"^(\d+)\s*(m|min|mins|minutes|h|hr|hrs|hours|d|day|days|w|weeks)$", val)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("m") and not unit.startswith("min"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("min"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("h"):
            delta = timedelta(hours=amount)
        elif unit.startswith("d"):
            delta = timedelta(days=amount)
        elif unit.startswith("w"):
            delta = timedelta(weeks=amount)
        else:
            delta = timedelta(hours=amount)
        return now + delta

    # Explicit date formats
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            parsed = datetime.strptime(val, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    raise ValueError(f"Could not parse duration/deadline '{val}'. Valid examples: '2h', '24h', '3d', '2026-09-10 18:00', or leave blank.")


def upgrade_twitter_avatar_url(url: Optional[str]) -> Optional[str]:
    """Upgrade Twitter/X profile avatar URLs from low-res thumbnails (_normal: 48x48, _mini: 24x24)
    to high-resolution 400x400 HD avatars (_400x400).

    This prevents blurry/pixelated thumbnails on Discord embeds, especially on Retina/HiDPI screens.
    """
    if not url or not isinstance(url, str):
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    # Twitter profile images on pbs.twimg.com or abs.twimg.com end with _normal.[ext], _mini.[ext], or _bigger.[ext]
    if "twimg.com" in cleaned or "profile_images" in cleaned:
        cleaned = re.sub(r"_(normal|mini|bigger)\.([a-zA-Z0-9]+)$", r"_400x400.\2", cleaned)
    return cleaned
