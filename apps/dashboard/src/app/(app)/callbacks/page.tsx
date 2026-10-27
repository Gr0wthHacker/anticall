"use client";

import Link from "next/link";
import { Badge } from "@/components/Badge";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { Loadable, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { CallbackView } from "@/lib/api/types";
import { useFeatures } from "@/lib/features";
import { formatDateTime, humanize } from "@/lib/format";
import { useResource } from "@/lib/hooks";

const COLUMNS: Column<CallbackView>[] = [
  { key: "when", header: "Requested", render: (c) => formatDateTime(c.created_at) },
  { key: "dest", header: "Number called back", render: (c) => c.destination },
  { key: "status", header: "Status", render: (c) => <Badge>{humanize(c.status)}</Badge> },
  { key: "outcome", header: "Outcome", render: (c) => (c.result ? humanize(c.result.outcome) : "Pending") },
  { key: "rec", header: "Recorded", render: (c) => (c.outbound_recording ? "Yes" : "No") },
];

function CallbackList() {
  const res = useResource("callbacks", () => api.get<{ items: CallbackView[] }>("/v1/callbacks"));
  return (
    <Loadable {...res} rows={4}>
      {(d) => (
        <DataTable
          caption="Callbacks"
          columns={COLUMNS}
          rows={d.items}
          rowKey={(c) => c.id}
          rowHref={(c) => `/callbacks/${c.id}`}
          emptyTitle="No callbacks yet"
          emptyHint="Request one from the detail page of an inbound call that passes every gate."
        />
      )}
    </Loadable>
  );
}

export default function CallbacksPage() {
  const { callback, loaded } = useFeatures();
  const off = loaded && callback?.callback_enabled !== true;
  return (
    <>
      <PageHeader
        title="Callbacks"
        subtitle="An automated assistant returns a recent call to the number that placed it, discloses itself, and asks who is behind the call."
        actions={<Link className="btn" href="/callbacks/settings">Callback settings</Link>}
      />
      {off ? (
        <EmptyState
          title="Automated callbacks are OFF on this server"
          hint="The feature ships disabled until counsel release gates are lifted. No callback can be requested."
        />
      ) : (
        <Section title="Your callbacks"><CallbackList /></Section>
      )}
    </>
  );
}
