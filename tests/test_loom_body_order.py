"""The harness priority list: one file ranks every bundled body, the person reorders it, the loom falls down it."""
import io
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from brr.loom.runtime import adapters, setup_channel
from brr.loom.runtime.home import Home


def test_every_bundled_body_is_ranked_in_the_order_file():
    """9987: a harness added as a body folder but missing from bodies/order fails here, not in setup."""
    bodies = Path(adapters.__file__).parent / "bodies"
    registry = {p.name for p in bodies.iterdir() if p.is_dir() and (p / "run").is_file()} - {"fake"}
    order = adapters.default_order()
    assert len(order) == len(set(order)), "a harness is ranked twice"
    assert set(order) == registry
    assert order[:2] == ["claude", "codex"]


def home_at(tmp_path):
    (tmp_path / "loom").mkdir()
    return Home(tmp_path, "aaaa")


def test_setup_lists_found_harnesses_best_first_and_a_typed_order_is_kept(tmp_path, monkeypatch, capsys):
    home = home_at(tmp_path)
    monkeypatch.setattr(setup_channel.shutil, "which", lambda name: None if name == "grok" else f"/bin/{name}")
    monkeypatch.setattr("brr.loom.runtime.service._windows", lambda home, shell: [{"name": "session", "left_pct": 90}])
    # vibe first, then claude; codex left out is off.
    monkeypatch.setattr("sys.stdin", io.StringIO("3 1\n"))
    setup_channel._choose_body(home)
    out = capsys.readouterr().out
    assert "1. claude" in out and "2. codex" in out and "3. vibe" in out and "grok: " in out
    assert adapters.default_body(home) == "vibe"
    assert adapters.body_order(home) == ["vibe", "claude", "grok"]  # grok not here yet: ranked after, not off
    assert "then claude when vibe is out of fuel or missing" in out
    # Run again: the person's order is the list, codex shown off; Enter keeps all of it.
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    setup_channel._choose_body(home)
    out = capsys.readouterr().out
    assert out.index("1. vibe") < out.index("2. claude") < out.index("3. codex") and "(off)" in out
    assert adapters.body_order(home) == ["vibe", "claude", "grok"] and adapters.default_body(home) == "vibe"


def test_a_preferred_body_that_cannot_run_falls_to_the_next_on_the_list(tmp_path):
    home = home_at(tmp_path)
    adapters.set_order(home, ["codex", "claude", "vibe"])
    can = {"codex": False, "claude": False, "vibe": True}
    assert adapters.fall_down(home, ("codex", "gpt-5"), can.get) == ("vibe", "")
    assert adapters.fall_down(home, ("claude", "opus"), {"claude": True}.get) == ("claude", "opus")
    # Nothing on the list can run: the choice stays, and parks on its fuel as before.
    assert adapters.fall_down(home, ("codex", ""), lambda shell: False) == ("codex", "")
    # A shell the list does not rank (a test's fake, a home's own folder) never falls.
    assert adapters.fall_down(home, ("fake", ""), lambda shell: shell == "vibe") == ("fake", "")
    # grok is off for this person: it is never fallen to.
    assert adapters.fall_down(home, ("codex", ""), {"grok": True}.get) == ("codex", "")


def test_bodies_order_is_checked_and_written_beside_the_model_keys(tmp_path):
    from brr.loom.runtime.config import ConfigError
    home = home_at(tmp_path)
    adapters.prefer(home, "default", "claude/opus")
    adapters.set_order(home, ["claude", "codex"])
    adapters.set_order(home, ["codex"])
    text = (tmp_path / "loom/preferences.toml").read_text()
    assert text == 'model.default = "claude/opus"\nbodies.order = ["codex"]\n'
    (tmp_path / "loom/preferences.toml").write_text('bodies.order = "claude"\n')
    with pytest.raises(ConfigError, match="bodies.order is a list"):
        adapters.body_order(home)


def _which(names):
    return lambda name: f"/bin/{name}" if name in names else None


def _gauge(root, shell, left, *, error=None, windows=True, age=0, reset_in=86400):
    import json
    path = root / "loom" / "usage" / f"{shell}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    reset = datetime.fromtimestamp(now + reset_in, timezone.utc).isoformat()
    payload = {"at": now - age, "windows": [
        {"name": "week", "left_pct": left, "resets_at": reset},
    ] if windows else []}
    if error is not None:
        payload["error"] = error
    path.write_text(json.dumps(payload))


