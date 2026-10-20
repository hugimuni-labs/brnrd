"""``Mirror``: the home's ``self`` main, pushed to its ``origin`` so doc links resolve.

Rooms land on ``home/self`` through ``send-self``; nothing else carried that
main to the forge, so a link to a file landed since the last hand push was a 404.
The loop pushes when ``main`` is ahead, with the home's ``forge_token`` and
never the machine's own git setup: no token, no mirror. A push that fails is
logged once, until one succeeds again.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from .publish import PublishError, push_command
from .selfrepo import git

EVERY = 30
_TIMEOUT = 120


def pending(self_dir: Path) -> tuple[str, str] | None:
    """``(origin url, main sha)`` when ``main`` has commits ``origin/main`` lacks."""
    url = git(self_dir, "remote", "get-url", "origin", check=False).stdout.strip()
    head = git(self_dir, "rev-parse", "--verify", "-q", "refs/heads/main", check=False).stdout.strip()
    if not url or not head:
        return None
    seen = git(self_dir, "rev-parse", "--verify", "-q", "refs/remotes/origin/main", check=False).stdout.strip()
    if seen == head:
        return None
    # Behind or diverged is not ours to force: only a fast-forward goes out.
    if seen and git(self_dir, "merge-base", "--is-ancestor", seen, head, check=False).returncode != 0:
        return None
    return url, head


def push(self_dir: Path, url: str, sha: str, forge_token: str) -> None:
    """Push ``sha`` to ``url``'s main under the token, then move ``origin/main``."""
    args, env, _lane, _note = push_command(url, "main", forge_token)
    refspec = f"{sha}:refs/heads/main"
    args[-1] = refspec
    proc = subprocess.run(["git", *args], cwd=self_dir, env=env, capture_output=True,
                          text=True, timeout=_TIMEOUT)
    if proc.returncode != 0:
        raise PublishError((proc.stderr.strip() or proc.stdout.strip() or "push failed")[-400:])
    git(self_dir, "update-ref", "refs/remotes/origin/main", sha)


class Mirror:
    def __init__(self, home, log):
        self.home, self.log = home, log
        self.checked: float | None = None
        self.worker: threading.Thread | None = None
        self.failing = False

    def step(self, config) -> None:
        if not config.forge_token or (self.worker and self.worker.is_alive()):
            return
        now = time.monotonic()
        if self.checked is not None and now - self.checked < EVERY:
            return
        self.checked = now
        self_dir = self.home.root / "self"
        if not (self_dir / ".git").exists():
            return
        found = pending(self_dir)
        if found is None:
            return
        url, sha = found
        self.worker = threading.Thread(target=self._push, name="loom-mirror", daemon=True,
                                       args=(self_dir, url, sha, config.forge_token))
        self.worker.start()

    def _push(self, self_dir: Path, url: str, sha: str, forge_token: str) -> None:
        try:
            push(self_dir, url, sha, forge_token)
            self.failing = False
        except Exception as exc:
            if not self.failing:
                self.log(self.home, f"mirror: push of self main failed: {exc}")
            self.failing = True
