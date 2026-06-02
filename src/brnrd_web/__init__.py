"""brnrd_web — the brnrd dashboard (AGPLv3; see ``LICENSE``).

Thin HTMX-ready web surface bundled into the ``brr[backend]`` extra
and served from the brnrd app. This first slice carries just login +
the device-flow approve page so ``brr brnrd connect`` is usable by a
human; the fuller dashboard (projects, tasks, vault) follows per
``kb/plan-brnrd-dashboard-mvp.md``.
"""

from __future__ import annotations

from .routes import router

__all__ = ["router"]
