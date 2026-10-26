"""A bare shell body leaves the model to the harness's own choice."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import brr
from brr.loom.runtime import service
from brr.loom.runtime.adapters import at_birth, resolve
from brr.loom.runtime.home import Home

SRC = str(Path(brr.__file__).resolve().parents[1])


def test_a_bare_shell_seat_and_config_body_mean_the_harness_pick(tmp_path):
    home = Home(tmp_path / "home")
    (home.thread_dir("t")).mkdir(parents=True)
    (home.thread_dir("t") / "seat").write_text("body: claude\n")
    assert at_birth(home, "t", ("fake", "x")) == ("claude", "")
    (home.thread_dir("t") / "seat").write_text("body: codex/gpt-6\n")
    assert at_birth(home, "t", ("fake", "x")) == ("codex", "gpt-6")
    (home.thread_dir("t") / "seat").write_text("body: claude/\n")
    with pytest.raises(ValueError):
        at_birth(home, "t", ("fake", "x"))

    config = home.root / "loom" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('body = "claude"\n')
    assert service._body_choice(home.root) == "claude"
    (home.thread_dir("t") / "seat").write_text("")
    assert at_birth(home, "t", ("fake", "x")) == ("claude", "")


def _drive(tmp_path, shell: str, core: str, env: dict[str, str] | None = None) -> list[str]:
    """Run the bundled body's real ``run`` with its CLI stubbed; the CLI's argv."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    out = tmp_path / "argv"
    cli = stub / shell
    cli.write_text(f"#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > {out}\n")
    cli.chmod(0o755)
    room = tmp_path / "room"
    room.mkdir(exist_ok=True)
    (tmp_path / "wake.md").write_text("the wake")
    run = resolve(Home(tmp_path / "home"), shell) / "run"
    subprocess.run(
        [sys.executable, str(run), str(room), core], check=True,
        env={**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
             "PYTHONPATH": SRC, "BRNRD_JACK": "jack", "BRNRD_WAIT": "1",
             "BRNRD_WAKE": str(tmp_path / "wake.md"), "BRNRD_CORE": "", **(env or {})},
    )
    return out.read_text().splitlines()


def test_claude_run_passes_no_model_when_the_core_is_empty(tmp_path):
    argv = _drive(tmp_path, "claude", "")
    assert "--model" not in argv
    assert argv[0] == "-p" and argv[-1] == "the wake"


def test_claude_run_passes_a_named_core(tmp_path):
    argv = _drive(tmp_path, "claude", "sonnet")
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_codex_run_carries_the_persons_model_when_the_core_is_empty(tmp_path):
    codex = tmp_path / "codex-home"
    codex.mkdir()
    (codex / "config.toml").write_text('model = "gpt-6"\n')
    argv = _drive(tmp_path, "codex", "", {"CODEX_HOME": str(codex)})
    assert argv[argv.index("--model") + 1] == "gpt-6"
    assert "--ignore-user-config" in argv

    (codex / "config.toml").write_text('[profiles.fast]\nmodel = "gpt-6-mini"\n')
    assert "--model" not in _drive(tmp_path, "codex", "", {"CODEX_HOME": str(codex)})
