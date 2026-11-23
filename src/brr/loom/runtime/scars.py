"""Read the seven-day scar history from the loom's hand record."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .home import home_for
from .notes import HANDS, SCAR_WINDOW, _read_hands, _SCARS, _when
from .refusals import Parser


@dataclass
class Counts:
    fired: int = 0
    failed: int = 0
    last_at: float = 0
    last_line: str = ""


def report(path: Path, now: float) -> str:
    history = _read_hands(path).get(_SCARS, {})
    cutoff = now - SCAR_WINDOW
    lines = ["Scar displays over the last 7 days"]
    since = history.get("since")
    if since is None:
        lines.append("No scar displays recorded yet.")
    elif since > cutoff:
        lines.append(f"Recording began {_when(since)}.")

    counts: dict[str, Counts] = {}
    for call in history.get("calls", {}).values():
        if not cutoff <= call["at"] <= now:
            continue
        for scar in call["paths"]:
            count = counts.setdefault(scar, Counts())
            count.fired += 1
            if "failure" in call and call["ended"] <= now:
                count.failed += 1
                if call["ended"] >= count.last_at:
                    count.last_at = call["ended"]
                    count.last_line = call["failure"]

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
    print(report(home.root / "loom" / HANDS, time.time()))
    return 0
