"""``Updater``: the loom moves its own source forward, then re-execs.

``update`` in loom/config.toml picks the source. ``dev`` follows a git
checkout's main: every ``EVERY`` seconds, off the loop, it fetches origin's
main and asks the forge for that commit's checks. When every check passed and
the tip is ahead of HEAD, the loop fast-forwards at the next idle tick (no
body of this install has a tool call open) and re-execs; the start that
follows writes ``loom.moved``. It never resets: a HEAD main does not contain,
a branch other than main or a tree with uncommitted changes is refused, and
the refusal is the ``update`` step's ``loom.degraded``. Red or pending checks
wait without a fact. ``stable`` is the release channel (product-shape move
3), not built here: it logs once and stays put. ``auto``, the default, is
``dev`` for a loom that runs from a checkout and ``off`` otherwise.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import resolve_secret
from .selfrepo import git

EVERY = 300
#: Check conclusions that count as passed; any other completed one is red.
PASSED = {"success", "neutral", "skipped"}
_MERGE = re.compile(r"^Merge pull request #(\d+)")


class Refused(Exception):
    """The checkout is not one the updater may touch; the message says why."""


@dataclass(frozen=True)
class Offer:
    old: str
    new: str


def slug(url: str) -> str | None:
    """``owner/repo`` of a GitHub remote, https or ssh."""
    if url.startswith("git@github.com:"):
        path = url.partition(":")[2]
    else:
        parts = urlsplit(url)
        if parts.hostname != "github.com":
            return None
        path = parts.path
    path = path.strip("/").removesuffix(".git")
    return path if path.count("/") == 1 else None


class GitHubChecks:
    """A commit's check runs, read with the home's forge token or the machine's gh login."""

    def __init__(self, forge_token: str | None):
        self.forge_token = forge_token

    def _token(self) -> str:
        if self.forge_token:
            return resolve_secret(self.forge_token)
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                              timeout=20, check=True).stdout.strip()

    def verdict(self, url: str, sha: str) -> str:
        """``green`` when every run passed, ``red`` when one failed, else ``pending``."""
        repo = slug(url)
        if repo is None:
            raise Refused(f"origin {url} is not on GitHub: no CI to read")
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=100",
            headers={"Authorization": f"Bearer {self._token()}",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            runs = json.load(resp).get("check_runs") or []
        if any(r.get("status") == "completed" and r.get("conclusion") not in PASSED for r in runs):
            return "red"
        if not runs or any(r.get("status") != "completed" for r in runs):
            return "pending"
        return "green"


class DevSource:
    """A git checkout that follows origin's main, fast-forward only."""

    def __init__(self, root: Path, forge):
        self.root, self.forge = root, forge

    def _git(self, *args: str, check: bool = True) -> str:
        return git(self.root, *args, check=check).stdout.strip()

    def _ancestor(self, older: str, newer: str) -> bool:
        return git(self.root, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0

    def refusal(self) -> str:
        """Why the checkout may not move now; empty when it may."""
        branch = self._git("symbolic-ref", "-q", "--short", "HEAD", check=False)
        if branch and branch != "main":
            return f"checkout {self.root} is on branch {branch}, not main: not touched"
        dirty = self._git("status", "--porcelain", "--untracked-files=no").splitlines()
        if dirty:
            return (f"checkout {self.root} has {len(dirty)} uncommitted change"
                    f"{'s' * (len(dirty) > 1)} ({dirty[0].strip()}): not touched")
        return ""

    def poll(self) -> Offer | None:
        """Off the loop: the move main's green tip offers, if any."""
        url = self._git("remote", "get-url", "origin")
        self._git("fetch", "--quiet", "--no-tags", "origin", "refs/heads/main")
        tip, head = self._git("rev-parse", "FETCH_HEAD"), self._git("rev-parse", "HEAD")
        if tip == head or self._ancestor(tip, head):
            return None
        if not self._ancestor(head, tip):
            raise Refused(f"checkout {self.root} at {head[:8]} has diverged from main "
                          f"{tip[:8]}: not touched")
        if self.forge.verdict(url, tip) != "green":
            return None
        if why := self.refusal():
            raise Refused(why)
        return Offer(head, tip)

    def apply(self, offer: Offer) -> bool:
        """On the loop, at an idle tick: fast-forward; false when HEAD moved since the poll."""
        if why := self.refusal():
            raise Refused(why)
        if self._git("rev-parse", "HEAD") != offer.old:
            return False
        self._git("merge", "--ff-only", "--quiet", offer.new)
        return True


#: The seam for move 3: ``stable`` gets its source here when releases exist.
SOURCES = {"dev": DevSource}


def forge_for(config):
    return GitHubChecks(config.forge_token)


class Updater:
    """Polls its source off the loop; moves only when the loop says it is idle."""

    def __init__(self, home, log, root: Path):
        self.home, self.log, self.root = home, log, root
        self.checked: float | None = None
        self.worker: threading.Thread | None = None
        self.failing: tuple[Exception, object] | None = None
        self.offer: Offer | None = None
        self.moved: Offer | None = None
        self.said: set[str] = set()

    def _say_once(self, text: str) -> None:
        if text not in self.said:
            self.said.add(text)
            self.log(self.home, f"update: {text}")

    def _source(self, config):
        mode = config.update
        if mode == "auto":
            mode = "dev" if (self.root / ".git").exists() else "off"
        if mode == "off":
            return None
        if mode not in SOURCES:
            self._say_once(f"update = {mode} has no source yet; staying at this version")
            return None
        if not (self.root / ".git").exists():
            self._say_once(f"update = {mode}, but {self.root} is not a git checkout; staying put")
            return None
        return SOURCES[mode](self.root, forge_for(config))

    def step(self, config, idle) -> None:
        """Sets ``moved`` once the checkout moved; raises while the source refuses or fails."""
        source = self._source(config)
        if source is None:
            self.failing = self.offer = None
            return
        if self.offer is not None and idle():
            offer, self.offer = self.offer, None
            if source.apply(offer):
                self.log(self.home, f"update: fast-forwarded {self.root} {offer.old[:8]} -> {offer.new[:8]}")
                self.moved = offer
                return
        self._attempt(source)
        if self.failing is not None:
            raise self.failing[0].with_traceback(self.failing[1])

    def _attempt(self, source) -> None:
        if self.worker and self.worker.is_alive():
            return
        now = time.monotonic()
        if self.checked is not None and now - self.checked < EVERY:
            return
        self.checked = now
        self.worker = threading.Thread(target=self._poll, args=(source,), name="loom-update",
                                       daemon=True)
        self.worker.start()

    def _poll(self, source) -> None:
        try:
            self.offer = source.poll()
            self.failing = None
        except Exception as exc:
            self.offer = None
            self.failing = (exc, exc.__traceback__)


def merges(root: Path, old: str, new: str) -> list[str]:
    """``#N`` of each pull request merged between two commits, oldest first."""
    proc = git(root, "log", "--merges", "--reverse", "--format=%s", f"{old}..{new}", check=False)
    return [f"#{m.group(1)}" for line in proc.stdout.splitlines() if (m := _MERGE.match(line))]


def moved_text(fact) -> str:
    data = fact.data or {}
    loaded = ", ".join(data.get("merges") or ()) or "no merged pull requests"
    return f"loom moved {str(data.get('from'))[:8]} → {str(data.get('to'))[:8]}, loaded {loaded}"


def moved_line(facts, strand: str, gen: int, install: str) -> str:
    """``moved:`` for a body seated through a move; a body woken after it reads it in its wake."""
    from .bodies import fact_for
    from .health import _seconds
    from .ledger import kind_facts
    began = fact_for(facts, "body.started", strand, gen)
    if began is None:
        return ""
    moves = [moved_text(f) for f in kind_facts(facts, "loom.moved")
             if f.data.get("install") == install and _seconds(f.at) >= _seconds(began.at)]
    return "moved: " + "; ".join(moves) if moves else ""
