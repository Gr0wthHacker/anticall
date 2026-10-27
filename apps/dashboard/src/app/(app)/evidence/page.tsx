"use client";

import { useState, type FormEvent } from "react";
import { Badge, EvidenceBadge } from "@/components/Badge";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorBanner } from "@/components/ErrorBanner";
import { DefList, Field, KeyValues, Loadable, Notice, PageHeader, Section, StatusMessage } from "@/components/ui";
import { api, requestBlob } from "@/lib/api/client";
import type { CaseTimeline, EvidenceArtifact, Page, Schemas } from "@/lib/api/types";
import { formatBytes, formatDateTime, humanize } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

type EnforceCase = Schemas["callshield__api__routers__cases__CaseOut"];

interface ExportView { id: string; status: string; downloadable: boolean; zip_sha256: string | null; expires_at: string | null }

function ArtifactActions({ a, reload }: { a: EvidenceArtifact; reload: () => void }) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const redact = useAction();
  const del = useAction();
  async function doRedact() {
    const r = await redact.run(() => api.post(`/v1/cases/${a.case_id}/evidence/${a.id}/redact`, { redact_phone_numbers: true }));
    if (r) { setMsg("Redacted copy created. The original is unchanged."); reload(); }
  }
  async function doDelete() {
    const r = await del.run(() => api.post(`/v1/evidence/${a.id}/deletion-requests`, { reason: "requested from dashboard" }));
    if (r) { setConfirmDelete(false); setMsg("Deletion request recorded. A legal hold may delay it."); reload(); }
  }
  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {a.case_id && <button type="button" className="btn" disabled={redact.pending} onClick={() => void doRedact()}>Redact phone numbers (new copy)</button>}
      <button type="button" className="btn btn-danger" onClick={() => setConfirmDelete(true)}>Request deletion</button>
      <ErrorBanner error={redact.error} />
      <StatusMessage message={msg} />
      <ConfirmDialog open={confirmDelete} title="Request deletion" confirmLabel="Request deletion" danger busy={del.pending} onConfirm={() => void doDelete()} onCancel={() => setConfirmDelete(false)}>
        <p>This asks for the artifact to be deleted. Items under a legal hold or retention rule may be kept, and the request is logged.</p>
        <ErrorBanner error={del.error} />
      </ConfirmDialog>
    </div>
  );
}

function ArtifactView({ id }: { id: string }) {
  const res = useResource(`artifact-${id}`, () => api.get<EvidenceArtifact>(`/v1/evidence/${id}`, { verify: true }));
  return (
    <Loadable {...res} rows={3}>
      {(a) => (
        <Section title={`Artifact ${humanize(a.source_type)}`}>
          <p className="mb-3 flex flex-wrap items-center gap-2"><EvidenceBadge label={a.evidence_label} /><Badge>{humanize(a.origin)}</Badge>{a.legal_hold && <Badge tone="warn">Legal hold</Badge>}{a.deleted_at && <Badge tone="bad">Deleted</Badge>}</p>
          <DefList rows={[
            ["ID", <code key="i" className="text-xs">{a.id}</code>],
            ["Collected", formatDateTime(a.collected_at)],
            ["Collection method", humanize(a.collection_method)],
            ["Size", formatBytes(a.byte_length)],
            ["SHA-256", <code key="h" className="break-all text-xs">{a.sha256}</code>],
            ["Retention expires", a.expires_at ? formatDateTime(a.expires_at) : "No expiry set"],
            ["Processing authorization", humanize(a.processing_authorization)],
            ["Sharing authorization", humanize(a.sharing_authorization)],
            ["Redaction", humanize(a.redaction_state)],
            ["Version", String(a.version)],
            ["Derived from", a.derived_from_id ?? "Original"],
          ]} />
          <h3 className="mb-2 mt-4 text-sm font-medium">Provenance</h3>
          <KeyValues data={a.provenance} />
          <ArtifactActions a={a} reload={res.reload} />
        </Section>
      )}
    </Loadable>
  );
}

