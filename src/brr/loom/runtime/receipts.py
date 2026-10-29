"""What a thread shipped: its pull requests, read from the ledger, never stored.

``publish <repo> --pr N`` writes ``pr.opened`` and ``publish <repo> --merged
N`` writes ``pr.merged``, each only after git on the source showed it. The
loom writes ``loom.started`` each time it starts or re-executes, naming the
commit its own source is at and the merged pull requests that commit holds.
A pull request merged with no ``pr.merged`` still counts: the start finds the
merge commit of an opened head in its own history and names it in
``merges``. The same history delivers a wish whose result is a pull request
the latest start holds (``held_merge``). Whether a merge is loaded is read from the latest start alone, so
a rollback unloads it. A loom whose source is not a git checkout knows no commit: its
merges are drawn as unknown, never as not loaded.
"""

from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .ledger import Fact, append, kind_facts
from .selfrepo import git

#: The load word of a merged pull request, by what the latest start knows.
LOAD = {True: "loaded", False: "not loaded", None: "unknown"}


@dataclass(frozen=True)
class Receipt:
    thread: str
    repo: str
    number: int
    url: str
    head: str
    merge_commit: str | None
    #: In the latest start's list; ``None`` when unmerged or the start knows no commit.
    loaded: bool | None

    def state(self) -> str:
        return "merged" if self.merge_commit else "opened"

    def load(self) -> str | None:
        return LOAD[self.loaded] if self.merge_commit else None

    def line(self) -> str:
        word = self.state() + (f", {self.load()}" if self.merge_commit else "")
        return f"{self.repo}#{self.number} {word}" + (f" {self.url}" if self.url else "")


def source_root() -> Path:
    """The checkout the running loom imports, when it runs from one.

    ``BRNRD_LOOM_SOURCE`` is test-only: the suite points it away from the
    checkout under test, so no test loom fetches or moves it.
    """
    if override := os.environ.get("BRNRD_LOOM_SOURCE"):
        return Path(override)
    import brr
    return Path(brr.__file__).resolve().parents[2]


def record_start(home, facts, *, root: Path | None = None) -> Fact:
    """``loom.started`` for this process. ``.git`` is looked for at the root
    itself: a site-packages install inside some other repo is not that repo."""
    root = source_root() if root is None else root
    commit, loaded, merges = None, [], {}
    if (root / ".git").exists():
        proc = git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", check=False)
        commit = proc.stdout.strip() or None
    if commit:
        recorded = set()
        for fact in kind_facts(facts, "pr.merged"):
            data = fact.data or {}
            name = f"{data.get('repo')}#{data.get('number')}"
            recorded.add(name)
            held = git(root, "merge-base", "--is-ancestor", str(data.get("merge_commit")),
                       commit, check=False)
            if held.returncode == 0:
                loaded.append(name)
        merges = _merges_held(root, commit, facts, recorded)
        loaded.extend(merges)
    install = home.install_id()
    data = {"install": install, "pid": os.getpid(), "loaded": sorted(set(loaded))}
    if commit:
        data["commit"] = commit
        if merges:
            data["merges"] = merges
    before = next((str(f.data["commit"]) for f in reversed(kind_facts(facts, "loom.started"))
                   if f.data.get("install") == install and f.data.get("commit")), None)
    started = append(home, Fact(kind="loom.started", by=f"loom:{install}", data=data))
    if commit and before and before != commit:
        # Whoever moved it, the updater or a hand: the seated bodies hear it once.
        from .update import merges as between
        append(home, Fact(kind="loom.moved", by=f"loom:{install}", id=f"loom.moved:{started.id}", data={
            "install": install, "from": before, "to": commit,
            "merges": between(root, before, commit), "started": started.id}))
    return started


@functools.lru_cache(maxsize=8)
def _merge_log(root: Path, commit: str) -> tuple[tuple[str, str, str], ...]:
    """``(merge, second parent, subject)`` for each merge in ``commit``'s
    history, newest first. A commit never changes, so one walk serves it."""
    text = git(root, "log", "--merges", "--format=%H %P%x09%s", commit, check=False).stdout
    rows = []
    for line in text.splitlines():
        hashes, _tab, subject = line.partition("\t")
        commits = hashes.split()
        if len(commits) >= 3:
            rows.append((commits[0], commits[2], subject))
    return tuple(rows)


