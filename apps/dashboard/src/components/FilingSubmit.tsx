"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api/client";
import { newIdempotencyKey } from "@/lib/api/idempotency";
import { ApiRequestError } from "@/lib/api/errors";
import type { ManualHandoff, PreparedSubmission, Schemas, UnsupportedDestinationDetails } from "@/lib/api/types";
import { useAction } from "@/lib/hooks";
import { Badge } from "./Badge";
import { ErrorBanner } from "./ErrorBanner";
import { Notice, StatusMessage } from "./ui";

type Draft = Schemas["DraftOut"];
type Statement = { id: string; text: string; required?: boolean };

export function attestationStatements(draft: Draft): Statement[] {
  const raw = draft.attestation["statements"];
  return Array.isArray(raw) ? (raw as Statement[]) : [];
}

function displayedItems(draft: Draft): string[] {
  const raw = draft.attestation["must_display_before_accept"];
  return Array.isArray(raw) ? (raw as string[]) : [];
}

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<string | null>(null);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("Copied to clipboard.");
    } catch {
      setState("Copy failed. Select the text above and copy it by hand.");
    }
  }
  return (
    <div className="mt-2">
      <button type="button" className="btn" onClick={() => void copy()}>Copy prepared submission</button>
      <StatusMessage message={state} />
    </div>
  );
}

export function ManualHandoffPanel({ details }: { details: UnsupportedDestinationDetails }) {
  const handoff: ManualHandoff = details.manual_handoff ?? {};
  const prepared: PreparedSubmission | undefined = details.prepared_submission;
  return (
    <section aria-labelledby="handoff-h" className="rounded-md border border-warn/60 bg-warn/10 p-4" data-testid="manual-handoff">
      <h3 id="handoff-h" className="text-base font-semibold">This destination cannot be filed electronically from here</h3>
      <p className="mt-1 text-sm">Nothing was sent. File it yourself on the official site using the prepared text below.</p>
      {handoff.official_url && (
        <p className="mt-2 text-sm">Official site: <a className="link" href={handoff.official_url} target="_blank" rel="noreferrer noopener">{handoff.official_url}</a></p>
      )}
      {handoff.steps && handoff.steps.length > 0 && (
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">{handoff.steps.map((s) => <li key={s}>{s}</li>)}</ol>
      )}
      {prepared && (
        <div className="mt-3">
          <p className="text-sm font-medium">Prepared submission (digest {prepared.payload_digest})</p>
          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-panel p-3 text-xs">{prepared.copyable_text}</pre>
          <CopyButton text={prepared.copyable_text} />
        </div>
      )}
      {details.approval_status === "VOID" && <p className="mt-2 text-sm text-bad">Your earlier approval no longer matches the draft. Approve again before filing.</p>}
    </section>
  );
}

function unsupportedDetails(error: unknown): UnsupportedDestinationDetails | null {
  if (error instanceof ApiRequestError && error.status === 422 && error.code === "UNSUPPORTED_DESTINATION") {
    return error.details as UnsupportedDestinationDetails;
  }
  return null;
}

interface Props {
  draft: Draft;
  onChanged?: () => void;
  /** Called with the filing id after a transmitted (not manual) submission. */
  onSubmitted?: (filingId: string) => void;
}

export function FilingSubmit({ draft, onChanged, onSubmitted }: Props) {
  const statements = attestationStatements(draft);
  const [accepted, setAccepted] = useState<string[]>([]);
  const [sharing, setSharing] = useState(false);
  const [approved, setApproved] = useState<Schemas["ApprovalOut"] | null>(null);
  const [submitKey] = useState(() => newIdempotencyKey("submit"));
  const approve = useAction();
  const submit = useAction();
  const isApproved = approved !== null || draft.review_state === "APPROVED";
  const allAccepted = statements.filter((s) => s.required !== false).every((s) => accepted.includes(s.id));

  async function doApprove() {
    const res = await approve.run(() =>
      api.post<Schemas["ApprovalOut"]>(`/v1/filing-drafts/${draft.draft_id}/approve`, {
        accepted_statement_ids: accepted,
        attestation_version: String(draft.attestation["version"] ?? ""),
        displayed_items: displayedItems(draft),
        evidence_snapshot_digest: draft.evidence_snapshot_digest,
        payload_digest: draft.payload_digest,
        sharing_confirmed: sharing,
        idempotency_key: newIdempotencyKey("approve"),
      }),
    );
    if (res) { setApproved(res); onChanged?.(); }
  }

  async function doSubmit() {
    const res = await submit.run(() => api.post<Schemas["FilingOut"]>(`/v1/filing-drafts/${draft.draft_id}/submit`, {}, submitKey));
    if (res) onSubmitted?.(res.filing_id);
  }

  const handoff = unsupportedDetails(submit.raw);

  return (
    <div className="space-y-4">
      <fieldset className="rounded-md border border-line p-3">
        <legend className="px-1 text-sm font-medium">Attestation (version {String(draft.attestation["version"] ?? "")})</legend>
        {draft.attestation["notice"] ? <p className="mb-2 text-xs text-muted">{String(draft.attestation["notice"])}</p> : null}
        <ul className="space-y-2">
          {statements.map((s) => (
            <li key={s.id}>
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1 h-5 w-5" disabled={isApproved} checked={accepted.includes(s.id) || isApproved}
                  onChange={(e) => setAccepted((a) => (e.target.checked ? [...a, s.id] : a.filter((x) => x !== s.id)))} />
                <span>{s.text}</span>
              </label>
            </li>
          ))}
        </ul>
        <label className="mt-3 flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 h-5 w-5" disabled={isApproved} checked={sharing || isApproved} onChange={(e) => setSharing(e.target.checked)} />
          <span>I confirm the selected material may be shared with this recipient.</span>
        </label>
      </fieldset>

      <p className="text-sm">Payload digest (SHA-256): <code className="break-all text-xs">{approved?.approved_payload_digest ?? draft.payload_digest}</code></p>

      {isApproved ? (
        <p className="flex items-center gap-2 text-sm"><Badge tone="ok">APPROVED</Badge> Approval is bound to the digest above. If the draft changes, you must approve again.</p>
      ) : (
        <div>
          <button type="button" className="btn btn-primary" disabled={!allAccepted || !sharing || approve.pending} onClick={() => void doApprove()}>
            Approve this exact draft
          </button>
          <ErrorBanner error={approve.error} />
        </div>
      )}

      <div>
        <button type="button" className="btn btn-primary" disabled={!isApproved || submit.pending} onClick={() => void doSubmit()}>
          Submit
        </button>
        {!isApproved && <p className="mt-1 text-xs text-muted">Submit unlocks after you approve.</p>}
        {handoff ? <div className="mt-3"><ManualHandoffPanel details={handoff} /></div> : <ErrorBanner error={submit.error} />}
        {handoff && draft.case_id && <p className="mt-2 text-sm"><Link className="link" href={`/cases/${draft.case_id}`}>Back to the case</Link></p>}
      </div>
      <Notice>Submitting never retries by itself. If the result is unclear, the filing shows UNKNOWN and you check its status; it is not sent again.</Notice>
    </div>
  );
}
