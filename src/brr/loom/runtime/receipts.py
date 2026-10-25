"""What a thread shipped: its pull requests, read from the ledger, never stored.

``publish <repo> --pr N`` writes ``pr.opened`` and ``publish <repo> --merged
N`` writes ``pr.merged``, each only after git on the source showed it. The
loom writes ``loom.started`` each time it starts or re-executes, naming the
commit its own source is at and the merged pull requests that commit holds.
Whether a merge is loaded is read from the latest start alone, so a rollback
unloads it. A loom whose source is not a git checkout knows no commit: its
merges are drawn as unknown, never as not loaded.
"""

from __future__ import annotations

import os
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
    """The checkout the running loom imports, when it runs from one."""
    import brr
    return Path(brr.__file__).resolve().parents[2]


def record_start(home, facts, *, root: Path | None = None) -> Fact:
    """``loom.started`` for this process. ``.git`` is looked for at the root
    itself: a site-packages install inside some other repo is not that repo."""
    root = source_root() if root is None else root
    commit, loaded = None, []
    if (root / ".git").exists():
        proc = git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", check=False)
        commit = proc.stdout.strip() or None
    if commit:
        for fact in kind_facts(facts, "pr.merged"):
            data = fact.data or {}
            held = git(root, "merge-base", "--is-ancestor", str(data.get("merge_commit")),
                       commit, check=False)
            if held.returncode == 0:
                loaded.append(f"{data.get('repo')}#{data.get('number')}")
    data = {"install": home.install_id(), "pid": os.getpid(),
            "loaded": sorted(set(loaded))}
    if commit:
        data["commit"] = commit
    return append(home, Fact(kind="loom.started", by=f"loom:{home.install_id()}", data=data))


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
    found: dict[str, list[Receipt]] = {}
    for key in opened:
        thread, repo, number = key
        was, done = opened[key] or {}, merged.get(key)
        commit = (done or {}).get("merge_commit")
        found.setdefault(thread, []).append(Receipt(
            thread, repo, number, str(was.get("url") or ""),
            str((done or was).get("head") or ""), commit,
            None if held is None or not commit else f"{repo}#{number}" in held))
    return found
