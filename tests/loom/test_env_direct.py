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
    gauge.write_text(json.dumps({"at": time.time(), "windows": [{"name": "week", "left_pct": 64,
        "resets_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}]}))
    ident = heard(home, seat, "Iterate to the deadline")
    command(home, seat, "wish", ident, "--result", "url:https://example.com/render",
            "--line", "First render")
    assert view(home, ident).state == "granting"
    # Without a recorded deadline even an overnight park is accepted.
    from test_wake import cli as process_cli
    receipt = process_cli(["wish", "--room", str(seat), ident, "--read", "waiting: his review",
                           "--wait", "at:+13h"])
    assert receipt.returncode == 0 and "this wait idles 13.0 h" in receipt.stderr
    from brr.loom.runtime.loom import park_reminders
    reminder = park_reminders(read_facts(home), view(home, ident).thread)
    from types import SimpleNamespace
    from brr.loom.runtime.loom import _render_boundary
    boundary = _render_boundary(home, read_facts(home), "s-aaaa-test", 1,
                                SimpleNamespace(thread="work", fuel="", why="", shell="fake"))
    assert reminder in boundary
    assert "this wait idles 13.0 h" in reminder and "no recorded grant deadline" in reminder
    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    command(home, seat, "wish", ident, "--deadline", deadline)
    # A long daytime wait is only a reminder, even ahead of pace with a deadline.
    command(home, seat, "wish", ident, "--read", "waiting: daytime", "--wait", "at:+6h")
    assert deadline in view(home, ident).reads[-1]["wait"]["reminder"]
    before = view(home, ident).reads
    error = command(home, seat, "wish", ident, "--read", "waiting: his review",
                    "--wait", "at:+13h", ok=False)
    assert "13.0 h" in error and "fuel in hand" in error and deadline in error
    assert 'idle: <why>' in error and "or shorten" in error
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
    # Behind pace the overnight wait is accepted even with a deadline.
    payload["windows"][0]["left_pct"] = 20
    gauge.write_text(json.dumps(payload))
    command(home, seat, "wish", ident, "--read", "waiting: behind pace", "--wait", "at:+13h")
    payload["windows"] = []
    gauge.write_text(json.dumps(payload))
    command(home, seat, "wish", ident, "--read", "waiting: fuel unknown", "--wait", "at:+13h")
    payload["windows"] = [{"name": "week", "left_pct": 64,
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


def test_boundary_fuel_shows_week_pace(ledger, monkeypatch):
    from brr.loom.runtime import usage
    from brr.loom.runtime.port import render_boundary
    home, _ = ledger
    now = time.time()
    monkeypatch.setattr(usage.time, "time", lambda: now)
    folder = home.root / "loom/usage"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "fake.json").write_text(json.dumps({"at": now, "windows": [
        {"name": "week", "left_pct": 64,
         "resets_at": datetime.fromtimestamp(now + 5 * 86400, timezone.utc).isoformat()}]}))
    text = render_boundary("s-test", 1, "test", [], set(), {}, usage.fuels(home, "fake"))
    assert "week 64% left · 5.0d to reset · pace 71% · 0.5d behind" in text
