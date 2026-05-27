"""Tests for the GitHub gate.

The gate ships as a Python package (``brr.gates.github``) split into
focused submodules. Tests patch on the submodule where each helper
lives so monkeypatch replaces the symbol every caller resolves at
runtime (Python looks up module globals at call time):

- ``client``     — HTTP transport, ``_request`` / ``_api_get`` / ``_api_post`` / ``_api_patch``
- ``state``      — JSON state, token resolution, ``_validate_token``
- ``setup``      — interactive ``auth`` / ``bind`` / ``setup``, ``autodetect_repo``
- ``parse``      — pure parsers (URL, JSON, mention filtering, body formatters)
- ``polling``    — the per-trigger pollers
- ``delivery``   — response-post + branch-footer + thread-reply
- ``progress``   — live progress card (``render_update``)
- ``loop``       — ``_loop_once`` / ``_handle_api_error`` / ``run_loop``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import protocol
from brr import run_progress
from brr.gates import github
from brr.gates.github import (
    cache,
    client,
    constants,
    delivery,
    loop,
    parse,
    paths,
    polling,
    progress,
    state,
    wizard,
)
from brr.task import Task


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


# ── paths ─────────────────────────────────────────────────────────────


def test_path_builders_match_documented_endpoints():
    """Path builders are the wire contract reused by brnrd; lock them down."""
    assert paths.user() == "/user"
    assert paths.repo_issues("o/r") == "/repos/o/r/issues"
    assert paths.repo_issue_comments("o/r") == "/repos/o/r/issues/comments"
    assert paths.repo_pulls_comments("o/r") == "/repos/o/r/pulls/comments"
    assert paths.pull("o/r", 62) == "/repos/o/r/pulls/62"
    assert paths.issue_comments("o/r", 7) == "/repos/o/r/issues/7/comments"
    assert paths.issue_comment("o/r", 555) == "/repos/o/r/issues/comments/555"
    assert (
        paths.pull_comment_replies("o/r", 62, 999)
        == "/repos/o/r/pulls/62/comments/999/replies"
    )


# ── token resolution ────────────────────────────────────────────────


def test_resolve_token_prefers_stored(monkeypatch):
    monkeypatch.setattr(state, "_gh_cli_token", lambda: "from-gh")
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")

    assert github.resolve_token({"token": "stored-token"}) == "stored-token"


def test_resolve_token_falls_back_to_gh_cli(monkeypatch):
    monkeypatch.setattr(state, "_gh_cli_token", lambda: "gh-cli-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    assert github.resolve_token({}) == "gh-cli-token"


def test_resolve_token_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(state, "_gh_cli_token", lambda: None)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    assert github.resolve_token({}) == "env-token"


def test_resolve_token_returns_none_when_nothing(monkeypatch):
    monkeypatch.setattr(state, "_gh_cli_token", lambda: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    assert github.resolve_token({}) is None


def test_auth_prompts_when_no_token_source(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(state, "_gh_cli_token", lambda: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "pasted-token")
    monkeypatch.setattr(state, "_validate_token", lambda _t: "octocat")
    brr_dir = tmp_path / ".brr"

    github.auth(brr_dir)

    saved = state._load_state(brr_dir)
    assert saved["token"] == "pasted-token"
    assert saved["bot_login"] == "octocat"
    assert saved["token_source"] == "stored"


def test_auth_uses_gh_cli_token_without_storing(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_gh_cli_token", lambda: "gh-cli-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(state, "_validate_token", lambda _t: "octocat")
    brr_dir = tmp_path / ".brr"

    github.auth(brr_dir)

    saved = state._load_state(brr_dir)
    assert "token" not in saved, "gh CLI tokens must not be persisted"
    assert saved["bot_login"] == "octocat"
    assert saved["token_source"] == "gh-cli"


# ── HTTP helper ─────────────────────────────────────────────────────


class _FakeGitHubResponse:
    def __init__(
        self,
        status_code: int,
        payload: object | None,
        *,
        headers: dict[str, str] | None = None,
        text: str = "",
        reason: str = "",
    ):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text
        self.reason = reason
        self.content = b"x" if payload is not None else b""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_request_uses_requests_params_json_and_headers(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeGitHubResponse(200, {"ok": True}, headers={"X-RateLimit-Remaining": "1"})

    monkeypatch.setattr(client.requests, "request", fake_request)

    payload, headers = client._request(
        "secret",
        "POST",
        "/repos/o/r/issues",
        params={"state": "open", "cursor": "", "none": None},
        body={"title": "hello"},
    )

    assert payload == {"ok": True}
    assert headers == {"X-RateLimit-Remaining": "1"}
    assert len(calls) == 1
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert url == "https://api.github.com/repos/o/r/issues"
    assert kwargs["params"] == {"state": "open"}
    assert kwargs["json"] == {"title": "hello"}
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["headers"]["Accept"] == "application/vnd.github+json"
    assert kwargs["timeout"] == constants._HTTP_TIMEOUT


def test_request_sends_if_none_match_when_etag_cached(monkeypatch):
    """Conditional GET: a previously-cached ETag is sent as If-None-Match."""
    seen_headers: list[dict[str, str]] = []

    def fake_request(method, url, **kwargs):
        seen_headers.append(dict(kwargs["headers"]))
        return _FakeGitHubResponse(
            200, [{"ok": True}], headers={"ETag": "W/\"new-etag\""},
        )

    monkeypatch.setattr(client.requests, "request", fake_request)

    store: dict[str, str] = {"GET /repos/o/r/issues/comments": "W/\"old-etag\""}
    payload, headers = client._request(
        "tok", "GET", "/repos/o/r/issues/comments",
        params={"since": "T0"}, etag_store=store,
    )

    assert payload == [{"ok": True}]
    assert seen_headers[0].get("If-None-Match") == "W/\"old-etag\""
    # ETag from the response replaces the cached one in place.
    assert store["GET /repos/o/r/issues/comments"] == "W/\"new-etag\""


def test_request_304_returns_none_and_preserves_cached_etag(monkeypatch):
    """A 304 means the resource is unchanged; the body is None and we don't
    overwrite the cached ETag with whatever the 304 response carries."""
    def fake_request(method, url, **kwargs):
        return _FakeGitHubResponse(304, None, headers={"ETag": "W/\"old-etag\""})

    monkeypatch.setattr(client.requests, "request", fake_request)

    store = {"GET /repos/o/r/issues/comments": "W/\"old-etag\""}
    payload, headers = client._request(
        "tok", "GET", "/repos/o/r/issues/comments", etag_store=store,
    )

    assert payload is None
    assert store["GET /repos/o/r/issues/comments"] == "W/\"old-etag\""


def test_request_without_etag_store_does_not_send_if_none_match(monkeypatch):
    seen_headers: list[dict[str, str]] = []

    def fake_request(method, url, **kwargs):
        seen_headers.append(dict(kwargs["headers"]))
        return _FakeGitHubResponse(200, [], headers={"ETag": "W/\"x\""})

    monkeypatch.setattr(client.requests, "request", fake_request)

    client._request("tok", "GET", "/repos/o/r/issues/comments")

    assert "If-None-Match" not in seen_headers[0]


def test_polling_threads_etag_through_cursor(tmp_path, monkeypatch):
    """The mention poller persists ETags across loop iterations so quiet
    repos stop spending rate-limit budget on identical responses."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"mention": "@brr-bot"},
        "cursor": {
            "comments_since": "2026-01-01T00:00:00Z",
            "review_comments_since": "2026-01-01T00:00:00Z",
        },
    })

    seen_if_none_match: list[str | None] = []
    call_count = {"n": 0}

    def fake_request(method, url, **kwargs):
        seen_if_none_match.append(kwargs["headers"].get("If-None-Match"))
        call_count["n"] += 1
        # First call returns 200 with an ETag, sets the cache.
        if call_count["n"] == 1:
            return _FakeGitHubResponse(
                200, [], headers={"ETag": "W/\"e-comments\""},
            )
        # Second call returns 304 — nothing changed since the cached ETag.
        return _FakeGitHubResponse(304, None, headers={"ETag": "W/\"e-comments\""})

    monkeypatch.setattr(client.requests, "request", fake_request)

    loop._loop_once(brr_dir, inbox, responses)
    saved = state._load_state(brr_dir)
    assert "etags" in saved["cursor"]
    assert saved["cursor"]["etags"].get("GET /repos/o/r/issues/comments") == "W/\"e-comments\""

    # Second pass: the previously-stored ETag is sent as If-None-Match.
    loop._loop_once(brr_dir, inbox, responses)
    issue_comments_calls = [
        h for h, url in zip(seen_if_none_match, range(call_count["n"]))
        if h is not None
    ]
    assert "W/\"e-comments\"" in issue_comments_calls, (
        f"expected the cached ETag in subsequent If-None-Match headers, "
        f"got {seen_if_none_match!r}"
    )
    # 304 means no new events.
    assert protocol.list_pending(inbox) == []


