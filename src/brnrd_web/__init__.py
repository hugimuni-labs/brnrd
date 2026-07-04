"""brnrd_web dashboard package."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from starlette.staticfiles import StaticFiles

from .activity_dashboard import router as activity_dashboard_router
from .plans_dashboard import router as plans_dashboard_router
from .routes import router as legacy_router

router = APIRouter()
router.include_router(activity_dashboard_router)
router.include_router(plans_dashboard_router)
router.include_router(legacy_router)

_STATIC_DIR = Path(__file__).with_name("static")


def mount_static(app) -> None:
    app.mount(
        "/static/brnrd_web",
        StaticFiles(directory=_STATIC_DIR),
        name="brnrd_static",
    )


__all__ = ["mount_static", "router"]
