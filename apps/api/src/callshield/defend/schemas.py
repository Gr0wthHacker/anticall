"""Request/response models for DEFEND routers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from callshield.core.phone import InvalidPhoneNumberError, normalize_e164
from callshield.domain.enums import (
    ClassificationLabel,
    ProcessingMode,
    RoutingAction,
    TrustedContactKind,
)
from callshield.policy.config import LineRoutingConfig
from callshield.policy.routing import RULE_ACTIONS, RuleMatcher


def _phone(value: str) -> str:
    try:
        return normalize_e164(value)
    except InvalidPhoneNumberError as exc:
        raise ValueError("invalid phone number") from exc


class Page(BaseModel):
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------------ lines


class LineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    e164: str
    label: str = Field(default="", max_length=100)
    provider: str = Field(default="generic", max_length=64)
    endpoint_id: str | None = Field(default=None, max_length=120)
    routing_config: LineRoutingConfig = Field(default_factory=LineRoutingConfig)

    @field_validator("e164")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return _phone(value)


class LinePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=100)
    routing_config: dict[str, Any] | None = None  # partial; merged then validated


class LineOut(BaseModel):
    id: uuid.UUID
    e164: str
    label: str
    verification_status: str
    verified_at: datetime | None
    screening_enabled: bool
    routing_config: dict[str, Any]
    routable: bool
    created_at: datetime


class WebhookCredentials(BaseModel):
    """Shown once at creation / rotation. Store the secret; it is never returned again."""

    url_path: str
    line_key: str
    secret: str
    signature_scheme: str = (
        "HMAC-SHA256 over '<timestamp>.<nonce>.<body>', header X-CallShield-Signature: v1=<hex>"
    )


class LineCreated(LineOut):
    webhook: WebhookCredentials


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=4, max_length=12)


class VerifyResponse(BaseModel):
    status: str
    detail: str


class ScreeningToggleResponse(BaseModel):
    line_id: uuid.UUID
    screening_enabled: bool
    effective_routing: str
    processing_allows_screening: bool


# ------------------------------------------------------------------ calls


class CallSummary(BaseModel):
    id: uuid.UUID
    line_id: uuid.UUID | None
    direction: str
    disposition: str
    started_at: datetime | None
    ended_at: datetime | None
    displayed_caller_id: str | None
    label: str | None
    category: str | None


class CallList(Page):
    items: list[CallSummary]


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: ClassificationLabel
    note: str = Field(default="", max_length=500)


# ------------------------------------------------------------------ rules


def _check_rule_action(value: RoutingAction) -> RoutingAction:
    if value not in RULE_ACTIONS:
        raise ValueError("action must be one of ALLOW, TRANSFER, SCREEN, VOICEMAIL, REJECT")
    return value


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: uuid.UUID
    priority: int = Field(default=100, ge=0, le=100000)
    matcher: RuleMatcher
    action: RoutingAction
    enabled: bool = True

    _validate_action = field_validator("action")(_check_rule_action)


class RulePut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=0, le=100000)
    matcher: RuleMatcher
    action: RoutingAction
    enabled: bool = True

    _validate_action = field_validator("action")(_check_rule_action)


class RuleOut(BaseModel):
    id: uuid.UUID
    line_id: uuid.UUID
    priority: int
    matcher: dict[str, Any]
    action: str
    enabled: bool
    created_at: datetime


# ------------------------------------------------------------------ contacts


class ContactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: uuid.UUID
    e164: str
    kind: TrustedContactKind = TrustedContactKind.TRUSTED
    name: str = Field(default="", max_length=200)

    @field_validator("e164")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return _phone(value)


class ContactPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TrustedContactKind | None = None
    name: str | None = Field(default=None, max_length=200)


class ContactOut(BaseModel):
    id: uuid.UUID
    line_id: uuid.UUID
    e164: str
    kind: str
    name: str
    created_at: datetime


# ------------------------------------------------------------------ consent


class ConsentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processing_mode: ProcessingMode
    authorized: bool
    all_party_basis_confirmed: bool = False
    remote_stt_ok: bool = False
    notes: str = Field(default="", max_length=500)


class ConsentOut(BaseModel):
    id: uuid.UUID
    processing_mode: str
    authorized: bool
    authorized_at: datetime | None
    revoked_at: datetime | None
    legal_basis: dict[str, Any]
    created_at: datetime


class DecisionOut(BaseModel):
    allow_live_stt: bool
    allow_recording: bool
    allow_transcript_persist: bool
    allow_remote_stt: bool
    mode: str
    reasons: list[str]
    jurisdiction: str
    policy_version: str


class ConsentView(BaseModel):
    records: list[ConsentOut]
    decision: DecisionOut


# ------------------------------------------------------------------ audit


class AuditEventOut(BaseModel):
    id: uuid.UUID
    actor: str
    action: str
    data: dict[str, Any]
    time: datetime
    immutable_ref: str


class AuditEventList(Page):
    items: list[AuditEventOut]


class TelephonyHealthOut(BaseModel):
    overall: str
    providers: list[dict[str, Any]]
    adapters: list[dict[str, Any]]
