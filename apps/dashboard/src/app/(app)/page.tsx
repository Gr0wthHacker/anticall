"use client";

import Link from "next/link";
import { Badge, FlagBadge } from "@/components/Badge";
import { Loadable, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { Page, Schemas } from "@/lib/api/types";
import { FLAG_LABELS, useFeatures } from "@/lib/features";
import { useResource } from "@/lib/hooks";

type CallSummary = Schemas["CallSummary"];
type ResolveCase = Schemas["callshield__api__routers__resolution_cases__CaseOut"];
type EnforceCase = Schemas["callshield__api__routers__cases__CaseOut"];

interface Stats {
  incoming: number;
  screened: number;
  human: number;
  unresolvedResolve: number;
  unresolvedEnforce: number;
  possibleRepeat: number;
  telephony: Schemas["TelephonyHealthOut"];
}

const CLOSED_RESOLVE = new Set(["CLOSED", "NOT_RELATED"]);

async function callTotal(filters: Record<string, string>): Promise<number> {
  const page = await api.get<Page<CallSummary>>("/v1/calls", { ...filters, limit: 1 });
  return page.total;
}

async function loadStats(): Promise<Stats> {
  const [incoming, screened, human, resolve, enforce, telephony] = await Promise.all([
    callTotal({ direction: "INBOUND" }),
    callTotal({ disposition: "SCREENED" }),
    callTotal({ disposition: "TRANSFERRED" }),
    api.get<ResolveCase[]>("/v1/resolution-cases"),
    api.get<Page<EnforceCase>>("/v1/cases", { limit: 200 }),
    api.get<Schemas["TelephonyHealthOut"]>("/v1/health/telephony"),
  ]);
  return {
    incoming,
    screened,
    human,
    unresolvedResolve: resolve.filter((c) => !CLOSED_RESOLVE.has(c.status)).length,
    unresolvedEnforce: enforce.items.filter((c) => c.state !== "CLOSED").length,
    possibleRepeat: resolve.filter((c) => c.status === "POSSIBLE_RECONTACT").length,
    telephony,
  };
}

function Stat({ label, value, note }: { label: string; value: number; note?: string }) {
  return (
    <div className="card">
      <p className="text-sm text-muted">{label}</p>
      <p className="text-3xl font-semibold">{value}</p>
      {note && <p className="mt-1 text-xs text-muted">{note}</p>}
    </div>
  );
}

function CapabilityList() {
  const { flags, loaded, callback } = useFeatures();
  if (!loaded) return <p className="text-sm text-muted">Loading feature flags…</p>;
  const rows = Object.entries(flags);
  return (
    <ul className="grid gap-2 sm:grid-cols-2">
      {rows.map(([key, on]) => (
        <li key={key} className="flex items-center justify-between rounded-md border border-line p-3 text-sm">
          <span>{FLAG_LABELS[key] ?? key}</span>
          <FlagBadge on={on} />
        </li>
      ))}
      <li className="flex items-center justify-between rounded-md border border-line p-3 text-sm">
        <span>Automated callback</span>
        <FlagBadge on={callback?.callback_enabled === true} />
      </li>
    </ul>
  );
}

function telephonyTone(overall: string): "ok" | "warn" | "neutral" {
  if (overall === "OK") return "ok";
  return overall === "DEGRADED" ? "warn" : "neutral";
}

export default function OverviewPage() {
  const res = useResource("overview", loadStats);
  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="Counts come straight from your account. Nothing here is estimated."
      />
      <Loadable {...res} rows={4}>
        {(s) => (
          <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <Stat label="Incoming calls" value={s.incoming} />
            <Stat label="Screened" value={s.screened} />
            <Stat label="Routed to a human" value={s.human} />
            <Stat
              label="Unresolved cases"
              value={s.unresolvedResolve + s.unresolvedEnforce}
              note={`${s.unresolvedResolve} in Resolve, ${s.unresolvedEnforce} in Cases`}
            />
            <Stat label="Possible repeat contact" value={s.possibleRepeat} note="Needs your review. Not a confirmed repeat." />
            <div className="card">
              <p className="text-sm text-muted">Telephony status</p>
              <p className="mt-1">
                <Badge tone={telephonyTone(s.telephony.overall)}>{s.telephony.overall}</Badge>
              </p>
              <Link href="/health" className="link mt-2 inline-block text-sm">
                System health details
              </Link>
            </div>
          </div>
        )}
      </Loadable>
      <Section title="Feature gates">
        <p className="mb-3 text-sm text-muted">
          Risky features ship OFF. A feature marked OFF is disabled on the server and cannot be used from this dashboard.
        </p>
        <CapabilityList />
      </Section>
    </>
  );
}
