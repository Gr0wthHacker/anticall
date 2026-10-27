# CallShield dashboard

Next.js (App Router) + TypeScript strict + Tailwind. Talks to the FastAPI backend in `apps/api`. All pages are client-rendered; the JWT lives in memory and `sessionStorage`.

## Run

```bash
npm install
npm run gen:api     # regenerate src/lib/api/schema.d.ts from ../api/openapi.json
npm run dev         # http://localhost:3000
npm run lint && npm run typecheck && npm test && npm run build
```

## API base URL and CORS

`NEXT_PUBLIC_API_BASE_URL` (build time, default `http://127.0.0.1:8000`). The API has no CORS middleware, so a browser on another origin cannot call it directly. Use the built-in proxy instead:

```bash
# Git Bash on Windows: prefix with MSYS_NO_PATHCONV=1 or "/api-proxy" is rewritten to a Windows path
NEXT_PUBLIC_API_BASE_URL=/api-proxy API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

`/api-proxy/*` is rewritten to `API_PROXY_TARGET` (read at build time; rewrites are baked into the standalone build).

## Docker

`infra/docker/dashboard.Dockerfile` builds from the repo root and runs the `output: "standalone"` bundle (`node server.js`). Pass `--build-arg NEXT_PUBLIC_API_BASE_URL=...`.

## Layout

- `src/lib/api/` generated `schema.d.ts`, `client.ts` (auth, error envelope, `Idempotency-Key`), `errors.ts`, `types.ts` (hand types for endpoints the spec leaves as free-form objects)
- `src/components/` Badge (evidence labels), GateList, Timeline, ConfirmDialog (focus trap), DataTable, EmptyState, ErrorBanner, SkeletonLoader, CallbackPanel, FilingSubmit
- `src/app/(app)/` authenticated pages; `src/app/login/` sign-in and first-tenant bootstrap
- `src/lib/acknowledgment-v1.json` is a copy of `docs/legal/acknowledgment-v1.json` (`npm run sync:legal` refreshes it)

## Rules the UI follows

- Evidence is always labeled OBSERVED / CLAIMED / MODEL_INFERRED / USER_ATTESTED / EXTERNALLY_VERIFIED.
- No "contact now" button (`start-contact` returns 410). Callbacks go through the 7-gate eligibility panel, and no field accepts a number to call.
- No legal-validity percentage and no attack-volume metrics. Assessments show only the four allowed states.
- Filing never retries on its own; UNKNOWN stays UNKNOWN until you check status.
- Gated features show OFF when the server has them off.
