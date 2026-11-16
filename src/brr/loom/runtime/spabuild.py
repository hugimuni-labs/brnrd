"""Keep the loopback board matched to the resident's checkout.

Try the exact verified asset, supported local npm build, then the newest
published ancestor. If none works, keep the last good board or run without one.
Managed installs retry when idle; a missing board never refuses installation.
Work happens off the loop and only complete builds replace the served tree.
"""

from __future__ import annotations

import os
import re
import shlex
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


def npm_supported() -> bool:
    """The lockfile's build tooling needs Node 20.19+, 22.13+, or 24+; npm 9+."""
    if shutil.which('npm') is None or shutil.which('node') is None:
        return False
    try:
        versions = []
        for command in ('node', 'npm'):
            result = subprocess.run([command, '--version'], capture_output=True, text=True, timeout=5, check=True)
            match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?', result.stdout.strip())
            if not match:
                return False
            versions.append(tuple(map(int, match.groups())))
        node, npm = versions
        return npm[0] >= 9 and (node >= (24, 0, 0) or (node[0] == 22 and node >= (22, 13, 0))
                               or (node[0] == 20 and node >= (20, 19, 0)))
    except (OSError, subprocess.SubprocessError):
        return False


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

    def step(self, idle=True) -> None:
        """On the loop: start a build when the tree moved; raises while HEAD's tree failed to build."""
        if self.worker is not None and self.worker.is_alive():
            return
        if self.clock() < self.due:
            if self.failed is not None:
                raise BuildFailed(f"{self.failed[1]}; serving the build of tree {self.built()[:8] or 'none'}")
            return
        self.due = self.clock() + self.every
        if not (idle() if callable(idle) else idle):
            return
        tree = self.wanted()
        if tree is None or tree == self.built():
            self.failed = None
            return
        if self.failed is not None and self.failed[0] == tree:
            raise BuildFailed(f"{self.failed[1]}; serving the build of tree {self.built()[:8] or 'none'}")
        self.worker = threading.Thread(target=self._build, args=(tree,), name="spa-build", daemon=True)
        self.worker.start()

    def _fetch(self, commit: str, tree: str) -> bool:
        from .board_asset import fetch
        try:
            shutil.rmtree(self.next, ignore_errors=True)
            fetch(commit, tree, self.next)
            self._swap(upstream=tree)
            return True
        except Exception as exc:
            shutil.rmtree(self.next, ignore_errors=True)
            self.notice(commit, f"spa: rejected/unavailable board for {commit} ({exc}); archive not served")
            return False

    def _local(self, tree: str) -> None:
        shutil.rmtree(self.next, ignore_errors=True)
        lock, installed = self.frontend / "package-lock.json", self.frontend / "node_modules" / ".package-lock.json"
        if not installed.is_file() or lock.stat().st_mtime > installed.stat().st_mtime:
            self.run(["npm", "ci", "--no-audit", "--no-fund"], self.frontend)
        self.run(["npm", "run", "build"], self.frontend, {**os.environ, "BRNRD_SPA_OUT": self.next.name})
        if not (self.next / "200.html").is_file():
            raise BuildFailed(f"npm run build wrote no {self.next.name}/200.html")
        (self.next / STAMP).write_text(tree + "\n")
        self._swap()
        self.log(f"spa: built tree {tree[:8]} into {self.build}")

    def _ancestor(self, exact: str) -> bool:
        from .board_asset import available
        try:
            commits = available()
            # git's date order selects the newest published reachable commit,
            # never an unrelated branch merely because it has a newer asset.
            ancestors = (self._git('rev-list', '--date-order', 'HEAD') or '').splitlines()
            for commit in ancestors:
                if commit == exact or commit not in commits:
                    continue
                tree = self._git('rev-parse', f'{commit}:src/frontend')
                if tree and self._fetch(commit, tree):
                    self.notice('older:' + commit, f'spa: board is from older ancestor commit {commit}')
                    return True
        except Exception as exc:
            self.notice('listing', f'spa: cannot list older board assets ({exc})')
        return False

    def _build(self, tree: str) -> None:
        try:
            if self.upstream:
                commit, upstream_tree = self.upstream
                matched = (self.build / '200.html').is_file() and self.built_upstream() == upstream_tree
                if not matched:
                    matched = self._fetch(commit, upstream_tree)
                if matched and not self.changed:
                    self.failed = None
                    return
                if npm_supported():
                    try:
                        self._local(tree)
                        self.failed = None
                        return
                    except Exception as exc:
                        self.notice('local:' + tree, f'spa: local board build failed ({exc}); keeping last good build')
                        shutil.rmtree(self.next, ignore_errors=True)
                elif self.changed:
                    self.notice(tree, 'spa: frontend changed but npm is unavailable or unsupported; keeping last good build')
                if not matched:
                    self._ancestor(commit)
                if not (self.build / '200.html').is_file():
                    from . import entry
                    retry = shlex.join([str(entry.current() / 'bin/python'), '-I', '-m',
                                        'brr.loom.runtime.board_asset'])
                    self.notice('absent', f'spa: installed without a board; fetch it later with: {retry}; '
                                'the loom will retry when idle')
                self.failed = None
                # Missing/bad assets and unavailable npm are soft failures. Retry
                # off-loop when idle, bounded to one attempt per five minutes.
                self.due = self.clock() + max(self.every, 300.0)
                return
            if not npm_supported():
                self.notice(tree, 'spa: npm is unavailable or unsupported; keeping last good build')
                self.failed = None
                return
            self._local(tree)
            self.failed = None
        except Exception as exc:
            shutil.rmtree(self.next, ignore_errors=True)
            self.failed = (tree, f"{type(exc).__name__}: {exc}"[:300])

    def _swap(self, upstream: str | None = None) -> None:
        if upstream or self.upstream:
            (self.next / '.upstream').write_text((upstream or self.upstream[1]) + '\n')
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
