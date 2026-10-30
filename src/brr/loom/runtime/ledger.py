"""Append-only facts. One writer, the loom; a test may inject a letter."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from collections import defaultdict, OrderedDict
from datetime import datetime
from pathlib import Path

from brr.daemon2.facts import Fact, keep, union

from .home import Home, mint, thread_of
from .registry import admit, resource_of


class LedgerConflict(RuntimeError):
    """The same fact id already exists with different content."""


def _content(fact: Fact) -> str:
    return json.dumps(
        [fact.kind, fact.by, fact.data], sort_keys=True, separators=(",", ":"),
    )


def _freeze(data: dict) -> dict:
    """Return the same deep JSON representation a subsequent disk read sees.

    Repo declarations contain tuple pairs. A shallow copy leaves those tuples
    in a locally appended Fact while the ledger decodes them as arrays, which
    would make the carried view report a false duplicate-ID conflict.
    """
    return json.loads(json.dumps(data))


# The loom's own file, not a strand's. One writer: the router, except a
# strand's loom still records that strand's exit and a person asks for a machine.
_LOOM_KINDS = {
    "lease", "released", "attention", "router", "router.renewed",
    "speech", "speech.part", "thread.closed", "attempt.ended", "port.drained", "letter.disposed", "letter.due",
    "loom.started", "home.migrated", "loom.degraded", "loom.recovered", "loom.moved", "loom.pruned", "lease.fenced", "lease.handed", "machines.prefer", "machines.pin", "self.healed",
    "library.problem", "library.cleared",
}


def strand_of(fact: Fact) -> str | None:
    """Explicit strand, strand writer, or a letter's strand sender."""
    strand = fact.data.get("strand")
    if isinstance(strand, str) and strand:
        return strand
    if fact.by.startswith("strand:"):
        return fact.by.split(":", 1)[1]
    sender = fact.data.get("from") if fact.kind == "letter" else None
    return sender if isinstance(sender, str) and sender.startswith("s-") else None


def fact_filename(fact: Fact) -> str:
    """loom.jsonl holds leases, releases, attention, and injected letters."""
    if ((fact.kind in {"source", "blob", "note"} and fact.by.startswith("loom:"))
            or (fact.kind == "note" and fact.by.startswith("person:"))):
        return "loom.jsonl"
    if resource_of(fact.kind) == "wish" and fact.by.startswith(("person:", "loom:")):
        return "loom.jsonl"
    if fact.kind in _LOOM_KINDS:
        return "loom.jsonl"
    if fact.kind == "letter" and "gen" not in fact.data:
        return "loom.jsonl"
    strand = strand_of(fact)
    if not isinstance(strand, str) or not strand or "/" in strand:
        raise ValueError(f"no strand file for {fact.kind} {fact.id}")
    return f"{strand}.jsonl"


def read_file(path: Path) -> list[Fact]:
    """Parse one jsonl file, skipping a trailing line the process didn't finish."""
    raw = path.read_bytes()
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        cut = raw.rfind(b"\n")
        raw = raw[:cut + 1] if cut >= 0 else b""
    facts = []
    for line in raw.decode().splitlines():
        if line.strip():
            facts.append(Fact.from_row(json.loads(line)))
    return facts


def read_facts(home: Home) -> list[Fact]:
    """Union every install's folder. Appending stays inside the caller's own.

    The process's one fold of this home, rescanned now and copied: a copy is
    a list of pointers, a refold was ~200 MB and ~5 s (2026-10-30: the loom
    held several at once, swapped them out, and gc paged them back in).
    """
    root = home.root / "ledger" / "facts"
    if not root.is_dir():
        return []
    return list(shared_reader(home).now(home))


def fact_order(fact):
    return (fact.hlc or (int(datetime.fromisoformat(fact.at).timestamp() * 1000),
                         0, fact.by), fact.id)


