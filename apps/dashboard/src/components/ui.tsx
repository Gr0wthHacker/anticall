import type { ReactNode } from "react";
import { ErrorBanner } from "./ErrorBanner";
import { SkeletonLoader } from "./SkeletonLoader";
import { asText, isRecord } from "@/lib/format";

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function Section({
  title,
  children,
  id,
}: {
  title: string;
  children: ReactNode;
  id?: string;
}) {
  const headingId = id ? `${id}-h` : undefined;
  return (
    <section id={id} aria-labelledby={headingId} className="card mb-5">
      <h2 id={headingId} className="mb-3 text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function Notice({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "warn" }) {
  const cls = tone === "warn" ? "border-warn/60 bg-warn/10" : "border-line bg-surface";
  return <p className={`rounded-md border p-3 text-sm ${cls}`}>{children}</p>;
}

export function DefList({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[12rem_1fr]">
      {rows.map(([term, desc]) => (
        <div key={term} className="contents">
          <dt className="text-muted">{term}</dt>
          <dd className="min-w-0 break-words">{desc}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Render an arbitrary object as a definition list (for free-form API fields). */
export function KeyValues({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return <p className="text-sm text-muted">No fields recorded.</p>;
  return (
    <DefList
      rows={entries.map(([k, v]) => [k, isRecord(v) || Array.isArray(v) ? <code key={k} className="break-all text-xs">{asText(v)}</code> : asText(v)])}
    />
  );
}

export function Field({
  label,
  hint,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  htmlFor: string;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="label">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

/** Wrap a loaded resource with consistent loading and error states. */
export function Loadable<T>({
  data,
  error,
  loading,
  reload,
  children,
  rows,
}: {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload?: () => void;
  children: (data: T) => ReactNode;
  rows?: number;
}) {
  if (error && !data) return <ErrorBanner error={error} onRetry={reload} />;
  if (loading && !data) return <SkeletonLoader rows={rows} />;
  if (!data) return null;
  return (
    <>
      {error ? <ErrorBanner error={error} onRetry={reload} /> : null}
      {children(data)}
    </>
  );
}

export function StatusMessage({ message }: { message: string | null }) {
  return (
    <p role="status" aria-live="polite" className="text-sm text-ok">
      {message}
    </p>
  );
}
