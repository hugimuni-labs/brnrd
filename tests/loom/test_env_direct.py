"""The runtime callers expose repo choices and refuse unexplained long idle."""
import json
import time
from datetime import datetime, timedelta, timezone

from brr.loom.runtime.__main__ import main as cli
from brr.loom.runtime.adapters import repos
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.selfrepo import git
from test_library import _add
from test_wishes import command, heard, ledger, view  # noqa: F401


def test_new_thread_requires_a_repo_choice(ledger, capsys):
    home, seat = ledger
    for name, purpose in (("product", "the product"), ("animations", "visual work")):
        assert _add(home.root, name, f"https://github.com/ada/{name}", "--kind", "known",
                    "--whose", "Ada", "--for", purpose) == 0
    args = ["send", "--room", str(seat), "--to", "thread:research",
            "--wish", "new:Investigate", "Investigate"]
    before = list(read_facts(home))
    assert cli(args) == 1
    error = capsys.readouterr().err
    assert "product; for: the product" in error
    assert "animations; for: visual work" in error
    assert "--repo none" in error and "e.g." in error
    assert list(read_facts(home)) == before
    assert not home.routable("research")
    assert cli(args + ["--repo", "none"]) == 0
    assert home.routable("research")
    seat_text = git(home.root / "self", "show", "main:threads/research/seat").stdout
    assert repos(seat_text, home) == []


def test_long_grant_park_requires_a_reason(ledger, monkeypatch):
    home, seat = ledger
    monkeypatch.setenv("BRNRD_BODY_SHELL", "fake")
    folder = home.root / "loom/bodies/fake"
    folder.mkdir(parents=True)
    (folder / "run").write_text("#!/bin/sh\nexit 0\n")
    (folder / "usage").write_text("#!/bin/sh\nexit 0\n")
    (folder / "usage").chmod(0o755)
    gauge = home.root / "loom/usage/fake.json"
    gauge.parent.mkdir(parents=True)
    gauge.write_text(json.dumps({"at": time.time(), "windows": [{"name": "week", "left_pct": 36,
        "resets_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}]}))
    ident = heard(home, seat, "Iterate to the deadline")
    command(home, seat, "wish", ident, "--result", "url:https://example.com/render",
            "--line", "First render")
    assert view(home, ident).state == "granting"
    before = view(home, ident).reads
    error = command(home, seat, "wish", ident, "--read", "waiting: his review",
                    "--wait", "at:+13h", ok=False)
    assert "13.0 hours" in error and "granting" in error and "week 36%" in error
    assert 'idle: <why>' in error and "Shorten" in error
    assert view(home, ident).reads == before
    command(home, seat, "wish", ident, "--read", "idle: ", "--wait", "at:+13h", ok=False)
    command(home, seat, "wish", ident, "--read", "waiting: a short break", "--wait", "at:+3h")
    command(home, seat, "wish", ident, "--read", "idle: he is asleep and the piece is done",
            "--wait", "at:+13h")
    assert view(home, ident).reads[-1]["wait"]["on"].startswith("at:")
    from brr.loom.runtime.config import load_config
    payload = json.loads(gauge.read_text())
    payload["windows"][0]["left_pct"] = load_config(home.root).hold_floor_pct
    gauge.write_text(json.dumps(payload))
    command(home, seat, "wish", ident, "--read", "waiting: fuel floor", "--wait", "at:+13h")
    payload["windows"] = []
    gauge.write_text(json.dumps(payload))
    command(home, seat, "wish", ident, "--read", "waiting: fuel unknown", "--wait", "at:+13h")
    payload["windows"] = [{"name": "week", "left_pct": 36,
                           "resets_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}]
    gauge.write_text(json.dumps(payload))
    command(home, seat, "wish", ident, "--result", "url:https://example.com/render",
            "--line", "Finished", "--deliver")
    assert view(home, ident).state == "delivered"
    command(home, seat, "wish", ident, "--read", "waiting: his review", "--wait", "at:+13h")


def test_one_source_keeps_the_existing_default(ledger):
    home, seat = ledger
    assert _add(home.root, "product", "https://github.com/ada/product", "--kind", "known",
                "--whose", "Ada", "--for", "the product") == 0
    command(home, seat, "send", "--to", "thread:one-source", "--wish", "new:Investigate", "Investigate")
    assert home.routable("one-source")
