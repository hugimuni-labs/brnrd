"""The loop's health: every step runs through one guard, and its streaks are facts.

The first failure of a step writes ``loom.degraded``; the first pass after
writes ``loom.recovered``. Between them a step that does not pace its own
retries backs off (1, 2, 4 … 300 calls), and only the first traceback
reaches loom.log. Open streaks are read back from the ledger at start, so a
restart keeps them.

Streaks of one step less than ``CLEAR_AFTER`` apart are one episode: a disk
that dips under the floor and climbs back every half hour is one standing
condition, not a new one per dip. A streak still open after ``NOTICE_AFTER``
seconds tells the home thread once per episode, again only when its cause
(the error's type and frame) changes or its grade worsens, and an episode
that was told and then stays up ``CLEAR_AFTER`` seconds ends with one passive
"cleared" notice, owed by nobody.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact

from .ledger import LedgerConflict, append, kind_facts

MAX_BACKOFF = 300
NOTICE_AFTER = 300.0
CLEAR_AFTER = 4 * 3600.0
_BRR = Path(__file__).resolve().parents[2]


@dataclass
class Streak:
    since: str
    error: str
    count: int = 1
    skip: int = 0
    told: bool = False


@dataclass
class Episode:
    """Streaks of one step that follow each other within ``CLEAR_AFTER``."""
    since: str
    dips: int = 1
    told: str = ""  # the cause last told, "" while untold
    grade: int = 0  # the grade last told
    up: float = 0.0  # when the last streak recovered, 0 while one is open


def cause(error: str, at: str) -> str:
    """What an episode tells by: the error's type and the frame it broke in, never its reading."""
    kind = error.split(":", 1)[0]
    return f"{kind} at {Path(at).name}" if at else kind


def grade(exc: BaseException) -> int:
    """How bad a reading is, in steps; an error that carries none is grade 0."""
    value = getattr(exc, "grade", 0)
    return value if isinstance(value, int) else 0


def _iso(seconds: float) -> str:
    # Milliseconds: a step that recovers and fails again opens a streak of its own.
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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


def _up(back: Fact) -> float:
    return _seconds(str(back.data.get("up") or back.at))


def _episodes(facts, install: str | None, now: float) -> dict[str, tuple[Fact, Fact | None]]:
    """Per step, its latest streak and that streak's recovery, while the episode stands.

    An episode stands while a streak is open or its recovery is younger than
    ``CLEAR_AFTER``; a recovered one stands only once the told cause rides on it,
    so the gauge keeps what a body was told about and drops a dip nobody heard of.
    """
    recovered = {f.id.replace("loom.recovered:", "loom.degraded:", 1): f
                 for f in kind_facts(facts, "loom.recovered")
                 if install is None or f.data.get("install") == install}
    latest: dict[str, Fact] = {}
    for fact in kind_facts(facts, "loom.degraded"):
        if install is None or fact.data.get("install") == install:
            step = str(fact.data.get("step"))
            if step not in latest or str(fact.data.get("since")) >= str(latest[step].data.get("since")):
                latest[step] = fact
    standing = {}
    for step, fact in latest.items():
        back = recovered.get(fact.id)
        if back is None or (back.data.get("told") and now - _up(back) < CLEAR_AFTER):
            standing[step] = (fact, back)
    return standing


def acted(facts, install: str | None, since: str) -> str:
    """What the loom did about the disk since an episode began: reaped paths and pruned caches."""
    start, reaped, pruned, freed = _seconds(since), 0, 0, 0
    for kind in ("room.reaped", "loom.pruned"):
        for fact in kind_facts(facts, kind):
            if (install is not None and fact.by != f"loom:{install}") or _seconds(fact.at) < start:
                continue
            freed += int(fact.data.get("bytes") or 0)
            if kind == "room.reaped":
                reaped += 1
            else:
                pruned += 1
    done = ([f"reaped {reaped} path{'s' * (reaped > 1)}"] if reaped else []) + (
        [f"pruned caches{f' {pruned}x' if pruned > 1 else ''}"] if pruned else [])
    return f"; did: {', '.join(done)}, {freed / 1024 ** 3:.1f} GB freed" if done else ""


