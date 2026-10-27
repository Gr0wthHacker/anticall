"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { EvidenceBadge } from "@/components/Badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { DefList, Loadable, Notice, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { CallbackView } from "@/lib/api/types";
import { asText, formatDateTime, humanize, isRecord } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

interface Labeled {
  value: unknown;
  label: string;
}

function toLabeled(entry: unknown, fallback: string): Labeled {
  if (isRecord(entry) && "value" in entry) {
    return { value: entry["value"], label: typeof entry["label"] === "string" ? entry["label"] : fallback };
  }
  return { value: entry, label: fallback };
}

function LabeledTable({ data, fallback, caption }: { data: Record<string, unknown>; fallback: string; caption: string }) {
  const rows = Object.entries(data);
  if (rows.length === 0) return <p className="text-sm text-muted">None recorded.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[22rem] text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="text-muted">
          <tr><th scope="col" className="py-1 pr-3 font-medium">Field</th><th scope="col" className="py-1 pr-3 font-medium">Value</th><th scope="col" className="py-1 font-medium">Source</th></tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map(([k, v]) => {
            const item = toLabeled(v, fallback);
            return (
              <tr key={k}>
                <th scope="row" className="py-2 pr-3 font-normal">{humanize(k)}</th>
                <td className="break-words py-2 pr-3">{asText(item.value) || "None"}</td>
                <td className="py-2"><EvidenceBadge label={item.label} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CancelButton({ id, onDone }: { id: string; onDone: () => void }) {
  const action = useAction();
  return (
    <div className="mb-5">
      <button
        type="button"
        className="btn btn-danger"
        disabled={action.pending}
        onClick={() => void action.run(() => api.post(`/v1/callbacks/${id}/cancel`)).then((r) => r && onDone())}
      >
        Cancel this callback
      </button>
      <ErrorBanner error={action.error} />
    </div>
  );
}

function Body({ cb, reload }: { cb: CallbackView; reload: () => void }) {
  return (
    <>
      <Section title="Request">
        <DefList
          rows={[
            ["Status", humanize(cb.status)],
            ["Number called back", `${cb.destination} (derived from the logged inbound call)`],
            ["Requested", formatDateTime(cb.created_at)],
            ["Recording", cb.outbound_recording ? "Requested for this call" : "Off"],
            ["Inbound call", <Link key="c" className="link" href={`/calls/${cb.inbound_call_id}`}>Open call</Link>],
            ["Case", cb.case_id ? <Link key="k" className="link" href={`/cases/${cb.case_id}`}>Open case</Link> : "Not attached"],
          ]}
        />
      </Section>
      {["QUEUED"].includes(cb.status) && <CancelButton id={cb.id} onDone={reload} />}
      {cb.result ? (
        <>
          <Section title="Claimed by the answering party">
            <Notice>Everything here was said by the party who answered. It is not verified.</Notice>
            <div className="mt-3"><LabeledTable data={cb.result.fields} fallback="CLAIMED" caption="Claimed fields" /></div>
          </Section>
          <Section title="Observed by the system">
            <div className="mt-1"><LabeledTable data={cb.result.metadata} fallback="OBSERVED" caption="Observed metadata" /></div>
            <p className="mt-3 text-sm text-muted">
              Outcome: {humanize(cb.result.outcome)}. Retention until {formatDateTime(cb.result.retention_expires_at)}
              {cb.result.tombstoned ? " (content already expired)" : ""}.
            </p>
          </Section>
        </>
      ) : (
        <Notice>No result yet. Fields appear here after the call ends.</Notice>
      )}
      {cb.gate_report && (
        <Section title="Gate report at queue time">
          <code className="block break-all text-xs">{JSON.stringify(cb.gate_report)}</code>
        </Section>
      )}
    </>
  );
}

export default function CallbackDetailPage() {
  const { id } = useParams<{ id: string }>();
  const res = useResource(`callback-${id}`, () => api.get<CallbackView>(`/v1/callbacks/${id}`));
  return (
    <>
      <PageHeader title="Callback detail" subtitle="Claimed and observed values are labeled separately." />
      <Loadable {...res} rows={4}>{(cb) => <Body cb={cb} reload={res.reload} />}</Loadable>
    </>
  );
}
