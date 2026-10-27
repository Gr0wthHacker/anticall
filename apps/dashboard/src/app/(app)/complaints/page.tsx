"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Badge, EvidenceBadge } from "@/components/Badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Field, Loadable, Notice, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import { newIdempotencyKey } from "@/lib/api/idempotency";
import type { Page, Schemas } from "@/lib/api/types";
import { asText, formatDateTime, humanize } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

type EnforceCase = Schemas["callshield__api__routers__cases__CaseOut"];
type Destination = Schemas["callshield__filing__schemas__DestinationOut"];
type Run = Schemas["AssessmentRunOut"];

const ALLOWED_STATES = ["INSUFFICIENT_INFORMATION", "POSSIBLY_RELEVANT", "REQUIRES_COUNSEL_REVIEW", "NOT_SUPPORTED_BY_CURRENT_EVIDENCE"];
const FCC_NOTE = "The FCC does not resolve individual complaints.";

function Destinations() {
  const res = useResource("filing-destinations", () => api.get<Destination[]>("/v1/filing-destinations"));
  return (
    <Section title="Where complaints can go">
      <p className="mb-3 text-sm text-muted">Capabilities are shown as documented. A destination without a verified electronic interface is a manual handoff: we prepare the text, you file it.</p>
      <Loadable {...res} rows={3}>
        {(list) => (
          <ul className="space-y-3">
            {list.map((d) => {
              const text = d.notices.map(asText);
              const needsFcc = d.agency.toUpperCase().includes("FCC") && !text.some((n) => n.includes("does not resolve"));
              return (
                <li key={d.slug} className="rounded-md border border-line p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <strong>{d.name}</strong>
                    <Badge>{d.agency}</Badge>
                    <Badge tone={d.electronic_submission_supported ? "ok" : "warn"}>{d.electronic_submission_supported ? "Electronic submission supported" : "Manual handoff only"}</Badge>
                    {!d.validated && <Badge tone="bad">Not validated</Badge>}
                  </div>
                  <p className="mt-1 text-muted">Capability: {humanize(d.capability_status)}. Last reviewed {d.last_reviewed_at ? formatDateTime(d.last_reviewed_at) : "never"}.</p>
                  {needsFcc && <p className="mt-1 font-medium">{FCC_NOTE}</p>}
                  {text.length > 0 && <ul className="mt-1 list-disc pl-5">{text.map((n) => <li key={n}>{n}</li>)}</ul>}
                  <p className="mt-1 text-muted">{d.authorization_requirements}</p>
                </li>
              );
            })}
          </ul>
        )}
      </Loadable>
    </Section>
  );
}

function RunView({ run }: { run: Run }) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">Evaluated {formatDateTime(run.evaluated_at)} with ruleset {run.ruleset_version}</p>
      {run.results.map((r) => (
        <div key={r.assessment_id} className="rounded-md border border-line p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <strong>{humanize(r.issue_type)}</strong>
            <Badge tone={r.status === "POSSIBLY_RELEVANT" ? "warn" : "neutral"}>{ALLOWED_STATES.includes(r.status) ? humanize(r.status) : "Unrecognized status"}</Badge>
          </div>
          {r.missing_requirements.length > 0 && <p className="mt-1">Missing: {r.missing_requirements.map(asText).join("; ")}</p>}
          {r.contradictions.length > 0 && <p className="mt-1">Contradictions: {r.contradictions.map(asText).join("; ")}</p>}
          {r.counsel_reasons.length > 0 && <p className="mt-1">For counsel: {r.counsel_reasons.map(asText).join("; ")}</p>}
        </div>
      ))}
      {run.ai_commentary && (
        <div className="rounded-md border border-line p-3 text-sm">
          <p className="mb-1 flex items-center gap-2"><EvidenceBadge label="MODEL_INFERRED" /> AI commentary</p>
          <p>{run.ai_commentary.text}</p>
        </div>
      )}
      <Notice>{run.disclaimer}</Notice>
    </div>
  );
}

