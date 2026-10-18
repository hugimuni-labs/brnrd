"""The loop pushes snapshot v1 to the board: on a settled change, else once a minute."""

import json
import sys
import time

import pytest

from brr.daemon2.facts import Fact
from brr.gates import cloud
from brr.loom.runtime import snapshot, usage
from brr.loom.runtime.ledger import inject_letter

from _step import Loom, kill_bodies, wait_until, write_thread

README = """---
id: web
status: open
tense: plan
opened-by: thread:inbox
---
# Put the loom on the web. Then tell the inbox.

## Plan

- [x] Contract read · re: p-test/aaaaa
- [ ] Publisher pushes · re: p-test/bbbbb

## Done

- pr https://github.com/acme/brnrd/pull/7 — snapshot contract
"""


class Cloud:
    """Stands in for ``cloud._request``; ``fail`` makes every call raise."""

    def __init__(self):
        self.calls, self.fail = [], False

    def __call__(self, base_url, method, path, *, token=None, json=None, retry=True, **_):
        self.calls.append({"url": base_url, "method": method, "path": path,
                           "token": token, "doc": json, "retry": retry})
        if self.fail:
            raise RuntimeError("board unreachable")
        return {}


@pytest.fixture
def board(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "web", README, "answer-fast", wait="0")
    state = tmp_path / "account"
    (state / "gates").mkdir(parents=True)
    (state / "gates/cloud.json").write_text(json.dumps({"brnrd_url": "https://board.test", "token": "tok"}))
    with (root / "loom/config.toml").open("a") as stream:
        stream.write(f'relay_state = "{state}"\nbody = "fake/haiku"\n')
    now = [1000.0]
    fake = Cloud()
    assembled = []
    real = snapshot.assemble
    monkeypatch.setattr(snapshot, "clock", lambda: now[0])
    monkeypatch.setattr(snapshot, "ASSEMBLE_EVERY", 0)
    # The loop's gauge sweep would ask this machine's real shells.
    monkeypatch.setattr(usage, "_refresh", lambda *a, **k: None)
    monkeypatch.setattr(snapshot, "assemble", lambda *a: assembled.append(real(*a)) or assembled[-1])
    monkeypatch.setattr(cloud, "_request", fake)
    loom = Loom(root)
    loom.now, loom.cloud, loom.assembled = now, fake, assembled
    yield loom
    loom.halt()
    kill_bodies(loom)
    assert not loom.errors, loom.dump()


def fuel(root, left):
    path = root / "loom/usage/fake.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": time.time(), "windows": [
        {"name": "session", "left_pct": left, "resets_at": "2026-10-19T05:00:00+00:00"}]}))


def reporting_body(root):
    """Notes what it is owed; on web it also reports the PR to the inbox."""
    folder = root / "loom/bodies/fake"
    folder.mkdir(parents=True)
    (folder / "jack").write_text("hand\n")
    run = folder / "run"
    run.write_text(f"""#!{sys.executable}
import sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
for letter in ctx.owed():
    ctx.send("thread:" + own, note="ok", re=letter.id)
if own == "web":
    ctx.send("thread:inbox", "PR https://github.com/acme/brnrd/pull/7 opened")
ctx.jack("stop")
""")
    run.chmod(0o755)


def steps(loom, n=3):
    """Let the loop assemble ``n`` more times at the current clock."""
    seen = len(loom.assembled)
    wait_until(lambda: len(loom.assembled) >= seen + n, 10, loom.dump)


def test_document_matches_the_contract(board):
    fuel(board.root, 82)
    reporting_body(board.root)
    inject_letter(board.root, to="thread:web", body="Ship the board, please.")
    board.start()

    def pushed_full():
        board.now[0] += snapshot.IDLE
        steps(board)
        doc = board.cloud.calls[-1]["doc"] if board.cloud.calls else {}
        web = [t for t in doc.get("threads", []) if t["id"] == "web"]
        return web and web[0].get("strands") and any(e["kind"] == "receipt" for e in doc["log"])
    wait_until(pushed_full, 20, board.dump)
    call = board.cloud.calls[-1]
    assert (call["url"], call["method"], call["path"], call["token"], call["retry"]) == (
        "https://board.test", "PUT", "/v1/daemons/loom", "tok", False)
    doc = call["doc"]
    assert doc["v"] == 1 and doc["at"].endswith("Z")
    assert doc["bodies"] == {"default": "fake/haiku", "choices": ["fake/haiku"]}
    assert doc["fuel"] == [{"shell": "fake", "state": "ok", "cores": ["haiku"], "gauges": [
        {"window": "session", "left_pct": 82, "resets_at": "2026-10-19T05:00:00Z"}]}]
    thread = next(t for t in doc["threads"] if t["id"] == "web")
    assert thread["id"] == "web" and thread["title"] == "Put the loom on the web."
    assert thread["status"] == "open" and thread["opened_by"] == "thread:inbox"
    assert thread["plan"] == {"done": 1, "total": 2, "next": "Publisher pushes"}
    assert thread["done"] == [{"kind": "pr", "ref": "https://github.com/acme/brnrd/pull/7",
                               "line": "snapshot contract"}]
    assert thread["turn"] in {"mine", "none"} and isinstance(thread["owed"], int)
    assert "blocked_on" not in thread and thread["last_at"].endswith("Z")
    for strand in thread["strands"]:
        assert strand["body"] == "fake/haiku" and strand["since"].endswith("Z")
        assert set(strand) <= {"id", "body", "state", "since"}
        assert strand.get("state", "idle") in snapshot.STATES
    for entry in doc["log"]:
        assert {"id", "at", "from", "thread", "kind", "head"} <= set(entry)
        assert entry["kind"] in {"letter", "receipt", "notice"} and len(entry["head"]) <= 280
    asked = next(e for e in doc["log"] if e["head"] == "Ship the board, please.")
    assert asked == {"id": asked["id"], "at": asked["at"], "from": "p-test", "to": "thread:web",
                     "thread": "web", "kind": "letter", "head": "Ship the board, please."}
    receipt = next(e for e in doc["log"] if e["kind"] == "receipt")
    assert receipt["thread"] == "web" and receipt["to"] == "thread:inbox"
    assert receipt["ref"] == "https://github.com/acme/brnrd/pull/7"
    assert receipt["from"] in {s["id"] for s in thread["strands"]}
    # Nothing the loom lacks is sent as a placeholder.
    text = json.dumps(doc)
    assert '""' not in text and "null" not in text


