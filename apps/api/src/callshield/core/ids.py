"""UUID helpers. All primary keys are uuid4."""

from __future__ import annotations

import uuid


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def parse_id(value: str | uuid.UUID) -> uuid.UUID:
    """Parse a string/UUID; raises ValueError on garbage."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