class LedgerView(list):
    """Canonical process-local ledger, with an explicit revision and delta.

    Only append is mutable. Disk changes replace the view on repair. Ordinary
    extension is proven conservatively: every new priority exceeds every old
    priority and no new id resolves an old missing predecessor. Otherwise the
    authoritative union is replayed. Startup never restores a checkpoint.
    """
    def __init__(self, facts=()):
        super().__init__()
        self.revision = 0
        self.by_id = {}
        self.positions = {}
        self.by_kind = defaultdict(list)
        self.raw_letters = {}
        self.named_letters = {}
        self.letters_by_source = defaultdict(set)
        self.letters_by_reply = defaultdict(set)
        self.sources_by_blob = defaultdict(set)
        self.letter_projections = {}
        self.noncanonical_letters = False
        self.by_body = defaultdict(list)
        self.by_strand = defaultdict(list)
        self.latest = {}
        self.latest_strand = {}
        self.latest_at = {}
        self.timed = {}
        self.due_ids = set()
        self.missing = set()
        self.maximum = None
        self.cache = {}
        self.merged_kinds = {}
        self.semantic_revision = 0
        self.reducer = None
        self.delta = ()
        self.reset = True
        self.duplicates = []
        self.extend(facts)

    def _add(self, fact):
        if fact.kind != "router.renewed":
            self.semantic_revision += 1
        self.positions[fact.id] = len(self)
        super().append(fact)
        self.by_id[fact.id] = fact
        self.by_kind[fact.kind].append(fact)
        self.by_body[(fact.data.get('strand'), fact.data.get('gen'))].append(fact)
        strand = fact.data.get('strand')
        self.by_strand[strand].append(fact)
        self.latest[(fact.kind, strand, fact.data.get('gen'))] = fact
        self.latest_strand[(fact.kind, strand)] = fact
        stamp = datetime.fromisoformat(fact.at).timestamp()
        self.latest_at[(fact.kind, strand)] = max(self.latest_at.get((fact.kind, strand), 0), stamp)
        if fact.kind == 'letter':
            self.raw_letters[fact.id] = fact
            name = str(fact.data.get('id') or fact.id)
            self.noncanonical_letters |= name != fact.id
            self.named_letters[name] = fact
            self.letters_by_source[fact.data.get('cites')].add(fact.id)
            self.letters_by_reply[fact.data.get('re')].add(fact.id)
        elif fact.kind == 'source':
            for ref in fact.after:
                self.sources_by_blob[ref].add(fact.id)
        if fact.kind == 'letter' and fact.data.get('at'):
            self.timed[fact.id] = fact
        elif fact.kind == 'letter.due':
            self.due_ids.add(fact.id)
            self.timed.pop(fact.data.get('letter'), None)
        self.missing.update(p for p in fact.after if p not in self.by_id)
        self.missing.discard(fact.id)
        key = fact_order(fact)
        self.maximum = max(self.maximum, key) if self.maximum is not None else key

    def incoming(self, facts) -> tuple[list, bool]:
        """The batch's new facts in canonical order, and whether they reorder this view.

        Records a displaced duplicate as it goes: call it once per batch.
        """
        added, displaced = [], False
        for fact in facts:
            old = self.by_id.get(fact.id)
            if old is not None:
                if old == fact:
                    continue
                # Another install's copy of an ingested source: the lower HLC
                # stays, and displacing a held copy replays the union.
                if keep(old, fact) is old:
                    self.duplicates.append(fact)
                    continue
                displaced = True
            added.append(fact)
        if not added:
            return [], False
        # union(delta) validates duplicates/cycles within the incoming batch.
        delta = union(added, duplicates=self.duplicates)
        return delta, (displaced or any(f.id in self.missing for f in delta)
                       or self.maximum is not None and any(fact_order(f) <= self.maximum for f in delta))

    def extend(self, facts):
        self.apply(*self.incoming(facts))

    def apply(self, delta, repair):
        """Add what ``incoming`` returned, rebuilding this view in place on a reorder."""
        if not delta:
            self.delta = ()
            return
        if repair:
            duplicates = self.duplicates
            ordered = union(self, delta, duplicates=duplicates)
            fresh = LedgerView(ordered)
            fresh.duplicates = duplicates
            prior = self.revision
            self.__dict__.clear()
            self.__dict__.update(fresh.__dict__)
            super().clear()
            super().extend(fresh)
            self.revision = prior + 1
            self.delta = tuple(ordered)
            self.reset = True
        else:
            for fact in delta:
                self._add(fact)
            self.revision += 1
            self.delta = tuple(delta)
            self.reset = False

    def append(self, fact):
        self.extend((fact,))

    def project_letters(self, name, inputs, build):
        """Carry source projections; visit only new or newly resolvable letters."""
        if self.noncanonical_letters:
            return self.project(name, inputs, lambda rows: build(rows, None))
        kinds = ('letter', 'source', 'blob')
        counts = tuple(len(self.by_kind[kind]) for kind in kinds)
        prior = self.letter_projections.get(name)
        if prior is None or prior[0] != inputs:
            selected, result = set(self.raw_letters), {}
        else:
            _, cursors, result = prior
            if cursors == counts:
                return result
            selected = set()
            for kind, offset in zip(kinds, cursors):
                for fact in self.by_kind[kind][offset:]:
                    if kind == 'letter':
                        selected.add(fact.id)
                        selected.update(self.letters_by_reply.get(fact.id, ()))
                    elif kind == 'source':
                        selected.update(self.letters_by_source.get(fact.id, ()))
                    else:
                        for source in self.sources_by_blob.get(fact.id, ()):
                            selected.update(self.letters_by_source.get(source, ()))
        letters = [self.raw_letters[ident] for ident in sorted(selected, key=self.positions.__getitem__)]
        for ident in selected:
            result.pop(ident, None)
        result.update(build(self, letters))
        self.letter_projections[name] = (inputs, counts, result)
        return result

    def project(self, name, inputs, build):
        dependencies = {
            'senders': ('lease',),
            'reply-channels': ('source', 'letter'),
            'attachments': ('source', 'blob', 'letter'),
            'reply-context': ('source', 'letter'),
            'letters-by-sender': ('letter',),
            'wishes': ('wish', 'wish.read', 'wish.result', 'wish.verdict', 'wish.left', 'wish.top', 'wish.pick',
                       'letter', 'lease'),
        }
        if name == 'body-state':
            revision = len(self.by_body[inputs[:2]])
        elif name in dependencies:
            revision = tuple(len(self.by_kind[kind]) for kind in dependencies[name])
        else:
            revision = self.semantic_revision
        key = (name, inputs)
        prior = self.cache.get(key)
        if prior is None or prior[0] != revision:
            self.cache[key] = (revision, build(self))
        return self.cache[key][1]


