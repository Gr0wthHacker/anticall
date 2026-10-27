"""Notification hooks for operational failures (PRD 7.3). Delivery channels (email/push)
are deployment-specific; this module is the seam. Hooks must not receive raw phone numbers."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("callshield.defend")


@dataclass(frozen=True)
class Notification:
    tenant_id: uuid.UUID
    kind: str  # e.g. "telephony.registration_failed"
    detail: dict[str, Any] = field(default_factory=dict)


NotificationHook = Callable[[Notification], None]
_HOOKS: list[NotificationHook] = []


def add_notification_hook(hook: NotificationHook) -> None:
    _HOOKS.append(hook)


def clear_notification_hooks() -> None:
    _HOOKS.clear()


def notify(notification: Notification) -> None:
    """Fan out to hooks; a failing hook never breaks call handling."""
    logger.warning("notification kind=%s tenant=%s", notification.kind, notification.tenant_id)
    for hook in list(_HOOKS):
        try:
            hook(notification)
        except Exception:
            logger.exception("notification hook failed kind=%s", notification.kind)
