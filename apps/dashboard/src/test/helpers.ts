import { vi } from "vitest";

type Route = { status?: number; body: unknown };
type Handler = Route | ((init: RequestInit | undefined) => Route);

/** Replace global fetch with a router keyed by "METHOD path". Returns the mock for assertions. */
export function mockFetch(routes: Record<string, Handler>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const key = `${init?.method ?? "GET"} ${url.pathname}`;
    const handler = routes[key];
    if (!handler) {
      const body = { error: { code: "NOT_FOUND", message: `no mock for ${key}`, details: {} } };
      return new Response(JSON.stringify(body), { status: 404 });
    }
    const route = typeof handler === "function" ? handler(init) : handler;
    return new Response(JSON.stringify(route.body), {
      status: route.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export function headerOf(init: RequestInit | undefined, name: string): string | null {
  return new Headers(init?.headers).get(name);
}