def test_spend_keys_are_read_and_a_keep_inside_the_burn_list_is_refused(tmp_path, monkeypatch):
    from brr.loom.runtime.config import ConfigError
    home = home_at(tmp_path)
    monkeypatch.setattr("shutil.which", _which({"grok", "codex", "claude"}))
    (tmp_path / "loom/preferences.toml").write_text(
        'model.default = "claude/opus"\nspend.burn = ["grok", "codex"]\nspend.keep = "claude"\n'
    )
    assert adapters.default_body(home) == "claude/opus"
    (tmp_path / "loom/preferences.toml").write_text('spend.burn = ["grok", "claude"]\nspend.keep = "claude"\n')
    with pytest.raises(ConfigError, match="is also in spend.burn"):
        adapters.body_order(home)
    (tmp_path / "loom/preferences.toml").write_text('spend.burn = "grok"\n')
    with pytest.raises(ConfigError, match="spend.burn is a list"):
        adapters.body_order(home)
    (tmp_path / "loom/preferences.toml").write_text('spend.keep = ["claude"]\n')
    with pytest.raises(ConfigError, match="spend.keep is one shell"):
        adapters.body_order(home)
    (tmp_path / "loom/preferences.toml").write_text('nope = "x"\n')
    with pytest.raises(ConfigError, match="spend.burn"):
        adapters.body_order(home)


def test_spend_picks_in_engine_order_using_the_initiative_share(tmp_path, monkeypatch):
    from brr.loom.runtime.config import set_value

    home = home_at(tmp_path)
    monkeypatch.setattr("shutil.which", _which({"grok", "codex", "claude"}))
    adapters.set_order(home, ["grok", "codex", "claude"])
    # Collectors poll every ten minutes. A current window can be six minutes old.
    _gauge(tmp_path, "grok", 65, age=360)
    _gauge(tmp_path, "codex", 90)

    assert adapters.spend_choice(home) == "grok"
    set_value(home.root, "initiative_share", 25)
    assert adapters.spend_choice(home) == "codex"
    set_value(home.root, "initiative_share_grok", 100)
    assert adapters.spend_choice(home) == "grok"
    set_value(home.root, "initiative_share_grok", 0)
    assert adapters.spend_choice(home) == "codex"


def test_spend_respects_configured_candidates_and_reserved_engine(tmp_path, monkeypatch):
    home = home_at(tmp_path)
    monkeypatch.setattr("shutil.which", _which({"grok", "codex", "claude"}))
    preferences = tmp_path / "loom/preferences.toml"
    _gauge(tmp_path, "grok", 90)
    _gauge(tmp_path, "codex", 90)
    _gauge(tmp_path, "claude", 90)
    preferences.write_text('bodies.order = ["grok", "codex", "claude"]\nspend.keep = "grok"\n')
    assert adapters.spend_choice(home) == "codex"
    with preferences.open("a") as handle:
        handle.write('spend.burn = ["claude", "codex"]\n')
    assert adapters.spend_choice(home) == "claude"
    _gauge(tmp_path, "claude", 1)
    assert adapters.spend_choice(home) == "codex"
    preferences.write_text('spend.burn = []\n')
    with pytest.raises(ValueError, match="no eligible engine"):
        adapters.spend_choice(home)


def test_spend_skips_unavailable_and_unusable_readings(tmp_path, monkeypatch):
    home = home_at(tmp_path)
    adapters.set_order(home, ["grok", "codex", "claude"])
    monkeypatch.setattr("shutil.which", _which({"codex", "claude"}))
    _gauge(tmp_path, "grok", 90)
    _gauge(tmp_path, "codex", 40)
    assert adapters.spend_choice(home) == "codex"
    monkeypatch.setattr("shutil.which", _which({"grok", "codex", "claude"}))
    (tmp_path / "loom/usage/grok.json").unlink()
    assert adapters.spend_choice(home) == "codex"
    _gauge(tmp_path, "grok", 90, windows=False)
    assert adapters.spend_choice(home) == "codex"
    _gauge(tmp_path, "grok", 1)
    assert adapters.spend_choice(home) == "codex"
    _gauge(tmp_path, "grok", 90, error="expired")
    assert adapters.spend_choice(home) == "codex"
    _gauge(tmp_path, "grok", 90, age=360, reset_in=-60)
    assert adapters.spend_choice(home) == "codex"
    _gauge(tmp_path, "codex", 1)
    with pytest.raises(ValueError, match="ahead of its initiative pace") as raised:
        adapters.spend_choice(home)
    assert "fuel " in str(raised.value)


