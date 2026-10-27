#!/usr/bin/env python3
"""Verify Postgres row-level security and ledger immutability after `alembic upgrade head`.

Needs only psycopg (v3). No repo imports. Run in CI by the `api-postgres` job.

Usage:
    CALLSHIELD_DB_URL=postgresql+psycopg://user:pass@host:5432/db python scripts/verify_postgres_rls.py

The URL must belong to a superuser. The script:
  1. creates a non-owner, non-superuser, NOBYPASSRLS role `callshield_app`
  2. seeds two tenants as superuser, with one row per sampled table per tenant
     (rows are built generically from information_schema, so new NOT NULL columns
     do not break the script; a CHECK constraint it cannot satisfy is reported by table)
  3. as `callshield_app` checks:
       (a) tenant A sees only tenant A rows
       (b) no tenant (unset or empty) sees nothing
       (c) UPDATE / DELETE on ledger_events fail (append-only trigger)
       (d) INSERT with a mismatched tenant_id fails the RLS policy

Exit codes: 0 all checks passed, 1 a check failed, 2 setup error.
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

APP_ROLE = "callshield_app"
APP_PASSWORD = "callshield_app_ci_only"  # throwaway role in a throwaway CI database
SAMPLE_TABLES = (
    "protected_lines",
    "call_sessions",
    "cases",
    "callback_requests",
    "evidence_artifacts",
    "ledger_events",
)
NOW, TODAY = "__now__", "__today__"

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)
    print(f"FAIL  {message}")


def check(condition: bool, passed: str, failed: str) -> None:
    if condition:
        print(f"PASS  {passed}")
    else:
        fail(failed)


def plain_url(raw: str) -> str:
    """SQLAlchemy URL (postgresql+psycopg://) to a libpq URL."""
    return raw.replace("postgresql+psycopg://", "postgresql://", 1)


def app_conninfo(raw: str) -> str:
    parsed = conninfo_to_dict(plain_url(raw))
    parsed["user"] = APP_ROLE
    parsed["password"] = APP_PASSWORD
    return make_conninfo(**parsed)


def placeholders(values: list[Any]) -> list[sql.Composable]:
    out: list[sql.Composable] = []
    for value in values:
        if isinstance(value, str) and value == NOW:
            out.append(sql.SQL("now()"))
        elif isinstance(value, str) and value == TODAY:
            out.append(sql.SQL("current_date"))
        else:
            out.append(sql.Placeholder())
    return out


def bound(values: list[Any]) -> list[Any]:
    return [v for v in values if not (isinstance(v, str) and v in (NOW, TODAY))]


# --- generic row builder (superuser seeding and the app-role insert probe) ---------------


class RowBuilder:
    def __init__(self, cur: psycopg.Cursor[Any]) -> None:
        self.cur = cur
        self.made: dict[tuple[str, str], Any] = {}

    def columns(self, table: str) -> list[dict[str, Any]]:
        self.cur.execute(
            """
            SELECT column_name, data_type, udt_name, character_maximum_length,
                   is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        names = [d.name for d in self.cur.description or []]
        return [dict(zip(names, row, strict=True)) for row in self.cur.fetchall()]

    def foreign_keys(self, table: str) -> dict[str, str]:
        """Single-column FKs: column -> referenced table."""
        self.cur.execute(
            """
            SELECT a.attname, cf.relname
            FROM pg_constraint c
            JOIN pg_class ct ON ct.oid = c.conrelid
            JOIN pg_class cf ON cf.oid = c.confrelid
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
            WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 1
              AND ct.relname = %s AND ct.relnamespace = 'public'::regnamespace
            """,
            (table,),
        )
        return {col: ref for col, ref in self.cur.fetchall()}

    def enum_label(self, udt_name: str) -> str:
        self.cur.execute(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = %s ORDER BY e.enumsortorder LIMIT 1",
            (udt_name,),
        )
        row = self.cur.fetchone()
        if row is None:
            raise RuntimeError(f"no enum labels for type {udt_name}")
        return str(row[0])

    def value_for(self, col: dict[str, Any]) -> Any:
        dtype, name = col["data_type"], col["column_name"]
        if dtype == "uuid":
            return uuid.uuid4()
        if dtype in ("character varying", "text", "character"):
            text = f"{name}-{uuid.uuid4().hex}"
            limit = col["character_maximum_length"]
            return text[-limit:] if limit else text
        if dtype in ("integer", "bigint", "smallint", "numeric", "double precision", "real"):
            return 1
        if dtype == "boolean":
            return False
        if dtype.startswith("timestamp"):
            return NOW
        if dtype == "date":
            return TODAY
        if dtype in ("json", "jsonb"):
            return Jsonb({})
        if dtype == "ARRAY":
            return []
        if dtype == "USER-DEFINED":
            return self.enum_label(col["udt_name"])
        raise RuntimeError(f"unsupported column type {dtype} for column {name}")

    def insert_tenant(self, tenant_id: uuid.UUID) -> None:
        """tenants has no tenant_id column; its own id is the tenant id."""
        names: list[str] = []
        values: list[Any] = []
        for col in self.columns("tenants"):
            if col["is_nullable"] == "NO" and col["column_default"] is None:
                names.append(col["column_name"])
                values.append(tenant_id if col["column_name"] == "id" else self.value_for(col))
        self._insert("tenants", names, values)

    def make(self, table: str, tenant_id: uuid.UUID, *, fresh_leaf: bool = False) -> Any:
        """Insert (or reuse) one row of `table` for `tenant_id`; parent rows are reused."""
        key = (table, str(tenant_id))
        if not fresh_leaf and key in self.made:
            return self.made[key]
        fks = self.foreign_keys(table)
        names: list[str] = []
        values: list[Any] = []
        row_id: Any = None
        for col in self.columns(table):
            name = col["column_name"]
            required = col["is_nullable"] == "NO" and col["column_default"] is None
            parent = fks.get(name)
            if name == "tenant_id" or (parent == "tenants" and required):
                names.append(name)
                values.append(tenant_id)
            elif parent is not None and parent != table and required:
                names.append(name)
                values.append(self.make(parent, tenant_id))
            elif required:
                value = self.value_for(col)
                names.append(name)
                values.append(value)
                if name == "id":
                    row_id = value
        self._insert(table, names, values)
        if not fresh_leaf:
            self.made[key] = row_id
        return row_id

    def _insert(self, table: str, names: list[str], values: list[Any]) -> None:
        stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, names)),
            sql.SQL(", ").join(placeholders(values)),
        )
        try:
            self.cur.execute(stmt, bound(values))
        except psycopg.Error as exc:
            raise RuntimeError(f"insert into {table} failed: {exc}".strip()) from exc


# --- setup -------------------------------------------------------------------------------


def setup_role(admin: psycopg.Connection[Any]) -> None:
    role = sql.Identifier(APP_ROLE)
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
        if cur.fetchone() is None:
            cur.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                ).format(role, sql.Literal(APP_PASSWORD))
            )
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        cur.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
                role
            )
        )
        cur.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role)
        )


def seed(admin: psycopg.Connection[Any]) -> tuple[RowBuilder, uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    builder = RowBuilder(admin.cursor())
    for tenant in (tenant_a, tenant_b):
        builder.insert_tenant(tenant)
        for table in SAMPLE_TABLES:
            builder.make(table, tenant)
    return builder, tenant_a, tenant_b


# --- checks ------------------------------------------------------------------------------


def set_tenant(cur: psycopg.Cursor[Any], tenant: str | None) -> None:
    if tenant is None:
        cur.execute("RESET app.tenant_id")
    else:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))


def visible_tenants(cur: psycopg.Cursor[Any], table: str) -> set[str]:
    cur.execute(sql.SQL("SELECT DISTINCT tenant_id::text FROM {}").format(sql.Identifier(table)))
    return {row[0] for row in cur.fetchall()}


def check_rls_enabled(admin: psycopg.Connection[Any]) -> None:
    with admin.cursor() as cur:
        for table in ("tenants", *SAMPLE_TABLES):
            cur.execute(
                "SELECT relrowsecurity FROM pg_class "
                "WHERE relname = %s AND relnamespace = 'public'::regnamespace",
                (table,),
            )
            row = cur.fetchone()
            if row is None:
                fail(f"{table}: table does not exist after migrations")
            elif table == "tenants":
                print(f"INFO  tenants: relrowsecurity={row[0]} (not in the migration RLS list)")
            else:
                check(bool(row[0]), f"{table}: RLS enabled", f"{table}: RLS is NOT enabled")


def check_isolation(app: psycopg.Connection[Any], a: uuid.UUID, b: uuid.UUID) -> None:
    with app.cursor() as cur:
        cur.execute(
            "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        user, is_super, bypass = cur.fetchone() or ("?", True, True)
        check(
            not is_super and not bypass,
            f"connected as non-privileged role {user}",
            f"role {user} is superuser/bypassrls; RLS would not apply",
        )
        for tenant, other in ((a, b), (b, a)):
            set_tenant(cur, str(tenant))
            for table in SAMPLE_TABLES:
                seen = visible_tenants(cur, table)
                check(
                    seen == {str(tenant)},
                    f"(a) {table}: tenant {str(tenant)[:8]} sees only its own rows",
                    f"(a) {table}: tenant {str(tenant)[:8]} sees {sorted(seen)} "
                    f"(expected only itself; other tenant is {str(other)[:8]})",
                )
        for label, value in (("unset", None), ("empty string", "")):
            set_tenant(cur, value)
            for table in SAMPLE_TABLES:
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                count = (cur.fetchone() or (-1,))[0]
                check(
                    count == 0,
                    f"(b) {table}: no tenant ({label}) sees 0 rows",
                    f"(b) {table}: no tenant ({label}) sees {count} rows",
                )
    app.rollback()


def check_ledger_immutable(app: psycopg.Connection[Any], a: uuid.UUID) -> None:
    attempts = {
        "UPDATE": "UPDATE ledger_events SET tenant_id = tenant_id",
        "DELETE": "DELETE FROM ledger_events",
    }
    for verb, statement in attempts.items():
        with app.cursor() as cur:
            set_tenant(cur, str(a))
            cur.execute("SELECT count(*) FROM ledger_events")
            if (cur.fetchone() or (0,))[0] == 0:
                fail(f"(c) ledger_events {verb}: no visible row to attack; seed problem")
                app.rollback()
                continue
            try:
                cur.execute(sql.SQL(statement))  # type: ignore[arg-type]
                fail(f"(c) ledger_events {verb} succeeded; it must be append-only")
            except psycopg.Error as exc:
                msg = str(exc).strip()
                check(
                    "append-only" in msg,
                    f"(c) ledger_events {verb} rejected by trigger",
                    f"(c) ledger_events {verb} rejected, but not by the trigger: {msg}",
                )
        app.rollback()


def attempt_insert(
    app: psycopg.Connection[Any], builder: RowBuilder, table: str, row_tenant: uuid.UUID
) -> tuple[bool, str]:
    with app.cursor() as cur:
        builder.cur = cur
        try:
            builder.make(table, row_tenant, fresh_leaf=True)
            return True, ""
        except RuntimeError as exc:
            return False, str(exc)
        finally:
            app.rollback()


def check_cross_tenant_insert(
    app: psycopg.Connection[Any], builder: RowBuilder, a: uuid.UUID, b: uuid.UUID
) -> None:
    for table in ("protected_lines", "cases", "callback_requests"):
        with app.cursor() as cur:
            set_tenant(cur, str(a))  # session is tenant A for both probes
        # positive control first, so a rejection below is the policy and not bad seed data
        inserted, err = attempt_insert(app, builder, table, a)
        check(
            inserted,
            f"(d) {table}: INSERT with matching tenant_id allowed",
            f"(d) {table}: positive control failed, so the check is inconclusive: {err}",
        )
        with app.cursor() as cur:
            set_tenant(cur, str(a))
        inserted, err = attempt_insert(app, builder, table, b)
        check(
            (not inserted) and "row-level security" in err,
            f"(d) {table}: INSERT with mismatched tenant_id rejected by policy",
            f"(d) {table}: mismatched INSERT "
            + ("was allowed" if inserted else f"failed for another reason: {err}"),
        )


def main() -> int:
    raw = os.environ.get("CALLSHIELD_DB_URL", "")
    if not raw.startswith("postgresql"):
        print("setup error: CALLSHIELD_DB_URL must be a postgresql URL", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(plain_url(raw), autocommit=True) as admin:
            setup_role(admin)
            check_rls_enabled(admin)
            builder, tenant_a, tenant_b = seed(admin)
        with psycopg.connect(app_conninfo(raw)) as app:
            check_isolation(app, tenant_a, tenant_b)
            check_ledger_immutable(app, tenant_a)
            check_cross_tenant_insert(app, builder, tenant_a, tenant_b)
    except (psycopg.Error, RuntimeError) as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        return 2
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("all Postgres RLS checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