def test_request_error_uses_github_json_message(monkeypatch):
    def fake_request(method, url, **kwargs):
        return _FakeGitHubResponse(
            403,
            {"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0"},
            text='{"message":"API rate limit exceeded"}',
        )

    monkeypatch.setattr(client.requests, "request", fake_request)

    with pytest.raises(github.GitHubAPIError) as caught:
        client._request("secret", "GET", "/rate_limit")

    assert caught.value.status == 403
    assert caught.value.message == "API rate limit exceeded"
    assert caught.value.headers == {"x-ratelimit-remaining": "0"}


# ── repo autodetect ────────────────────────────────────────────────


def test_autodetect_repo_from_origin_https(tmp_path, monkeypatch):
    monkeypatch.setattr(wizard.gitops, "default_remote", lambda _r: "origin")
    monkeypatch.setattr(
        wizard.gitops, "remote_url",
        lambda _r, _name: "https://github.com/Gurio/brr.git",
    )
    assert github.autodetect_repo(tmp_path) == "Gurio/brr"


def test_autodetect_repo_from_origin_ssh(tmp_path, monkeypatch):
    monkeypatch.setattr(wizard.gitops, "default_remote", lambda _r: "origin")
    monkeypatch.setattr(
        wizard.gitops, "remote_url",
        lambda _r, _name: "git@github.com:Gurio/brr.git",
    )
    assert github.autodetect_repo(tmp_path) == "Gurio/brr"


@pytest.mark.parametrize("url, expected", [
    ("https://api.github.com/repos/o/r/issues/42", 42),
    ("https://api.github.com/repos/o/r/pulls/62", 62),
    ("https://api.github.com/repos/o/r/pulls/62/comments", 62),
    ("", None),
])
def test_extract_issue_and_pr_numbers(url, expected):
    if "/issues/" in url:
        assert parse._extract_issue_number(url) == expected
    else:
        assert parse._extract_pr_number(url) == expected


