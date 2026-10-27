import type { GateResult } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { Badge } from "./Badge";

const GATE_TITLES: Record<string, string> = {
  INBOUND_RECORD: "1. Inbound call is logged",
  ATTESTATION_A: "2. STIR/SHAKEN attestation is A",
  WITHIN_WINDOW: "3. Within your callback window",
  USER_AUTHORIZATION: "4. You authorize this callback",
  NOT_BLOCKLISTED: "5. Number is not blocklisted",
  CAPS: "6. Attempt caps not exceeded",
  CALLING_HOURS: "7. Within calling hours (8:00 to 21:00 local)",
};

export function gateTitle(gate: string): string {
  return GATE_TITLES[gate] ?? humanize(gate);
}

export function GateList({ gates }: { gates: GateResult[] }) {
  return (
    <ol className="space-y-2" aria-label="Callback eligibility gates">
      {gates.map((g) => (
        <li key={g.gate} className="flex flex-wrap items-start gap-x-3 gap-y-1 rounded-md border border-line p-3">
          <Badge tone={g.passed ? "ok" : "bad"}>
            <span aria-hidden="true" className="mr-1">
              {g.passed ? "✔" : "✖"}
            </span>
            {g.passed ? "PASS" : "FAIL"}
          </Badge>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{gateTitle(g.gate)}</p>
            <p className="text-sm text-muted">{g.message}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}