def test_handoff_briefing_uses_readings_and_share_when_engines_are_available(tmp_path, monkeypatch):
    from brr.loom.runtime.briefing import BRIEFING, HANDOFF, TITLE, section

    home = home_at(tmp_path)
    today = f"## {TITLE}\n{BRIEFING}"
    monkeypatch.setattr("shutil.which", _which({"grok"}))
    assert section(home) == today
    assert section() == today
    monkeypatch.setattr("shutil.which", _which({"grok", "codex", "claude"}))
    (tmp_path / "loom/preferences.toml").write_text('spend.burn = []\n')
    assert section(home) == f"## {TITLE}\n{BRIEFING.rstrip(chr(10))}\n{HANDOFF}"
    assert "fuel readings and the person's initiative share" in section(home)
    assert "marked" not in HANDOFF


def test_send_spend_reaches_write_send_and_is_exclusive_with_body(tmp_path, monkeypatch, capsys):
    from brr.loom.runtime import verbs_loom
    seen = {}

    def fake_write_send(room, **kwargs):
        seen["spend"] = kwargs["spend"]
        seen["body"] = kwargs["body_choice"]
        return "s-aaaa-test/abcde"

    monkeypatch.setattr(verbs_loom, "write_send", fake_write_send)
    room = tmp_path / "rooms" / "s-aaaa-test"
    room.mkdir(parents=True)
    assert verbs_loom._send([
        "--room", str(room), "--to", "thread:newone", "--spend", "--wish", "new:Work", "Work",
    ]) == 0
    assert seen == {"spend": True, "body": None}
    assert verbs_loom._send([
        "--room", str(room), "--to", "thread:newone", "--spend", "--body", "grok",
        "--wish", "new:Work", "Work",
    ]) == 1
    assert "exclusive" in capsys.readouterr().err
    assert verbs_loom._send([
        "--room", str(room), "--to", "thread:newone", "--reopen", "Again", "--spend",
    ]) == 1
    assert "--spend" in capsys.readouterr().err


def test_spend_pins_the_seat_before_open_and_a_refusal_opens_nothing(tmp_path, monkeypatch):
    from brr.loom.runtime import lifecycle
    from brr.loom.runtime.port import PortError, write_send
    home = home_at(tmp_path)
    monkeypatch.setattr("shutil.which", _which({"grok", "codex", "claude"}))
    (tmp_path / "loom/preferences.toml").write_text(
        'spend.burn = ["grok", "codex"]\nspend.keep = "claude"\n'
    )
    _gauge(tmp_path, "grok", 1)
    _gauge(tmp_path, "codex", 40)
    _gauge(tmp_path, "claude", 90)
    room = tmp_path / "rooms" / "s-aaaa-test"
    room.mkdir(parents=True)
    pinned = {}

    def stop(*args, **kwargs):
        pinned["body"] = args[4]
        raise PortError("stop before land")

    monkeypatch.setattr(lifecycle, "open_thread", stop)
    with pytest.raises(PortError, match="stop before land"):
        write_send(room, to="thread:handoff", sender="s-aaaa-test", body="Work",
                   wish="new:Work", spend=True)
    assert pinned["body"] == "codex"
    assert not (room / "port/out").exists()
    _gauge(tmp_path, "codex", 1)
    with pytest.raises(PortError, match="ahead of its initiative pace"):
        write_send(room, to="thread:handoff", sender="s-aaaa-test", body="Work",
                   wish="new:Work", spend=True)
    assert not (tmp_path / "self/threads/handoff").exists()
    # A single harness leaves default body selection in place, even when behind pace.
    monkeypatch.setattr("shutil.which", _which({"grok"}))
    pinned.clear()
    with pytest.raises(PortError, match="stop before land"):
        write_send(room, to="thread:handoff", sender="s-aaaa-test", body="Work",
                   wish="new:Work", spend=True)
    assert pinned["body"] is None
    readme = tmp_path / "self/threads/work/README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("---\nid: work\nstatus: open\ntense: plan\n---\n# Work\n")
    monkeypatch.setattr("shutil.which", _which({"grok", "codex"}))
    with pytest.raises(PortError, match="--body is only for opening"):
        write_send(room, to="thread:work", sender="s-aaaa-test", body="hi", spend=True)
