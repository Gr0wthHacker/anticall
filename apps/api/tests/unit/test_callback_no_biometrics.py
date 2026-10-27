"""Voice biometrics are prohibited (spec "Data handling"): dependency + source audit."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = API_ROOT / "src"
THIS_FILE = Path(__file__).resolve()

# Built by concatenation so this audit file never contains the banned tokens verbatim.
_BANNED = [
    "resemb" + "lyzer",
    "speech" + "brain",
    "pyan" + "note",
    "wespe" + "aker",
    "tita" + "net",
    "ecapa",
    "x-" + "vector",
    "d-" + "vector",
    "voice" + "print",
    "speaker" + "_embedding",
    "speaker" + "-embedding",
    "speaker" + "_id",
    "speaker" + "-id",
    "speaker" + "_verification",
    "voice" + "_embedding",
    "voice" + "_similarity",
    "voice" + "-biometric",
    "voice" + "_biometric",
]
_PATTERN = re.compile("|".join(re.escape(token) for token in _BANNED), re.IGNORECASE)


def _dependency_text() -> str:
    data = tomllib.loads((API_ROOT / "pyproject.toml").read_text(encoding="utf8"))
    project = data.get("project", {})
    deps = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        deps.extend(group)
    for group in data.get("dependency-groups", {}).values():
        deps.extend(str(item) for item in group)
    return "\n".join(deps)


def test_pyproject_declares_no_banned_voice_libraries() -> None:
    hits = _PATTERN.findall(_dependency_text())
    assert not hits, f"voice-biometric dependency declared: {hits}"


def test_source_tree_has_no_banned_voice_symbols() -> None:
    offenders: dict[str, list[str]] = {}
    for path in SRC_ROOT.rglob("*.py"):
        if path.resolve() == THIS_FILE:
            continue
        found = _PATTERN.findall(path.read_text(encoding="utf8", errors="ignore"))
        if found:
            offenders[str(path.relative_to(API_ROOT))] = sorted(set(found))
    assert not offenders, f"voice-biometric symbols present: {offenders}"


def test_installed_environment_has_no_banned_voice_packages() -> None:
    from importlib.metadata import distributions

    names = {(d.metadata["Name"] or "").lower() for d in distributions()}
    installed = [n for n in names if _PATTERN.search(n)]
    assert not installed, f"voice-biometric packages installed: {installed}"


def test_test_tree_only_mentions_banned_tokens_in_this_audit_or_docs() -> None:
    tests_root = API_ROOT / "tests"
    offenders = [
        str(p.relative_to(API_ROOT))
        for p in tests_root.rglob("*.py")
        if p.resolve() != THIS_FILE and _PATTERN.search(p.read_text(encoding="utf8"))
    ]
    assert not offenders, offenders
