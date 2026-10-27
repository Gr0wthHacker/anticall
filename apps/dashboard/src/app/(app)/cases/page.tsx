"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Badge } from "@/components/Badge";
import { DataTable, type Column } from "@/components/DataTable";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Field, Loadable, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { Page, Schemas } from "@/lib/api/types";
import { formatDateTime, humanize } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

type EnforceCase = Schemas["callshield__api__routers__cases__CaseOut"];

const COLUMNS: Column<EnforceCase>[] = [
  { key: "title", header: "Case", render: (c) => c.title },
  { key: "state", header: "State", render: (c) => <Badge tone="brand">{humanize(c.state)}</Badge> },
  { key: "attr", header: "Attribution", render: (c) => humanize(c.attribution_status) },
  { key: "opened", header: "Opened", render: (c) => formatDateTime(c.opened_at) },
  { key: "upd", header: "Updated", render: (c) => formatDateTime(c.updated_at) },
];

function NewCaseForm() {
  const router = useRouter();
  const lines = useResource("lines-for-case", () => api.get<Page<Schemas["LineOut"]>>("/v1/lines", { limit: 200 }));
  const [lineId, setLineId] = useState("");
  const [title, setTitle] = useState("");
  const action = useAction();
  async function submit(e: FormEvent) {
    e.preventDefault();
    const line = lineId || lines.data?.items[0]?.id;
    if (!line) return;
    const created = await action.run(() =>
      api.post<EnforceCase>("/v1/cases", { protected_line_id: line, title }),
    );
    if (created) router.push(`/cases/${created.id}`);
  }
  return (
    <Section title="Start a complaint case" id="new-case">
      <p className="mb-3 text-sm text-muted">A case collects evidence and calls for one complaint. Nothing is sent anywhere until you approve a draft.</p>
      <form onSubmit={submit} className="grid max-w-xl gap-3" aria-label="Start a case">
        <Field label="Protected line" htmlFor="case-line">
          <select id="case-line" className="input" value={lineId} onChange={(e) => setLineId(e.target.value)} required>
            {(lines.data?.items ?? []).map((l) => <option key={l.id} value={l.id}>{l.label || l.e164}</option>)}
          </select>
        </Field>
        <Field label="Case title" htmlFor="case-title">
          <input id="case-title" className="input" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <ErrorBanner error={action.error ?? lines.error} />
        <div><button type="submit" className="btn btn-primary" disabled={action.pending || !lines.data?.items.length}>Create case</button></div>
        {lines.data && lines.data.items.length === 0 && <p className="text-sm text-muted">Add a line in Settings first.</p>}
      </form>
    </Section>
  );
}

export default function CasesPage() {
  const res = useResource("cases", () => api.get<Page<EnforceCase>>("/v1/cases", { limit: 100 }));
  return (
    <>
      <PageHeader title="Cases" subtitle="Evidence cases for complaints. Each fact carries a source label." />
      <Loadable {...res} rows={4}>
        {(page) => (
          <div className="mb-5">
            <DataTable
              caption="Cases"
              columns={COLUMNS}
              rows={page.items}
              rowKey={(c) => c.id}
              rowHref={(c) => `/cases/${c.id}`}
              emptyTitle="No cases yet"
              emptyHint="Start one below, or from a call that cannot be called back."
            />
          </div>
        )}
      </Loadable>
      <NewCaseForm />
    </>
  );
}
