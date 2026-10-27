"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LineCard } from "@/components/LineCard";
import { DefList, Field, Loadable, Notice, PageHeader, Section, StatusMessage } from "@/components/ui";
import { ACKNOWLEDGMENT, readAck, saveAck } from "@/lib/acknowledgment";
import { api } from "@/lib/api/client";
import type { Page, Schemas } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { useFeatures } from "@/lib/features";
import { useAction, useResource } from "@/lib/hooks";

function Acknowledgment({ userId, accepted, onAccepted }: { userId: string; accepted: boolean; onAccepted: () => void }) {
  const [checked, setChecked] = useState<string[]>([]);
  const [warn, setWarn] = useState<string | null>(null);
  const required = ACKNOWLEDGMENT.items.filter((i) => i.required);
  const all = required.every((i) => checked.includes(i.id));
  async function accept() {
    const ok = await saveAck(userId);
    if (ok) onAccepted();
    else setWarn("This browser blocked local storage, so the acceptance could not be saved.");
  }
  if (accepted) return <Section title="Acknowledgment"><p role="status" className="text-sm">Accepted for version {ACKNOWLEDGMENT.version} in this browser. Telephony features are unlocked.</p></Section>;
  return (
    <Section title="Acknowledgment before activating telephony features">
      <Notice tone="warn">{ACKNOWLEDGMENT.notice}</Notice>
      <fieldset className="mt-3">
        <legend className="mb-2 text-sm font-medium">{ACKNOWLEDGMENT.preamble}</legend>
        <ul className="space-y-2">
          {ACKNOWLEDGMENT.items.map((i) => (
            <li key={i.id}>
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1 h-5 w-5" checked={checked.includes(i.id)} onChange={(e) => setChecked((c) => (e.target.checked ? [...c, i.id] : c.filter((x) => x !== i.id)))} />
                <span>{i.text}</span>
              </label>
            </li>
          ))}
        </ul>
      </fieldset>
      <button type="button" className="btn btn-primary mt-3" disabled={!all} onClick={() => void accept()}>I accept</button>
      <p className="mt-2 text-xs text-muted">Stored in this browser only. The API has no acknowledgment endpoint yet.</p>
      <StatusMessage message={warn} />
    </Section>
  );
}

function NewLine({ onCreated }: { onCreated: () => void }) {
  const [e164, setE164] = useState("");
  const [label, setLabel] = useState("");
  const [created, setCreated] = useState<Schemas["LineCreated"] | null>(null);
  const action = useAction();
  async function submit(e: FormEvent) {
    e.preventDefault();
    const r = await action.run(() => api.post<Schemas["LineCreated"]>("/v1/lines", { e164, label }));
    if (r) { setCreated(r); setE164(""); setLabel(""); onCreated(); }
  }
  return (
    <Section title="Add a line">
      <form onSubmit={submit} className="grid max-w-xl gap-3" aria-label="Add a line">
        <Field label="Your phone number (E.164)" htmlFor="line-e164" hint="Example format: +14155550100"><input id="line-e164" className="input" required pattern="\+[0-9]{8,15}" value={e164} onChange={(e) => setE164(e.target.value)} /></Field>
        <Field label="Label" htmlFor="line-label"><input id="line-label" className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
        <ErrorBanner error={action.error} />
        <div><button type="submit" className="btn btn-primary" disabled={action.pending}>Add line</button></div>
      </form>
      {created && (
        <div className="mt-4" role="status">
          <Notice tone="warn">Copy the webhook details now. The secret is shown once.</Notice>
          <DefList rows={[["Webhook path", created.webhook.url_path], ["Line key", created.webhook.line_key], ["Secret", <code key="s" className="break-all">{created.webhook.secret}</code>]]} />
        </div>
      )}
    </Section>
  );
}

export default function SettingsPage() {
  const { me } = useAuth();
  const { callback } = useFeatures();
  const lines = useResource("settings-lines", () => api.get<Page<Schemas["LineOut"]>>("/v1/lines", { limit: 200 }));
  const [accepted, setAccepted] = useState(false);
  useEffect(() => {
    if (me) setAccepted(readAck(me.user_id) !== null);
  }, [me]);
  return (
    <>
      <PageHeader title="Settings" subtitle="Lines, ownership, screening and consent." actions={<Link className="btn" href="/callbacks/settings">Callback settings</Link>} />
      {me && <Acknowledgment userId={me.user_id} accepted={accepted} onAccepted={() => setAccepted(true)} />}
      <Section title="Lines">
        <Loadable {...lines} rows={2}>
          {(page) => page.items.length === 0 ? <p className="text-sm">No lines yet. Add one below.</p> : (
            <>{page.items.map((l) => <LineCard key={l.id} line={l} acknowledged={accepted} callbackEnabled={callback?.callback_enabled === true} onChanged={lines.reload} />)}</>
          )}
        </Loadable>
      </Section>
      <NewLine onCreated={lines.reload} />
    </>
  );
}
