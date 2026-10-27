"""Timezone resolution that degrades safely.

Accepts IANA names (needs tzdata on the host; absent on stock Windows) plus fixed offsets
written as 'UTC', 'UTC+HH:MM' or 'UTC-HH:MM'. Anything unresolvable returns None so the
calling-hours gate fails closed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_OFFSET = re.compile(r"^UTC(?:([+-])(\d{1,2})(?::?(\d{2}))?)?$")


def resolve_timezone(name: str | None) -> tzinfo | None:
    if not name:
        return None
    match = _OFFSET.match(name.strip())
    if match:
        sign, hours, minutes = match.groups()
        if sign is None:
            return UTC
        delta = timedelta(hours=int(hours), minutes=int(minutes or 0))
        if delta > timedelta(hours=14):
            return None
        return timezone(delta if sign == "+" else -delta)
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def local_time(now_utc: datetime, tz: tzinfo) -> datetime:
    return now_utc.astimezone(tz)
