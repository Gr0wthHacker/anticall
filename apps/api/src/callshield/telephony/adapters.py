"""Concrete adapters: a generic JSON webhook and an Asterisk ARI-style event shim.

Neither performs network calls: route methods build RouteInstruction objects that the
webhook response carries back to the telephony gateway.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from callshield.core.phone import InvalidPhoneNumberError, normalize_e164
from callshield.telephony.base import (
    MalformedEventError,
    ProviderCallEvent,
    ProviderEventType,
    RouteInstruction,
)
from callshield.telephony.signing import verify_signed_request

MAX_BODY_BYTES = 64 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def load_json_object(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_BODY_BYTES:
        raise MalformedEventError("body too large")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MalformedEventError("invalid json") from exc
    if not isinstance(data, dict):
        raise MalformedEventError("body must be an object")
    return data


def clean_display(value: object, limit: int = 64) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str | int):
        raise MalformedEventError("bad caller field")
    text = _CONTROL.sub("", str(value)).strip()[:limit]
    return text or None


def to_e164(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MalformedEventError("bad number field")
    try:
        return normalize_e164(value)
    except InvalidPhoneNumberError as exc:
        raise MalformedEventError("bad number") from exc


def parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(float(value), UTC)
        if isinstance(value, str):
            fixed = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00"))
            parsed = datetime.fromisoformat(fixed)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise MalformedEventError("bad timestamp") from exc
    raise MalformedEventError("bad timestamp")


def build_event(**fields: Any) -> ProviderCallEvent:
    try:
        return ProviderCallEvent(**fields)
    except ValidationError as exc:
        raise MalformedEventError("event failed validation") from exc


class BaseAdapter:
    """Shared HMAC verification and instruction builders."""

    name = "base"

    def verify_webhook(
        self, headers: Mapping[str, str], body: bytes, secret: str, tolerance_seconds: int = 300
    ) -> str:
        return verify_signed_request(headers, body, secret, tolerance_seconds)

    def answer(self, call_id: str) -> RouteInstruction:
        return RouteInstruction(op="answer", call_id=call_id)

    def transfer(self, call_id: str, target: str, reason: str = "") -> RouteInstruction:
        return RouteInstruction(op="transfer", call_id=call_id, target=target, reason=reason)

    def voicemail(self, call_id: str, reason: str = "") -> RouteInstruction:
        return RouteInstruction(op="voicemail", call_id=call_id, reason=reason)

    def hangup(self, call_id: str, reason: str = "") -> RouteInstruction:
        return RouteInstruction(op="hangup", call_id=call_id, reason=reason)

    def play(self, call_id: str, text: str) -> RouteInstruction:
        return RouteInstruction(op="play", call_id=call_id, text=text)

    def health(self) -> dict[str, str]:
        return {"status": "UNKNOWN", "detail": "no active probe; status follows verified events"}

    def restore_fallback(self, endpoint_id: str | None) -> bool:
        return False  # gateway-specific; generic adapters cannot reconfigure the gateway


class GenericWebhookAdapter(BaseAdapter):
    name = "generic"

    def parse_event(self, body: bytes) -> ProviderCallEvent:
        data = load_json_object(body)
        try:
            etype = ProviderEventType(str(data.get("type")))
        except ValueError as exc:
            raise MalformedEventError("unknown event type") from exc
        diversions = data.get("diversions") or []
        if not isinstance(diversions, list):
            raise MalformedEventError("bad diversions")
        attestation = data.get("attestation") or {}
        if not isinstance(attestation, dict):
            raise MalformedEventError("bad attestation")
        return build_event(
            provider=self.name,
            event_id=str(data.get("event_id") or ""),
            event_type=etype,
            call_id=data.get("call_id"),
            to_number=to_e164(data.get("to")),
            endpoint_id=data.get("endpoint_id"),
            from_number=clean_display(data.get("from")),
            direction=str(data.get("direction") or "INBOUND").upper(),
            attestation=attestation,
            hop_count=data.get("hop_count") or 0,
            diversions=tuple(to_e164(d) or "" for d in diversions),
            occurred_at=parse_timestamp(data.get("timestamp")),
            duration_seconds=data.get("duration_seconds"),
            text=data.get("text"),
            detail=str(data.get("detail") or "")[:200],
        )


_ARI_TYPES: dict[str, ProviderEventType] = {
    "StasisStart": ProviderEventType.CALL_RECEIVED,
    "BlindTransfer": ProviderEventType.CALL_TRANSFERRED,
    "StasisEnd": ProviderEventType.CALL_ENDED,
    "ChannelUtterance": ProviderEventType.CALLER_UTTERANCE,  # custom: STT result
    "ScreeningTimeout": ProviderEventType.SCREENING_TIMEOUT,  # custom: AI/stream lost
}


def _ari_endpoint(channel_name: object) -> str | None:
    if not isinstance(channel_name, str) or "/" not in channel_name:
        return None
    return channel_name.split("/", 1)[1].rsplit("-", 1)[0][:120] or None


def _ari_args(args: object) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in args if isinstance(args, list) else []:
        if isinstance(item, str) and "=" in item:
            key, _, value = item.partition("=")
            out[key] = value
    return out


class AriAdapter(BaseAdapter):
    """Asterisk ARI-style events as forwarded by a signing relay."""

    name = "ari"

    def _ari_type(self, data: dict[str, Any]) -> ProviderEventType:
        kind = str(data.get("type"))
        if kind == "ChannelStateChange":
            state = (data.get("channel") or {}).get("state")
            if state == "Up":
                return ProviderEventType.CALL_ANSWERED
        if kind == "PeerStatusChange":
            status = (data.get("peer") or {}).get("peer_status")
            if status in ("Unreachable", "Unregistered", "Rejected"):
                return ProviderEventType.REGISTRATION_FAILED
            if status in ("Reachable", "Registered"):
                return ProviderEventType.REGISTRATION_OK
        mapped = _ARI_TYPES.get(kind)
        if mapped is None:
            raise MalformedEventError("unsupported ARI event")
        return mapped

    def parse_event(self, body: bytes) -> ProviderCallEvent:
        data = load_json_object(body)
        etype = self._ari_type(data)
        channel = data.get("channel") or {}
        if not isinstance(channel, dict):
            raise MalformedEventError("bad channel")
        caller = channel.get("caller") or {}
        args = _ari_args(data.get("args"))
        peer = data.get("peer") or {}
        endpoint = _ari_endpoint(channel.get("name")) or (
            str(peer.get("name"))[:120] if isinstance(peer, dict) and peer.get("name") else None
        )
        ts = data.get("timestamp")
        cid = channel.get("id")
        return build_event(
            provider=self.name,
            event_id=f"{data.get('type')}:{cid}:{ts}",
            event_type=etype,
            call_id=cid,
            to_number=to_e164((channel.get("dialplan") or {}).get("exten")),
            endpoint_id=endpoint,
            from_number=clean_display((caller or {}).get("number")),
            attestation={"attest": args["attest"]} if "attest" in args else {},
            hop_count=int(args["hops"]) if args.get("hops", "").isdigit() else 0,
            occurred_at=parse_timestamp(ts),
            text=data.get("text"),
            detail=str(data.get("detail") or "")[:200],
        )
