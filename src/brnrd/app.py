"""FastAPI application factory for the brnrd backend."""

from __future__ import annotations

from fastapi import FastAPI

from .config import Settings, get_settings
from .db import Base, make_engine, make_session_factory
from .inbox import Forwarder, default_forwarder
from .routers import accounts, daemons, dev, pairing


def create_app(
    settings: Settings | None = None,
    *,
    forwarder: Forwarder | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="brnrd", version="0.1.0")

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)

    app.state.settings = settings
    app.state.engine = engine
    app.state.SessionLocal = make_session_factory(engine)
    # The forwarder is the seam where a response body leaves brnrd
    # without being persisted. Default is a no-op; tests install a
    # capturing forwarder; production wires the platform post.
    app.state.forwarder = forwarder or default_forwarder

    app.include_router(accounts.router)
    app.include_router(pairing.router)
    app.include_router(daemons.router)
    if settings.enable_dev_endpoints:
        app.include_router(dev.router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": "brnrd"}

    return app
