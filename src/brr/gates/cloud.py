"""Cloud gate — drains a brnrd repo inbox into the local ``.brr/``."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .. import claude_status, claude_usage, codex_status, codex_usage, gitops, presence, protocol, run_ledger, run_progress, runner_quota
from .. import dominion, schedule as schedule_mod, wake_request
from ..gates.github.parse import parse_origin_url
from ..run import Run, list_runs, run_manifest_path
from . import delivery, runtime

_POLL_WAIT_S = 25
_HTTP_TIMEOUT_S = 60
_DEFAULT_DAEMON_NAME = "daemon"
_RESPONSE_LIMITS = {"telegram": 3900}
_SESSION = requests.Session()
_CLAUDE_QUOTA_PUBLISH_MAX_AGE_SECONDS = 240.0
# Codex's probe is a ~1.5s process spawn against an account-metadata endpoint
# (no model tokens), so it can refresh well inside the dashboard's 300s
# staleness threshold without costing anything but wall-clock.
_CODEX_QUOTA_PUBLISH_MAX_AGE_SECONDS = 120.0
# Dashboard snapshots (activity/plans/quota/live-runs/PR-review-queue/run-ledger) used
# to publish once per `_loop_once` iteration, which is paced by the inbox
# long-poll above (`_POLL_WAIT_S = 25`) — a constant chosen for chat
# responsiveness, never for dashboard freshness. That coupling capped every
# dashboard snapshot at ~25s stale by construction. Publishing runs on its
# own short cadence instead — see kb/plan-loom-realtime-build.md slice 0.
_DASHBOARD_PUBLISH_INTERVAL_S = 3


class BrnrdAuthError(RuntimeError):
    pass


_AUTH_HINT = "Re-run `brnrd connect` to link this daemon to your brnrd repo."

# A 401 is retried, not fatal — see `run_loop`. Slow cadence, because the one
# case that deserves patience (a transient server-side auth failure) resolves
# on its own, and the one that doesn't (a truly bad token) should keep saying
# so once every five minutes rather than kill the gate on its way out.
_AUTH_RETRY_MIN_S = 5
_AUTH_RETRY_CAP_S = 300


# Gateway-transient statuses worth a short retry: the hosted brnrd sits
# behind a router that answers 502/503/504 for the duration of a deploy
# window (main auto-deploys on merge). A single blip tracebacked
# `brnrd connect` mid-deploy (2026-07-09); a couple of paced retries ride
# out the blip without hiding a real outage — the last error still raises.
# Upstream never saw these requests (the router refused them), so retrying
# non-idempotent methods doesn't double-deliver.
_RETRY_STATUSES = frozenset({502, 503, 504})
_RETRY_SLEEPS_S = (2.0, 5.0)


def _request(base_url: str, method: str, path: str, *, token: str | None = None, json: dict | None = None, params: dict | None = None, timeout: float = _HTTP_TIMEOUT_S) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for attempt, sleep_s in enumerate((*_RETRY_SLEEPS_S, None)):
        resp = _SESSION.request(method, base_url.rstrip("/") + path, json=json, params=params, headers=headers, timeout=timeout)
        if resp.status_code in _RETRY_STATUSES and sleep_s is not None:
            print(f"[brnrd:cloud] {method} {path} -> {resp.status_code} (gateway); retry {attempt + 1}/{len(_RETRY_SLEEPS_S)} in {sleep_s:.0f}s")
            time.sleep(sleep_s)
            continue
        break
    if resp.status_code == 401:
        raise BrnrdAuthError(f"brnrd {method} {path} -> 401: {resp.text[:200]} — {_AUTH_HINT}")
    if not 200 <= resp.status_code < 300:
        raise RuntimeError(f"brnrd {method} {path} -> {resp.status_code}: {resp.text[:200]}")
    return resp.json() if resp.content else {}


def _load_state(brr_dir: Path) -> dict:
    return runtime.load_state(brr_dir, "cloud")


def _save_state(brr_dir: Path, state: dict) -> None:
    runtime.save_state(brr_dir, "cloud", state)


def _repo_capabilities(brr_dir: Path) -> dict:
    repo_root = brr_dir.parent
    caps: dict[str, object] = {"repo_root": str(repo_root)}
    try:
        remote = gitops.default_remote(repo_root)
        if remote:
            url = gitops.remote_url(repo_root, remote)
            if url:
                caps["git_remote"] = url
                repo_full_name = parse_origin_url(url)
                if repo_full_name:
                    caps["repo_full_name"] = repo_full_name
        caps["branch"] = gitops.current_branch(repo_root)
        default_branch = gitops.default_branch(repo_root)
        if default_branch:
            caps["default_branch"] = default_branch
    except Exception:
        pass
    return caps


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return bool(state.get("token") and state.get("brnrd_url") and state.get("repo_id"))


def relay_pack(brr_dir: Path, pack: dict, *, ttl_s: int | None = None) -> str | None:
    state = _load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return None
    body: dict = {"pack": pack}
    if ttl_s:
        body["ttl_s"] = ttl_s
    try:
        result = _request(state["brnrd_url"], "POST", "/v1/daemons/pack", token=state["token"], json=body)
    except Exception as e:
        print(f"[brnrd:cloud] pack relay failed: {e}")
        return None
    url = result.get("render_url")
    return url if isinstance(url, str) and url else None


_CONFIG_CHANGE_MINT_TIMEOUT_S = 10.0


def propose_config_change(
    brr_dir: Path,
    *,
    proposal_id: str,
    config_key: str,
    current_value: Any,
    requested_value: Any,
    reason: str = "",
    timeout: float = _CONFIG_CHANGE_MINT_TIMEOUT_S,
) -> dict[str, Any] | None:
    """Mint a brnrd.dev approve/confirm URL for a proposed config-key change.

    Loom envelope Phase 2 (kb/design-multi-workstream-concurrency.md
    §"Named forks — round 2"): when a resident wants more of an
    allowlisted, user-tunable ceiling than ``.brr/config`` currently
    grants, the change is never applied unilaterally, and never on a
    chat-typed approval — it rides the same device-flow shape as
    ``routers/pairing.py``'s daemon pairing, gated behind the account
    owner's login (``src/brnrd/routers/config_approval.py``). Returns
    ``None`` (never raises) when this daemon isn't cloud-connected, since a
    repo with no brnrd.dev account has no approver to escalate to — the
    caller falls back to a locally-parked-only proposal in that case.
    A connected daemon whose mint *request* fails returns
    ``{"error": <detail>}`` instead, so the caller can report the real
    failure rather than misdiagnosing it as not-connected.

    Called synchronously from ``daemon.py``'s outbox drain (a deliberate,
    narrow exception to gates normally talking to the daemon only through
    the filesystem — see ``gates/README.md``): this is a rare,
    resident-initiated action, not a routine dispatch-loop tick, so the
    shorter-than-default ``timeout`` bounds how long a slow/unreachable
    server can stall that drain rather than avoiding the call entirely; a
    fully async two-phase mint (park now, mint on a later tick) was
    considered and set aside because it would leave a proposal's approve
    link — and any minting failure — invisible to the user until a
    separate poll noticed it.
    """
    state = _load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return None
    try:
        return _request(
            state["brnrd_url"],
            "POST",
            "/v1/daemons/config-requests",
            token=state["token"],
            json={
                "proposal_id": proposal_id,
                "config_key": config_key,
                "current_value": "" if current_value is None else str(current_value),
                "requested_value": str(requested_value),
                "reason": reason,
            },
            timeout=timeout,
        )
    except Exception as e:
        # Distinguish "connected but the mint failed" from "not connected":
        # the caller's user-facing message must not tell a cloud-connected
        # account to run `brnrd connect` when the real story is e.g. a 422
        # (server allowlist out of lockstep — observed live 2026-07-11) or
        # a deploy-window 502. The error detail is the actionable part.
        print(f"[brnrd:cloud] config-change proposal mint failed: {e}")
        return {"error": str(e)}


def connect(brr_dir: Path, *, brnrd_url: str, daemon_name: str = _DEFAULT_DAEMON_NAME, poll_interval_s: float = 2.0, timeout_s: float = 600.0, out: Callable[[str], None] = print) -> dict:
    pair = _request(brnrd_url, "POST", "/v1/accounts/pair")
    out(f"[brnrd] Approve this daemon at: {pair['pair_url']}")
    deadline = time.monotonic() + timeout_s
    while True:
        status = _request(brnrd_url, "GET", f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": pair["poll_secret"]})
        if status.get("status") == "paired" and status.get("daemon_token"):
            break
        if time.monotonic() > deadline:
            raise TimeoutError("pairing timed out — re-run `brnrd connect`")
        time.sleep(poll_interval_s)
    state = _load_state(brr_dir)
    capabilities = dict(state.get("capabilities") or {})
    capabilities.update(_repo_capabilities(brr_dir))
    state.update({
        "brnrd_url": brnrd_url.rstrip("/"),
        "token": status["daemon_token"],
        "account_id": status.get("account_id"),
        "repo_id": status["repo_id"],
        "daemon_name": daemon_name,
        "capabilities": capabilities,
        "since": state.get("since", 0),
    })
    _save_state(brr_dir, state)
    out(f"[brnrd] Connected to brnrd repo {status['repo_id']}.")
    pair = status.get("telegram_pair") or {}
    if isinstance(pair, dict):
        deep_link = str(pair.get("deep_link") or "").strip()
        instructions = str(pair.get("instructions") or "").strip()
        pair_code = str(pair.get("pair_code") or "").strip()
        if deep_link:
            out(f"[brnrd] Pair Telegram chat: {deep_link}")
            if pair_code:
                out(f"[brnrd] If Telegram only opens the chat, send: /start {pair_code}")
        elif instructions:
            out(f"[brnrd] Telegram pairing: {instructions}")
    return state


def setup(brr_dir: Path) -> None:
    print("[brnrd] Run `brnrd connect` to link this daemon to a brnrd repo.")


def auth(brr_dir: Path) -> None:
    setup(brr_dir)


def bind(brr_dir: Path) -> None:
    setup(brr_dir)


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    registered = False
    try:
        _register(brr_dir, state)
        registered = True
    except Exception as e:
        # A failed register is never fatal, not even a 401. The hosted brnrd
        # answers with whatever its router has during a deploy window, and a
        # daemon that gives up here is a daemon whose cloud gate is dead until
        # someone notices — which is exactly what a restart *during* the
        # outage used to reproduce (2026-07-12: messages to the cloud bot
        # vanished, the restart didn't help, the daemon looked healthy).
        # The poll loop below re-attempts registration once it gets through.
        print(f"[brnrd:cloud] register failed: {e}, will retry")
    threading.Thread(
        target=_dashboard_publish_loop,
        args=(brr_dir, inbox_dir),
        daemon=True,
        name="cloud-dashboard-publish",
    ).start()
    backoff = 1
    auth_backoff = _AUTH_RETRY_MIN_S
    while True:
        try:
            _loop_once(brr_dir, inbox_dir, responses_dir)
            backoff = 1
            auth_backoff = _AUTH_RETRY_MIN_S
            if not registered:
                try:
                    _register(brr_dir, _load_state(brr_dir))
                    registered = True
                    print("[brnrd:cloud] re-registered after recovery")
                except Exception as e:  # keep draining; capabilities lag, chat works
                    print(f"[brnrd:cloud] re-register failed: {e}")
        except BrnrdAuthError as e:
            # 401 used to end the thread. Auth failure is not reliably
            # permanent — a mid-deploy router, a cold start, a token the
            # server re-issues — and the failure mode of exiting is the worst
            # one available: chat messages disappear with no error anywhere
            # the user can see, while `brr up` keeps reporting a healthy
            # daemon. Retrying at a slow cadence costs one request per five
            # minutes and keeps a genuinely bad token loudly visible instead
            # of silently terminal.
            print(f"[brnrd:cloud] auth failed: {e}, retrying in {auth_backoff}s")
            time.sleep(auth_backoff)
            auth_backoff = min(auth_backoff * 2, _AUTH_RETRY_CAP_S)
        except Exception as e:
            print(f"[brnrd:cloud] error: {e}, retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)


def _dashboard_publish_tick(brr_dir: Path, inbox_dir: Path) -> None:
    """One publish pass — see ``_dashboard_publish_loop`` for why it exists.

    Split out from the loop so a test can drive a single tick without
    threading or monkeypatching ``time.sleep`` on a ``while True``.
    """
    state = _load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return
    # Runners first: its response piggybacks the pending wake request
    # (#328 tap-to-request), the one dispatch-relevant datum in this tick.
    # Behind the others, a slow or 502-retrying dashboard PUT stretched the
    # mirror's staleness to tens of seconds — long enough for a tap racing
    # its own follow-up message to lose (found live 2026-07-11).
    _publish_runners(brr_dir, state)
    _publish_activity(brr_dir, inbox_dir, state)
    _publish_plans(brr_dir, state)
    _publish_quota(brr_dir, state)
    _publish_live_runs(brr_dir, state)
    _publish_pr_review_queue(brr_dir, state)
    _publish_run_ledger(brr_dir, state)


def _dashboard_publish_loop(brr_dir: Path, inbox_dir: Path) -> None:
    """Publish the dashboard snapshots on their own short cadence.

    This thread is the *only* publisher. ``_loop_once`` used to publish once
    per inbox long-poll return too, on the theory that duplicate publishes
    were "harmless, idempotent overwrites" — they weren't: two threads
    PUTting the same activity snapshot concurrently raced the server's
    delete-then-insert replace into ``UniqueViolation`` 500s (seen live
    2026-07-09 as ``PUT /v1/daemons/activity -> 502`` spam). One publisher,
    no race. This loop is also what actually delivers on "a live
    dashboard": `_loop_once`'s cadence is capped at ``_POLL_WAIT_S`` (25s,
    chosen for chat responsiveness) whether or not any inbox event ever
    arrives. See kb/plan-loom-realtime-build.md slice 0.
    """
    while True:
        try:
            _dashboard_publish_tick(brr_dir, inbox_dir)
        except Exception as e:
            print(f"[brnrd:cloud] dashboard publish loop error: {e}")
        time.sleep(_DASHBOARD_PUBLISH_INTERVAL_S)


def _register(brr_dir: Path, state: dict) -> None:
    caps = dict(state.get("capabilities") or {})
    caps.update(_repo_capabilities(brr_dir))
    _request(state["brnrd_url"], "POST", "/v1/daemons/register", token=state["token"], json={"daemon_name": state.get("daemon_name", _DEFAULT_DAEMON_NAME), "capabilities": caps})


def _origin_meta(reply_to: dict) -> dict:
    platform = reply_to.get("platform") or ""
    meta: dict[str, object] = {"cloud_platform": platform, "cloud_chat_id": "", "cloud_topic_id": ""}
    if platform == "telegram":
        chat_id = reply_to.get("chat_id")
        topic_id = reply_to.get("topic_id")
        meta["cloud_chat_id"] = "" if chat_id is None else chat_id
        meta["cloud_topic_id"] = "" if topic_id is None else topic_id
        copies = {"message_id": "cloud_message_id", "user": "cloud_user", "user_id": "cloud_user_id", "username": "cloud_username"}
        for src, dst in copies.items():
            value = reply_to.get(src)
            if value not in (None, ""):
                meta[dst] = value
        return meta
    if platform == "github":
        repo = str(reply_to.get("repo") or "")
        issue_number = reply_to.get("issue_number")
        meta["cloud_chat_id"] = f"{repo}#{issue_number}" if repo and issue_number not in (None, "") else ""
        copies = {"repo": "github_repo", "kind": "github_kind", "issue_number": "github_issue_number", "comment_id": "github_comment_id", "author": "github_author", "html_url": "github_html_url", "trigger": "github_trigger", "mention": "github_mention", "pr_number": "github_pr_number", "branch_target": "branch_target"}
        for src, dst in copies.items():
            value = reply_to.get(src)
            if value not in (None, ""):
                meta[dst] = value
    return meta


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    since = state.get("since", 0)
    result = _request(state["brnrd_url"], "GET", "/v1/daemons/inbox", token=state["token"], params={"since": since, "wait": _POLL_WAIT_S})
    events = result.get("events", [])
    for ev in events:
        protocol.create_event(inbox_dir, source="cloud", body=ev.get("body") or "", cloud_event_id=ev["event_id"], **_origin_meta(ev.get("reply_to") or {}))
    cursor = result.get("cursor", since)
    if cursor != since:
        # Trust the server's cursor in both directions: it moves up as
        # events deliver, and moves *down* when the server detects a
        # cursor from an older DB epoch and heals it (brnrd
        # ``inbox_service.clamp_since``). Rejecting the lower value kept a
        # stale cursor stale forever.
        state["since"] = cursor
        _save_state(brr_dir, state)
    _deliver_responses(brr_dir, inbox_dir, responses_dir, state)


def _deliver_responses(brr_dir: Path, inbox_dir: Path, responses_dir: Path, state: dict) -> None:
    def deliver(event: dict, body: str) -> None:
        cloud_event_id = event.get("cloud_event_id")
        if not cloud_event_id:
            raise RuntimeError("missing cloud_event_id")
        limit = _RESPONSE_LIMITS.get(event.get("cloud_platform") or "")
        if limit is not None:
            body = delivery.resolve_overflow(body, limit=limit, gist_fn=delivery.post_gist)
        _request(state["brnrd_url"], "POST", "/v1/daemons/responses", token=state["token"], json={"event_id": cloud_event_id, "body_markdown": body, "status": "done"})
    runtime.deliver_responses(inbox_dir, responses_dir, "cloud", deliver)


def _iso_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _iso_from_event(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _summary(text: str, *, limit: int = 140) -> str:
    one_line = " ".join((text or "").split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1].rstrip() + "…"


def _runner_payload(meta: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    name = str(meta.get("runner_name") or meta.get("shell") or "").strip()
    shell = str(meta.get("runner_shell") or meta.get("shell") or "").strip()
    core = str(meta.get("runner_core") or meta.get("core") or "").strip()
    klass = str(meta.get("runner_class") or "").strip()
    if name:
        out["name"] = name
    if shell:
        out["shell"] = shell
    elif name:
        out["shell"] = name
    if core:
        out["core"] = core
    if klass:
        out["class"] = klass
    return out


def _run_activity_records(brr_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    runs_dir = brr_dir / "runs"
    for task in list_runs(runs_dir):
        if task.status not in {"pending", "running"}:
            continue
        manifest = run_manifest_path(runs_dir, task.id)
        try:
            stat = manifest.stat()
        except OSError:
            stat = None
        updated = _iso_from_epoch(stat.st_mtime if stat else None)
        started = _iso_from_epoch(stat.st_ctime if stat else None)
        records.append(
            {
                "id": f"run:{task.id}",
                "kind": "run",
                "source": task.source,
                "conversation_key": task.conversation_key,
                "summary": _summary(task.body) or task.event_id,
                "runner": _runner_payload(task.meta),
                "status": task.status,
                "phase": str(task.meta.get("publish_status") or ""),
                "branch": (
                    str(task.meta.get("branch_name") or task.meta.get("publish_branch") or "")
                    if task.meta.get("has_new_commit") is True else ""
                ),
                "pr_number": task.meta.get("pr_number"),
                "started_at": started,
                "updated_at": updated,
                "links": {},
            }
        )
    return records


def _schedule_activity_records(brr_dir: Path) -> list[dict[str, Any]]:
    try:
        from .. import config as conf

        cfg = conf.load_config(brr_dir.parent)
        dom = None
        for candidate in dominion.resident_dominion_candidates(brr_dir.parent, cfg):
            if candidate.path.is_dir():
                dom = candidate.path
                break
        if dom is None:
            return []
        entries = schedule_mod.parse_schedule(dom)
    except Exception:
        return []
    state = schedule_mod.load_state(brr_dir)
    records: list[dict[str, Any]] = []
    for entry in entries:
        scheduled_for: float | None = None
        status = "scheduled"
        if entry.kind == "at":
            rec = state.get(entry.id) or {}
            if rec.get("fired"):
                continue
            scheduled_for = entry.at
        elif entry.kind == "every":
            rec = state.get(entry.id) or {}
            last = rec.get("last_fired")
            try:
                last_fired = float(last)
            except (TypeError, ValueError):
                last_fired = None
            if last_fired is not None and entry.interval:
                scheduled_for = last_fired + entry.interval
            status = "recurring"
        records.append(
            {
                "id": f"schedule:{entry.id}",
                "kind": "scheduled",
                "source": "schedule",
                "conversation_key": entry.conversation_key or f"schedule:{entry.id}",
                "summary": _summary(entry.body) or f"self-scheduled thought: {entry.id}",
                "runner": {},
                "status": status,
                "phase": entry.kind,
                "scheduled_for": _iso_from_epoch(scheduled_for),
                "links": {},
            }
        )
    return records


def _respawn_activity_records(inbox_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event in protocol.list_pending(inbox_dir):
        parent = str(event.get("respawned_from_event") or "").strip()
        if not parent:
            continue
        deferred = protocol.event_is_deferred(event)
        records.append(
            {
                "id": f"respawn:{event.get('id')}",
                "kind": "respawn",
                "source": str(event.get("source") or ""),
                "conversation_key": str(event.get("conversation_key") or ""),
                "summary": _summary(str(event.get("body") or "")) or parent,
                "runner": _runner_payload(event),
                "status": "scheduled" if deferred else str(event.get("status") or "pending"),
                "phase": str(event.get("respawn_reason") or ""),
                "branch": str(event.get("branch") or event.get("branch_target") or ""),
                "pr_number": event.get("pr_number") or event.get("github_pr_number"),
                "defer_until": _iso_from_event(event.get("defer_until")),
                "links": {},
            }
        )
    return records


def _activity_snapshot(brr_dir: Path, inbox_dir: Path) -> list[dict[str, Any]]:
    return [
        *_run_activity_records(brr_dir),
        *_schedule_activity_records(brr_dir),
        *_respawn_activity_records(inbox_dir),
    ]


def _publish_activity(brr_dir: Path, inbox_dir: Path, state: dict) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    try:
        _request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/activity",
            token=state["token"],
            json={"records": _activity_snapshot(brr_dir, inbox_dir)},
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] activity publish failed: {e}")


def _plans_snapshot(brr_dir: Path) -> dict[str, str] | None:
    """Read CS5/CS7 plan + ledger files for CPS mirroring, if resolvable.

    Returns ``None`` (not published) rather than raising when the account
    dominion can't be resolved read-only — a plain repo-local `.brr/`
    without an account context is a normal, supported shape, not an error.
    """
    from .. import account as account_mod

    repo_root = brr_dir.parent
    try:
        ctx = account_mod.resolve_context(repo_root, create=False)
        label = account_mod.repo_label(repo_root)
        repo_plan = account_mod.active_plan_path(ctx, label)
        cross_plan = account_mod.cross_repo_plans_path(ctx) / "active.md"
        ledger = account_mod.decisions_ledger_path(ctx)
        return {
            "repo_plan_md": repo_plan.read_text() if repo_plan.exists() else "",
            "cross_repo_plan_md": cross_plan.read_text() if cross_plan.exists() else "",
            "decision_ledger_md": ledger.read_text() if ledger.exists() else "",
        }
    except Exception as e:
        print(f"[brnrd:cloud] plans snapshot skipped: {e}")
        return None


def _publish_plans(brr_dir: Path, state: dict) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    snapshot = _plans_snapshot(brr_dir)
    if snapshot is None:
        return
    try:
        _request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/plans",
            token=state["token"],
            json=snapshot,
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] plans publish failed: {e}")


def _quota_window(
    label: str,
    percent: float | None,
    reset: str | None = None,
    resets_at: float | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "used": None,
        "limit": None,
        "percent": percent,
        "reset": reset,
        # Machine-parseable reset instant (unix epoch seconds), alongside the
        # display-text `reset` above — the window-track visual's time-
        # remaining axis needs this, `reset` alone is prose (2026-07-06,
        # kb/design-dashboard-live-surface.md "Shipped" gap this closes).
        "resets_at": resets_at,
    }


def _codex_quota_shell(brr_dir: Path) -> dict[str, Any] | None:
    """Codex's quota row: the app-server probe, backstopped by the rollout read.

    The rollout read alone *does* have an idle-window gap — the comment here
    used to deny it ("live every loop tick, no idle-window gap the way Claude's
    cached PTY scrape has"), and that was simply wrong: nothing writes a
    ``token_count`` event between runs, so an idle Codex froze this row until the
    dashboard aged it out to ``stale`` (#312 made that honest, #315 asked for it
    to stop happening). The active ``codex app-server`` probe closes it — an
    account-metadata call that needs no run and spends no quota — on the same
    bounded idle cadence the Claude row already refreshes on.
    """
    levels = codex_usage.merge_levels(
        codex_usage.load_or_refresh_snapshot(
            brr_dir,
            max_age_seconds=_CODEX_QUOTA_PUBLISH_MAX_AGE_SECONDS,
            timeout_seconds=10.0,
        ),
        codex_status.load_levels(),
    )
    quota = levels.get("quota") if isinstance(levels, dict) else None
    if not isinstance(quota, dict):
        return None
    primary = quota.get("primary_remaining_percent")
    secondary = quota.get("secondary_remaining_percent")
    if primary is None and secondary is None:
        return None
    return {
        "shell": "codex",
        "status": "known",
        # The reading's own capture time, not "now" — whichever seam supplied the
        # quota stamped it (`merge_levels` carries that stamp through), so the
        # dashboard measures staleness off the same clock for both shells and a
        # failed probe can never make a frozen rollout look live.
        "updated_at": levels.get("updated_at"),
        "windows": [
            _quota_window("5h window", primary, resets_at=quota.get("primary_resets_at")),
            _quota_window("weekly", secondary, resets_at=quota.get("secondary_resets_at")),
        ],
        # Free "Full reset (Weekly + 5 hr)" grants sitting unredeemed on the
        # account — only the app-server seam knows about these, and a quota row
        # that reads 4% left while four resets go unused is telling half a truth.
        "reset_credits": quota.get("reset_credits_available"),
        # Claude's shell carries a proven per-run USD figure in `credits`
        # (`_claude_credits_block`, sourced from the headless result JSON's
        # `total_cost_usd`); Codex's CLI result JSON has no equivalent
        # accounting field, so there is nothing bounded to read here — named
        # explicitly rather than just omitting the key, which reads
        # identically to "unknown" from the dashboard (brnrd.dev live-run
        # dashboard posture, 2026-07-13: "do not fabricate or infer spend
        # from model names").
        "spend": {
            "status": "unimplemented",
            "reason": "no per-run cost figure in the Codex CLI's result JSON yet",
        },
    }


def _claude_week_model_windows(
    levels: dict[str, Any], buckets: dict[str, Any]
) -> list[dict[str, Any]]:
    """Per-model weekly windows (Fable's own pool today) as real windows.

    ``claude_usage.parse_usage_text`` already parses ``Current week
    (Fable)`` alongside the primary ``Current week`` line into
    ``levels["week_models"][label]`` (full reset info) and the deduped
    percentage into ``quota.buckets.week_models[label]`` — but until now
    nothing here ever read either, so a Fable-heavy account's own weekly
    pool was silently dropped from the dashboard: not wrong, just never
    published, which reads identically to "unknown" from the outside (the
    brnrd.dev live-run dashboard report this closes, 2026-07-13). One window
    per labeled model, sorted for a stable publish order.
    """
    bucket_pcts = buckets.get("week_models") if isinstance(buckets, dict) else None
    meta = levels.get("week_models") if isinstance(levels, dict) else None
    if not isinstance(bucket_pcts, dict):
        return []
    out: list[dict[str, Any]] = []
    for label in sorted(bucket_pcts):
        bucket = bucket_pcts.get(label)
        if not isinstance(bucket, dict):
            continue
        pct = bucket.get("remaining_percentage")
        if pct is None:
            continue
        label_meta = meta.get(label) if isinstance(meta, dict) else None
        reset = label_meta.get("reset") if isinstance(label_meta, dict) else None
        resets_at = label_meta.get("resets_at") if isinstance(label_meta, dict) else None
        out.append(_quota_window(f"weekly ({label})", pct, reset, resets_at))
    return out


def _claude_quota_shell(brr_dir: Path) -> dict[str, Any] | None:
    outbox_dir = runner_quota.latest_claude_usage_outbox_dir(brr_dir)
    levels = (
        claude_usage.load_or_refresh_snapshot(
            outbox_dir,
            cwd=brr_dir,
            max_age_seconds=_CLAUDE_QUOTA_PUBLISH_MAX_AGE_SECONDS,
            timeout_seconds=10.0,
            wait_for_credits=True,
        )
        if outbox_dir else None
    )
    quota = levels.get("quota") if isinstance(levels, dict) else None
    buckets = quota.get("buckets") if isinstance(quota, dict) else None
    credits = _claude_credits_block(brr_dir, usage_levels=levels)
    if not isinstance(buckets, dict):
        if credits is None:
            return None
        buckets = {}
    session = (
        buckets.get("session") if isinstance(buckets.get("session"), dict) else {}
    )
    week = buckets.get("week") if isinstance(buckets.get("week"), dict) else {}
    session_pct = session.get("remaining_percentage")
    week_pct = week.get("remaining_percentage")
    week_model_windows = _claude_week_model_windows(
        levels if isinstance(levels, dict) else {}, buckets
    )
    if (
        session_pct is None and week_pct is None
        and not week_model_windows and credits is None
    ):
        return None
    return {
        "shell": "claude",
        "status": "known",
        # The scrape's own capture time, not "now". The cloud publisher now
        # refreshes the cached PTY probe on a bounded idle cadence, but the
        # dashboard still measures freshness off this field so a failed or
        # skipped refresh cannot make old data look live.
        "updated_at": levels.get("updated_at"),
        "windows": [
            _quota_window(
                "5h window", session_pct, levels.get("session_reset"), levels.get("session_resets_at")
            ),
            _quota_window(
                "weekly", week_pct, levels.get("week_reset"), levels.get("week_resets_at")
            ),
            *week_model_windows,
        ],
        "credits": credits,
    }


def _claude_credits_block(
    brr_dir: Path,
    *,
    usage_levels: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Claude credits evidence from `/usage` plus per-run spend, when proven.

    ``usage_levels["usage_credits"]`` is Claude's account credit-balance
    surface from the interactive ``/usage`` panel (amount spent / cap /
    reset). Separately, the run-scoped ``total_cost_usd`` in the headless
    result JSON is
    an internal accounting figure, not a real charge. It becomes real dollars
    the moment the subscription's 5h/weekly window is exhausted and Anthropic
    falls the account through to metered credits (confirmed live 2026-07-07:
    a maintainer-observed run kept working straight through an exhausted 5h
    window, billed ~$1 in credits) — so this is not a projection, it is the
    same terminal-JSON field :mod:`brr.claude_status` already collects for
    the boot-prompt ``spend`` facet, just never published to the dashboard
    before now. ``None`` when no run has ever produced one (cold cache, or a
    Codex-only daemon).
    """
    outbox_dir = runner_quota.latest_claude_spend_outbox_dir(brr_dir)
    levels = claude_status.load_snapshot(outbox_dir) if outbox_dir else None
    spend = levels.get("spend") if isinstance(levels, dict) else None
    usage = (
        usage_levels.get("usage_credits")
        if isinstance(usage_levels, dict) else None
    )
    total = spend.get("total_cost_usd") if isinstance(spend, dict) else None
    if not isinstance(usage, dict) and total is None:
        return None
    block = {
        "total_cost_usd": total,
        "summary": spend.get("summary") if isinstance(spend, dict) else None,
        "updated_at": levels.get("updated_at") if isinstance(levels, dict) else None,
    }
    if isinstance(usage, dict):
        block.update(
            {
                "enabled": usage.get("enabled"),
                "used_percentage": usage.get("used_percentage"),
                "remaining_percentage": usage.get("remaining_percentage"),
                "spent_amount": usage.get("spent_amount"),
                "limit_amount": usage.get("limit_amount"),
                "currency": usage.get("currency"),
                "reset": usage.get("reset"),
                "resets_at": usage.get("resets_at"),
                "summary": usage.get("summary") or block.get("summary"),
                "run_spend_summary": spend.get("summary") if isinstance(spend, dict) else None,
                "updated_at": (
                    usage_levels.get("updated_at")
                    if isinstance(usage_levels, dict) else block.get("updated_at")
                ),
            }
        )
    return block


def _quota_snapshot(brr_dir: Path) -> list[dict[str, Any]]:
    """This daemon's runner-quota snapshot: real per-shell 5h/weekly windows.

    Mirrors the Activity/Plans publish shape (#237) — reads whatever local
    evidence already exists (Codex's live rollout read, Claude's cached
    ``/usage`` scrape via :func:`runner_quota.latest_claude_usage_outbox_dir`).
    Claude's cached scrape is refreshed here on a bounded idle cadence shorter
    than the dashboard's stale threshold, not on every publish tick. A shell
    with no evidence yet is omitted, not reported as a fake zero.
    """
    shells = [_claude_quota_shell(brr_dir), _codex_quota_shell(brr_dir)]
    return [shell for shell in shells if shell is not None]


def _publish_quota(brr_dir: Path, state: dict) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    try:
        _request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/quota",
            token=state["token"],
            json={"shells": _quota_snapshot(brr_dir)},
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] quota publish failed: {e}")


def _runners_snapshot(brr_dir: Path) -> dict[str, Any]:
    """This daemon's runner catalog: locally-discovered Shell+Core profiles.

    #328's spool rack, daemon-owned discovery: the same PATH-filtered,
    probe-augmented projection the Run Context Bundle's "Runner catalog"
    block injects into every wake (`runner.available_runner_catalog` —
    Core registry + `runner_cores.probe_shell_models`, no network).
    ``default`` is the profile `resolve_runner` resolves for a plain wake
    right now — the ``shell=``/``core=`` config pin, or the cost-aware
    selection when unpinned. Publishing the *discovered* view (not the
    packaged registry alone) is deliberate: installed shells update on
    their own clock, and a hardcoded menu rots silently (#343).
    """
    from .. import runner

    repo_root = brr_dir.parent
    default: str | None
    try:
        default = runner.resolve_runner(repo_root)
    except Exception:
        default = None
    try:
        profiles = runner.available_runner_catalog(repo_root, selected=default)
    except Exception as e:
        print(f"[brnrd:cloud] runner catalog read failed: {e}")
        profiles = []
    return {"profiles": profiles, "default": default}


def _publish_runners(brr_dir: Path, state: dict) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    payload = _runners_snapshot(brr_dir)
    # #328 tap-to-request: ack wake requests a dispatched wake has spent,
    # and mirror back the account's still-pending one (if any). Same
    # publish tick, no extra request — see src/brr/wake_request.py.
    acked = wake_request.consumed_ids(brr_dir)
    payload["consumed_wake_request_ids"] = acked
    try:
        body = _request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/runners",
            token=state["token"],
            json=payload,
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] runners publish failed: {e}")
        return
    wake_request.clear_consumed(brr_dir, acked)
    pending = body.get("pending_wake_request") if isinstance(body, dict) else None
    wake_request.store_pending(
        brr_dir, pending if isinstance(pending, dict) else None,
    )


