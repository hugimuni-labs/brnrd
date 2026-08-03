"""Tests for the local open-issue-number cache behind the stale-open facet.

Mirrors ``tests/test_forge_pr_cache.py``'s shape and its two invariants,
turned on a different cache (see :mod:`brr.forge_issue_cache`'s own
docstring for why a second cache exists rather than widening the PR one):

1. **The render path never touches the network.** Only :func:`refresh` (and
   its daemon-tick wrappers) may shell out to ``gh``.
2. **absent ≠ unknown ≠ none.** No cache, and a failed refresh, must both
   read as *unknown*; only a successful refresh that found nothing may read
   as "no open issues".
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from brr import forge_issue_cache

from _helpers import commit_files, init_git_repo


def _repo_with_remote(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "one\n"})
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/Gurio/brr.git"],
        cwd=repo, check=True,
    )
    return repo


def _write_cache(repo: Path, payload: dict) -> Path:
    path = forge_issue_cache.cache_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _iso(offset_seconds: float = 0.0) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_seconds)
    )


def _gh_only(handler):
    """Intercept ``gh`` calls with *handler*; let git through to the real thing."""
    real_run = subprocess.run

    def dispatch(cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        if argv[:1] == ["gh"]:
            return handler(argv, **kwargs)
        return real_run(cmd, *args, **kwargs)

    return dispatch


# ── cache read: absent / error / stale / fresh ───────────────────────


def test_read_state_absent_is_unknown_not_empty(tmp_path):
    repo = _repo_with_remote(tmp_path)
    state = forge_issue_cache.read_state(repo)
    assert state["status"] == "absent"
    assert state["numbers"] is None


def test_read_state_fresh(tmp_path):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "numbers": [1002, 957]})
    state = forge_issue_cache.read_state(repo)
    assert state["status"] == "fresh"
    assert set(state["numbers"]) == {1002, 957}
    assert state["age_seconds"] < 60


def test_read_state_stale_carries_age(tmp_path):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(-3600), "numbers": []})
    state = forge_issue_cache.read_state(repo)
    assert state["status"] == "stale"
    assert state["numbers"] == []  # a real, known "no open issues"
    assert 3500 < state["age_seconds"] < 3700


def test_read_state_error_cache_is_unknown(tmp_path):
    repo = _repo_with_remote(tmp_path)
    _write_cache(
        repo, {"fetched_at": _iso(), "numbers": None, "error": "gh: not logged in"},
    )
    state = forge_issue_cache.read_state(repo)
    assert state["status"] == "error"
    assert state["numbers"] is None
    assert "not logged in" in state["error"]


# ── refresh (the one network path — daemon-side only) ────────────────


def test_refresh_writes_cache(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    rows = [{"number": 1002}, {"number": 957}]

    def fake_gh(cmd, **kwargs):
        assert cmd[:3] == ["gh", "issue", "list"]
        assert "--repo" in cmd and "Gurio/brr" in cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(fake_gh))
    payload = forge_issue_cache.refresh(repo)

    assert payload["repo"] == "Gurio/brr"
    assert payload["error"] is None
    on_disk = json.loads(forge_issue_cache.cache_path(repo).read_text())
    assert set(on_disk["numbers"]) == {1002, 957}
    assert on_disk["fetched_at"]


def test_refresh_failure_records_error_and_keeps_last_rows(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(-60), "numbers": [1002]})

    def fake_gh(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "gh: not logged in\n")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(fake_gh))
    payload = forge_issue_cache.refresh(repo)

    assert payload["error"] == "gh: not logged in"
    # Last good rows survive, honestly aged — a bad refresh is not "no issues".
    assert payload["numbers"] == [1002]


def test_refresh_if_stale_skips_a_fresh_cache(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "numbers": []})

    def no_gh(cmd, **kwargs):
        pytest.fail("refresh_if_stale called gh on a fresh cache")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(no_gh))
    assert forge_issue_cache.refresh_if_stale(repo) is False


def test_refresh_if_stale_refreshes_an_old_cache(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(-3600), "numbers": []})
    calls: list[list[str]] = []

    def fake_gh(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(fake_gh))
    assert forge_issue_cache.refresh_if_stale(repo) is True
    assert calls and calls[0][:3] == ["gh", "issue", "list"]
