"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Field } from "@/components/ui";
import { API_BASE_URL } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { useAction } from "@/lib/hooks";

type Mode = "login" | "bootstrap";

export default function LoginPage() {
  const { status, login, bootstrap } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const action = useAction();

  useEffect(() => {
    if (status === "authed") router.replace("/");
  }, [status, router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (mode === "login") {
      await action.run(() => login(email, password));
      return;
    }
    await action.run(() =>
      bootstrap({
        tenant_name: tenantName,
        admin_email: email,
        admin_password: password,
        ...(displayName ? { admin_display_name: displayName } : {}),
      }),
    );
  }

  return (
    <main id="main" className="mx-auto max-w-md p-4 pt-10 sm:pt-16">
      <h1 className="mb-1 text-2xl font-semibold">CallShield Network</h1>
      <p className="mb-5 text-sm text-muted">
        {mode === "login" ? "Sign in to your dashboard." : "Create the first tenant and its admin user."}
      </p>
      <form onSubmit={onSubmit} className="card space-y-4" aria-label={mode === "login" ? "Sign in" : "Create tenant"}>
        {mode === "bootstrap" && (
          <>
            <Field label="Tenant name" htmlFor="tenant">
              <input id="tenant" className="input" required value={tenantName} onChange={(e) => setTenantName(e.target.value)} />
            </Field>
            <Field label="Your display name" htmlFor="dname">
              <input id="dname" className="input" autoComplete="name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </Field>
          </>
        )}
        <Field label="Email" htmlFor="email">
          <input id="email" type="email" className="input" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password" htmlFor="password" hint={mode === "bootstrap" ? "Use a strong password. It is sent only to your API." : undefined}>
          <input id="password" type="password" className="input" required autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorBanner error={action.error} />
        <button type="submit" className="btn btn-primary w-full" disabled={action.pending}>
          {action.pending ? "Working…" : mode === "login" ? "Sign in" : "Create tenant"}
        </button>
      </form>
      <p className="mt-4 text-sm">
        <button
          type="button"
          className="link min-h-[44px]"
          onClick={() => {
            action.clear();
            setMode(mode === "login" ? "bootstrap" : "login");
          }}
        >
          {mode === "login" ? "First time? Create the first tenant" : "Back to sign in"}
        </button>
      </p>
      <p className="mt-2 text-xs text-muted">API: {API_BASE_URL}</p>
      {mode === "bootstrap" && (
        <p className="mt-2 text-xs text-muted">
          Tenant creation works only while no tenant exists, or when the server sets
          CALLSHIELD_ALLOW_BOOTSTRAP=true.
        </p>
      )}
    </main>
  );
}
