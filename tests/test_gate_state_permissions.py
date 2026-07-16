"""Secret-bearing gate state is private on POSIX."""

from __future__ import annotations

import os
import stat

import pytest

from brr.gates import runtime
from brr.gates.github import state as github_state


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX mode bits are not a Windows access-control guarantee",
)


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def _with_permissive_umask(save):
    previous = os.umask(0)
    try:
        save()
    finally:
        os.umask(previous)


def test_shared_gate_state_is_private_under_permissive_umask(tmp_path):
    brr_dir = tmp_path / ".brr"
    _with_permissive_umask(
        lambda: runtime.save_state(brr_dir, "telegram", {"token": "secret"})
    )

    assert _mode(runtime.state_path(brr_dir, "telegram")) == 0o600


def test_github_state_repairs_existing_permissive_mode(tmp_path):
    brr_dir = tmp_path / ".brr"
    path = github_state._state_path(brr_dir)
    path.parent.mkdir(parents=True)
    path.write_text('{"token": "old"}\n', encoding="utf-8")
    path.chmod(0o664)

    _with_permissive_umask(
        lambda: github_state._save_state(brr_dir, {"token": "new"})
    )

    assert _mode(path) == 0o600
    assert github_state._load_state(brr_dir) == {"token": "new"}
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []
