"""Manual handoff helpers (ENF-016): official path plus a copyable prepared submission."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from callshield.db.models.enforce import FilingDraft
from callshield.db.models.filing_ext import FilingDraftMeta
from callshield.filing.adapters import FilingDraftView, ManualHandoffAdapter, PreparedSubmission
from callshield.filing.destinations import DestinationView


def manual_adapter_for(dest: DestinationView) -> ManualHandoffAdapter:
    return ManualHandoffAdapter(
        adapter_id=f"manual_handoff:{dest.slug}",
        destination_name=dest.name,
        official_url=str(dest.manual_handoff.get("official_url", dest.source_url)),
        steps=tuple(dest.manual_handoff.get("steps", [])),
        supported_issue_types=tuple(dest.supported_issue_types),
        notices=tuple(dest.notices),
    )


def draft_view(draft: FilingDraft, meta: FilingDraftMeta) -> FilingDraftView:
    return FilingDraftView(
        draft_id=draft.id,
        revision=draft.revision,
        agency=draft.agency,
        destination_slug=meta.destination_slug,
        template_version=draft.template_version,
        payload=dict(draft.draft_json.get("payload", {})),
        payload_digest=meta.payload_digest,
    )


def manual_path(dest: DestinationView) -> dict[str, Any]:
    """The official place to file by hand. Never claims electronic submission."""
    return {
        "destination": dest.name,
        "method": "manual_handoff",
        "official_url": dest.manual_handoff.get("official_url", dest.source_url),
        "steps": list(dest.manual_handoff.get("steps", [])),
        "notices": list(dest.notices),
        "validated": dest.validated,
    }


def prepared_for(dest: DestinationView, view: FilingDraftView) -> dict[str, Any]:
    prepared: PreparedSubmission = manual_adapter_for(dest).prepare(view)
    return asdict(prepared)
