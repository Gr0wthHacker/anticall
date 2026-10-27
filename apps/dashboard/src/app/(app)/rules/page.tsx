"use client";

import { useState, type FormEvent } from "react";
import { Badge } from "@/components/Badge";
import { DataTable, type Column } from "@/components/DataTable";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Field, Loadable, PageHeader, Section } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { Page, Schemas } from "@/lib/api/types";
import { asText, humanize } from "@/lib/format";
import { useAction, useResource } from "@/lib/hooks";

type Rule = Schemas["RuleOut"];
type Contact = Schemas["ContactOut"];
const ACTIONS: Schemas["RoutingAction"][] = ["ALLOW", "TRANSFER", "SCREEN", "VOICEMAIL", "REJECT", "FALLBACK"];
const KINDS: Schemas["TrustedContactKind"][] = ["TRUSTED", "PERMITTED", "KNOWN_UNWANTED", "OVERRIDE"];

const splitList = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);

function RuleTable({ rules, reload }: { rules: Rule[]; reload: () => void }) {
  const action = useAction();
  const columns: Column<Rule>[] = [
    { key: "pri", header: "Priority", render: (r) => r.priority },
    { key: "act", header: "Action", render: (r) => <Badge tone="brand">{humanize(r.action)}</Badge> },
    { key: "match", header: "Matches", render: (r) => <code className="break-all text-xs">{asText(r.matcher)}</code> },
    { key: "on", header: "Status", render: (r) => (r.enabled ? "Enabled" : "Disabled") },
    { key: "do", header: "Change", render: (r) => (
      <button type="button" className="btn" disabled={action.pending || !r.enabled} aria-label={`Disable rule priority ${r.priority}`}
        onClick={() => void action.run(() => api.del(`/v1/rules/${r.id}`)).then((x) => x && reload())}>Disable</button>
    ) },
  ];
  return (<><ErrorBanner error={action.error} /><DataTable caption="Routing rules" columns={columns} rows={rules} rowKey={(r) => r.id} emptyTitle="No rules" emptyHint="Without rules, the line's default routing applies." /></>);
}

function NewRule({ onCreated }: { onCreated: () => void }) {
  const lines = useResource("rule-lines", () => api.get<Page<Schemas["LineOut"]>>("/v1/lines", { limit: 200 }));
  const [lineId, setLineId] = useState("");
  const [act, setAct] = useState<string>("REJECT");
  const [priority, setPriority] = useState("100");
  const [exact, setExact] = useState("");
  const [prefix, setPrefix] = useState("");
  const [attest, setAttest] = useState("");
  const action = useAction();
  async function submit(e: FormEvent) {
    e.preventDefault();
    const line = lineId || lines.data?.items[0]?.id;
    if (!line) return;
    const matcher: Record<string, unknown> = {};
    if (exact) matcher["caller_exact"] = splitList(exact);
    if (prefix) matcher["caller_prefix"] = splitList(prefix);
    if (attest) matcher["attestation_in"] = splitList(attest.toUpperCase());
    const r = await action.run(() => api.post("/v1/rules", { line_id: line, action: act, priority: Number(priority), matcher }));
    if (r) { setExact(""); setPrefix(""); setAttest(""); onCreated(); }
  }
  return (
    <Section title="Add a routing rule">
      <form onSubmit={submit} className="grid max-w-xl gap-3" aria-label="Add a routing rule">
        <Field label="Line" htmlFor="r-line"><select id="r-line" className="input" value={lineId} onChange={(e) => setLineId(e.target.value)}>{(lines.data?.items ?? []).map((l) => <option key={l.id} value={l.id}>{l.label || l.e164}</option>)}</select></Field>
        <Field label="Action" htmlFor="r-act"><select id="r-act" className="input" value={act} onChange={(e) => setAct(e.target.value)}>{ACTIONS.map((a) => <option key={a} value={a}>{humanize(a)}</option>)}</select></Field>
        <Field label="Priority" htmlFor="r-pri" hint="Lower numbers are checked first."><input id="r-pri" type="number" className="input" value={priority} onChange={(e) => setPriority(e.target.value)} /></Field>
        <Field label="Caller IDs (exact, comma separated)" htmlFor="r-exact"><input id="r-exact" className="input" value={exact} onChange={(e) => setExact(e.target.value)} /></Field>
        <Field label="Caller ID prefixes (comma separated)" htmlFor="r-prefix"><input id="r-prefix" className="input" value={prefix} onChange={(e) => setPrefix(e.target.value)} /></Field>
        <Field label="Attestation levels (A, B, C)" htmlFor="r-att"><input id="r-att" className="input" value={attest} onChange={(e) => setAttest(e.target.value)} /></Field>
        <ErrorBanner error={action.error ?? lines.error} />
        <div><button type="submit" className="btn btn-primary" disabled={action.pending}>Add rule</button></div>
      </form>
    </Section>
  );
}

