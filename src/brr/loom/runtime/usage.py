"""Optional body fuel, refreshed off the tick and cached per home and shell."""

import fcntl
import json
import math
import os
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
_REFRESHING = set()


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


def print_windows(rows) -> None:
    windows = [{"name": name, "left_pct": left,
                "resets_at": datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset else None}
               for name, left, reset in rows if left is not None]
    if not windows:
        raise SystemExit(1)
    print(json.dumps({"windows": windows}))


def _refresh(path, script) -> None:
    at, windows = time.time(), []
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
        if previous and 0 <= at - float(previous) < PROBE_INTERVAL:
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
            for window in json.loads(out)["windows"]:
                left = window["left_pct"]
                if (isinstance(left, (int, float)) and math.isfinite(left) and left <= 100
                        and isinstance(window.get("name"), str)):
                    windows.append(window)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        windows = []
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        try:
            if attempted:
                atomic_write(path, json.dumps({"at": at, "windows": windows}) + "\n")
        finally:
            if claim is not None:
                claim.close()
            _REFRESHING.discard(path)


def reading(home, shell: str, after: float = 0):
    """None while refreshing; an empty reading means the gauge is unknown."""
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
        if 0 <= time.time() - reading["at"] < CACHE_TTL and (reading["at"] > after or not reading["windows"]):
            return reading
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if path not in _REFRESHING:
        _REFRESHING.add(path)
        threading.Thread(target=_refresh, args=(path, script), daemon=True).start()
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


def below_floor(home, shell: str, after: float = 0) -> bool:
    """Unknown fuel has no opinion; any exhausted window ends a hold."""
    from .config import load_config
    value = reading(home, shell, after)
    floor = load_config(home.root).hold_floor_pct
    return bool(value and any(w["left_pct"] <= floor for w in value["windows"]))
