"""``SpaBuild``: the loom keeps ``src/frontend/build`` current with its own checkout (self threads/web-main).

The loopback ``serve`` reads the SvelteKit build per request, and nothing else builds it on a resident's machine:
follow-main fast-forwards the checkout and re-execs, and the build would stay at whatever ``npm run build`` last
wrote. So at every loop pass, a loom with a board compares the build's stamp (``build/.tree``, the git tree of
``src/frontend`` it was built from) with ``HEAD:src/frontend``; when they differ it builds off the loop into
``build.next`` (``npm ci`` first when ``package-lock.json`` is newer than the installed one) and swaps it in only
once the build succeeded. A failed build keeps the old one served and raises from ``step`` while HEAD stays on
that tree, which the loop's guard turns into ``loom.degraded`` (step ``spa``) on every body's boundary; the next
tree tries again. A re-exec mid-build loses nothing: the stamp still differs, so the new process builds again.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

STAMP = ".tree"
#: Seconds a build or an install may take before it counts as failed.
LIMIT = 900
#: Seconds between reads of HEAD's tree: a ``git`` fork at every 0.2 s tick was a share of the loom's idle CPU.
#: Follow-main re-execs after it moves the checkout, and a fresh process reads at its first pass.
EVERY = 30.0


class BuildFailed(Exception):
    pass


def _run(args: list[str], cwd: Path, env: dict | None = None) -> None:
    proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=LIMIT)
    if proc.returncode != 0:
        tail = (proc.stderr.strip() or proc.stdout.strip()).splitlines()[-3:]
        raise BuildFailed(f"{' '.join(args)} exited {proc.returncode}: {' | '.join(tail)}"[:300])


class SpaBuild:
    def __init__(self, frontend: Path, log, run=_run, every: float = EVERY, clock=time.monotonic):
        self.frontend, self.log, self.run = frontend, log, run
        self.every, self.clock, self.due = every, clock, 0.0
        self.build, self.next = frontend / "build", frontend / "build.next"
        self.worker: threading.Thread | None = None
        self.failed: tuple[str, str] | None = None  # (tree, why)

    def wanted(self) -> str | None:
        proc = subprocess.run(["git", "rev-parse", "HEAD:./"], cwd=self.frontend, capture_output=True, text=True)
        return proc.stdout.strip() or None if proc.returncode == 0 else None

    def built(self) -> str:
        stamp = self.build / STAMP
        return stamp.read_text().strip() if stamp.is_file() else ""

    def step(self) -> None:
        """On the loop: start a build when the tree moved; raises while HEAD's tree failed to build."""
        if self.worker is not None and self.worker.is_alive():
            return
        if self.clock() < self.due:
            if self.failed is not None:
                raise BuildFailed(f"{self.failed[1]}; serving the build of tree {self.built()[:8] or 'none'}")
            return
        self.due = self.clock() + self.every
        tree = self.wanted()
        if tree is None or tree == self.built():
            self.failed = None
            return
        if self.failed is not None and self.failed[0] == tree:
            raise BuildFailed(f"{self.failed[1]}; serving the build of tree {self.built()[:8] or 'none'}")
        self.worker = threading.Thread(target=self._build, args=(tree,), name="spa-build", daemon=True)
        self.worker.start()

    def _build(self, tree: str) -> None:
        try:
            shutil.rmtree(self.next, ignore_errors=True)
            lock, installed = self.frontend / "package-lock.json", self.frontend / "node_modules" / ".package-lock.json"
            if not installed.is_file() or lock.stat().st_mtime > installed.stat().st_mtime:
                self.run(["npm", "ci", "--no-audit", "--no-fund"], self.frontend)
            self.run(["npm", "run", "build"], self.frontend, {**os.environ, "BRNRD_SPA_OUT": self.next.name})
            if not (self.next / "200.html").is_file():
                raise BuildFailed(f"npm run build wrote no {self.next.name}/200.html")
            (self.next / STAMP).write_text(tree + "\n")
            old = self.frontend / "build.old"
            shutil.rmtree(old, ignore_errors=True)
            if self.build.exists():
                self.build.rename(old)
            self.next.rename(self.build)
            shutil.rmtree(old, ignore_errors=True)
            self.failed = None
            self.log(f"spa: built tree {tree[:8]} into {self.build}")
        except Exception as exc:  # the loop's guard reports it; the old build stays served
            shutil.rmtree(self.next, ignore_errors=True)
            self.failed = (tree, f"{type(exc).__name__}: {exc}"[:300])
