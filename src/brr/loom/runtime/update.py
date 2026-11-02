"""``Updater``: the loom moves its own source forward, then re-execs.

``update`` in loom/config.toml picks the source. ``dev`` follows a git
checkout's main: every ``EVERY`` seconds, off the loop, it fetches origin's
main and asks the forge for that commit's checks. When every check passed and
the tip is ahead of HEAD, the loop fast-forwards at the next idle tick and
re-execs. Idle starts strict (no body of this install has a tool call open)
and loosens the longer the move waits (``PATIENCE``), since several seated
bodies seldom pause at once; the start that
follows writes ``loom.moved``. It never resets: a HEAD main does not contain,
a branch other than main or a tree with uncommitted changes is refused, and
the refusal is the ``update`` step's ``loom.degraded``. Red or pending checks
wait without a fact; a red one is said once in loom.log. A check counts only
when the tip's pull request had to pass it (``GitHubChecks.gates``): a deploy
is not a gate. ``stable`` is the release channel (product-shape move 3), not
built here: it logs once and stays put. ``auto``, the default, is
``dev`` for a loom that runs from a checkout and ``off`` otherwise.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import resolve_secret
from .selfrepo import git

EVERY = 300
#: Seconds between looks for an idle tick while an offer waits: a look reads the ledger.
LOOK_EVERY = 5
#: Check conclusions that count as passed; any other completed one is red.
PASSED = {"success", "neutral", "skipped"}
#: (seconds a green move has waited, how young an open tool call must be to hold it back);
#: ``None`` is any open call. A call that has run a minute waits on CI or tests; it is not
#: importing the checkout. On 6 h of this loom's calls (2026-10-26) the strict gate was open
#: 39% of the time with 7 waits over 10 min, and once 1% of a 44 min busy stretch; under 60 s
#: the longest wait was 8 min, under 15 s 1 min.
PATIENCE = ((0, None), (600, 60), (1800, 15))
_MERGE = re.compile(r"^Merge pull request #(\d+)")
#: GitHub's squash and rebase-squash title: the pull request's title, then `` (#N)``.
_SQUASH = re.compile(r"\(#(\d+)\)$")


def pull_of(subject: str) -> int | None:
    """The pull request a commit on main lands, by the subject GitHub writes:
    ``Merge pull request #N …`` for a merge commit, ``… (#N)`` for a squash."""
    m = _MERGE.match(subject) or _SQUASH.search(subject.rstrip())
    return int(m.group(1)) if m else None


class Refused(Exception):
    """The checkout is not one the updater may touch; the message says why."""


@dataclass(frozen=True)
class Offer:
    old: str
    new: str


