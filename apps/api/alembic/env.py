"""Alembic environment. URL: -x db_url=..., config attribute, or CALLSHIELD_DB_URL."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from callshield.core.config import get_settings
from callshield.db import models  # noqa: F401
from callshield.db.base import Base
from callshield.db.session import make_engine

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return (
        context.get_x_argument(as_dictionary=True).get("db_url")
        or config.get_main_option("sqlalchemy.url")
        or get_settings().db_url
    )


def _configure_and_run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _configure_and_run(supplied)
        return
    engine = make_engine(_url())
    with engine.connect() as connection:
        _configure_and_run(connection)
        connection.commit()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
