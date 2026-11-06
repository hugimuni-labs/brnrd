"""The harness priority list: one file ranks every bundled body, the person reorders it, the loom falls down it."""
import io
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


def _gauge(root, shell, left, *, error=None, windows=True):
    import json
    path = root / "loom" / "usage" / f"{shell}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"at": 1_700_000_000, "windows": [{"name": "week", "left_pct": left}] if windows else []}
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
    assert adapters.burn_marks(home) == (["grok", "codex"], "claude")
    assert adapters.spend_mode(home) == "burn"
    # The judgement shell is the keep mark when the file leaves it out of the burn list.
    (tmp_path / "loom/preferences.toml").write_text(
        'model.default = "claude/opus"\nspend.burn = ["grok", "codex"]\n'
    )
    assert adapters.keep_shell(home) == "claude"
    assert adapters.burn_marks(home) == (["grok", "codex"], "claude")
    # Named in the burn list, it is not kept: the person put it there to spend.
    (tmp_path / "loom/preferences.toml").write_text(
        'model.default = "claude/opus"\nspend.burn = ["claude", "grok"]\n'
    )
    assert adapters.keep_shell(home) is None
    (tmp_path / "loom/preferences.toml").write_text('spend.burn = ["grok", "claude"]\nspend.keep = "claude"\n')
    with pytest.raises(ConfigError, match="is also in spend.burn"):
        adapters.burn_marks(home)
    (tmp_path / "loom/preferences.toml").write_text('spend.burn = "grok"\n')
    with pytest.raises(ConfigError, match="spend.burn is a list"):
        adapters.body_order(home)
    (tmp_path / "loom/preferences.toml").write_text('spend.keep = ["claude"]\n')
    with pytest.raises(ConfigError, match="spend.keep is one shell"):
        adapters.body_order(home)
    (tmp_path / "loom/preferences.toml").write_text('nope = "x"\n')
    with pytest.raises(ConfigError, match="spend.burn"):
        adapters.body_order(home)


def test_spend_picks_the_first_shell_above_the_floor(tmp_path, monkeypatch):
    home = home_at(tmp_path)
    monkeypatch.setattr("shutil.which", _which({"grok", "codex", "claude", "vibe"}))
    (tmp_path / "loom/preferences.toml").write_text(
        'spend.burn = ["grok", "codex", "vibe"]\nspend.keep = "claude"\n'
    )
    # grok is dry, claude is keep and fuller, codex is the first burn shell the predicate allows.
    assert adapters.spend_choice(home, usable=lambda shell: shell in {"codex", "claude"}) == "codex"
    # grok would win on fuel, and it is not installed, so the next burn shell is the pin.
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
    _gauge(tmp_path, "grok", 3.0)
    assert adapters.spend_choice(home) == "codex"
    _gauge(tmp_path, "grok", 90, error="expired")
    assert adapters.spend_choice(home) == "codex"
    _gauge(tmp_path, "codex", 1)
    with pytest.raises(ValueError, match="no burn shell") as raised:
        adapters.spend_choice(home)
    assert "fuel " in str(raised.value)
    # One installed harness: the flag is a no-op, even when the file names more shells.
    monkeypatch.setattr("shutil.which", _which({"grok"}))
    assert adapters.spend_choice(home) is None
    monkeypatch.setattr("shutil.which", _which({"grok", "codex"}))
    (tmp_path / "loom/preferences.toml").write_text('spend.burn = []\n')
    with pytest.raises(ValueError, match="spend.burn is empty"):
        adapters.spend_choice(home)
    (tmp_path / "loom/preferences.toml").write_text('spend.keep = "claude"\n')
    with pytest.raises(ValueError, match="needs spend.burn"):
        adapters.spend_choice(home)


def test_one_harness_briefing_is_today_and_two_harnesses_gain_one_sentence(tmp_path, monkeypatch):
    from brr.loom.runtime.briefing import BRIEFING, BURN, HEADROOM, TITLE, section
    home = home_at(tmp_path)
    today = f"## {TITLE}\n{BRIEFING}"
    monkeypatch.setattr("shutil.which", _which({"grok"}))
    (tmp_path / "loom/preferences.toml").write_text(
        'spend.burn = ["grok", "codex"]\nspend.keep = "claude"\n'
    )
    assert section(home) == today
    assert section() == today
    monkeypatch.setattr("shutil.which", _which({"grok", "codex", "claude"}))
    assert section() == today
    assert section(home) == f"## {TITLE}\n{BRIEFING.rstrip(chr(10))}\n{BURN}"
    assert "send --spend" not in section()
    (tmp_path / "loom/preferences.toml").write_text('spend.burn = []\n')
    assert section(home) == today
    (tmp_path / "loom/preferences.toml").unlink()
    assert section(home) == f"## {TITLE}\n{BRIEFING.rstrip(chr(10))}\n{HEADROOM}"
    assert "send --body" not in today


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
    with pytest.raises(PortError, match="no burn shell"):
        write_send(room, to="thread:handoff", sender="s-aaaa-test", body="Work",
                   wish="new:Work", spend=True)
    assert not (tmp_path / "self/threads/handoff").exists()
    # One harness: --spend does not pin a body.
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
