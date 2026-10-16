"""Failed gauges do not impose a collector timeout on every start."""

import json
import sys
from pathlib import Path

import pytest

from brr.loom.runtime import usage
from brr.loom.runtime.home import Home


def test_failed_gauge_is_cached_across_exits_until_its_ttl(tmp_path, monkeypatch):
    home = Home(tmp_path)
    path = tmp_path / "loom/usage/claude.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"at": 100, "windows": []}))
    monkeypatch.setattr(usage.time, "time", lambda: 200)
    calls = []

    class Refresh:
        def __init__(self, target, args, daemon):
            self.args = args

        def start(self):
            calls.append(self.args)
            usage._REFRESHING.discard(self.args[0])

    monkeypatch.setattr(usage.threading, "Thread", Refresh)
    for exit_at in (110, 150, 199):
        assert usage.reading(home, "claude", after=exit_at) == {"at": 100, "windows": []}
    assert not calls
    monkeypatch.setattr(usage.time, "time", lambda: 401)
    assert usage.reading(home, "claude", after=199) is None
    assert len(calls) == 1


def test_known_fuel_still_refreshes_after_an_exit(tmp_path, monkeypatch):
    home = Home(tmp_path)
    path = tmp_path / "loom/usage/claude.json"
    path.parent.mkdir(parents=True)
    value = {"at": 100, "windows": [{"name": "session", "left_pct": 90}]}
    path.write_text(json.dumps(value))
    monkeypatch.setattr(usage.time, "time", lambda: 200)
    assert usage.reading(home, "claude", after=99) == value
    # Keep the asynchronous refresh pending so this test opens no real shell.
    usage._REFRESHING.add(path)
    try:
        assert usage.reading(home, "claude", after=110) is None
    finally:
        usage._REFRESHING.discard(path)


@pytest.mark.parametrize(("shell", "reading_file", "expected"), [
    ("codex", "codex_rate_limits_2026_10_12.json", "fuel codex session 33% week 58%"),
    ("grok", "grok_usage_screen_2026_10_12.txt", "fuel grok week 100%"),
])
def test_recorded_shell_reading_reaches_a_body_boundary(tmp_path, monkeypatch, shell, reading_file, expected):
    """Actual body-folder readers parse real captures on the live loom path."""
    from connector_support import install_fake
    from brr.loom.runtime.ledger import inject_letter
    from brr.loom.runtime.selfrepo import git
    from _step import Loom, wait_until, write_thread

    root = tmp_path / "home"
    write_thread(root, "gauge", "Gauge proof", "", wait="0")
    (root / "self/threads/gauge/seat").write_text(f"body: {shell}/proof\n")
    git(root / "self", "add", "threads")
    git(root / "self", "commit", "-m", "Choose the recorded gauge body.")
    folder = root / "loom/bodies" / shell
    folder.mkdir(parents=True)
    bundled = Path(usage.__file__).parent / "bodies" / shell / "usage"
    fixture = Path(__file__).parents[1] / "fixtures" / reading_file
    # Substitute only the account I/O. The bundled script, parser, async
    # collection, start gate, jack and rendered boundary all run unchanged.
    probe = (
        "from brr import codex_usage as reader\n"
        f"reader.probe_rate_limits = lambda **kw: reader.parse_rate_limits(json.loads(Path({str(fixture)!r}).read_text()))\n"
        if shell == "codex" else
        "from brr import grok_usage as reader\n"
        f"reader.capture_levels = lambda **kw: reader.parse_usage_text(Path({str(fixture)!r}).read_bytes())\n"
    )
    script = folder / "usage"
    script.write_text(f"#!{sys.executable}\nimport json, runpy\nfrom pathlib import Path\n" + probe
                      + f"runpy.run_path({str(bundled)!r}, run_name='__main__')\n")
    script.chmod(0o755)
    (folder / "jack").write_text("hand\n")
    run = folder / "run"
    run.write_text(f'''#!{sys.executable}
import sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
view = ctx.jack("start")
for letter in view.letters:
    ctx.send("channel:fake", view.raw, re=letter.id)
''')
    run.chmod(0o755)
    received = []
    install_fake(monkeypatch, lambda text: received.append(text))
    inject_letter(root, to="thread:gauge", body="Show your fuel.")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: received, 12, loom.dump)
        assert expected in received[0]
    finally:
        loom.halt()
    assert not loom.errors


def test_repeated_timed_out_probes_kill_the_whole_group(tmp_path, monkeypatch):
    import os
    import subprocess
    import time
    import threading

    home = Home(tmp_path)
    folder = tmp_path / 'loom/bodies/stuck'
    folder.mkdir(parents=True)
    (folder / 'run').touch()
    script = folder / 'usage'
    script.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(60)\n')
    script.chmod(0o755)
    real_communicate = subprocess.Popen.communicate
    real_popen = subprocess.Popen
    processes = []
    def launch(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(subprocess, 'Popen', launch)
    timeouts = []
    def fast_timeout(proc, *args, **kwargs):
        timeouts.append(kwargs['timeout'])
        return real_communicate(proc, timeout=.1)
    monkeypatch.setattr(real_popen, 'communicate', fast_timeout)
    path = (tmp_path / 'loom/usage/stuck.json').resolve()
    for _ in range(3):
        assert usage.reading(home, 'stuck') is None
        deadline = time.monotonic() + 3
        while path in usage._REFRESHING and time.monotonic() < deadline:
            threading.Event().wait(.01)
        assert path not in usage._REFRESHING
        assert json.loads(path.read_text())['windows'] == []
        pid = processes[-1].pid
        assert processes[-1].returncode == -9
        with pytest.raises(ProcessLookupError):
            os.killpg(pid, 0)
        path.unlink()  # next clock cycle has an expired cache
        path.with_suffix(".lock").write_text("0")
    assert timeouts == [15, 15, 15]


def test_attention_prints_each_shell_with_fuel(tmp_path, capsys):
    import time
    from brr.loom.runtime.verbs_loom import _attention
    home = Home(tmp_path)
    for shell in usage.shells(home):
        path = tmp_path / 'loom/usage' / f'{shell}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'at': time.time(), 'windows': [
            {'name': 'week', 'left_pct': 42}]}))
    assert _attention(['--home', str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f'fuel {shell} week 42%' for shell in usage.shells(home)]


def test_a_reading_from_before_its_window_reset_is_not_fuel(tmp_path, monkeypatch):
    # 2026-10-16: at 20:50 the session window reset, the gauge still held the
    # 2% read at 20:48, and the loom parked the body for another 15 minutes.
    from datetime import datetime, timezone
    home = Home(tmp_path)
    path = tmp_path / "loom/usage/claude.json"
    path.parent.mkdir(parents=True)
    reset = datetime.fromtimestamp(150, timezone.utc).isoformat()
    path.write_text(json.dumps({"at": 100, "windows": [
        {"name": "session", "left_pct": 2, "resets_at": reset}]}))
    usage._REFRESHING.add(path)
    try:
        monkeypatch.setattr(usage.time, "time", lambda: 140)
        assert usage.reading(home, "claude")["windows"][0]["left_pct"] == 2
        monkeypatch.setattr(usage.time, "time", lambda: 160)
        assert usage.reading(home, "claude") is None
        assert usage.dry_until(home, "claude") is None
    finally:
        usage._REFRESHING.discard(path)