def test_autodetect_repo_returns_none_for_non_github(tmp_path, monkeypatch):
    monkeypatch.setattr(wizard.gitops, "default_remote", lambda _r: "origin")
    monkeypatch.setattr(
        wizard.gitops, "remote_url",
        lambda _r, _name: "git@gitlab.com:owner/repo.git",
    )
    assert github.autodetect_repo(tmp_path) is None


# ── is_configured ────────────────────────────────────────────────────


def test_is_configured_requires_repo_triggers_and_token(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_gh_cli_token", lambda: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    brr_dir = tmp_path / ".brr"

    # Empty state — not configured.
    assert github.is_configured(brr_dir) is False

    # Token only — still not configured.
    state._save_state(brr_dir, {"token": "x"})
    assert github.is_configured(brr_dir) is False

    # Token + repo, no triggers — still not configured.
    state._save_state(brr_dir, {"token": "x", "repo": "o/r"})
    assert github.is_configured(brr_dir) is False

    # Token + repo + at least one trigger — configured.
    state._save_state(brr_dir, {
        "token": "x", "repo": "o/r", "triggers": {"label": "brr"},
    })
    assert github.is_configured(brr_dir) is True


# ── label trigger ───────────────────────────────────────────────────


def test_label_trigger_creates_event_for_new_labelled_issue(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"label": "brr"},
    })

    api_calls = []

    def fake_api_get(token, path, params=None, **kwargs):
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

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)

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
    loop._loop_once(brr_dir, inbox, responses)
    assert len(protocol.list_pending(inbox)) == 1


def test_label_trigger_skips_pull_requests(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"label": "brr"},
    })

    monkeypatch.setattr(client, "_api_get", lambda token, path, params=None, **kwargs: [
        {
            "number": 7,
            "title": "PR title",
            "user": {"login": "octocat"},
            "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/7"},
            "updated_at": "2026-05-15T10:00:00Z",
        },
    ])

    loop._loop_once(brr_dir, inbox, responses)
    assert protocol.list_pending(inbox) == []


# ── mention trigger ─────────────────────────────────────────────────


def test_mention_trigger_creates_event_for_pr_comment_with_branch_target(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    def fake_api_get(token, path, params=None, **kwargs):
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

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)

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
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"mention": "@brr-bot"},
    })

    monkeypatch.setattr(client, "_api_get", lambda token, path, params=None, **kwargs: [
        {
            "id": 1,
            "body": "@brr-bot triage this please",
            "user": {"login": "bob"},
            "issue_url": "https://api.github.com/repos/o/r/issues/5",
            "html_url": "https://github.com/o/r/issues/5#issuecomment-1",
            "updated_at": "2026-05-15T12:00:00Z",
        },
    ])

    loop._loop_once(brr_dir, inbox, responses)

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
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"mention": "@brr-bot"},
    })

    monkeypatch.setattr(client, "_api_get", lambda token, path, params=None, **kwargs: [
        {
            "id": 2,
            "body": "@brr-bot <- echoed in the bot's own reply",
            "user": {"login": "brr-bot"},
            "issue_url": "https://api.github.com/repos/o/r/issues/9",
            "html_url": "https://github.com/o/r/issues/9#issuecomment-2",
            "updated_at": "2026-05-15T12:00:00Z",
        },
    ])

    loop._loop_once(brr_dir, inbox, responses)
    assert protocol.list_pending(inbox) == []


def test_mention_trigger_pat_holder_can_mention_automation_account(
    tmp_path, monkeypatch,
):
    """``bot_login`` is the token owner; @-triggers name the account to ignore."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "Gurio",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    def fake_api_get(token, path, params=None, **kwargs):
        if path == "/repos/owner/name/issues/comments":
            return [
                {
                    "id": 14,
                    "body": "@brr-bot could you follow up on this PR",
                    "user": {"login": "Gurio"},
                    "issue_url": "https://api.github.com/repos/owner/name/issues/14",
                    "html_url": "https://github.com/owner/name/pull/14#issuecomment-14",
                    "updated_at": "2026-05-17T12:00:00Z",
                },
            ]
        if path == "/repos/owner/name/pulls/14":
            return {"head": {"ref": "brr/runner-ergonomics-review"}}
        return []

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    assert events[0]["github_trigger"] == "mention"
    assert events[0]["branch_target"] == "brr/runner-ergonomics-review"


def test_mention_trigger_emits_pr_review_when_summary_mentions(tmp_path, monkeypatch):
    """A PR review with a body that @-mentions us produces a pr-review event,
    discovered via line-comment polling (each line comment carries the
    parent ``pull_request_review_id``)."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    def fake_api_get(token, path, params=None, **kwargs):
        if path == "/repos/owner/name/issues/comments":
            return []
        if path == "/repos/owner/name/pulls/comments":
            return [
                {
                    "id": 9001,
                    "body": "nit: rename variable",  # no mention here
                    "user": {"login": "alice"},
                    "pull_request_review_id": 555,
                    "pull_request_url": "https://api.github.com/repos/owner/name/pulls/62",
                    "html_url": "https://github.com/owner/name/pull/62#discussion_r9001",
                    "path": "src/x.py",
                    "line": 10,
                    "updated_at": "2026-05-27T12:20:00Z",
                },
            ]
        if path == "/repos/owner/name/pulls/62/reviews/555":
            return {
                "id": 555,
                "state": "CHANGES_REQUESTED",
                "body": "@brr-bot please address these comments",
                "user": {"login": "alice"},
                "html_url": "https://github.com/owner/name/pull/62#pullrequestreview-555",
            }
        if path == "/repos/owner/name/pulls/62":
            return {"head": {"ref": "feature-review-summary"}}
        return []

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    # Two events: one for the line comment (no mention so skipped),
    # plus one for the review summary (which does mention).
    # Actually only the review event because the line comment lacks the mention.
    review_events = [e for e in events if e.get("github_kind") == "pr-review"]
    assert len(review_events) == 1
    ev = review_events[0]
    assert ev["github_pr_number"] == 62
    assert ev["github_review_id"] == 555
    assert ev["github_review_state"] == "CHANGES_REQUESTED"
    assert ev["branch_target"] == "feature-review-summary"
    assert "@brr-bot please address" in ev["body"]