function Contacts() {
  const lines = useResource("contact-lines", () => api.get<Page<Schemas["LineOut"]>>("/v1/lines", { limit: 200 }));
  const res = useResource("contacts", () => api.get<Contact[]>("/v1/contacts"));
  const [lineId, setLineId] = useState("");
  const [e164, setE164] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<string>("TRUSTED");
  const action = useAction();
  async function add(e: FormEvent) {
    e.preventDefault();
    const line = lineId || lines.data?.items[0]?.id;
    if (!line) return;
    const r = await action.run(() => api.post("/v1/contacts", { line_id: line, e164, name, kind }));
    if (r) { setE164(""); setName(""); res.reload(); }
  }
  const columns: Column<Contact>[] = [
    { key: "name", header: "Name", render: (c) => c.name || "Unnamed" },
    { key: "num", header: "Number", render: (c) => c.e164 },
    { key: "kind", header: "Kind", render: (c) => <Badge>{humanize(c.kind)}</Badge> },
    { key: "rm", header: "Remove", render: (c) => <button type="button" className="btn" aria-label={`Remove ${c.name || c.e164}`} onClick={() => void action.run(() => api.del(`/v1/contacts/${c.id}`)).then(() => res.reload())}>Remove</button> },
  ];
  return (
    <Section title="Trusted contacts">
      <Loadable {...res} rows={2}>{(list) => <DataTable caption="Trusted contacts" columns={columns} rows={list} rowKey={(c) => c.id} emptyTitle="No contacts" />}</Loadable>
      <form onSubmit={add} className="mt-4 grid max-w-xl gap-3" aria-label="Add a contact">
        <Field label="Line" htmlFor="c-line"><select id="c-line" className="input" value={lineId} onChange={(e) => setLineId(e.target.value)}>{(lines.data?.items ?? []).map((l) => <option key={l.id} value={l.id}>{l.label || l.e164}</option>)}</select></Field>
        <Field label="Name" htmlFor="c-name"><input id="c-name" className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Number (E.164)" htmlFor="c-num"><input id="c-num" className="input" required pattern="\+[0-9]{8,15}" value={e164} onChange={(e) => setE164(e.target.value)} /></Field>
        <Field label="Kind" htmlFor="c-kind"><select id="c-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((k) => <option key={k} value={k}>{humanize(k)}</option>)}</select></Field>
        <ErrorBanner error={action.error} />
        <div><button type="submit" className="btn btn-primary" disabled={action.pending}>Add contact</button></div>
      </form>
    </Section>
  );
}

export default function RulesPage() {
  const res = useResource("rules", () => api.get<Rule[]>("/v1/rules"));
  return (
    <>
      <PageHeader title="Rules" subtitle="Routing rules decide what happens to a call before any model is involved." />
      <Section title="Routing rules"><Loadable {...res} rows={3}>{(rules) => <RuleTable rules={rules} reload={res.reload} />}</Loadable></Section>
      <NewRule onCreated={res.reload} />
      <Contacts />
    </>
  );
}
