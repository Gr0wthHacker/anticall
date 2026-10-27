/** Ranges from docs/prd/CHANGE_callback_flow.md; the server enforces them again. */
export const RANGES: Record<string, [number, number]> = {
  callback_window_hours: [1, 168],
  listen_window_seconds: [0, 10],
  bare_greeting_disclose_ms: [500, 3000],
  max_attempts_per_inbound: [1, 2],
  max_attempts_per_number_30d: [1, 2],
  max_attempts_per_user_day: [1, 5],
  max_question_turns: [1, 4],
  max_call_seconds: [30, 120],
};

export function validateDraft(draft: Record<string, string | boolean>, editable: string[]): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const key of editable) {
    const range = RANGES[key];
    const raw = draft[key];
    if (!range || typeof raw !== "string") continue;
    const n = Number(raw);
    if (raw.trim() === "" || !Number.isInteger(n) || n < range[0] || n > range[1]) {
      errors[key] = `Enter a whole number from ${range[0]} to ${range[1]}.`;
    }
  }
  return errors;
}
