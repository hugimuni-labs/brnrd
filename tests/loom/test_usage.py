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
    from brr.loom.runtime import speak
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
    monkeypatch.setitem(speak.EFFECTS, "fake", lambda _h, _c, _k, text, _ctx: received.append(text))
    inject_letter(root, to="thread:gauge", body="Show your fuel.")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: received, 12, loom.dump)
        assert expected in received[0]
    finally:
        loom.halt()
    assert not loom.errors
