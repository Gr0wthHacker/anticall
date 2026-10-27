"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Badge, EvidenceBadge } from "@/components/Badge";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Timeline, type TimelineItem } from "@/components/Timeline";
import { DefList, Field, Loadable, Notice, PageHeader, Section, StatusMessage } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { CaseDetailView, CaseTimeline, SufficiencyItem } from "@/lib/api/types";
import { EVIDENCE_LABELS } from "@/lib/api/types";
import { formatDateTime, humanize } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

const ATTRIBUTION = ["UNKNOWN", "CANDIDATE", "CORROBORATED", "DISPUTED"];

function Checklist({ data }: { data: CaseDetailView["evidence_sufficiency"] }) {
  const tone = (s: string) => (s === "PRESENT" ? "ok" : s === "PARTIAL" ? "warn" : "bad");
  return (
    <Section title="Evidence checklist">
      <Notice>{data.disclaimer}</Notice>
      <div className="mt-3" aria-live="polite">
        <p className="mb-2 text-sm font-medium">Missing sources</p>
        {data.missing_sources.length === 0 ? (
          <p className="text-sm">No sources are missing from the checklist.</p>
        ) : (
          <ul className="list-disc pl-5 text-sm text-bad">{data.missing_sources.map((k) => <li key={k}>{humanize(k)}</li>)}</ul>
        )}
      </div>
      <ul className="mt-4 space-y-2">
        {data.items.map((i: SufficiencyItem) => (
          <li key={i.key} className="flex flex-wrap items-start gap-2 rounded-md border border-line p-2 text-sm">
            <Badge tone={tone(i.status)}>{i.status}</Badge>
            <div className="min-w-0 flex-1">
              <p>{String(i["description"] ?? humanize(i.key))}</p>
              {typeof i["detail"] === "string" && i["detail"] && <p className="text-muted">{i["detail"]}</p>}
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-muted">This is a checklist of what is on file. It is not a score of how strong a legal claim is.</p>
    </Section>
  );
}

function UnlinkDialog({ caseId, incidentId, onClose, onDone }: { caseId: string; incidentId: string | null; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const action = useAction();
  async function confirm() {
    const r = await action.run(() => api.post(`/v1/cases/${caseId}/incidents:unlink`, { incident_id: incidentId, reason }));
    if (r) { onClose(); onDone(); }
  }
  return (
    <ConfirmDialog open={incidentId !== null} title="Unlink incident" confirmLabel="Unlink" busy={action.pending} confirmDisabled={reason.trim() === ""} onConfirm={() => void confirm()} onCancel={onClose}>
      <p>The link is closed by adding a new record. The earlier link stays on file.</p>
      <Field label="Reason" htmlFor="unlink-reason"><input id="unlink-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <ErrorBanner error={action.error} />
    </ConfirmDialog>
  );
}

function Incidents({ caseId, detail, reload }: { caseId: string; detail: CaseDetailView; reload: () => void }) {
  const [incidentId, setIncidentId] = useState("");
  const [sameOperator, setSameOperator] = useState(false);
  const [unlinking, setUnlinking] = useState<string | null>(null);
  const action = useAction();
  async function link(e: FormEvent) {
    e.preventDefault();
    const r = await action.run(() =>
      api.post(`/v1/cases/${caseId}/incidents:link`, { incident_id: incidentId.trim(), user_confirms_same_operator: sameOperator, reason: "linked from dashboard" }),
    );
    if (r) { setIncidentId(""); reload(); }
  }
  return (
    <Section title="Linked incidents">
      {detail.incident_links.length === 0 ? <p className="text-sm">No incidents linked.</p> : (
        <ul className="space-y-2">
          {detail.incident_links.map((l) => (
            <li key={l.incident_id} className="flex flex-wrap items-center gap-2 rounded-md border border-line p-2 text-sm">
              <code className="text-xs">{l.incident_id}</code>
              <Badge tone={l.linked ? "ok" : "neutral"}>{l.linked ? "Linked" : "Unlinked"}</Badge>
              <span className="text-muted">{humanize(l.linkage_type)} by {l.actor}</span>
              <EvidenceBadge label={l.effective_label || l.evidence_label} />
              {l.linked && <button type="button" className="btn ml-auto" onClick={() => setUnlinking(l.incident_id)}>Unlink</button>}
            </li>
          ))}
        </ul>
      )}
      {detail.possible_subsequent_incident_ids.length > 0 && (
        <Notice tone="warn">{detail.possible_subsequent_incident_ids.length} later incident(s) are possibly related. Review them before linking. Same caller ID alone does not prove the same caller.</Notice>
      )}
      <form onSubmit={link} className="mt-4 grid max-w-xl gap-3" aria-label="Link an incident">
        <Field label="Incident ID to link" htmlFor="inc-id" hint="Copy an incident ID from a call or review item.">
          <input id="inc-id" className="input" value={incidentId} onChange={(e) => setIncidentId(e.target.value)} required />
        </Field>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 h-5 w-5" checked={sameOperator} onChange={(e) => setSameOperator(e.target.checked)} />
          <span>I believe this incident is from the same operator as the others.</span>
        </label>
        <ErrorBanner error={action.error} />
        <div><button type="submit" className="btn btn-primary" disabled={action.pending}>Link incident</button></div>
      </form>
      <UnlinkDialog caseId={caseId} incidentId={unlinking} onClose={() => setUnlinking(null)} onDone={reload} />
    </Section>
  );
}

function TimelineSection({ caseId }: { caseId: string }) {
  const res = useResource(`case-tl-${caseId}`, () => api.get<CaseTimeline>(`/v1/cases/${caseId}/timeline`));
  return (
    <Section title="Timeline">
      <div className="mb-3 flex flex-wrap gap-2" aria-label="Source labels">
        {EVIDENCE_LABELS.map((l) => <EvidenceBadge key={l} label={l} />)}
      </div>
      <Loadable {...res} rows={3}>
        {(tl) => {
          const items: TimelineItem[] = tl.entries.map((e) => ({
            id: e.entry_id, at: e.occurred_at, label: e.source_label, title: e.summary,
            meta: `${humanize(e.kind)} · time ${humanize(e.time_basis)}`,
          }));
          return items.length ? <Timeline items={items} ariaLabel="Case timeline" /> : <p className="text-sm">No events yet.</p>;
        }}
      </Loadable>
    </Section>
  );
}

function Controls({ caseId, detail, reload }: { caseId: string; detail: CaseDetailView; reload: () => void }) {
  const [attr, setAttr] = useState(detail.case.attribution_status);
  const [basis, setBasis] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const move = useAction();
  const review = useAction();
  async function go(to: string) {
    const r = await move.run(() => api.post(`/v1/cases/${caseId}/transition`, { to_state: to, expected_review_version: detail.case.review_version }));
    if (r) { setMsg(`Moved to ${humanize(to)}.`); reload(); }
  }
  async function submitReview(e: FormEvent) {
    e.preventDefault();
    const r = await review.run(() => api.post(`/v1/cases/${caseId}/attribution-review`, { status: attr, basis }));
    if (r) { setMsg("Attribution review saved."); reload(); }
  }
  return (
    <Section title="Actions">
      <p className="mb-2 text-sm font-medium">Move case forward</p>
      <div className="flex flex-wrap gap-2">
        {detail.available_transitions.length === 0 && <span className="text-sm text-muted">No transitions available from this state.</span>}
        {detail.available_transitions.map((t) => (
          <button key={t} type="button" className="btn" disabled={move.pending} onClick={() => void go(t)}>{humanize(t)}</button>
        ))}
      </div>
      <ErrorBanner error={move.error} />
      <form onSubmit={submitReview} className="mt-5 grid max-w-xl gap-3" aria-label="Attribution review">
        <p className="text-sm font-medium">Attribution review (reviewer roles)</p>
        <Field label="Attribution status" htmlFor="attr-status">
          <select id="attr-status" className="input" value={attr} onChange={(e) => setAttr(e.target.value)}>
            {ATTRIBUTION.map((a) => <option key={a} value={a}>{humanize(a)}</option>)}
          </select>
        </Field>
        <Field label="Basis for this status" htmlFor="attr-basis"><input id="attr-basis" className="input" required value={basis} onChange={(e) => setBasis(e.target.value)} /></Field>
        <ErrorBanner error={review.error} />
        <div><button type="submit" className="btn" disabled={review.pending}>Save review</button></div>
      </form>
      <StatusMessage message={msg} />
    </Section>
  );
}

export default function CaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const res = useResource(`case-${id}`, () => api.get<CaseDetailView>(`/v1/cases/${id}`));
  return (
    <Loadable {...res} rows={5}>
      {(d) => (
        <>
          <PageHeader
            title={d.case.title}
            subtitle="Status fields are kept separate on purpose: state, attribution, recording authorization and legal assessment move independently."
            actions={<Link className="btn" href={`/complaints?case=${id}`}>Complaints for this case</Link>}
          />
          <Section title="Status">
            <DefList rows={[
              ["Case state", <Badge key="s" tone="brand">{humanize(d.case.state)}</Badge>],
              ["Attribution", humanize(d.case.attribution_status)],
              ["Repeat contact verified", d.repeat_contact_verified ? "Yes" : "No. Same caller ID alone is not proof."],
              ["Recording authorization", humanize(d.case.recording_authorization)],
              ["Legal assessment", humanize(d.case.legal_assessment)],
              ["Resolution", humanize(d.case.case_resolution)],
              ["Opened", formatDateTime(d.case.opened_at)],
            ]} />
          </Section>
          <Checklist data={d.evidence_sufficiency} />
          <Incidents caseId={id} detail={d} reload={res.reload} />
          <TimelineSection caseId={id} />
          <Controls caseId={id} detail={d} reload={res.reload} />
        </>
      )}
    </Loadable>
  );
}