def test_mention_trigger_skips_pr_review_without_mention(tmp_path, monkeypatch):
    """A review whose body doesn't @-mention us is fetched but discarded;
    no event is emitted for it."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    def fake_api_get(token, path, params=None, **kwargs):
        if path == "/repos/owner/name/issues/comments":
            return []
        if path == "/repos/owner/name/pulls/comments":
            return [
                {
                    "id": 9100,
                    "body": "regular line note",
                    "user": {"login": "alice"},
                    "pull_request_review_id": 700,
                    "pull_request_url": "https://api.github.com/repos/owner/name/pulls/63",
                    "html_url": "https://github.com/owner/name/pull/63#discussion_r9100",
                    "updated_at": "2026-05-27T12:25:00Z",
                },
            ]
        if path == "/repos/owner/name/pulls/63/reviews/700":
            return {
                "id": 700,
                "state": "COMMENTED",
                "body": "Looks fine overall, some nits inline.",
                "user": {"login": "alice"},
                "html_url": "https://github.com/owner/name/pull/63#pullrequestreview-700",
            }
        return []

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert [e for e in events if e.get("github_kind") == "pr-review"] == []


def test_mention_trigger_dedupes_reviews_across_polls(tmp_path, monkeypatch):
    """A review surfaced by multiple line comments is fetched once;
    persisted ``seen_review_ids`` prevents refetching across loop passes."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    review_fetches: list[str] = []

    def fake_api_get(token, path, params=None, **kwargs):
        if path == "/repos/owner/name/issues/comments":
            return []
        if path == "/repos/owner/name/pulls/comments":
            return [
                # Two line comments both belonging to the same review.
                {
                    "id": 1, "body": "x", "user": {"login": "alice"},
                    "pull_request_review_id": 42,
                    "pull_request_url": "https://api.github.com/repos/owner/name/pulls/64",
                    "html_url": "https://github.com/owner/name/pull/64#discussion_r1",
                    "updated_at": "2026-05-27T12:30:00Z",
                },
                {
                    "id": 2, "body": "y", "user": {"login": "alice"},
                    "pull_request_review_id": 42,
                    "pull_request_url": "https://api.github.com/repos/owner/name/pulls/64",
                    "html_url": "https://github.com/owner/name/pull/64#discussion_r2",
                    "updated_at": "2026-05-27T12:31:00Z",
                },
            ]
        if path == "/repos/owner/name/pulls/64/reviews/42":
            review_fetches.append(path)
            return {
                "id": 42, "state": "APPROVED",
                "body": "@brr-bot looks good",
                "user": {"login": "alice"},
                "html_url": "https://github.com/owner/name/pull/64#pullrequestreview-42",
            }
        return []

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)
    assert len(review_fetches) == 1, "review must be fetched once within a single poll"

    # Second loop pass — same line comments still in the API response,
    # but the cursor has advanced and seen_review_ids dedupes the fetch.
    loop._loop_once(brr_dir, inbox, responses)
    assert len(review_fetches) == 1, "review must not be refetched across polls"


def test_deliver_pr_review_response_posts_to_pr_thread_with_quote(
    tmp_path, monkeypatch,
):
    """pr-review replies go to the PR's top-level comment endpoint with a
    quote pointer back at the review (no dedicated review-reply endpoint
    exists for the summary body)."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    protocol.create_event(
        inbox,
        source="github",
        body="@brr-bot please address these comments",
        github_repo="owner/name",
        github_kind="pr-review",
        github_issue_number=62,
        github_pr_number=62,
        github_review_id=555,
        github_review_state="CHANGES_REQUESTED",
        github_author="alice",
        github_html_url="https://github.com/owner/name/pull/62#pullrequestreview-555",
        github_trigger="mention",
    )
    event = protocol.list_pending(inbox)[0]
    protocol.set_status(event, "done")
    protocol.write_response(responses, event["id"], "On it — will push fixes shortly.")

    posts: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        client, "_api_post",
        lambda token, path, body: posts.append((path, body)),
    )

    delivery._deliver_responses(brr_dir, inbox, responses, "secret")

    assert len(posts) == 1
    path, payload = posts[0]
    assert path == "/repos/owner/name/issues/62/comments"
    text = payload["body"]
    assert text.startswith(
        "> Replying to [@alice's comment]"
        "(https://github.com/owner/name/pull/62#pullrequestreview-555)"
    )
    assert text.endswith("On it — will push fixes shortly.")


def test_mention_trigger_creates_event_for_pr_review_comment(tmp_path, monkeypatch):
    """Inline PR review comments live on /pulls/comments, not /issues/comments."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    def fake_api_get(token, path, params=None, **kwargs):
        if path == "/repos/owner/name/issues/comments":
            return []
        if path == "/repos/owner/name/pulls/comments":
            return [
                {
                    "id": 3309624726,
                    "body": "@brr-bot is this link wrong?",
                    "user": {"login": "alice"},
                    "pull_request_url": "https://api.github.com/repos/owner/name/pulls/62",
                    "html_url": "https://github.com/owner/name/pull/62#discussion_r3309624726",
                    "path": "kb/plan.md",
                    "line": 42,
                    "updated_at": "2026-05-27T12:20:00Z",
                },
            ]
        if path == "/repos/owner/name/pulls/62":
            return {"head": {"ref": "feature-review"}}
        return []

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    ev = events[0]
    assert ev["github_kind"] == "pr-review-comment"
    assert ev["github_issue_number"] == 62
    assert ev["github_pr_number"] == 62
    assert ev["github_comment_id"] == 3309624726
    assert ev["github_path"] == "kb/plan.md"
    assert ev["github_line"] == 42
    assert ev["branch_target"] == "feature-review"
    assert "On `kb/plan.md` line 42:" in ev["body"]
    assert "@brr-bot is this link wrong?" in ev["body"]


