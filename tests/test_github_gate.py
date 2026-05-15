"""Tests for the GitHub gate."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from brr import protocol
from brr.gates import github


# ── parse_origin_url ─────────────────────────────────────────────────


@pytest.mark.parametrize("url, expected", [
    ("git@github.com:Gurio/brr.git", "Gurio/brr"),
    ("git@github.com:Gurio/brr", "Gurio/brr"),
    ("https://github.com/Gurio/brr.git", "Gurio/brr"),
    ("https://github.com/Gurio/brr", "Gurio/brr"),
    ("https://github.com/Gurio/brr/", "Gurio/brr"),
    ("git@gitlab.com:owner/repo.git", None),
    ("https://gitlab.com/owner/repo", None),
    ("", None),
    ("not-a-url", None),
])
def test_parse_origin_url(url, expected):
    assert github.parse_origin_url(url) == expected


# ── token resolution ────────────────────────────────────────────────


def test_resolve_token_prefers_stored(monkeypatch):
    monkeypatch.setattr(github, "_gh_cli_token", lambda: "from-gh")
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")

    assert github.resolve_token({"token": "stored-token"}) == "stored-token"


def test_resolve_token_falls_back_to_gh_cli(monkeypatch):
    monkeypatch.setattr(github, "_gh_cli_token", lambda: "gh-cli-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    assert github.resolve_token({}) == "gh-cli-token"


def test_resolve_token_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(github, "_gh_cli_token", lambda: None)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    assert github.resolve_token({}) == "env-token"


def test_resolve_token_returns_none_when_nothing(monkeypatch):
    monkeypatch.setattr(github, "_gh_cli_token", lambda: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    assert github.resolve_token({}) is None


def test_auth_prompts_when_no_token_source(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(github, "_gh_cli_token", lambda: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "pasted-token")
    monkeypatch.setattr(github, "_validate_token", lambda _t: "octocat")
    brr_dir = tmp_path / ".brr"

    github.auth(brr_dir)

    state = github._load_state(brr_dir)
    assert state["token"] == "pasted-token"
    assert state["bot_login"] == "octocat"
    assert state["token_source"] == "stored"


def test_auth_uses_gh_cli_token_without_storing(tmp_path, monkeypatch):
    monkeypatch.setattr(github, "_gh_cli_token", lambda: "gh-cli-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(github, "_validate_token", lambda _t: "octocat")
    brr_dir = tmp_path / ".brr"

    github.auth(brr_dir)

    state = github._load_state(brr_dir)
    assert "token" not in state, "gh CLI tokens must not be persisted"
    assert state["bot_login"] == "octocat"
    assert state["token_source"] == "gh-cli"


# ── repo autodetect ────────────────────────────────────────────────


def test_autodetect_repo_from_origin_https(tmp_path, monkeypatch):
    monkeypatch.setattr(github.gitops, "default_remote", lambda _r: "origin")
    monkeypatch.setattr(
        github.gitops, "remote_url",
        lambda _r, _name: "https://github.com/Gurio/brr.git",
    )
    assert github.autodetect_repo(tmp_path) == "Gurio/brr"


def test_autodetect_repo_from_origin_ssh(tmp_path, monkeypatch):
    monkeypatch.setattr(github.gitops, "default_remote", lambda _r: "origin")
    monkeypatch.setattr(
        github.gitops, "remote_url",
        lambda _r, _name: "git@github.com:Gurio/brr.git",
    )
    assert github.autodetect_repo(tmp_path) == "Gurio/brr"


def test_autodetect_repo_returns_none_for_non_github(tmp_path, monkeypatch):
    monkeypatch.setattr(github.gitops, "default_remote", lambda _r: "origin")
    monkeypatch.setattr(
        github.gitops, "remote_url",
        lambda _r, _name: "git@gitlab.com:owner/repo.git",
    )
    assert github.autodetect_repo(tmp_path) is None


# ── is_configured ────────────────────────────────────────────────────


def test_is_configured_requires_repo_triggers_and_token(tmp_path, monkeypatch):
    monkeypatch.setattr(github, "_gh_cli_token", lambda: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    brr_dir = tmp_path / ".brr"

    # Empty state — not configured.
    assert github.is_configured(brr_dir) is False

    # Token only — still not configured.
    github._save_state(brr_dir, {"token": "x"})
    assert github.is_configured(brr_dir) is False

    # Token + repo, no triggers — still not configured.
    github._save_state(brr_dir, {"token": "x", "repo": "o/r"})
    assert github.is_configured(brr_dir) is False

    # Token + repo + at least one trigger — configured.
    github._save_state(brr_dir, {
        "token": "x", "repo": "o/r", "triggers": {"label": "brr"},
    })
    assert github.is_configured(brr_dir) is True


# ── label trigger ───────────────────────────────────────────────────


def test_label_trigger_creates_event_for_new_labelled_issue(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    github._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"label": "brr"},
    })

    api_calls = []

    def fake_api_get(token, path, params=None):
        api_calls.append((path, params))
        if path == "/repos/owner/name/issues":
            return [
                {
                    "number": 42,
                    "title": "fix the auth tests",
                    "body": "they fail in CI",
                    "user": {"login": "octocat"},
                    "html_url": "https://github.com/owner/name/issues/42",
                    "updated_at": "2026-05-15T10:00:00Z",
                },
            ]
        return []

    monkeypatch.setattr(github, "_api_get", fake_api_get)

    github._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    ev = events[0]
    assert ev["source"] == "github"
    assert ev["github_kind"] == "issue"
    assert ev["github_issue_number"] == 42
    assert ev["github_repo"] == "owner/name"
    assert ev["github_trigger"] == "label"
    assert ev["github_label"] == "brr"
    assert "fix the auth tests" in ev["body"]

    # Cursor advanced; second poll on the same issue does not re-create.
    github._loop_once(brr_dir, inbox, responses)
    assert len(protocol.list_pending(inbox)) == 1


def test_label_trigger_skips_pull_requests(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    github._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"label": "brr"},
    })

    monkeypatch.setattr(github, "_api_get", lambda token, path, params=None: [
        {
            "number": 7,
            "title": "PR title",
            "user": {"login": "octocat"},
            "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/7"},
            "updated_at": "2026-05-15T10:00:00Z",
        },
    ])

    github._loop_once(brr_dir, inbox, responses)
    assert protocol.list_pending(inbox) == []


# ── mention trigger ─────────────────────────────────────────────────


def test_mention_trigger_creates_event_for_pr_comment_with_branch_target(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    github._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    def fake_api_get(token, path, params=None):
        if path == "/repos/owner/name/issues/comments":
            return [
                {
                    "id": 999,
                    "body": "@brr-bot please fix the failing test",
                    "user": {"login": "alice"},
                    "issue_url": "https://api.github.com/repos/owner/name/issues/123",
                    "html_url": "https://github.com/owner/name/pull/123#issuecomment-999",
                    "updated_at": "2026-05-15T11:00:00Z",
                },
            ]
        if path == "/repos/owner/name/pulls/123":
            return {"head": {"ref": "feature-x"}}
        return []

    monkeypatch.setattr(github, "_api_get", fake_api_get)

    github._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    ev = events[0]
    assert ev["github_kind"] == "pr-comment"
    assert ev["github_issue_number"] == 123
    assert ev["github_pr_number"] == 123
    assert ev["github_comment_id"] == 999
    # Critical: PR head branch flows through to branch_target so the
    # daemon's pre-task fetch+ff hook can refresh it.
    assert ev["branch_target"] == "feature-x"
    assert "@brr-bot please fix" in ev["body"]


def test_mention_trigger_handles_issue_comment_without_branch_target(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    github._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"mention": "@brr-bot"},
    })

    monkeypatch.setattr(github, "_api_get", lambda token, path, params=None: [
        {
            "id": 1,
            "body": "@brr-bot triage this please",
            "user": {"login": "bob"},
            "issue_url": "https://api.github.com/repos/o/r/issues/5",
            "html_url": "https://github.com/o/r/issues/5#issuecomment-1",
            "updated_at": "2026-05-15T12:00:00Z",
        },
    ])

    github._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    ev = events[0]
    assert ev["github_kind"] == "issue-comment"
    assert "branch_target" not in ev
    assert "github_pr_number" not in ev


def test_mention_trigger_ignores_bot_own_comments(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    github._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"mention": "@brr-bot"},
    })

    monkeypatch.setattr(github, "_api_get", lambda token, path, params=None: [
        {
            "id": 2,
            "body": "@brr-bot <- echoed in the bot's own reply",
            "user": {"login": "brr-bot"},
            "issue_url": "https://api.github.com/repos/o/r/issues/9",
            "html_url": "https://github.com/o/r/issues/9#issuecomment-2",
            "updated_at": "2026-05-15T12:00:00Z",
        },
    ])

    github._loop_once(brr_dir, inbox, responses)
    assert protocol.list_pending(inbox) == []


def test_mention_trigger_skips_comments_without_mention(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    github._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"mention": "@brr-bot"},
    })

    monkeypatch.setattr(github, "_api_get", lambda token, path, params=None: [
        {
            "id": 3,
            "body": "ordinary comment, no mention",
            "user": {"login": "bob"},
            "issue_url": "https://api.github.com/repos/o/r/issues/5",
            "html_url": "https://github.com/o/r/issues/5#issuecomment-3",
            "updated_at": "2026-05-15T12:00:00Z",
        },
    ])

    github._loop_once(brr_dir, inbox, responses)
    assert protocol.list_pending(inbox) == []


# ── cursor advancement ─────────────────────────────────────────────


def test_polling_cursor_advances_across_iterations(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    github._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"label": "brr"},
        # Pin the starting cursor so the test doesn't depend on
        # wall-clock-derived initial lookback.
        "cursor": {"issues_since": "2026-01-01T00:00:00Z"},
    })

    captured_since: list[str | None] = []

    def fake_api_get(token, path, params=None):
        captured_since.append((params or {}).get("since"))
        if path == "/repos/o/r/issues":
            return [
                {
                    "number": 1,
                    "title": "first",
                    "user": {"login": "u"},
                    "html_url": "https://github.com/o/r/issues/1",
                    "updated_at": "2026-05-15T09:00:00Z",
                },
            ]
        return []

    monkeypatch.setattr(github, "_api_get", fake_api_get)

    github._loop_once(brr_dir, inbox, responses)
    state = github._load_state(brr_dir)
    assert state["cursor"]["issues_since"] == "2026-05-15T09:00:00Z"
    assert 1 in state["cursor"]["seen_issue_numbers"]

    github._loop_once(brr_dir, inbox, responses)
    # Second call uses the advanced cursor.
    assert captured_since[-1] == "2026-05-15T09:00:00Z"


# ── response delivery ─────────────────────────────────────────────


def test_response_posts_comment_to_originating_thread(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    github._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"label": "brr"},
    })

    event_path = protocol.create_event(
        inbox,
        source="github",
        body="placeholder",
        github_repo="owner/name",
        github_kind="issue",
        github_issue_number=42,
    )
    event = protocol.list_pending(inbox)[0]
    protocol.set_status(event, "done")
    protocol.write_response(responses, event["id"], "the answer")

    posts: list[tuple[str, dict]] = []

    def fake_api_post(token, path, body):
        posts.append((path, body))

    monkeypatch.setattr(github, "_api_post", fake_api_post)

    github._deliver_responses(brr_dir, inbox, responses, "secret")

    assert posts == [("/repos/owner/name/issues/42/comments", {"body": "the answer"})]
    assert not event_path.exists()


# ── error handling ────────────────────────────────────────────────


def test_4xx_marks_backoff_long(monkeypatch):
    err = github.GitHubAPIError(404, "Not Found", headers={})

    sleep_seconds = github._handle_api_error(err)

    # 4xx is non-transient; we sleep at least the floor.
    assert sleep_seconds == github._BACKOFF_MAX


def test_rate_limit_response_sleeps_until_reset(monkeypatch):
    import time as _time

    monkeypatch.setattr(_time, "time", lambda: 1_000_000)
    err = github.GitHubAPIError(
        403, "API rate limit exceeded",
        headers={
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": str(1_000_120),
        },
    )

    sleep_seconds = github._handle_api_error(err)

    assert sleep_seconds == 120


def test_retry_after_header_overrides_other_signals(monkeypatch):
    err = github.GitHubAPIError(
        429, "secondary rate limit",
        headers={
            "Retry-After": "45",
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": "1",
        },
    )

    sleep_seconds = github._handle_api_error(err)

    assert sleep_seconds == 45


def test_loop_once_noop_when_unconfigured(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    # No state at all.
    sleep_for = github._loop_once(brr_dir, inbox, responses)
    assert sleep_for == github._POLL_INTERVAL
    assert protocol.list_pending(inbox) == []


def test_extract_issue_number():
    assert github._extract_issue_number(
        "https://api.github.com/repos/o/r/issues/42",
    ) == 42
    assert github._extract_issue_number("") is None
    assert github._extract_issue_number("not a url") is None


def test_format_event_body_combines_title_and_body():
    out = github._format_event_body("Fix bug", "Steps to reproduce…")
    assert out.startswith("# Fix bug\n\nSteps to reproduce")
    assert github._format_event_body("title only", "") == "# title only\n"
    assert github._format_event_body("", "body only") == "body only\n"
