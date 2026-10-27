# Security Policy

## Reporting a vulnerability

Use GitHub private vulnerability reporting: **Security tab > Report a vulnerability** on [github.com/Gr0wthHacker/anticall](https://github.com/Gr0wthHacker/anticall/security/advisories/new). Do not open public issues for vulnerabilities, and do not include real phone numbers, recordings, or credentials in any report.

## Scope

In scope:
- Tenant isolation / authorization bypass
- Webhook signature validation or replay-defense bypass
- Ability to cause dial-out to an unverified destination, or to bypass attempt limits, cooldowns, or policy gates
- Prompt injection that leads to a tool call, policy change, or data disclosure
- Evidence tampering without an audit trail, secret exposure, PII in logs
- SIP credential handling and toll-fraud vectors

Out of scope:
- Vulnerabilities in third-party carriers, SIP providers, or AI vendors (report to them)
- Misconfigured self-hosted instances you do not operate
- Findings that require a disabled capability to be enabled with deliberately unsafe settings, unless the docs call that configuration safe
- Denial of service by volumetric traffic

## Response targets

Best-effort targets for a solo-maintained pre-alpha project, not guarantees:

| Step | Target |
|---|---|
| Acknowledge report | 7 days |
| Initial triage | 14 days |
| Fix or mitigation plan for confirmed high/critical | 30 days |

Coordinated disclosure: we will credit reporters who want credit once a fix ships.

## Bounty

There is no bug bounty program and no payment is offered or implied.

## Supported versions

Pre-alpha. Only the `main` branch is supported. No release is cleared for production telephony.

## Operators

Maintainers do not operate or supervise independent self-hosted instances. If you run one, you own patching, secrets rotation, and incident response. See [`docs/deployment/self-hosting.md`](docs/deployment/self-hosting.md).

## Abuse reports

For misuse of the software against third parties (harassment, spoofing, retaliatory calling), open an issue using the abuse-report template, or use a private advisory if it includes sensitive detail. See [`ACCEPTABLE_USE.md`](ACCEPTABLE_USE.md).