def _live_run_progress(brr_dir: Path, stream: str, run_id: str) -> run_progress.RunProgressView | None:
    """Best-effort progress projection for one live presence entry.

    Swallows its own failure — a malformed or half-written conversation
    log for one run must not take down the whole publish tick for every
    other live run.
    """
    if not stream or not run_id:
        return None
    try:
        return run_progress.project_run(brr_dir, stream, run_id)
    except Exception:
        return None


def _live_runs_snapshot(brr_dir: Path) -> list[dict[str, Any]]:
    """This daemon's live/coexisting-runs snapshot (#258).

    Reads the local presence registry (``src/brr/presence.py``) — every
    daemon-dispatched thought (and any ad-hoc session) already registers
    and heartbeats there, so this is a publish step over data that already
    exists, the same shape as Activity/Plans/Quota (#237). No new
    collection mechanism, just the account-scoped visibility those three
    don't give: "what is my daemon doing right now, across every repo it
    touches" (`kb/design-dashboard-live-surface.md` §"Reconsidered
    2026-07-06").

    #200's remaining slice: phase / progress-card richness, folded into
    this same publish tick rather than a new transport — ``project_run``
    (already used by the chat gates to render the compact card) gives us
    both ``phase`` and the live ``.card`` note text from the same
    per-conversation record projection. Read cost is real (``read_records``
    loads the whole conversation log, not a tail) and this now pays it once
    per active run per publish tick (~25-30s) instead of only on card
    writes — acceptable for a first cut, worth revisiting with a tailed
    read if a busy thread's log makes this tick hot. Budget/keepalive
    posture is deliberately *not* included here: that state lives only in
    the worker's in-memory loop today (``daemon.py``'s
    ``_keepalive_until``/budget tracking), nothing persists it yet, so it
    would need new state-threading, not just a read — named as the
    remaining gap rather than guessed at.
    """
    out: list[dict[str, Any]] = []
    for entry in presence.list_active(brr_dir):
        stream = str(entry.get("stream") or "")
        run_id = str(entry.get("run_id") or "")
        view = _live_run_progress(brr_dir, stream, run_id)
        out.append(
            {
                "id": str(entry.get("id") or ""),
                "kind": str(entry.get("kind") or ""),
                "stream": stream,
                "label": str(entry.get("label") or ""),
                "run_id": run_id,
                "repo_label": str(entry.get("repo_label") or ""),
                "started_at": _iso_from_epoch(entry.get("started_at")),
                "last_seen": _iso_from_epoch(entry.get("last_seen")),
                # Joins the live view to the same parent/child shape the
                # closed-run ledger already carries (run_ledger.py's
                # `parent_run_id`/`is_subspawn`) — named as a gap and
                # closed in kb/design-multi-workstream-concurrency.md
                # "Ranked moves" #1: a running `spawn:` child is now
                # distinguishable from a resident thought *while it's
                # still live*, not only after it closes into the ledger.
                "parent_run_id": str(entry.get("parent_run_id") or "") or None,
                "is_subspawn": bool(entry.get("is_subspawn")),
                # Shell+Core the running thought is on — same
                # name/shell/core/class shape `_runner_payload` already
                # produces for Activity/respawn rows, now carried on the
                # presence entry itself (`presence.register`'s runner_*
                # kwargs) so the live view can answer "which Runner is this"
                # while a run is still in flight, not only after it closes
                # into the ledger (brnrd.dev live-run dashboard posture,
                # 2026-07-13). ``{}`` when the entry predates this field or
                # no runner was selected yet (ad-hoc session presence).
                "runner": _runner_payload(entry),
                # #200 remaining slice: live phase + progress-card note,
                # None when there's no conversation record yet (a
                # just-registered entry) or projection failed.
                "phase": (view.phase if view is not None else None) or None,
                "card_text": (view.agent_card_text if view is not None else None) or None,
                "card_updated_at": (view.agent_card_updated_at if view is not None else None) or None,
            }
        )
    return out


