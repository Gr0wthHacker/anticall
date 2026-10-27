/** API error envelope: { error: { code, message, details } } (apps/api api/errors.py). */

export type ErrorDetails = Record<string, unknown>;

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ErrorDetails;

  constructor(status: number, code: string, message: string, details: ErrorDetails = {}) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get requestId(): string | null {
    const id = this.details["request_id"];
    return typeof id === "string" ? id : null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fromEnvelope(status: number, error: Record<string, unknown>): ApiRequestError {
  const code = typeof error["code"] === "string" ? error["code"] : `HTTP_${status}`;
  const message = typeof error["message"] === "string" ? error["message"] : "Request failed";
  const details = isRecord(error["details"]) ? error["details"] : {};
  return new ApiRequestError(status, code, message, details);
}

/** Turn any failed response body into an ApiRequestError. Never throws. */
export function parseErrorEnvelope(status: number, body: unknown): ApiRequestError {
  if (isRecord(body) && isRecord(body["error"])) return fromEnvelope(status, body["error"]);
  if (isRecord(body) && typeof body["detail"] === "string") {
    return new ApiRequestError(status, `HTTP_${status}`, body["detail"]);
  }
  return new ApiRequestError(status, `HTTP_${status}`, `Request failed (HTTP ${status})`);
}

export function describeError(error: unknown): string {
  if (error instanceof ApiRequestError) return `${error.message} (${error.code})`;
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}
