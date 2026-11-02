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
