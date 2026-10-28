"""The loop pushes snapshot v1 to the board: on a settled change, else once a minute."""

import json
import sys
import time

import pytest

from brr.daemon2.facts import Fact
from brr.gates import cloud
from brr.loom.runtime import snapshot, usage
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts

from _step import Loom, kill_bodies, wait_until, write_thread

README = """---
id: web
status: open
tense: plan
opened-by: thread:inbox
---
# Put the loom on the web. Then tell brnrd.

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
    """Notes what it is owed; on web it also reports the PR to brnrd."""
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
    if own == "web" and letter.sender == "p-test":
        ctx.send("thread:inbox", "PR https://github.com/acme/brnrd/pull/7 opened", cite=letter.id)
# The PR letter's no-policy notice can land at the stop; a body answers what the stop hands it.
while letters := ctx.jack("stop").letters:
    for letter in letters:
        ctx.send("thread:" + own, note="ok", re=letter.id)
""")
    run.chmod(0o755)


def steps(loom, n=3):
    """Let the loop assemble ``n`` more times at the current clock."""
    seen = len(loom.assembled)
    wait_until(lambda: len(loom.assembled) >= seen + n, 10, loom.dump)


def test_document_matches_the_contract(board):
    fuel(board.root, 82)
    reporting_body(board.root)
    ask = inject_letter(board.root, to="thread:web", body="Ship the board, please.")
    # The plan is web's wishes; its ticked README rows count for nothing.
    home = Home(board.root)
    for ident, line in (("w-contract", "Contract read"), ("w-push", "Publisher pushes")):
        append(home, Fact(kind="wish", by="person:test", id=ident, data={
            "wish": ident, "says": [{"letter": ask}], "from": "person", "line": line, "thread": "web"}))
    append(home, Fact(kind="wish.result", by="strand:s-test-web001", data={
        "wish": "w-contract", "kind": "pr", "ref": "#7", "line": "read", "delivered": True}))
    board.start()

    def pushed_full():
        board.now[0] += snapshot.IDLE
        steps(board)
        doc = board.cloud.calls[-1]["doc"] if board.cloud.calls else {}
        web = [t for t in doc.get("threads", []) if t["id"] == "web"]
        ended = any(f.kind == "attempt.ended" for f in read_facts(home))
        return ended and web and web[0].get("strands") and any(e["kind"] == "receipt" for e in doc["log"])
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


def test_a_failing_board_is_one_streak_and_the_loop_keeps_going(board):
    board.cloud.fail = True
    board.start()
    for count in (1, 2, 3):
        wait_until(lambda: len(board.cloud.calls) == count, 10, board.dump)
        steps(board)
        board.now[0] += snapshot.IDLE
    kinds = lambda kind: [f for f in read_facts(Home(board.root)) if f.kind == kind]
    # The parts pusher shares the unreachable cloud and keeps its own streak; this one is the publisher's.
    publisher = lambda: [f for f in kinds("loom.degraded") if f.data["step"] == "publisher"]
    [down] = publisher()
    assert "board unreachable" in down.data["error"]
    log = board.root / "loom/loom.log"
    assert log.read_text().count("degraded: publisher") == 1
    board.cloud.fail = False
    wait_until(lambda: len(board.cloud.calls) == 4, 10, board.dump)
    wait_until(lambda: kinds("loom.recovered"), 10, board.dump)
    board.cloud.fail = True
    board.now[0] += snapshot.IDLE
    wait_until(lambda: len(publisher()) == 2, 10, board.dump)
    assert log.read_text().count("degraded: publisher") == 2
    assert board.thread.is_alive()


def test_a_section_that_raises_is_left_out_and_is_its_own_streak(board, monkeypatch):
    fuel(board.root, 82)
    broken, real = [True], snapshot._fuel

    def gauges(*args):
        if broken[0]:
            raise TypeError("cannot unpack non-iterable dict object")
        return real(*args)
    monkeypatch.setattr(snapshot, "_fuel", gauges)
    board.start()
    wait_until(lambda: board.cloud.calls, 10, board.dump)
    doc = board.cloud.calls[-1]["doc"]
    assert "fuel" not in doc and any(t["id"] == "web" for t in doc["threads"])
    kinds = lambda kind: [f for f in read_facts(Home(board.root)) if f.kind == kind]
    [down] = kinds("loom.degraded")
    assert down.data["step"] == "publisher.fuel"
    assert down.data["error"] == "TypeError: cannot unpack non-iterable dict object"
    assert down.data["where"].startswith("loom/runtime/snapshot.py:")
    broken[0] = False
    wait_until(lambda: kinds("loom.recovered"), 10, board.dump)
    [back] = kinds("loom.recovered")
    assert back.data["step"] == "publisher.fuel"
    board.now[0] += snapshot.IDLE
    wait_until(lambda: "fuel" in board.cloud.calls[-1]["doc"], 10, board.dump)
    assert [f.data["step"] for f in kinds("loom.degraded")] == ["publisher.fuel"]


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
    # The input this test used to build, a resident wish with says and no parent, is refused.
    with pytest.raises(ValueError, match="ask --for <wish>"):
        wish_fact(read_facts(home), by="person:test", ident="new", line="Pick a domain",
                  says=[["s-test-web001/ask", 0, None]], thread="web", sender="resident")
    root_wish = append(home, wish_fact(read_facts(home), by="person:test", ident="new", line="A site",
                                       says=[["p-test/site", 0, None]], thread="web"))
    ask = append(home, wish_fact(read_facts(home), by="person:test", ident="new", line="Pick a domain",
                                 thread="web", sender="resident", parent=root_wish.data["wish"]))
    assert turn() != "yours"  # it waits on its opener, thread:inbox, until that asks onward
    onward = append(home, wish_fact(read_facts(home), by="person:test", ident="new", line="Pick a domain",
                                    thread="inbox", sender="resident", parent=ask.data["wish"]))
    assert turn() == "yours"
    for i, w in enumerate((onward, ask)):
        append(home, Fact(kind="wish.verdict", by="person:test", id=f"wish.verdict:t{i}",
                          data={"wish": w.data["wish"], "as": "accepted", "evidence": "word", "ref": "-"}))
    assert turn() == "none"


