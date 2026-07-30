#!/usr/bin/env python3
"""Row-count parity between a database dump and a live database.

The Upsun -> Scaleway cutover (``plan-upsun-to-scaleway-cutover.md``, phases 2
and 4) ends with one question: *did everything that matters arrive?* Answering
it by hand is two ``psql -c "select count(*)"`` calls against the two tables
somebody remembered, under time pressure, inside a maintenance window. This
script makes it a go/no-go with an exit code.

Two sources, either or both:

* ``--dump FILE`` — a ``pg_dump`` plain-text dump (``.sql`` or ``.sql.gz``, the
  shape ``upsun db:dump --gzip`` produces). Counts the rows in each ``COPY``
  block. No credentials, no network: the dump is the authority on what left the
  old host.
* ``--url URL`` — a SQLAlchemy URL. Counts ``select count(*)`` per table.

One source prints a census. Two sources diff them and **exit 1 on any
mismatch**.

The critical set is derived, not listed. ``_REGENERABLE`` names the tables that
rebuild themselves within a heartbeat of a daemon reconnecting
(``design-backend-deployment-portability.md`` -> "The database, honestly
split"); *everything else* is critical, so a table added after this file was
written is fail-loud by default rather than silently unwatched. ``--all``
widens the check to the regenerable tables too — right for phase 4, where the
source is frozen and any drift is a real defect.
"""

from __future__ import annotations

import argparse
import gzip
import re
import sys
from pathlib import Path

# Tables that a reconnecting daemon repopulates on its own. Everything not
# named here counts as legally or financially load-bearing.
_REGENERABLE = frozenset(
    {
        "activity_records",
        "channel_routes",
        "config_change_requests",
        "daemons",
        "events",
        "github_installed_repos",
        "pair_requests",
        "run_stop_requests",
        "runner_wake_requests",
        "tg_pair_codes",
    }
)

_COPY = re.compile(r"^COPY\s+(?:\"?[\w]+\"?\.)?\"?([\w]+)\"?\s")


def counts_from_dump(path: Path) -> dict[str, int]:
    """Rows per table in a plain-text ``pg_dump``, by walking its COPY blocks."""
    opener = gzip.open if path.suffix == ".gz" else open
    counts: dict[str, int] = {}
    table: str | None = None
    rows = 0
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if table is not None:
                if line.startswith("\\."):
                    counts[table] = rows
                    table, rows = None, 0
                else:
                    rows += 1
                continue
            match = _COPY.match(line)
            if match:
                table, rows = match.group(1), 0
    if table is not None:  # truncated dump: a COPY block that never closed
        raise SystemExit(f"{path}: unterminated COPY block for {table!r} — dump is truncated")
    return counts


def counts_from_url(url: str) -> dict[str, int]:
    """Rows per table in a live database, for the tables this codebase declares."""
    from sqlalchemy import create_engine, func, select

    from brnrd.models import Base

    engine = create_engine(url)
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for name, table in Base.metadata.tables.items():
            counts[name] = conn.execute(select(func.count()).select_from(table)).scalar_one()
    return counts


def render(counts: dict[str, int], label: str) -> None:
    print(f"# {label} — {len(counts)} tables, {sum(counts.values())} rows")
    for name in sorted(counts):
        mark = " " if name in _REGENERABLE else "*"
        print(f"{counts[name]:>9}  {mark} {name}")


def compare(left: dict[str, int], right: dict[str, int], *, check_all: bool) -> int:
    names = sorted(set(left) | set(right))
    watched = [n for n in names if check_all or n not in _REGENERABLE]
    bad = [n for n in watched if left.get(n) != right.get(n)]
    for name in watched:
        a, b = left.get(name), right.get(name)
        flag = "MISMATCH" if a != b else "ok"
        print(f"{'' if a is None else a:>9} {'' if b is None else b:>9}  {flag:>8}  {name}")
    skipped = [n for n in names if n not in watched]
    if skipped:
        print(f"# not checked ({len(skipped)} regenerable): {', '.join(skipped)}")
        print("# --all checks these too — use it for the phase 4 cutover.")
    if bad:
        print(f"\nFAIL — {len(bad)} table(s) differ: {', '.join(bad)}")
        return 1
    print(f"\nOK — {len(watched)} table(s) match row for row.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dump", type=Path, action="append", default=[], help="pg_dump .sql or .sql.gz")
    parser.add_argument("--url", action="append", default=[], help="SQLAlchemy database URL")
    parser.add_argument("--all", action="store_true", help="check regenerable tables too")
    args = parser.parse_args(argv)

    sources: list[tuple[str, dict[str, int]]] = []
    for path in args.dump:
        sources.append((f"dump {path.name}", counts_from_dump(path)))
    for url in args.url:
        sources.append((f"db {url.rsplit('@', 1)[-1]}", counts_from_url(url)))

    if not sources:
        parser.error("give at least one --dump or --url")
    if len(sources) == 1:
        render(sources[0][1], sources[0][0])
        return 0
    if len(sources) > 2:
        parser.error("compare exactly two sources")

    (left_label, left), (right_label, right) = sources
    print(f"# {left_label}  vs  {right_label}   (* = must match)")
    return compare(left, right, check_all=args.all)


if __name__ == "__main__":
    sys.exit(main())
