"""FastAPI application factory for the brnrd backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from .config import Settings, get_settings
from .db import Base, make_engine, make_session_factory
from .inbox import Forwarder, make_default_forwarder
from .migrations import run_startup_migrations
from .pack_relay import PackRelayStore
from .routers import accounts, billing, config_approval, daemons, dev, github_app, pairing, render, webhooks
from .routers import dashboard as dashboard_router
from .routers import repo_actions as repo_actions_router
from .routers import web_auth as web_auth_router

_STATIC_DIR = Path(__file__).with_name("static")


def _maybe_register_telegram_webhook(settings: Settings) -> None:
    if not settings.telegram_auto_webhook:
        return
    if not (settings.telegram_bot_token and settings.telegram_webhook_secret):
        return
    base = settings.public_base_url.rstrip("/")
    if not base.startswith("https://"):
        return
    from .platforms import telegram

    url = f"{base}/v1/webhooks/telegram"
    try:
        telegram.set_webhook(
            settings.telegram_bot_token,
            url,
            secret_token=settings.telegram_webhook_secret,
            timeout=10.0,
        )
    except Exception as e:
        print(f"[brnrd] telegram webhook registration failed: {e}")


def create_app(
    settings: Settings | None = None,
    *,
    forwarder: Forwarder | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _maybe_register_telegram_webhook(settings)
        yield

    app = FastAPI(title="brnrd", version="0.1.0", lifespan=lifespan)

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
    app.include_router(billing.router)
    app.include_router(pairing.router)
    app.include_router(config_approval.router)
    app.include_router(daemons.router)
    app.include_router(render.router)
    app.include_router(webhooks.router)
    app.include_router(github_app.router)
    if settings.enable_dev_endpoints:
        app.include_router(dev.router)

    # Dashboard routers (migrated from src/brnrd_web into src/brnrd/routers/).
    # Static assets (app.css, dashboard.css) are served from src/brnrd/static/
    # under the same URL prefix (/static/brnrd_web/) so deployed CDN cache keys
    # and existing client references remain byte-compatible.
    app.mount(
        "/static/brnrd_web",
        StaticFiles(directory=_STATIC_DIR),
        name="brnrd_static",
    )
    app.include_router(dashboard_router.router)
    app.include_router(repo_actions_router.router)
    app.include_router(web_auth_router.router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": "brnrd"}

    return app
