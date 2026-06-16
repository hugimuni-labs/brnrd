"""FastAPI application factory for the brnrd backend."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .config import Settings, get_settings
from .db import Base, make_engine, make_session_factory
from .inbox import Forwarder, make_default_forwarder
from .migrations import run_startup_migrations
from .pack_relay import PackRelayStore
from .routers import accounts, daemons, dev, github_app, pairing, render, webhooks


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>brnrd</title>
  <link rel="stylesheet" href="/static/brnrd_web/app.css">
</head>
<body class="auth-page">
  <main class="auth-shell">
    <section class="auth-card">
      <p class="eyebrow">brnrd</p>
      <h1>brnrd is running</h1>
      <p class="lede">
        The managed control plane is online. Sign in with GitHub to approve
        daemon connections and manage project bindings.
      </p>
      <p><a class="button" href="/login">Open login</a></p>
    </section>
  </main>
</body>
</html>
"""


def create_app(
    settings: Settings | None = None,
    *,
    forwarder: Forwarder | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="brnrd", version="0.1.0")

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    run_startup_migrations(engine)

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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        return _INDEX_HTML

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": "brnrd"}

    return app
