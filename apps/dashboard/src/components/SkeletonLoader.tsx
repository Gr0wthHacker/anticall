export function SkeletonLoader({ rows = 3, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}…</span>
      <div className="space-y-3" aria-hidden="true">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="h-10 animate-pulse rounded-md bg-line/60" />
        ))}
      </div>
    </div>
  );
}
