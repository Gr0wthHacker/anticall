"""Entry point: `callshield <command>`. Each command lives in its own module."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from callshield.cli import (
    config_cmd,
    db_cmd,
    doctor_cmd,
    init_cmd,
    run_cmd,
    user_cmd,
    voice_cmd,
)
from callshield.cli.context import CliContext

Handler = Callable[[argparse.Namespace, CliContext], int]
COMMANDS: dict[str, tuple[object, Handler]] = {
    "init": (init_cmd, init_cmd.run),
    "doctor": (doctor_cmd, doctor_cmd.run),
    "config": (config_cmd, config_cmd.run),
    "voice": (voice_cmd, voice_cmd.run),
    "db": (db_cmd, db_cmd.run),
    "user": (user_cmd, user_cmd.run),
    "run": (run_cmd, run_cmd.run),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="callshield", description="CallShield setup and operations. Not legal advice."
    )
    parser.add_argument("--env-file", help="read settings from this file instead of ./.env")
    sub = parser.add_subparsers(dest="command", required=True)
    for module, _ in COMMANDS.values():
        module.register(sub)  # type: ignore[attr-defined]
    return parser


def main(argv: Sequence[str] | None = None, ctx: CliContext | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse exits on bad usage; surface it as a return code
        return int(exc.code) if isinstance(exc.code, int) else 2
    _, handler = COMMANDS[args.command]
    return handler(args, ctx or CliContext())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
