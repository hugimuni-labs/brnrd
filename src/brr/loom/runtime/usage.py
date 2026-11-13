"""Optional body fuel, refreshed off the tick and cached per home and shell."""

import fcntl
import json
import math
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .adapters import resolve
from .home import atomic_write
from .selfrepo import _env_without_pin

CACHE_TTL = 300
REFRESH_INTERVAL = 600
PROBE_INTERVAL = 60
# A refresh still running past this reads as unknown fuel, so a wanted body
# starts: on 10-31 one blocked refresh held every wake pending for ten hours.
STUCK_AFTER = 120
_REFRESHING = set()
_STARTED: dict[Path, float] = {}


class StuckRefresh(RuntimeError):
    """A usage refresh that has not returned within ``STUCK_AFTER``."""


def shells(home):
    names = set()
    for root in (Path(__file__).parent / "bodies", home.root / "loom/bodies"):
        if root.is_dir():
            names.update(p.name for p in root.iterdir() if p.is_dir())
    for name in sorted(names):
        try:
            folder = resolve(home, name)
        except ValueError:
            continue
        if (folder / "usage").is_file():
            yield name


def print_windows(rows, *, error: str | None = None) -> None:
    windows = [{"name": name, "left_pct": left,
                "resets_at": datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset else None}
               for name, left, reset in rows if left is not None]
    if not windows and not error:
        raise SystemExit(1)
    print(json.dumps({"windows": windows, **({"error": error} if error else {})}))


