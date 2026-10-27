"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Badge } from "@/components/Badge";
import { DataTable, type Column } from "@/components/DataTable";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Field, Loadable, Notice, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { Page, Schemas } from "@/lib/api/types";
import { formatDateTime, humanize } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

type ResolveCase = Schemas["callshield__api__routers__resolution_cases__CaseOut"];

const COLUMNS: Column<ResolveCase>[] = [
  { key: "entity", header: "Claimed party", render: (c) => c.claimed_entity || "Unknown" },
  { key: "status", header: "Status", render: (c) => <Badge tone="brand">{humanize(c.status)}</Badge> },
  { key: "obj", header: "Objective", render: (c) => c.objective || "Not set" },
  { key: "urg", header: "Urgency", render: (c) => humanize(c.urgency) },
  { key: "upd", header: "Updated", render: (c) => formatDateTime(c.updated_at) },
];

function NewResolveCase() {
  const router = useRouter();
  const lines = useResource("lines-for-resolve", () => api.get<Page<Schemas["LineOut"]>>("/v1/lines", { limit: 200 }));
  const [lineId, setLineId] = useState("");
  const [entity, setEntity] = useState("");
  const [summary, setSummary] = useState("");
  const action = useAction();
  async function submit(e: FormEvent) {
    e.preventDefault();
    const line = lineId || lines.data?.items[0]?.id;
    if (!line) return;
    const created = await action.run(() =>
      api.post<ResolveCase>("/v1/resolution-cases", { line_id: line, claimed_entity: entity, summary }),
    );
    if (created) router.push(`/resolve/${created.id}`);
  }
  return (
    <Section title="Open a resolution case">
      <form onSubmit={submit} className="grid max-w-xl gap-3" aria-label="Open a resolution case">
        <Field label="Protected line" htmlFor="rc-line">
          <select id="rc-line" className="input" value={lineId} onChange={(e) => setLineId(e.target.value)}>
            {(lines.data?.items ?? []).map((l) => <option key={l.id} value={l.id}>{l.label || l.e164}</option>)}
          </select>
        </Field>
        <Field label="Party that claimed to be calling" htmlFor="rc-entity" hint="This is a claim, not a verified identity.">
          <input id="rc-entity" className="input" value={entity} onChange={(e) => setEntity(e.target.value)} />
        </Field>
        <Field label="What happened" htmlFor="rc-summary"><textarea id="rc-summary" className="input" rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} /></Field>
        <ErrorBanner error={action.error ?? lines.error} />
        <div><button type="submit" className="btn btn-primary" disabled={action.pending || !lines.data?.items.length}>Open case</button></div>
      </form>
    </Section>
  );
}

export default function ResolvePage() {
  const res = useResource("resolve-cases", () => api.get<ResolveCase[]>("/v1/resolution-cases"));
  return (
    <>
      <PageHeader title="Resolve" subtitle="Track requests to stop unwanted calls: destinations, attempts you made, and what to do next." />
      <Notice>The system does not place opt-out calls for you. Outbound contact is replaced by the callback flow on a call&apos;s detail page, and by the complaint flow.</Notice>
      <div className="my-4">
        <Loadable {...res} rows={4}>
          {(cases) => (
            <DataTable caption="Resolution cases" columns={COLUMNS} rows={cases} rowKey={(c) => c.id} rowHref={(c) => `/resolve/${c.id}`} emptyTitle="No resolution cases" emptyHint="Open one below to start tracking." />
          )}
        </Loadable>
      </div>
      <NewResolveCase />
    </>
  );
}
