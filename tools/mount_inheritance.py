#!/usr/bin/env python3
"""What does a forged boot transcript actually hand a successor?

The measurement `.brr/reports/the-other-door.md` (2026-09-18) was written from,
kept runnable because the claim it settles rots: `boot.mount` forges a claude
session (:mod:`brr.transcript`) and `--fork-session`-mounts it, and the only
honest answer to *"how much of the predecessor rode in"* is the file itself.

Two modes, because there are two questions and they have different evidence:

``audit``   read every forged seed on this machine and classify its perceptions.
            A seed is brnrd's, not claude's, by file mode: ``Path.write_text``
            leaves 644 where claude's own session writer leaves 600. That is a
            heuristic about two umasks, **not a contract** — if it ever stops
            holding, this tool's corpus is wrong and its output should be read
            as "no evidence of", never "no".

``blocks``  run the two production builders for the ``prior-run`` block against
            real node directories and report the delta. This is the part that
            does not depend on the heuristic above: mounting a wake does not
            merely move that block out of the prose, it **substitutes the
            builder's text** (`prompts._MOUNTABLE_TEXT_BUILDERS`) — the
            predecessor's whole `body.md` where the prose carried its map.

Why a successor's small `session-start` reading is not evidence of a cold boot:
the two doors differ by three orders of magnitude (a live seat scroll measured
39.5 MB / 7,442 rows against a 46 KB / 14-row seed), while the predecessor's
node is 1-6 % of the seed. The token count detects a *native resume* and is
blind to everything this tool measures.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

# The product's own wake files, wherever they are read from: a source checkout
# (`src/brr/prompts/…`) or an installed venv (`site-packages/brr/prompts/…`).
# Matching only the checkout spelling classified 24 venv-era seeds as carrying
# inherited content — caught by running this against the full history rather
# than the window the report was written from.
PRODUCT_MARKERS = ("/brr/prompts/", "/brr/docs/")


def _seeds(projects_root: Path):
    for path in sorted(projects_root.glob("*/*.jsonl")):
        try:
            if (path.stat().st_mode & 0o777) != 0o644:
                continue
        except OSError:
            continue
        yield path


def _perceptions(path: Path):
    """[(location, result_bytes)] for a seed, or [] if it is not one."""
    out, pending = [], None
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, ValueError):
        return []
    for row in rows:
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            return []  # a plain text turn: never something this module forged
        for block in content:
            if block.get("type") == "tool_use":
                pending = block.get("input", {}).get("file_path")
            elif block.get("type") == "tool_result" and pending:
                out.append((pending, len(block.get("content") or "")))
                pending = None
    return out


def audit(projects_root: Path) -> int:
    seeds = list(_seeds(projects_root))
    rows = [(p, _perceptions(p)) for p in seeds]
    rows = [(p, x) for p, x in rows if x]
    if not rows:
        print(f"no forged seeds under {projects_root}")
        return 1
    foreign, totals, shares = [], [], []
    locations: dict[str, int] = {}
    for path, perceptions in rows:
        total = sum(n for _, n in perceptions)
        totals.append(total)
        carried = [(loc, n) for loc, n in perceptions
                   if not any(m in loc for m in PRODUCT_MARKERS)]
        for loc, _ in perceptions:
            locations[loc] = locations.get(loc, 0) + 1
        if carried:
            foreign.append((path, carried))
            shares.append(sum(n for _, n in carried) / total)
    print(f"forged seeds            : {len(rows)}")
    print(f"carrying a non-product read: {len(foreign)}")
    print(f"seed bytes  median      : {int(statistics.median(totals)):,}")
    if shares:
        print(f"inherited share median  : {statistics.median(shares) * 100:.1f} %")
    print("\nevery distinct mounted location:")
    for loc, count in sorted(locations.items(), key=lambda kv: -kv[1]):
        mark = "  " if any(m in loc for m in PRODUCT_MARKERS) else "<-"
        print(f"{count:5}{mark} {loc.replace(os.path.expanduser('~'), '~')}")
    return 0


def blocks(repo_root: Path, nodes_root: Path, limit: int) -> int:
    sys.path.insert(0, str(repo_root / "src"))
    from brr import prompts  # noqa: PLC0415 — the point is to run production code

    candidates = sorted(
        (d for d in nodes_root.iterdir()
         if (d / "body.md").exists() and (d / "state.md").exists()),
        key=lambda d: d.stat().st_mtime,
    )[-limit:]
    if not candidates:
        print(f"no run nodes under {nodes_root}")
        return 1
    original = prompts._prior_run_node
    factors = []
    print(f"{'node':28} {'prose':>8} {'mount':>8} {'delta':>9} {'x':>6}")
    try:
        for node in candidates:
            prompts._prior_run_node = lambda _r, _n=node: (_n / "state.md", _n / "body.md")
            prose = len(prompts._build_prior_run_block(repo_root).encode())
            mount = len(prompts._build_prior_run_mount_text(repo_root).encode())
            if prose:
                factors.append(mount / prose)
            print(f"{node.name:28} {prose:8,} {mount:8,} {mount - prose:+9,} "
                  f"{(mount / prose if prose else 0):6.2f}")
    finally:
        prompts._prior_run_node = original
    if factors:
        print(f"\nmedian factor: {statistics.median(factors):.2f}x — "
              f"the wake pays this much more of the predecessor when it mounts")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("audit", "blocks"))
    parser.add_argument("--projects-root", type=Path,
                        default=Path.home() / ".claude" / "projects")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--nodes-root", type=Path,
                        help="home/runs/<repo-label>/ — required for `blocks`")
    parser.add_argument("--limit", type=int, default=16)
    args = parser.parse_args(argv)
    if args.mode == "audit":
        return audit(args.projects_root)
    if args.nodes_root is None:
        parser.error("blocks needs --nodes-root (…/home/runs/<repo-label>)")
    return blocks(args.repo_root, args.nodes_root, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
