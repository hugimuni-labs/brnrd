"""Gate run-loop: poll, deliver, sleep.

The daemon spawns ``run_loop`` in its own thread (see
``gates/__init__.py`` and ``daemon.py``). Errors are caught here so
the gate keeps running even when GitHub is unreachable; rate-limit
backoff is calculated from the response headers when possible.
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import runtime
from . import delivery, polling, state
from .client import GitHubAPIError
from .constants import _BACKOFF_MAX, _POLL_INTERVAL


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
        print(f"[brnrd:github] {exc} — backing off {_BACKOFF_MAX}s")
        return _BACKOFF_MAX
    print(f"[brnrd:github] {exc} — backing off {_POLL_INTERVAL}s")
    return _POLL_INTERVAL


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> int:
    state_dict = state._load_state(brr_dir)
    token = state.resolve_token(state_dict)
    repo = state_dict.get("repo")
    triggers = state_dict.get("triggers") or {}
    if not token or not repo:
        return _POLL_INTERVAL

    cursor = state_dict.setdefault("cursor", {})

    # Authorization gate (#408): the allowlist is read fresh each poll
    # cycle (an operator edit takes effect on the next loop iteration);
    # the permission cache is scoped to just this cycle so a chatty
    # author costs at most one collaborator-permission lookup per repo
    # per poll, not one per event.
    allowlist = state.allowlist(state_dict)
    permission_cache: dict[tuple[str, str], str | None] = {}

    if triggers:
        if triggers.get("any"):
            polling._poll_any_activity(
                token, repo, state_dict.get("bot_login", ""), cursor, inbox_dir,
                allowlist=allowlist, permission_cache=permission_cache,
            )
        else:
            bot_login = state_dict.get("bot_login", "")
            if triggers.get("opened"):
                polling._poll_opened_trigger(
                    token, repo, cursor, inbox_dir, bot_login=bot_login,
                    allowlist=allowlist, permission_cache=permission_cache,
                )
            if "label" in triggers:
                polling._poll_label_trigger(
                    token, repo, triggers["label"], cursor, inbox_dir,
                    bot_login=bot_login,
                    allowlist=allowlist, permission_cache=permission_cache,
                )
            if "mention" in triggers:
                polling._poll_mention_trigger(
                    token, repo, triggers["mention"], bot_login,
                    cursor, inbox_dir,
                    allowlist=allowlist, permission_cache=permission_cache,
                )
            if "assignee" in triggers:
                polling._poll_assignee_trigger(
                    token, repo, triggers["assignee"], cursor, inbox_dir,
                    allowlist=allowlist, permission_cache=permission_cache,
                )

    state_dict["cursor"] = cursor
    state._save_state(brr_dir, state_dict)

    delivery._deliver_responses(brr_dir, inbox_dir, responses_dir, token, repo)
    return _POLL_INTERVAL


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    """Daemon-thread entry point. Catches its own errors and backs off."""
    backoff = 1
    while True:
        try:
            sleep_seconds = _loop_once(brr_dir, inbox_dir, responses_dir)
            runtime.record_loop_health(brr_dir, "github", ok=True)
            backoff = 1
        except GitHubAPIError as exc:
            runtime.record_loop_health(brr_dir, "github", ok=False, error=str(exc))
            sleep_seconds = _handle_api_error(exc)
            backoff = 1
        except Exception as exc:
            runtime.record_loop_health(brr_dir, "github", ok=False, error=str(exc))
            print(f"[brnrd:github] error: {exc}, retrying in {backoff}s")
            sleep_seconds = backoff
            backoff = min(backoff * 2, _BACKOFF_MAX)
        time.sleep(max(1, int(sleep_seconds)))
