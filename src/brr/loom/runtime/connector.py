"""Enabled folders declare channels and exchange the port's letter files."""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .adapters import resolve
from .config import config_path, load_config


def carries(home):
    found = {}
    for name in load_config(home.root).connectors:
        folder = resolve(home, name, "connectors")
        for raw in (folder / "carries").read_text().splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?: edit)?", raw.strip()):
                raise ValueError(f"{folder}/carries: invalid line {raw!r}")
            platform, *flags = raw.split()
            if platform in found:
                raise ValueError(f"{platform} carried by both {found[platform][0]} and {name}")
            found[platform] = (name, folder, "edit" in flags)
    return found


def environment(home, name):
    state = home.root / "loom/connectors" / name / "state"
    state.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in {"GIT_DIR", "GIT_WORK_TREE"}}
    src = str(Path(__file__).resolve().parents[3])
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.update(BRNRD_HOME=str(home.root.resolve()), BRNRD_STATE=str(state.resolve()),
               BRNRD_CONFIG=str(config_path(home.root).resolve()),
               PYTHONPATH=src + os.pathsep + env.get("PYTHONPATH", ""))
    return env


class Inputs:
    """One supervised input per enabled folder. A directory input is passive."""
    def __init__(self, home):
        carries(home)  # validate the complete declaration before starting any child
        self.home, self.children = home, {}
        self.names = load_config(home.root).connectors

    def step(self):
        from .source import drain
        for name in self.names:
            folder = resolve(self.home, name, "connectors")
            drop = self.home.root / "loom/connectors" / name / "state/in"
            drop.mkdir(parents=True, exist_ok=True)
            command = folder / "in"
            child, due, delay = self.children.get(name, (None, 0, 1))
            if command.is_file() and (child is None or child.poll() is not None):
                if child is not None:
                    due, child = time.monotonic() + delay, None
                    delay = min(delay * 2, 60)
                if time.monotonic() >= due:
                    try:
                        child = subprocess.Popen([str(command), str(drop.resolve())],
                                                 env=environment(self.home, name))
                    except OSError:
                        due, delay = time.monotonic() + delay, min(delay * 2, 60)
                self.children[name] = (child, due, delay)
            drain(self.home, name, drop)

    def close(self):
        for child, _due, _delay in self.children.values():
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
