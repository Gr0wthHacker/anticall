"""Voice engine errors and the lazy optional-import helper."""

from __future__ import annotations

import importlib
from typing import Any

INSTALL_HINT = "pip install 'callshield[voice]'"


class VoiceEngineUnavailable(RuntimeError):  # noqa: N818
    """A configured engine cannot be built (missing library, model or setting).

    The message always says what to install or download; nothing is fetched automatically.
    """


def import_optional(module: str, *, purpose: str) -> Any:
    """Import an optional library lazily with a friendly error."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise VoiceEngineUnavailable(
            f"{purpose} needs the '{module}' package. Install it with {INSTALL_HINT}."
        ) from exc
