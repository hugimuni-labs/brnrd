"""Cloud gate — drains a brnrd repo inbox into the local ``.brr/``."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .. import gitops, protocol, run_progress
from .. import dominion, schedule as schedule_mod
from ..gates.github.parse import parse_origin_url
from ..run import Run, list_runs, run_manifest_path
from . import delivery, runtime

_POLL_WAIT_S = 25
_HTTP_TIMEOUT_S = 60
_DEFAULT_DAEMON_NAME = "daemon"
_RESPONSE_LIMITS = {"telegram": 3900}
_SESSION = requests.Session()


class BrnrdAuthError(RuntimeError):
    pass


_AUTH_HINT = "Re-run `brnrd connect` to link this daemon to your brnrd repo."


def _request(base_url: str, method: str, path: str, *, token: str | None = None, json: dict | None = None, params: dict | None = None, timeout: float = _HTTP_TIMEOUT_S) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = _SESSION.request(method, base_url.rstrip("/") + path, json=json, params=params, headers=headers, timeout=timeout)
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
        print(f"[brr:cloud] pack relay failed: {e}")
        return None
    url = result.get("render_url")
    return url if isinstance(url, str) and url else None


def connect(brr_dir: Path, *, brnrd_url: str, daemon_name: str = _DEFAULT_DAEMON_NAME, poll_interval_s: float = 2.0, timeout_s: float = 600.0, out: Callable[[str], None] = print) -> dict:
    pair = _request(brnrd_url, "POST", "/v1/accounts/pair")
    out(f"[brr] Approve this daemon at: {pair['pair_url']}")
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
    out(f"[brr] Connected to brnrd repo {status['repo_id']}.")
    return state


def setup(brr_dir: Path) -> None:
    print("[brr] Run `brnrd connect` to link this daemon to a brnrd repo.")


def auth(brr_dir: Path) -> None:
    setup(brr_dir)


def bind(brr_dir: Path) -> None:
    setup(brr_dir)


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    try:
        _register(brr_dir, state)
    except BrnrdAuthError as e:
        print(f"[brr:cloud] auth failed: {e}")
        return
    except Exception as e:
        print(f"[brr:cloud] register failed: {e}")
    backoff = 1
    while True:
        try:
            _loop_once(brr_dir, inbox_dir, responses_dir)
            backoff = 1
        except BrnrdAuthError as e:
            print(f"[brr:cloud] auth failed: {e}")
            return
        except Exception as e:
            print(f"[brr:cloud] error: {e}, retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)


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
    if cursor > since:
        state["since"] = cursor
        _save_state(brr_dir, state)
    _deliver_responses(brr_dir, inbox_dir, responses_dir, state)
    _publish_activity(brr_dir, inbox_dir, state)


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
                "branch": str(task.meta.get("branch_name") or task.meta.get("publish_branch") or ""),
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
        print(f"[brr:cloud] activity publish failed: {e}")


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
