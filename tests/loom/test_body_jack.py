"""``$BRNRD_JACK`` runs the same in every shell a harness uses (his 11405).

Codex runs a body's commands through the person's shell, zsh on a Mac, and
zsh never splits an unquoted variable: a jack that was a whole command line
was one file name there, so a codex body could not reach its loom.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from brr.loom.runtime.loom import _env


def _room_with_shim(tmp_path: Path, monkeypatch) -> Path:
    # A space in both paths: the jack must survive it in every shell.
    root = tmp_path / "brnrd root"
    shim = root / "bin" / "brnrd"
    shim.parent.mkdir(parents=True)
    shim.write_text('#!/bin/sh\nprintf "%s|" "$@"\n')
    shim.chmod(0o755)
    monkeypatch.setenv("BRNRD_ROOT", str(root))
    home = tmp_path / "the home"
    (home / "loom").mkdir(parents=True)
    (home / "loom" / "config.toml").write_text('name = "t"\n')
    room = home / "rooms" / "s1"
    (room / "port").mkdir(parents=True)
    return room


SHELLS = [s for s in ("zsh", "bash", "sh") if shutil.which(s)]


@pytest.mark.parametrize("shell", SHELLS)
def test_jack_runs_from_each_shell_as_the_wake_says_to_call_it(tmp_path, monkeypatch, shell):
    room = _room_with_shim(tmp_path, monkeypatch)
    env = _env(room)
    jack = Path(env["BRNRD_JACK"])
    assert jack.parent == room / "port" and jack.stat().st_mode & 0o111
    # Codex runs ``<shell> -lc <command>``; the wake's line is quoted.
    out = subprocess.run([shell, "-c", '"$BRNRD_JACK" --event start'], env=env,
                         capture_output=True, text=True, check=True)
    assert out.stdout == f"runtime|jack|--room|{room}|--event|start|"


def test_unquoted_jack_runs_in_zsh(tmp_path, monkeypatch):
    if not shutil.which("zsh"):
        pytest.skip("no zsh")
    room = _room_with_shim(tmp_path, monkeypatch)
    env = _env(room)
    out = subprocess.run(["zsh", "-c", "$BRNRD_JACK --event post"], env=env,
                         capture_output=True, text=True, check=True)
    assert out.stdout == f"runtime|jack|--room|{room}|--event|post|"


def test_hook_command_lines_quote_the_jack(tmp_path, monkeypatch):
    """Claude and grok hand their hook a shell line built from the path."""
    import shlex
    room = _room_with_shim(tmp_path, monkeypatch)
    env = _env(room)
    for body in ("claude", "grok"):
        source = (Path(__file__).parents[2] / "src/brr/loom/runtime/bodies" / body / "run").read_text()
        assert 'shlex.quote(os.environ["BRNRD_JACK"]) + " --event "' in source
    line = shlex.quote(env["BRNRD_JACK"]) + " --event stop"
    out = subprocess.run(["sh", "-c", line], env=env, capture_output=True, text=True, check=True)
    assert out.stdout == f"runtime|jack|--room|{room}|--event|stop|"
