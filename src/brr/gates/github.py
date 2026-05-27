"""GitHub gate — turns GitHub activity into events.

The gate polls the GitHub REST API for three configurable triggers:

- ``label-on-issue``: a new (or updated) open issue carrying the
  configured label becomes one inbox event.
- ``mention-in-comment``: a new comment containing the configured
  mention string becomes one event. Covers issue/PR timeline comments
  (``/issues/comments``) and inline PR review comments on diffs
  (``/pulls/comments``). PR-anchored comments carry the PR head branch
  as ``branch_target`` so the daemon's pre-task fetch+ff refreshes that
  branch before the worker runs. For ``@handle``-style triggers,
  comments authored by ``handle`` are filtered so the named account
  cannot self-loop; the PAT holder can still @-mention that account
  from their own comments.
- ``any``: every new issue, PR, and comment fires an event. Overrides
  label and mention when set. Token-expensive on busy repos; off by
  default. PR events include ``branch_target``; bot's own comments are
  still filtered.

Replies are posted as comments on the originating issue or PR.

State lives at ``.brr/gates/github.json``. Auth resolution at setup
time, in order:

1. ``gh auth token`` shell-out when ``gh`` is on PATH.
2. ``GITHUB_TOKEN`` environment variable.
3. Interactive paste, stored in the state file.

The gate is built-in but ``is_configured`` returns false until setup
runs — there is no surprise auto-enable. Webhooks are deliberately
out of scope for v1 (require a public URL and signature verification);
polling matches the rest of brr's gate model.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from requests.utils import quote

from .. import gitops, protocol, run_progress
from ..task import Task


_API_ROOT = "https://api.github.com"
_USER_AGENT = "brr-github-gate"
_API_VERSION = "2022-11-28"
_POLL_INTERVAL = 60
_BACKOFF_MAX = 120
_HTTP_TIMEOUT = 30
# Cap how far back we look on first poll so a freshly-configured gate
# doesn't re-process a year of historical comments.
_INITIAL_LOOKBACK = timedelta(hours=1)
# Cap how many seen IDs we keep per trigger to bound state file size.
_SEEN_CAP = 500


# ── HTTP helpers ─────────────────────────────────────────────────────


class GitHubAPIError(RuntimeError):
    """Raised on any non-2xx GitHub API response."""

    def __init__(self, status: int, message: str, *, headers: dict | None = None):
        super().__init__(f"github {status}: {message}")
        self.status = status
        self.message = message
        self.headers = headers or {}


def _request(
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, str]]:
    """Issue a GitHub API call. Returns ``(parsed_body, response_headers)``.

    Raises ``GitHubAPIError`` on non-2xx responses; the ``Retry-After`` /
    ``X-RateLimit-*`` headers are surfaced on the exception so the caller
    can sleep until reset.
    """
    url = _API_ROOT + path
    clean_params = None
    if params:
        # Filter out None / empty values so callers can pass optional
        # cursors without crafting URL strings by hand.
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        if clean:
            clean_params = clean
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": _USER_AGENT,
        "X-GitHub-Api-Version": _API_VERSION,
    }
    response = requests.request(
        method,
        url,
        params=clean_params,
        json=body if body is not None else None,
        headers=headers,
        timeout=_HTTP_TIMEOUT,
    )
    response_headers = {k: v for k, v in response.headers.items()}
    if not 200 <= response.status_code < 300:
        raise GitHubAPIError(
            response.status_code,
            _github_error_message(response),
            headers=response_headers,
        )
    if not response.content:
        return None, response_headers
    return response.json(), response_headers


def _github_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message")
        if message:
            return str(message)[:500]
    if response.text:
        return response.text[:500]
    return response.reason or ""


def _api_get(token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    payload, _ = _request(token, "GET", path, params=params)
    return payload


def _api_post(token: str, path: str, body: dict[str, Any]) -> Any:
    payload, _ = _request(token, "POST", path, body=body)
    return payload


# ── State ────────────────────────────────────────────────────────────


def _state_path(brr_dir: Path) -> Path:
    return brr_dir / "gates" / "github.json"


def _load_state(brr_dir: Path) -> dict:
    path = _state_path(brr_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_state(brr_dir: Path, state: dict) -> None:
    path = _state_path(brr_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ── Token resolution ────────────────────────────────────────────────


def _gh_cli_token() -> str | None:
    """Read a token from ``gh auth token`` if the binary is available."""
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def _env_token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(name)
        if token:
            return token.strip()
    return None


def resolve_token(state: dict) -> str | None:
    """Return the active token, preferring stored > gh CLI > env.

    Stored tokens win because they are explicit operator intent — they
    are only saved when the operator pasted one during ``setup``. The
    gh CLI and env fallbacks are first-time setup conveniences.
    """
    stored = state.get("token")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return _gh_cli_token() or _env_token()


def _validate_token(token: str) -> str:
    """Return the authenticated user's login. Raises on failure."""
    payload = _api_get(token, "/user")
    if not isinstance(payload, dict) or not payload.get("login"):
        raise GitHubAPIError(0, "no login in /user response")
    return str(payload["login"])