def test_wishes_carry_their_words_results_and_the_answer_newer_than_them(tmp_path):
    from brr.loom.runtime.config import load_config
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import append, read_facts
    root = tmp_path / "home"
    write_thread(root, "web", README, "answer-fast", wait="0")
    home = Home(root)

    def at(minute):
        return f"2026-10-19T10:{minute:02d}:00+00:00"

    def fact(kind, minute, ident, data, by="strand:s-test-web001"):
        append(home, Fact(kind=kind, by=by, id=ident, at=at(minute), data=data))

    def letter(minute, ident, body, **extra):
        fact("letter", minute, ident, {"id": ident, "from": "person:test", "to": "thread:web",
                                       "body": body, **extra}, by="person:test")

    def wish(minute, ident, sender, line, says=(), by="strand:s-test-web001", **parent):
        fact("wish", minute, ident, {"wish": ident, "says": list(says), "from": sender,
                                     "line": line, "thread": "web", **parent}, by=by)

    def result(minute, ident, wish, ref, line, delivered):
        fact("wish.result", minute, ident, {"wish": wish, "kind": "pr", "ref": ref, "line": line,
                                            "delivered": delivered})
    letter(0, "p-test/said1", "Hello. Show my wishes on the board, please.")
    wish(1, "w-said", "person", "Wishes on the board", [["p-test/said1", 7, 27]], by="person:test")
    fact("wish.read", 2, "wish.read:r1", {"wish": "w-said", "reading": "a list per wish", "by": "resident"})
    result(3, "wish.result:r1", "w-said", "#1", "draft", False)
    result(4, "wish.result:r2", "w-said", "#2", "board", True)
    letter(5, "p-test/answer", "Not quite: also the cost.", wish="w-said", verdict="correct")
    wish(3, "w-ask", "resident", "Pick a domain", parent="w-said")
    # An unplaced quote (dispositions.say_of) cites the whole letter.
    wish(3, "w-unplaced", "person", "Unplaced", [["p-test/said1", 0, None, "not in it"]], by="person:test")
    # A record, the shape every new wish fact writes; and one bad say beside a good one.
    wish(3, "w-record", "person", "Record", [{"letter": "p-test/said1", "offset": 7, "size": 4}], by="person:test")
    wish(3, "w-bad", "person", "Bad", [["p-test/said1", "7", 4], {"offset": 1}, "p-test/said1",
                                        ["p-test/said1", 0, 6]], by="person:test")
    wish(6, "w-done", "person", "Old thing", [["p-test/said1", 0, None]], by="person:test")
    result(7, "wish.result:d1", "w-done", "#0", "old", True)
    letter(8, "p-test/agree", "Yes.", wish="w-done", verdict="agree")
    fact("wish.verdict", 9, "wish.verdict:d1", {"wish": "w-done", "as": "accepted", "evidence": "word",
                                                "ref": "p-test/agree"}, by="person:test")

    rows = snapshot.assemble(home, read_facts(home), load_config(root))["wishes"]
    assert [w["id"] for w in rows] == ["w-said", "w-ask", "w-bad", "w-record", "w-unplaced", "w-done"]
    said, ask, bad, record, unplaced, done = rows
    assert [s["head"] for s in unplaced["says"]] == ["Hello. Show my wishes on the board, please."]
    assert [s["head"] for s in record["says"]] == ["Show"]
    assert [s["head"] for s in bad["says"]] == ["Hello."]
    assert said == {
        "id": "w-said", "thread": "web", "from": "person", "line": "Wishes on the board",
        "state": "delivered", "origin": "said", "at": "2026-10-19T10:04:00Z",
        "says": [{"id": "p-test/said1", "from": "person:test", "at": "2026-10-19T10:00:00Z",
                  "head": "Show my wishes on the board"}],
        "reading": "a list per wish",
        "results": [{"kind": "pr", "ref": "#1", "line": "draft", "delivered": False, "at": "2026-10-19T10:03:00Z"},
                    {"kind": "pr", "ref": "#2", "line": "board", "delivered": True, "at": "2026-10-19T10:04:00Z"}],
        "answer": {"verdict": "correct", "at": "2026-10-19T10:05:00Z", "head": "Not quite: also the cost."},
    }
    assert ask == {"id": "w-ask", "thread": "web", "from": "resident", "line": "Pick a domain",
                   "state": "heard", "origin": "said", "at": "2026-10-19T10:03:00Z"}
    assert done["state"] == "accepted" and "answer" not in done
    assert done["verdict"] == {"as": "accepted", "evidence": "word", "at": "2026-10-19T10:09:00Z"}
