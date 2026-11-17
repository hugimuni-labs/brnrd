"""Focused tests for the daemon's package-release availability observation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import __version__, release_availability


def _repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / ".brr").mkdir()
    release_availability.cache_path(tmp_path).parent.mkdir(parents=True)
    return tmp_path


@pytest.mark.parametrize(
    ("installed", "latest", "expected"),
    [
        ("0.1.0", "0.2.0", "update available: 0.1.0 → 0.2.0"),
        ("1.0", "1.0.0", None),
    ],
)
def test_compares_pep440_versions(installed, latest, expected):
    assert release_availability.Availability(installed, latest).render() == expected


def test_fresh_cache_skips_request_and_stale_cache_refreshes(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    checked_at = 10_000.0
    path = release_availability.cache_path(repo)
    path.write_text(json.dumps({"checked_at": checked_at, "latest": "0.1.0"}), encoding="utf-8")
    calls: list[float] = []

    def fetch(*, timeout):
        calls.append(timeout)
        return "0.2.0"

    monkeypatch.setattr(release_availability, "_fetch_latest", fetch)
    release_availability.refresh_if_stale(repo, now=checked_at + 60)
    assert calls == []

    release_availability.refresh_if_stale(
        repo,
        now=checked_at + release_availability.DEFAULT_TTL_SECONDS + 1,
    )
    assert calls == [release_availability.REQUEST_TIMEOUT_SECONDS]
    assert release_availability.load(repo)["latest"] == "0.2.0"


def test_request_failure_preserves_last_good_observation(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    original = {"schema": 1, "checked_at": 1.0, "latest": "999.0.0"}
    path = release_availability.cache_path(repo)
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(release_availability, "_fetch_latest", lambda **_kwargs: None)

    observed = release_availability.refresh_if_stale(
        repo,
        now=release_availability.DEFAULT_TTL_SECONDS + 2,
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["latest"] == original["latest"]
    assert saved["checked_at"] == original["checked_at"]
    assert saved["attempted_at"] == release_availability.DEFAULT_TTL_SECONDS + 2
    assert observed is not None
    assert observed.render() == f"update available: {__version__} → 999.0.0"

    # The failed attempt is itself cached: a fast daemon tick cannot retry
    # until the daily TTL has elapsed.
    release_availability.refresh_if_stale(
        repo,
        now=release_availability.DEFAULT_TTL_SECONDS + 3,
    )
    assert json.loads(path.read_text(encoding="utf-8")) == saved


def test_refresh_if_stale_populates_both_channels_independently(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(release_availability, "_fetch_latest", lambda **_kw: "1.2.0")
    monkeypatch.setattr(release_availability, "_fetch_latest_npm", lambda **_kw: "1.3.0")

    release_availability.refresh_if_stale(repo, now=1.0)

    assert release_availability.observation(repo).latest == "1.2.0"
    assert release_availability.npm_observation(repo).latest == "1.3.0"


def test_npm_channel_failure_does_not_blank_the_release_channel(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    path = release_availability.cache_path(repo)
    path.write_text(
        json.dumps({"schema": 1, "attempted_at": 1.0, "checked_at": 1.0,
                    "latest": "1.0.0", "npm_checked_at": 1.0, "npm_latest": "1.0.0"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(release_availability, "_fetch_latest", lambda **_kw: "1.1.0")
    monkeypatch.setattr(release_availability, "_fetch_latest_npm", lambda **_kw: None)

    release_availability.refresh_if_stale(
        repo, now=release_availability.DEFAULT_TTL_SECONDS + 2,
    )

    # A failed npm fetch keeps its own last-good value, exactly like the
    # release channel's failure-preserves-last-good contract above — the two
    # channels' freshness clocks are shared, but their last-known-good
    # values are not allowed to clobber each other.
    assert release_availability.observation(repo).latest == "1.1.0"
    assert release_availability.npm_observation(repo).latest == "1.0.0"


def test_npm_observation_absent_when_never_fetched(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    assert release_availability.npm_observation(repo) is None


def test_latest_release_is_read_from_the_repository_tags_not_pypi(monkeypatch):
    import subprocess
    asked = []

    def run(args, **kwargs):
        asked.append(args)
        listing = "a\trefs/tags/v0.7.0\nb\trefs/tags/v0.7.1\nc\trefs/tags/v0.7.1^{}\nd\trefs/tags/v0.8.0rc1\n"
        return subprocess.CompletedProcess(args, 0, listing, "")

    monkeypatch.setattr(release_availability.subprocess, "run", run)
    assert release_availability._fetch_latest(timeout=1) == "0.7.1"
    assert asked == [["git", "ls-remote", "--tags", release_availability.REPOSITORY_URL, "refs/tags/v*"]]


@pytest.mark.parametrize("failure", ["exit", "missing-git", "timeout", "no-releases"])
def test_a_tag_read_that_fails_is_none_never_a_raise(monkeypatch, failure):
    import subprocess

    def run(args, **kwargs):
        if failure == "missing-git":
            raise FileNotFoundError("git")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 1)
        if failure == "exit":
            return subprocess.CompletedProcess(args, 128, "", "fatal: unable to access")
        return subprocess.CompletedProcess(args, 0, "d\trefs/tags/board-builds\n", "")

    monkeypatch.setattr(release_availability.subprocess, "run", run)
    assert release_availability._fetch_latest(timeout=1) is None