def test_mention_trigger_skips_comments_without_mention(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"mention": "@brr-bot"},
    })

    monkeypatch.setattr(client, "_api_get", lambda token, path, params=None, **kwargs: [
        {
            "id": 3,
            "body": "ordinary comment, no mention",
            "user": {"login": "bob"},
            "issue_url": "https://api.github.com/repos/o/r/issues/5",
            "html_url": "https://github.com/o/r/issues/5#issuecomment-3",
            "updated_at": "2026-05-15T12:00:00Z",
        },
    ])

    loop._loop_once(brr_dir, inbox, responses)
    assert protocol.list_pending(inbox) == []


# ── cursor advancement ─────────────────────────────────────────────


def test_polling_cursor_advances_across_iterations(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"label": "brr"},
        # Pin the starting cursor so the test doesn't depend on
        # wall-clock-derived initial lookback.
        "cursor": {"issues_since": "2026-01-01T00:00:00Z"},
    })

    captured_since: list[str | None] = []

    def fake_api_get(token, path, params=None, **kwargs):
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

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)
    saved = state._load_state(brr_dir)
    assert saved["cursor"]["issues_since"] == "2026-05-15T09:00:00Z"
    assert 1 in saved["cursor"]["seen_issue_numbers"]

    loop._loop_once(brr_dir, inbox, responses)
    # Second call uses the advanced cursor.
    assert captured_since[-1] == "2026-05-15T09:00:00Z"


# ── response delivery ─────────────────────────────────────────────