# ── Repo autodetect ─────────────────────────────────────────────────


_GITHUB_HOSTS = {"github.com", "www.github.com"}
_HTTPS_RE = re.compile(r"^https?://([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$")
_SSH_RE = re.compile(r"^git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$")


def parse_origin_url(url: str) -> str | None:
    """Return ``owner/name`` for a github.com remote URL, or ``None``."""
    if not url:
        return None
    url = url.strip()
    m = _SSH_RE.match(url)
    if m and m.group(1) in _GITHUB_HOSTS:
        return f"{m.group(2)}/{m.group(3)}"
    m = _HTTPS_RE.match(url)
    if m and m.group(1) in _GITHUB_HOSTS:
        return f"{m.group(2)}/{m.group(3)}"
    return None


def autodetect_repo(repo_root: Path) -> str | None:
    remote = gitops.default_remote(repo_root)
    if not remote:
        return None
    url = gitops.remote_url(repo_root, remote)
    if not url:
        return None
    return parse_origin_url(url)


# ── Setup ────────────────────────────────────────────────────────────


def auth(brr_dir: Path) -> None:
    state = _load_state(brr_dir)
    token = resolve_token(state)
    source = "stored" if state.get("token") else None
    if token is None:
        token = input("GitHub personal access token (repo scope): ").strip()
        if not token:
            print("[brr] No token provided.")
            return
        source = "stored"
    elif not state.get("token"):
        # We picked one up from gh CLI or env. Don't store it; just
        # validate now so the operator knows it works.
        source = "gh-cli" if _gh_cli_token() == token else "env"

    try:
        login = _validate_token(token)
    except Exception as exc:
        print(f"[brr] GitHub auth failed: {exc}")
        return

    state["bot_login"] = login
    if source == "stored":
        state["token"] = token
    else:
        # Make sure we don't keep a stale stored token if the operator
        # is rotating to a gh CLI / env-based flow.
        state.pop("token", None)
    state["token_source"] = source
    _save_state(brr_dir, state)
    print(f"[brr] GitHub auth ok: @{login} (source={source})")


def _prompt_trigger(label: str, default: str) -> str | None:
    """Prompt for a trigger string.

    - Enter → accepts the bracketed default as-is.
    - ``off`` / ``none`` / ``disable`` → remove the trigger (returns ``None``).
    - Anything else → use that literal value.
    """
    raw = input(f"{label} (off to disable) [{default}]: ").strip()
    if not raw:
        return default
    if raw.lower() in ("off", "none", "disable"):
        return None
    return raw


