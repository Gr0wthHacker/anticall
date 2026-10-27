"use client";

import { useState, type FormEvent } from "react";
import { Badge } from "@/components/Badge";
import { DataTable, type Column } from "@/components/DataTable";
import { Loadable, PageHeader } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { Page, Schemas } from "@/lib/api/types";
import { CLASS_LABELS } from "@/lib/constants";
import { formatDateTime, humanize } from "@/lib/format";
import { useResource } from "@/lib/hooks";

type CallSummary = Schemas["CallSummary"];

const DISPOSITIONS = [
  "RECEIVED", "ANSWERED_BY_USER", "SCREENED", "TRANSFERRED", "VOICEMAIL", "REJECTED", "MISSED",
  "NO_ANSWER", "TIMED_OUT", "FALLBACK_ROUTED", "FAILED", "UNKNOWN",
];
const PAGE_SIZE = 25;

interface Filters {
  direction: string;
  disposition: string;
  class: string;
  claimed: string;
  date_from: string;
  date_to: string;
}
const EMPTY: Filters = { direction: "", disposition: "", class: "", claimed: "", date_from: "", date_to: "" };

function toIso(date: string, endOfDay: boolean): string | undefined {
  if (!date) return undefined;
  return new Date(`${date}T${endOfDay ? "23:59:59" : "00:00:00"}`).toISOString();
}

const COLUMNS: Column<CallSummary>[] = [
  { key: "when", header: "Started", render: (c) => formatDateTime(c.started_at) },
  { key: "from", header: "Displayed caller ID", render: (c) => c.displayed_caller_id ?? "None shown" },
  { key: "dir", header: "Direction", render: (c) => humanize(c.direction) },
  { key: "disp", header: "Disposition", render: (c) => <Badge>{humanize(c.disposition)}</Badge> },
  {
    key: "label",
    header: "Current classification",
    render: (c) => (c.label ? humanize(c.label) : "Unclassified"),
  },
];

function Select({ id, label, value, options, onChange }: {
  id: string; label: string; value: string; options: string[]; onChange: (v: string) => void;
}) {
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      <select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Any</option>
        {options.map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
      </select>
    </div>
  );
}

function FilterForm({ onApply }: { onApply: (f: Filters) => void }) {
  const [f, setF] = useState<Filters>(EMPTY);
  const set = (k: keyof Filters) => (v: string) => setF((prev) => ({ ...prev, [k]: v }));
  function submit(e: FormEvent) {
    e.preventDefault();
    onApply(f);
  }
  return (
    <form onSubmit={submit} className="card mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Filter calls">
      <Select id="f-dir" label="Direction" value={f.direction} options={["INBOUND", "OUTBOUND"]} onChange={set("direction")} />
      <Select id="f-disp" label="Disposition" value={f.disposition} options={DISPOSITIONS} onChange={set("disposition")} />
      <Select id="f-class" label="Classification" value={f.class} options={CLASS_LABELS} onChange={set("class")} />
      <div>
        <label htmlFor="f-claimed" className="label">Claimed identity</label>
        <input id="f-claimed" className="input" value={f.claimed} onChange={(e) => set("claimed")(e.target.value)} />
      </div>
      <div>
        <label htmlFor="f-from" className="label">From date</label>
        <input id="f-from" type="date" className="input" value={f.date_from} onChange={(e) => set("date_from")(e.target.value)} />
      </div>
      <div>
        <label htmlFor="f-to" className="label">To date</label>
        <input id="f-to" type="date" className="input" value={f.date_to} onChange={(e) => set("date_to")(e.target.value)} />
      </div>
      <div className="flex gap-2 sm:col-span-2 lg:col-span-3">
        <button type="submit" className="btn btn-primary">Apply filters</button>
        <button type="button" className="btn" onClick={() => { setF(EMPTY); onApply(EMPTY); }}>Clear</button>
      </div>
    </form>
  );
}

export default function CallsPage() {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [offset, setOffset] = useState(0);
  const key = `calls-${JSON.stringify(filters)}-${offset}`;
  const res = useResource(key, () =>
    api.get<Page<CallSummary>>("/v1/calls", {
      direction: filters.direction,
      disposition: filters.disposition,
      class: filters.class,
      claimed: filters.claimed,
      date_from: toIso(filters.date_from, false),
      date_to: toIso(filters.date_to, true),
      limit: PAGE_SIZE,
      offset,
    }),
  );
  return (
    <>
      <PageHeader title="Calls" subtitle="Displayed caller ID is what the network showed. It does not prove who called." />
      <FilterForm onApply={(f) => { setOffset(0); setFilters(f); }} />
      <div aria-live="polite">
        <Loadable {...res} rows={5}>
          {(page) => (
            <>
              <p className="mb-2 text-sm text-muted">{page.total} call{page.total === 1 ? "" : "s"} match.</p>
              <DataTable
                caption="Calls"
                columns={COLUMNS}
                rows={page.items}
                rowKey={(c) => c.id}
                rowHref={(c) => `/calls/${c.id}`}
                emptyTitle="No calls match"
                emptyHint="Calls appear here after your protected line receives them."
              />
              <div className="mt-3 flex items-center gap-2">
                <button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
                <button type="button" className="btn" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
              </div>
            </>
          )}
        </Loadable>
      </div>
    </>
  );
}
