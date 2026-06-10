"""brnrd_web — the brnrd dashboard (AGPLv3; see ``LICENSE``).

Thin HTMX-ready web surface bundled into the ``brr[backend]`` extra
and served from the brnrd app. This first slice carries just login +
the device-flow approve page so ``brr brnrd connect`` is usable by a
human; the fuller dashboard (projects, tasks, vault) follows per
``kb/plan-brnrd-dashboard-mvp.md``.
"""

from __future__ import annotations

from pathlib import Path

from starlette.staticfiles import StaticFiles

from .routes import router

_STATIC_DIR = Path(__file__).with_name("static")


def mount_static(app) -> None:
    """Mount brnrd_web's packaged assets on the parent FastAPI app."""
    app.mount(
        "/static/brnrd_web",
        StaticFiles(directory=_STATIC_DIR),
        name="brnrd_static",
    )


__all__ = ["mount_static", "router"]
