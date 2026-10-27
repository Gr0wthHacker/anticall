"use client";

import { useParams } from "next/navigation";
import { Badge, EvidenceBadge } from "@/components/Badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { DefList, Loadable, Notice, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";
import { formatDateTime, humanize } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

type Filing = Schemas["FilingOut"];

function stateTone(state: string): "ok" | "warn" | "bad" | "neutral" {
  if (state === "SUBMITTED" || state === "AGENCY_ACKNOWLEDGED") return "ok";
  if (state === "FAILED") return "bad";
  return state === "UNKNOWN" ? "warn" : "neutral";
}

function Receipts({ filing }: { filing: Filing }) {
  return (
    <Section title="Receipts">
      <Notice>{filing.receipt_disclaimer}</Notice>
      {filing.receipts.length === 0 ? <p className="mt-3 text-sm">No receipt on file. None is ever created by the dashboard.</p> : (
        <ul className="mt-3 space-y-3">
          {filing.receipts.map((r) => (
            <li key={r.receipt_id} className="rounded-md border border-line p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <EvidenceBadge label={r.evidence_label} />
                <Badge>{humanize(r.receipt_kind)}</Badge>
                <Badge tone={r.is_agency_acknowledgment ? "ok" : "neutral"}>{r.is_agency_acknowledgment ? "Agency acknowledgment" : "Not an agency acknowledgment"}</Badge>
              </div>
              <DefList rows={[
                ["Confirmation number", r.confirmation_number ?? "None given"],
                ["Source of confirmation", humanize(r.confirmation_source)],
                ["Endpoint", r.endpoint || "Not recorded"],
                ["Submitted", formatDateTime(r.submitted_at)],
                ["Payload digest", <code key="d" className="break-all text-xs">{r.payload_digest}</code>],
              ]} />
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export default function FilingPage() {
  const { id } = useParams<{ id: string }>();
  const res = useResource(`filing-${id}`, () => api.get<Filing>(`/v1/filings/${id}`));
  const check = useAction();
  async function statusCheck() {
    const r = await check.run(() => api.post<Filing>(`/v1/filings/${id}/status-check`));
    if (r) res.reload();
  }
  return (
    <Loadable {...res} rows={4}>
      {(f) => (
        <>
          <PageHeader title="Filing" subtitle={`${f.agency} via ${humanize(f.adapter)}`} />
          <Section title="Status">
            <p className="mb-3 flex items-center gap-2 text-sm"><Badge tone={stateTone(f.state)}>{f.state}</Badge></p>
            {f.state === "UNKNOWN" && (
              <Notice tone="warn">We could not confirm whether the agency received this. It was NOT sent again and will not be sent again automatically. Check the status below or on the agency&apos;s site before doing anything else.</Notice>
            )}
            <DefList rows={[
              ["Next step", f.next_step || "None"],
              ["Sent", f.sent_at ? formatDateTime(f.sent_at) : "Not sent by this system"],
              ["Failure code", f.failure_code ?? "None"],
              ["Payload digest", <code key="d" className="break-all text-xs">{f.payload_digest}</code>],
            ]} />
            <div className="mt-3">
              <button type="button" className="btn" disabled={check.pending} onClick={() => void statusCheck()}>Check status</button>
              <ErrorBanner error={check.error} />
            </div>
          </Section>
          <Receipts filing={f} />
        </>
      )}
    </Loadable>
  );
}
