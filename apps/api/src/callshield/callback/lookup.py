"""Injectable line-type / geography lookup for the destination's calling-hours check.

The default returns None (no geography), in which case only the user's local time applies.
A real provider lookup may be injected via `callback.runtime.set_runtime`. Results must come
from a local/permitted source; the lookup never receives the user's data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LineTypeInfo:
    line_type: str | None = None  # e.g. "landline", "voip", "mobile"
    timezone: str | None = None  # IANA name or UTC+HH:MM; None = no geography


class LineTypeLookup(Protocol):
    def lookup(self, e164: str) -> LineTypeInfo | None: ...


class NoGeographyLookup:
    def lookup(self, e164: str) -> LineTypeInfo | None:
        return None
