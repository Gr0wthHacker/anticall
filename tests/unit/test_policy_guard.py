"""Unit tests for scripts/policy_guard.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "policy_guard", Path(__file__).resolve().parents[2] / "scripts" / "policy_guard.py"
)
assert _SPEC and _SPEC.loader
policy_guard = importlib.util.module_from_spec(_SPEC)
sys.modules["policy_guard"] = policy_guard
_SPEC.loader.exec_module(policy_guard)


def rules(path: str, text: str) -> list[str]:
    return [v.rule for v in policy_guard.scan_text(path, text)]


def test_bulk_dialer_flagged() -> None:
    assert rules("apps/api/src/x.py", "schedule_bulk_calls(numbers)") == ["PG001"]


def test_spoof_construct_flagged() -> None:
    assert rules("apps/api/src/x.py", "caller_id_override = '+15550100'") == ["PG002"]
    assert rules("apps/api/src/x.py", "def spoof_it(): ...") == ["PG002"]


def test_spoof_detection_names_not_flagged() -> None:
    assert rules("apps/api/src/x.py", "is_spoofed(call)\nspoof_suspected = True") == []


def test_dial_in_ai_agent_flagged() -> None:
    assert rules("apps/api/src/callshield/ai_agent/tools.py", "client.dial(num)") == ["PG003"]
    assert rules("packages/ai-agent/tools.ts", '{"name": "dial"}') == ["PG003"]


def test_dial_outside_ai_agent_allowed() -> None:
    assert rules("apps/api/src/callshield/telephony/outbound.py", "dial(dest)") == []


def test_tests_and_docs_exempt() -> None:
    assert rules("apps/api/tests/unit/test_x.py", "schedule_bulk_calls()") == []
    assert rules("apps/api/src/callshield/ai_agent/test_tools.py", "dial(x)") == []
    assert rules("apps/api/src/docs/x.py", "schedule_bulk_calls()") == []


def test_allow_marker_skips_line() -> None:
    assert rules("apps/api/src/x.py", "schedule_bulk_calls()  # policy-guard: allow") == []


def test_scan_tree_clean_and_dirty(tmp_path: Path) -> None:
    (tmp_path / "apps" / "api" / "src").mkdir(parents=True)
    (tmp_path / "apps" / "api" / "src" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert policy_guard.main([str(tmp_path)]) == 0
    (tmp_path / "apps" / "api" / "src" / "bad.py").write_text(
        "schedule_bulk_calls()\n", encoding="utf-8"
    )
    assert policy_guard.main([str(tmp_path)]) == 1