def test_a_burst_of_changes_pushes_once_after_the_debounce(board):
    fuel(board.root, 90)
    board.start()
    wait_until(lambda: len(board.cloud.calls) == 1, 10, board.dump)
    fuel(board.root, 80)
    steps(board)
    board.now[0] += 3
    fuel(board.root, 70)
    steps(board)
    board.now[0] += snapshot.DEBOUNCE - 1
    steps(board)
    assert len(board.cloud.calls) == 1
    board.now[0] += 1
    wait_until(lambda: len(board.cloud.calls) == 2, 10, board.dump)
    steps(board)
    assert len(board.cloud.calls) == 2
    assert board.cloud.calls[1]["doc"]["fuel"][0]["gauges"][0]["left_pct"] == 70


def test_an_unchanged_snapshot_is_re_put_once_a_minute(board):
    board.start()
    wait_until(lambda: len(board.cloud.calls) == 1, 10, board.dump)
    board.now[0] += snapshot.IDLE - 1
    steps(board)
    assert len(board.cloud.calls) == 1
    board.now[0] += 1
    wait_until(lambda: len(board.cloud.calls) == 2, 10, board.dump)
    steps(board)
    first, second = (c["doc"] for c in board.cloud.calls)
    assert len(board.cloud.calls) == 2 and snapshot.digest(first) == snapshot.digest(second)


def test_a_failing_board_is_logged_once_and_the_loop_keeps_going(board):
    board.cloud.fail = True
    board.start()
    for count in (1, 2, 3):
        wait_until(lambda: len(board.cloud.calls) == count, 10, board.dump)
        steps(board)
        board.now[0] += snapshot.IDLE
    log = board.root / "loom/loom.log"
    assert log.read_text().count("snapshot: push failed: board unreachable") == 1
    board.cloud.fail = False
    wait_until(lambda: len(board.cloud.calls) == 4, 10, board.dump)
    steps(board)
    board.cloud.fail = True
    board.now[0] += snapshot.IDLE
    wait_until(lambda: log.read_text().count("snapshot: push failed") == 2, 10, board.dump)
    assert board.thread.is_alive()


def test_without_relay_state_nothing_is_read_or_sent(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "web", README, "answer-fast", wait="0")
    calls = []
    monkeypatch.setattr(cloud, "_request", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(cloud, "_load_state_from_dir", lambda *a: calls.append(a))
    monkeypatch.setattr(snapshot, "assemble", lambda *a: calls.append(a))
    now = [0.0]
    monkeypatch.setattr(snapshot, "clock", lambda: now[0])
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: (root / "ledger").is_dir(), 10, loom.dump)
        for _ in range(3):
            now[0] += snapshot.IDLE
            time.sleep(0.1)
    finally:
        loom.halt()
    assert calls == [] and not loom.errors


def test_an_open_ask_to_a_person_is_their_turn(tmp_path):
    from brr.loom.runtime.config import load_config
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import append, read_facts
    from brr.loom.runtime.verbs_wishes import wish_fact
    root = tmp_path / "home"
    write_thread(root, "web", README, "answer-fast", wait="0")
    home = Home(root)

    def turn():
        doc = snapshot.assemble(home, read_facts(home), load_config(root))
        return next(t for t in doc["threads"] if t["id"] == "web")["turn"]
    assert turn() == "none"
    ask = append(home, wish_fact(read_facts(home), by="person:test", ident="new", line="Pick a domain",
                                 thread="web", sender="resident"))
    assert turn() == "yours"
    append(home, Fact(kind="wish.verdict", by="person:test", id="wish.verdict:t1",
                      data={"wish": ask.data["wish"], "as": "accepted", "evidence": "word", "ref": "-"}))
    assert turn() == "none"
