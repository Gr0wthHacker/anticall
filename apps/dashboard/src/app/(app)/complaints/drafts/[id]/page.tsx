"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Badge, EvidenceBadge } from "@/components/Badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { FilingSubmit } from "@/components/FilingSubmit";
import { DefList, Field, Loadable, Notice, PageHeader, Section, StatusMessage } from "@/components/ui";
import { api } from "@/lib/api/client";
import { newIdempotencyKey } from "@/lib/api/idempotency";
import type { Schemas } from "@/lib/api/types";
import { asText, formatDateTime, humanize, isRecord } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

type Draft = Schemas["DraftOut"];

function Words({ draft }: { draft: Draft }) {
  const p = draft.payload;
  const recipient = isRecord(p["recipient"]) ? p["recipient"] : {};
  const channel = isRecord(p["submission_channel"]) ? p["submission_channel"] : {};
  const calls = Array.isArray(p["calls"]) ? (p["calls"] as Record<string, unknown>[]) : [];
  const attachments = Array.isArray(p["attachments"]) ? (p["attachments"] as Record<string, unknown>[]) : [];
  const names = Array.isArray(p["claimed_caller_names"]) ? (p["claimed_caller_names"] as Record<string, unknown>[]) : [];
  return (
    <>
      <Section title="Recipient and channel">
        <DefList rows={[
          ["Recipient", `${asText(recipient["destination"])} (${asText(recipient["agency"])})`],
          ["Official site", asText(recipient["official_url"])],
          ["Submission channel", `${humanize(asText(channel["method"]))}. ${asText(channel["description"])}`],
        ]} />
      </Section>
      <Section title="Exact complaint text">
        <p className="mb-2 text-sm text-muted">This is the exact text that will be approved and filed.</p>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-surface p-3 text-sm" tabIndex={0}>{asText(p["complaint_text"])}</pre>
      </Section>
      <Section title="Calls included">
        {calls.length === 0 ? <p className="text-sm">No calls included.</p> : (
          <ul className="space-y-2 text-sm">{calls.map((c, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2">
              <EvidenceBadge label={asText(c["evidence_label"])} /><span>{c["occurred_at"] ? formatDateTime(asText(c["occurred_at"])) : "Time not recorded"}</span>
              <span>Displayed ID {asText(c["displayed_number"]) || "none"}</span><Badge tone={c["review_status"] === "CONFIRMED" ? "ok" : "warn"}>{asText(c["review_status"])}</Badge>
            </li>
          ))}</ul>
        )}
        {names.length > 0 && (
          <p className="mt-3 text-sm">Names the caller gave (unverified): {names.map((n) => asText(n["name"])).join(", ")} <EvidenceBadge label="CLAIMED" /></p>
        )}
      </Section>
      <Section title="Attachments">
        {attachments.length === 0 ? <p className="text-sm">No attachments. Audio and transcripts are not included unless you chose them and consent rules allow it.</p> : (
          <ul className="space-y-2 text-sm">{attachments.map((a) => (
            <li key={asText(a["artifact_id"])} className="flex flex-wrap items-center gap-2"><EvidenceBadge label={asText(a["evidence_label"])} /><code className="break-all text-xs">{asText(a["artifact_id"])}</code><span className="text-muted">{asText(a["media_type"])}</span></li>
          ))}</ul>
        )}
      </Section>
    </>
  );
}

function Provenance({ draft }: { draft: Draft }) {
  return (
    <Section title="Sources, exclusions and completeness">
      <p className="text-sm">Evidence snapshot digest: <code className="break-all text-xs">{draft.evidence_snapshot_digest}</code></p>
      {draft.exclusions.length > 0 && (<><h3 className="mb-1 mt-3 text-sm font-medium">Left out on purpose</h3><ul className="list-disc pl-5 text-sm">{draft.exclusions.map((x, i) => <li key={i}>{asText(x)}</li>)}</ul></>)}
      {Object.keys(draft.completeness).length > 0 && (<><h3 className="mb-1 mt-3 text-sm font-medium">Completeness</h3><code className="block break-all text-xs">{asText(draft.completeness)}</code></>)}
      {draft.notices.length > 0 && <ul className="mt-3 list-disc pl-5 text-sm">{draft.notices.map((n) => <li key={n}>{n}</li>)}</ul>}
    </Section>
  );
}

function ExternalReceipt({ draftId }: { draftId: string }) {
  const router = useRouter();
  const [filedAt, setFiledAt] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const action = useAction();
  async function submit(e: FormEvent) {
    e.preventDefault();
    const res = await action.run(() => api.post<Schemas["FilingOut"]>(`/v1/filing-drafts/${draftId}/external-receipt`, {
      filed_at: new Date(filedAt).toISOString(), confirmation_number: confirmation || null, idempotency_key: newIdempotencyKey("receipt"),
    }));
    if (res) { setMsg("Saved as your own statement (USER_ATTESTED)."); router.push(`/complaints/filings/${res.filing_id}`); }
  }
  return (
    <Section title="I filed this myself on the official site">
      <p className="mb-3 text-sm text-muted">Record when you filed and any confirmation number you were given. It is saved as your statement, not as proof from the agency. Only enter a number you actually received.</p>
      <form onSubmit={submit} className="grid max-w-xl gap-3" aria-label="Record external filing">
        <Field label="Filed at" htmlFor="rcpt-at"><input id="rcpt-at" type="datetime-local" required className="input" value={filedAt} onChange={(e) => setFiledAt(e.target.value)} /></Field>
        <Field label="Confirmation number (optional)" htmlFor="rcpt-no"><input id="rcpt-no" className="input" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></Field>
        <ErrorBanner error={action.error} /><StatusMessage message={msg} />
        <div><button type="submit" className="btn" disabled={action.pending}>Save my filing record</button></div>
      </form>
    </Section>
  );
}

export default function DraftPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const res = useResource(`draft-${id}`, () => api.get<Draft>(`/v1/filing-drafts/${id}`));
  return (
    <Loadable {...res} rows={5}>
      {(draft) => (
        <>
          <PageHeader title="Review before you file" subtitle={`Revision ${draft.revision}. Review state: ${humanize(draft.review_state)}.`} />
          <Notice tone="warn">Not legal advice. A complaint does not establish that any law was broken.</Notice>
          <div className="mt-4"><Words draft={draft} /></div>
          <Provenance draft={draft} />
          <Section title="Attest, approve and submit">
            <FilingSubmit draft={draft} onChanged={res.reload} onSubmitted={(fid) => router.push(`/complaints/filings/${fid}`)} />
          </Section>
          <ExternalReceipt draftId={draft.draft_id} />
        </>
      )}
    </Loadable>
  );
}
