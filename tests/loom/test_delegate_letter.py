"""A background delegate finishing reaches its body, held in stop, as a letter."""

import json

from brr.loom.runtime.ledger import inject_letter

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies


def test_held_body_receives_its_background_delegate_report_as_a_letter(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "work", "Worker", "work", wait="30")
    seen = root / "seen.json"
    report = "PINEAPPLE " + "x" * 2000
    bodies(root, f'''import json, sys, threading, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
for letter in ctx.owed():
    ctx.send(f"thread:{{own}}", note="delegated", re=letter.id)
while ctx.jack("post").letters:
    time.sleep(0.03)
# A foreground delegate answered in its tool result: no letter.
ctx.jack("subagent-stop", {{"agent_id": "fg1", "agent_type": "general-purpose",
                            "last_assistant_message": "inline", "background_tasks": []}})
finish = threading.Timer(0.5, ctx.jack, ("subagent-stop", {{
    "agent_id": "bg1", "agent_type": "general-purpose", "last_assistant_message": {report!r},
    "background_tasks": [{{"id": "bg1", "type": "subagent", "status": "running"}}]}}))
finish.start()
began = time.monotonic()
view = ctx.jack("stop")
held = time.monotonic() - began
for letter in view.letters:
    ctx.send(f"thread:{{own}}", note="read", re=letter.id)
Path({str(seen)!r}).write_text(json.dumps({{
    "kind": view.kind, "held": held, "room": str(ctx.room),
    "letters": [[letter.id, letter.reply, letter.body] for letter in view.letters]}}))
''')
    (root / "loom/bodies/fake/jack").write_text("hooks\n")
    inject_letter(root, to="thread:work", body="delegate the mock")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(seen.exists, 15, loom.dump)
    finally:
        loom.halt()
    assert not loom.errors
    got = json.loads(seen.read_text())
    assert got["kind"] == "block"
    room = got["room"]
    strand = room.rsplit("/", 1)[1]
    [[ident, reply, body]] = got["letters"]
    path = f"{room}/delegates/bg1.md"
    assert ident.startswith(f"{strand}/") and reply == "-"
    assert body.startswith(f"delegate general-purpose bg1 finished; full report: {path}\nPINEAPPLE x")
    head = body.rstrip("\n").split("\n", 1)[1]
    assert head.endswith("…") and len(head) == 800
    assert open(path).read() == report + "\n"
    assert not (tmp_path / "home/rooms" / strand / "delegates/fg1.md").exists()
    # One tick, not the 30 s wait: the timer letter is due in the tick that drains it.
    assert got["held"] < 3, got["held"]
