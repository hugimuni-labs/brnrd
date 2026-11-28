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
from .tick_inputs import input_stat, once, read_json

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


def print_windows(rows, *, error: str | None = None, state: str | None = None,
                  reason: str | None = None) -> None:
    windows = [{"name": name, "left_pct": left,
                "resets_at": datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset else None}
               for name, left, reset in rows if left is not None]
    # An error wins over a named state. Unmetered with no windows is a reading.
    known = state == "unmetered" and not error
    if not windows and not error and not known:
        raise SystemExit(1)
    payload = {"windows": windows}
    if error:
        payload["error"] = error
    elif known:
        payload["state"] = "unmetered"
        if isinstance(reason, str) and reason.strip():
            payload["reason"] = reason
    print(json.dumps(payload))


def _refresh(path, script, after=0) -> None:
    at, windows = time.time(), []
    error = None
    state = None
    reason = None
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
            if isinstance(payload.get("error"), str) and payload["error"].strip():
                error = payload["error"]
            elif payload.get("state") == "unmetered":
                state = "unmetered"
                if isinstance(payload.get("reason"), str) and payload["reason"].strip():
                    reason = payload["reason"]
            for window in payload["windows"]:
                left = window["left_pct"]
                if (isinstance(left, (int, float)) and math.isfinite(left) and left <= 100
                        and isinstance(window.get("name"), str)):
                    windows.append(window)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        windows = []
        state = None
        reason = None
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
                body = {"at": at, "windows": windows}
                if error:
                    body["error"] = error
                elif state == "unmetered":
                    body["state"] = state
                    if reason:
                        body["reason"] = reason
                samples = _kept_samples(path, at)
                if samples:
                    body["samples"] = samples
                atomic_write(path, json.dumps(body) + "\n")
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
    path = home.root / "loom/usage" / f"{shell}.json"

    def collector():
        try:
            script = resolve(home, shell) / "usage"
        except ValueError:
            return None
        return script if script.is_file() else None

    script = once("usage-collector", (home.root, shell), collector)
    if script is None:
        return {"windows": []}
    valid, reading = read_json(path)
    try:
        # An exit requires fresh *fuel*, not another failed PTY probe. Keep
        # unknown gauges for the same five-minute TTL so every new start does
        # not wait out the collector timeout while the shell is unavailable.
        # A window that has reset since the reading was taken is not known:
        # at the reset, a 2% reading taken a minute earlier re-parked a body.
        if valid:
            after = max([after] + [_reset(w) for w in reading["windows"] if _reset(w) <= time.time()])
            if 0 <= time.time() - reading["at"] < CACHE_TTL and (reading["at"] > after or not fresh and not reading["windows"]):
                return reading
    except (ValueError, KeyError, TypeError):
        pass
    if cached_only:
        return None
    path = once("usage-path", path, path.resolve)
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
#: Board error code for an unsuccessful quota reading.
FAILS = "fails"
QUOTA_UNAVAILABLE = "quota unavailable"
#: A known steady state: the plan exposes no quota, so the line does not say ``fails``.
UNMETERED = "unmetered"


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
    """True when the collector wrote an error string. The text stays in the file."""
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    return isinstance(error, str) and bool(error.strip())


def known_unmetered(payload) -> bool:
    """True when the collector named ``unmetered`` and wrote no error.

    An error string wins, so a failed probe is never read as this state.
    Windows, when the reading has any, still draw ahead of the word.
    """
    return isinstance(payload, dict) and payload.get("state") == UNMETERED and not collector_failed(payload)


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


# A sample younger than this is noise, not an hour's burn.
_PACE_MIN = 20 * 60
# Keep two hours, so the hour the line needs is still in the file.
_SAMPLE_KEEP = 2 * 3600


