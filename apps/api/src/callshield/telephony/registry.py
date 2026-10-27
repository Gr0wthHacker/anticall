"""Adapter registry. Tests may register fakes; unknown providers are rejected."""

from __future__ import annotations

from callshield.telephony.adapters import AriAdapter, GenericWebhookAdapter
from callshield.telephony.base import ProviderAdapter
from callshield.telephony.mock import MockProvider

_ADAPTERS: dict[str, ProviderAdapter] = {}


def _defaults() -> dict[str, ProviderAdapter]:
    return {a.name: a for a in (GenericWebhookAdapter(), AriAdapter(), MockProvider())}


def get_adapter(name: str) -> ProviderAdapter | None:
    if not _ADAPTERS:
        _ADAPTERS.update(_defaults())
    return _ADAPTERS.get(name)


def register_adapter(adapter: ProviderAdapter) -> None:
    get_adapter(adapter.name)  # ensure defaults are loaded first
    _ADAPTERS[adapter.name] = adapter


def reset_adapters() -> None:
    _ADAPTERS.clear()


def adapter_names() -> list[str]:
    get_adapter("generic")
    return sorted(_ADAPTERS)
