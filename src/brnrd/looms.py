"""Which of an account's daemons are looms, in one place.

A loom's daemon row has two births. `brnrd account connect` mints a token
with no `/register`, so the loom's first snapshot names the row
``LOOM_DAEMON_NAME``. `brnrd setup` registers right after the approval, so
its row carries the computer's hostname, and only its snapshot
(``loom_updated_at``, stamped even when the board lane refuses the copy)
says it is a loom. Reading the name alone missed every loom setup made.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Daemon, Repo

#: The loom names itself; `brnrd account connect` mints its token without a
#: `/register`, so the first snapshot creates the row.
LOOM_DAEMON_NAME = "loom"


def is_loom():
    """The SQL test for a loom's daemon row (see the module head)."""
    return or_(Daemon.daemon_name == LOOM_DAEMON_NAME, Daemon.loom_updated_at.is_not(None))


def loom_repos(db: Session, account_id: str) -> list[tuple[Repo, Daemon]]:
    """The account's loom repos with their loom daemon, the last seen first.

    A loom's first push makes its daemon row even when the push is refused, so a
    loom that never got a yes is found here too. Every install of one resident
    shares its self repo, so the repo is the resident; which install answers is
    the git inbox lease's call, on the installs, never the server's.
    """
    rows = db.execute(
        select(Repo, Daemon)
        .join(Daemon, Daemon.repo_id == Repo.id)
        .where(Repo.account_id == account_id, is_loom())
        .order_by(Daemon.last_seen_at.desc())
    ).all()
    return [(repo, daemon) for repo, daemon in rows]
