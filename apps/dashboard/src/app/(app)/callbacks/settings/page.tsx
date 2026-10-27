"use client";

import { useState, type FormEvent } from "react";
import { Badge, FlagBadge } from "@/components/Badge";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Loadable, Notice, PageHeader, Section, StatusMessage } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { CallbackSettingsBody, SettingChanger } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useFeatures } from "@/lib/features";
import { humanize } from "@/lib/format";
import { RANGES, validateDraft } from "@/lib/callbackSettings";
import { useAction, useResource } from "@/lib/hooks";


const LOCK_NOTE: Record<SettingChanger, string> = {
  user: "You can change this",
  operator: "Operator only",
  locked: "Locked",
  per_call: "Chosen per callback",
};

function canEdit(who: SettingChanger | undefined, isOperator: boolean): boolean {
  if (who === "user") return !isOperator;
  if (who === "operator") return isOperator;
  return false;
}

function Row(props: {
  name: string;
  who: SettingChanger | undefined;
  value: string | boolean;
  editable: boolean;
  error?: string;
  onChange: (v: string | boolean) => void;
}) {
  const { name, value, editable } = props;
  const id = `cb-${name}`;
  const range = RANGES[name];
  const note = props.who ? LOCK_NOTE[props.who] : "Set by the server";
  return (
    <div className="grid gap-1 border-b border-line py-3 sm:grid-cols-[16rem_1fr]">
      <label htmlFor={id} className="text-sm font-medium">{humanize(name)}</label>
      <div>
        {typeof value === "boolean" ? (
          <input id={id} type="checkbox" className="h-5 w-5" checked={value} disabled={!editable} onChange={(e) => props.onChange(e.target.checked)} />
        ) : (
          <input
            id={id}
            className="input max-w-xs"
            inputMode="numeric"
            value={value}
            readOnly={!editable}
            aria-readonly={!editable}
            aria-invalid={props.error ? true : undefined}
            aria-describedby={`${id}-note`}
            onChange={(e) => props.onChange(e.target.value)}
          />
        )}
        <p id={`${id}-note`} className="mt-1 text-xs text-muted">
          {note}{range && editable ? ` · allowed ${range[0]} to ${range[1]}` : ""}
          {!editable && props.who !== "user" ? " · read-only" : ""}
        </p>
        {props.error && <p role="alert" className="text-xs text-bad">{props.error}</p>}
      </div>
    </div>
  );
}

function SettingsForm({ body, reload }: { body: CallbackSettingsBody; reload: () => void }) {
  const { me } = useAuth();
  const isOperator = me?.role === "OPERATOR";
  const initial = Object.fromEntries(Object.entries(body.settings).map(([k, v]) => [k, typeof v === "boolean" ? v : String(v)]));
  const [draft, setDraft] = useState<Record<string, string | boolean>>(initial);
  const [tz, setTz] = useState(body.timezone ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const action = useAction();
  const keys = Object.keys(body.settings);
  const editable = keys.filter((k) => canEdit(body.changeable_by[k], isOperator));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaved(null);
    const found = validateDraft(draft, editable);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const patch: Record<string, number | boolean | string> = {};
    for (const k of editable) {
      const v = draft[k];
      if (v !== initial[k]) patch[k] = typeof v === "string" ? Number(v) : (v as boolean);
    }
    if (!isOperator && tz && tz !== (body.timezone ?? "")) patch["timezone"] = tz;
    if (Object.keys(patch).length === 0) { setSaved("Nothing to save."); return; }
    const res = await action.run(() => api.patch("/v1/callback/settings", patch));
    if (res) { setSaved("Settings saved."); reload(); }
  }

  return (
    <form onSubmit={submit} aria-label="Callback settings">
      {keys.map((k) => (
        <Row
          key={k}
          name={k}
          who={body.changeable_by[k]}
          value={draft[k] ?? ""}
          editable={editable.includes(k)}
          error={errors[k]}
          onChange={(v) => setDraft((d) => ({ ...d, [k]: v }))}
        />
      ))}
      {!isOperator && (
        <div className="grid gap-1 py-3 sm:grid-cols-[16rem_1fr]">
          <label htmlFor="cb-timezone" className="text-sm font-medium">Time zone</label>
          <input id="cb-timezone" className="input max-w-xs" placeholder="e.g. America/Chicago" value={tz} onChange={(e) => setTz(e.target.value)} />
        </div>
      )}
      <ErrorBanner error={action.error} />
      <div className="mt-3 flex items-center gap-3">
        <button type="submit" className="btn btn-primary" disabled={action.pending || editable.length === 0}>Save settings</button>
        <StatusMessage message={saved} />
      </div>
    </form>
  );
}

export default function CallbackSettingsPage() {
  const { callback, loaded } = useFeatures();
  const on = callback?.callback_enabled === true;
  const res = useResource(on ? "callback-settings" : null, () => api.get<CallbackSettingsBody>("/v1/callback/settings"));
  return (
    <>
      <PageHeader title="Callback settings" subtitle="Hard ceilings are set by the server and cannot be raised here." />
      {loaded && !on ? (
        <Notice tone="warn">Automated callbacks are OFF on this server, so there are no settings to change.</Notice>
      ) : (
        <>
          <p className="mb-3 flex items-center gap-2 text-sm">Feature: <FlagBadge on={on} /> <Badge>Locked: no remote AI processing, attestation A only</Badge></p>
          <Section title="Settings">
            <Loadable {...res} rows={6}>{(body) => <SettingsForm body={body} reload={res.reload} />}</Loadable>
          </Section>
        </>
      )}
    </>
  );
}
