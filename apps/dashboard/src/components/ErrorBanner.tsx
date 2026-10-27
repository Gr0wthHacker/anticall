import { ApiRequestError } from "@/lib/api/errors";

/** Error display. role="alert" so screen readers announce it immediately. */
export function ErrorBanner({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  if (!error) return null;
  const known = error instanceof ApiRequestError ? error : null;
  const message = typeof error === "string" ? error : (known?.message ?? "Something went wrong");
  return (
    <div role="alert" className="rounded-md border border-bad bg-bad/10 p-3 text-sm">
      <p className="font-medium text-bad">{message}</p>
      {known && (
        <p className="mt-1 text-muted">
          Code {known.code}
          {known.requestId ? ` · request ${known.requestId}` : ""}
        </p>
      )}
      {onRetry && (
        <button type="button" className="btn mt-2" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