@dataclass(frozen=True)
class Held:
    """Main's tip is red on a check its pull request had to pass: the loom stays on ``head``."""
    tip: str
    head: str
    check: str


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

    def _get(self, repo: str, path: str) -> dict:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/{path}",
            headers={"Authorization": f"Bearer {self._token()}",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    def _runs(self, repo: str, sha: str) -> list[dict]:
        return self._get(repo, f"commits/{sha}/check-runs?per_page=100").get("check_runs") or []

    def gates(self, url: str, pull: int | None) -> set[str] | None:
        """The checks pull request ``pull`` had to pass: those that ran on its head, not skipped.

        A check main runs that its pull request never did is a deploy, not a gate: 29f304c9
        held the loom ~2 h on publish-container's "Build and publish image", a registry push
        that failed with every code check green (2026-11-01). ``None``, every check counts,
        when the commit names no pull request or its head ran none."""
        repo = slug(url)
        if pull is None or repo is None:
            return None
        try:
            head = str((self._get(repo, f"pulls/{pull}").get("head") or {}).get("sha") or "")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        ran = {str(r.get("name")) for r in self._runs(repo, head) if r.get("conclusion") != "skipped"} if head else set()
        return ran or None

    def judge(self, url: str, sha: str, gates: set[str] | None = None) -> tuple[str, str]:
        """(``verdict``, the first red check's name or ``""``), over ``gates`` when given."""
        repo = slug(url)
        if repo is None:
            raise Refused(f"origin {url} is not on GitHub: no CI to read")
        runs = [r for r in self._runs(repo, sha) if gates is None or r.get("name") in gates]
        # A run with a conclusion is done, even when GitHub left its status in_progress.
        done = [r for r in runs if r.get("status") == "completed" or r.get("conclusion")]
        # A failure that ended before a live run started is being rerun: #2506's
        # "Backend tests" read red for 5 minutes while its shard reran (2026-10-29).
        live = [str(r.get("started_at") or "~") for r in runs if r not in done]
        for r in done:
            if (r.get("conclusion") not in PASSED
                    and not (r.get("completed_at") and any(start > r["completed_at"] for start in live))):
                return "red", str(r.get("name") or "?")
        if not runs or len(done) < len(runs):
            return "pending", ""
        return "green", ""

    def verdict(self, url: str, sha: str) -> str:
        """``green`` when every run passed, ``red`` when one failed, else ``pending``."""
        return self.judge(url, sha)[0]


#: Main's tip as this process last fetched it, for the wake's ``loom:`` line.
SEEN: dict = {}


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

    def poll(self) -> Offer | Held | None:
        """Off the loop: the move main's green tip offers, the red check holding a red one."""
        url = self._git("remote", "get-url", "origin")
        self._git("fetch", "--quiet", "--no-tags", "origin", "refs/heads/main")
        tip, head = self._git("rev-parse", "FETCH_HEAD"), self._git("rev-parse", "HEAD")
        SEEN.update(tip=tip, at=time.time())
        if tip == head or self._ancestor(tip, head):
            return None
        if not self._ancestor(head, tip):
            raise Refused(f"checkout {self.root} at {head[:8]} has diverged from main "
                          f"{tip[:8]}: not touched")
        subject = self._git("log", "-1", "--format=%s", tip)
        state, red = self.forge.judge(url, tip, self.forge.gates(url, pull_of(subject)))
        if state == "red":
            return Held(tip, head, red)
        if state != "green":
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
        self.held: Held | None = None
        self.moved: Offer | None = None
        self.looked = 0.0
        #: Monotonic time a green move first waited for an idle tick; cleared by the move.
        self.waiting: float | None = None
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

    def young(self) -> float | None:
        """How young an open tool call must be to hold back the waiting move."""
        waited = time.monotonic() - (self.waiting or time.monotonic())
        return [young for after, young in PATIENCE if waited >= after][-1]

    def step(self, config, idle) -> None:
        """Sets ``moved`` once the checkout moved; raises while the source refuses or fails.

        ``idle(young)`` is true when no body has an open tool call younger than ``young``
        seconds (``None``: none open at all)."""
        source = self._source(config)
        if source is None:
            self.failing = self.offer = self.waiting = None
            return
        if self.offer is not None and self.waiting is None:
            self.waiting = time.monotonic()
            self._say_once(f"{self.offer.new[:8]} is green; waiting for an idle tick")
        if self.offer is not None and time.monotonic() - self.looked >= LOOK_EVERY:
            self.looked = time.monotonic()
            young = self.young()
            if not idle(young):
                self._attempt(source)
                return
            offer, self.offer = self.offer, None
            if source.apply(offer):
                waited = time.monotonic() - self.waiting
                held = "any open call" if young is None else f"open calls under {young:.0f}s"
                self.log(self.home, f"update: fast-forwarded {self.root} {offer.old[:8]} -> {offer.new[:8]}"
                                    f" after waiting {waited:.0f}s ({held} held it)")
                self.moved, self.waiting = offer, None
                return
        self._attempt(source)
        if (held := self.held) is not None:
            self._say_once(f"{held.tip[:8]} is red on {held.check}; staying on {held.head[:8]}")
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
            found = source.poll()
            self.offer = found if isinstance(found, Offer) else None
            self.held = found if isinstance(found, Held) else None
            self.failing = None
        except Exception as exc:
            self.offer = self.held = None
            self.failing = (exc, exc.__traceback__)


def merges(root: Path, old: str, new: str) -> list[str]:
    """``#N`` of each pull request landed between two commits, oldest first,
    merged or squashed. Main's own line only: a branch's commits are not landings."""
    proc = git(root, "log", "--first-parent", "--reverse", "--format=%s", f"{old}..{new}", check=False)
    return [f"#{n}" for line in proc.stdout.splitlines() if (n := pull_of(line)) is not None]


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


def wake_line(facts, install: str, root: Path) -> str:
    """``loom:`` for a wake: the commit this loom runs against main as last fetched,
    the pull requests main holds that it has not loaded, and what its last move loaded."""
    from .ledger import kind_facts
    running = next((str(f.data["commit"]) for f in reversed(kind_facts(facts, "loom.started"))
                    if f.data.get("install") == install and f.data.get("commit")), "")
    if not running:
        return ""
    tip = str(SEEN.get("tip") or "")
    if not tip:
        main = "main not fetched by this loom (update off, or not polled yet)"
    elif tip == running or git(root, "merge-base", "--is-ancestor", tip, running, check=False).returncode == 0:
        main = f"main {tip[:8]}, nothing behind"
    else:
        count = git(root, "rev-list", "--count", f"{running}..{tip}", check=False).stdout.strip() or "?"
        pending = merges(root, running, tip)
        main = (f"main {tip[:8]} is {count} commit{'s' * (count != '1')} ahead"
                + (f", not loaded: {', '.join(pending)}" if pending else ""))
    last = next((f for f in reversed(kind_facts(facts, "loom.moved")) if f.data.get("install") == install), None)
    return f"loom: runs {running[:8]}; {main}" + (f"; last {moved_text(last)}" if last else "") + "\n"
