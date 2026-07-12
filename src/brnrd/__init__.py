"""brnrd — the managed backend.

Copyright (C) 2026 HugiMuni SAS.

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or (at
your option) any later version. It is distributed WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU Affero General Public License (``LICENSE``
in this package) for details, and ``LICENSE-OVERVIEW.md`` at the repo root
for why this package is AGPL while the daemon core is MIT.

This is the inbox-as-service spine: accounts, projects, a device-flow
connect handshake, and the daemon-facing register / long-poll / respond /
deregister loop that the daemon's ``cloud`` gate drains. See
``kb/plan-brnrd-inbox-prototype.md``.
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
