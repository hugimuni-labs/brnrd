"""FastAPI application factory for the brnrd backend."""

from __future__ import annotations

from fastapi import FastAPI

from .config import Settings, get_settings
from .db import Base, make_engine, make_session_factory
from .inbox import Forwarder, make_default_forwarder
from .pack_relay import PackRelayStore
from .routers import accounts, daemons, dev, github_app, pairing, render, webhooks


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
    # without being persisted. Default dispatches to the configured
    # platform (Telegram today); tests install a capturing forwarder.
    app.state.forwarder = forwarder or make_default_forwarder(settings)
    # Transient, RAM-only relay for diffense review packs. Never touches
    # the database — brnrd renders a relayed pack, it does not store it.
    app.state.pack_relay = PackRelayStore(default_ttl_s=settings.pack_relay_ttl_s)

    app.include_router(accounts.router)
    app.include_router(pairing.router)
    app.include_router(daemons.router)
    app.include_router(render.router)
    app.include_router(webhooks.router)
    app.include_router(github_app.router)
    if settings.enable_dev_endpoints:
        app.include_router(dev.router)

    # The dashboard (src/brnrd_web) is part of the brr[backend] extra.
    from brnrd_web import mount_static, router as web_router

    mount_static(app)
    app.include_router(web_router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": "brnrd"}

    return app
