"""Leases held as refs on the self's remote, swapped by ``git push --force-with-lease``.

A lease is ``refs/leases/<what>``: a commit with an empty tree whose message is
one JSON line, ``{holder, gen, ttl, ledger, released}``. Acquire, renew and
release are one atomic push that names the sha this loom last saw, so of two
racing pushes exactly one lands. A renewal keeps ``gen`` and changes the sha;
a takeover bumps ``gen``.

No two machines compare clocks. The holder's lease runs ``ttl`` from the moment
it began the push that landed. A follower counts ``ttl + margin`` from the
moment it first saw the current sha. Both count on a clock that keeps running
while the machine sleeps (``boot_clock``), never on the wall clock.

A renew that fails for want of a network keeps the lease when nobody else is
contesting it: no peer's ``refs/ledger/*`` moved within ``contest_window`` of
the last good poll. A strict authority fences anyway. The design and its
receipts: brnrd-self threads/machines/design.md, section 3.1.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from brr.daemon2.leases import Lease

LEASES = "refs/leases/"
LEDGERS = "refs/ledger/"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def boot_clock() -> float:
    """Seconds on a clock that counts sleep: ``CLOCK_BOOTTIME`` on Linux,
    ``CLOCK_MONOTONIC`` on macOS (``time.monotonic`` there stops in sleep)."""
    if sys.platform.startswith("linux"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.clock_gettime(time.CLOCK_MONOTONIC)


class Unreachable(RuntimeError):
    """The remote did not answer; nothing is known about the ref."""


Runner = Callable[[list[str]], subprocess.CompletedProcess]


@dataclass
class _Seen:
    sha: str
    at: float
    body: dict = field(default_factory=dict)


class GitLeaseAuthority:
    """The ``LeaseAuthority`` lease calls over git refs.

    ``git`` runs one git call that talks to the remote (``push``,
    ``ls-remote``, ``fetch``) in ``repo``, a local repository that holds the
    lease commits; ``remote`` is the name or URL those calls name.
    ``riders`` returns extra ``{ref: sha}`` pushed atomically with every
    lease push (an install's ``refs/ledger/<install>``); the rider on
    ``refs/ledger/<holder>`` is written into the lease as ``ledger``.
    """

    def __init__(self, repo: Path, remote: str, git: Runner, *,
                 clock: Callable[[], float] = boot_clock, margin: float = 5.0,
                 strict: bool = False, contest_window: float = 600.0,
                 riders: Callable[[], dict[str, str]] = dict):
        self.repo = Path(repo)
        self.remote = remote
        self.git = git
        self.clock = clock
        self.margin = margin
        self.strict = strict
        self.contest_window = contest_window
        self.riders = riders
        self.seen: dict[str, _Seen] = {}
        self.polled: float | None = None
        self.held: dict[str, tuple[Lease, str, float]] = {}  # what -> (lease, sha, valid_until)
        self.graced: set[str] = set()
        self.prior: dict[str, tuple[str, str]] = {}  # what -> (holder, ledger) we took over

    # -- local plumbing -------------------------------------------------

    def _local(self, *args: str, stdin: str | None = None) -> str:
        proc = subprocess.run(["git", "-C", str(self.repo), *args], input=stdin,
                              capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    def _commit(self, body: dict) -> str:
        # A nonce: two renewals in one second would otherwise be one sha,
        # and a follower would never see the second.
        line = json.dumps(dict(body, nonce=uuid.uuid4().hex[:12]),
                          sort_keys=True, separators=(",", ":"))
        return self._local("-c", "user.name=brnrd", "-c", "user.email=loom@brnrd.dev",
                           "commit-tree", EMPTY_TREE, "-m", line)

    # -- observing --------------------------------------------------------

    def poll(self) -> dict[str, str]:
        """``ls-remote`` the lease and ledger refs; note when each sha changed."""
        proc = self.git(["ls-remote", self.remote, LEASES + "*", LEDGERS + "*"])
        if proc.returncode != 0:
            raise Unreachable(proc.stderr.strip() or f"ls-remote exited {proc.returncode}")
        now = self.clock()
        refs = {}
        for line in proc.stdout.splitlines():
            sha, _, ref = line.partition("\t")
            if sha and ref:
                refs[ref] = sha
        moved = [ref for ref, sha in refs.items()
                 if ref not in self.seen or self.seen[ref].sha != sha]
        for ref in list(self.seen):
            if ref not in refs:
                del self.seen[ref]
        for ref in moved:
            self.seen[ref] = _Seen(refs[ref], now)
        wanted = [ref for ref in moved if ref.startswith(LEASES)]
        if wanted:
            fetch = self.git(["fetch", "--no-tags", "--quiet", self.remote,
                              *(f"+{ref}:refs/seen/{ref[5:]}" for ref in wanted)])
            if fetch.returncode != 0:
                raise Unreachable(fetch.stderr.strip() or "fetch failed")
            for ref in wanted:
                self.seen[ref].body = self._body(refs[ref])
        self.polled = now
        return refs

    def _body(self, sha: str) -> dict:
        try:
            body = json.loads(self._local("log", "-1", "--format=%B", sha))
        except (subprocess.CalledProcessError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    def view(self, what: str) -> dict | None:
        """The lease as last polled, with ``expired`` judged on this clock."""
        seen = self.seen.get(LEASES + what)
        if seen is None:
            return None
        ttl = float(seen.body.get("ttl", 0) or 0)
        expired = bool(seen.body.get("released")) or (
            self.clock() - seen.at > ttl + self.margin)
        return dict(seen.body, sha=seen.sha, expired=expired)

    def contested(self, holder: str) -> bool:
        """A peer's ledger ref moved within ``contest_window`` of the last good poll."""
        if self.polled is None:
            return False
        mine = LEDGERS + holder
        return any(ref.startswith(LEDGERS) and ref != mine
                   and self.polled - seen.at <= self.contest_window
                   for ref, seen in self.seen.items())

    # -- the swap ---------------------------------------------------------

    def _push(self, what: str, body: dict, expected: str) -> tuple[str, float]:
        """Push one lease commit; ``(sha, t_send)`` on success.

        Returns ``("", t_send)`` when the swap lost, raises ``Unreachable``
        when nothing is known.
        """
        riders = self.riders()
        ledger = riders.get(LEDGERS + body["holder"])
        if ledger:
            body = dict(body, ledger=ledger)
        sha = self._commit(body)
        ref = LEASES + what
        t_send = self.clock()
        proc = self.git(["push", "--atomic", "--porcelain",
                         f"--force-with-lease={ref}:{expected}", self.remote,
                         f"{sha}:{ref}", *(f"{v}:{k}" for k, v in riders.items())])
        if proc.returncode == 0:
            self.seen[ref] = _Seen(sha, t_send, body)
            return sha, t_send
        report = proc.stdout + proc.stderr
        if "[rejected]" in report or "[remote rejected]" in report or "stale info" in report:
            return "", t_send
        raise Unreachable(proc.stderr.strip() or f"push exited {proc.returncode}")

    def acquire(self, what: str, holder: str, ttl: float, *,
                capabilities: set[str] | None = None,
                required: set[str] | None = None, floor: int = 1) -> Lease | None:
        """Take the lease if it is free or expired; a takeover bumps ``gen``,
        never below ``floor`` (the gens a single-machine home already used)."""
        if not holder or ttl <= 0:
            raise ValueError("holder and positive ttl required")
        if what in self.held and self.held[what][0].holder == holder:
            return self.renew(self.held[what][0], ttl)
        try:
            self.poll()
        except Unreachable:
            return None
        current = self.view(what)
        if current is not None and not current["expired"] and current.get("holder") != holder:
            return None
        gen = max(int(current.get("gen", 0)) + 1 if current else 1, floor)
        expected = current["sha"] if current else ""
        # Whose ledger a new holder must have read before it grants.
        self.prior[what] = ((current.get("holder", ""), current.get("ledger", ""))
                            if current else ("", ""))
        try:
            sha, t_send = self._push(what, {"holder": holder, "gen": gen, "ttl": ttl,
                                            "released": False}, expected)
        except Unreachable:
            return None
        if not sha:
            self.seen.pop(LEASES + what, None)  # re-read before believing anything
            return None
        lease = Lease(what, holder, gen, t_send + ttl, tuple(sorted(capabilities or ())))
        self.held[what] = (lease, sha, t_send + ttl)
        self.graced.discard(what)
        return lease

    def renew(self, lease: Lease, ttl: float) -> Lease | None:
        held = self.held.get(lease.what)
        if held is None or held[0].gen != lease.gen or held[0].holder != lease.holder:
            return None
        _old, sha, _until = held
        try:
            new, t_send = self._push(lease.what, {"holder": lease.holder, "gen": lease.gen,
                                                  "ttl": ttl, "released": False}, sha)
        except Unreachable:
            if self.strict or self.contested(lease.holder):
                return None if not self.authorize(lease) else lease
            self.graced.add(lease.what)
            return lease
        if not new:
            self._drop(lease.what)
            return None
        renewed = Lease(lease.what, lease.holder, lease.gen, t_send + ttl, lease.capabilities)
        self.held[lease.what] = (renewed, new, t_send + ttl)
        self.graced.discard(lease.what)
        return renewed

    def authorize(self, lease: Lease) -> bool:
        """Held at this gen, and inside ``valid_until - margin`` on this clock,
        or past it with the renew failing for want of a network and nobody
        contesting. No network call."""
        held = self.held.get(lease.what)
        if held is None or held[0].gen != lease.gen or held[0].holder != lease.holder:
            return False
        if self.clock() < held[2] - self.margin:
            return True
        return (lease.what in self.graced and not self.strict
                and not self.contested(lease.holder))

    def release(self, lease: Lease) -> bool:
        held = self.held.get(lease.what)
        if held is None or held[0].gen != lease.gen:
            return False
        try:
            sha, _t = self._push(lease.what, {"holder": lease.holder, "gen": lease.gen,
                                              "ttl": 0, "released": True}, held[1])
        except Unreachable:
            sha = ""
        self._drop(lease.what)
        return bool(sha)

    def _drop(self, what: str) -> None:
        self.held.pop(what, None)
        self.graced.discard(what)
        self.seen.pop(LEASES + what, None)
