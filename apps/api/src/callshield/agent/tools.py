"""Bounded agent tool registry (PRD 9.2).

The tool set is a STATIC, enumerated frozenset. There is deliberately no dial, schedule,
disable_audit, bypass_consent or change_trunk_limits tool, and no way to register one at
runtime. Every call (accepted or rejected) is logged for audit (PRD 11.2).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from callshield.agent.context import AgentSessionContext, ClaimRecord
from callshield.agent.templates import OUTBOUND_OPT_OUT, template_text
from callshield.core.redaction import redact_text
from callshield.domain.enums import ContactOutcome, EvidenceLabel

TOOL_NAMES: frozenset[str] = frozenset(
    {
        "read_case_summary",
        "get_verified_destination",
        "get_current_ivr_map",
        "send_dtmf",
        "speak_opt_out",
        "request_human_handoff",
        "record_disposition",
        "end_session",
    }
)

AGENT_DISPOSITION_CODES: frozenset[str] = frozenset(
    {
        ContactOutcome.AGENT_CONNECTED,
        ContactOutcome.REQUEST_DELIVERED,
        ContactOutcome.ACKNOWLEDGED,
        ContactOutcome.REFUSED,
        ContactOutcome.REFERRED_TO_CHANNEL,
        ContactOutcome.UNRESOLVED,
        ContactOutcome.WRONG_DESTINATION,
    }
)
_VALID_DIGITS = frozenset("0123456789*#")
MAX_NOTES = 500


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _reject(error: str) -> ToolResult:
    return ToolResult(False, error=error)


class AgentToolRegistry:
    """Executes ToolCalls against ONE session context. The only path to side effects."""

    def __init__(self, ctx: AgentSessionContext) -> None:
        self._ctx = ctx
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            "read_case_summary": self._read_case_summary,
            "get_verified_destination": self._get_verified_destination,
            "get_current_ivr_map": self._get_current_ivr_map,
            "send_dtmf": self._send_dtmf,
            "speak_opt_out": self._speak_opt_out,
            "request_human_handoff": self._request_human_handoff,
            "record_disposition": self._record_disposition,
            "end_session": self._end_session,
        }
        assert frozenset(self._handlers) == TOOL_NAMES

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def execute(self, call: ToolCall) -> ToolResult:
        handler = self._handlers.get(call.name)
        result = handler(dict(call.args)) if handler else _reject("UNKNOWN_TOOL")
        entry = {
            "tool": call.name,
            "args_keys": sorted(call.args),
            "ok": result.ok,
            "error": result.error,
        }
        self._ctx.tool_log.append(entry)
        if not result.ok:
            self._ctx.rejected_tool_calls.append(entry)
        return result

    # ----------------------------------------------------------------- guards

    def _session_guard(self, args: dict[str, Any]) -> str:
        ctx = self._ctx
        if str(args.get("session_id")) != str(ctx.session_id):
            return "SESSION_MISMATCH"
        if not (ctx.active and ctx.authorized) or ctx.ended:
            return "SESSION_NOT_ACTIVE_OR_AUTHORIZED"
        return ""

    # ------------------------------------------------------------------ tools

    def _read_case_summary(self, args: dict[str, Any]) -> ToolResult:
        if str(args.get("case_id")) != str(self._ctx.case_id):
            return _reject("CASE_MISMATCH")
        return ToolResult(True, dict(self._ctx.case_summary))

    def _get_verified_destination(self, args: dict[str, Any]) -> ToolResult:
        if str(args.get("destination_id")) != str(self._ctx.destination_id):
            return _reject("DESTINATION_MISMATCH")
        return ToolResult(True, dict(self._ctx.destination_info))

    def _get_current_ivr_map(self, args: dict[str, Any]) -> ToolResult:
        if str(args.get("destination_id")) != str(self._ctx.destination_id):
            return _reject("DESTINATION_MISMATCH")
        path = self._ctx.approved_path
        steps = path.to_choices() if path else []
        return ToolResult(True, {"advisory": True, "steps": steps})

    def _send_dtmf(self, args: dict[str, Any]) -> ToolResult:
        ctx = self._ctx
        if err := self._session_guard(args):
            return _reject(err)
        digits = args.get("digits")
        if ctx.approved_path is None or not ctx.approved_path.usable():
            return _reject("NO_APPROVED_PATH")
        if not isinstance(digits, str) or len(digits) != 1 or digits not in _VALID_DIGITS:
            return _reject("INVALID_DIGITS")
        if ctx.pending_dtmf != digits:
            return _reject("DIGIT_NOT_ON_APPROVED_PATH")
        ctx.pending_dtmf = None
        ctx.handle.send_dtmf(digits)
        ctx.dtmf_sent.append(digits)
        return ToolResult(True, {"sent": True})

    def _speak_opt_out(self, args: dict[str, Any]) -> ToolResult:
        ctx = self._ctx
        if err := self._session_guard(args):
            return _reject(err)
        if not ctx.ai_allowed:
            return _reject("AI_SPEECH_NOT_ALLOWED")
        if not ctx.human_connected:
            return _reject("NO_HUMAN_CONNECTED")
        template_id = args.get("template_id")
        text = template_text(template_id) if isinstance(template_id, str) else None
        if text is None or not isinstance(template_id, str):
            return _reject("TEMPLATE_NOT_APPROVED")
        completed = ctx.handle.speak(text)
        ctx.add_transcript("AGENT", text, EvidenceLabel.OBSERVED, complete=completed, offset=0.0)
        ctx.spoken_template_ids.append(template_id)
        if template_id == OUTBOUND_OPT_OUT:
            ctx.request_spoken = True
            ctx.request_delivered = completed
        return ToolResult(True, {"completed": completed})

    def _request_human_handoff(self, args: dict[str, Any]) -> ToolResult:
        if err := self._session_guard(args):
            return _reject(err)
        self._ctx.handoff_requested = True
        return ToolResult(True, {"handoff": True})

    def _record_disposition(self, args: dict[str, Any]) -> ToolResult:
        ctx = self._ctx
        if err := self._session_guard(args):
            return _reject(err)
        code = str(args.get("code"))
        notes = redact_text(str(args.get("notes", "")))[:MAX_NOTES]
        if code not in AGENT_DISPOSITION_CODES:
            return _reject("DISPOSITION_CODE_NOT_ALLOWED")
        if code == ContactOutcome.ACKNOWLEDGED and ctx.verified_ack is None:
            # A model asserting acknowledgment without counterparty evidence: reject and
            # keep only a MODEL_INFERRED note. Never promote it to a fact.
            ctx.claims.append(
                ClaimRecord(
                    "ACKNOWLEDGED_ASSERTED_BY_MODEL",
                    EvidenceLabel.MODEL_INFERRED,
                    notes,
                    accepted=False,
                    note="no matching counterparty transcript",
                )
            )
            return _reject("ACK_NOT_SUPPORTED_BY_TRANSCRIPT")
        ctx.dispositions.append(
            {"code": code, "notes": notes, "label": str(EvidenceLabel.MODEL_INFERRED)}
        )
        return ToolResult(True, {"recorded": code})

    def _end_session(self, args: dict[str, Any]) -> ToolResult:
        if str(args.get("session_id")) != str(self._ctx.session_id):
            return _reject("SESSION_MISMATCH")
        ctx = self._ctx
        if not ctx.ended:
            ctx.ended = True
            ctx.end_reason = ctx.end_reason or "AGENT_ENDED"
            ctx.handle.hangup()
        return ToolResult(True, {"ended": True})


def new_session_id() -> uuid.UUID:
    return uuid.uuid4()
