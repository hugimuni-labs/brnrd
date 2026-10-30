"""Each install's ledger folder travels on ``refs/ledger/<install>`` of the lease remote.

``pack`` commits this install's ``ledger/facts/<install>/`` as a flat tree:
every strand file as it is, and ``loom.jsonl`` cut at line ends into sealed
segments of about ``SEGMENT`` bytes, ``loom.<n>.jsonl``, so a push carries
the one open segment and a tree, never the whole 19 MB file. Each commit's
parent is the previous one, so a ref only fast-forwards and "has the reader
caught up to sha X" is ``merge-base --is-ancestor``.

``sync`` fetches the peers' refs that moved and writes their trees into
``ledger/facts/<peer>/``, appending in place when a file only grew, so the
ledger reader takes the new lines without re-reading everything. Only
complete lines travel. The design: brnrd-self threads/machines/design.md,
section 3.2.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .gitlease import LEDGERS, GitLeaseAuthority
from .home import Home, check_install
from .ledger import mark_changed

SEGMENT = 1 << 20
HEARTBEAT = 60.0


def _complete(raw: bytes) -> bytes:
    return raw[:raw.rfind(b"\n") + 1]


def segments(raw: bytes, size: int = SEGMENT) -> list[bytes]:
    """Cut at the first line end at or past each ``size`` bytes; the last
    piece is the open one. The cut depends only on the bytes, so a file that
    grew keeps every sealed segment it had."""
    out, start = [], 0
    while start < len(raw):
        end = raw.find(b"\n", start + size - 1)
        end = len(raw) if end < 0 else end + 1
        out.append(raw[start:end])
        start = end
    return out


class Shuttle:
    def __init__(self, home: Home, gate: GitLeaseAuthority, *,
                 size: int = SEGMENT, heartbeat: float = HEARTBEAT):
        self.home = home
        self.gate = gate
        self.size = size
        self.heartbeat = heartbeat
        self.mine = home.install_id()
        self.ref = LEDGERS + self.mine
        self.state_path = home.root / "ledger" / "shuttle.json"
        self.blobs: dict[str, tuple[tuple, str]] = {}  # name -> (stamp | bytes len, blob)
        self.sealed: list[str] = []  # blob shas of sealed loom segments, in order
        self.sealed_bytes = 0
        self._beat: float | None = None
        self._seeded = False

    # -- local plumbing -------------------------------------------------

    def _git(self, *args: str, stdin: bytes | None = None) -> str:
        proc = subprocess.run(["git", "-C", str(self.gate.repo), *args], input=stdin,
                              capture_output=True, check=True)
        return proc.stdout.decode().strip()

    def _blob(self, raw: bytes) -> str:
        return self._git("hash-object", "-w", "--stdin", stdin=raw)

    def _local_sha(self, ref: str) -> str:
        try:
            return self._git("rev-parse", "--verify", "-q", ref + "^{commit}")
        except subprocess.CalledProcessError:
            return ""

    def _state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True))
        os.replace(tmp, self.state_path)

    # -- this install's folder ------------------------------------------

    def _entries(self) -> list[tuple[str, str]]:
        entries = []
        for path in sorted(self.home.facts_dir().glob("*.jsonl")):
            stat = path.stat()
            stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if path.name == "loom.jsonl":
                entries.extend(self._loom(path, stat))
                continue
            cached = self.blobs.get(path.name)
            if cached is None or cached[0] != stamp:
                cached = (stamp, self._blob(_complete(path.read_bytes())))
                self.blobs[path.name] = cached
            entries.append((path.name, cached[1]))
        return entries

    def _loom(self, path: Path, stat) -> list[tuple[str, str]]:
        if stat.st_size < self.sealed_bytes:  # rewritten, not grown
            self.sealed, self.sealed_bytes = [], 0
        with path.open("rb") as handle:
            handle.seek(self.sealed_bytes)
            tail = _complete(handle.read())
        pieces = segments(tail, self.size)
        # Every piece but the last is sealed: its cut no longer moves.
        for piece in pieces[:-1]:
            self.sealed.append(self._blob(piece))
            self.sealed_bytes += len(piece)
        shas = list(self.sealed)
        if pieces:
            shas.append(self._blob(pieces[-1]))
        return [(f"loom.{n:04d}.jsonl", sha) for n, sha in enumerate(shas)]

    def pack(self) -> str:
        """Commit the folder onto ``refs/ledger/<me>`` when its tree changed;
        the ref's sha either way ("" when the folder is empty)."""
        entries = self._entries()
        parent = self._local_sha(self.ref)
        if not parent and not self._seeded:
            # A fresh gate repo: start from the remote's copy of our ref, or
            # our first commit would not fast-forward it and the atomic lease
            # push it rides would be refused with it.
            self._seeded = True
            self.gate.git(["fetch", "--no-tags", "--quiet", self.gate.remote,
                           f"+{self.ref}:{self.ref}"])
            parent = self._local_sha(self.ref)
        if not entries:
            return parent
        listing = "".join(f"100644 blob {sha}\t{name}\n" for name, sha in entries)
        tree = self._git("mktree", stdin=listing.encode())
        if parent and self._git("rev-parse", parent + "^{tree}") == tree:
            return parent
        args = ["-c", "user.name=brnrd", "-c", "user.email=loom@brnrd.dev",
                "commit-tree", tree, "-m", f"ledger {self.mine}"]
        if parent:
            args += ["-p", parent]
        sha = self._git(*args)
        self._git("update-ref", self.ref, sha)
        return sha

    def riders(self) -> dict[str, str]:
        """For the gate: this install's ledger rides every lease push."""
        sha = self.pack()
        return {self.ref: sha} if sha else {}

    # -- the peers' folders ---------------------------------------------

    def sync(self, *, holding: bool) -> list[str]:
        """Poll, heartbeat our ref when not holding (the holder's rides its
        renewals), fetch the peers' refs that moved, and write their folders.
        Returns the peers whose folder changed. Network failures leave
        everything as it was."""
        from .gitlease import Unreachable

        now = self.gate.clock()
        if self._beat is not None and now - self._beat < self.heartbeat:
            return []
        self._beat = now
        if not holding:
            sha = self.pack()
            seen = self.gate.seen.get(self.ref)
            if sha and (seen is None or seen.sha != sha):
                self.gate.git(["push", "--porcelain", self.gate.remote, f"{sha}:{self.ref}"])
        try:
            refs = self.gate.poll()
        except Unreachable:
            return []
        state = self._state()
        done = state.setdefault("materialized", {})
        wanted = {}
        for ref, sha in refs.items():
            if not ref.startswith(LEDGERS) or ref == self.ref:
                continue
            peer = ref[len(LEDGERS):]
            try:
                check_install(peer)
            except ValueError:
                continue
            if done.get(peer) != sha:
                wanted[peer] = sha
        if not wanted:
            return []
        fetch = self.gate.git(["fetch", "--no-tags", "--quiet", self.gate.remote,
                               *(f"+{LEDGERS}{peer}:{LEDGERS}{peer}" for peer in wanted)])
        if fetch.returncode != 0:
            return []
        changed = []
        for peer, sha in wanted.items():
            if self._local_sha(LEDGERS + peer) != sha:
                continue
            self._write(peer, sha)
            done[peer] = sha
            changed.append(peer)
        self._save(state)
        return changed

    def _write(self, peer: str, sha: str) -> None:
        folder = self.home.root / "ledger" / "facts" / peer
        folder.mkdir(parents=True, exist_ok=True)
        for line in self._git("ls-tree", sha).splitlines():
            meta, _, name = line.partition("\t")
            blob = meta.split()[2]
            if "/" in name or not name.endswith(".jsonl"):
                continue
            raw = subprocess.run(["git", "-C", str(self.gate.repo), "cat-file", "blob", blob],
                                 capture_output=True, check=True).stdout
            path = folder / name
            try:
                have = path.read_bytes()
            except FileNotFoundError:
                have = None
            if have == raw:
                continue
            if have is not None and raw.startswith(have):
                with path.open("ab") as handle:
                    handle.write(raw[len(have):])
            else:
                tmp = folder / f".{name}.tmp"
                tmp.write_bytes(raw)
                os.replace(tmp, path)
            mark_changed(folder.parent)

    def caught_up(self, holder: str, sha: str) -> bool:
        """The previous holder's ledger at ``sha`` is in our folder for it."""
        if not sha or holder == self.mine:
            return True
        done = self._state().get("materialized", {}).get(holder)
        if not done:
            return False
        return subprocess.run(["git", "-C", str(self.gate.repo), "merge-base",
                               "--is-ancestor", sha, done],
                              capture_output=True).returncode == 0
