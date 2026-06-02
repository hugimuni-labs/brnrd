"""brnrd — the managed backend for brr (AGPLv3; see ``LICENSE``).

This is the inbox-as-service spine prototype: accounts, projects,
a device-flow connect handshake, and the daemon-facing
register / long-poll / respond / deregister loop that the brr
daemon's ``cloud`` gate drains. See
``kb/plan-brnrd-inbox-prototype.md``.
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
