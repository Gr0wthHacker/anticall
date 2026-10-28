"""Send ONE synthetic inbound call to a running local API (fictional numbers only).

    python scripts/synthetic_call.py --email you@example.test

Reads the password from the CALLSHIELD_PASSWORD environment variable or prompts for it.
Uses the signed generic webhook, exactly like a provider would. Nothing leaves your machine
unless you point --base-url somewhere else.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import uuid
from typing import Any

import httpx

from callshield.telephony.mock import (
    FICTIONAL_CALLER,
    FICTIONAL_LINE,
    inbound_call_payload,
    signed_request,
)


def _auth(client: httpx.Client, email: str, password: str) -> dict[str, str]:
    resp = client.post("/v1/auth/login", json={"email": email, "password": password})
    resp.raise_for_status()
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _find_line_id(client: httpx.Client, headers: dict[str, str]) -> str | None:
    items = client.get("/v1/lines", headers=headers).json().get("items", [])
    for item in items:
        if item["e164"] == FICTIONAL_LINE:
            return str(item["id"])
    return None


def _line(client: httpx.Client, headers: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Create the synthetic line, or rotate the secret of the existing one (rerun-safe)."""
    resp = client.post(
        "/v1/lines",
        headers=headers,
        json={"e164": FICTIONAL_LINE, "label": "synthetic test line", "provider": "generic"},
    )
    if resp.status_code == 201:
        body = resp.json()
        return str(body["id"]), dict(body["webhook"])
    line_id = _find_line_id(client, headers)
    if line_id is None:
        resp.raise_for_status()
        raise RuntimeError("could not create or find the synthetic line")
    rotated = client.post(f"/v1/lines/{line_id}/webhook-secret:rotate", headers=headers)
    rotated.raise_for_status()
    return line_id, dict(rotated.json())


def _verify(client: httpx.Client, headers: dict[str, str], line_id: str) -> None:
    """Verify the line using the in-process dev code channel (only possible in-process)."""
    from callshield.core.config import get_settings
    from callshield.defend.verification import get_challenge_channel

    client.post(f"/v1/lines/{line_id}/verify", headers=headers, json={}).raise_for_status()
    channel = get_challenge_channel(get_settings())
    code = channel.last_code(uuid.UUID(line_id))  # type: ignore[attr-defined]
    client.post(
        f"/v1/lines/{line_id}/verify", headers=headers, json={"code": code}
    ).raise_for_status()


def run_synthetic_call(
    client: httpx.Client, email: str, password: str, in_process: bool = False
) -> dict[str, Any]:
    headers = _auth(client, email, password)
    line_id, creds = _line(client, headers)
    if in_process:
        _verify(client, headers, line_id)
    call_id = f"synthetic-{uuid.uuid4().hex[:8]}"
    payload = inbound_call_payload(call_id=call_id, caller=FICTIONAL_CALLER)
    body, signed = signed_request(creds["secret"], payload, call_id)
    resp = client.post(f"/v1/webhooks/generic/{creds['line_key']}", content=body, headers=signed)
    if resp.status_code == 403 and "not verified" in resp.text:
        sys.stderr.write("Line is not verified. Re-run with --in-process (dev only).\n")
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="run the API in this process against your .env database (dev only)",
    )
    args = parser.parse_args()
    password = os.environ.get("CALLSHIELD_PASSWORD") or getpass.getpass("Admin password: ")
    if args.in_process:
        from fastapi.testclient import TestClient

        from callshield.api.app import create_app

        with TestClient(create_app()) as local:
            result = run_synthetic_call(local, args.email, password, in_process=True)
    else:
        with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
            result = run_synthetic_call(client, args.email, password)
    sys.stdout.write(f"webhook status: {result['status']}; call id: {result['call_id']}\n")
    sys.stdout.write("Open the dashboard Calls page to see it.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