def bind(brr_dir: Path) -> None:
    state = _load_state(brr_dir)
    if resolve_token(state) is None:
        print("[brr] Run `brr auth github` first.")
        return

    repo_root = brr_dir.parent
    detected = autodetect_repo(repo_root)
    prompt = f"GitHub repo (owner/name) [{detected}]: " if detected else "GitHub repo (owner/name): "
    repo = input(prompt).strip() or (detected or "")
    if not repo or "/" not in repo:
        print("[brr] Repo must look like 'owner/name'.")
        return
    state["repo"] = repo

    triggers: dict[str, Any] = state.get("triggers") or {}

    # 'any' fires on every issue, PR, and comment — overrides label/mention.
    print("Watch all activity fires on every new issue, PR, and comment without")
    print("filtering. Token-expensive on busy repos. Off by default.")
    any_raw = input("Enable? (on to enable, Enter to skip) [off]: ").strip().lower()
    if any_raw in ("on", "yes", "true"):
        triggers = {"any": True}
        state["triggers"] = triggers
        _save_state(brr_dir, state)
        print(f"[brr] GitHub gate bound: repo={repo} triggers=['any']")
        return
    triggers.pop("any", None)

    label = _prompt_trigger(
        "Label to watch on issues",
        str(triggers.get("label") or "brr"),
    )
    if label is None:
        triggers.pop("label", None)
    else:
        triggers["label"] = label

    mention = _prompt_trigger(
        "Mention string to watch in comments",
        str(triggers.get("mention") or "@brr-bot"),
    )
    if mention is None:
        triggers.pop("mention", None)
    else:
        triggers["mention"] = mention

    if not triggers:
        print("[brr] No triggers configured — at least one of label / mention required.")
        return
    state["triggers"] = triggers
    _save_state(brr_dir, state)
    print(f"[brr] GitHub gate bound: repo={repo} triggers={list(triggers)}")


def setup(brr_dir: Path) -> None:
    auth(brr_dir)
    if "bot_login" in _load_state(brr_dir):
        bind(brr_dir)


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return (
        bool(state.get("repo"))
        and bool(state.get("triggers"))
        and resolve_token(state) is not None
    )


# ── Gate loop ────────────────────────────────────────────────────────


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    """Daemon-thread entry point. Catches its own errors and backs off."""
    backoff = 1
    while True:
        try:
            sleep_seconds = _loop_once(brr_dir, inbox_dir, responses_dir)
            backoff = 1
        except GitHubAPIError as exc:
            sleep_seconds = _handle_api_error(exc)
            backoff = 1
        except Exception as exc:
            print(f"[brr:github] error: {exc}, retrying in {backoff}s")
            sleep_seconds = backoff
            backoff = min(backoff * 2, _BACKOFF_MAX)
        time.sleep(max(1, int(sleep_seconds)))


def _handle_api_error(exc: GitHubAPIError) -> int:
    """Return how long to sleep after an API error.

    Rate-limit responses include either ``Retry-After`` or a
    ``X-RateLimit-Reset`` epoch; both let us sleep precisely.
    """
    headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
    if exc.status in (403, 429) and "retry-after" in headers:
        try:
            return max(1, int(headers["retry-after"]))
        except ValueError:
            pass
    if exc.status in (403, 429) and headers.get("x-ratelimit-remaining") == "0":
        try:
            reset = int(headers.get("x-ratelimit-reset", "0"))
            now = int(time.time())
            return max(1, reset - now)
        except ValueError:
            pass
    if 400 <= exc.status < 500:
        # Unauthorised / forbidden / not-found is not transient. Surface
        # the failure to the operator and back off gently so we don't
        # spam logs.
        print(f"[brr:github] {exc} — backing off {_BACKOFF_MAX}s")
        return _BACKOFF_MAX
    print(f"[brr:github] {exc} — backing off {_POLL_INTERVAL}s")
    return _POLL_INTERVAL


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> int:
    state = _load_state(brr_dir)
    token = resolve_token(state)
    repo = state.get("repo")
    triggers = state.get("triggers") or {}
    if not token or not repo or not triggers:
        return _POLL_INTERVAL

    cursor = state.setdefault("cursor", {})

    if triggers.get("any"):
        _poll_any_activity(
            token, repo, state.get("bot_login", ""), cursor, inbox_dir,
        )
    else:
        if "label" in triggers:
            _poll_label_trigger(token, repo, triggers["label"], cursor, inbox_dir)
        if "mention" in triggers:
            _poll_mention_trigger(
                token, repo, triggers["mention"], state.get("bot_login", ""),
                cursor, inbox_dir,
            )

    state["cursor"] = cursor
    _save_state(brr_dir, state)

    _deliver_responses(brr_dir, inbox_dir, responses_dir, token)
    return _POLL_INTERVAL


# ── Triggers ─────────────────────────────────────────────────────────


def _initial_since() -> str:
    return _format_iso(datetime.now(timezone.utc) - _INITIAL_LOOKBACK)


