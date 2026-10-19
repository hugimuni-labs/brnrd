from _step import mail
"""A molted successor inherits the committed next step and the unanswered ask."""

import json

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.selfrepo import init_self
from brr.loom.runtime.project import is_notice

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies


def test_molt_mid_work_lands_readme_and_inherits_unanswered_letter(tmp_path):
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work\n\nNext step: original", "work", wait="0")
    first = inject_letter(root, to="thread:work", body="first ask")
    second = inject_letter(root, to="thread:work", body="second ask")
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
        assert second in result["wake"] and "second ask" in result["bodies"]
        assert "first ask" not in result["bodies"]
        assert "Next step: inherited" in (root / "self/threads/work/README.md").read_text()
        wait_until(lambda: not mail(root, loom.facts(), "work"), 5, loom.dump)
        assert any(f.kind == "attempt.ended" and f.data["cause"] == "molted" for f in loom.facts())
        assert not any(is_notice(f) and "body exited" in f.data["body"] for f in loom.facts())
        assert not list((root / "rooms").glob("*/port/molt-pending"))
    finally:
        loom.halt()
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