def _sample_row(payload) -> dict | None:
    """One reading reduced to the time and each window's level."""
    if not isinstance(payload, dict):
        return None
    try:
        at = float(payload["at"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(at):
        return None
    windows = []
    for window in payload.get("windows") or []:
        if not isinstance(window, dict) or not isinstance(window.get("name"), str):
            continue
        left = window.get("left_pct")
        if isinstance(left, bool) or not isinstance(left, (int, float)) or not math.isfinite(left):
            continue
        windows.append({"name": window["name"], "left_pct": left})
    return {"at": at, "windows": windows}


def _kept_samples(path, at: float) -> list[dict]:
    """Earlier readings still inside two hours, including the file about to be replaced."""
    try:
        previous = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    rows = []
    if isinstance(previous, dict):
        for raw in previous.get("samples") or []:
            if isinstance(raw, dict):
                row = _sample_row(raw)
                if row is not None:
                    rows.append(row)
        current = _sample_row(previous)
        if current is not None:
            rows.append(current)
    kept = {}
    for row in rows:
        if 0 < at - row["at"] <= _SAMPLE_KEEP:
            kept[row["at"]] = row
    return [kept[stamp] for stamp in sorted(kept)][-48:]


def _session_level(row) -> float | None:
    for window in row.get("windows") or []:
        if isinstance(window, dict) and window.get("name") == "session":
            left = window.get("left_pct")
            if isinstance(left, (int, float)) and not isinstance(left, bool):
                return float(left)
    return None


def session_pace(payload, now=None) -> dict | None:
    """Burn of the session window over the last hour.

    None when the reading has no session window. ``known`` is false until a
    sample at least twenty minutes old exists: one reading cannot say a burn.
    ``falling`` is false when the level did not drop. ``about_h`` is the dry
    time rounded to an hour, so the sentence stays still while a percent ticks.
    """
    if not isinstance(payload, dict):
        return None
    windows = gauge_windows(payload)
    if not windows:
        return None
    session = next((window for window in windows if window["name"] == "session"), None)
    if session is None:
        return None
    now = time.time() if now is None else now
    try:
        read_at = float(payload["at"])
    except (KeyError, TypeError, ValueError):
        return None
    found = []
    for raw in payload.get("samples") or []:
        if not isinstance(raw, dict):
            continue
        try:
            stamp = float(raw["at"])
        except (KeyError, TypeError, ValueError):
            continue
        left = _session_level(raw)
        if left is None or not _PACE_MIN <= now - stamp <= _SAMPLE_KEEP:
            continue
        found.append((stamp, left))
    pace = {"left_pct": session["left_pct"], "read_at": read_at,
            "resets_at": session.get("resets_at"), "known": False, "falling": False}
    if not found:
        return pace
    old_at, old_left = min(found, key=lambda row: abs((now - row[0]) - 3600))
    hours = (read_at - old_at) / 3600
    if hours <= 0:
        return pace
    burn = (old_left - session["left_pct"]) / hours
    pace["known"] = True
    pace["burn_pct_per_h"] = burn
    if burn <= 0:
        return pace
    dry_h = session["left_pct"] / burn
    dry_at = read_at + dry_h * 3600
    reset = _reset(session)
    pace.update(falling=True, dry_h=dry_h, dry_at=dry_at,
                about_h=0 if dry_h < 0.5 else max(1, round(dry_h)),
                before_reset=bool(reset) and dry_at < reset)
    return pace


def session_pace_line(pace) -> str:
    """The sentence the board and the body's boundary both say."""
    if not pace:
        return ""
    if not pace.get("known"):
        return "session pace: one reading, burn not known yet"
    if not pace.get("falling"):
        return "session pace: not falling"
    when = "under 1h" if pace.get("about_h") == 0 else f"about {pace['about_h']}h"
    which = "before" if pace.get("before_reset") else "after"
    return f"session pace: dry in {when}, {which} the reset"


def pace_line_for(home, shell: str) -> str:
    """The seated shell's pace sentence, from the file the collector last wrote.

    Reads the file and does not start a collector: the boundary must not pay a probe.
    """
    if not shell or not _SHELL_NAME.fullmatch(shell):
        return ""
    path = home.root / "loom" / "usage" / f"{shell}.json"
    valid, payload = read_json(path)
    return session_pace_line(session_pace(payload)) if valid else ""


def _past_reset(payload, now: float) -> bool:
    """True when ``at`` is older than the newest reset that has already passed.

    A reset still in the future does not count: every sample is older than a
    clock that has not arrived, and the mark would sit on every gauge.
    """
    passed = [_reset(window) for window in payload["windows"]
              if isinstance(window, dict) and 0 < _reset(window) <= now]
    return bool(passed) and float(payload["at"]) < max(passed)


def usage_files(home):
    folder = home.root / "loom" / "usage"
    input_stat(folder)
    return once("usage-files", folder, lambda: sorted(folder.glob("*.json")))


def _shell_readings(home):
    for path in usage_files(home):
        name = path.stem
        if not _SHELL_NAME.fullmatch(name):
            continue
        valid, payload = read_json(path)
        if valid and (windows := gauge_windows(payload)) is not None:
            yield name, payload, windows


def lapsing_fuel(home) -> str:
    """Report each live window separately because quotas can overlap."""
    now = time.time()
    readings = []
    for name, payload, windows in _shell_readings(home):
        live = [f"{w['name']} {max(0, w['left_pct']):g}% at "
                f"{datetime.fromtimestamp(_reset(w), timezone.utc):%Y-%m-%d %H:%MZ}"
                for w in windows if _reset(w) > now]
        if live:
            readings.append(f"{name} " + ", ".join(live))
        elif known_unmetered(payload):
            readings.append(f"{name} unmetered")
        else:
            readings.append(f"{name} lapse unknown")
    return "fuel lapsing if unspent: " + (" · ".join(readings) or "no readings")


def shell_fuels(home) -> dict[str, str]:
    """Window text per shell with a reading in ``loom/usage``.

    A shell with no file, or a file that yields no windows and no error, is
    omitted. A collector error with no window is shown as quota unavailable.
    A reading whose
    state is ``unmetered``, with no window and no error, is that word: a known
    gap, not a failure. A reading older than its newest reset that has already
    passed is kept and marked with ``?``
    (``grok week 45%?``): dropping it is how a full tank was read as dry.
    """
    now, found = time.time(), {}
    for name, payload, windows in _shell_readings(home):
        parts = [window_text(window) for window in windows]
        if parts:
            found[name] = " ".join(parts) + ("?" if _past_reset(payload, now) else "")
        elif gauge_windows(payload) is not None and known_unmetered(payload):
            found[name] = UNMETERED
        elif gauge_windows(payload) is not None and collector_failed(payload):
            found[name] = QUOTA_UNAVAILABLE
    return found


def fuels(home, shell: str) -> str:
    """Every shell with a reading, with the seated shell first."""
    found = shell_fuels(home)
    if not found:
        return ""
    order = sorted(found, key=lambda name: (name != shell, name))
    return "fuel " + " · ".join(f"{name} {found[name]}" for name in order)


def below_floor(home, shell: str, after: float = 0) -> bool:
    """Unknown fuel has no opinion; any exhausted window ends a hold."""
    from .config import load_config
    value = reading(home, shell, after)
    floor = load_config(home.root).hold_floor_pct
    return bool(value and any(w["left_pct"] <= floor for w in value["windows"]))
