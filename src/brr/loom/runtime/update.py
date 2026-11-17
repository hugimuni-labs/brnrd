"""A green main tip, or with ``update = stable`` the newest green release tag, is offered to the resident;
only ``update take`` merges it. Both move the same checkout the same way: stable is a target, not an install kind.

Offers and decisions live in the ledger. The loop polls off-thread, sends one
notice per open offer, and re-execs only after take checked the merged tree.
The existing CI gate judgment and progressively looser idle gate are shared.
"""

from __future__ import annotations

import json
import os
import sys
import fcntl
from functools import lru_cache
from contextlib import contextmanager
import re
import subprocess
import threading
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import resolve_secret
from .selfrepo import git

EVERY = 300
#: Seconds between looks for an idle tick while an offer waits: a look reads the ledger.
LOOK_EVERY = 5
#: Check conclusions that count as passed; any other completed one is red.
PASSED = {"success", "neutral", "skipped"}
#: Seconds a ``since`` judgment with no run started since reads pending: GitHub
#: registers a rerun's check runs a few seconds after ``gh run rerun`` returns.
RERUN_GRACE = 120
#: How many unchecked commits on main's tip ``poll`` steps over: the receipt line's push after a release is one.
UNCHECKED = 5
# UTF-8 bytes for the PR detail shared by the letter and its offer fact.
DETAIL_BYTES = 4500
BODY_CHARS = 300


def _moment(text) -> datetime | None:
    """A GitHub or fact timestamp, aware; None when absent or unreadable."""
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
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
    pulls: tuple[dict, ...] = ()
    #: What ``new`` is: ``main``, or the release tag stable follows (``v0.7.2``).
    name: str = "main"


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

    def description(self, url: str, number: int) -> dict:
        data = self._get(slug(url), f"pulls/{number}")
        return {"number": number, "title": data["title"], "body": data.get("body") or ""}

    def _runs(self, repo: str, sha: str) -> list[dict]:
        return self._get(repo, f"commits/{sha}/check-runs?per_page=100").get("check_runs") or []

    def ran(self, url: str, sha: str) -> bool:
        """Whether any check ran on ``sha``. A push made with a workflow's own token starts none: receipt-line.yml's
        README commit after every release sat on main's tip with no runs, read pending for ever, and held every
        following loom until the next merge (0.7.1, 2026-11-10: #2679 merged 15:37Z, unloaded at 16:15Z)."""
        repo = slug(url)
        return repo is None or bool(self._runs(repo, sha))

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

    def judge(self, url: str, sha: str, gates: set[str] | None = None, since: str | None = None,
              now: datetime | None = None) -> tuple[str, str]:
        """(``verdict``, the first red check's name or ``""``), over ``gates`` when given.

        ``since``, a park's time, drops the runs that had concluded before it once
        any run started at or after it: a park made after ``gh run rerun`` waits
        for the new attempt, not the old red. With none started since, the first
        ``RERUN_GRACE`` seconds read pending (the rerun not yet registered), then
        every run counts, as without ``since``."""
        repo = slug(url)
        if repo is None:
            raise Refused(f"origin {url} is not on GitHub: no CI to read")
        runs = [r for r in self._runs(repo, sha) if gates is None or r.get("name") in gates]
        parked = _moment(since) if since else None
        if parked is not None:
            def before(r, field):
                moment = _moment(r.get(field))
                return moment is not None and moment < parked
            if any(not before(r, "started_at") for r in runs):
                # A run queued but not started counts as started since: it is the rerun.
                runs = [r for r in runs if not before(r, "completed_at")]
            elif ((now or datetime.now(timezone.utc)) - parked).total_seconds() < RERUN_GRACE:
                return "pending", ""
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

    def verdict(self, url: str, sha: str, since: str | None = None) -> str:
        """``green`` when every run passed, ``red`` when one failed, else ``pending``."""
        return self.judge(url, sha, since=since)[0]


#: Main's tip as this process last fetched it, for the wake's ``loom:`` line.
SEEN: dict = {}