def _merges_held(root: Path, commit: str, facts, recorded: set[str]) -> dict[str, str]:
    """``repo#N`` to its merge commit in ``commit``'s history, for each opened
    pull request with no ``pr.merged``: a merge whose second parent is one of
    its opened heads and whose subject names ``#N``, as ``publish --merged``
    proves it."""
    heads: dict[str, tuple[str, int]] = {}
    for fact in kind_facts(facts, "pr.opened"):
        data = fact.data or {}
        name = f"{data.get('repo')}#{data.get('number')}"
        if name not in recorded and data.get("head"):
            heads[str(data["head"])] = (name, data.get("number"))
    found = {}
    for merge, head, subject in (_merge_log(root, commit) if heads else ()):
        if head in heads:
            name, number = heads[head]
            if re.search(rf"#{number}\b", subject):
                found.setdefault(name, merge)
    return found


#: A wish result's pull request: ``pr:N`` (the loom's own repo) or its URL.
_PULL = re.compile(r"/([^/]+)/pull/(\d+)/?$")


@functools.lru_cache(maxsize=8)
def _repo_name(root: Path) -> str:
    url = git(root, "remote", "get-url", "origin", check=False).stdout.strip()
    return (url.rstrip("/").rsplit("/", 1)[-1] if url else root.name).removesuffix(".git")


def held_merge(result: dict, start: dict | None, *, root: Path | None = None) -> str | None:
    """The merge commit in the start's commit of the pull request a wish
    result names, if the loom's source holds one: a merge subject GitHub
    writes, ``Merge pull request #N``. A ``pr:N`` names the loom's own repo;
    a URL must name it too. ``None`` when the start knows no commit."""
    commit = (start or {}).get("commit")
    root = source_root() if root is None else root
    if not commit or not (root / ".git").exists():
        return None
    ref = str(result.get("ref") or "")
    if result.get("kind") == "pr" and ref.isdigit():
        number = ref
    elif (match := _PULL.search(ref)) and match.group(1) == _repo_name(root):
        number = match.group(2)
    else:
        return None
    pattern = re.compile(rf"^Merge pull request #{number}\b")
    return next((merge for merge, _head, subject in _merge_log(root, commit)
                 if pattern.match(subject)), None)


def receipts(facts, install: str) -> dict[str, list[Receipt]]:
    """Each thread's pull requests in the order first claimed; a later
    ``pr.opened`` of the same one names its newer head. Loaded is read from
    ``install``'s latest start: another machine's loom runs its own checkout.
    Read off the ledger's kind index, so a tick's thread table walks no old
    ledger."""
    opened, merged, start = {}, {}, None
    for fact in kind_facts(facts, "pr.opened", "pr.merged", "loom.started"):
        if fact.kind in ("pr.opened", "pr.merged"):
            data = fact.data or {}
            key = (str(data.get("thread")), str(data.get("repo")), data.get("number"))
            (opened if fact.kind == "pr.opened" else merged)[key] = data
            opened.setdefault(key, None)
        elif fact.kind == "loom.started" and (fact.data or {}).get("install") == install:
            start = fact.data
    held = (set(start.get("loaded") or ()) if start and start.get("commit") else None)
    found_merges = (start.get("merges") or {}) if held is not None else {}
    found: dict[str, list[Receipt]] = {}
    for key in opened:
        thread, repo, number = key
        was, done = opened[key] or {}, merged.get(key)
        commit = (done or {}).get("merge_commit") or found_merges.get(f"{repo}#{number}")
        found.setdefault(thread, []).append(Receipt(
            thread, repo, number, str(was.get("url") or ""),
            str((done or was).get("head") or ""), commit,
            None if held is None or not commit else f"{repo}#{number}" in held))
    return found
