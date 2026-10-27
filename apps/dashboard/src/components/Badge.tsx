import type { ReactNode } from "react";
import { humanize } from "@/lib/format";

export type Tone = "neutral" | "ok" | "warn" | "bad" | "brand";

const TONE_CLASS: Record<Tone, string> = {
  neutral: "border-line text-muted",
  ok: "border-ok/50 bg-ok/10 text-ok",
  warn: "border-warn/50 bg-warn/10 text-warn",
  bad: "border-bad/50 bg-bad/10 text-bad",
  brand: "border-brand/50 bg-brand/10 text-brand",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-semibold ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  );
}

const EVIDENCE: Record<string, { tone: Tone; hint: string; mark: string }> = {
  OBSERVED: { tone: "ok", hint: "Recorded by the system from carrier or call data", mark: "●" },
  CLAIMED: { tone: "warn", hint: "Stated by the other party; unverified", mark: "◐" },
  MODEL_INFERRED: { tone: "brand", hint: "A model's conclusion; may be wrong", mark: "◆" },
  USER_ATTESTED: { tone: "neutral", hint: "Stated by you; not independently checked", mark: "■" },
  EXTERNALLY_VERIFIED: { tone: "ok", hint: "Confirmed by an outside source", mark: "✔" },
};

/** Evidence label badge. Meaning is carried by the text and a symbol, never color alone. */
export function EvidenceBadge({ label }: { label: string | null | undefined }) {
  const key = (label ?? "").toUpperCase();
  const meta = EVIDENCE[key];
  if (!meta) return <Badge>{label ? humanize(label) : "Unlabeled"}</Badge>;
  return (
    <span title={meta.hint}>
      <Badge tone={meta.tone}>
        <span aria-hidden="true" className="mr-1">
          {meta.mark}
        </span>
        {key}
        <span className="sr-only">. {meta.hint}</span>
      </Badge>
    </span>
  );
}

export function FlagBadge({ on, onText = "ON", offText = "OFF" }: {
  on: boolean;
  onText?: string;
  offText?: string;
}) {
  return <Badge tone={on ? "ok" : "neutral"}>{on ? onText : offText}</Badge>;
}
