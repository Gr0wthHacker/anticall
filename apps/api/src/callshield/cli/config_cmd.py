"""`callshield config show|validate`. Secrets are always redacted."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from callshield.cli.context import CliContext
from callshield.cli.out import err, out
from callshield.configreg.registry import SETTINGS_CLASSES, all_rows
from callshield.configreg.validate import semantic_problems
from callshield.doctor.loader import load_config
from callshield.doctor.model import LoadedConfig, safe_validation_message

REDACTED_SET = "***set***"


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("config", help="show or validate configuration")
    inner = p.add_subparsers(dest="config_command", required=True)
    show = inner.add_parser("show", help="print effective config (secrets redacted)")
    show.add_argument("--json", action="store_true")
    inner.add_parser("validate", help="check the config loads and is consistent")


def effective_values(cfg: LoadedConfig) -> dict[str, str]:
    by_class = {type(m): m for m in cfg.all_models()}
    values: dict[str, str] = {}
    for row in all_rows():
        model = next(by_class[c] for c in SETTINGS_CLASSES if c.__name__ == row.group)
        raw = getattr(model, row.field)
        if row.secret:
            secret = raw.get_secret_value() if raw is not None else ""
            values[row.env] = REDACTED_SET if secret else "(empty)"
        else:
            values[row.env] = json.dumps(raw) if isinstance(raw, list) else str(raw)
    return values


def _load(args: argparse.Namespace) -> LoadedConfig | None:
    try:
        return load_config(Path(args.env_file) if args.env_file else None)
    except ValidationError as exc:
        err("invalid config: " + safe_validation_message(exc))
        return None


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    cfg = _load(args)
    if cfg is None:
        return 1
    if args.config_command == "show":
        values = effective_values(cfg)
        if args.json:
            out(json.dumps(values, indent=2, sort_keys=True))
        else:
            for env, value in values.items():
                out(f"{env}={value}")
        return 0
    problems = semantic_problems(cfg)
    for problem in problems:
        err(f"problem: {problem}")
    out("config is valid" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0
