"""Drive the runtime verb with the same boundary and ledger a live body sees."""

from concurrent.futures import ThreadPoolExecutor
import time
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import jack, waits
from brr.loom.runtime.__main__ import main
from brr.loom.runtime.home import atomic_write
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.loom import _render_boundary
from brr.loom.runtime.port import parse_frontmatter
from test_waits import park
from test_wishes import heard, ledger  # noqa: F401


@pytest.fixture
def body(ledger, monkeypatch):
    home, room = ledger
    monkeypatch.setenv("BRNRD_ROOM", str(room))
    monkeypatch.setenv("BRNRD_THREAD", "work")
    return home, room


def _boundary(home, room):
    context = SimpleNamespace(thread="work", fuel="", why=None, shell="fake")
    text = _render_boundary(home, read_facts(home), room.name, 1, context)
    atomic_write(room / "port/in/boundary.md", text)


def _during_wait(write, stream):
    def later():
        time.sleep(0.25)
        started = time.monotonic()
        return write(), started

    with ThreadPoolExecutor(1) as worker:
        arriving = worker.submit(later)
        assert main(["attend", "--for", "1.8s", "--streams", stream]) == 0
        returned = time.monotonic()
        item, started = arriving.result()
    assert 0 <= returned - started < 2
    return item


def test_letter_appended_mid_wait(body, capsys):
    home, room = body
    _boundary(home, room)

    def arrive():
        ident = inject_letter(home.root, to="thread:work", body="A new letter\nThe rest of the text.")
        _boundary(home, room)
        return ident

    ident = _during_wait(arrive, "letters")
    output = capsys.readouterr().out
    assert output.startswith(f"letters · {ident} · A new letter\n")
    assert f"- {ident} · from " in output
    assert "  The rest of the text.\n" in output


@pytest.mark.parametrize("event", ["result", "verdict", "resolved"])
def test_external_board_change(body, tmp_path, capsys, event):
    home, room = body
    wish = heard(home, room, "Build the thing")
    ready = tmp_path / "ready"
    if event == "resolved":
        park(home, room, wish, "--wait", f"file:{ready}")

    def arrive():
        if event == "result":
            for writer in (room.name, "s-aaaa-else"):
                result = append(home, Fact(kind="wish.result", by=f"strand:{writer}", data={
                    "wish": wish, "kind": "url", "ref": "https://example.com", "line": "Built",
                    "delivered": False,
                }))
            return result.id
        if event == "verdict":
            return append(home, Fact(kind="wish.verdict", by="person:test", data={
                "wish": wish, "as": "accepted", "evidence": "act", "ref": "approved",
            })).id
        ready.write_text("0\n")
        facts = read_facts(home)
        waits.observe_waits(home, facts, True)
        return next(f.id for f in facts if f.id.startswith("loom/wait-"))

    ident = _during_wait(arrive, "board")
    assert capsys.readouterr().out.splitlines()[0].startswith(f"board · {ident} · ")


def test_timeout_exits_successfully(body, capsys):
    started = time.monotonic()
    assert main(["attend", "--for", "0.05s"]) == 0
    assert time.monotonic() - started >= 0.05
    assert capsys.readouterr().out == "attend: nothing in 0.05s\n"


def test_attended_letter_is_shown_once_even_before_the_loom_drains(body, capsys):
    home, room = body
    _boundary(home, room)
    jack.execute("start", room, "{}")
    ident = inject_letter(home.root, to="thread:work", body="Only show these words once")
    _boundary(home, room)
    assert main(["attend", "--for", "0.25s"]) == 0
    assert f"letters · {ident}" in capsys.readouterr().out
    shown = [parse_frontmatter(p.read_text()) for p in (room / "port/out").glob("*.md")]
    assert any(row.get("kind") == "shown" and row.get("ids") == ident for row in shown)
    assert "Only show these words once" not in jack.execute("post", room, "{}")
    assert main(["attend", "--for", "0.05s"]) == 0
    assert capsys.readouterr().out == "attend: nothing in 0.05s\n"


def _receipts(room):
    rows = [parse_frontmatter(p.read_text()) for p in (room / "port/out").glob("*.md")]
    return [row for row in rows if row.get("kind") == "shown"]


def test_an_owed_letter_is_receipted_once_however_many_calls_follow(body):
    home, room = body
    ident = inject_letter(home.root, to="thread:work", body="Owed through a long turn")
    _boundary(home, room)
    jack.execute("start", room, "{}")
    assert [row["ids"] for row in _receipts(room)] == [ident]
    for _ in range(5):
        jack.execute("post", room, "{}")
    assert [row["ids"] for row in _receipts(room)] == [ident]
    later = inject_letter(home.root, to="thread:work", body="A second letter mid-turn")
    _boundary(home, room)
    assert later in jack.execute("post", room, "{}")
    jack.execute("post", room, "{}")
    assert sorted(row["ids"] for row in _receipts(room)) == sorted([ident, f"{ident} {later}"])
