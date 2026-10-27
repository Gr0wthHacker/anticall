import { ApiRequestError, parseErrorEnvelope } from "./errors";

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

const TOKEN_KEY = "callshield.token";

let memoryToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

function readStored(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeStored(token: string | null): void {
  try {
    if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
    else window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the in-memory token still works for this tab */
  }
}

export function getToken(): string | null {
  if (memoryToken === null && typeof window !== "undefined") memoryToken = readStored();
  return memoryToken;
}

export function setToken(token: string | null): void {
  memoryToken = token;
  writeStored(token);
}

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  query?: Query;
  body?: unknown;
  idempotencyKey?: string;
  auth?: boolean;
  signal?: AbortSignal;
}

export function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return `${API_BASE_URL}${path}${qs ? `?${qs}` : ""}`;
}

function buildHeaders(opts: RequestOptions): Headers {
  const headers = new Headers({ Accept: "application/json" });
  if (opts.body !== undefined) headers.set("Content-Type", "application/json");
  if (opts.idempotencyKey) headers.set("Idempotency-Key", opts.idempotencyKey);
  const token = opts.auth === false ? null : getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

async function send(method: string, path: string, opts: RequestOptions): Promise<Response> {
  try {
    return await fetch(buildUrl(path, opts.query), {
      method,
      headers: buildHeaders(opts),
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiRequestError(0, "NETWORK_ERROR", "Could not reach the CallShield API");
  }
}

export async function request<T>(
  method: string,
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const res = await send(method, path, opts);
  const body = await readBody(res);
  if (!res.ok) {
    if (res.status === 401 && opts.auth !== false) unauthorizedHandler?.();
    throw parseErrorEnvelope(res.status, body);
  }
  return body as T;
}

/** Authenticated binary download (export zips). Keeps the bearer token out of URLs. */
export async function requestBlob(path: string): Promise<Blob> {
  const res = await send("GET", path, {});
  if (!res.ok) {
    if (res.status === 401) unauthorizedHandler?.();
    throw parseErrorEnvelope(res.status, await readBody(res));
  }
  return res.blob();
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) =>
    request<T>("GET", path, { query, signal }),
  post: <T>(path: string, body?: unknown, idempotencyKey?: string) =>
    request<T>("POST", path, { body: body ?? {}, idempotencyKey }),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, { body }),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, { body }),
  del: <T>(path: string) => request<T>("DELETE", path),
};
