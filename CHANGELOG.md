# Changelog

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project is pre-alpha; no release is cleared for production telephony.

## [Unreleased]

### Added
- `callshield` CLI (init wizard, doctor, config, voice models, db upgrade, user create-admin, run), provider profiles (unverified templates) with pjsip/env renderers, `GET /v1/system/doctor`, dashboard Setup page, quickstart/trunk/going-live docs, generated configuration reference. `env.example` is now generated from the settings classes and uses the real variable names (the previous file used names the code never read: `CALLSHIELD_DATABASE_URL`, `CALLSHIELD_SECRET_KEY`, etc.). `CALLSHIELD_CALLBACK_*` and `CALLSHIELD_LLM_*` now also read `.env`.
- Repository scaffolding: README, SECURITY, PRIVACY (template), ACCEPTABLE_USE, DISCLAIMER, CONTRIBUTING, CODE_OF_CONDUCT (Contributor Covenant 2.1)
- AGPL-3.0 LICENSE
- Draft legal pack under `docs/legal/` (all drafts, not legal advice)
- Developer docs: decision log D-001..D-008, ADRs 0001-0005, architecture, testing, traceability
- Docker Compose, Dockerfiles, Prometheus config
- Asterisk R0 test harness (fictional numbers only)
- CI (lint, type check, tests, policy guard), CodeQL, Dependabot
