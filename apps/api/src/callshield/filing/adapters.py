"""Filing adapter contract (PRD 19.8) and registry (ENF-015).

`manual_handoff` adapters implement validate/prepare only: they have NO `submit`.
Only adapters with a documented, authorized API are registered as `documented_api`,
and registration refuses anything else. There is no CAPTCHA handling, no headless
browser, no simulated agency API and no bulk path in this module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from callshield.domain.enums import AdapterMethod


@dataclass(frozen=True)
class FilingDraftView:
    """What an adapter may see of a draft: the exact approved payload and identifiers."""

    draft_id: uuid.UUID
    revision: int
    agency: str
    destination_slug: str
    template_version: str
    payload: dict[str, Any]
    payload_digest: str
    issue_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedSubmission:
    """Copyable manual-handoff package (ENF-016). Nothing here is transmitted."""

    destination_name: str
    method: str
    official_url: str
    steps: tuple[str, ...]
    copyable_text: str
    fields: dict[str, Any]
    payload_digest: str
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApprovedSubmission:
    """Input to submit(): the exact approved payload and its digest, nothing else."""

    draft_id: uuid.UUID
    approval_id: uuid.UUID
    payload: dict[str, Any]
    payload_digest: str
    idempotency_key: str
    scoped_credential_ref: str


class TransportOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"  # transport acknowledged receipt
    REJECTED = "REJECTED"  # definitively not accepted; nothing was received
    AMBIGUOUS = "AMBIGUOUS"  # timeout / unparseable / no clear answer


@dataclass(frozen=True)
class SubmissionReceipt:
    outcome: TransportOutcome
    transport_ref: str | None = None
    # Present only when the receiving body itself confirmed, distinguishably from transport.
    agency_confirmation: str | None = None
    endpoint: str = ""
    response: dict[str, Any] = field(default_factory=dict)
    failure_code: str | None = None


class FilingStatusState(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    RECEIVED = "RECEIVED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FilingStatus:
    state: FilingStatusState
    transport_ref: str | None = None
    agency_confirmation: str | None = None
    response: dict[str, Any] = field(default_factory=dict)


class FilingAdapter(Protocol):
    """PRD 19.8 interface. `submit` and `lookup_status` are optional (documented_api only)."""

    id: str
    destination_name: str
    method: AdapterMethod
    supported_issue_types: tuple[str, ...]

    def validate(self, draft: FilingDraftView) -> ValidationReport: ...

    def prepare(self, draft: FilingDraftView) -> PreparedSubmission: ...


@runtime_checkable
class SubmittingAdapter(Protocol):
    """A documented_api adapter. Must implement both calls."""

    id: str
    method: AdapterMethod

    def submit(self, submission: ApprovedSubmission) -> SubmissionReceipt: ...

    def lookup_status(self, idempotency_key: str, payload_digest: str) -> FilingStatus: ...


class ManualHandoffAdapter:
    """Prepares a copyable submission for the official site. Deliberately has no submit()."""

    method = AdapterMethod.MANUAL_HANDOFF

    def __init__(
        self,
        adapter_id: str,
        destination_name: str,
        official_url: str,
        steps: tuple[str, ...],
        supported_issue_types: tuple[str, ...] = (),
        notices: tuple[str, ...] = (),
    ) -> None:
        self.id = adapter_id
        self.destination_name = destination_name
        self.supported_issue_types = supported_issue_types
        self._official_url = official_url
        self._steps = steps
        self._notices = notices

    def validate(self, draft: FilingDraftView) -> ValidationReport:
        errors = []
        if not draft.payload.get("complaint_text"):
            errors.append("complaint_text is empty")
        if not draft.payload_digest:
            errors.append("payload digest missing")
        return ValidationReport(not errors, tuple(errors))

    def prepare(self, draft: FilingDraftView) -> PreparedSubmission:
        payload = draft.payload
        return PreparedSubmission(
            destination_name=self.destination_name,
            method=self.method.value,
            official_url=self._official_url,
            steps=self._steps,
            copyable_text=str(payload.get("complaint_text", "")),
            fields=dict(payload.get("fields", {})),
            payload_digest=draft.payload_digest,
            notices=self._notices,
        )


class AdapterRegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class RegisteredAdapter:
    adapter: SubmittingAdapter
    scoped_credential_ref: str


_DOCUMENTED: dict[str, RegisteredAdapter] = {}


def register_documented_adapter(
    destination_slug: str, adapter: SubmittingAdapter, scoped_credential_ref: str
) -> None:
    """Register an adapter for a destination. Refuses anything not documented_api with submit."""
    if getattr(adapter, "method", None) is not AdapterMethod.DOCUMENTED_API:
        raise AdapterRegistrationError("only documented_api adapters may be registered")
    if not callable(getattr(adapter, "submit", None)) or not callable(
        getattr(adapter, "lookup_status", None)
    ):
        raise AdapterRegistrationError(
            "documented_api adapters must implement submit and lookup_status"
        )
    if not scoped_credential_ref:
        raise AdapterRegistrationError("a scoped credential reference is required")
    _DOCUMENTED[destination_slug] = RegisteredAdapter(adapter, scoped_credential_ref)


def unregister_documented_adapter(destination_slug: str) -> None:
    _DOCUMENTED.pop(destination_slug, None)


def reset_adapters() -> None:
    _DOCUMENTED.clear()


def documented_adapter_for(destination_slug: str) -> RegisteredAdapter | None:
    return _DOCUMENTED.get(destination_slug)


def registered_slugs() -> frozenset[str]:
    return frozenset(_DOCUMENTED)
