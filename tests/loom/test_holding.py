from _step import mail
"""Warm follow-ups and paced exits through real loom loops and hook bodies."""

import json
import os

from connector_support import install_fake
from brr.loom.runtime.ledger import inject_letter

from brr.loom.runtime.selfrepo import git, init_self

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies


def test_worker_answers_followup_warm_then_is_held_and_a_later_letter_starts_it(tmp_path, monkeypatch):
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "opener", "Opener", "answer-fast", wait="0")
    write_thread(root, "work", "---\nid: work\nstatus: open\ntense: plan\n"
                 "opened-by: thread:opener\n---\n# Worker\n", "work", wait="1.2")
    replies = []
    install_fake(monkeypatch, lambda text: replies.append(json.loads(text)))
    waiting = root / "waiting"
    follow = root / "follow"
    kept = root / "kept"
    bodies(root, f'''import json, os, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
follow = Path({str(follow)!r})
while True:
    for letter in letters:
        ctx.send("channel:fake", json.dumps({{"body": letter.body, "pid": os.getpid()}}), re=letter.id)
    # Let the loom record our last answer before asking to stop.
    while ctx.jack("post").letters:
        time.sleep(0.03)
    ctx.handoff({str(waiting)!r}, str(os.getpid()))
    if not follow.exists():
        # Keep the flock until the follow-up is already in the ledger. Writing
        # waiting and then calling stop made jack's wait the whole window.
        while not follow.exists():
            time.sleep(0.03)
    view = ctx.jack("stop")
    if view.kind != "block":
        break
    if not view.letters:
        # Owing nothing past its wait, a worker is held, never let go; the
        # fake ends here so a later letter has a body to start.
        ctx.handoff({str(kept)!r}, json.loads(view.raw)["reason"])
        break
    letters = view.letters
''')
    (root / "loom/bodies/fake/jack").write_text("hooks\n")
    inject_letter(root, to="thread:work", body="first")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: len(replies) == 1 and waiting.exists() and not mail(root, loom.facts(), "work"), 12, loom.dump)
        pid = replies[0]["pid"]
        os.kill(pid, 0)
        inject_letter(root, to="thread:work", body="follow-up")
        follow.touch()
        wait_until(lambda: len(replies) == 2 and not mail(root, loom.facts(), "work"), 10, loom.dump)
        assert replies == [{"body": "first", "pid": pid}, {"body": "follow-up", "pid": pid}]
        wait_until(lambda: any(f.kind == "body.exited" for f in loom.facts()), 8, loom.dump)
        assert kept.read_text().startswith("cache keepalive, nothing new arrived")
        assert not any(f.id.startswith("loom/bounce-") for f in loom.facts())
        inject_letter(root, to="thread:work", body="later")
        wait_until(lambda: len(replies) == 3 and not mail(root, loom.facts(), "work"), 12, loom.dump)
        assert replies[2]["body"] == "later" and replies[2]["pid"] != pid
        starts = [f for f in loom.facts() if f.kind == "body.started"]
        assert [f.data["pid"] for f in starts] == [pid, replies[2]["pid"]]
    finally:
        loom.halt()
    assert not loom.errors


import pytest


