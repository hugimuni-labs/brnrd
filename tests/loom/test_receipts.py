"""A thread's pull requests: claimed through ``publish``, proven by git on the source.

Local bare repos stand in for the forge: ``refs/pull/N/head`` is written by
hand, as GitHub writes it, and merges are made with ``commit-tree``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from brr.loom.runtime import receipts as receipts_mod, snapshot
from brr.loom.runtime.asks import asks
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import Home
from brr.loom.runtime import publish as publish_mod
from brr.loom.runtime.ledger import Fact, append, read_facts
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.receipts import receipts, record_start
from brr.loom.runtime.selfrepo import git
from brr.loom.runtime.threads import thread_table

from _step import Loom, wait_until
from test_project_repos import _kill, _note
from test_publish import _open, _tip, _verb


def _pull(bare: Path, number: int, commit: str) -> None:
    git(bare, "update-ref", f"refs/pull/{number}/head", commit)


def _merge(bare: Path, head: str, subject: str, *, squash: bool = False) -> str:
    """A merge of ``head`` into the source's main, as the forge's button makes it."""
    main = _tip(bare, "refs/heads/main")
    parents = ["-p", main] if squash else ["-p", main, "-p", head]
    commit = git(bare, "commit-tree", f"{head}^{{tree}}", *parents, "-m", subject).stdout.strip()
    git(bare, "update-ref", "refs/heads/main", commit)
    return commit


def _kinds(root: Path, kind: str) -> list:
    return [fact for fact in read_facts(Home(root)) if fact.kind == kind]


def _shipped(root: Path) -> list[str]:
    return [r.line() for r in receipts(read_facts(Home(root)), Home(root).install_id()).get("work", [])]


def test_a_pull_request_is_claimed_opened_then_merged_by_its_merge_commit(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        head = _tip(checkout, "HEAD")
        _pull(bare, 7, head)
        proc = _verb(room, "widget", "--pr", "7")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == f"pr.opened widget#7 {head}\n"
        [opened] = _kinds(root, "pr.opened")
        assert opened.data == {"thread": "work", "repo": "widget", "number": 7, "url": "",
                               "head": head}
        assert _shipped(root) == ["widget#7 opened"]

        merge = _merge(bare, head, "Merge pull request #7 from acme/loom/work")
        proc = _verb(room, "widget", "--merged", "7")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == f"pr.merged widget#7 {merge}\n"
        [merged] = _kinds(root, "pr.merged")
        assert merged.data == {"thread": "work", "repo": "widget", "number": 7, "head": head,
                               "merge_commit": merge}
        # No loom start has said what it runs: unknown, never "not loaded".
        assert _shipped(root) == ["widget#7 merged, unknown"]
    finally:
        _kill(read_facts(Home(root)))


def test_a_squash_merge_is_refused_out_loud(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        head = _tip(checkout, "HEAD")
        _pull(bare, 7, head)
        _merge(bare, head, "Fix things (#7)", squash=True)
        proc = _verb(room, "widget", "--merged", "7")
        assert proc.returncode == 1
        assert "no merge commit with second parent = head" in proc.stderr
        assert "squash" in proc.stderr
        assert _kinds(root, "pr.merged") == []
    finally:
        _kill(read_facts(Home(root)))


def test_a_merge_whose_subject_names_another_pull_request_is_refused(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        head = _tip(checkout, "HEAD")
        _pull(bare, 7, head)
        _merge(bare, head, "Merge pull request #70 from acme/other")
        proc = _verb(room, "widget", "--merged", "7")
        assert proc.returncode == 1 and "does not name #7" in proc.stderr
        assert _kinds(root, "pr.merged") == []
    finally:
        _kill(read_facts(Home(root)))


def test_a_pull_request_whose_head_this_thread_never_pushed_is_refused(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        # Committed, not published: the head is on the forge by some other hand.
        git(bare, "fetch", str(checkout), "refs/heads/loom/work:refs/heads/elsewhere")
        _pull(bare, 7, _tip(checkout, "HEAD"))
        proc = _verb(room, "widget", "--pr", "7")
        assert proc.returncode == 1 and "never pushed" in proc.stderr
        proc = _verb(room, "widget", "--pr", "8")
        assert proc.returncode == 1 and "no pull request 8" in proc.stderr
        proc = _verb(room, "widget", "--merged", "0")
        assert proc.returncode == 1 and "0 is not a pull request number" in proc.stderr
        assert _kinds(root, "pr.opened") == []
    finally:
        _kill(read_facts(Home(root)))


class _FakeForge:
    """A pull request body the way GitHub keeps it: read, then rewritten whole."""

    def __init__(self, body: str):
        self.text, self.writes = body, 0

    def body(self, number: int) -> str:
        return self.text

    def set_body(self, number: int, text: str) -> None:
        self.text, self.writes = text, self.writes + 1


def test_an_opened_pull_request_says_whose_it_is_once(tmp_path: Path, monkeypatch, capsys) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        home = Home(root)
        append(home, Fact(kind="letter", by=f"loom:{home.install_id()}", id="l-ada", data={
            "id": "l-ada", "from": "person:ada", "to": "thread:work", "body": "sign the pull requests"}))
        append(home, Fact(kind="wish", by="person:ada", id="w-sign", data={
            "wish": "w-sign", "says": [["l-ada", 0, None]], "from": "person",
            "line": "sign the pull requests", "thread": "work"}))
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        _pull(bare, 7, _tip(checkout, "HEAD"))
        forge = _FakeForge("Fixes the widget.\n\n<!-- brnrd -->")
        monkeypatch.setattr(publish_mod, "_forge_for", lambda home, source: forge)

        assert publish_mod.main(["widget", "--room", str(room), "--pr", "7"]) == 0, capsys.readouterr().err
        line = (f"Opened by resident brnrd for ada · install {home.install_id()} · thread work"
                f" · wish w-sign")
        # Above the marker, so a person's comment routing still skips what brnrd wrote.
        assert forge.text == f"Fixes the widget.\n\n{line}\n\n<!-- brnrd -->"
        assert forge.writes == 1

        # A second record reads the footer and writes nothing.
        assert publish_mod.main(["widget", "--room", str(room), "--pr", "7"]) == 0
        assert forge.writes == 1 and forge.text.count("Opened by resident brnrd") == 1
    finally:
        _kill(read_facts(Home(root)))


def test_a_forge_that_refuses_the_footer_still_leaves_the_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        _pull(bare, 7, _tip(checkout, "HEAD"))

        class Refusing(_FakeForge):
            def set_body(self, number: int, text: str) -> None:
                raise OSError("403 Resource not accessible by integration")

        monkeypatch.setattr(publish_mod, "_forge_for", lambda home, source: Refusing(""))
        assert publish_mod.main(["widget", "--room", str(room), "--pr", "7"]) == 0
        assert "not signed: 403" in capsys.readouterr().err
        assert len(_kinds(root, "pr.opened")) == 1
    finally:
        _kill(read_facts(Home(root)))


def test_sign_puts_the_footer_above_the_last_marker_or_adds_one() -> None:
    sign = publish_mod.sign
    assert sign("", "Opened by resident brnrd", "ab12") == "Opened by resident brnrd\n\n<!-- brnrd:ab12 -->"
    assert sign("Body.", "Opened by resident brnrd", "ab12") == (
        "Body.\n\nOpened by resident brnrd\n\n<!-- brnrd:ab12 -->")
    signed = "Body.\n\nOpened by resident brnrd for ada\n\n<!-- brnrd -->"
    assert sign(signed, "Opened by resident brnrd for bo", "ab12") == signed


def test_loaded_follows_the_latest_start_and_a_rollback_unloads(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        head = _tip(checkout, "HEAD")
        _pull(bare, 7, head)
        merge = _merge(bare, head, "Merge pull request #7 from acme/loom/work")
        assert _verb(room, "widget", "--merged", "7").returncode == 0
        home = Home(root)
        # A loom source checkout, first at the merge, then rolled back before it.
        source = tmp_path / "loom-src"
        git(None, "clone", "--quiet", str(bare), str(source))
        assert _tip(source, "HEAD") == merge
        record_start(home, read_facts(home), root=source)
        assert _kinds(root, "loom.started")[-1].data["loaded"] == ["widget#7"]
        assert _shipped(root) == ["widget#7 merged, loaded"]
        # Another install's loom starting on an older checkout is its load, not this one's:
        # through the thread table, which every surface reads.
        git(source, "checkout", "--quiet", "--detach", "HEAD^1")
        other = Home(root, install="bbbb" if home.install_id() != "bbbb" else "cccc")
        record_start(other, read_facts(home), root=source)
        assert {p.parent.name for p in (root / "ledger" / "facts").glob("*/*.jsonl")} >= {
            home.install_id(), other.install_id()}
        [row] = [r for r in thread_table(home, read_facts(home)) if r["thread"] == "work"]
        assert row["receipts"] == "widget#7 merged, loaded"
        assert _shipped(root) == ["widget#7 merged, loaded"]
        record_start(home, read_facts(home), root=source)
        started = _kinds(root, "loom.started")[-1].data
        assert started["loaded"] == [] and started["commit"] == _tip(source, "HEAD")
        assert _shipped(root) == ["widget#7 merged, not loaded"]
        # A source that is no git checkout knows no commit.
        plain = tmp_path / "site-packages"
        plain.mkdir()
        record_start(home, read_facts(home), root=plain)
        assert "commit" not in _kinds(root, "loom.started")[-1].data
        assert _shipped(root) == ["widget#7 merged, unknown"]
    finally:
        _kill(read_facts(Home(root)))


def test_receipts_are_drawn_in_threads_and_asks_all(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        _pull(bare, 7, _tip(checkout, "HEAD"))
        assert _verb(room, "widget", "--pr", "7").returncode == 0
        home = Home(root)
        text = asks(read_facts(home), main_snapshot(home), everything=True,
                    install=home.install_id())
        assert "work · widget#7 opened\n" in text
        proc = subprocess.run([sys.executable, "-m", "brr", "threads", "--home", str(root)],
                              text=True, capture_output=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        lines = [line.split(" · ") for line in proc.stdout.splitlines()]
        assert lines[0][-1] == "receipts"
        rows = {row[0]: dict(zip(lines[0], row)) for row in lines[1:]}
        assert rows["work"]["receipts"] == "widget#7 opened"
        doc = snapshot.assemble(home, read_facts(home), load_config(root))
        work = next(t for t in doc["threads"] if t["id"] == "work")
        assert work["receipts"] == [{"repo": "widget", "number": 7, "head": _tip(checkout, "HEAD"),
                                     "state": "opened"}]
    finally:
        _kill(read_facts(Home(root)))


def test_the_loom_records_its_start(tmp_path: Path, monkeypatch) -> None:
    root, room, checkout, bare = _open(tmp_path)
    _kill(read_facts(Home(root)))
    monkeypatch.setattr(receipts_mod, "source_root", lambda: checkout)
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: _kinds(root, "loom.started"), 10, lambda: "no loom.started")
        [started] = _kinds(root, "loom.started")
        assert started.by == f"loom:{Home(root).install_id()}"
        assert started.data["commit"] == _tip(checkout, "HEAD")
    finally:
        loom.halt()
        _kill(read_facts(Home(root)))


def test_a_start_lists_a_merge_no_pr_merged_recorded(tmp_path: Path, monkeypatch) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        head = _tip(checkout, "HEAD")
        _pull(bare, 7, head)
        assert _verb(room, "widget", "--pr", "7").returncode == 0
        # Merged on the forge; nobody ran publish --merged.
        merge = _merge(bare, head, "Merge pull request #7 from acme/loom/work")
        assert _kinds(root, "pr.merged") == []
        source = tmp_path / "loom-src"
        git(None, "clone", "--quiet", str(bare), str(source))
    finally:
        _kill(read_facts(Home(root)))
    monkeypatch.setattr(receipts_mod, "source_root", lambda: source)
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: _kinds(root, "loom.started"), 10, lambda: "no loom.started")
        [started] = _kinds(root, "loom.started")
        assert started.data["commit"] == merge
        assert started.data["loaded"] == ["widget#7"]
        assert started.data["merges"] == {"widget#7": merge}
        assert _shipped(root) == ["widget#7 merged, loaded"]
    finally:
        loom.halt()
        _kill(read_facts(Home(root)))


def test_a_start_does_not_list_a_merge_naming_another_pull_request(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        head = _tip(checkout, "HEAD")
        _pull(bare, 7, head)
        assert _verb(room, "widget", "--pr", "7").returncode == 0
        _merge(bare, head, "Merge pull request #70 from acme/other")
        source = tmp_path / "loom-src"
        git(None, "clone", "--quiet", str(bare), str(source))
        home = Home(root)
        record_start(home, read_facts(home), root=source)
        started = _kinds(root, "loom.started")[-1].data
        assert started["loaded"] == [] and "merges" not in started
        assert _shipped(root) == ["widget#7 opened"]
    finally:
        _kill(read_facts(Home(root)))


def test_a_start_lists_a_squash_of_its_own_repo(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        head = _tip(checkout, "HEAD")
        _pull(bare, 7, head)
        assert _verb(room, "widget", "--pr", "7").returncode == 0
        # Squashed on the forge: one parent, GitHub's "<title> (#N)", no head kept.
        _merge(bare, head, "an unrelated fix (#70)", squash=True)
        squash = _merge(bare, head, "fix the widget (#7)", squash=True)
        source = tmp_path / "loom-src"
        git(None, "clone", "--quiet", str(bare), str(source))
        home = Home(root)
        record_start(home, read_facts(home), root=source)
        started = _kinds(root, "loom.started")[-1].data
        assert started["loaded"] == ["widget#7"]
        assert started["merges"] == {"widget#7": squash}
        assert _shipped(root) == ["widget#7 merged, loaded"]
    finally:
        _kill(read_facts(Home(root)))


def test_a_start_lists_every_landing_since_the_first_start(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        assert _verb(room, "widget").returncode == 0
        stale = _tip(checkout, "HEAD")
        _pull(bare, 7, stale)
        assert _verb(room, "widget", "--pr", "7").returncode == 0
        _merge(bare, stale, "Merge pull request #5 from acme/before", squash=True)
        source = tmp_path / "loom-src"
        git(None, "clone", "--quiet", str(bare), str(source))
        home = Home(root)
        record_start(home, read_facts(home), root=source)
        assert _kinds(root, "loom.started")[-1].data["loaded"] == []
        # The branch moved on with no new --pr; nobody recorded #9 at all.
        _note(checkout, room, "more.txt", "more\n")
        assert _verb(room, "widget").returncode == 0
        head = _tip(checkout, "HEAD")
        _pull(bare, 7, head)
        seven = _merge(bare, head, "Merge pull request #7 from acme/loom/work")
        _merge(bare, head, "Merge pull request #9 from acme/someone-else")
        git(source, "pull", "--quiet", "--ff-only")
        record_start(home, read_facts(home), root=source)
        started = _kinds(root, "loom.started")[-1].data
        assert started["loaded"] == ["widget#7", "widget#9"]
        assert started["merges"] == {"widget#7": seven}
        assert _shipped(root) == ["widget#7 merged, loaded"]
    finally:
        _kill(read_facts(Home(root)))
