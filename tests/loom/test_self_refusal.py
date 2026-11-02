"""Home-caused refusals through the live tick and attempt-end path."""
import pytest

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.owners import letter_owners
from brr.loom.runtime.selfrepo import git
from _step import Loom, wait_until, write_thread, kill_bodies


@pytest.mark.parametrize("fault", ["plan", "identity", "stance"])
def test_self_refusal_stays_owed_and_one_notice_repairs_home(tmp_path, fault):
    root = tmp_path / "home"
    write_thread(root, "work", "The ask", "answer-fast", wait="0")
    clone = root / "self"
    recipe = clone / "core/loom/wake"
    if fault == "plan":
        # A home owns its recipe, including the pre-clipping recipe from 4401.
        text = recipe.read_text()
        assert "    if excess:\n" in text
        recipe.write_text(text.replace("    if excess:\n", "    if False:\n", 1))
        path = clone / "threads/work/README.md"
        path.write_text(path.read_text() + "\n## Plan\n- [ ] " + "x" * 30000 + "\n")
    else:
        path = clone / ("core/identity.md" if fault == "identity" else "memory/stance.md")
        path.write_text("x" * 30000)
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "Home state that cannot wake")
    ident = inject_letter(root, to="thread:work", body="Please answer")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: len([f for f in loom.facts() if f.kind == "attempt.ended"
                               and f.data["thread"] == "work"]) >= 1, 15, loom.dump)
        import time
        time.sleep(0.5)
        facts = loom.facts()
        assert len([f for f in facts if f.kind == "attempt.ended"
                    and f.data["thread"] == "work"]) == 1
        fate = letter_owners(facts, main_snapshot(Home(root)))[ident]
        assert not fate.done and fate.owner == "loom"
        ends = [f for f in facts if f.kind == "attempt.ended" and f.data["thread"] == "work"]
        assert all(d["outcome"] == "retry" and d["reason"] == "home-caused failure"
                   for f in ends for d in f.data["dispositions"] if d["id"] == ident)
        notices = [f for f in facts if f.kind == "letter" and f.id.startswith("loom/self-refusal-")]
        assert len(notices) == 1
        assert notices[0].data["to"] == "thread:inbox"
        assert not any(f.kind == "letter" and f.id.startswith("loom/failed-message-") for f in facts)
        assert not any(f.kind == "letter" and str(f.data.get("to", "")).startswith("channel:") for f in facts)
        # Repair main. The original letter, rather than a resend, now runs.
        if fault == "plan":
            path.write_text(path.read_text().split("\n## Plan")[0])
        else:
            path.write_text("The resident.\n")
        git(clone, "add", "-A")
        git(clone, "commit", "-m", "Repair the home")
        wait_until(lambda: letter_owners(loom.facts(), main_snapshot(loom.home))[ident].done,
                   15, loom.dump)
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_intrinsically_oversized_letter_keeps_input_failure_path(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "work", "The ask", "answer-fast", wait="0")
    ident = inject_letter(root, to="thread:work", body="x" * 30000)
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: letter_owners(loom.facts(), main_snapshot(loom.home))[ident].owner == "operator",
                   15, loom.dump)
        ends = [f for f in loom.facts() if f.kind == "attempt.ended" and f.data["thread"] == "work"]
        assert len(ends) == 2
        assert ends[0].data["dispositions"][0]["reason"] == "first failed attempt"
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_quota_failure_refreshes_gauge_keeps_ask_and_parks(tmp_path):
    import json
    import sys
    import time
    from datetime import datetime, timezone
    from brr.loom.runtime import usage
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Quota proof', '', wait='0')
    seat = root / 'self/threads/work/seat'
    seat.write_text('body: quota/proof\n')
    git(root / 'self', 'add', 'threads')
    git(root / 'self', 'commit', '-m', 'Seat the quota shell')
    folder = root / 'loom/bodies/quota'
    folder.mkdir(parents=True)
    marker = root / 'spent'
    reset = datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat()
    for name, body in {
        'run': f'import sys\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nsys.exit(1)\n',
        'usage': f'import json\nfrom pathlib import Path\nprint(json.dumps({{"windows": [{{"name": "session", "left_pct": 0 if Path({str(marker)!r}).exists() else 4, "resets_at": {reset!r}}}]}}))\n',
    }.items():
        path = folder / name
        path.write_text(f'#!{sys.executable}\n' + body)
        path.chmod(0o755)
    (folder / 'jack').write_text('hand\n')
    # A recent successful collector and its throttle must both be bypassed.
    cache = root / 'loom/usage/quota.json'
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({'at': time.time(), 'windows': [
        {'name': 'session', 'left_pct': 4, 'resets_at': reset}]}))
    cache.with_suffix('.lock').write_text(str(time.time()))
    ident = inject_letter(root, to='thread:work', body='Please answer')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == 'body.parked' for f in loom.facts()), 15, loom.dump)
        facts = loom.facts()
        assert not letter_owners(facts, main_snapshot(loom.home))[ident].done
        ended = next(f for f in facts if f.kind == 'attempt.ended')
        assert ended.data['cause'] == 'paced'
        assert ended.data['dispositions'] == [{'id': ident, 'outcome': 'retry', 'reason': 'paced'}]
        parked = next(f for f in facts if f.kind == 'body.parked')
        assert parked.data['until'] == usage.dry_until(loom.home, 'quota')
        assert json.loads(cache.read_text())['windows'][0]['left_pct'] == 0
        assert not any(f.id.startswith(('loom/bounce-', 'loom/failed-message-')) for f in facts)
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_disappeared_install_does_not_spend_a_letters_crash_allowance(tmp_path):
    from types import SimpleNamespace
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.ledger import append, read_facts
    from brr.loom.runtime.router import Router
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Recover another install', '', wait='0')
    home = Home(root)
    ident = inject_letter(root, to='thread:work', body='Please answer')
    strand = 's-dead-remote'
    for kind, data in [
        ('lease', dict(thread='work', strand=strand, gen=1, install='dead')),
        ('shown', dict(strand=strand, gen=1, ids=[ident])),
    ]:
        append(home, Fact(kind=kind, by='loom:dead' if kind == 'lease' else 'strand:' + strand,
                          id=kind + ':remote', data=data, at='2026-01-01T00:00:00+00:00', hlc=(1, 0, 'dead')))
    router = Router(home)
    router.lease = SimpleNamespace(gen=1)
    facts = router._release_abandoned(read_facts(home), 'work', lambda room: False)
    fate = letter_owners(facts, main_snapshot(home))[ident]
    assert not fate.done and fate.reason == 'home-caused failure'
    notices = [f for f in facts if f.id.startswith('loom/self-refusal-')]
    assert len(notices) == 1 and notices[0].data['to'] == 'thread:work'
    assert not any(f.id.startswith(('loom/bounce-', 'loom/failed-message-')) for f in facts)


def test_home_retry_fallback_survives_router_restart(tmp_path):
    from datetime import datetime, timedelta, timezone
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.router import Router
    root = tmp_path / 'home'
    write_thread(root, 'work', 'The ask', 'answer-fast', wait='0')
    home = Home(root)
    revision = git(root / 'self', 'rev-parse', 'main').stdout.strip()
    now = datetime.now(timezone.utc)
    def attempt(age):
        return Fact(kind='attempt.ended', id='end', by='loom:test',
                    at=(now - timedelta(seconds=age)).isoformat(), data={
                        'thread': 'work', 'evidence': {'self_revision': revision},
                        'dispositions': [{'id': 'ask', 'outcome': 'retry',
                                          'reason': 'home-caused failure'}]})
    assert Router(home)._home_retry_waiting([attempt(599)], 'work')
    assert not Router(home)._home_retry_waiting([attempt(601)], 'work')
