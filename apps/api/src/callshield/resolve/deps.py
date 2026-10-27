"""FastAPI dependencies for the resolve routers: injectable dialer and dialogue model."""

from __future__ import annotations

import os

from fastapi import Request

from callshield.agent.dialogue import DialogueModel, RuleBasedDialogueModel
from callshield.resolve.dialer import DisabledDialer, MockDialer, OutboundDialer


def get_dialer(request: Request) -> OutboundDialer:
    """Dialer used by start-contact. Override in tests or set `app.state.dialer`.

    Default is the DisabledDialer. `CALLSHIELD_DIALER=mock` selects the scripted MockDialer
    (dev/test only, never in prod). The real telephony adapter plugs in via app.state.dialer.
    """
    configured: OutboundDialer | None = getattr(request.app.state, "dialer", None)
    if configured is not None:
        return configured
    settings = request.app.state.settings
    if os.environ.get("CALLSHIELD_DIALER") == "mock" and settings.environment != "prod":
        mock = MockDialer()
        request.app.state.dialer = mock
        return mock
    return DisabledDialer()


def get_dialogue_model() -> DialogueModel:
    """Rule-based by default; an LLM-backed model is optional and never required."""
    return RuleBasedDialogueModel()
