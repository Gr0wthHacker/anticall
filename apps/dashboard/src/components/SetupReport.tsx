import { Badge, type Tone } from "@/components/Badge";
import type { Schemas } from "@/lib/api/types";

type Doctor = Schemas["DoctorOut"];

const STATUS_TONE: Record<string, Tone> = { PASS: "ok", WARN: "warn", FAIL: "bad", SKIP: "neutral" };
const DOCS_BASE = (process.env.NEXT_PUBLIC_DOCS_BASE_URL ?? "").replace(/\/+$/, "");

export function DocLink({ path }: { path: string }) {
  if (!DOCS_BASE) return <code className="text-xs">{path}</code>;
  return (
    <a className="underline" href={`${DOCS_BASE}/${path}`} rel="noreferrer">
      <code className="text-xs">{path}</code>
    </a>
  );
}

export function DoctorChecks({ checks }: { checks: Doctor["checks"] }) {
  return (
    <ul className="space-y-2">
      {checks.map((c) => (
        <li key={c.id} className="rounded-md border border-line p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{c.status}</Badge>
            <strong>{c.title}</strong>
          </div>
          {c.detail ? <p className="mt-1 text-muted">{c.detail}</p> : null}
          {c.fix && (c.status === "WARN" || c.status === "FAIL") ? (
            <p className="mt-1">
              <span className="font-medium">Fix: </span>
              {c.fix}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function GatedFeatures({ features }: { features: Doctor["gated_features"] }) {
  const on = features.filter((f) => f.enabled).length;
  return (
    <>
      <p className="mb-3 text-sm" role="status">
        {on === 0 ? "All gated features are off." : `${on} gated feature(s) are ON.`}
      </p>
      <ul className="space-y-2">
        {features.map((f) => (
          <li key={f.key} className="rounded-md border border-line p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={f.enabled ? "warn" : "ok"}>{f.enabled ? "ON" : "off"}</Badge>
              <strong>{f.title}</strong>
              <code className="text-xs text-muted">{f.env_var}</code>
            </div>
            <p className="mt-1 text-muted">{f.warning}</p>
            <p className="mt-1 text-xs text-muted">Related gates: {f.gates}</p>
          </li>
        ))}
      </ul>
    </>
  );
}

export function DocsList({ docs }: { docs: Doctor["docs"] }) {
  return (
    <ul className="space-y-1 text-sm">
      {Object.entries(docs).map(([name, path]) => (
        <li key={name}>
          {name.replace(/_/g, " ")}: <DocLink path={path} />
        </li>
      ))}
    </ul>
  );
}
