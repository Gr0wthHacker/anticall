import type { ReactNode } from "react";

export function EmptyState({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-line p-6 text-center">
      <p className="font-medium">{title}</p>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}
