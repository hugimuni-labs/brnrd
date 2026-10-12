"""A molted successor inherits the committed next step and the unanswered ask."""

import json

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.selfrepo import init_self
from brr.loom.runtime.project import is_notice, owed

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
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
if "Next step: inherited" in ctx.wake():
    Path({str(received)!r}).write_text(json.dumps({{"wake": ctx.wake(), "bodies": [l.body for l in letters]}}))
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
        wait_until(lambda: not owed(loom.facts(), "work"), 5, loom.dump)
        assert not any(is_notice(f) and "body exited" in f.data["body"] for f in loom.facts())
        assert not list((root / "rooms").glob("*/port/molt-pending"))
    finally:
        loom.halt()
    assert not loom.errors