def kind_facts(facts, *kinds):
    """Indexed raw rows in canonical order, or the ordinary iterable fallback."""
    if isinstance(facts, LedgerView):
        if len(kinds) == 1:
            return facts.by_kind[kinds[0]]
        # Bodies ask for the same merge every tick; re-sort only after one of its kinds grew.
        counts = tuple(len(facts.by_kind[kind]) for kind in kinds)
        prior = facts.merged_kinds.get(kinds)
        if prior is None or prior[0] != counts:
            prior = facts.merged_kinds[kinds] = (counts, sorted(
                (f for kind in kinds for f in facts.by_kind[kind]), key=lambda f: facts.positions[f.id]))
        return prior[1]
    return [f for f in facts if f.kind in kinds]


# Every write into ledger/facts touches this after its bytes are down, so a
# reader that sees it unchanged skips stat-ing every stream. A writer running
# code from before the marker does not touch it: FULL_EVERY bounds that.
MARKER = ".changed"
FULL_EVERY = 2.0


def mark_changed(facts_root) -> None:
    path = os.path.join(str(facts_root), MARKER)
    try:
        os.utime(path, ns=(time.time_ns(), time.time_ns()))
    except FileNotFoundError:
        with open(path, "a"):
            pass


def _marker(root: str):
    try:
        st = os.stat(os.path.join(root, MARKER))
    except OSError:
        return None
    return st.st_ino, st.st_mtime_ns, st.st_ctime_ns


def _names(directory, *, dirs=False):
    try:
        with os.scandir(directory) as entries:
            return [e.name for e in entries
                    if not e.name.startswith(".") and (e.is_dir() if dirs else e.is_file())]
    except (FileNotFoundError, NotADirectoryError):
        return []


