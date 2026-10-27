"use client";

import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Badge, EvidenceBadge } from "@/components/Badge";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Timeline, type TimelineItem } from "@/components/Timeline";
import { DefList, Field, Loadable, Notice, PageHeader, Section, StatusMessage } from "@/components/ui";
import { api } from "@/lib/api/client";
import { newIdempotencyKey } from "@/lib/api/idempotency";
import type { Schemas } from "@/lib/api/types";
import { CONTACT_OUTCOMES } from "@/lib/constants";
import { asText, formatDateTime, humanize, isRecord } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

type Detail = Schemas["CaseDetail"];
interface TimelineRow { seq: number; kind: string; actor: string; data: Record<string, unknown>; created_at: string }

function Destinations({ d }: { d: Detail }) {
  return (
    <Section title="Destinations and provenance">
      <p className="mb-3 text-sm text-muted">Where the request could go and how we know. A destination known only from caller ID is never dialed automatically.</p>
      {d.destinations.length === 0 ? <p className="text-sm">No destinations recorded.</p> : (
        <ul className="space-y-2">
          {d.destinations.map((x) => (
            <li key={x.id} className="flex flex-wrap items-center gap-2 rounded-md border border-line p-2 text-sm">
              <span>{x.phone_masked}</span>
              <Badge>{humanize(x.provenance)}</Badge>
              <Badge tone={x.verification_status === "VERIFIED" ? "ok" : "warn"}>{humanize(x.verification_status)}</Badge>
              {!x.is_current && <Badge>Superseded</Badge>}
              {x.verified_at && <span className="text-muted">verified {formatDateTime(x.verified_at)}</span>}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function Attempts({ d }: { d: Detail }) {
  return (
    <Section title="Attempts">
      {d.attempts.length === 0 ? <p className="text-sm">No attempts logged.</p> : (
        <ul className="space-y-2">
          {d.attempts.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-2 text-sm">
              <EvidenceBadge label={a.evidence_label} />
              <span>{formatDateTime(a.started_at)}</span>
              <Badge>{humanize(a.mode)}</Badge>
              <span>{a.outcome ? humanize(a.outcome) : "No outcome"}</span>
              <span className="text-muted">to {a.dialed_phone_masked}</span>
              {a.parked_for_review && <Badge tone="warn">Needs review</Badge>}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function ManualAttempt({ d, reload }: { d: Detail; reload: () => void }) {
  const [dest, setDest] = useState("");
  const [outcome, setOutcome] = useState<string>("UNRESOLVED");
  const [when, setWhen] = useState("");
  const [notes, setNotes] = useState("");
  const [historic, setHistoric] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const action = useAction();
  async function submit(e: FormEvent) {
    e.preventDefault();
    const destination = dest || d.destinations[0]?.id;
    if (!destination) return;
    const r = await action.run(() =>
      api.post(`/v1/resolution-cases/${d.id}/attempts/manual`, {
        destination_id: destination, outcome, notes, historic,
        occurred_at: when ? new Date(when).toISOString() : null,
        idempotency_key: newIdempotencyKey("attempt"),
      }),
    );
    if (r) { setMsg("Attempt logged as USER_ATTESTED."); setNotes(""); reload(); }
  }
  if (d.destinations.length === 0) return <Section title="Log an attempt you made"><p className="text-sm">Add a destination before logging an attempt.</p></Section>;
  return (
    <Section title="Log an attempt you made yourself">
      <form onSubmit={submit} className="grid max-w-xl gap-3" aria-label="Log a manual attempt">
        <Field label="Destination" htmlFor="ma-dest">
          <select id="ma-dest" className="input" value={dest} onChange={(e) => setDest(e.target.value)}>
            {d.destinations.map((x) => <option key={x.id} value={x.id}>{x.phone_masked} ({humanize(x.provenance)})</option>)}
          </select>
        </Field>
        <Field label="Outcome" htmlFor="ma-outcome">
          <select id="ma-outcome" className="input" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            {CONTACT_OUTCOMES.map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
          </select>
        </Field>
        <Field label="When" htmlFor="ma-when" hint="Leave empty for now."><input id="ma-when" type="datetime-local" className="input" value={when} onChange={(e) => setWhen(e.target.value)} /></Field>
        <Field label="Notes" htmlFor="ma-notes"><textarea id="ma-notes" className="input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 h-5 w-5" checked={historic} onChange={(e) => setHistoric(e.target.checked)} /><span>This happened in the past and I am recording it from memory.</span></label>
        <ErrorBanner error={action.error} />
        <StatusMessage message={msg} />
        <div><button type="submit" className="btn btn-primary" disabled={action.pending}>Log attempt</button></div>
      </form>
    </Section>
  );
}

function NextSteps({ d }: { d: Detail }) {
  return (
    <Section title="Suggested next steps">
      <Notice>These are read-only recommendations. Nothing happens unless you act, and none of them starts a call by itself.</Notice>
      {d.next_steps.length === 0 ? <p className="mt-3 text-sm">No suggestions right now.</p> : (
        <ul className="mt-3 space-y-2">
          {d.next_steps.map((s) => (
            <li key={s.id} className="rounded-md border border-line p-2 text-sm">
              <p className="font-medium">{humanize(s.kind)}</p>
              <p className="text-muted">{s.rationale}</p>
              {s.requires_new_authorization && <p className="text-warn">Needs your fresh authorization first.</p>}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function StopControl({ d, reload }: { d: Detail; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [close, setClose] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const action = useAction();
  async function stop() {
    const r = await action.run(() => api.post<Schemas["StopOut"]>(`/v1/resolution-cases/${d.id}/stop`, { reason: "stopped from dashboard", close }));
    if (r) {
      setOpen(false);
      setMsg(`Stopped. ${r.aborted_attempts} attempt(s) aborted, ${r.revoked_authorizations} authorization(s) revoked.`);
      reload();
    }
  }
  return (
    <Section title="Stop">
      <p className="mb-3 text-sm">Stops all activity on this case and revokes any open authorization.</p>
      <button type="button" className="btn btn-danger" onClick={() => setOpen(true)}>Stop this case</button>
      <StatusMessage message={msg} />
      <ConfirmDialog open={open} title="Stop this case?" confirmLabel="Stop case" danger busy={action.pending} onConfirm={() => void stop()} onCancel={() => setOpen(false)}>
        <p>Any attempt in progress is aborted and authorizations are revoked.</p>
        <label className="flex items-center gap-2"><input type="checkbox" className="h-5 w-5" checked={close} onChange={(e) => setClose(e.target.checked)} /> Also close the case</label>
        <ErrorBanner error={action.error} />
      </ConfirmDialog>
    </Section>
  );
}

function Events({ id }: { id: string }) {
  const res = useResource(`rc-tl-${id}`, () => api.get<TimelineRow[]>(`/v1/resolution-cases/${id}/timeline`));
  return (
    <Section title="Timeline">
      <Loadable {...res} rows={3}>
        {(rows) => {
          const items: TimelineItem[] = rows.map((r) => ({
            id: String(r.seq), at: r.created_at, title: humanize(r.kind), meta: `by ${r.actor}`,
            label: isRecord(r.data) && typeof r.data["evidence_label"] === "string" ? r.data["evidence_label"] : undefined,
            children: isRecord(r.data) && Object.keys(r.data).length > 0 ? <p className="break-words text-xs text-muted">{asText(r.data)}</p> : undefined,
          }));
          return items.length ? <Timeline items={items} ariaLabel="Resolution timeline" /> : <p className="text-sm">No events yet.</p>;
        }}
      </Loadable>
    </Section>
  );
}

export default function ResolveDetailPage() {
  const { id } = useParams<{ id: string }>();
  const res = useResource(`rc-${id}`, () => api.get<Detail>(`/v1/resolution-cases/${id}`));
  return (
    <Loadable {...res} rows={5}>
      {(d) => (
        <>
          <PageHeader title={d.claimed_entity || "Resolution case"} subtitle={d.summary || "Claimed party is unverified."} />
          <Section title="Status">
            <DefList rows={[
              ["Status", <Badge key="s" tone="brand">{humanize(d.status)}</Badge>],
              ["Claimed party status", humanize(d.claimed_entity_status)],
              ["Objective", d.objective || "Not set"],
              ["Urgency", humanize(d.urgency)],
              ["Policy region", d.policy_region || "Not set"],
            ]} />
          </Section>
          <Destinations d={d} />
          <Attempts d={d} />
          <Events id={id} />
          <ManualAttempt d={d} reload={res.reload} />
          <NextSteps d={d} />
          <StopControl d={d} reload={res.reload} />
        </>
      )}
    </Loadable>
  );
}
