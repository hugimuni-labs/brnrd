"""Optional body fuel, refreshed off the tick and cached per home and shell."""

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

_REFRESHING = set()


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
    try:
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
            atomic_write(path, json.dumps({"at": at, "windows": windows}) + "\n")
        finally:
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
        if 0 <= time.time() - reading["at"] < 300 and reading["at"] > after:
            return reading
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if path not in _REFRESHING:
        _REFRESHING.add(path)
        threading.Thread(target=_refresh, args=(path, script), daemon=True).start()
    return None


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
