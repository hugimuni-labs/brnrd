"""The loop's health: every step runs through one guard, and its streaks are facts.

The first failure of a step writes ``loom.degraded``; the first pass after
writes ``loom.recovered``. Between them the step backs off (1, 2, 4 … 300
calls) and only the first traceback reaches loom.log. Open streaks are read
back from the ledger at start, so a restart keeps them. A streak still open
after ``NOTICE_AFTER`` seconds is told to the home thread once.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact

from .ledger import LedgerConflict, append, kind_facts

MAX_BACKOFF = 300
NOTICE_AFTER = 300.0
_BRR = Path(__file__).resolve().parents[2]


@dataclass
class Streak:
    since: str
    error: str
    count: int = 1
    skip: int = 0
    told: bool = False


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def where(exc: BaseException) -> str:
    """The innermost frame inside brr/, as a path under it and a line."""
    found = ""
    for frame in traceback.extract_tb(exc.__traceback__):
        path = Path(frame.filename).resolve()
        if path.is_relative_to(_BRR):
            found = f"{path.relative_to(_BRR)}:{frame.lineno}"
    return found


def open_streaks(facts, install: str | None = None) -> list[Fact]:
    """``loom.degraded`` facts no ``loom.recovered`` has closed, oldest first."""
    closed = {f.id.replace("loom.recovered:", "loom.degraded:", 1)
              for f in kind_facts(facts, "loom.recovered")}
    return [f for f in kind_facts(facts, "loom.degraded") if f.id not in closed
            and (install is None or f.data.get("install") == install)]


def degraded_line(facts, install: str | None = None) -> str:
    """One boundary line: what is degraded now, since when, and where it broke."""
    parts = []
    for fact in open_streaks(facts, install):
        data = fact.data
        kind = str(data.get("error", "")).split(":", 1)[0]
        at = Path(str(data.get("where") or "")).name
        since = str(data.get("since", ""))[11:16]
        parts.append(f"{data.get('step')} since {since}Z ({kind}" + (f" at {at})" if at else ")"))
    return "degraded: " + "; ".join(parts) if parts else ""


class Health:
    """The guard ``run()`` passes each loop step through."""

    def __init__(self, home, facts, log):
        self.home, self.log = home, log
        self.install = home.install_id()
        self.streaks: dict[str, Streak] = {}
        for fact in open_streaks(facts, self.install):
            self.streaks[str(fact.data["step"])] = Streak(
                str(fact.data["since"]), str(fact.data.get("error", "")))

    def guard(self, step: str, fn, *args, backoff: bool = True) -> None:
        """``backoff=False`` for a step its caller already spaces out."""
        streak = self.streaks.get(step)
        if streak is not None and streak.skip > 0:
            streak.skip -= 1
            return
        try:
            fn(*args)
        except Exception as exc:
            self._failed(step, exc, backoff)
        else:
            if streak is not None:
                self._passed(step, streak)

    def _failed(self, step: str, exc: Exception, backoff: bool) -> None:
        error = f"{type(exc).__name__}: {exc}"[:300]
        streak = self.streaks.get(step)
        if streak is None:
            streak = self.streaks[step] = Streak(_iso(time.time()), error)
            self.log(self.home, f"degraded: {step}\n" + "".join(
                traceback.format_exception(exc)).rstrip())
            self._write("loom.degraded", step, streak.since, {
                "install": self.install, "step": step, "error": error,
                "where": where(exc), "since": streak.since})
        else:
            streak.count += 1
            streak.error = error
        if backoff:
            streak.skip = min(2 ** (streak.count - 1), MAX_BACKOFF)
        if not streak.told and time.time() - _seconds(streak.since) >= NOTICE_AFTER:
            streak.told = True
            self._tell(step, streak)

    def _passed(self, step: str, streak: Streak) -> None:
        del self.streaks[step]
        self.log(self.home, f"recovered: {step} after {streak.count} failures since {streak.since}")
        self._write("loom.recovered", step, streak.since, {
            "install": self.install, "step": step, "since": streak.since,
            "count": streak.count, "last": streak.error})

    def _write(self, kind: str, step: str, since: str, data: dict) -> None:
        # The guard's own bookkeeping must never be what stops the loop.
        try:
            append(self.home, Fact(kind=kind, by=f"loom:{self.install}", data=data,
                                   id=f"{kind}:{self.install}:{step}:{since}"))
        except LedgerConflict:
            pass
        except Exception:
            self.log(self.home, f"health: {kind} {step} not written\n{traceback.format_exc().rstrip()}")

    def _tell(self, step: str, streak: Streak) -> None:
        from .ledger import read_facts
        from .loom import notice
        try:
            notice(self.home, read_facts(self.home), "degraded", f"{step}:{streak.since}",
                   f"the loom's {step} step has failed since {streak.since}, "
                   f"{streak.count} times: {streak.error}",
                   f"the traceback is in loom/loom.log; the step retries with backoff "
                   f"and loom.recovered says when it passes "
                   f"(fact loom.degraded:{self.install}:{step}:{streak.since})",
                   thread=self.home.main_thread())
        except Exception:
            self.log(self.home, f"health: notice for {step} not sent\n{traceback.format_exc().rstrip()}")