def test_response_posts_comment_to_originating_thread(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
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

    monkeypatch.setattr(client, "_api_post", fake_api_post)

    delivery._deliver_responses(brr_dir, inbox, responses, "secret")

    # Label-trigger events: the issue itself is the source, so the
    # response body lands verbatim — no quote preface needed (and one
    # would just point the comment back at its own issue).
    assert posts == [("/repos/owner/name/issues/42/comments", {"body": "the answer"})]
    assert not event_path.exists()


def test_response_to_mention_quotes_source_comment(tmp_path, monkeypatch):
    """Mention-triggered replies prepend a quote pointer at the source."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    protocol.create_event(
        inbox,
        source="github",
        body="@brr-bot please fix",
        github_repo="owner/name",
        github_kind="pr-comment",
        github_issue_number=7,
        github_pr_number=7,
        github_comment_id=12345,
        github_author="alice",
        github_html_url="https://github.com/owner/name/pull/7#issuecomment-12345",
        github_trigger="mention",
    )
    event = protocol.list_pending(inbox)[0]
    protocol.set_status(event, "done")
    protocol.write_response(responses, event["id"], "Done — pushed to feature-x.")

    posts: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        client, "_api_post",
        lambda token, path, body: posts.append((path, body)),
    )

    delivery._deliver_responses(brr_dir, inbox, responses, "secret")

    assert len(posts) == 1
    path, body = posts[0]
    assert path == "/repos/owner/name/issues/7/comments"
    text = body["body"]
    assert text.startswith(
        "> Replying to [@alice's comment]"
        "(https://github.com/owner/name/pull/7#issuecomment-12345)"
    )
    assert text.endswith("Done — pushed to feature-x.")


def test_response_to_pr_review_comment_replies_in_thread(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
    })

    protocol.create_event(
        inbox,
        source="github",
        body="@brr-bot check this hunk",
        github_repo="owner/name",
        github_kind="pr-review-comment",
        github_issue_number=62,
        github_pr_number=62,
        github_comment_id=3309624726,
        github_author="alice",
        github_html_url="https://github.com/owner/name/pull/62#discussion_r3309624726",
        github_trigger="mention",
    )
    event = protocol.list_pending(inbox)[0]
    protocol.set_status(event, "done")
    protocol.write_response(responses, event["id"], "Looks correct to me.")

    posts: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        client, "_api_post",
        lambda token, path, body: posts.append((path, body)),
    )

    delivery._deliver_responses(brr_dir, inbox, responses, "secret")

    assert len(posts) == 1
    path, payload = posts[0]
    assert path == "/repos/owner/name/pulls/62/comments/3309624726/replies"
    assert "Looks correct to me." in payload["body"]
    assert "discussion_r3309624726" in payload["body"]


def test_response_to_mention_falls_back_when_author_missing(tmp_path, monkeypatch):
    # Comments without a resolved author still get the quote pointer,
    # just without the @-handle (rare but possible for deleted users).
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"mention": "@brr-bot"},
    })

    protocol.create_event(
        inbox, source="github", body="@brr-bot",
        github_repo="o/r",
        github_kind="issue-comment",
        github_issue_number=3,
        github_comment_id=77,
        github_html_url="https://github.com/o/r/issues/3#issuecomment-77",
        github_trigger="mention",
    )
    event = protocol.list_pending(inbox)[0]
    protocol.set_status(event, "done")
    protocol.write_response(responses, event["id"], "ack")

    posts: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        client, "_api_post",
        lambda token, path, body: posts.append((path, body)),
    )

    delivery._deliver_responses(brr_dir, inbox, responses, "secret")

    text = posts[0][1]["body"]
    assert text.startswith(
        "> Replying to [the source comment]"
        "(https://github.com/o/r/issues/3#issuecomment-77)"
    )


# ── error handling ────────────────────────────────────────────────


def test_4xx_marks_backoff_long(monkeypatch):
    err = github.GitHubAPIError(404, "Not Found", headers={})

    sleep_seconds = loop._handle_api_error(err)

    # 4xx is non-transient; we sleep at least the floor.
    assert sleep_seconds == constants._BACKOFF_MAX


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

    sleep_seconds = loop._handle_api_error(err)

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

    sleep_seconds = loop._handle_api_error(err)

    assert sleep_seconds == 45


def test_loop_once_noop_when_unconfigured(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    # No state at all.
    sleep_for = loop._loop_once(brr_dir, inbox, responses)
    assert sleep_for == constants._POLL_INTERVAL
    assert protocol.list_pending(inbox) == []


def test_extract_issue_number():
    assert parse._extract_issue_number(
        "https://api.github.com/repos/o/r/issues/42",
    ) == 42
    assert parse._extract_issue_number("") is None
    assert parse._extract_issue_number("not a url") is None


def test_format_event_body_combines_title_and_body():
    out = parse._format_event_body("Fix bug", "Steps to reproduce…")
    assert out.startswith("# Fix bug\n\nSteps to reproduce")
    assert parse._format_event_body("title only", "") == "# title only\n"
    assert parse._format_event_body("", "body only") == "body only\n"


# ── bind() UX: _prompt_trigger and default handling ────────────────


def _make_inputs(*values):
    """Return an ``input`` replacement that yields ``values`` in order."""
    it = iter(values)
    return lambda _prompt: next(it)


def test_bind_enter_accepts_label_and_mention_defaults(tmp_path, monkeypatch):
    """Pressing Enter at each trigger prompt accepts the bracketed default."""
    brr_dir = tmp_path / ".brr"
    state._save_state(brr_dir, {"token": "t", "bot_login": "brr-bot"})
    monkeypatch.setattr(wizard, "autodetect_repo", lambda _: None)
    # Inputs: repo, any-prompt (Enter=skip), label (Enter=brr), mention (Enter=@brr-bot)
    monkeypatch.setattr("builtins.input", _make_inputs("owner/repo", "", "", ""))

    github.bind(brr_dir)

    saved = state._load_state(brr_dir)
    assert saved["triggers"] == {"label": "brr", "mention": "@brr-bot"}


def test_bind_off_disables_label(tmp_path, monkeypatch):
    """Typing 'off' at the label prompt removes the label trigger."""
    brr_dir = tmp_path / ".brr"
    state._save_state(brr_dir, {"token": "t", "bot_login": "b", "triggers": {"label": "brr"}})
    monkeypatch.setattr(wizard, "autodetect_repo", lambda _: None)
    # repo, any-skip, label=off, mention=Enter
    monkeypatch.setattr("builtins.input", _make_inputs("owner/repo", "", "off", ""))

    github.bind(brr_dir)

    saved = state._load_state(brr_dir)
    assert "label" not in saved["triggers"]
    assert saved["triggers"]["mention"] == "@brr-bot"


def test_bind_typed_value_overrides_default(tmp_path, monkeypatch):
    """Typing a custom label string uses that string, not the default."""
    brr_dir = tmp_path / ".brr"
    state._save_state(brr_dir, {"token": "t", "bot_login": "b"})
    monkeypatch.setattr(wizard, "autodetect_repo", lambda _: None)
    monkeypatch.setattr("builtins.input", _make_inputs("owner/repo", "", "my-label", "off"))

    github.bind(brr_dir)

    saved = state._load_state(brr_dir)
    assert saved["triggers"] == {"label": "my-label"}


def test_bind_any_trigger_saves_and_skips_label_mention_prompts(tmp_path, monkeypatch, capsys):
    """Enabling 'any' saves triggers={'any': True} and skips subsequent prompts."""
    brr_dir = tmp_path / ".brr"
    state._save_state(brr_dir, {"token": "t", "bot_login": "b"})
    monkeypatch.setattr(wizard, "autodetect_repo", lambda _: None)
    # repo, any=on — no further prompts expected
    inputs = _make_inputs("owner/repo", "on")
    monkeypatch.setattr("builtins.input", inputs)

    github.bind(brr_dir)

    saved = state._load_state(brr_dir)
    assert saved["triggers"] == {"any": True}
    assert "['any']" in capsys.readouterr().out


# ── any trigger pollers ───────────────────────────────────────────


def test_any_trigger_emits_issue_event(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"any": True},
        "cursor": {"any_issues_since": "2026-01-01T00:00:00Z"},
    })

    def fake_api_get(token, path, params=None, **kwargs):
        if path == "/repos/owner/name/issues":
            return [
                {
                    "number": 5,
                    "title": "A plain issue",
                    "body": "details here",
                    "user": {"login": "alice"},
                    "html_url": "https://github.com/owner/name/issues/5",
                    "updated_at": "2026-05-15T10:00:00Z",
                },
            ]
        return []

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    ev = events[0]
    assert ev["github_kind"] == "issue"
    assert ev["github_issue_number"] == 5
    assert ev["github_trigger"] == "any"
    assert "branch_target" not in ev


def test_any_trigger_emits_pr_event_with_branch_target(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"any": True},
        "cursor": {"any_issues_since": "2026-01-01T00:00:00Z"},
    })

    def fake_api_get(token, path, params=None, **kwargs):
        if path == "/repos/owner/name/issues":
            return [
                {
                    "number": 10,
                    "title": "My PR",
                    "body": "a change",
                    "user": {"login": "bob"},
                    "html_url": "https://github.com/owner/name/pull/10",
                    "pull_request": {"url": "https://api.github.com/repos/owner/name/pulls/10"},
                    "updated_at": "2026-05-15T11:00:00Z",
                },
            ]
        if path == "/repos/owner/name/pulls/10":
            return {"head": {"ref": "feature-y"}}
        return []

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    ev = events[0]
    assert ev["github_kind"] == "pr"
    assert ev["github_pr_number"] == 10
    assert ev["branch_target"] == "feature-y"
    assert ev["github_trigger"] == "any"


def test_any_trigger_emits_comment_events_skipping_bot(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"any": True},
        "cursor": {"any_comments_since": "2026-01-01T00:00:00Z"},
    })

    def fake_api_get(token, path, params=None, **kwargs):
        if path == "/repos/o/r/issues/comments":
            return [
                {
                    "id": 100,
                    "body": "human comment",
                    "user": {"login": "alice"},
                    "issue_url": "https://api.github.com/repos/o/r/issues/3",
                    "html_url": "https://github.com/o/r/issues/3#issuecomment-100",
                    "updated_at": "2026-05-15T12:00:00Z",
                },
                {
                    "id": 101,
                    "body": "bot's own reply — must be filtered",
                    "user": {"login": "brr-bot"},
                    "issue_url": "https://api.github.com/repos/o/r/issues/3",
                    "html_url": "https://github.com/o/r/issues/3#issuecomment-101",
                    "updated_at": "2026-05-15T12:01:00Z",
                },
            ]
        return []

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    loop._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    assert events[0]["github_comment_id"] == 100
    assert events[0]["github_kind"] == "issue-comment"
    assert events[0]["github_trigger"] == "any"


def test_any_trigger_overrides_label_and_mention_in_loop(tmp_path, monkeypatch):
    """When 'any' is set, label/mention pollers must not run."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "o/r",
        "triggers": {"any": True, "label": "brr", "mention": "@brr-bot"},
    })

    poll_any_calls: list[str] = []
    poll_label_calls: list[str] = []
    poll_mention_calls: list[str] = []

    monkeypatch.setattr(
        polling, "_poll_any_activity",
        lambda *a, **kw: poll_any_calls.append("any"),
    )
    monkeypatch.setattr(
        polling, "_poll_label_trigger",
        lambda *a, **kw: poll_label_calls.append("label"),
    )
    monkeypatch.setattr(
        polling, "_poll_mention_trigger",
        lambda *a, **kw: poll_mention_calls.append("mention"),
    )
    monkeypatch.setattr(delivery, "_deliver_responses", lambda *a, **kw: None)

    loop._loop_once(brr_dir, inbox, responses)

    assert poll_any_calls == ["any"]
    assert poll_label_calls == []
    assert poll_mention_calls == []