def _spawn_pool_width(brr_dir: Path) -> int:
    """Configured ``spawn:`` pool width (``spawn.max_concurrent``), for the
    loom-envelope Phase 1 limits panel (`kb/design-multi-workstream-
    concurrency.md` §"Loom envelope").

    Piggybacked on the live-runs publish tick rather than a new endpoint —
    the *active* count is already derivable from ``is_subspawn`` entries in
    ``_live_runs_snapshot`` above, this is the one number that publish
    doesn't already carry. Reuses ``daemon._max_concurrent_spawns``'s own
    clamped-default parsing via a deferred import rather than duplicating
    it: ``daemon.py`` already does a deferred ``from .gates import cloud``
    (see its own comment there), so importing the other direction here has
    to stay deferred too, executed at runtime after both modules are
    fully loaded, not at import time.
    """
    from .. import config as _config
    from ..daemon import _max_concurrent_spawns

    cfg = _config.load_config(brr_dir.parent)
    return _max_concurrent_spawns(cfg)


def _publish_live_runs(brr_dir: Path, state: dict) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    try:
        _request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/live-runs",
            token=state["token"],
            json={
                "runs": _live_runs_snapshot(brr_dir),
                "spawn_max_concurrent": _spawn_pool_width(brr_dir),
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] live-runs publish failed: {e}")


