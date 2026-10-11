"""A live home runs threads on different bodies, including a home-supplied one."""

from brr.loom.runtime import speak
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.project import owed

from _step import Loom, wait_until, write_thread


def test_each_thread_answers_through_its_own_body_folder(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "bundled", "---\nid: bundled\nstatus: open\ntense: plan\n"
                 "body: fake/echo\n---\n# Bundled body\n", "echo-wake", wait="0")
    write_thread(root, "local", "---\nid: local\nstatus: open\ntense: plan\n"
                 "body: local/small\n---\n# Local body\n", "unused", wait="0")
    folder = root / "loom/bodies/local"
    folder.mkdir(parents=True)
    (folder / "jack").write_text("hand\n")
    (folder / "wall").write_text("usage limit\n")
    script = folder / "run"
    script.write_text("""#!/usr/bin/env python3
import sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
for letter in ctx.owed():
    ctx.send("channel:fake", "local " + sys.argv[2] + ": " + letter.body, re=letter.id)
ctx.jack("stop")
""")
    script.chmod(0o755)
    sent = []
    monkeypatch.setitem(speak.EFFECTS, "fake", lambda _h, _c, _k, text, _ctx: sent.append(text))
    inject_letter(root, to="thread:bundled", body="hello bundled")
    inject_letter(root, to="thread:local", body="hello local")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: len(sent) == 2 and not owed(loom.facts(), "bundled")
                   and not owed(loom.facts(), "local"), 12, loom.dump)
        assert "local small: hello local" in sent
        assert any("# Bundled body" in text and "hello bundled" in text for text in sent)
    finally:
        loom.halt()
    assert not loom.errors