# ── render_update (progress cards) ───────────────────────────────────


def _make_packet(ptype: str, conv_key: str, task_id: str):
    """Minimal UpdatePacket-compatible object for render_update tests."""
    from types import SimpleNamespace
    return SimpleNamespace(type=ptype, conversation_key=conv_key, payload={"task_id": task_id})


def _write_task(brr_dir: Path, task_id: str, *, repo: str, issue_number: int) -> None:
    task = Task(
        id=task_id,
        event_id="evt-001",
        body="do the thing",
        source="github",
        conversation_key="github:default",
        meta={"github_repo": repo, "github_issue_number": issue_number},
    )
    tasks_dir = brr_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task_id}.md").write_text(task.to_frontmatter(), encoding="utf-8")


def test_render_update_creates_comment_on_task_created(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    state._save_state(brr_dir, {"token": "tok", "bot_login": "brr-bot", "repo": "o/r"})
    _write_task(brr_dir, "task-001", repo="o/r", issue_number=42)
    monkeypatch.setattr(progress, "_build_card_text", lambda *a: "**task received**")

    posts: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        client, "_api_post",
        lambda token, path, body: posts.append((path, body)) or {"id": 999},
    )
    monkeypatch.setattr(client, "_api_patch", lambda token, path, body: None)

    pkt = _make_packet("task_created", "github:default", "task-001")
    github.render_update(brr_dir, pkt)

    assert len(posts) == 1
    assert posts[0][0] == "/repos/o/r/issues/42/comments"
    assert posts[0][1]["body"] == "**task received**"

    entry = progress._load_progress_for_task(brr_dir, "task-001")
    assert entry["comment_id"] == 999


