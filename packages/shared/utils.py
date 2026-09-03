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
