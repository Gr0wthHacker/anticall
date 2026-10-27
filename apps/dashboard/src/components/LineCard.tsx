"use client";

import { useState, type FormEvent } from "react";
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";
import { formatDateTime, humanize } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";
import { Badge } from "./Badge";
import { ErrorBanner } from "./ErrorBanner";
import { DefList, Field, Loadable, Notice, StatusMessage } from "./ui";

type Line = Schemas["LineOut"];
const MODES: Schemas["ProcessingMode"][] = ["METADATA_ONLY", "LIVE_STT_TRANSIENT", "TRANSCRIPT_RETAINED", "RECORDING_RETAINED"];

interface Props {
  line: Line;
  acknowledged: boolean;
  callbackEnabled: boolean;
  onChanged: () => void;
}

function Screening({ line, acknowledged, onChanged }: Pick<Props, "line" | "acknowledged" | "onChanged">) {
  const action = useAction();
  const [msg, setMsg] = useState<string | null>(null);
  async function toggle(enable: boolean) {
    const r = await action.run(() => api.post<Schemas["ScreeningToggleResponse"]>(`/v1/lines/${line.id}/screening:${enable ? "enable" : "disable"}`));
    if (r) { setMsg(`Screening ${r.screening_enabled ? "on" : "off"}. Calls now: ${humanize(r.effective_routing)}.`); onChanged(); }
  }
  return (
    <div>
      <p className="mb-2 text-sm">Screening is <strong>{line.screening_enabled ? "ON" : "OFF"}</strong>.</p>
      {line.screening_enabled ? (
        <button type="button" className="btn" disabled={action.pending} onClick={() => void toggle(false)}>Turn screening off</button>
      ) : (
        <button type="button" className="btn btn-primary" disabled={action.pending || !acknowledged} onClick={() => void toggle(true)}>Turn screening on</button>
      )}
      {!line.screening_enabled && !acknowledged && <p className="mt-1 text-xs text-muted">Accept the acknowledgments above first.</p>}
      <ErrorBanner error={action.error} /><StatusMessage message={msg} />
    </div>
  );
}

function CodeStep({ title, path, ready, onChanged }: { title: string; path: string; ready: boolean; onChanged: () => void }) {
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const action = useAction();
  async function send(withCode: boolean) {
    const r = await action.run(() => api.post<{ status?: string; detail?: string }>(path, withCode ? { code } : {}));
    if (r) { setMsg(r.detail ?? humanize(r.status ?? "done")); onChanged(); }
  }
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{title}</p>
      <button type="button" className="btn" disabled={!ready || action.pending} onClick={() => void send(false)}>Send me a code</button>
      <div className="flex flex-wrap items-end gap-2">
        <div className="w-40"><Field label="Code" htmlFor={`code-${path}`}><input id={`code-${path}`} className="input" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} /></Field></div>
        <button type="button" className="btn" disabled={!ready || action.pending || code.length < 4} onClick={() => void send(true)}>Confirm code</button>
      </div>
      <ErrorBanner error={action.error} /><StatusMessage message={msg} />
    </div>
  );
}

function Consent({ line, acknowledged }: { line: Line; acknowledged: boolean }) {
  const res = useResource(`consent-${line.id}`, () => api.get<Schemas["ConsentView"]>(`/v1/lines/${line.id}/consent`));
  const [mode, setMode] = useState<string>("METADATA_ONLY");
  const [authorized, setAuthorized] = useState(false);
  const [remote, setRemote] = useState(false);
  const [allParty, setAllParty] = useState(false);
  const action = useAction();
  async function submit(e: FormEvent) {
    e.preventDefault();
    const r = await action.run(() => api.post(`/v1/lines/${line.id}/consent`, { processing_mode: mode, authorized, remote_stt_ok: remote, all_party_basis_confirmed: allParty }));
    if (r) res.reload();
  }
  return (
    <div>
      <p className="mb-2 text-sm font-medium">Processing consent</p>
      <Loadable {...res} rows={1}>
        {(v) => (
          <div className="mb-3 text-sm">
            <p>Current mode: <Badge>{humanize(v.decision.mode)}</Badge> Live speech-to-text {v.decision.allow_live_stt ? "allowed" : "not allowed"}; transcript kept: {v.decision.allow_transcript_persist ? "yes" : "no"}; recording kept: {v.decision.allow_recording ? "yes" : "no"}.</p>
            {v.decision.reasons.length > 0 && <ul className="mt-1 list-disc pl-5 text-muted">{v.decision.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
          </div>
        )}
      </Loadable>
      <form onSubmit={submit} className="grid max-w-xl gap-3" aria-label={`Consent for ${line.label || line.e164}`}>
        <Field label="Processing mode" htmlFor={`mode-${line.id}`}>
          <select id={`mode-${line.id}`} className="input" value={mode} onChange={(e) => setMode(e.target.value)}>{MODES.map((m) => <option key={m} value={m}>{humanize(m)}</option>)}</select>
        </Field>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 h-5 w-5" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} /><span>I authorize this mode on this line.</span></label>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 h-5 w-5" checked={remote} onChange={(e) => setRemote(e.target.checked)} /><span>Remote speech-to-text is acceptable.</span></label>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 h-5 w-5" checked={allParty} onChange={(e) => setAllParty(e.target.checked)} /><span>I have confirmed the all-party consent basis for recording where required.</span></label>
        <ErrorBanner error={action.error} />
        <div><button type="submit" className="btn" disabled={action.pending || (mode !== "METADATA_ONLY" && !acknowledged)}>Save consent</button></div>
      </form>
    </div>
  );
}

export function LineCard({ line, acknowledged, callbackEnabled, onChanged }: Props) {
  return (
    <article className="card mb-4" aria-label={`Line ${line.label || line.e164}`}>
      <h3 className="mb-2 text-base font-semibold">{line.label || line.e164}</h3>
      <DefList rows={[
        ["Number", line.e164],
        ["Verification", <Badge key="v" tone={line.verification_status === "VERIFIED" ? "ok" : "warn"}>{humanize(line.verification_status)}</Badge>],
        ["Verified at", line.verified_at ? formatDateTime(line.verified_at) : "Not verified"],
        ["Routable", line.routable ? "Yes" : "No"],
      ]} />
      <div className="mt-4 grid gap-5 lg:grid-cols-2">
        <Screening line={line} acknowledged={acknowledged} onChanged={onChanged} />
        <CodeStep title="Verify you own this line" path={`/v1/lines/${line.id}/verify`} ready={acknowledged} onChanged={onChanged} />
        {callbackEnabled ? (
          <CodeStep title="Verify caller ID ownership for callbacks" path={`/v1/lines/${line.id}/outbound-ownership-check`} ready={acknowledged} onChanged={onChanged} />
        ) : (
          <Notice>Callback caller-ID verification appears here when callbacks are turned on for the server.</Notice>
        )}
      </div>
      <div className="mt-5 border-t border-line pt-4"><Consent line={line} acknowledged={acknowledged} /></div>
    </article>
  );
}
