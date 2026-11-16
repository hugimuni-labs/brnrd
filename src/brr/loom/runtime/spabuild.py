"""Keep the loopback board matched to the resident's checkout.

A managed upstream tree uses its verified release asset, without Node. Local
frontend changes rebuild with npm when available. Missing assets or npm retain
the last good board and log once; local build failures retain it and degrade.
Work happens off the loop and only complete builds replace the served tree.
"""

from __future__ import annotations

import os
import hashlib
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
        self.noticed: set[str] = set()
        self.upstream: tuple[str, str] | None = None
        self.changed = False

    def _git(self, *args: str) -> str | None:
        proc = subprocess.run(["git", *args], cwd=self.frontend, capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def wanted(self) -> str | None:
        tree = self._git("rev-parse", "HEAD:./")
        if not tree:
            return None
        origin = self._git("remote", "get-url", "origin")
        self.upstream = None
        if origin in {"https://github.com/hugimuni-labs/brnrd", "https://github.com/hugimuni-labs/brnrd.git",
                      "git@github.com:hugimuni-labs/brnrd.git"}:
            commit = self._git("merge-base", "HEAD", "refs/remotes/origin/main")
            upstream_tree = self._git("rev-parse", f"{commit}:src/frontend") if commit else None
            if commit and upstream_tree:
                self.upstream = (commit, upstream_tree)
        if not self.upstream:
            self.changed = True
            return tree
        base = self.upstream[0]
        diff = self._git("diff", "--binary", base, "--", ".") or ""
        untracked = self._git("ls-files", "--others", "--exclude-standard", "--", ".") or ""
        self.changed = bool(diff or untracked or (self.upstream and tree != self.upstream[1]))
        if diff or untracked:
            digest = hashlib.sha256(diff.encode())
            for name in untracked.splitlines():
                path = self.frontend / name
                digest.update(name.encode())
                if path.is_file():
                    digest.update(path.read_bytes())
            return digest.hexdigest()
        return tree

    def notice(self, key: str, message: str) -> None:
        if key not in self.noticed:
            self.noticed.add(key)
            self.log(message)

    def built(self) -> str:
        stamp = self.build / STAMP
        return stamp.read_text().strip() if stamp.is_file() else ""

    def built_upstream(self) -> str:
        stamp = self.build / '.upstream'
        return stamp.read_text().strip() if stamp.is_file() else self.built()

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
            if self.upstream:
                commit, upstream_tree = self.upstream
                if self.built_upstream() != upstream_tree:
                    try:
                        from .board_asset import fetch
                        fetch(commit, upstream_tree, self.next)
                        self._swap()
                        self.log(f"spa: fetched board for upstream {commit}")
                    except Exception as exc:
                        shutil.rmtree(self.next, ignore_errors=True)
                        self.notice(commit, f"spa: board for {commit} unavailable ({exc}); keeping last good build; "
                                    "if none exists, run npm ci && npm run build in src/frontend")
                if not self.changed:
                    self.failed = None
                    return
            if shutil.which("npm") is None:
                self.notice(tree, "spa: frontend changed but npm is unavailable; keeping last good build; "
                            "install npm and run npm ci && npm run build in src/frontend")
                self.failed = None
                return
            lock, installed = self.frontend / "package-lock.json", self.frontend / "node_modules" / ".package-lock.json"
            if not installed.is_file() or lock.stat().st_mtime > installed.stat().st_mtime:
                self.run(["npm", "ci", "--no-audit", "--no-fund"], self.frontend)
            self.run(["npm", "run", "build"], self.frontend, {**os.environ, "BRNRD_SPA_OUT": self.next.name})
            if not (self.next / "200.html").is_file():
                raise BuildFailed(f"npm run build wrote no {self.next.name}/200.html")
            (self.next / STAMP).write_text(tree + "\n")
            self._swap()
            self.failed = None
            self.log(f"spa: built tree {tree[:8]} into {self.build}")
        except Exception as exc:  # the loop's guard reports it; the old build stays served
            shutil.rmtree(self.next, ignore_errors=True)
            self.failed = (tree, f"{type(exc).__name__}: {exc}"[:300])

    def _swap(self) -> None:
        if self.upstream:
            (self.next / '.upstream').write_text(self.upstream[1] + '\n')
        old = self.frontend / "build.old"
        shutil.rmtree(old, ignore_errors=True)
        if self.build.exists():
            self.build.rename(old)
        try:
            self.next.rename(self.build)
        except OSError:
            if old.exists():
                old.rename(self.build)
            raise
        shutil.rmtree(old, ignore_errors=True)