class DevSource:
    """A git checkout that can merge origin main into any current branch."""

    def __init__(self, root: Path, forge):
        self.root, self.forge = root, forge
        self.descriptions = {}

    def _git(self, *args: str, check: bool = True) -> str:
        return git(self.root, *args, check=check).stdout.strip()

    def _ancestor(self, older: str, newer: str) -> bool:
        return git(self.root, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0

    def refusal(self) -> str:
        """Why the checkout may not move now; empty when it may."""
        dirty = self._git("status", "--porcelain", "--untracked-files=no").splitlines()
        if dirty:
            return (f"checkout {self.root} has {len(dirty)} uncommitted change"
                    f"{'s' * (len(dirty) > 1)} ({dirty[0].strip()}): commit tracked changes first; not touched")
        return ""

    def poll(self) -> Offer | Held | None:
        """Off the loop: the move main's green tip offers, the red check holding a red one."""
        url = self._git("remote", "get-url", "origin")
        self._git("fetch", "--quiet", "--no-tags", "origin", "refs/heads/main")
        tip, head = self._git("rev-parse", "FETCH_HEAD"), self._git("rev-parse", "HEAD")
        SEEN.update(tip=tip, at=time.time(), name="main")
        if tip == head or self._ancestor(tip, head):
            return None
        # A tip no check ran on and no pull request gated is never judged: the move goes as far as its first parent
        # that is (``GitHubChecks.ran``). A merge whose runs are not registered yet still waits, as pending.
        target = tip
        subject = self._git("log", "-1", "--format=%s", target)
        for _ in range(UNCHECKED):
            if pull_of(subject) is not None or self.forge.ran(url, target):
                break
            parent = self._git("rev-parse", f"{target}^1")
            if self._ancestor(parent, head):
                return None
            target = parent
            subject = self._git("log", "-1", "--format=%s", target)
        return self._judged(url, head, target, subject)

    def _judged(self, url: str, head: str, target: str, subject: str, name: str = "main") -> Offer | Held | None:
        """The offer ``target`` makes once its checks are green; the red check holding it; else nothing yet."""
        state, red = self.forge.judge(url, target, self.forge.gates(url, pull_of(subject)))
        if state == "red":
            return Held(target, head, red)
        if state != "green":
            return None
        if self._ancestor(target, head):
            return None
        base = self._git("merge-base", head, target)
        pulls = []
        subjects = self._git("log", "--first-parent", "--reverse", "--format=%s", f"{base}..{target}")
        for subject in subjects.splitlines():
            number = pull_of(subject)
            if number is None:
                continue
            key = (url, number)
            if key not in self.descriptions:
                try:
                    data = self.forge.description(url, number)
                except Exception:
                    # Descriptive enrichment must never block a green update.
                    data = {"number": number, "title": subject, "body": ""}
                self.descriptions[key] = pull_summary(url, number, data)
            pulls.append(self.descriptions[key])
        return Offer(head, target, tuple(pulls), name)

    def apply(self, offer: Offer) -> tuple[str, str, int]:
        """Merge on the current branch; abort conflicts without retaining tree changes."""
        if why := self.refusal():
            raise Refused(why)
        if self._git("rev-parse", "--verify", "-q", "MERGE_HEAD", check=False):
            raise Refused("a merge is already in progress; finish or abort it first")
        before = self._git("rev-parse", "HEAD")
        own = int(self._git("rev-list", "--count", f"{offer.new}..{before}"))
        mode = "merge" if own else "fast-forward"
        if self._ancestor(offer.new, before):
            return before, "merge" if own else "fast-forward", own
        result = git(self.root, "-c", "core.hooksPath=/dev/null", "merge", "--no-edit",
                     "--no-autostash", "--ff", offer.new, check=False)
        if result.returncode:
            conflicts = self._git("diff", "--name-only", "--diff-filter=U")
            if self._git("rev-parse", "--verify", "-q", "MERGE_HEAD", check=False):
                self._git("merge", "--abort")
            raise Refused("update merge aborted; checkout left as it was. "
                          + ("Conflicting files: " + conflicts.replace("\n", ", ") if conflicts
                             else result.stderr.strip()))
        return before, mode, own


#: A release tag stable follows: ``v`` and three numbers. A suffix (``v0.8.0rc1``) is a pre-release, never followed.
RELEASE = re.compile(r"refs/tags/v(\d+)\.(\d+)\.(\d+)")


def newest_release(listing: str) -> tuple[str, str] | None:
    """``(tag, commit)`` of the highest release in ``git ls-remote --tags`` output; an annotated tag's
    peeled ``^{}`` line names its commit."""
    commits, numbers = {}, {}
    for line in listing.splitlines():
        sha, _, ref = line.partition("\t")
        peeled = ref.endswith("^{}")
        ref = ref.removesuffix("^{}")
        if not (match := RELEASE.fullmatch(ref)):
            continue
        tag = ref.removeprefix("refs/tags/")
        numbers[tag] = tuple(map(int, match.groups()))
        if peeled or tag not in commits:
            commits[tag] = sha
    if not numbers:
        return None
    tag = max(numbers, key=numbers.get)
    return tag, commits[tag]


class ReleaseSource(DevSource):
    """The same checkout, offered origin's newest release tag instead of main's tip.

    A release tag is a merge on main (release.yml checks it), so it is judged by main's own gate, merged by
    ``apply`` like any offer and rolled back by the same take check. A checkout ahead of every release (one
    installed from main, then set to stable) stays where it is until a release passes it."""

    def poll(self) -> Offer | Held | None:
        url = self._git("remote", "get-url", "origin")
        found = newest_release(self._git("ls-remote", "--tags", "origin", "refs/tags/v*"))
        if found is None:
            return None
        tag, _ = found
        self._git("fetch", "--quiet", "--no-tags", "origin", f"refs/tags/{tag}")
        tip, head = self._git("rev-parse", "FETCH_HEAD^{commit}"), self._git("rev-parse", "HEAD")
        SEEN.update(tip=tip, at=time.time(), name=tag)
        if tip == head or self._ancestor(tip, head):
            return None
        return self._judged(url, head, tip, self._git("log", "-1", "--format=%s", tip), tag)


SOURCES = {"dev": DevSource, "stable": ReleaseSource}


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
        self.reconciled = None
        self.pending = self.loading = False
        self.looked = 0.0
        self.source = None
        self.source_mode = None
        #: Monotonic time a green move first waited for an idle tick; cleared by the move.
        self.waiting: float | None = None
        self.said: set[str] = set()
        #: (stat of .git/config, the mode its brnrd.update gave) for ``update = auto``.
        self.checkout_mode: tuple[tuple | None, str] | None = None

    def _say_once(self, text: str) -> None:
        if text not in self.said:
            self.said.add(text)
            self.log(self.home, f"update: {text}")

    def _checkout_mode(self) -> str:
        """``auto``: the checkout's own ``brnrd.update`` (stable|dev), else dev; off when it is no checkout.

        The npm launcher installs a release tag's checkout before any home config exists, so the
        checkout carries its following setting itself; an explicit home ``update =`` still wins.
        """
        if not (self.root / ".git").exists():
            return "off"
        try:  # read again only when the checkout's config changed: step runs every tick
            seen = (self.root / ".git" / "config").stat()
            stamp = (seen.st_mtime_ns, seen.st_ino, seen.st_size)
        except OSError:
            stamp = None
        if self.checkout_mode is None or self.checkout_mode[0] != stamp:
            try:
                found = subprocess.run(["git", "-C", str(self.root), "config", "--get", "brnrd.update"],
                                       capture_output=True, text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                found = ""
            self.checkout_mode = (stamp, found if found in ("stable", "dev") else "dev")
        return self.checkout_mode[1]

    def _source(self, config):
        mode = config.update
        if mode == "auto":
            mode = self._checkout_mode()
        if mode == "off":
            return None
        if mode not in SOURCES:
            self._say_once(f"update = {mode} has no source yet; staying at this version")
            return None
        if not (self.root / ".git").exists():
            self._say_once(f"update = {mode}, but {self.root} is not a git checkout; staying put")
            return None
        # A source lives across polls: an open offer must not repeatedly fetch
        # descriptions already read (including unavailable descriptions).
        if self.source is None or self.source_mode != (mode, config.forge_token):
            self.source = SOURCES[mode](self.root, forge_for(config))
            self.source_mode = (mode, config.forge_token)
        return self.source

    def young(self) -> float | None:
        """How young an open tool call must be to hold back the waiting move."""
        waited = time.monotonic() - (self.waiting or time.monotonic())
        return [young for after, young in PATIENCE if waited >= after][-1]

    def step(self, config, idle) -> None:
        """Offer without moving; a checked take alone enables the idle reload."""
        from .ledger import shared_reader, kind_facts
        source = self._source(config)
        self.pending = self.loading = False
        if source is None:
            self.failing = self.offer = self.waiting = None
            return
        facts = shared_reader(self.home).now(self.home)
        self.pending = open_offer(facts, self.home, self.root) is not None
        checked = latest(facts, self.home, self.root, "update.checked")
        self.reconciled = checked.id if checked else None
        # A start containing the checked tree consumed this take. A later own
        # commit, hand pull or rollback must not turn that old take pending again.
        started = next((f for f in reversed(kind_facts(facts, "loom.started"))
                        if f.data.get("install") == self.home.install_id()), None)
        consumed = checked and started and started.at >= checked.at and (
            started.at > checked.at or (
                started.data.get("commit") and source._ancestor(checked.data["new"], started.data["commit"])))
        if checked and checked.data["ok"] and not consumed:
            self.loading = True
            if source._git("rev-parse", "HEAD") != checked.data["new"]:
                raise Refused("checkout changed after update take; run update take again to check it")
            if why := source.refusal():
                raise Refused(why)
            if self.waiting is None:
                self.waiting = time.monotonic()
                self._say_once(f"{checked.data['new'][:8]} checked; waiting for an idle tick")
            if time.monotonic() - self.looked >= LOOK_EVERY:
                self.looked = time.monotonic()
                young = self.young()
                if idle(young):
                    self.moved = Offer(checked.data["old"], checked.data["new"])
                    self.log(self.home, f"update: loading checked {checked.data['mode']} "
                             f"{checked.data['new'][:8]} (" +
                             ("any open call" if young is None else f"open calls under {young:.0f}s") + " held it)")
            return
        self.waiting = None
        if self.offer is not None:
            found, self.offer = self.offer, None
            if not source._ancestor(found.new, source._git("rev-parse", "HEAD")):
                offer_update(self.home, self.root, facts, found)
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
    method = (f", {data['mode']} over {data.get('own', 0)} own commits" if data.get("mode") else "")
    return f"loom moved {str(data.get('from'))[:8]} → {str(data.get('to'))[:8]}, loaded {loaded}{method}"


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
    tip, name = str(SEEN.get("tip") or ""), SEEN.get("name") or "main"
    if not tip:
        main = "main not fetched by this loom (update off, or not polled yet)"
    elif tip == running or git(root, "merge-base", "--is-ancestor", tip, running, check=False).returncode == 0:
        main = f"{name} {tip[:8]}, nothing behind"
    else:
        count = git(root, "rev-list", "--count", f"{running}..{tip}", check=False).stdout.strip() or "?"
        pending = merges(root, running, tip)
        main = (f"{name} {tip[:8]} is {count} commit{'s' * (count != '1')} ahead"
                + (f", not loaded: {', '.join(pending)}" if pending else ""))
    last = next((f for f in reversed(kind_facts(facts, "loom.moved")) if f.data.get("install") == install), None)
    return f"loom: runs {running[:8]}; {main}" + (f"; last {moved_text(last)}" if last else "") + "\n"


@lru_cache(maxsize=8)
def lock_path(root: Path) -> Path:
    common = git(root, "rev-parse", "--git-common-dir").stdout.strip()
    return (root / common).resolve() / "brnrd-update.lock"


@contextmanager
def checkout_lock(root: Path, *, blocking=True):
    """Serialize take and the loop's source observer, including across homes."""
    with lock_path(root).open("a") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)


def latest(facts, home, root, kind):
    from .ledger import kind_facts
    return next((f for f in reversed(kind_facts(facts, kind)) if f.kind == kind
                 and f.data.get("install") == home.install_id()
                 and f.data.get("root") == str(root.resolve())), None)


def open_offer(facts, home, root):
    offered = latest(facts, home, root, "update.offered")
    closed = latest(facts, home, root, "update.decided")
    return offered if offered and (not closed or closed.data["offer"] != offered.data["offer"]) else None


def record(home, root, kind, **data):
    from .ledger import Fact, append
    return append(home, Fact(kind=kind, by=f"loom:{home.install_id()}",
                            data={"install": home.install_id(), "root": str(root.resolve()), **data}))


def offer_update(home, root, facts, found):
    from .home import mint
    from .loom import notice, notice_id
    previous = latest(facts, home, root, "update.offered")
    current = open_offer(facts, home, root)
    if previous and previous.data["new"] == found.new and not current:
        return  # held or taken: one decision per green tip
    subject = current.data["subject"] if current else f"{home.install_id()}:{mint(12)}"
    letter = notice_id("update", subject)
    source = DevSource(root, None)
    own = int(source._git("rev-list", "--count", f"{found.new}..HEAD"))
    dirty = bool(source._git("status", "--porcelain", "--untracked-files=no"))
    pulls, details, omitted = offer_details(found.pulls)
    # The notice id is stable while its target advances. Record first, so take
    # reads the offered target even if the loop exits before sending the notice.
    offered = previous
    if not previous or previous.data["new"] != found.new:
        offered = record(home, root, "update.offered", offer=letter, subject=subject, old=found.old,
                         new=found.new, own=own, dirty=dirty, pulls=pulls, omitted=omitted, ref=found.name)
    if not any(f.id == letter for f in facts):
        what = (("Main has a green update" if found.name == "main" else f"Release {found.name} is out and green")
                + f", {found.new[:8]}, for {root}. "
                f"Your checkout carries {own} own commits; "
                f"tracked files {'have uncommitted changes' if dirty else 'are committed'}.\n\n"
                + (details or "No merged pull requests since the merge base.") +
                "\n\nRead what changed; look over your own additions, lessons and habits. "
                "Take it when you judge it right, or hold and say why. "
                "Tell your person only what concerns them, as they have said they prefer. "
                "A newer green tip updates this offer without a second letter; brnrd runtime update shows its current contents.")
        # notice derives its id; reuse exactly the subject minted above.
        notice(home, facts, "update", subject, what,
               "brnrd runtime update take; or brnrd runtime update hold --why 'your reason'")
    return offered


def pull_summary(url, number, data):
    """Keep identity and a stated excerpt, never a whole PR body."""
    body = str(data.get("body") or "").strip().split("\n\n", 1)[0]
    if len(body) > BODY_CHARS:
        body = body[:BODY_CHARS] + f"… [excerpt: first {BODY_CHARS} characters]"
    elif body != str(data.get("body") or "").strip():
        body += " [excerpt: first paragraph]"
    repo = slug(url)
    return {"number": number, "title": str(data.get("title") or ""),
            "url": f"https://github.com/{repo}/pull/{number}" if repo else f"{url}#pull/{number}",
            "body": body}


def offer_details(pulls):
    """One bounded detail shared by the fact and notice; show lists all landings."""
    kept, chunks, used = [], [], 0
    for pull in pulls:
        chunk = f"#{pull['number']} · {pull['title']}\n{pull['body']}\n{pull['url']}"
        size = max(len(chunk.encode("utf-8")), len(json.dumps(pull).encode("utf-8"))) + 2
        if used + size > DETAIL_BYTES:
            break
        kept.append(pull)
        chunks.append(chunk)
        used += size
    omitted = len(pulls) - len(kept)
    if omitted:
        chunks.append(f"and {omitted} more: brnrd runtime update")
    return kept, "\n\n".join(chunks), omitted


def check_tree(root: Path, home) -> None:
    """Fresh interpreter, candidate imports and the real home's config; no service starts."""
    code = """
import sys
from pathlib import Path
root, home = map(Path, sys.argv[1:])
sys.path.insert(0, str(root / 'src'))
import brr
assert Path(brr.__file__).resolve().is_relative_to(root / 'src'), brr.__file__
from brr.loom.runtime import loom, __main__
from brr.loom.runtime.config import load_config
load_config(home)
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    try:
        # Ignore existing bytecode as well as avoiding writes into the checkout:
        # git can give two different sources the same timestamp and byte length.
        with tempfile.TemporaryDirectory(prefix="brnrd-update-check-") as cache:
            result = subprocess.run([sys.executable, "-B", "-X", f"pycache_prefix={cache}",
                                     "-c", code, str(root.resolve()), str(home.root)],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise Refused("merged tree check timed out after 60s") from exc
    if result.returncode:
        raise Refused("merged tree check failed: " + (result.stderr or result.stdout).strip())


def decide(home, root, action, why=""):
    from .config import load_config
    from .ledger import read_facts, Fact, append
    with checkout_lock(root):
        updater = Updater(home, lambda *args: None, root)
        source = updater._source(load_config(home.root))
        if source is None:
            raise Refused("updates are off or this install has no update source")
        facts = read_facts(home)
        offered = open_offer(facts, home, root)
        if offered is None and action == "take":
            decided = latest(facts, home, root, "update.decided")
            if decided and decided.data["action"] == "take":
                offered = latest(facts, home, root, "update.offered")
        if offered is None:
            raise Refused("no open update offer")
        data = offered.data
        if action == "hold":
            if not why.strip():
                raise Refused("holding an update requires --why")
            record(home, root, "update.decided", offer=data["offer"], tip=data["new"],
                   action="hold", why=why)
        else:
            print(f"Taking offered {data.get('ref', 'main')} {data['new']} into {source._git('rev-parse', '--abbrev-ref', 'HEAD')}")
            before = source._git("rev-parse", "HEAD")
            mode, own, ok = "merge", 0, False
            try:
                before, mode, own = source.apply(Offer(data["old"], data["new"]))
                from .launcher import dependencies, reinstall
                changed = dependencies(root, before, source._git("rev-parse", "HEAD"))
                try:
                    if changed:
                        reinstall(root, sys.executable)
                    check_tree(root, home)
                except BaseException as failure:
                    source._git("reset", "--hard", before)
                    if changed:
                        try:
                            reinstall(root, sys.executable)
                        except Exception as restore:
                            raise Refused(f"take failed ({failure}); checkout restored to {before}, "
                                          f"but dependency restoration failed ({restore}); "
                                          "the launcher will retry it before starting") from failure
                    raise
                ok = True
            finally:
                record(home, root, "update.checked", offer=data["offer"], old=before,
                       new=source._git("rev-parse", "HEAD"), tip=data["new"],
                       base=source._git("merge-base", data["old"], data["new"]),
                       mode=mode, own=own, ok=ok)
            why = f"checked {mode} over {own} own commits; reload at the next idle tick"
            record(home, root, "update.decided", offer=data["offer"], tip=data["new"],
                   action="take", why=why)
        append(home, Fact(kind="note", by=f"loom:{home.install_id()}",
                          data={"re": data["offer"], "why": why}))
        return why


def main(argv=None):
    from .home import home_for
    from .config import ConfigError
    from .receipts import source_root
    from .refusals import Parser, refuse
    parser = Parser(verb="update", prog="brnrd runtime update")
    parser.add_argument("action", nargs="?", default="show", choices=("show", "take", "hold"))
    parser.add_argument("--why", default="")
    parser.add_argument("--home")
    parser.add_argument("--room")
    args = parser.parse_args(argv)
    try:
        home, root = home_for(args.home, args.room), source_root()
        if args.action == "show":
            from .ledger import read_facts
            offered = open_offer(read_facts(home), home, root)
            if offered is None:
                raise Refused("no open update offer")
            data = dict(offered.data)
            # The durable offer and wake stay bounded; inspection can list every
            # landing directly from the offered range without a forge request.
            source = DevSource(root, None)
            base = source._git("merge-base", data["old"], data["new"])
            url = source._git("remote", "get-url", "origin")
            subjects = source._git("log", "--first-parent", "--reverse", "--format=%s", f"{base}..{data['new']}")
            data["landings"] = [pull_summary(url, n, {"title": line})
                                for line in subjects.splitlines() if (n := pull_of(line)) is not None]
            print(json.dumps(data, indent=2))
        else:
            print(decide(home, root, args.action, args.why))
        return 0
    except (Refused, subprocess.SubprocessError, OSError, ConfigError) as exc:
        return refuse("update", exc)
