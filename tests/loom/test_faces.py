"""A body's face reaches its thread through the port, and dies with the body."""

import json

from brr.loom.runtime.faces import faces
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.selfrepo import init_self

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies


def test_face_is_the_bodys_own_and_a_successor_starts_without_one(tmp_path):
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work", "work", wait="0")
    inject_letter(root, to="thread:work", body="first ask")
    seen, woke = root / "seen.json", root / "woke.json"
    bodies(root, f'''import json, subprocess, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.home import atomic_write
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.home import home_of_room
ctx = Ctx(Path(sys.argv[1]))
def face(*args):
    return subprocess.run([sys.executable, "-m", "brr.loom.runtime", "face", "--room", str(ctx.room), *args],
                          capture_output=True, text=True, env=ctx.env, cwd=ctx.room)
letters = ctx.owed()
if "carry on" not in ctx.wake():
    refused = face("blah")
    telemetry = face("id_l")
    assert face("focsued").stdout == "face focused\\n"
    atomic_write(Path({str(seen)!r}), json.dumps({{
        "refused": [refused.returncode, refused.stderr], "telemetry": telemetry.returncode}}))
    ctx.jack("post")
    for letter in letters:
        ctx.send("thread:work", note="faced", re=letter.id)
    ctx.molt("carry on")
    assert ctx.jack("stop").raw == ""
else:
    from brr.loom.runtime.faces import faces
    atomic_write(Path({str(woke)!r}), json.dumps(faces(read_facts(home_of_room(ctx.room)))))
    face("tired"); ctx.jack("post"); face("--off"); ctx.jack("post")
    for letter in letters:
        ctx.send("thread:work", note="done", re=letter.id)
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(seen.exists, 30, loom.dump)
        result = json.loads(seen.read_text())
        assert result["refused"][0] == 1
        assert "one of: focused stuck pleased" in result["refused"][1]
        assert result["telemetry"] == 1
        wait_until(lambda: faces(loom.facts()).get("work"), 15, loom.dump)
        first = faces(loom.facts())["work"]
        assert first["word"] == "focused"
        wait_until(lambda: any(f.kind == "face" and f.data["word"] is None for f in loom.facts()), 30, loom.dump)
        written = [f for f in loom.facts() if f.kind == "face"]
        # The refused words wrote nothing.
        assert [f.data["word"] for f in written] == ["focused", "tired", None]
        assert written[1].data["strand"] != first["strand"]
        assert json.loads(woke.read_text()) == {}
        assert "work" not in faces(loom.facts())
    finally:
        loom.halt()
    assert not loom.errors

