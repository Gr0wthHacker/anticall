"""`callshield db upgrade`: run Alembic migrations to head."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from callshield.cli.context import CliContext
from callshield.cli.out import err, out
from callshield.doctor.deps import find_alembic_dir
from callshield.doctor.loader import load_config
from callshield.doctor.model import safe_validation_message


class MigrationsNotFoundError(RuntimeError):
    pass


def upgrade_database(db_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    alembic_dir = find_alembic_dir()
    if alembic_dir is None:
        raise MigrationsNotFoundError("alembic directory not found; run from a source checkout")
    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    command.upgrade(cfg, "head")


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("db", help="database commands")
    inner = p.add_subparsers(dest="db_command", required=True)
    inner.add_parser("upgrade", help="apply migrations to head")


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    try:
        cfg = load_config(Path(args.env_file) if args.env_file else None)
    except ValidationError as exc:
        err("invalid config: " + safe_validation_message(exc))
        return 2
    try:
        upgrade_database(cfg.settings.db_url)
    except MigrationsNotFoundError as exc:
        err(str(exc))
        return 2
    out("database is at head")
    return 0
