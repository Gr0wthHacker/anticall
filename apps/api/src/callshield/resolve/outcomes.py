"""Map a finished call to R-007 outcome, case states and opt-out status.

Conservative by construction: success is never inferred from silence, dropped streams,
incomplete audio or model predictions. ACKNOWLEDGED requires a CLAIMED acknowledgment
taken from the counterparty's own complete transcript after the request was delivered.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from callshield.agent.acknowledgment import CounterpartyKind as K
from callshield.agent.context import AgentSessionContext
from callshield.agent.controller import CallRunResult, StopCondition
from callshield.domain.enums import ContactOutcome as O
from callshield.domain.enums import OptOutStatus
from callshield.domain.enums import ResolutionState as R


@dataclass(frozen=True)
class AttemptOutcome:
    outcome: O
    states: tuple[R, ...]
    delivered: bool
    opt_out_statuses: tuple[OptOutStatus, ...] = field(default_factory=tuple)


def _no_delivery(run: CallRunResult, ctx: AgentSessionContext) -> AttemptOutcome:
    interrupted = (R.INTERRUPTED,)
    if run.no_answer or run.busy:
        return AttemptOutcome(O.NO_ANSWER, interrupted, False)
    if run.stop_condition is StopCondition.DESTINATION_CHANGED:
        return AttemptOutcome(O.WRONG_DESTINATION, interrupted, False)
    if run.stop_condition is not None:
        return AttemptOutcome(O.UNRESOLVED, interrupted, False)
    if run.ivr_failure:
        return AttemptOutcome(O.IVR_FAILED, interrupted, False)
    if run.handoff:
        return AttemptOutcome(O.AGENT_CONNECTED, interrupted, False)
    if run.stream_lost or run.remote_hangup or run.setup_failed:
        return AttemptOutcome(O.DROPPED, interrupted, False)
    if run.human_connected:
        return AttemptOutcome(O.UNRESOLVED, interrupted, False)
    return AttemptOutcome(O.IVR_FAILED, interrupted, False)


def _delivered(run: CallRunResult, ctx: AgentSessionContext) -> AttemptOutcome:
    sent = (OptOutStatus.DELIVERED,)
    flow = (R.REQUEST_DELIVERED,)
    if run.stop_condition is not None:
        return AttemptOutcome(
            O.REQUEST_DELIVERED, (*flow, R.INTERRUPTED), True, (*sent, OptOutStatus.UNCONFIRMED)
        )
    if ctx.verified_ack is not None:
        return AttemptOutcome(
            O.ACKNOWLEDGED, (*flow, R.ACKNOWLEDGED), True, (*sent, OptOutStatus.ACKNOWLEDGED)
        )
    if K.REFUSAL in run.observed:
        return AttemptOutcome(O.REFUSED, (*flow, R.REFUSED), True, (*sent, OptOutStatus.REFUSED))
    unconfirmed = (*sent, OptOutStatus.UNCONFIRMED)
    if K.CHANNEL_REFERRAL in run.observed:
        return AttemptOutcome(O.REFERRED_TO_CHANNEL, (*flow, R.UNCONFIRMED), True, unconfirmed)
    if K.CALL_BACK in run.observed:
        return AttemptOutcome(O.UNRESOLVED, (*flow, R.UNCONFIRMED), True, unconfirmed)
    if run.stream_lost:
        return AttemptOutcome(O.DROPPED, (*flow, R.INTERRUPTED), True, unconfirmed)
    return AttemptOutcome(O.REQUEST_DELIVERED, (*flow, R.UNCONFIRMED), True, unconfirmed)


def derive_outcome(run: CallRunResult, ctx: AgentSessionContext) -> AttemptOutcome:
    if ctx.request_delivered:
        return _delivered(run, ctx)
    return _no_delivery(run, ctx)
