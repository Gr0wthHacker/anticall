#!/usr/bin/env python3
"""Policy guard: fail CI when source contains constructs the project forbids.

Rules (see PRD 3.2, ADR 0003, CONTRIBUTING safety rules):
  PG001  bulk/scheduled mass dialing helpers (``schedule_bulk_calls`` and kin)
  PG002  caller ID spoofing / override constructs
  PG003  dial capability inside the ai_agent package (LLM must not dial)

Scope: files under ``apps/`` and ``packages/``. Tests, docs, fixtures and
markdown are exempt, since they may name forbidden constructs to prove they are
blocked. A line containing ``policy-guard: allow`` is skipped (use sparingly;
requires security-owner review via CODEOWNERS).

Usage: python scripts/policy_guard.py [repo_root]
Exit code 0 = clean, 1 = violations found.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

SCAN_ROOTS = ("apps", "packages")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs"}
SKIP_DIRS = {
    "node_modules", ".next", ".venv", "venv", "__pycache__", "dist", "build",
    ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache",
}  # fmt: skip
EXEMPT_DIR_NAMES = {"tests", "test", "__tests__", "fixtures", "docs", "red-team", "legal-policy"}
ALLOW_MARKER = "policy-guard: allow"

BULK_PATTERN = re.compile(
    r"\b(schedule_bulk_calls|bulk_dial\w*|mass_dial\w*|auto_redial\w*|rapid_redial\w*)\b"
)
SPOOF_PATTERN = re.compile(
    r"\b(caller_id_override|override_caller_id|set_caller_id|fake_caller_id|"
    r"spoof_caller_id|spoof_cli|spoof_number|enable_spoofing|spoof)\b\s*(\(|=)"
    r"|\bdef\s+spoof\w*"
)
AI_AGENT_PATH = re.compile(r"(^|/)(ai_agent|ai-agent)(/|$)")
DIAL_PATTERN = re.compile(
    r"\b(dial|originate|place_call|make_call|start_outbound_call)\s*\("
    r"|[\"'](name|tool)[\"']\s*:\s*[\"'](dial|originate|place_call|make_call)[\"']"
)


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    line: int
    text: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.text.strip()}"


def is_exempt(rel_path: str) -> bool:
    """True for tests/docs/fixtures/markdown paths that may name forbidden constructs."""
    parts = rel_path.split("/")
    name = parts[-1]
    if any(p in EXEMPT_DIR_NAMES for p in parts[:-1]):
        return True
    return name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name


def scan_text(rel_path: str, text: str) -> list[Violation]:
    """Scan one file's text. ``rel_path`` is POSIX-style relative to the repo root."""
    if is_exempt(rel_path):
        return []
    in_ai_agent = bool(AI_AGENT_PATH.search(rel_path))
    found: list[Violation] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        if BULK_PATTERN.search(line):
            found.append(Violation("PG001", rel_path, number, line))
        if SPOOF_PATTERN.search(line):
            found.append(Violation("PG002", rel_path, number, line))
        if in_ai_agent and DIAL_PATTERN.search(line):
            found.append(Violation("PG003", rel_path, number, line))
    return found


def iter_source_files(repo_root: Path) -> Iterator[Path]:
    for root_name in SCAN_ROOTS:
        base = repo_root / root_name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
                continue
            yield path


def scan_tree(repo_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in iter_source_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            violations.append(Violation("PG000", rel, 0, f"unreadable: {exc}"))
            continue
        violations.extend(scan_text(rel, text))
    return violations


def main(argv: Iterable[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    repo_root = Path(args[0]).resolve() if args else Path(__file__).resolve().parent.parent
    violations = scan_tree(repo_root)
    if violations:
        sys.stderr.write(f"policy_guard: {len(violations)} violation(s)\n")
        for item in violations:
            sys.stderr.write(item.format() + "\n")
        return 1
    sys.stdout.write("policy_guard: clean\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