function CaseArtifacts({ caseId, onOpen }: { caseId: string; onOpen: (id: string) => void }) {
  const res = useResource(`ev-tl-${caseId}`, () => api.get<CaseTimeline>(`/v1/cases/${caseId}/timeline`));
  return (
    <Loadable {...res} rows={2}>
      {(tl) => {
        const ids = new Map<string, string>();
        for (const e of tl.entries) for (const [k, v] of Object.entries(e.source_ids)) if (k.includes("artifact")) ids.set(v, e.source_label);
        if (ids.size === 0) return <p className="text-sm">No evidence artifacts are referenced on this case timeline yet.</p>;
        return (
          <ul className="space-y-2">
            {[...ids].map(([id, label]) => (
              <li key={id} className="flex flex-wrap items-center gap-2 text-sm">
                <EvidenceBadge label={label} /><code className="text-xs">{id}</code>
                <button type="button" className="btn" onClick={() => onOpen(id)}>Open</button>
              </li>
            ))}
          </ul>
        );
      }}
    </Loadable>
  );
}

function ExportControl({ caseId }: { caseId: string }) {
  const [exp, setExp] = useState<ExportView | null>(null);
  const [mask, setMask] = useState(true);
  const action = useAction();
  async function create() {
    const r = await action.run(() => api.post<ExportView>(`/v1/cases/${caseId}/exports`, { mask_numbers: mask }));
    if (r) setExp(r);
  }
  async function refresh() {
    if (!exp) return;
    const r = await action.run(() => api.get<ExportView>(`/v1/cases/${caseId}/exports/${exp.id}`));
    if (r) setExp(r);
  }
  async function download() {
    if (!exp) return;
    const blob = await action.run(() => requestBlob(`/v1/cases/${caseId}/exports/${exp.id}/download`));
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `case-${caseId}-export.zip`;
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <Section title="Export this case">
      <p className="mb-3 text-sm text-muted">The package has the timeline, source labels, manifest and hashes. Anything left out is listed as an explicit omission.</p>
      <label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" className="h-5 w-5" checked={mask} onChange={(e) => setMask(e.target.checked)} /> Mask phone numbers</label>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn btn-primary" disabled={action.pending} onClick={() => void create()}>Create export</button>
        {exp && <button type="button" className="btn" onClick={() => void refresh()}>Check status</button>}
        {exp?.downloadable && <button type="button" className="btn" onClick={() => void download()}>Download zip</button>}
      </div>
      {exp && <p role="status" className="mt-2 text-sm">Export status: {humanize(exp.status)}{exp.zip_sha256 ? `. SHA-256 ${exp.zip_sha256}` : ""}</p>}
      <ErrorBanner error={action.error} />
    </Section>
  );
}

function CaseBrowser({ onOpen }: { onOpen: (id: string) => void }) {
  const cases = useResource("ev-cases", () => api.get<Page<EnforceCase>>("/v1/cases", { limit: 200 }));
  const [caseId, setCaseId] = useState("");
  return (
    <Section title="Browse by case">
      <Loadable {...cases} rows={1}>
        {(page) => page.items.length === 0 ? <p className="text-sm">No cases yet.</p> : (
          <>
            <Field label="Case" htmlFor="ev-case">
              <select id="ev-case" className="input max-w-xl" value={caseId} onChange={(e) => setCaseId(e.target.value)}>
                <option value="">Choose a case</option>
                {page.items.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
              </select>
            </Field>
            {caseId && <div className="mt-3"><CaseArtifacts caseId={caseId} onOpen={onOpen} /></div>}
            {caseId && <div className="mt-4"><ExportControl caseId={caseId} /></div>}
          </>
        )}
      </Loadable>
    </Section>
  );
}

export default function EvidencePage() {
  const [openId, setOpenId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  function open(e: FormEvent) {
    e.preventDefault();
    setOpenId(input.trim() || null);
  }
  return (
    <>
      <PageHeader title="Evidence" subtitle="Artifacts keep their original, with provenance, retention and a label for what kind of evidence they are. Corrections are new versions." />
      <Notice>There is no all-artifacts list in the API yet, so artifacts are reached from a case timeline or by ID.</Notice>
      <div className="mt-4"><CaseBrowser onOpen={setOpenId} /></div>
      <Section title="Open an artifact by ID">
        <form onSubmit={open} className="flex max-w-xl flex-wrap items-end gap-2" aria-label="Open artifact">
          <div className="min-w-0 flex-1"><Field label="Artifact ID" htmlFor="art-id"><input id="art-id" className="input" value={input} onChange={(e) => setInput(e.target.value)} /></Field></div>
          <button type="submit" className="btn">Open</button>
        </form>
      </Section>
      {openId && <ArtifactView id={openId} />}
    </>
  );
}