@pytest.mark.parametrize("reset", [7 * 86400, 60])
def test_owing_worker_is_told_once_holds_twice_then_low_fuel_preserves_and_parks_the_ask(tmp_path, reset):
    """A refill past the stop hook's life ends the body and the loom parks the
    ask; a refill within it is slept through, in the hook, by the same body."""
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "opener", "Opener", "answer-fast", wait="0")
    write_thread(root, "work", "---\nid: work\nstatus: open\ntense: plan\n"
                 "opened-by: thread:opener\n---\n# Worker\n", "work", wait="0.8")
    (root / "self/threads/work/seat").write_text("body: gauge/quiet\n")
    git(root / "self", "add", "threads")
    git(root / "self", "commit", "-m", "Choose the gauge body.")
    folder = root / "loom/bodies/gauge"
    folder.mkdir(parents=True)
    (folder / "jack").write_text("hooks\n")
    blocked = root / "blocks"
    waiting = root / "holding"
    run = folder / "run"
    run.write_text(f'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
ctx.owed()
# Wait for the shown receipt to reach the boundary; no answer is made.
time.sleep(0.3)
assert ctx.jack("stop").kind == "block"
ctx.handoff({str(blocked)!r}, "one\\n")
ctx.handoff({str(waiting)!r}, str(os.getpid()))
holds = 0
while True:
    view = ctx.jack("stop")
    if view.raw == "":
        break
    assert json.loads(view.raw)["reason"].startswith("cache keepalive, nothing new arrived: stop")
    holds += 1
    # Replace, never truncate: the test polls this file and must not read ''.
    ctx.handoff({str(root / "holds.tmp")!r}, str(holds))
    os.replace({str(root / "holds.tmp")!r}, {str(root / "holds")!r})
''')
    run.chmod(0o755)
    usage = folder / "usage"
    usage.write_text('''#!/usr/bin/env python3
import json, time
from datetime import datetime, timezone
from pathlib import Path
left = 2 if Path(__file__).with_name('low').exists() else 100
print(json.dumps({'windows': [{'name': 'session', 'left_pct': left,
    'resets_at': datetime.fromtimestamp(time.time() + RESET, timezone.utc).isoformat()}]}))
'''.replace("RESET", str(reset)))
    usage.chmod(0o755)
    letter = inject_letter(root, to="thread:work", body="still waiting on my child")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(waiting.exists, 12, loom.dump)
        pid = int(waiting.read_text())
        # Two actual wait expiries, beyond the hook subprocess's launch cost.
        from time import monotonic
        begun = monotonic()
        # Count the real expiries; hook launch/collector time is additional.
        wait_until(lambda: monotonic() - begun > 2.2 and (root / "holds").exists()
                   and int((root / "holds").read_text()) >= 2, 6, loom.dump)
        os.kill(pid, 0)
        assert blocked.read_text() == "one\n"
        assert int((root / "holds").read_text()) >= 2
        assert len([f for f in loom.facts() if f.kind == "body.started"]) == 1
        assert not any(f.kind == "body.exited" for f in loom.facts())
        (folder / "low").touch()
        # Advance the gauge's collection cycle: holds reuse cached fuel for
        # five minutes and probes share a one-minute attempt floor.
        from time import time
        from brr.loom.runtime.home import atomic_write
        gauge = root / "loom/usage/gauge.json"
        cached = json.loads(gauge.read_text())
        cached["at"] = time() - 301
        atomic_write(gauge, json.dumps(cached))
        gauge.with_suffix(".lock").write_text(str(time() - 301))
        if reset < 3600:
            # Parked in the hook: no exit, no turn, while the window is dry.
            wait_until(lambda: json.loads(gauge.read_text())["windows"][0]["left_pct"] == 2, 10, loom.dump)
            parked = int((root / "holds").read_text())
            import time as clock
            clock.sleep(2.5)
            os.kill(pid, 0)
            assert int((root / "holds").read_text()) == parked
            assert not any(f.kind in {"body.exited", "body.parked"} for f in loom.facts())
            # The refill: the same body holds on, still owing the ask. Its cache
            # went cold while parked, so no keepalive is paid until the ceiling.
            (folder / "low").unlink()
            cached = json.loads(gauge.read_text())
            cached["at"] = time() - 301
            atomic_write(gauge, json.dumps(cached))
            gauge.with_suffix(".lock").write_text(str(time() - 301))
            wait_until(lambda: json.loads(gauge.read_text())["windows"][0]["left_pct"] == 100, 10, loom.dump)
            clock.sleep(2.5)
            os.kill(pid, 0)
            assert int((root / "holds").read_text()) == parked
            assert len([f for f in loom.facts() if f.kind == "body.started"]) == 1
            assert [l.data["id"] for l in mail(root, loom.facts(), "work")] == [letter]
            return
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
        facts = loom.facts()
        assert any(f.kind == "body.exited" and f.data["code"] == 0 for f in facts)
        assert [f.data["cause"] for f in facts if f.kind == "attempt.ended"] == ["paced"]
        assert [l.data["id"] for l in mail(root, facts, "work")] == [letter]
        assert not any(f.id.startswith("loom/bounce-") or f.id.startswith("attention:wasted:") for f in facts)
        assert len([f for f in facts if f.kind == "body.started"]) == 1
    finally:
        loom.halt()
    assert not loom.errors
