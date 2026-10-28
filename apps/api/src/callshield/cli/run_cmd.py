"""`callshield run api|worker|telephony`: thin wrappers that say what they start."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from callshield.cli.context import CliContext
from callshield.cli.out import err, out

LOOPBACK = ("127.0.0.1", "localhost", "::1")
TELEPHONY_MODULE = "callshield.telephony.asterisk"


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("run", help="start a component in the foreground")
    p.add_argument("component", choices=["api", "worker", "telephony"])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--allow-public", action="store_true", help="permit non-localhost --host")


def _module_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def build_command(args: argparse.Namespace) -> list[str] | None:
    py = sys.executable
    if args.component == "api":
        return [
            py,
            "-m",
            "uvicorn",
            "callshield.api.app:create_app",
            "--factory",
            "--host",
            args.host,
            "--port",
            str(args.port),
        ]
    if args.component == "worker":
        return [py, "-m", "callshield.worker"]
    return [py, "-m", TELEPHONY_MODULE] if _module_exists(TELEPHONY_MODULE) else None


def _environment(env_file: str | None) -> dict[str, str]:
    env = dict(os.environ)
    path = Path(env_file) if env_file else Path(".env")
    if path.is_file():
        for key, value in dotenv_values(path).items():
            env.setdefault(key, value or "")
    return env


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    if args.component == "api" and args.host not in LOOPBACK and not args.allow_public:
        err("refusing to bind a non-localhost host without --allow-public (use a reverse proxy)")
        return 2
    command = build_command(args)
    if command is None:
        err(f"telephony runner ({TELEPHONY_MODULE}) is not available in this build")
        return 2
    out("Starting " + args.component + ": " + " ".join(command[1:]))
    return ctx.runner(command, _environment(args.env_file))
