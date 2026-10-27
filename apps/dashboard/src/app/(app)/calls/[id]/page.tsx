"use client";

import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Badge, EvidenceBadge } from "@/components/Badge";
import { CallbackPanel } from "@/components/CallbackPanel";
import { ErrorBanner } from "@/components/ErrorBanner";
import { DefList, KeyValues, Loadable, PageHeader, Section, StatusMessage } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { CallDetail, ClassificationView } from "@/lib/api/types";
import { CLASS_LABELS } from "@/lib/constants";
import { useFeatures } from "@/lib/features";
import { formatDateTime, formatDuration, humanize } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

function Facts({ call }: { call: CallDetail }) {
  const f = call.facts;
  return (
    <Section title="Observed facts">
      <p className="mb-3 flex items-center gap-2 text-sm">
        <EvidenceBadge label={f.evidence_label} /> <span className="text-muted">{f.note}</span>
      </p>
      <DefList
        rows={[
          ["Direction", humanize(f.direction)],
          ["Disposition", humanize(f.disposition)],
          ["Displayed caller ID", f.displayed_caller_id ?? "None shown"],
          ["Started", formatDateTime(f.started_at)],
          ["Ended", formatDateTime(f.ended_at)],
          ["Duration", formatDuration(f.duration_seconds)],
          ["Carrier / provider", f.provider],
        ]}
      />
      <h3 className="mb-2 mt-4 text-sm font-medium">Network attestation (STIR/SHAKEN)</h3>
      <KeyValues data={f.network_attestation} />
      {call.routing && (
        <>
          <h3 className="mb-2 mt-4 text-sm font-medium">Routing applied</h3>
          <DefList
            rows={[
              ["Action", humanize(call.routing.action)],
              ["Reason", call.routing.reason],
              ["Processing mode", humanize(call.routing.processing_mode)],
              ["Screening active", call.routing.screening_active ? "Yes" : "No"],
            ]}
          />
        </>
      )}
    </Section>
  );
}

function Claims({ call }: { call: CallDetail }) {
  return (
    <Section title="Claims made by the caller">
      <p className="mb-3 text-sm text-muted">Claims are what the caller said. They are not verified.</p>
      {call.claims.length === 0 ? (
        <p className="text-sm">No claims recorded.</p>
      ) : (
        <ul className="space-y-2">
          {call.claims.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-2 text-sm">
              <EvidenceBadge label={c.evidence_label} />
              <span>{humanize(c.statement_type)}: {c.claimed}</span>
              <Badge>{humanize(c.verification_status)}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function ConclusionRow({ c }: { c: ClassificationView }) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm">
      <EvidenceBadge label={c.evidence_label} />
      <strong>{humanize(c.label)}</strong>
      {typeof c.confidence === "number" && <span className="text-muted">confidence {c.confidence.toFixed(2)}</span>}
      {c.model_version && <span className="text-muted">({c.model_version})</span>}
      {c.reviewed_status && <Badge>{humanize(c.reviewed_status)}</Badge>}
    </li>
  );
}

function Conclusions({ call }: { call: CallDetail }) {
  return (
    <Section title="Model conclusions">
      <p className="mb-3 text-sm text-muted">
        These are inferences and can be wrong. A correction from you is added as a new record; the original stays on file.
      </p>
      {call.model_conclusions.length === 0 ? (
        <p className="text-sm">No classification yet.</p>
      ) : (
        <ul className="space-y-2">{call.model_conclusions.map((c, i) => <ConclusionRow key={c.id ?? i} c={c} />)}</ul>
      )}
      {call.effective_classification && (
        <p className="mt-3 text-sm">Current classification: <strong>{humanize(call.effective_classification.label)}</strong></p>
      )}
    </Section>
  );
}

function CorrectionForm({ callId, onDone }: { callId: string; onDone: () => void }) {
  const [label, setLabel] = useState<string>("UNSOLICITED_SALES");
  const [note, setNote] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const action = useAction();
  async function submit(e: FormEvent) {
    e.preventDefault();
    setDone(null);
    const res = await action.run(() => api.post(`/v1/calls/${callId}/corrections`, { label, note }));
    if (res) {
      setDone("Correction saved as a new record.");
      onDone();
    }
  }
  return (
    <Section title="Correct the classification">
      <form onSubmit={submit} className="grid max-w-xl gap-3" aria-label="Correct classification">
        <div>
          <label htmlFor="corr-label" className="label">What was this call?</label>
          <select id="corr-label" className="input" value={label} onChange={(e) => setLabel(e.target.value)}>
            {CLASS_LABELS.map((l) => <option key={l} value={l}>{humanize(l)}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="corr-note" className="label">Note (optional)</label>
          <textarea id="corr-note" className="input" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <ErrorBanner error={action.error} />
        <StatusMessage message={done} />
        <div><button type="submit" className="btn btn-primary" disabled={action.pending}>Save correction</button></div>
      </form>
    </Section>
  );
}

export default function CallDetailPage() {
  const { id } = useParams<{ id: string }>();
  const features = useFeatures();
  const res = useResource(`call-${id}`, () => api.get<CallDetail>(`/v1/calls/${id}`));
  return (
    <>
      <PageHeader title="Call detail" subtitle="Observed facts, caller claims and model conclusions are kept apart." />
      <Loadable {...res} rows={5}>
        {(call) => (
          <>
            <Facts call={call} />
            <Claims call={call} />
            <Conclusions call={call} />
            <CorrectionForm callId={call.id} onDone={res.reload} />
            {call.facts.direction === "INBOUND" && (
              <CallbackPanel
                callId={call.id}
                incidentId={call.incident_id}
                callbackEnabled={features.loaded ? (features.callback?.callback_enabled ?? false) : null}
              />
            )}
          </>
        )}
      </Loadable>
    </>
  );
}
