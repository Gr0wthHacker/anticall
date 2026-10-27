"""Injectable UTC clock. Always call utcnow(); tests call set_clock()."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

Clock = Callable[[], datetime]


def _system_clock() -> datetime:
    return datetime.now(UTC)


_clock: Clock = _system_clock


def utcnow() -> datetime:
    return _clock()


def set_clock(clock: Clock | None) -> None:
    global _clock
    _clock = clock or _system_clock


class FrozenClock:
    """Test clock: call advance() to move time."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)
