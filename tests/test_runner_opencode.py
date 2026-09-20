"""The third Shell is one profile: OpenCode boots from runners.toml alone."""

import os
import stat
from pathlib import Path

import pytest

from brr import runner as runner_mod
from brr.runner import RunnerInvocation, invoke_runner


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(runner_mod, "_profiles_cache", None)
    monkeypatch.setattr(runner_mod, "_profiles_cache_key", None)


def _shim(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "opencode"
    shim.write_text(
        '#!/bin/sh\nprintf "cwd=%s\\n" "$PWD"\nprintf "argc=%s\\n" "$#"\n'
        'for a in "$@"; do printf "arg=%s\\n" "$a"; done\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return bindir


def test_profile_resolves_without_hooks_or_quota():
    profile = runner_mod.runner_profile("opencode")
    assert profile.name == "opencode"
    assert runner_mod.profile_hooks_flavour("opencode") is None
    meta = runner_mod.profile_metadata("opencode")
    assert meta["class"] == "economy"
    assert "quota_source" not in meta


def test_unavailable_when_binary_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    profiles = runner_mod._load_profiles()
    assert runner_mod._runner_available("opencode", profiles) is False
    assert "opencode" not in runner_mod.detect_all_runners()


def test_argv_and_cwd_with_shim(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(_shim(tmp_path)) + os.pathsep + os.environ["PATH"])
    profiles = runner_mod._load_profiles()
    assert runner_mod._runner_available("opencode", profiles) is True
    assert "opencode" in runner_mod.detect_all_runners()
    work = tmp_path / "work"
    work.mkdir()
    resp = tmp_path / "resp.md"
    inv = RunnerInvocation(
        kind="daemon-run", label="oc", prompt="hello world",
        cwd=work, repo_root=tmp_path, response_path=str(resp),
    )
    result = invoke_runner("opencode", inv, cfg={})
    assert result.ok
    lines = result.stdout.splitlines()
    assert f"cwd={work.resolve()}" in [l for l in lines if l.startswith("cwd=")] or \
        any(l.startswith("cwd=") and l.endswith("work") for l in lines)
    assert lines[lines.index("argc=3")] == "argc=3"
    assert lines[-3:] == ["arg=run", "arg=--auto", "arg=hello world"]


def test_cmd_template_places_prompt_in_argv():
    cmd = runner_mod._build_cmd("opencode", "P", {})
    assert cmd == ["opencode", "run", "--auto", "P"]


def test_doctor_knows_opencode(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    diag = runner_mod.diagnose_runners()
    assert "opencode" in diag.shells_missing
    assert "OpenCode" in runner_mod.render_runner_doctor(diag)
