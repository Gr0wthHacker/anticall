"""Run all checks, build a report. Shared by the CLI and GET /v1/system/doctor."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from callshield.doctor.checks import ALL_CHECKS
from callshield.doctor.deps import default_deps
from callshield.doctor.loader import load_config
from callshield.doctor.model import (
    CheckResult,
    DoctorDeps,
    LoadedConfig,
    safe_validation_message,
)


@dataclass(frozen=True)
class DoctorReport:
    results: list[CheckResult]

    @property
    def counts(self) -> dict[str, int]:
        out = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
        for result in self.results:
            out[result.status] += 1
        return out

    @property
    def ok(self) -> bool:
        return self.counts["FAIL"] == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "counts": self.counts,
            "checks": [r.as_dict() for r in self.results],
        }


def run_checks(cfg: LoadedConfig, deps: DoctorDeps | None = None) -> DoctorReport:
    active = deps or default_deps()
    results: list[CheckResult] = []
    for check in ALL_CHECKS:
        try:
            results.append(check(cfg, active))
        except Exception as exc:  # a broken check must not hide the others
            name = check.__name__.removeprefix("check_")
            results.append(CheckResult(name, name, "FAIL", f"check crashed: {type(exc).__name__}"))
    return DoctorReport(results)


def run_doctor_from_env(env_file: str | None, deps: DoctorDeps | None = None) -> DoctorReport:
    from pathlib import Path

    try:
        cfg = load_config(Path(env_file) if env_file else None)
    except ValidationError as exc:
        return DoctorReport(
            [
                CheckResult(
                    "config",
                    "Configuration valid",
                    "FAIL",
                    safe_validation_message(exc),
                    "Fix the listed variables (see docs/deployment/configuration-reference.md).",
                )
            ]
        )
    return run_checks(cfg, deps)
