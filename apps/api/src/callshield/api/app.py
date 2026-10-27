"""Application factory."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from callshield import __version__
from callshield.api.discovery import include_discovered_routers
from callshield.api.errors import install_error_handlers
from callshield.core.config import Settings, get_settings
from callshield.db.session import make_engine, make_session_factory

REQUEST_ID_HEADER = "X-Request-ID"


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = incoming if 0 < len(incoming) <= 64 and incoming.isascii() else uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


def install_cors(app: FastAPI, settings: Settings) -> None:
    """CORS only for explicitly configured origins. Auth is a bearer header, so no cookies."""
    if not settings.cors_allow_origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="CallShield Network API",
        version=__version__,
        docs_url="/docs" if settings.environment != "prod" else None,
        redoc_url=None,
    )
    engine = engine or make_engine(settings.db_url, settings.db_echo)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.middleware("http")(request_id_middleware)
    install_error_handlers(app)
    install_cors(app, settings)  # added last so it is outermost and answers preflights
    app.state.mounted_routers = include_discovered_routers(app)
    return app