def _github_repo_label(label: str, repo_root: Path) -> str | None:
    try:
        remote = gitops.default_remote(repo_root)
        if remote:
            url = gitops.remote_url(repo_root, remote)
            if url:
                parsed = parse_origin_url(url)
                if parsed:
                    return parsed
    except Exception:
        pass
    text = str(label or "").strip()
    if text.count("/") == 1 and all(part.strip() for part in text.split("/", 1)):
        return text
    return None


def _pr_review_repo_labels(brr_dir: Path) -> list[str]:
    from .. import account as account_mod

    repo_root = brr_dir.parent
    try:
        ctx = account_mod.resolve_context(repo_root, create=False)
        repos = ctx.repos
    except Exception:
        repos = {account_mod.repo_label(repo_root): account_mod.AccountRepo(label=account_mod.repo_label(repo_root), root=repo_root)}

    out: list[str] = []
    seen: set[str] = set()
    for label, repo in sorted(repos.items()):
        repo_label = _github_repo_label(label, repo.root)
        if repo_label is None:
            continue
        key = repo_label.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(repo_label)
    return out


def _pr_review_snapshot(brr_dir: Path) -> list[dict[str, Any]]:
    """This daemon's account-scoped open-PR review queue (#259).

    Mirrors the Activity/Plans/Quota/Live-runs publish shape: collect local
    daemon evidence with the same ``gh`` dependency the director tick already
    uses, then let brnrd store the latest snapshot. The dashboard derives age
    from ``created_at``; this layer deliberately does not manufacture urgency.
    """
    prs: list[dict[str, Any]] = []
    for repo_label in _pr_review_repo_labels(brr_dir):
        cmd = [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,url,createdAt,isDraft,author,headRefName",
            "--repo",
            repo_label,
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=brr_dir.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("gh not found; install/authenticate GitHub CLI to publish PR review queue") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"gh pr list timed out for {repo_label}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"gh pr list failed for {repo_label}: {detail}")
        try:
            rows = json.loads(result.stdout or "[]")
        except ValueError as exc:
            raise RuntimeError(f"gh pr list returned invalid JSON for {repo_label}") from exc
        if not isinstance(rows, list):
            raise RuntimeError(f"gh pr list returned non-list JSON for {repo_label}")
        for row in rows:
            if not isinstance(row, dict):
                continue
            author = row.get("author")
            author_login = str(author.get("login") or "") if isinstance(author, dict) else str(author or "")
            number = row.get("number")
            try:
                number_int = int(number)
            except (TypeError, ValueError):
                continue
            prs.append(
                {
                    "number": number_int,
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                    "repo_label": repo_label,
                    "created_at": str(row.get("createdAt") or ""),
                    "draft": bool(row.get("isDraft")),
                    "author": author_login,
                }
            )
    return prs


def _publish_pr_review_queue(brr_dir: Path, state: dict) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    try:
        _request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/pr-review-queue",
            token=state["token"],
            json={"prs": _pr_review_snapshot(brr_dir)},
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] pr-review-queue publish failed: {e}")


