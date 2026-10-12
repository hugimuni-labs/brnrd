"""Failed gauges do not impose a collector timeout on every start."""

import json

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