class LedgerReader:
    """Process-local incremental view; the files remain authoritative.

    Only complete lines advance the offset. Replacement, truncation, and
    same-size edits invalidate a stream; removed files leave the union.
    ``read`` returns a fresh list because a tick appends its own observations.
    A view ``refresh`` returned only ever grows at its tail: a reorder builds a
    new view, so a walker on another thread never sees one half-rebuilt.
    """

    def __init__(self):
        self.streams = {}
        self.listing = (None, [])
        self.ordered = LedgerView()
        self.revision = 0
        self.seen = (None, None, 0.0)
        # One scan at a time: the loop, the board and every read_facts share a reader.
        self.lock = threading.Lock()

    def read(self, home: Home) -> list[Fact]:
        return list(self.refresh(home))

    def refresh(self, home: Home) -> LedgerView:
        root = str(home.root / "ledger" / "facts")
        with self.lock:
            # Read before the scan: a write landing during it moves the marker again.
            marker, now = _marker(root), time.monotonic()
            if (marker is not None and self.seen[:2] == (root, marker)
                    and now - self.seen[2] < FULL_EVERY):
                return self.ordered
            view = self._scan(root)
            self.seen = (root, marker, now)
            return view

    def now(self, home: Home) -> LedgerView:
        """``refresh`` without the marker's grace: every stream is stat'ed now."""
        root = str(home.root / "ledger" / "facts")
        with self.lock:
            marker, now = _marker(root), time.monotonic()
            view = self._scan(root)
            self.seen = (root, marker, now)
            return view

    def _scan(self, root: str) -> LedgerView:
        # Path.glob plus a Path sort costs more than the stats below; keep the parts order.
        names = sorted((directory, name) for directory in _names(root, dirs=True)
                       for name in _names(f"{root}/{directory}") if name.endswith(".jsonl"))
        if self.listing[0] != (root, names):
            self.listing = ((root, names), [Path(root, *parts) for parts in names])
        paths = self.listing[1]
        streams = {}
        changed = set(paths) != set(self.streams)
        repair = bool(set(self.streams) - set(paths))
        additions = []
        for path in paths:
            prior = self.streams.get(path)
            if prior is not None:
                # An unchanged stream costs one stat, not an open: hundreds of streams, every body, every tick.
                try:
                    stat = os.stat(prior[2])
                except FileNotFoundError:
                    stat = None
                if stat is not None and prior[0] == (stat.st_dev, stat.st_ino, stat.st_size,
                                                     stat.st_mtime_ns, stat.st_ctime_ns):
                    streams[path] = prior
                    continue
            with path.open("rb") as handle:
                stat = os.fstat(handle.fileno())
                stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
                         stat.st_ctime_ns)
                prior = self.streams.get(path)
                if prior is not None and prior[0] == stamp:
                    streams[path] = prior
                    continue
                changed = True
                if (prior is not None and prior[0][:2] == stamp[:2]
                        and stat.st_size > prior[0][2]):
                    offset = prior[1]
                else:
                    offset = 0
                    repair = repair or prior is not None
                handle.seek(offset)
                raw = handle.read()
                complete = raw[:raw.rfind(b"\n") + 1]
                added = [Fact.from_row(json.loads(line))
                         for line in complete.decode().splitlines() if line.strip()]
                streams[path] = (stamp, offset + len(complete), str(path))
                additions.extend(added)
        if changed:
            if repair:
                duplicates = []
                self.ordered = LedgerView(union(*(read_file(path) for path in paths),
                                                duplicates=duplicates))
                self.ordered.duplicates = duplicates
            else:
                delta, reorder = self.ordered.incoming(additions)
                if reorder:
                    duplicates = list(self.ordered.duplicates)
                    self.ordered = LedgerView(union(self.ordered, delta, duplicates=duplicates))
                    self.ordered.duplicates = duplicates
                else:
                    self.ordered.apply(delta, reorder)
            self.revision += 1
        self.streams = streams
        return self.ordered


_SHARED: dict[str, LedgerReader] = {}
_SHARED_LOCK = threading.Lock()


def shared_reader(home: Home) -> LedgerReader:
    """The process's one reader of a home's ledger."""
    with _SHARED_LOCK:
        return _SHARED.setdefault(str(home.root / "ledger" / "facts"), LedgerReader())


