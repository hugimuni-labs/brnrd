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


def test_run_refreshes_every_usage_script_even_without_a_seated_body(tmp_path, monkeypatch):
    """Drive the periodic caller, including shells used outside the loom."""
    import threading
    from brr.loom.runtime import loom
    from _step import write_thread

    root = tmp_path / "home"
    write_thread(root, "sweep", "Sweep", "", wait="0")
    home = Home(root)
    expected = set(usage.shells(home))
    assert {"claude", "codex", "grok", "vibe"} <= expected
    # No bodies need these shells. A custom shell with a script must be swept too.
    from brr.loom.runtime.adapters import resolve
    folder = root / "loom/bodies/extra"
    folder.mkdir(parents=True)
    (folder / "run").write_text("#!/bin/sh\nexit 0\n")
    (folder / "usage").write_text("#!/bin/sh\nexit 1\n")
    assert resolve(home, "extra") == folder
    expected.add("extra")
    seen = []
    stop = threading.Event()
    monkeypatch.setattr(usage, "reading", lambda home, shell: seen.append(shell))
    monkeypatch.setattr(loom, "tick_once", lambda *args: stop.set())
    loom.run(root, tick=.01, stop=stop)
    assert set(seen) == expected
    assert len(seen) == len(expected)


def test_grok_expired_error_survives_actual_collector_and_attempt_floor(tmp_path, monkeypatch):
    """The real usage script cannot start a PATH grok, even with expired auth."""
    import os
    from brr import grok_usage
    root = tmp_path / "home"
    home = Home(root)
    auth_home = tmp_path / "grok-home"
    auth_home.mkdir()
    auth = auth_home / "auth.json"
    auth.write_text(json.dumps({grok_usage.AUTH_SCOPE: {
        "key": "expired-test-key", "expires_at": "2000-01-01T00:00:00Z",
        "refresh_token": "must-not-refresh",
    }}))
    before = auth.read_bytes()
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    marker = tmp_path / "spawned-grok"
    binary = binary_dir / "grok"
    binary.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
    binary.chmod(0o755)
    monkeypatch.setenv("GROK_HOME", str(auth_home))
    monkeypatch.delenv("GROK_AUTH_PATH", raising=False)
    monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ["PATH"])
    path = root / "loom/usage/grok.json"
    script = Path(usage.__file__).parent / "bodies/grok/usage"
    usage._refresh(path, script)
    result = usage.reading(home, "grok")
    assert result["windows"] == [] and "expired" in result["error"]
    assert not marker.exists() and auth.read_bytes() == before
    cached = path.read_bytes()
    usage._refresh(path, script)
    assert path.read_bytes() == cached  # failed attempt still owns the 60s floor


def test_boundary_lists_every_cached_shell_and_park_stays_seated(tmp_path, monkeypatch):
    """The status line is every usage file. Hold and park still read the seat."""
    import time
    from datetime import datetime, timezone

    from brr.daemon2.facts import Fact
    from brr.loom.runtime import loom
    from brr.loom.runtime.bodies import decide
    from _step import write_thread

    root = tmp_path / "home"
    write_thread(root, "work", "All fuels", "")
    config = root / "loom/config.toml"
    config.write_text(config.read_text() + "hold_floor_pct = 3\n")
    now = time.time()
    future = datetime.fromtimestamp(now + 7 * 86400, timezone.utc).isoformat()
    passed = datetime.fromtimestamp(now - 30, timezone.utc).isoformat()

    def write(shell, at, windows):
        path = root / "loom/usage" / f"{shell}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": at, "windows": windows}))

    write("grok", now, [{"name": "week", "left_pct": 45, "resets_at": future}])
    write("claude", now, [
        {"name": "session", "left_pct": 72, "resets_at": future},
        {"name": "week", "left_pct": 11, "resets_at": future},
    ])
    write("codex", now - 90, [
        {"name": "session", "left_pct": 100, "resets_at": passed},
        {"name": "week", "left_pct": 100, "resets_at": passed},
    ])
    write("blank", now, [])
    home = Home(root)
    strand = "s-ab12-aaaaaa"

    def fact(kind, **data):
        return Fact(by="loom:aaaa", kind=kind, data={"strand": strand, "gen": 1, **data})

    lease = fact("lease", shell="grok", core="echo", thread="work")
    started = fact("body.started", pid=1, shell="grok")
    monkeypatch.setattr(loom, "body_alive", lambda room: True)
    loom._advance_body(home, strand, "work", 1, {}, [lease, started])
    status = next(line for line in (home.room(strand) / "port/in/boundary.md").read_text().splitlines()
                  if line.startswith("status:"))
    assert status.endswith(
        "fuel grok week 45% · claude session 72% week 11% · codex session 100% week 100%?")
    assert "vibe" not in status and "blank" not in status
    # threads.py strips this prefix. It must stay one shell.
    assert usage.fuel(home, "grok") == "fuel grok week 45%"
    assert usage.fuel(home, "grok").removeprefix("fuel grok ") == "week 45%"

    write("claude", now, [
        {"name": "session", "left_pct": 0, "resets_at": future},
        {"name": "week", "left_pct": 11, "resets_at": future},
    ])
    monkeypatch.setattr(loom, "body_alive", lambda room: False)
    wanted = [lease]
    observed, context = loom._observe_body(home, wanted, strand, "work", 1, {})
    assert observed.gauge == "ok" and context.empty is None
    assert "park" not in [command.name for command in decide(wanted, [observed])]
    assert usage.dry_until(home, "grok") is None
    assert usage.dry_until(home, "claude") is not None
    assert usage.fuel(home, "claude") == "fuel claude session 0% week 11%"

    write("grok", now, [{"name": "week", "left_pct": 1, "resets_at": future}])
    parked = loom._advance_body(home, strand, "work", 1, {}, wanted)
    park = next(item for item in parked if item.kind == "body.parked")
    assert park.data["shell"] == "grok" and park.data["window"] == "week"
    assert park.data["left_pct"] == 1
    assert usage.dry_until(home, "grok") is not None


def test_vibe_collector_reports_absence_without_inventing_fuel(tmp_path):
    home = Home(tmp_path)
    path = tmp_path / "loom/usage/vibe.json"
    usage._refresh(path, Path(usage.__file__).parent / "bodies/vibe/usage")
    result = usage.reading(home, "vibe")
    assert result["windows"] == []
    assert "token totals only" in result["error"]
