# Quickstart: first synthetic call in about 10 minutes

> Pre-alpha. This walkthrough uses the built-in mock path only. **No real phone line, SIP trunk, carrier or recording is involved**, and every gated feature stays off. Use fictional numbers (`+1415555 01xx`). Not legal advice.

You need Python 3.11+ and Node 20+ (Node only for the dashboard).

## 1. Install (2 min)

```bash
git clone <this repo> && cd anticall
python -m venv apps/api/.venv
# Linux/macOS: source apps/api/.venv/bin/activate
# Windows (Git Bash): source apps/api/.venv/Scripts/activate
pip install -e "apps/api[dev]"
```

Check it worked: `callshield --help`.

## 2. Configure (2 min)

```bash
cd apps/api
callshield init
```

Press Enter to accept the defaults (SQLite, generic profile, every gated feature **No**). The wizard writes `.env` (mode 0600 where the OS supports it), `pjsip.callshield.conf`, `ari.callshield.conf` and `SETUP_REPORT.md` listing what is on and off. Secrets are generated for you and never printed. When asked for an admin email and password, give them: you need that login below.

Prefer scripts? `callshield init --non-interactive --answers answers.json` (see `callshield init --help`).

## 3. Check and migrate (1 min)

```bash
callshield db upgrade
callshield doctor
```

`doctor` prints PASS/WARN/FAIL with a fix hint for each problem. WARNs about an unknown jurisdiction or backups are normal on a fresh install. Fix any FAIL before continuing.

Skipped the admin in the wizard? `callshield user create-admin --email you@example.test`.

## 4. Start the API (1 min)

```bash
callshield run api        # http://127.0.0.1:8000, localhost only
```

Leave it running. Open `http://127.0.0.1:8000/docs` to see the API.

## 5. Send one synthetic call (1 min)

In a second terminal, same virtualenv:

```bash
cd apps/api
python scripts/synthetic_call.py --in-process --email you@example.test
```

This creates a protected line for the fictional number `+14155550100`, verifies it using the dev-only in-memory code channel, and delivers one signed inbound-call webhook from `+14155550123`, exactly as a provider would. `--in-process` runs the API inside the script against your `.env` database, so it works even though the dev verification channel cannot be reached from outside the server process.

## 6. Open the dashboard (3 min)

```bash
cd apps/dashboard
npm install
npm run dev               # http://127.0.0.1:3000
```

Log in with the admin you created. Check **Calls** for the synthetic call and **Setup** for the doctor results, which gated features are off, and links to the docs.

## What next

- Real SIP trunk: [`deployment/connect-your-sip-trunk.md`](deployment/connect-your-sip-trunk.md). Read the provider's terms first.
- Every setting: [`deployment/configuration-reference.md`](deployment/configuration-reference.md).
- Before real calls: [`deployment/going-live-checklist.md`](deployment/going-live-checklist.md). Real telephony has not been exercised end to end in this project.
- Local Asterisk test harness: [`../integrations/asterisk/README.md`](../integrations/asterisk/README.md) (also unverified).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `callshield: command not found` | Activate the virtualenv; re-run `pip install -e "apps/api[dev]"`. |
| `doctor` FAIL: migrations | `callshield db upgrade` |
| `init` says `.env` exists | It will not overwrite secrets. Move it, or pass `--force`. |
| Synthetic call returns 403 not verified | Use `--in-process`. |
| Dashboard cannot reach the API | Same host/port as `NEXT_PUBLIC_API_BASE_URL`; the API's CORS list must include `http://127.0.0.1:3000` (the default). |
