"""Read the seven-day scar history from the loom's scar log."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .home import home_for
from .notes import _when
from .scar_log import LOG, WINDOW, history
from .refusals import Parser


@dataclass
class Counts:
    fired: int = 0
    failed: int = 0
    last_at: float = 0
    last_line: str = ""


def report(path: Path, now: float) -> str:
    since, rows = history(path, now)
    cutoff = now - WINDOW
    lines = ["Scar displays over the last 7 days"]
    if since is None:
        lines.append("No scar displays recorded yet.")
    elif since > cutoff:
        lines.append(f"Recording began {_when(since)}.")

    counts: dict[str, Counts] = {}
    outcomes = {row["call"]: row for row in rows if "paths" not in row and row["at"] <= now}
    for call in rows:
        if "paths" not in call or not cutoff <= call["at"] <= now:
            continue
        outcome = outcomes.get(call["call"], {})
        for scar in call["paths"]:
            count = counts.setdefault(scar, Counts())
            count.fired += 1
            if "failure" in outcome:
                count.failed += 1
                if outcome["at"] >= count.last_at:
                    count.last_at = outcome["at"]
                    count.last_line = outcome["failure"]

    lines.append("\nScars shown before a failed call")
    failing = sorted((scar for scar in counts if counts[scar].failed),
                     key=lambda scar: (-counts[scar].failed, scar))
    for scar in failing:
        count = counts[scar]
        last = count.last_line or "(no failure line recorded)"
        lines.append(f"{scar}: {count.fired} fired, {count.failed} failed"
                     f" | {_when(count.last_at)} | {last}")
    if not failing:
        lines.append("None recorded.")

    lines.append("\nMost-fired scars (up to 20)")
    for scar in sorted(counts, key=lambda scar: (-counts[scar].fired, scar))[:20]:
        lines.append(f"{scar}: {counts[scar].fired} fired")
    if not counts:
        lines.append("None recorded.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = Parser(verb="scars", prog="brnrd runtime scars")
    parser.add_argument("--home", help="loom home to read")
    args = parser.parse_args(argv)
    home = home_for(args.home)
    print(report(home.root / "loom" / LOG, time.time()))
    return 0
