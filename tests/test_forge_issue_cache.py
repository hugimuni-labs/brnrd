"""Tests for the local closed-issue cache behind `pitfall-cites-closed-issue`.

Same two invariants `test_forge_pr_cache.py` carries, ported to issues:

1. **`read_state` never touches the network** — it's a pure JSON load plus a
   freshness verdict, the only thing `notes_preflight` is allowed to call.
2. **absent != unknown != none.** No cache, and a failed refresh, must both
   read as *unknown*; only a successful refresh may report a real "nothing
   to check" or a real closed/open state.

Plus the property specific to this cache: it fetches *exactly the numbers
it's told to check* (one `gh issue view` per number), never a bulk list —
see the module docstring for why a recency-windowed bulk list is the wrong
shape for pitfall citations.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from brr import dominion, forge_issue_cache

from _helpers import init_git_repo


def _repo_with_github_remote(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/Gurio/brr.git"],
        cwd=repo, check=True,
    )
    return repo


def _repo_with_gitlab_remote(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://gitlab.com/group/proj.git"],
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
    real_run = subprocess.run

    def dispatch(cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        if argv[:1] == ["gh"]:
            return handler(argv, **kwargs)
        return real_run(cmd, *args, **kwargs)

    return dispatch


# ── cache read: absent / error / stale / fresh ───────────────────────


def test_read_state_absent_is_unknown_not_empty(tmp_path):
    repo = _repo_with_github_remote(tmp_path)
    state = forge_issue_cache.read_state(repo)
    assert state["status"] == "absent"
    assert state["issues"] is None


def test_read_state_fresh(tmp_path):
    repo = _repo_with_github_remote(tmp_path)
    _write_cache(repo, {
        "fetched_at": _iso(),
        "issues": {"1298": {"number": 1298, "state": "CLOSED", "closed_at": _iso(-3600)}},
    })
    state = forge_issue_cache.read_state(repo)
    assert state["status"] == "fresh"
    assert state["issues"]["1298"]["state"] == "CLOSED"


def test_read_state_stale_carries_age(tmp_path):
    repo = _repo_with_github_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(-3600), "issues": {}})
    state = forge_issue_cache.read_state(repo)
    assert state["status"] == "stale"
    assert state["issues"] == {}  # a real, known "nothing to check"
    assert 3500 < state["age_seconds"] < 3700


def test_read_state_error_cache_is_unknown(tmp_path):
    repo = _repo_with_github_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "issues": None, "error": "gh: not logged in"})
    state = forge_issue_cache.read_state(repo)
    assert state["status"] == "error"
    assert state["issues"] is None
    assert "not logged in" in state["error"]


# ── refresh: targeted per-number fetch ───────────────────────────────


def test_refresh_with_no_numbers_stamps_freshness_without_touching_gh(tmp_path, monkeypatch):
    """An empty citation set is a real, current 'nothing to check' — not a
    reason to leave the cache absent forever (which would make the daemon's
    stale check re-attempt every tick)."""
    repo = _repo_with_github_remote(tmp_path)

    def forbidden(cmd, **kwargs):
        pytest.fail("refresh() with no numbers reached gh")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(forbidden))
    payload = forge_issue_cache.refresh(repo, set())

    assert payload["issues"] == {}
    assert payload["error"] is None
    assert forge_issue_cache.read_state(repo)["status"] == "fresh"


def test_refresh_fetches_exactly_the_given_numbers(tmp_path, monkeypatch):
    repo = _repo_with_github_remote(tmp_path)
    calls: list[int] = []

    def fake_gh(cmd, **kwargs):
        assert cmd[:3] == ["gh", "issue", "view"]
        number = int(cmd[3])
        calls.append(number)
        assert "--repo" in cmd and "Gurio/brr" in cmd
        row = {
            "number": number, "title": f"issue {number}", "state": "CLOSED",
            "closedAt": "2026-08-05T00:00:00Z",
            "url": f"https://github.com/Gurio/brr/issues/{number}",
        }
        return subprocess.CompletedProcess(cmd, 0, json.dumps(row), "")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(fake_gh))
    payload = forge_issue_cache.refresh(repo, {1298, 931})

    assert sorted(calls) == [931, 1298]
    assert payload["issues"]["1298"]["state"] == "CLOSED"
    assert payload["issues"]["1298"]["closed_at"] == "2026-08-05T00:00:00Z"
    assert payload["issues"]["931"]["state"] == "CLOSED"


def test_refresh_leaves_a_number_untouched_when_gh_cannot_resolve_it(tmp_path, monkeypatch):
    """The common case: the number names a PR, not an issue. `gh issue
    view` fails, and the number simply gets no entry — never a guessed
    state."""
    repo = _repo_with_github_remote(tmp_path)

    def fake_gh(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, "", "GraphQL: Could not resolve to an issue\n",
        )

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(fake_gh))
    payload = forge_issue_cache.refresh(repo, {1004})

    assert payload["issues"] == {}
    assert payload["error"] is None  # not a refresh failure — just nothing to record


def test_refresh_keeps_prior_numbers_not_in_this_round(tmp_path, monkeypatch):
    repo = _repo_with_github_remote(tmp_path)
    _write_cache(repo, {
        "fetched_at": _iso(-7200),
        "issues": {"100": {"number": 100, "state": "CLOSED", "closed_at": "2026-01-01T00:00:00Z"}},
    })

    def fake_gh(cmd, **kwargs):
        row = {"number": 200, "title": "t", "state": "OPEN", "closedAt": None, "url": ""}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(row), "")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(fake_gh))
    payload = forge_issue_cache.refresh(repo, {200})

    assert payload["issues"]["100"]["state"] == "CLOSED"  # not re-checked, not dropped
    assert payload["issues"]["200"]["state"] == "OPEN"


def test_refresh_never_shells_out_on_a_non_github_remote(tmp_path, monkeypatch):
    repo = _repo_with_gitlab_remote(tmp_path)

    def forbidden(cmd, **kwargs):
        pytest.fail(f"refresh() shelled out to gh for a non-GitHub remote: {cmd}")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(forbidden))
    payload = forge_issue_cache.refresh(repo, {1298})

    assert payload["issues"] == {}
    assert payload["error"]
    assert "GitHub" in payload["error"]
    assert forge_issue_cache.read_state(repo)["status"] == "error"


def test_refresh_caps_a_pathological_citation_count(tmp_path, monkeypatch):
    repo = _repo_with_github_remote(tmp_path)
    calls: list[int] = []

    def fake_gh(cmd, **kwargs):
        number = int(cmd[3])
        calls.append(number)
        row = {"number": number, "title": "t", "state": "OPEN", "closedAt": None, "url": ""}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(row), "")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(fake_gh))
    numbers = set(range(1, forge_issue_cache.MAX_NUMBERS_PER_REFRESH + 51))
    assert len(numbers) == forge_issue_cache.MAX_NUMBERS_PER_REFRESH + 50
    payload = forge_issue_cache.refresh(repo, numbers)

    assert len(calls) == forge_issue_cache.MAX_NUMBERS_PER_REFRESH
    assert payload["truncated"] == 50


def test_refresh_if_stale_skips_a_fresh_cache(tmp_path, monkeypatch):
    repo = _repo_with_github_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(), "issues": {}})

    def no_gh(cmd, **kwargs):
        pytest.fail("refresh_if_stale called gh on a fresh cache")

    monkeypatch.setattr(forge_issue_cache.subprocess, "run", _gh_only(no_gh))
    assert forge_issue_cache.refresh_if_stale(repo) is False


def test_refresh_if_stale_refreshes_an_old_cache(tmp_path, monkeypatch):
    repo = _repo_with_github_remote(tmp_path)
    _write_cache(repo, {"fetched_at": _iso(-7200), "issues": {}})

    monkeypatch.setattr(
        forge_issue_cache.subprocess, "run",
        _gh_only(lambda cmd, **kwargs: pytest.fail("no numbers cited; gh must not be called")),
    )
    # No dominion / pitfalls store exists under this repo, so cited_numbers()
    # is empty and the refresh must stamp freshness without reaching gh.
    assert forge_issue_cache.refresh_if_stale(repo) is True
    assert forge_issue_cache.read_state(repo)["status"] == "fresh"


# ── cited_numbers: sourced from the resident's own pitfall stores ────


def test_cited_numbers_reads_the_legacy_dominion_pitfalls_store(tmp_path):
    repo = _repo_with_github_remote(tmp_path)
    (repo / ".brr").mkdir(parents=True, exist_ok=True)
    (repo / ".brr" / "config").write_text(
        f"home.path={tmp_path / 'home'}\n", encoding="utf-8"
    )
    dom = dominion.dominion_path(repo)
    dom.mkdir(parents=True, exist_ok=True)
    (dom / "pitfalls.md").write_text(
        "## A lesson\n"
        "trigger: x\n"
        "Closed by #1298, and also see https://github.com/o/r/pull/9.\n",
        encoding="utf-8",
    )

    numbers = forge_issue_cache.cited_numbers(repo)
    assert 1298 in numbers
    assert 9 not in numbers  # explicit /pull/ ref — forge_pr_cache's job, not this cache's