def _refresh(path, script, after=0) -> None:
    at, windows = time.time(), []
    error = None
    proc = None
    claim = None
    attempted = False
    try:
        # The persistent file records attempts, including collectors that fail
        # or whose jack process exits. flock releases on process death, so no
        # stale claim can survive the collector's 15-second lifetime.
        path.parent.mkdir(parents=True, exist_ok=True)
        claim = path.with_suffix(".lock").open("a+")
        try:
            fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        claim.seek(0)
        previous = claim.read().strip()
        if previous and float(previous) >= after and 0 <= at - float(previous) < PROBE_INTERVAL:
            return
        claim.seek(0)
        claim.truncate()
        claim.write(str(at))
        claim.flush()
        attempted = True
        env = _env_without_pin()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3]) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen([str(script)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=env, start_new_session=True)
        out, _ = proc.communicate(timeout=15)
        if proc.returncode == 0:
            payload = json.loads(out)
            if isinstance(payload.get("error"), str):
                error = payload["error"]
            for window in payload["windows"]:
                left = window["left_pct"]
                if (isinstance(left, (int, float)) and math.isfinite(left) and left <= 100
                        and isinstance(window.get("name"), str)):
                    windows.append(window)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        windows = []
    finally:
        try:
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                # A child stuck in the kernel ignores SIGKILL: leave it rather
                # than hold the gauge on an unbounded wait.
                proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            if attempted:
                atomic_write(path, json.dumps({"at": at, "windows": windows, **({"error": error} if error else {})}) + "\n")
        finally:
            if claim is not None:
                claim.close()
            _REFRESHING.discard(path)
            _STARTED.pop(path, None)


def _stuck(path) -> float | None:
    """How long the refresh of ``path`` has run, once past ``STUCK_AFTER``."""
    started = _STARTED.get(path) if path in _REFRESHING else None
    age = time.monotonic() - started if started is not None else 0
    return age if age >= STUCK_AFTER else None


def check_stuck() -> None:
    """Raise for refreshes past ``STUCK_AFTER``: the loop's guard makes it ``loom.degraded``."""
    stuck = sorted((path.stem, age) for path in list(_REFRESHING) if (age := _stuck(path)) is not None)
    if stuck:
        raise StuckRefresh("usage refresh stuck, gauge read as unknown: " + ", ".join(
            f"{name} for {age:.0f}s" for name, age in stuck))


def reading(home, shell: str, after: float = 0, *, fresh=False, cached_only=False):
    """None while refreshing; an empty reading means the gauge is unknown,
    as does a refresh still running past ``STUCK_AFTER``.

    ``cached_only`` never starts a collector; stale or absent fuel is None.
    """
    path = (home.root / "loom/usage" / f"{shell}.json").resolve()
    try:
        script = resolve(home, shell) / "usage"
    except ValueError:
        return {"windows": []}
    if not script.is_file():
        return {"windows": []}
    try:
        reading = json.loads(path.read_text())
        # An exit requires fresh *fuel*, not another failed PTY probe. Keep
        # unknown gauges for the same five-minute TTL so every new start does
        # not wait out the collector timeout while the shell is unavailable.
        # A window that has reset since the reading was taken is not known:
        # at the reset, a 2% reading taken a minute earlier re-parked a body.
        after = max([after] + [_reset(w) for w in reading["windows"] if _reset(w) <= time.time()])
        if 0 <= time.time() - reading["at"] < CACHE_TTL and (reading["at"] > after or not fresh and not reading["windows"]):
            return reading
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if cached_only:
        return None
    if _stuck(path) is not None:
        # Unknown, not pending; no second refresh joins the blocked one.
        return {"windows": []}
    if path not in _REFRESHING:
        _REFRESHING.add(path)
        _STARTED[path] = time.monotonic()
        threading.Thread(target=_refresh, args=(path, script, after) if fresh else (path, script), daemon=True).start()
    return None


def _reset(window) -> float:
    try:
        return datetime.fromisoformat(window["resets_at"]).timestamp()
    except (ValueError, TypeError, KeyError):
        return 0


def dry_until(home, shell: str):
    """When an exhausted window refills, or None. None also while refreshing
    or unknown: a caller that is already parked keeps waiting on None."""
    from .config import load_config
    value = reading(home, shell)
    floor = load_config(home.root).hold_floor_pct
    dry = [w for w in (value or {"windows": []})["windows"] if w["left_pct"] <= floor]
    if not dry:
        return None
    now = time.time()
    return max(_reset(w) if _reset(w) > now else now + 900 for w in dry)


def fuel(home, shell: str) -> str:
    value = reading(home, shell)
    parts = [f"{w['name']} {w['left_pct']:g}%" for w in value["windows"]] if value else []
    return f"fuel {shell} " + " ".join(parts) if parts else ""


_SHELL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
#: What the status line and the board header say when a collector wrote an error and no window.
FAILS = "fails"


def gauge_windows(payload) -> list[dict] | None:
    """Windows the status line would show, or None when the file is not a reading.

    An empty list is a reading with nothing to draw. The board header uses this
    same list, so a gauge the status line hides stays off the header.
    """
    if not isinstance(payload, dict):
        return None
    windows = payload.get("windows")
    if not isinstance(windows, list):
        return None
    try:
        at = float(payload["at"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(at):
        return None
    found = []
    for window in windows:
        if not isinstance(window, dict):
            continue
        left, name = window.get("left_pct"), window.get("name")
        if isinstance(left, bool) or not isinstance(left, (int, float)) or not math.isfinite(left):
            continue
        if left > 100 or not isinstance(name, str) or not name:
            continue
        row = {"name": name, "left_pct": left}
        if window.get("resets_at"):
            row["resets_at"] = window["resets_at"]
        found.append(row)
    return found


def collector_failed(payload) -> bool:
    """True when the collector wrote an error string. The text stays in the file; the line says ``fails``."""
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    return isinstance(error, str) and bool(error.strip())


def week_pace(window, now=None):
    """Time remaining at even weekly spend, and days of fuel ahead of it."""
    now = time.time() if now is None else now
    reset = _reset(window)
    if not now < reset <= now + 7 * 86400:
        return None
    days = (reset - now) / 86400
    return days, days / 7 * 100, window["left_pct"] / 100 * 7 - days


def window_text(window):
    text = f"{window['name']} {window['left_pct']:g}%"
    pace = week_pace(window) if window['name'] == 'week' else None
    if pace is not None:
        days, left, ahead = pace
        text += (f" left · {days:.1f}d to reset · pace {left:.0f}%"
                 f" · {abs(ahead):.1f}d {'ahead' if ahead > 0 else 'behind'}")
    return text


def _shown_windows(payload) -> list[str] | None:
    """Window text for one on-disk reading, or None when it shows nothing."""
    windows = gauge_windows(payload)
    if not windows:
        return None
    return [window_text(w) for w in windows]


def _past_reset(payload, now: float) -> bool:
    """True when ``at`` is older than the newest reset that has already passed.

    A reset still in the future does not count: every sample is older than a
    clock that has not arrived, and the mark would sit on every gauge.
    """
    passed = [_reset(window) for window in payload["windows"]
              if isinstance(window, dict) and 0 < _reset(window) <= now]
    return bool(passed) and float(payload["at"]) < max(passed)


def shell_fuels(home) -> dict[str, str]:
    """Window text per shell with a reading in ``loom/usage``.

    A shell with no file, or a file that yields no windows and no error, is
    omitted. A collector error with no window is the word ``fails`` (the ask
    says the same word, and the error text stays in the file). A reading older
    than its newest reset that has already passed is kept and marked with ``?``
    (``grok week 45%?``): dropping it is how a full tank was read as dry.
    """
    directory = home.root / "loom" / "usage"
    if not directory.is_dir():
        return {}
    now, found = time.time(), {}
    for path in sorted(directory.glob("*.json")):
        name = path.name.removesuffix(".json")
        if not _SHELL_NAME.fullmatch(name):
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        parts = _shown_windows(payload)
        if parts:
            found[name] = " ".join(parts) + ("?" if _past_reset(payload, now) else "")
        elif gauge_windows(payload) is not None and collector_failed(payload):
            found[name] = FAILS
    return found


def fuels(home, shell: str) -> str:
    """Every shell with a reading in ``loom/usage``, for the boundary line.

    The seated shell is first, then the rest by name. With a non-empty
    ``spend.burn`` and two or more installed harnesses, the other burn shells
    follow the seat in list order and the words ``burn`` and ``keep`` appear.
    One harness, or no burn list, stays the unmarked line. Hold and park do
    not call this.
    """
    found = shell_fuels(home)
    if not found:
        return ""
    from .adapters import burn_marks
    marks = burn_marks(home)
    if marks is None:
        order = ([shell] if shell in found else []) + sorted(n for n in found if n != shell)
        return "fuel " + " · ".join(f"{name} {found[name]}" for name in order)
    burn, keep = marks
    order = [shell] if shell in found else []
    for name in burn:
        if name in found and name not in order:
            order.append(name)
    if keep and keep in found and keep not in order:
        order.append(keep)
    order.extend(name for name in sorted(found) if name not in order)

    def shown(name: str) -> str:
        if name in burn:
            role = " burn"
        elif name == keep:
            role = " keep"
        else:
            role = ""
        return f"{name} {found[name]}{role}"

    return "fuel " + " · ".join(shown(name) for name in order)


def below_floor(home, shell: str, after: float = 0) -> bool:
    """Unknown fuel has no opinion; any exhausted window ends a hold."""
    from .config import load_config
    value = reading(home, shell, after)
    floor = load_config(home.root).hold_floor_pct
    return bool(value and any(w["left_pct"] <= floor for w in value["windows"]))
