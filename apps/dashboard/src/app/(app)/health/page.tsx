"use client";

import { Badge, FlagBadge } from "@/components/Badge";
import { KeyValues, Loadable, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";
import { FLAG_LABELS, useFeatures } from "@/lib/features";
import { formatDateTime, humanize } from "@/lib/format";
import { useResource } from "@/lib/hooks";

export default function HealthPage() {
  const res = useResource("health", () => api.get<Schemas["TelephonyHealthOut"]>("/v1/health/telephony"));
  const { flags, version, callback } = useFeatures();
  return (
    <>
      <PageHeader title="System Health" subtitle="Telephony provider status and which features are turned on." actions={<button type="button" className="btn" onClick={res.reload}>Refresh</button>} />
      <Section title="Telephony">
        <Loadable {...res} rows={3}>
          {(h) => (
            <>
              <p className="mb-3 text-sm">Overall: <Badge tone={h.overall === "OK" ? "ok" : h.overall === "DEGRADED" ? "warn" : "neutral"}>{h.overall}</Badge></p>
              <h3 className="mb-2 text-sm font-medium">Providers reporting</h3>
              {h.providers.length === 0 ? <p className="text-sm">No provider has reported yet.</p> : (
                <ul className="space-y-2 text-sm">{h.providers.map((p, i) => (
                  <li key={i} className="rounded-md border border-line p-2"><strong>{String(p["provider"])}</strong> <Badge>{humanize(String(p["status"]))}</Badge> <span className="text-muted">last event {p["last_event_at"] ? formatDateTime(String(p["last_event_at"])) : "never"}</span></li>
                ))}</ul>
              )}
              <h3 className="mb-2 mt-4 text-sm font-medium">Adapters</h3>
              {h.adapters.length === 0 ? <p className="text-sm">No adapters loaded.</p> : h.adapters.map((a, i) => <div key={i} className="mb-2 rounded-md border border-line p-2"><KeyValues data={a} /></div>)}
            </>
          )}
        </Loadable>
      </Section>
      <Section title="Feature gates">
        <p className="mb-3 text-sm text-muted">Server version {version ?? "unknown"}.</p>
        <ul className="grid gap-2 sm:grid-cols-2">
          {Object.entries(flags).map(([k, on]) => <li key={k} className="flex items-center justify-between rounded-md border border-line p-3 text-sm"><span>{FLAG_LABELS[k] ?? k}</span><FlagBadge on={on} /></li>)}
          <li className="flex items-center justify-between rounded-md border border-line p-3 text-sm"><span>Automated callback</span><FlagBadge on={callback?.callback_enabled === true} /></li>
        </ul>
      </Section>
    </>
  );
}