def test_render_update_patches_existing_comment(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    state._save_state(brr_dir, {"token": "tok", "bot_login": "brr-bot", "repo": "o/r"})
    _write_task(brr_dir, "task-002", repo="o/r", issue_number=7)
    progress._save_progress_for_task(brr_dir, "task-002", {"comment_id": 555, "last_text": "old"})
    monkeypatch.setattr(progress, "_build_card_text", lambda *a: "**running...**")

    patches: list[tuple[str, dict]] = []
    monkeypatch.setattr(client, "_api_patch", lambda token, path, body: patches.append((path, body)))
    posts: list = []
    monkeypatch.setattr(client, "_api_post", lambda *a, **kw: posts.append(a))

    pkt = _make_packet("heartbeat", "github:default", "task-002")
    github.render_update(brr_dir, pkt)

    assert patches == [("/repos/o/r/issues/comments/555", {"body": "**running...**"})]
    assert posts == []


def test_render_update_skips_duplicate_text(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    state._save_state(brr_dir, {"token": "tok", "bot_login": "brr-bot", "repo": "o/r"})
    _write_task(brr_dir, "task-003", repo="o/r", issue_number=1)
    progress._save_progress_for_task(
        brr_dir, "task-003", {"comment_id": 11, "last_text": "same text"},
    )
    monkeypatch.setattr(progress, "_build_card_text", lambda *a: "same text")

    patches: list = []
    monkeypatch.setattr(client, "_api_patch", lambda *a, **kw: patches.append(a))
    posts: list = []
    monkeypatch.setattr(client, "_api_post", lambda *a, **kw: posts.append(a))

    pkt = _make_packet("heartbeat", "github:default", "task-003")
    github.render_update(brr_dir, pkt)

    assert patches == []
    assert posts == []


def test_render_update_noop_for_non_github_source(tmp_path, monkeypatch):
    """render_update must ignore tasks not sourced from GitHub."""
    brr_dir = tmp_path / ".brr"
    state._save_state(brr_dir, {"token": "tok"})
    # Write a task with source="telegram"
    task = Task(
        id="task-tg",
        event_id="evt-x",
        body="ping",
        source="telegram",
        meta={"telegram_chat_id": 123},
    )
    (brr_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (brr_dir / "tasks" / "task-tg.md").write_text(task.to_frontmatter(), encoding="utf-8")

    posts: list = []
    monkeypatch.setattr(client, "_api_post", lambda *a, **kw: posts.append(a))

    pkt = _make_packet("task_created", "telegram:123:", "task-tg")
    github.render_update(brr_dir, pkt)

    assert posts == []


def test_render_update_noop_when_no_token(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    monkeypatch.setattr(state, "_gh_cli_token", lambda: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    # No state file at all — no token.
    posts: list = []
    monkeypatch.setattr(client, "_api_post", lambda *a, **kw: posts.append(a))

    pkt = _make_packet("task_created", "github:default", "task-x")
    github.render_update(brr_dir, pkt)

    assert posts == []


# ── branch footer + delivery ─────────────────────────────────────────


def test_branch_footer_returns_empty_when_no_branch():
    task = Task(id="t", event_id="e", body="b", source="github", meta={})
    assert delivery._branch_footer("o/r", task) == ""


def test_branch_footer_ignores_branch_name_before_finalize():
    task = Task(
        id="t", event_id="e", body="b", source="github",
        meta={"branch_name": "brr/task-abc"},
    )
    assert delivery._branch_footer("o/r", task) == ""


def test_branch_footer_includes_tree_and_compare_links():
    task = Task(
        id="t", event_id="e", body="b", source="github",
        meta={"publish_branch": "brr/task-abc"},
    )
    footer = delivery._branch_footer("owner/repo", task)
    assert "brr/task-abc" in footer
    assert "https://github.com/owner/repo/tree/brr/task-abc" in footer
    assert "compare/brr/task-abc?expand=1" in footer
    assert "Compare & open PR" in footer


def test_find_task_for_event(tmp_path):
    brr_dir = tmp_path / ".brr"
    _write_task(brr_dir, "task-find-me", repo="o/r", issue_number=1)
    # Give it a known event_id by rewriting the frontmatter:
    task_path = brr_dir / "tasks" / "task-find-me.md"
    text = task_path.read_text()
    text = text.replace("event_id: evt-001", "event_id: evt-target")
    task_path.write_text(text)

    found = delivery._find_task_for_event(brr_dir, "evt-target")
    assert found is not None
    assert found.id == "task-find-me"

    assert delivery._find_task_for_event(brr_dir, "evt-unknown") is None


def test_deliver_responses_appends_branch_footer(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {"token": "tok", "repo": "owner/repo"})

    # Create a task file with a pushed branch.
    task = Task(
        id="task-deliver",
        event_id="evt-deliver",
        body="do something",
        source="github",
        meta={"github_repo": "owner/repo", "github_issue_number": 5,
              "publish_branch": "brr/task-deliver"},
    )
    (brr_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (brr_dir / "tasks" / "task-deliver.md").write_text(task.to_frontmatter())

    # Create a done event and a response file.
    protocol.create_event(
        inbox, source="github", body="do something",
        github_repo="owner/repo",
        github_kind="issue",
        github_issue_number=5,
    )
    event = protocol.list_pending(inbox)[0]
    # Patch the event's event_id field to match the task.
    text = event["_path"].read_text()
    text = text.replace(f"id: {event['id']}", f"id: evt-deliver")
    event["_path"].write_text(text)

    protocol.set_status(event, "done")
    protocol.write_response(responses, "evt-deliver", "The work is done.")

    posts: list[tuple[str, dict]] = []
    monkeypatch.setattr(client, "_api_post", lambda token, path, body: posts.append((path, body)))

    delivery._deliver_responses(brr_dir, inbox, responses, "tok")

    assert len(posts) == 1
    body_text = posts[0][1]["body"]
    assert "The work is done." in body_text
    assert "brr/task-deliver" in body_text
    assert "compare/brr/task-deliver?expand=1" in body_text
