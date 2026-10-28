"""`callshield user create-admin`."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from callshield.cli.admin_service import AdminCreateError, create_admin
from callshield.cli.context import CliContext
from callshield.cli.out import err, out
from callshield.db.session import make_engine, make_session_factory, session_scope
from callshield.doctor.loader import load_config
from callshield.doctor.model import safe_validation_message


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("user", help="user management")
    inner = p.add_subparsers(dest="user_command", required=True)
    admin = inner.add_parser("create-admin", help="create a tenant and its first admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--tenant-name", default="My household")


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    try:
        cfg = load_config(Path(args.env_file) if args.env_file else None)
    except ValidationError as exc:
        err("invalid config: " + safe_validation_message(exc))
        return 2
    password = ctx.secret_fn("Admin password (min 12 chars, not shown): ")
    confirm = ctx.secret_fn("Repeat password: ")
    if password != confirm:
        err("passwords do not match")
        return 1
    engine = make_engine(cfg.settings.db_url)
    try:
        with session_scope(make_session_factory(engine)) as session:
            create_admin(session, args.tenant_name, args.email, password)
    except AdminCreateError as exc:
        err(str(exc))
        return 1
    finally:
        engine.dispose()
    out(f"created admin {args.email.strip().lower()}")
    return 0