function Assessment({ caseId }: { caseId: string }) {
  const res = useResource(`assess-${caseId}`, () => api.get<Schemas["AssessmentListOut"]>(`/v1/cases/${caseId}/assessments`));
  const run = useAction();
  async function assess() {
    const r = await run.run(() => api.post(`/v1/cases/${caseId}/assess`, { idempotency_key: newIdempotencyKey("assess") }));
    if (r) res.reload();
  }
  const latest = res.data?.runs[res.data.runs.length - 1] ?? res.data?.runs[0];
  return (
    <Section title="Assessment">
      <Notice tone="warn">Assessments are not legal advice and are not findings that any law was violated. The only possible outcomes are the four states below.</Notice>
      <ul className="my-3 flex flex-wrap gap-2" aria-label="Possible assessment states">{ALLOWED_STATES.map((s) => <li key={s}><Badge>{humanize(s)}</Badge></li>)}</ul>
      <button type="button" className="btn" disabled={run.pending} onClick={() => void assess()}>Run assessment</button>
      <ErrorBanner error={run.error} />
      <div className="mt-3">
        <Loadable {...res} rows={2}>{() => (latest ? <RunView run={latest} /> : <p className="text-sm">No assessment has been run for this case.</p>)}</Loadable>
      </div>
    </Section>
  );
}

function NewDraft({ caseId }: { caseId: string }) {
  const router = useRouter();
  const dests = useResource("dest-select", () => api.get<Destination[]>("/v1/filing-destinations"));
  const [slug, setSlug] = useState("");
  const action = useAction();
  async function submit(e: FormEvent) {
    e.preventDefault();
    const destination = slug || dests.data?.find((d) => d.active)?.slug;
    if (!destination) return;
    const draft = await action.run(() =>
      api.post<Schemas["DraftOut"]>(`/v1/cases/${caseId}/filings/drafts`, { destination, idempotency_key: newIdempotencyKey("draft") }),
    );
    if (draft) router.push(`/complaints/drafts/${draft.draft_id}`);
  }
  const detail = action.raw as { details?: { manual_handoff?: unknown } } | null;
  return (
    <Section title="Create a draft">
      <p className="mb-3 text-sm text-muted">A draft shows the exact words and attachments. Nothing is sent until you approve and submit.</p>
      <form onSubmit={submit} className="grid max-w-xl gap-3" aria-label="Create filing draft">
        <Field label="Recipient" htmlFor="draft-dest">
          <select id="draft-dest" className="input" value={slug} onChange={(e) => setSlug(e.target.value)}>
            {(dests.data ?? []).filter((d) => d.active).map((d) => <option key={d.slug} value={d.slug}>{d.name} ({d.agency})</option>)}
          </select>
        </Field>
        <ErrorBanner error={action.error ?? dests.error} />
        {detail?.details?.manual_handoff ? <Notice tone="warn">This recipient has no validated template. Use the official site listed above.</Notice> : null}
        <div><button type="submit" className="btn btn-primary" disabled={action.pending}>Generate draft</button></div>
      </form>
    </Section>
  );
}

function OpenDraft() {
  const router = useRouter();
  const [id, setId] = useState("");
  return (
    <Section title="Open an existing draft or filing">
      <form onSubmit={(e) => { e.preventDefault(); if (id.trim()) router.push(`/complaints/drafts/${id.trim()}`); }} className="flex max-w-xl flex-wrap items-end gap-2" aria-label="Open draft">
        <div className="min-w-0 flex-1"><Field label="Draft ID" htmlFor="open-draft"><input id="open-draft" className="input" value={id} onChange={(e) => setId(e.target.value)} /></Field></div>
        <button type="submit" className="btn">Open draft</button>
      </form>
    </Section>
  );
}

function CaseWorkspace() {
  const params = useSearchParams();
  const [caseId, setCaseId] = useState(params.get("case") ?? "");
  const cases = useResource("complaint-cases", () => api.get<Page<EnforceCase>>("/v1/cases", { limit: 200 }));
  return (
    <>
      <Section title="Choose a case">
        <Loadable {...cases} rows={1}>
          {(page) => (
            <Field label="Case" htmlFor="cp-case">
              <select id="cp-case" className="input max-w-xl" value={caseId} onChange={(e) => setCaseId(e.target.value)}>
                <option value="">Choose a case</option>
                {page.items.map((c) => <option key={c.id} value={c.id}>{c.title} ({humanize(c.state)})</option>)}
              </select>
            </Field>
          )}
        </Loadable>
      </Section>
      {caseId && <Assessment caseId={caseId} />}
      {caseId && <NewDraft caseId={caseId} />}
    </>
  );
}

export default function ComplaintsPage() {
  return (
    <>
      <PageHeader title="Complaints" subtitle="Assess, draft, review the exact words, approve and file. Each step is yours." />
      <Suspense fallback={<p className="text-sm">Loading…</p>}><CaseWorkspace /></Suspense>
      <OpenDraft />
      <Destinations />
    </>
  );
}
