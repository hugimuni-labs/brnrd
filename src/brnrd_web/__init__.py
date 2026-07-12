"""brnrd_web — the brnrd dashboard.

Copyright (C) 2026 HugiMuni SAS.

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or (at
your option) any later version. It is distributed WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU Affero General Public License (``LICENSE``
in this package) for details, and ``LICENSE-OVERVIEW.md`` at the repo root
for why this package is AGPL while the daemon core is MIT.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from starlette.staticfiles import StaticFiles

from .activity_dashboard import router as activity_dashboard_router
from .routes import router as legacy_router

router = APIRouter()
router.include_router(activity_dashboard_router)
router.include_router(legacy_router)

_STATIC_DIR = Path(__file__).with_name("static")


def mount_static(app) -> None:
    app.mount(
        "/static/brnrd_web",
        StaticFiles(directory=_STATIC_DIR),
        name="brnrd_static",
    )


__all__ = ["mount_static", "router"]
