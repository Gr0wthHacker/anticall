"""Injectable collaborators for CLI commands (prompts, probes, process runner)."""

from __future__ import annotations

import getpass
import secrets
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from callshield.doctor.deps import default_deps
from callshield.doctor.model import DoctorDeps

InputFn = Callable[[str], str]
Runner = Callable[[list[str], Mapping[str, str]], int]


def _default_runner(argv: list[str], env: Mapping[str, str]) -> int:
    return subprocess.call(argv, env=dict(env))


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class CliContext:
    input_fn: InputFn = input
    secret_fn: InputFn = getpass.getpass
    doctor_deps: DoctorDeps = field(default_factory=default_deps)
    runner: Runner = _default_runner
    new_secret: Callable[[], str] = _new_secret
    fetch: Callable[[str], bytes] | None = None
