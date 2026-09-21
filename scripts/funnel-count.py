#!/usr/bin/env python3
"""Daily funnel table from the server-side page-view counters.

Reads ``page_view_counts`` (written by ``brnrd.page_views``) — a counter table
with no per-visit rows, no IP, no query string. Read-only.

    BRNRD_DATABASE_URL=postgresql://… python scripts/funnel-count.py
    python scripts/funnel-count.py --day 2026-09-19 --days 7 --include-bots

Prints one row per (day, path): views · top-5 referer hosts. Bot-family
user-agents are excluded unless ``--include-bots``. Only 200/304 are counted at
write time, so ``views`` are pages actually served.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from collections import Counter, defaultdict

# Funnel order first, then everything else alphabetically.
_FUNNEL = ("/", "/pricing", "/learn", "/log")


def collect(session, *, start: dt.date, end: dt.date, include_bots: bool):
    from sqlalchemy import select

    from brnrd.page_views import PageViewCount as P

    stmt = select(P.day, P.path, P.referer_host, P.ua_family, P.views).where(P.day >= start, P.day <= end)
    if not include_bots:
        stmt = stmt.where(P.ua_family != "bot")
    views: dict[tuple[dt.date, str], int] = defaultdict(int)
    refs: dict[tuple[dt.date, str], Counter] = defaultdict(Counter)
    for day, path, host, _ua, n in session.execute(stmt):
        views[(day, path)] += n
        refs[(day, path)][host or "(direct)"] += n
    return views, refs


def render(views, refs) -> str:
    def order(key):
        day, path = key
        rank = _FUNNEL.index(path) if path in _FUNNEL else len(_FUNNEL)
        return (day, rank, path)

    lines = [f"{'day':<10}  {'path':<28} {'views':>6}  referers (top 5)"]
    for key in sorted(views, key=order):
        top = ", ".join(f"{h}×{n}" for h, n in refs[key].most_common(5))
        lines.append(f"{key[0].isoformat():<10}  {key[1]:<28} {views[key]:>6}  {top}")
    if len(lines) == 1:
        lines.append("(no rows)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", default=os.environ.get("BRNRD_DATABASE_URL", "sqlite:///./brnrd.db"))
    ap.add_argument("--day", help="last day, YYYY-MM-DD (default: yesterday, UTC)")
    ap.add_argument("--days", type=int, default=1, help="window length ending at --day (default 1)")
    ap.add_argument("--include-bots", action="store_true")
    args = ap.parse_args(argv)

    end = dt.date.fromisoformat(args.day) if args.day else dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=max(args.days, 1) - 1)

    from brnrd.db import make_engine, make_session_factory

    factory = make_session_factory(make_engine(args.database_url))
    with factory() as session:
        views, refs = collect(session, start=start, end=end, include_bots=args.include_bots)
    print(render(views, refs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
