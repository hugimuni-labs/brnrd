from _step import mail
"""A molted successor inherits the committed next step; a person hears back before the molt."""

import json

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.selfrepo import init_self
from brr.loom.runtime.project import is_notice

from _step import Loom, kill_bodies, wait_until, write_thread
from test_lifecycle import bodies


def _chat_letter(root, body):
    """A person letter a connector brought in, so it has a chat to answer on."""
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.home import Home, mint
    from brr.loom.runtime.ledger import append
    source, letter = f"src-{mint(5)}", f"p-test/{mint(5)}"
    append(Home(root), Fact(kind="source", by="loom:test", id=source, data={"id": source, "channel": "channel:fake"}))
    append(Home(root), Fact(kind="letter", by="person:p-test", id=letter, data={
        "id": letter, "to": "thread:work", "body": body, "from": "p-test", "cites": source}))
    return letter


def test_molt_refuses_an_unanswered_person_letter_then_lands_readme_and_hands_over_the_rest(tmp_path):
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work\n\nNext step: original", "work", wait="0")
    first = inject_letter(root, to="thread:work", body="first ask")
    second = _chat_letter(root, "second ask")
    received = root / "successor.json"
    bodies(root, f'''import json, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
if "Next step: inherited" in ctx.wake():
    atomic_write(Path({str(received)!r}), json.dumps({{"wake": ctx.wake(), "bodies": [l.body for l in letters]}}))
    for letter in letters:
        ctx.send("thread:work", note="finished inherited work", re=letter.id)
else:
    assert {{l.body for l in letters}} == {{"first ask", "second ask"}}
    ctx.send("channel:fake", "first answer", re={first!r})
    path = ctx.room / "self/threads/work/README.md"
    path.write_text(path.read_text().replace("Next step: original", "Next step: inherited"))
    try:
        ctx.molt("carry the next step")
    except RuntimeError as exc:
        assert {second!r} in str(exc) and {first!r} not in str(exc), str(exc)
    else:
        raise AssertionError("molt passed with a person letter unanswered")
    ctx.send("channel:fake", "on it, my successor finishes it", re={second!r})
    ctx.molt("carry the next step")
    assert ctx.jack("stop").raw == ""
assert not (ctx.room / "port/molt-pending").exists()
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(received.exists, 30, loom.dump)
        result = json.loads(received.read_text())
        assert "Next step: inherited" in result["wake"]
        # The ack settled the second ask; the successor carries its work from the README.
        assert "second ask" not in result["bodies"] and "first ask" not in result["bodies"]
        assert any(f.kind == "letter" and f.data.get("re") == second and f.data.get("to") == "channel:fake"
                   for f in loom.facts())
        assert "Next step: inherited" in (root / "self/threads/work/README.md").read_text()
        wait_until(lambda: not mail(root, loom.facts(), "work"), 5, loom.dump)
        assert any(f.kind == "attempt.ended" and f.data["cause"] == "molted" for f in loom.facts())
        assert not any(is_notice(f) and "body exited" in f.data["body"] for f in loom.facts())
        assert not list((root / "rooms").glob("*/port/molt-pending"))
    finally:
        loom.halt()
    assert not loom.errors


def test_molt_counts_a_reply_the_loom_has_not_drained_yet(tmp_path):
    # The loom drains port/out on its tick; a molt right after the ack ran
    # before that tick in 1 of 5 runs and was refused. Here the loom is stopped
    # between the ack and the molt, so the ack is only ever in port/out.
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work\n\nNext step: original", "work", wait="0")
    ask = _chat_letter(root, "an ask")
    go, result = root / "go", root / "result.txt"
    bodies(root, f'''import sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
ctx.owed()
while not Path({str(go)!r}).exists():
    time.sleep(0.02)
ctx.send("channel:fake", "on it, my successor finishes it", re={ask!r})
try:
    ctx.molt("carry the next step")
except RuntimeError as exc:
    atomic_write(Path({str(result)!r}), str(exc))
else:
    atomic_write(Path({str(result)!r}), "molted")
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "shown" and ask in f.data.get("ids", "") for f in loom.facts()),
                   30, loom.dump)
        loom.stop.set()
        loom.thread.join(timeout=5)
        go.touch()
        wait_until(result.exists, 30, loom.dump)
        assert result.read_text() == "molted"
        assert loom.out_files(), "the loom drained port/out; the ack was not held back"
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_tainted_molt_carries_room_work_without_laundering_it(tmp_path):
    from dataclasses import replace

    from brr.loom.runtime.labels import strand_label
    from brr.loom.runtime.selfrepo import git

    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work\n\nNext step: original", "work", wait="0")
    inject_letter(root, to="thread:work", body="research this")
    main = git(root / "self", "rev-parse", "main").stdout.strip()
    received = root / "successor.json"
    bodies(root, f'''import json, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.home import atomic_write, home_of_room
from brr.loom.runtime.labels import strand_label
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.merge import send_to_self
from brr.loom.runtime.selfrepo import git
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
clone = ctx.room / "self"
if "Next step: researched" in ctx.wake():
    result = {{"wake": ctx.wake(), "strand": ctx.room.name,
              "research": (clone / "threads/work/research.md").read_text()}}
    for letter in letters:
        ctx.send("thread:work", note="research inherited", re=letter.id)
    outcome = send_to_self(clone)
    result.update(status=outcome.status, stderr=outcome.stderr)
    atomic_write(Path({str(received)!r}), json.dumps(result))
else:
    atomic_write(ctx.room / "port/out/taint-research.json", '{{"tool":"WebFetch"}}\\n')
    home = home_of_room(ctx.room)
    deadline = time.monotonic() + 5
    while not strand_label(read_facts(home), ctx.room.name, self_root=home.root / "self").taint:
        assert time.monotonic() < deadline, "web receipt not ingested"
        time.sleep(0.02)
    path = clone / "threads/work/README.md"
    path.write_text(path.read_text().replace("Next step: original", "Next step: researched"))
    (clone / "threads/work/research.md").write_text("untrusted research stays in the room\\n")
    git(clone, "add", "threads/work/research.md")
    git(clone, "commit", "-m", "Keep research for the next body.")
    ctx.molt("carry the research")
    assert ctx.jack("stop").raw == ""
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(received.exists, 30, loom.dump)
        result = json.loads(received.read_text())
        assert "Next step: researched" in result["wake"]
        assert "carry the research" in result["wake"]
        assert result["research"] == "untrusted research stays in the room\n"
        assert result["status"] == "refused"
        assert "taint=1 strand " + result["strand"] in result["stderr"]
        assert "20-labels" in result["stderr"]
        assert git(root / "self", "rev-parse", "main").stdout.strip() == main
        assert "Next step: original" in (root / "self/threads/work/README.md").read_text()
        assert not (root / "self/threads/work/research.md").exists()
        facts = loom.facts()
        successor = result["strand"]
        label = lambda fs: strand_label(fs, successor, self_root=root / "self", jack_log="")
        assert label(facts).taint
        # Both independent routes preserve quarantine: ancestry, and the shown letter.
        assert label([f for f in facts if not (f.kind == "shown" and f.data.get("strand") == successor)]).taint
        assert label([replace(f, data={k: v for k, v in f.data.items() if k != "parent"})
                      if f.kind == "lease" else f for f in facts]).taint
        wait_until(lambda: not mail(root, loom.facts(), "work"), 5, loom.dump)
    finally:
        loom.halt()
    assert not loom.errors


def test_a_successor_is_told_which_owed_letters_its_predecessor_already_read(tmp_path):
    """Live, 2026-10-12: a person's "add the line, then molt" stayed owed after the
    molt; the successor read it as an instruction, redid it and molted again."""
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work\n\nNext step: original", "work", wait="0")
    ask = inject_letter(root, to="thread:work", body="add the line, then molt")
    received = root / "successor.json"
    bodies(root, f'''import json, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
if "predecessor" in ctx.wake():
    atomic_write(Path({str(received)!r}), json.dumps({{"wake": ctx.wake()}}))
    for letter in letters:
        ctx.send("thread:work", note="settled", re=letter.id)
else:
    ctx.molt("line added")
    assert ctx.jack("stop").raw == ""
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(received.exists, 30, loom.dump)
        wake = json.loads(received.read_text())["wake"]
        assert f"already read by your predecessor, who molted and left its handover: {ask}" in wake
        assert wake.count("already read by your predecessor") == 1
    finally:
        loom.halt()
    assert not loom.errors


def test_a_successor_wakes_with_its_predecessors_molt_why_and_the_notes_after_it(tmp_path):
    """wake-vision: gen 221 of the inbox grepped the ledger for the molt why and the
    note its predecessor wrote after the molt, which held the real state."""
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work\n\nNext step: original", "work", wait="0")
    inject_letter(root, to="thread:work", body="merge it once green")
    received = root / "successor.json"
    bodies(root, f'''import json, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
if "handover from" in ctx.wake():
    atomic_write(Path({str(received)!r}), json.dumps({{"wake": ctx.wake()}}))
    for letter in letters:
        ctx.send("thread:work", note="settled", re=letter.id)
else:
    ctx.molt("NEXT: merge #7 when its re-run is green")
    ctx.send("thread:work", note="done after the molt: #7 merged as abc123", re=letters[0].id)
    assert ctx.jack("stop").raw == ""
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(received.exists, 30, loom.dump)
        wake = json.loads(received.read_text())["wake"]
        assert "(gen 1, molted " in wake
        assert "NEXT: merge #7 when its re-run is green" in wake
        assert "done after the molt: #7 merged as abc123" in wake
        assert wake.index("NEXT: merge #7") < wake.index("done after the molt")
    finally:
        loom.halt()
    assert not loom.errors