def _poll_label_trigger(
    token: str,
    repo: str,
    label: str,
    cursor: dict,
    inbox_dir: Path,
) -> None:
    since = cursor.get("issues_since") or _initial_since()
    seen = set(cursor.get("seen_issue_numbers") or [])
    issues = _api_get(
        token, f"/repos/{repo}/issues",
        params={
            "state": "open",
            "labels": label,
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    latest_seen = since
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        if number in seen:
            continue
        # GitHub returns PRs from /issues too; skip them — PR work belongs
        # to the mention trigger, not the label trigger, because PRs almost
        # always have ongoing back-and-forth and a label trigger would
        # only fire once per PR which is rarely what an operator wants.
        if "pull_request" in issue:
            continue

        title = str(issue.get("title") or "").strip()
        body = str(issue.get("body") or "").strip()
        author = (issue.get("user") or {}).get("login") or ""
        protocol.create_event(
            inbox_dir,
            source="github",
            body=_format_event_body(title, body),
            github_repo=repo,
            github_kind="issue",
            github_issue_number=number,
            github_author=author,
            github_html_url=issue.get("html_url") or "",
            github_trigger="label",
            github_label=label,
            title=title,
        )
        seen.add(number)
        ts = issue.get("updated_at") or issue.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["issues_since"] = latest_seen
    cursor["seen_issue_numbers"] = sorted(seen)[-_SEEN_CAP:]


def _poll_mention_trigger(
    token: str,
    repo: str,
    mention: str,
    token_login: str,
    cursor: dict,
    inbox_dir: Path,
) -> None:
    since = cursor.get("comments_since") or _initial_since()
    seen = set(cursor.get("seen_comment_ids") or [])
    comments = _api_get(
        token, f"/repos/{repo}/issues/comments",
        params={
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    pr_branch_cache: dict[int, str] = {}
    latest_seen = since
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = comment.get("id")
        if not isinstance(cid, int):
            continue
        if cid in seen:
            continue
        body = str(comment.get("body") or "")
        if mention not in body:
            continue
        author = (comment.get("user") or {}).get("login") or ""
        if _skip_mention_comment_author(author, mention, token_login):
            # Don't re-trigger when the named @-account echoes the trigger
            # (or, for non-@ triggers, the token holder's own comments).
            continue

        html_url = str(comment.get("html_url") or "")
        is_pr = "/pull/" in html_url
        issue_number = _extract_issue_number(comment.get("issue_url") or "")
        if issue_number is None:
            continue

        meta: dict[str, Any] = {
            "github_repo": repo,
            "github_kind": "pr-comment" if is_pr else "issue-comment",
            "github_issue_number": issue_number,
            "github_comment_id": cid,
            "github_author": author,
            "github_html_url": html_url,
            "github_trigger": "mention",
            "github_mention": mention,
        }
        if is_pr:
            meta["github_pr_number"] = issue_number
            branch = pr_branch_cache.get(issue_number) or _fetch_pr_head_branch(
                token, repo, issue_number,
            )
            if branch:
                pr_branch_cache[issue_number] = branch
                meta["branch_target"] = branch

        protocol.create_event(
            inbox_dir,
            source="github",
            body=_format_event_body("", body),
            **meta,
        )
        seen.add(cid)
        ts = comment.get("updated_at") or comment.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["comments_since"] = latest_seen
    cursor["seen_comment_ids"] = sorted(seen)[-_SEEN_CAP:]

    _poll_mention_review_comments(
        token, repo, mention, token_login, cursor, inbox_dir, pr_branch_cache,
    )


def _poll_mention_review_comments(
    token: str,
    repo: str,
    mention: str,
    token_login: str,
    cursor: dict,
    inbox_dir: Path,
    pr_branch_cache: dict[int, str],
) -> None:
    """Poll inline PR review comments (diff line threads) for *mention*."""
    since = cursor.get("review_comments_since") or _initial_since()
    seen = set(cursor.get("seen_review_comment_ids") or [])
    comments = _api_get(
        token, f"/repos/{repo}/pulls/comments",
        params={
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    latest_seen = since
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = comment.get("id")
        if not isinstance(cid, int):
            continue
        if cid in seen:
            continue
        body = str(comment.get("body") or "")
        if mention not in body:
            continue
        author = (comment.get("user") or {}).get("login") or ""
        if _skip_mention_comment_author(author, mention, token_login):
            continue

        pr_number = _extract_pr_number(comment.get("pull_request_url") or "")
        if pr_number is None:
            continue

        html_url = str(comment.get("html_url") or "")
        path = str(comment.get("path") or "").strip()
        line = comment.get("line")
        meta: dict[str, Any] = {
            "github_repo": repo,
            "github_kind": "pr-review-comment",
            "github_issue_number": pr_number,
            "github_pr_number": pr_number,
            "github_comment_id": cid,
            "github_author": author,
            "github_html_url": html_url,
            "github_trigger": "mention",
            "github_mention": mention,
        }
        if path:
            meta["github_path"] = path
        if isinstance(line, int):
            meta["github_line"] = line

        branch = pr_branch_cache.get(pr_number) or _fetch_pr_head_branch(
            token, repo, pr_number,
        )
        if branch:
            pr_branch_cache[pr_number] = branch
            meta["branch_target"] = branch

        protocol.create_event(
            inbox_dir,
            source="github",
            body=_format_review_comment_body(path, line, body),
            **meta,
        )
        seen.add(cid)
        ts = comment.get("updated_at") or comment.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["review_comments_since"] = latest_seen
    cursor["seen_review_comment_ids"] = sorted(seen)[-_SEEN_CAP:]


def _poll_any_activity(
    token: str,
    repo: str,
    bot_login: str,
    cursor: dict,
    inbox_dir: Path,
) -> None:
    """Poll all issues, PRs, and comments without filtering.

    Used when ``triggers["any"]`` is set. Emits one event per new issue,
    PR, and comment. PR events carry ``branch_target`` so the daemon's
    pre-task fetch+ff refreshes the PR head branch. Bot's own comments
    are filtered to prevent self-triggering loops.
    """
    # --- Issues and PRs -----------------------------------------------
    since = cursor.get("any_issues_since") or _initial_since()
    seen = set(cursor.get("any_seen_issue_numbers") or [])
    items = _api_get(
        token, f"/repos/{repo}/issues",
        params={
            "state": "all",
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    latest_seen = since
    for item in items:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if not isinstance(number, int):
            continue
        if number in seen:
            continue

        is_pr = "pull_request" in item
        title = str(item.get("title") or "").strip()
        body_text = str(item.get("body") or "").strip()
        author = (item.get("user") or {}).get("login") or ""
        meta: dict[str, Any] = {
            "github_repo": repo,
            "github_issue_number": number,
            "github_author": author,
            "github_html_url": item.get("html_url") or "",
            "github_trigger": "any",
        }
        if is_pr:
            meta["github_kind"] = "pr"
            meta["github_pr_number"] = number
            branch = _fetch_pr_head_branch(token, repo, number)
            if branch:
                meta["branch_target"] = branch
        else:
            meta["github_kind"] = "issue"
        protocol.create_event(
            inbox_dir,
            source="github",
            body=_format_event_body(title, body_text),
            title=title,
            **meta,
        )
        seen.add(number)
        ts = item.get("updated_at") or item.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["any_issues_since"] = latest_seen
    cursor["any_seen_issue_numbers"] = sorted(seen)[-_SEEN_CAP:]

    # --- Comments -----------------------------------------------------
    since_c = cursor.get("any_comments_since") or _initial_since()
    seen_c = set(cursor.get("any_seen_comment_ids") or [])
    comments = _api_get(
        token, f"/repos/{repo}/issues/comments",
        params={
            "since": since_c,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    pr_branch_cache: dict[int, str] = {}
    latest_seen_c = since_c
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = comment.get("id")
        if not isinstance(cid, int):
            continue
        if cid in seen_c:
            continue
        author = (comment.get("user") or {}).get("login") or ""
        if author and bot_login and author == bot_login:
            continue
        html_url = str(comment.get("html_url") or "")
        is_pr_comment = "/pull/" in html_url
        issue_number = _extract_issue_number(comment.get("issue_url") or "")
        if issue_number is None:
            continue
        body_text = str(comment.get("body") or "")
        meta_c: dict[str, Any] = {
            "github_repo": repo,
            "github_kind": "pr-comment" if is_pr_comment else "issue-comment",
            "github_issue_number": issue_number,
            "github_comment_id": cid,
            "github_author": author,
            "github_html_url": html_url,
            "github_trigger": "any",
        }
        if is_pr_comment:
            meta_c["github_pr_number"] = issue_number
            branch = pr_branch_cache.get(issue_number) or _fetch_pr_head_branch(
                token, repo, issue_number,
            )
            if branch:
                pr_branch_cache[issue_number] = branch
                meta_c["branch_target"] = branch
        protocol.create_event(
            inbox_dir,
            source="github",
            body=_format_event_body("", body_text),
            **meta_c,
        )
        seen_c.add(cid)
        ts = comment.get("updated_at") or comment.get("created_at")
        if isinstance(ts, str) and ts > latest_seen_c:
            latest_seen_c = ts

    cursor["any_comments_since"] = latest_seen_c
    cursor["any_seen_comment_ids"] = sorted(seen_c)[-_SEEN_CAP:]

    _poll_any_review_comments(
        token, repo, bot_login, cursor, inbox_dir, pr_branch_cache,
    )


def _poll_any_review_comments(
    token: str,
    repo: str,
    bot_login: str,
    cursor: dict,
    inbox_dir: Path,
    pr_branch_cache: dict[int, str],
) -> None:
    since = cursor.get("any_review_comments_since") or _initial_since()
    seen = set(cursor.get("any_seen_review_comment_ids") or [])
    comments = _api_get(
        token, f"/repos/{repo}/pulls/comments",
        params={
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    latest_seen = since
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = comment.get("id")
        if not isinstance(cid, int):
            continue
        if cid in seen:
            continue
        author = (comment.get("user") or {}).get("login") or ""
        if author and bot_login and author == bot_login:
            continue

        pr_number = _extract_pr_number(comment.get("pull_request_url") or "")
        if pr_number is None:
            continue

        html_url = str(comment.get("html_url") or "")
        path = str(comment.get("path") or "").strip()
        line = comment.get("line")
        body_text = str(comment.get("body") or "")
        meta: dict[str, Any] = {
            "github_repo": repo,
            "github_kind": "pr-review-comment",
            "github_issue_number": pr_number,
            "github_pr_number": pr_number,
            "github_comment_id": cid,
            "github_author": author,
            "github_html_url": html_url,
            "github_trigger": "any",
        }
        if path:
            meta["github_path"] = path
        if isinstance(line, int):
            meta["github_line"] = line

        branch = pr_branch_cache.get(pr_number) or _fetch_pr_head_branch(
            token, repo, pr_number,
        )
        if branch:
            pr_branch_cache[pr_number] = branch
            meta["branch_target"] = branch

        protocol.create_event(
            inbox_dir,
            source="github",
            body=_format_review_comment_body(path, line, body_text),
            **meta,
        )
        seen.add(cid)
        ts = comment.get("updated_at") or comment.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["any_review_comments_since"] = latest_seen
    cursor["any_seen_review_comment_ids"] = sorted(seen)[-_SEEN_CAP:]


_ISSUE_URL_RE = re.compile(r"/issues/(\d+)$")
_PR_URL_RE = re.compile(r"/pulls/(\d+)(?:/|$)")


def _login_to_skip_for_mention_trigger(mention: str, token_login: str) -> str | None:
    """GitHub login whose issue/PR comments never emit *mention* events.

    ``bot_login`` in state is the authenticated token owner (often a human
    PAT). Skipping ``author == token_login`` would drop every @-mention the
    operator writes. When the trigger is ``@handle``-shaped, only ``handle``
    is skipped (the automation account named in the mention). For custom
    substring triggers without a leading ``@…`` handle, fall back to the
    token login so legacy ``HELPME``-style triggers still avoid self-loops.
    """
    raw = (mention or "").strip()
    if raw.startswith("@"):
        acc: list[str] = []
        for ch in raw[1:]:
            if ch in " \t\n\r":
                break
            if ch.isalnum() or ch == "-":
                acc.append(ch)
            else:
                break
        login = "".join(acc).strip("-")
        if login:
            return login
    tl = (token_login or "").strip()
    return tl or None


def _skip_mention_comment_author(author: str, mention: str, token_login: str) -> bool:
    skip = _login_to_skip_for_mention_trigger(mention, token_login)
    if not skip or not author:
        return False
    return author.casefold() == skip.casefold()


def _extract_issue_number(issue_url: str) -> int | None:
    if not issue_url:
        return None
    m = _ISSUE_URL_RE.search(issue_url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _extract_pr_number(pull_request_url: str) -> int | None:
    if not pull_request_url:
        return None
    m = _PR_URL_RE.search(pull_request_url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _format_review_comment_body(path: str, line: object, body: str) -> str:
    """Prefix inline review context so the worker knows which hunk was tagged."""
    text = body.strip()
    if path:
        loc = f"`{path}`"
        if isinstance(line, int):
            loc += f" line {line}"
        header = f"On {loc}:\n\n"
        return (header + text + "\n") if text else header
    return _format_event_body("", body)


def _fetch_pr_head_branch(token: str, repo: str, pr_number: int) -> str | None:
    pr = _api_get(token, f"/repos/{repo}/pulls/{pr_number}")
    if not isinstance(pr, dict):
        return None
    head = pr.get("head") or {}
    ref = head.get("ref")
    return str(ref) if isinstance(ref, str) and ref else None


def _format_event_body(title: str, body: str) -> str:
    if title and body:
        return f"# {title}\n\n{body}".strip() + "\n"
    if title:
        return f"# {title}\n"
    return body.strip() + "\n" if body else ""


# ── Response delivery ──────────────────────────────────────────────


def _find_task_for_event(brr_dir: Path, event_id: str) -> Task | None:
    """Scan .brr/tasks/ for the task whose event_id matches *event_id*."""
    tasks_dir = brr_dir / "tasks"
    if not tasks_dir.exists():
        return None
    for path in tasks_dir.glob("*.md"):
        task = Task.from_file(path)
        if task and task.event_id == event_id:
            return task
    return None


def _branch_footer(repo: str, task: Task) -> str:
    """Return a Markdown footer with branch / PR links, or empty string.

    Only appended after finalization has identified the branch that
    should be published. The ``?expand=1`` on the compare URL pre-fills
    GitHub's PR-creation form so clicking it is one step from merging.
    """
    branch = task.meta.get("publish_branch")
    if not branch:
        return ""
    base_url = f"https://github.com/{repo}"
    tree_url = f"{base_url}/tree/{quote(branch, safe='/')}"
    compare_url = (
        f"{base_url}/compare/{quote(branch, safe='/')}?expand=1"
    )
    return (
        f"\n\n---\n"
        f"Branch: [`{branch}`]({tree_url}) · "
        f"[Compare & open PR ↗]({compare_url})"
    )


def _deliver_responses(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
    token: str,
) -> None:
    for event in protocol.list_done(inbox_dir, "github"):
        eid = event["id"]
        repo = event.get("github_repo")
        number = _coerce_int(event.get("github_issue_number"))
        body = protocol.read_response(responses_dir, eid)
        if body is None:
            continue
        if not repo or number is None:
            print(f"[brr:github] delivery error for {eid}: missing repo / issue_number")
            continue
        task = _find_task_for_event(brr_dir, eid)
        if task is not None:
            footer = _branch_footer(repo, task)
            if footer:
                body = body.rstrip() + footer + "\n"
        threaded_body = _thread_reply_body(event, body)
        kind = str(event.get("github_kind") or "")
        review_cid = _coerce_int(event.get("github_comment_id"))
        pr_number = _coerce_int(
            event.get("github_pr_number") or event.get("github_issue_number"),
        )
        if kind == "pr-review-comment" and review_cid is not None and pr_number is not None:
            post_path = (
                f"/repos/{repo}/pulls/{pr_number}/comments/{review_cid}/replies"
            )
        else:
            post_path = f"/repos/{repo}/issues/{number}/comments"
        try:
            _api_post(
                token, post_path,
                body={"body": threaded_body},
            )
        except GitHubAPIError as exc:
            print(f"[brr:github] delivery error for {eid}: {exc}")
            continue
        resp_path = protocol.response_path(responses_dir, eid)
        protocol.cleanup(event["_path"], resp_path)


_COMMENT_KINDS = frozenset({
    "issue-comment", "pr-comment", "pr-review-comment",
})


def _thread_reply_body(event: dict, body: str) -> str:
    """Prepend a quote-style pointer back at the triggering comment.

    GitHub's issue/PR comment endpoint has no first-class "reply to a
    specific comment" primitive (review-comment replies are a separate
    API and a separate trigger we don't expose). The closest thing to a
    visible thread anchor is a blockquote linking to the source comment,
    which is what the GitHub web UI itself generates when a user clicks
    "Quote reply". Skipped for label-triggered events because the issue
    itself *is* the source — the comment doesn't need to point at it.
    """
    kind = str(event.get("github_kind") or "")
    if kind not in _COMMENT_KINDS:
        return body
    url = str(event.get("github_html_url") or "").strip()
    if not url:
        return body
    author = str(event.get("github_author") or "").strip()
    if author:
        preface = f"> Replying to [@{author}'s comment]({url})\n\n"
    else:
        preface = f"> Replying to [the source comment]({url})\n\n"
    return preface + body


def _coerce_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_iso(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Live progress card ────────────────────────────────────────────────


_RENDERABLE_PACKETS = {
    "task_created",
    "env_prepared",
    "container_started",
    "container_preserved",
    "run_started",
    "attempt_started",
    "attempt_failed",
    "retrying",
    "artifact_created",
    "heartbeat",
    "finalizing",
    "push_started",
    "push_done",
    "done",
    "failed",
    "conflict",
}


def _progress_state_path(brr_dir: Path, task_id: str) -> Path:
    safe = task_id.replace("/", "_").replace("..", "_")
    return brr_dir / "gates" / "github" / "progress" / f"{safe}.json"


def _load_progress_for_task(brr_dir: Path, task_id: str) -> dict | None:
    path = _progress_state_path(brr_dir, task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_progress_for_task(brr_dir: Path, task_id: str, data: dict) -> None:
    path = _progress_state_path(brr_dir, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_card_text(brr_dir: Path, conv_key: str, task_id: str) -> str | None:
    view = run_progress.project_task(brr_dir, conv_key, task_id)
    if view is None:
        return None
    return run_progress.render_text(
        view,
        compact=True,
        style=run_progress.GITHUB_MARKDOWN_STYLE,
    )


def _api_patch(token: str, path: str, body: dict[str, Any]) -> Any:
    payload, _ = _request(token, "PATCH", path, body=body)
    return payload


def render_update(brr_dir: Path, packet: Any) -> None:
    """Create/edit a GitHub progress comment for *packet*.

    On ``task_created`` a fresh comment is posted on the originating issue
    or PR and the resulting comment ID is stored so later packets can edit
    the same comment via PATCH. Failures are swallowed — the daemon must
    keep running even if the GitHub API is unreachable.
    """
    ptype = getattr(packet, "type", None)
    if ptype not in _RENDERABLE_PACKETS:
        return

    state = _load_state(brr_dir)
    token = resolve_token(state)
    if not token:
        return

    conv_key = getattr(packet, "conversation_key", "") or ""
    task_id = run_progress.task_id_from_packet(packet)
    if not conv_key or not task_id:
        return

    task = Task.from_file(brr_dir / "tasks" / f"{task_id}.md")
    if task is None or task.source != "github":
        return
    repo = task.meta.get("github_repo") or state.get("repo")
    number = _coerce_int(task.meta.get("github_issue_number"))
    if not repo or number is None:
        return

    text = _build_card_text(brr_dir, conv_key, task_id)
    if text is None:
        return

    entry = _load_progress_for_task(brr_dir, task_id)

    if entry and entry.get("last_text") == text:
        entry["last_render"] = ptype
        _save_progress_for_task(brr_dir, task_id, entry)
        return

    try:
        if entry and entry.get("comment_id"):
            try:
                _api_patch(
                    token,
                    f"/repos/{repo}/issues/comments/{entry['comment_id']}",
                    body={"body": text},
                )
            except Exception:
                # Comment deleted; fall through to post a fresh one.
                new = _api_post(
                    token,
                    f"/repos/{repo}/issues/{number}/comments",
                    body={"body": text},
                )
                cid = (new or {}).get("id") if isinstance(new, dict) else None
                if cid is None:
                    return
                entry = {"comment_id": cid}
        else:
            new = _api_post(
                token,
                f"/repos/{repo}/issues/{number}/comments",
                body={"body": text},
            )
            cid = (new or {}).get("id") if isinstance(new, dict) else None
            if cid is None:
                return
            entry = {"comment_id": cid}

        entry["last_text"] = text
        entry["last_render"] = ptype
        _save_progress_for_task(brr_dir, task_id, entry)

    except Exception as exc:
        print(f"[brr:github] render_update error for {task_id}: {exc}")