def degraded_line(facts, install: str | None = None, now: float | None = None) -> str:
    """The gauge: per standing condition, since when, where it broke, and how often it dipped."""
    parts = []
    standing = _episodes(facts, install, time.time() if now is None else now)
    for step, (fact, back) in sorted(standing.items(), key=lambda kv: str(kv[1][0].data.get("since"))):
        data = fact.data
        kind = cause(str(data.get("error", "")), str(data.get("where") or ""))
        dips = int(data.get("dips") or 1)
        first = str(data.get("episode") or data.get("since", ""))[11:16]
        since = str(data.get("since", ""))[11:16]
        did = acted(facts, install, str(data.get("episode") or data.get("since"))) if step == "disk" else ""
        if back is None:
            part = f"{step} since {since}Z ({kind}"
            part += (f"; dip {dips} since {first}Z" if dips > 1 else "") + did + ")"
        else:
            part = (f"{step} up since {_iso(_up(back))[11:16]}Z after {dips} dip{'s' * (dips > 1)} "
                    f"since {first}Z ({kind}{did}; told, watching)")
        parts.append(part)
    return "degraded: " + "; ".join(parts) if parts else ""


def _about(about) -> dict:
    try:
        return dict(about()) if about is not None else {}
    except Exception:
        return {}


class Health:
    """The guard ``run()`` passes each loop step through."""

    def __init__(self, home, facts, log):
        self.home, self.log = home, log
        self.install = home.install_id()
        self.streaks: dict[str, Streak] = {}
        self.episodes: dict[str, Episode] = {}
        for fact in open_streaks(facts, self.install):
            self.streaks[str(fact.data["step"])] = Streak(
                str(fact.data["since"]), str(fact.data.get("error", "")))
        # A restart inside an episode keeps it: its next dip tells nothing new.
        for step, (fact, back) in _episodes(facts, self.install, time.time()).items():
            data = fact.data
            self.episodes[step] = Episode(
                str(data.get("episode") or data.get("since")), int(data.get("dips") or 1),
                str((back.data if back else {}).get("told") or ""),
                int((back.data if back else {}).get("grade") or 0),
                _up(back) if back else 0.0)

    def guard(self, step: str, fn, *args, backoff: bool = True, overrun: float | None = None,
              about=None) -> None:
        """``backoff=False`` for a step its caller already spaces out. ``overrun``
        is how long the step may run: past it, a watchdog writes ``loom.degraded``
        for ``<step>.overrun`` while the step still runs, and its end writes
        ``loom.recovered`` with how long it ran (2026-10-25: a nine-minute tick left
        no fact). ``about()`` adds fields to both, such as the live-body count."""
        streak = self.streaks.get(step)
        if streak is not None and streak.skip > 0:
            streak.skip -= 1
            return
        watchdog = None
        if overrun is not None:
            begun = time.monotonic()
            watchdog = threading.Timer(overrun, self._overran, (step, overrun, about))
            watchdog.daemon = True
            watchdog.start()
        try:
            fn(*args)
        except Exception as exc:
            self._failed(step, exc, backoff)
        else:
            if streak is not None:
                self._passed(step, streak)
            else:
                episode = self.episodes.get(step)
                if episode is not None and episode.up and time.time() - episode.up >= CLEAR_AFTER:
                    del self.episodes[step]
                    if episode.told:
                        self._cleared(step, episode)
        finally:
            if watchdog is not None:
                watchdog.cancel()
                watchdog.join()
                late = self.streaks.get(step + ".overrun")
                if late is not None:
                    self._passed(step + ".overrun", late,
                                 {"seconds": round(time.monotonic() - begun, 3), **_about(about)})

    def _overran(self, step: str, limit: float, about) -> None:
        try:
            raise TimeoutError(f"{step} still running after {limit:g}s, past the router lease")
        except TimeoutError as exc:
            self._failed(step + ".overrun", exc, backoff=False, extra={"seconds": limit, **_about(about)})

    def _failed(self, step: str, exc: Exception, backoff: bool, extra: dict | None = None) -> None:
        error = f"{type(exc).__name__}: {exc}"[:300]
        streak = self.streaks.get(step)
        now = time.time()
        if streak is None:
            streak = self.streaks[step] = Streak(_iso(now), error)
            episode = self.episodes.get(step)
            if episode is not None and (not episode.up or now - episode.up < CLEAR_AFTER):
                episode.dips += 1
                episode.up = 0.0
            else:
                episode = self.episodes[step] = Episode(streak.since)
            if episode.dips == 1:
                self.log(self.home, f"degraded: {step}\n" + "".join(
                    traceback.format_exception(exc)).rstrip())
            else:
                self.log(self.home, f"degraded: {step} again, dip {episode.dips} since {episode.since}: {error}")
            self._write("loom.degraded", step, streak.since, {
                "install": self.install, "step": step, "error": error,
                "where": where(exc), "since": streak.since,
                "episode": episode.since, "dips": episode.dips, **(extra or {})})
        else:
            streak.count += 1
            streak.error = error
        if backoff:
            streak.skip = min(2 ** (streak.count - 1), MAX_BACKOFF)
        if not streak.told and now - _seconds(streak.since) >= NOTICE_AFTER:
            streak.told = True
            episode = self.episodes.setdefault(step, Episode(streak.since))
            said, worse = cause(error, where(exc)), grade(exc)
            if said != episode.told or worse > episode.grade:
                episode.told, episode.grade = said, max(worse, episode.grade if said == episode.told else 0)
                self._tell(step, streak, episode, said)

    def _passed(self, step: str, streak: Streak, extra: dict | None = None) -> None:
        del self.streaks[step]
        episode = self.episodes.setdefault(step, Episode(streak.since))
        episode.up = time.time()
        self.log(self.home, f"recovered: {step} after {streak.count} failures since {streak.since}")
        self._write("loom.recovered", step, streak.since, {
            "install": self.install, "step": step, "since": streak.since,
            "count": streak.count, "last": streak.error,
            "episode": episode.since, "told": episode.told, "grade": episode.grade,
            "up": _iso(episode.up), **(extra or {})})

    def _write(self, kind: str, step: str, since: str, data: dict) -> None:
        # The guard's own bookkeeping must never be what stops the loop.
        try:
            append(self.home, Fact(kind=kind, by=f"loom:{self.install}", data=data,
                                   id=f"{kind}:{self.install}:{step}:{since}"))
        except LedgerConflict:
            pass
        except Exception:
            self.log(self.home, f"health: {kind} {step} not written\n{traceback.format_exc().rstrip()}")

    def _tell(self, step: str, streak: Streak, episode: Episode, said: str) -> None:
        from .ledger import read_facts
        from .loom import notice
        dips = (f", dip {episode.dips} of an episode since {episode.since}" if episode.dips > 1 else "")
        try:
            # The id is the episode, the cause and the grade: a restart or a dip with nothing new writes nothing.
            notice(self.home, read_facts(self.home), "degraded",
                   f"{step}:{episode.since}:{said}:{episode.grade}",
                   f"the loom's {step} step has failed since {streak.since}{dips}, "
                   f"{streak.count} times: {streak.error}",
                   f"the traceback is in loom/loom.log; the step retries with backoff, the "
                   f"degraded: line on the boundary is its gauge, and further dips of the same "
                   f"cause stay silent until it worsens or clears "
                   f"(fact loom.degraded:{self.install}:{step}:{streak.since})",
                   thread=self.home.main_thread())
        except Exception:
            self.log(self.home, f"health: notice for {step} not sent\n{traceback.format_exc().rstrip()}")

    def _cleared(self, step: str, episode: Episode) -> None:
        from .ledger import read_facts
        from .loom import notice
        try:
            notice(self.home, read_facts(self.home), "cleared", f"{step}:{episode.since}",
                   f"the loom's {step} step is clear: up since {_iso(episode.up)} after "
                   f"{episode.dips} dip{'s' * (episode.dips > 1)} since {episode.since} ({episode.told})",
                   "nothing to do", thread=self.home.main_thread(), passive=True)
        except Exception:
            self.log(self.home, f"health: cleared notice for {step} not sent\n{traceback.format_exc().rstrip()}")
