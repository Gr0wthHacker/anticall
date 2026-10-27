"""LLMClient Protocol, errors and the deterministic MockLLM used in tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable


class LLMError(Exception):
    """Any failure talking to a model. Callers fall back to rule-based output."""


class LLMTimeoutError(LLMError):
    pass


@runtime_checkable
class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str, *, timeout_seconds: float) -> str:
        """Return the raw model text. Must raise LLMError subclasses on failure."""
        ...


class MockLLM:
    """Deterministic fake. `responder(system, user)` returns text or raises LLMError."""

    name = "mock-llm"

    def __init__(self, responder: Callable[[str, str], str] | str | None = None) -> None:
        self._responder = responder
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *, timeout_seconds: float) -> str:
        self.calls.append((system, user))
        if self._responder is None:
            return '{"label": "UNKNOWN", "confidence": 0.1}'
        if isinstance(self._responder, str):
            return self._responder
        return self._responder(system, user)
