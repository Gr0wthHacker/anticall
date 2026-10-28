"""Fake probes for doctor/CLI tests: no database server, no ARI, no network."""

from __future__ import annotations

from callshield.configreg.ext_settings import AriSettings
from callshield.doctor.model import AriProbe, DbStatus, DoctorDeps


def fake_deps(
    *,
    db: DbStatus | None = None,
    ari: AriProbe | None = None,
    missing_modules: tuple[str, ...] = (),
) -> DoctorDeps:
    db_status = db or DbStatus(True, True, "revision test")
    ari_probe = ari or AriProbe(True, "online", "ARI answered")

    def check_db(_url: str) -> DbStatus:
        return db_status

    def probe_ari(_ari: AriSettings, _trunk: str) -> AriProbe:
        return ari_probe

    def module_available(name: str) -> bool:
        return name not in missing_modules

    return DoctorDeps(check_db=check_db, probe_ari=probe_ari, module_available=module_available)
