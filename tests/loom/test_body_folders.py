from _step import mail
"""A live home runs threads on different bodies, including a home-supplied one."""

from brr.loom.runtime import speak
from brr.loom.runtime.ledger import inject_letter

from brr.loom.runtime.selfrepo import git, init_self

from _step import Loom, wait_until, write_thread


def test_each_thread_answers_through_its_own_body_folder(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "bundled", "---\nid: bundled\nstatus: open\ntense: plan\n"
                 "---\n# Bundled body\n", "echo-wake", wait="0")
    write_thread(root, "local", "---\nid: local\nstatus: open\ntense: plan\n"
                 "---\n# Local body\n", "unused", wait="0")
    (root / "self/threads/bundled/seat").write_text("body: fake/echo\n")
    (root / "self/threads/local/seat").write_text("body: local/small\n")
    git(root / "self", "add", "threads")
    git(root / "self", "commit", "-m", "Choose the thread bodies.")
    folder = root / "loom/bodies/local"
    folder.mkdir(parents=True)
    (folder / "jack").write_text("hand\n")
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
        wait_until(lambda: len(sent) == 2 and not mail(root, loom.facts(), "bundled")
                   and not mail(root, loom.facts(), "local"), 12, loom.dump)
        assert "local small: hello local" in sent
        assert any("# Bundled body" in text and "hello bundled" in text for text in sent)
    finally:
        loom.halt()
    assert not loom.errors


def test_gauge_at_configured_floor_parks_then_starts_and_answers(tmp_path, capsys):
    from brr.loom.runtime.project import holder
    from brr.loom.runtime.verbs_loom import _attention
    root = tmp_path / "home"
    write_thread(root, "gauge", "---\nid: gauge\nstatus: open\ntense: plan\n"
                 "---\n# Gauge\n", "answer-fast", wait="0")
    (root / "self/threads/gauge/seat").write_text("body: gauge/echo\n")
    git(root / "self", "add", "threads")
    git(root / "self", "commit", "-m", "Choose the gauge body.")
    folder = root / "loom/bodies/gauge"
    folder.mkdir(parents=True)
    (root / "loom/config.toml").write_text("hold_floor_pct = 7\n")
    (folder / "jack").write_text("hooks\n")
    run = folder / "run"
    run.write_text("""#!/usr/bin/env python3
import os, sys
os.execv(sys.executable, [sys.executable, '-m', 'brr.loom.runtime.fakebody',
                        '--room', sys.argv[1], '--policy', 'answer-fast'])
""")
    run.chmod(0o755)
    usage = folder / "usage"
    usage.write_text("""#!/usr/bin/env python3
import json, time
from datetime import datetime, timezone
from pathlib import Path
left = 100 if Path(__file__).with_name('refilled').exists() else 7
print(json.dumps({'windows': [{'name': 'week', 'left_pct': left,
    'resets_at': datetime.fromtimestamp(time.time() + 1, timezone.utc).isoformat()}]}))
""")
    usage.chmod(0o755)
    inject_letter(root, to="thread:gauge", body="answer after refill")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
        facts = loom.facts()
        assert not any(f.kind in {"body.started", "body.died"} for f in facts)
        assert holder(facts, "gauge") and mail(root, facts, "gauge")
        _attention(["--home", str(root)])
        assert "parked · gauge · gauge week 7% · until " in capsys.readouterr().out
        (folder / "refilled").touch()
        wait_until(lambda: not mail(root, loom.facts(), "gauge"), 10, loom.dump)
        assert len([f for f in loom.facts() if f.kind == "body.started"]) == 1
        assert not any(f.kind == "body.died" for f in loom.facts())
        _attention(["--home", str(root)])
        assert "parked" not in capsys.readouterr().out
    finally:
        loom.halt()
    assert not loom.errors


def test_landed_seat_edit_and_molt_switch_the_successors_body(tmp_path):
    from test_lifecycle import bodies
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work", "work", wait="0")
    bodies(root, '''import sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.selfrepo import git
ctx = Ctx(Path(sys.argv[1]))
ctx.owed()
clone = ctx.room / "self"
(clone / "threads/work/seat").write_text("# Execution choice\\nbody: other/small\\nrepo: ignored\\nwatch: ignored\\n")
git(clone, "add", "threads/work/seat")
git(clone, "commit", "-m", "Choose another body.")
ctx.molt("use the landed seat")
''')
    folder = root / "loom/bodies/other"
    folder.mkdir(parents=True)
    (folder / "jack").write_text("hand\n")
    script = folder / "run"
    script.write_text('''#!/usr/bin/env python3
import sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
for letter in ctx.owed():
    ctx.send("thread:work", note="answered by " + sys.argv[2], re=letter.id)
''')
    script.chmod(0o755)
    first = inject_letter(root, to="thread:work", body="switch and finish this ask")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: not mail(root, loom.facts(), "work"), 20, loom.dump)
        starts = [f for f in loom.facts() if f.kind == "body.started"]
        assert [f.data["shell"] for f in starts] == ["fake", "other"]
        assert starts[0].data["strand"] != starts[1].data["strand"]
        assert any(f.kind == "note" and f.data.get("re") == first
                   and f.data.get("why") == "answered by small" for f in loom.facts())
        assert "body: other/small" in git(root / "self", "show", "main:threads/work/seat").stdout
    finally:
        loom.halt()
    assert not loom.errors
