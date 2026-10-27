/** Idempotency-Key helper: one key per user intent; reuse the same key when retrying it. */
export function newIdempotencyKey(prefix = "dash"): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random}`;
}
