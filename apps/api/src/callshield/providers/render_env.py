"""Pure renderers for ``.env`` and ``env.example``, generated from the settings classes."""

from __future__ import annotations

from collections.abc import Mapping

from callshield.configreg.registry import ConfigRow, all_rows

REQUIRED_SECRETS = frozenset(
    {
        "CALLSHIELD_JWT_SECRET",
        "CALLSHIELD_WEBHOOK_SECRET",
        "CALLSHIELD_CALLBACK_HMAC_SECRET",
        "CALLSHIELD_ARI_PASSWORD",
    }
)
EXAMPLE_ANSWERS: dict[str, str] = {"CALLSHIELD_CORS_ALLOW_ORIGINS": '["http://127.0.0.1:3000"]'}
EXTRA_VARS: tuple[tuple[str, str, str], ...] = (
    ("POSTGRES_USER", "callshield", "Postgres container user (compose only)."),
    ("POSTGRES_PASSWORD", "CHANGE_ME", "Postgres container password (compose only)."),
    ("POSTGRES_DB", "callshield", "Postgres container database (compose only)."),
    ("NEXT_PUBLIC_API_BASE_URL", "http://127.0.0.1:8000", "Dashboard -> API URL (build time)."),
)
HEADER = (
    "# CallShield Network environment.\n"
    "# Keep this file private (chmod 600) and NEVER commit it.\n"
    "# Every risky capability is OFF by default. Turning one on does not make it lawful:\n"
    "# see docs/legal/release-gates.md and docs/deployment/going-live-checklist.md.\n"
)


class EnvValueError(ValueError):
    """A value that cannot be written to a dotenv file safely."""


def quote_env_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise EnvValueError("env values cannot contain newlines or NUL")
    if any(ch in value for ch in " #\"'$"):
        if "'" in value:
            raise EnvValueError("env values with spaces or '#' cannot also contain a quote")
        return f"'{value}'"
    return value


def _comment(row: ConfigRow) -> list[str]:
    bits = [f"default: {row.default}"]
    if row.range_text:
        bits.append(f"range: {row.range_text}")
    if row.gate:
        bits.append(f"gate: {row.gate}")
    return [f"# {row.description}", f"#   ({'; '.join(bits)}; changed by: {row.who})"]


def _line(row: ConfigRow, answers: Mapping[str, str]) -> str:
    if row.env in answers:
        return f"{row.env}={quote_env_value(str(answers[row.env]))}"
    if row.env in REQUIRED_SECRETS:
        return f"{row.env}=CHANGE_ME"
    if row.secret or row.raw_default in (None, "", []):
        return f"# {row.env}="
    return f"{row.env}={quote_env_value(_plain_default(row))}"


def _plain_default(row: ConfigRow) -> str:
    return row.default


def render_env(answers: Mapping[str, str]) -> str:
    """Return .env text with EVERY setting, comments, and ``answers`` applied."""
    rows = all_rows()
    out = [HEADER]
    group = ""
    for row in rows:
        if row.group != group:
            group = row.group
            out.append(f"\n# ===== {group} =====")
        out.extend(_comment(row))
        out.append(_line(row, answers))
    out.append("\n# ===== Docker compose / dashboard (not read by the API) =====")
    for name, default, text in EXTRA_VARS:
        out.append(f"# {text}")
        out.append(f"{name}={quote_env_value(str(answers.get(name, default)))}")
    return "\n".join(out) + "\n"


def render_env_example() -> str:
    return render_env(EXAMPLE_ANSWERS)


def known_env_names() -> set[str]:
    names = {row.env for row in all_rows()}
    names.update(name for name, _, _ in EXTRA_VARS)
    return names
