"""FastAPI application factory for the brnrd backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from brr import __version__
from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from .config import Settings, get_settings
from .db import Base, make_engine, make_session_factory
from .inbox import Forwarder, make_default_forwarder
from .migrations import run_startup_migrations
from .pack_relay import PackRelayStore
from .routers import accounts, billing, config_approval, daemons, dev, github_app, pairing, render, stats, webhooks
from .routers import dashboard as dashboard_router
from .routers import repo_actions as repo_actions_router
from .routers import web_auth as web_auth_router
from .security_headers import HSTSMiddleware
from .spa import backend_namespaces, declared_paths, mount_frontend, resolve_frontend_dir

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

    # Same source as `brnrd --version` and the dashboard's `daemon_version`
    # (#674): this one is published in the OpenAPI schema, so a literal here
    # is a hand-maintained copy that goes stale in public.
    app = FastAPI(title="brnrd", version=__version__, lifespan=lifespan)

    # Transport security travels with the image, not with a host's route
    # table — the 2026-07-31 finding is written up in `security_headers.py`.
    app.add_middleware(HSTSMiddleware, max_age=settings.hsts_max_age)

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

    # One list, in registration order — and the same object the SPA fallback
    # reads its namespace set from below (#847). Anything appended here is
    # claimed by the backend automatically; there is no second list to update.
    routers = [
        accounts.router,
        billing.router,
        pairing.router,
        config_approval.router,
        daemons.router,
        render.router,
        webhooks.router,
        github_app.router,
        stats.router,
    ]
    if settings.enable_dev_endpoints:
        routers.append(dev.router)
    # Dashboard routers (migrated from src/brnrd_web into src/brnrd/routers/).
    routers += [dashboard_router.router, repo_actions_router.router, web_auth_router.router]
    for router in routers:
        app.include_router(router)

    # Static assets (app.css, dashboard.css) are served from src/brnrd/static/
    # under the same URL prefix (/static/brnrd_web/) so deployed CDN cache keys
    # and existing client references remain byte-compatible.
    app.mount(
        "/static/brnrd_web",
        StaticFiles(directory=_STATIC_DIR),
        name="brnrd_static",
    )

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": "brnrd"}

    # Last, on purpose: the SvelteKit build is served by this process, not by
    # whatever router happens to sit in front of it (#847). Registration order
    # is the priority order, so every route declared above still wins and only
    # unmatched requests fall through to the SPA shell. See spa.py for why the
    # backend/SPA boundary is derived from the route table rather than listed.
    # Published on app.state so it is readable without a build directory —
    # tests/test_spa_serving.py asserts it stays disjoint from the SvelteKit
    # route tree, which is the check that keeps the boundary honest.
    app.state.backend_namespaces = backend_namespaces(declared_paths(app, routers))
    frontend_dir = resolve_frontend_dir(settings.frontend_dir)
    if frontend_dir is not None:
        mount_frontend(app, frontend_dir, app.state.backend_namespaces)

    return app
