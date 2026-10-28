"""`callshield doctor`: PASS/WARN/FAIL checks with fix hints; exit 1 on any FAIL."""

from __future__ import annotations

import argparse
import json

from callshield.cli.context import CliContext
from callshield.cli.out import out
from callshield.doctor.engine import run_doctor_from_env


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("doctor", help="check this installation")
    p.add_argument("--json", action="store_true", help="machine-readable output")


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    report = run_doctor_from_env(args.env_file, ctx.doctor_deps)
    if args.json:
        out(json.dumps(report.as_dict(), indent=2))
        return 0 if report.ok else 1
    for result in report.results:
        out(
            f"[{result.status:<4}] {result.title}" + (f": {result.detail}" if result.detail else "")
        )
        if result.fix and result.status in ("WARN", "FAIL"):
            out(f"         fix: {result.fix}")
    counts = report.counts
    head = f"{counts['PASS']} pass, {counts['WARN']} warn, "
    out(f"\n{head}{counts['FAIL']} fail, {counts['SKIP']} skipped")
    return 0 if report.ok else 1