def _run_ledger_snapshot(brr_dir: Path) -> list[dict[str, Any]]:
    """This daemon's recent closed-run receipt rows (#271).

    Reads the local-first ``.brr/run-ledger.jsonl`` written at run closeout.
    Missing files and malformed lines are not publish failures: the ledger
    invariant is "unavailable evidence becomes absent/null, not a closeout or
    dashboard failure."
    """
    path = run_ledger.ledger_path(brr_dir.parent)
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = deque(handle, maxlen=20)
    except FileNotFoundError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _publish_run_ledger(brr_dir: Path, state: dict) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    try:
        _request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/run-ledger",
            token=state["token"],
            json={"rows": _run_ledger_snapshot(brr_dir)},
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] run-ledger publish failed: {e}")


class _CloudCardTransport:
    def __init__(self, state: dict, event_id: str) -> None:
        self._state = state
        self._event_id = event_id

    def _post(self, body: dict) -> dict:
        return _request(self._state["brnrd_url"], "POST", "/v1/daemons/card", token=self._state["token"], json=body)

    def send(self, text: str, *, reply_to: int | None = None) -> int | None:
        return self._post({"event_id": self._event_id, "text": text}).get("message_id")

    def edit(self, message_id: int, text: str) -> None:
        self._post({"event_id": self._event_id, "text": text, "message_id": message_id})


def _card_text_for(brr_dir: Path, conv_key: str, run_id: str, platform: str) -> str | None:
    if platform == "telegram":
        from . import telegram
        return telegram.card_text(brr_dir, conv_key, run_id)
    return None


def render_update(brr_dir: Path, packet: Any) -> None:
    if getattr(packet, "type", None) not in run_progress.CARD_PACKETS:
        return
    state = _load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return
    conv_key = getattr(packet, "conversation_key", "") or ""
    run_id = run_progress.run_id_from_packet(packet)
    if not conv_key or not run_id:
        return
    task = Run.from_file(run_manifest_path(brr_dir / "runs", run_id))
    if task is None or task.source != "cloud":
        return
    cloud_event_id = task.meta.get("cloud_event_id")
    if not cloud_event_id:
        return
    text = _card_text_for(brr_dir, conv_key, run_id, str(task.meta.get("cloud_platform") or ""))
    if text is None:
        return
    delivery.update_card(brr_dir, "cloud", run_id, text, transport=_CloudCardTransport(state, str(cloud_event_id)), render_tag=getattr(packet, "type", None))
