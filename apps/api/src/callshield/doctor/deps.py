"""Real (non-test) implementations of the doctor's external probes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
from sqlalchemy import Engine, text

from callshield.db.session import make_engine
from callshield.doctor.model import AriProbe, DbStatus, DoctorDeps
from callshield.telephony.asterisk.settings import AsteriskSettings


def find_alembic_dir() -> Path | None:
    """apps/api/alembic next to the source tree (editable install). None for wheel installs."""
    import callshield

    candidate = Path(callshield.__file__).resolve().parents[2] / "alembic"
    return candidate if (candidate / "env.py").exists() else None


def _head_revision(alembic_dir: Path) -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    return head or ""


def _missing_sqlite_file(db_url: str) -> bool:
    prefix = "sqlite:///"
    if not db_url.startswith(prefix) or db_url in ("sqlite://", "sqlite:///:memory:"):
        return False
    return not Path(db_url[len(prefix) :]).exists()


def check_engine(engine: Engine) -> DbStatus:
    """Reachability and migration state for an existing engine (does not dispose it)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            current = _current_revision(conn)
    except Exception as exc:
        return DbStatus(False, None, type(exc).__name__)
    alembic_dir = find_alembic_dir()
    if alembic_dir is None:
        return DbStatus(True, None, "migration scripts not found next to the package")
    return DbStatus(True, current == _head_revision(alembic_dir), f"revision {current or 'none'}")


def check_db(db_url: str) -> DbStatus:
    if _missing_sqlite_file(db_url):
        return DbStatus(True, False, "database file does not exist yet")
    try:
        engine = make_engine(db_url)
    except Exception as exc:
        return DbStatus(False, None, type(exc).__name__)
    try:
        return check_engine(engine)
    finally:
        engine.dispose()


def _current_revision(conn: object) -> str | None:
    from alembic.runtime.migration import MigrationContext

    return MigrationContext.configure(conn).get_current_revision()  # type: ignore[arg-type]


def probe_ari(ari: AsteriskSettings, trunk_name: str) -> AriProbe:
    """Ask ARI for its info and (best effort) the trunk endpoint state. Short timeout."""
    auth = (ari.ari_username, ari.ari_password.get_secret_value())
    base = f"{ari.ari_url}/ari"
    try:
        with httpx.Client(timeout=3.0, auth=auth) as client:
            info = client.get(f"{base}/asterisk/info")
            if info.status_code != 200:
                return AriProbe(False, None, f"HTTP {info.status_code}")
            state = _trunk_state(client, base, trunk_name)
    except httpx.HTTPError as exc:
        return AriProbe(False, None, type(exc).__name__)
    return AriProbe(True, state, "ARI answered")


def _trunk_state(client: httpx.Client, base: str, trunk_name: str) -> str | None:
    if not trunk_name:
        return None
    resp = client.get(f"{base}/endpoints/PJSIP/{trunk_name}")
    if resp.status_code != 200:
        return None
    state = resp.json().get("state")
    return str(state) if state else None


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def default_deps() -> DoctorDeps:
    return DoctorDeps(check_db=check_db, probe_ari=probe_ari, module_available=module_available)
