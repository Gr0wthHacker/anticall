"""Automatic router discovery.

Any module/package under callshield.api.routers exporting `router`
(an APIRouter) is mounted with NO extra prefix; routers declare full /v1/... paths.
"""

from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter, FastAPI

import callshield.api.routers as routers_pkg


def discover_routers() -> list[tuple[str, APIRouter]]:
    found: list[tuple[str, APIRouter]] = []
    prefix = routers_pkg.__name__ + "."
    for info in sorted(pkgutil.iter_modules(routers_pkg.__path__, prefix), key=lambda i: i.name):
        module = importlib.import_module(info.name)
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            found.append((info.name, router))
    return found


def include_discovered_routers(app: FastAPI) -> list[str]:
    names: list[str] = []
    for name, router in discover_routers():
        app.include_router(router)
        names.append(name)
    return names
