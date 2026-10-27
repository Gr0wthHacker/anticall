"""Process-wide callback collaborators (settings, dialer, model, lookup, store, keyer).

Mirrors `defend.verification.set_challenge_channel`: production wiring calls `set_runtime`;
tests inject fakes. Defaults are safe: disabled dialer, no geography, rule-based dialogue,
local filesystem store, in-memory ownership-code outbox only in dev/test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from callshield.callback.config import CallbackSettings
from callshield.callback.dialer_port import CallbackDialer, DisabledCallbackDialer
from callshield.callback.dialogue import CallbackDialogueModel, RuleBasedCallbackModel
from callshield.callback.keys import DestinationKeyer
from callshield.callback.lookup import LineTypeLookup, NoGeographyLookup
from callshield.callback.storage import LocalFsStore, MediaStore
from callshield.core.config import get_settings
from callshield.core.errors import PolicyBlockedError
from callshield.defend.verification import ChallengeChannel, InMemoryOutboxChannel


@dataclass
class CallbackRuntime:
    settings: CallbackSettings
    keyer: DestinationKeyer
    dialer: CallbackDialer | DisabledCallbackDialer = field(default_factory=DisabledCallbackDialer)
    model: CallbackDialogueModel = field(default_factory=RuleBasedCallbackModel)
    line_lookup: LineTypeLookup = field(default_factory=NoGeographyLookup)
    store: MediaStore | None = None
    ownership_channel: ChallengeChannel | None = None
    environment: str = "dev"

    def media_store(self) -> MediaStore:
        if self.store is None:
            self.store = LocalFsStore(self.settings.storage_root)
        return self.store

    def channel(self) -> ChallengeChannel:
        if self.ownership_channel is None:
            if self.environment not in ("dev", "test"):
                raise PolicyBlockedError(
                    "no ownership-check channel configured", code="VERIFICATION_CHANNEL_MISSING"
                )
            self.ownership_channel = InMemoryOutboxChannel()
        return self.ownership_channel


_runtime: CallbackRuntime | None = None


def build_default_runtime() -> CallbackRuntime:
    environment = get_settings().environment
    settings = CallbackSettings()
    keyer = DestinationKeyer(
        settings.hmac_secret.get_secret_value(), allow_dev_default=environment in ("dev", "test")
    )
    return CallbackRuntime(settings=settings, keyer=keyer, environment=environment)


def get_runtime() -> CallbackRuntime:
    global _runtime
    if _runtime is None:
        _runtime = build_default_runtime()
    return _runtime


def set_runtime(runtime: CallbackRuntime | None) -> None:
    global _runtime
    _runtime = runtime