class WriterIndex:
    """Local ID/content and HLC indexes, refreshed under the ledger flock."""
    def __init__(self):
        self.stamps = {}
        self.existing = {}
        self.clocks = {}

    def refresh(self, directory):
        stamps, additions = {}, []
        paths = sorted(directory.glob('*.jsonl'))
        repair = bool(set(self.stamps) - set(paths))
        for path in paths:
            with path.open('rb') as handle:
                stat = os.fstat(handle.fileno())
                stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                prior = self.stamps.get(path)
                if prior and prior[0] == stamp:
                    stamps[path] = prior
                    continue
                grows = prior and prior[0][:2] == stamp[:2] and stamp[2] > prior[0][2]
                repair |= bool(prior and not grows)
                offset = prior[1] if grows else 0
                handle.seek(offset)
                raw = handle.read()
                complete = raw[:raw.rfind(b'\n') + 1]
                additions.extend(Fact.from_row(json.loads(line)) for line in complete.splitlines() if line.strip())
                stamps[path] = (stamp, offset + len(complete))
        if repair:
            additions = [f for path in paths for f in read_file(path)]
            existing, clocks = {}, {}
        else:
            existing, clocks = self.existing, self.clocks
        # Validate before publishing cursor progress, including conflicts.
        batch = {}
        for fact in additions:
            old = batch.get(fact.id, existing.get(fact.id))
            if old is not None and _content(old) != _content(fact):
                raise LedgerConflict(f'conflicting fact id: {fact.id}')
            batch[fact.id] = fact
        for fact in additions:
            existing[fact.id] = fact
            if fact.hlc:
                install = fact.hlc[2]
                clocks[install] = max(clocks.get(install, (-1, -1)), fact.hlc[:2])
        self.existing, self.clocks, self.stamps = existing, clocks, stamps

    def next_hlc(self, install):
        ms = int(time.time() * 1000)
        best_ms, counter = self.clocks.get(install, (-1, -1))
        return (ms, 0, install) if ms > best_ms else (best_ms, counter + 1, install)


_writer_indexes = OrderedDict()


def _writer_index(directory):
    key = directory.resolve()
    index = _writer_indexes.pop(key, None) or WriterIndex()
    _writer_indexes[key] = index
    while len(_writer_indexes) > 32:
        _writer_indexes.popitem(last=False)
    index.refresh(directory)
    return index


def _heal_tail(path: Path) -> None:
    """Repair only an unfinished tail; ordinary appends read one byte."""
    try:
        handle = path.open('r+b')
    except FileNotFoundError:
        return
    with handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if not size:
            return
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b'\n':
            return
        end = size
        while end:
            start = max(0, end - 8192)
            handle.seek(start)
            block = handle.read(end - start)
            cut = block.rfind(b'\n')
            if cut >= 0:
                end = start + cut + 1
                break
            end = start
        handle.truncate(end)
        handle.flush()
        os.fsync(handle.fileno())


# Called with the lock's path when ``append`` finds it held, before it waits:
# the loom sets it to resume a call it paused that holds the lock (pausing.py).
ON_CONTENDED = None


def append(home: Home, fact: Fact) -> Fact:
    """Append ``fact``. Same id + same content is a no-op; different content raises.

    The one write path: a fact of a closed resource the registry does not
    declare is refused here, before anything touches the folder.
    """
    admit(fact)
    directory = home.facts_dir()
    lock_path = directory / ".lock"
    with open(lock_path, "a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if ON_CONTENDED is not None:
                ON_CONTENDED(lock_path)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            index = _writer_index(directory)
            old = index.existing.get(fact.id)
            data = _freeze(fact.data)
            if old is not None:
                probe = Fact(kind=fact.kind, by=fact.by, data=data, id=fact.id)
                if _content(old) == _content(probe):
                    return old
                raise LedgerConflict(f"conflicting fact id: {fact.id}")
            hlc = fact.hlc or index.next_hlc(home.install_id())
            stored = Fact(
                kind=fact.kind, by=fact.by, data=data, at=fact.at, id=fact.id,
                v=fact.v, hlc=hlc, after=fact.after,
            )
            line = json.dumps(stored.row(), sort_keys=True, separators=(",", ":"))
            path = directory / fact_filename(stored)
            _heal_tail(path)
            with open(path, "a") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            mark_changed(directory.parent)
            return stored
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def inject_letter(root: Path | str, *, to: str, body: str,
                  sender: str = "p-test", re: str | None = None) -> str:
    """A test or a person writes one letter. The sender is ``p-<name>``."""
    if not sender.startswith("p-"):
        raise ValueError(f"injected sender must be p-<name>, got {sender!r}")
    home = Home(root)
    thread = thread_of(to)
    if not home.thread_exists(thread):
        raise FileNotFoundError(
            f"no thread {thread} ({home.thread_dir(thread) / 'README.md'})")
    letter_id = f"{sender}/{mint(5)}"
    data: dict = {"id": letter_id, "to": to, "body": body, "from": sender}
    if re:
        data["re"] = re
    append(home, Fact(kind="letter", by=f"person:{sender}", data=data, id=letter_id))
    return letter_id
