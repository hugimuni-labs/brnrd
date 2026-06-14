"""Cloud gate — drains a brnrd project inbox into the local ``.brr/``.

This is the daemon side of the inbox-as-service protocol
(``kb/design-brnrd-protocol.md``). It long-polls brnrd for events
scoped to the connected project, writes them as ordinary
``.brr/inbox/`` events (so the runner handles them exactly like
Telegram / Slack messages), and posts runner responses back to
brnrd, which forwards them to the originating platform.

Credentials + the ``since`` cursor live in ``.brr/gates/cloud.json``
via the shared gate runtime. ``brr brnrd connect`` populates them
through the device-flow pairing handshake.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import requests

from .. import protocol, run_progress
from ..task import Task
from . import delivery, runtime

# Server long-polls up to ~25s; the client timeout must comfortably
# exceed that so a quiet inbox isn't read as a transport error.
_POLL_WAIT_S = 25
_HTTP_TIMEOUT_S = 60
_DEFAULT_DAEMON_NAME = "daemon"

# Per-origin-platform single-message size budget for the final answer.
# The daemon offloads anything larger to a gist before POSTing so the
# body fits without relying on brnrd-side chunking (which stays only as a
# safety net). Telegram's hard cap is 4096; stay under it with margin,
# matching the OSS telegram gate. Unlisted platforms skip daemon overflow.
_RESPONSE_LIMITS = {"telegram": 3900}


# ── HTTP seam ────────────────────────────────────────────────────────


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    json: dict | None = None,
    params: dict | None = None,
    timeout: float = _HTTP_TIMEOUT_S,
) -> dict:
    """Single chokepoint for brnrd HTTP. Tests monkeypatch this."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = requests.request(
        method,
        base_url.rstrip("/") + path,
        json=json,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    if not 200 <= resp.status_code < 300:
        raise RuntimeError(
            f"brnrd {method} {path} -> {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json() if resp.content else {}


# ── State ────────────────────────────────────────────────────────────


def _load_state(brr_dir: Path) -> dict:
    return runtime.load_state(brr_dir, "cloud")


def _save_state(brr_dir: Path, state: dict) -> None:
    runtime.save_state(brr_dir, "cloud", state)


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return bool(state.get("token") and state.get("brnrd_url") and state.get("project_id"))


def relay_pack(brr_dir: Path, pack: dict, *, ttl_s: int | None = None) -> str | None:
    """Relay a diffense review pack to brnrd for a transient rendered view.

    brnrd holds the pack in memory behind an unguessable, TTL-bounded
    token and renders it on demand — it never persists it
    (``kb/design-diffense.md`` → "Where packs live"). Returns the render
    URL to link from the PR body, or ``None`` when managed mode isn't
    configured or the relay fails: a missing rich link is never worth
    blocking the PR over (the body still carries the projection + the
    embedded pack).
    """
    state = _load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return None
    body: dict = {"pack": pack}
    if ttl_s:
        body["ttl_s"] = ttl_s
    try:
        result = _request(
            state["brnrd_url"], "POST", "/v1/daemons/pack",
            token=state["token"], json=body,
        )
    except Exception as e:  # noqa: BLE001 - best-effort rich surface
        print(f"[brr:cloud] pack relay failed: {e}")
        return None
    url = result.get("render_url")
    return url if isinstance(url, str) and url else None


# ── Device-flow connect ──────────────────────────────────────────────


def connect(
    brr_dir: Path,
    *,
    brnrd_url: str,
    daemon_name: str = _DEFAULT_DAEMON_NAME,
    poll_interval_s: float = 2.0,
    timeout_s: float = 600.0,
    out: Callable[[str], None] = print,
) -> dict:
    """Run the device-flow pairing and persist the daemon token.

    Starts a pair, prints the approval URL + code, then polls until
    the account approves it against a project (via the dashboard or
    the approve endpoint) and brnrd hands back a project-scoped
    daemon token.
    """
    pair = _request(brnrd_url, "POST", "/v1/accounts/pair")
    out(f"[brr] Approve this daemon at: {pair['pair_url']}")
    out(f"[brr] Pair code: {pair['pair_code']}")

    deadline = time.monotonic() + timeout_s
    while True:
        status = _request(
            brnrd_url,
            "GET",
            f"/v1/accounts/pair/{pair['pair_code']}",
            params={"poll_secret": pair["poll_secret"]},
        )
        if status.get("status") == "paired" and status.get("daemon_token"):
            break
        if time.monotonic() > deadline:
            raise TimeoutError("pairing timed out — re-run `brr brnrd connect`")
        time.sleep(poll_interval_s)

    state = _load_state(brr_dir)
    state.update(
        {
            "brnrd_url": brnrd_url.rstrip("/"),
            "token": status["daemon_token"],
            "project_id": status["project_id"],
            "daemon_name": daemon_name,
            "since": state.get("since", 0),
        }
    )
    _save_state(brr_dir, state)
    out(f"[brr] Connected to brnrd project {status['project_id']}.")
    return state


def setup(brr_dir: Path) -> None:
    """``brr setup cloud`` points at the device-flow connect verb."""
    print("[brr] Run `brr brnrd connect` to link this daemon to a brnrd project.")


def auth(brr_dir: Path) -> None:
    setup(brr_dir)


def bind(brr_dir: Path) -> None:
    setup(brr_dir)


# ── Gate loop ────────────────────────────────────────────────────────


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    """Register once, then long-poll + deliver in a backoff loop."""
    state = _load_state(brr_dir)
    try:
        _register(state)
    except Exception as e:  # noqa: BLE001 - registration is best-effort
        print(f"[brr:cloud] register failed: {e}")
    runtime.run_loop(
        lambda: _loop_once(brr_dir, inbox_dir, responses_dir),
        label="cloud",
    )


def _register(state: dict) -> None:
    _request(
        state["brnrd_url"],
        "POST",
        "/v1/daemons/register",
        token=state["token"],
        json={"daemon_name": state.get("daemon_name", _DEFAULT_DAEMON_NAME), "capabilities": {}},
    )


def _origin_meta(reply_to: dict) -> dict:
    """Return local inbox frontmatter for a brnrd origin routing blob."""
    platform = reply_to.get("platform") or ""
    meta: dict[str, object] = {
        "cloud_platform": platform,
        "cloud_chat_id": "",
        "cloud_topic_id": "",
    }
    if platform == "telegram":
        chat_id = reply_to.get("chat_id")
        topic_id = reply_to.get("topic_id")
        meta["cloud_chat_id"] = "" if chat_id is None else chat_id
        meta["cloud_topic_id"] = "" if topic_id is None else topic_id
        copies = {
            "message_id": "cloud_message_id",
            "user": "cloud_user",
            "user_id": "cloud_user_id",
            "username": "cloud_username",
        }
        for src, dst in copies.items():
            value = reply_to.get(src)
            if value not in (None, ""):
                meta[dst] = value
        return meta

    if platform == "github":
        repo = str(reply_to.get("repo") or "")
        issue_number = reply_to.get("issue_number")
        meta["cloud_chat_id"] = (
            f"{repo}#{issue_number}" if repo and issue_number not in (None, "") else ""
        )
        copies = {
            "repo": "github_repo",
            "kind": "github_kind",
            "issue_number": "github_issue_number",
            "comment_id": "github_comment_id",
            "author": "github_author",
            "html_url": "github_html_url",
            "trigger": "github_trigger",
            "mention": "github_mention",
            "pr_number": "github_pr_number",
            "branch_target": "branch_target",
        }
        for src, dst in copies.items():
            value = reply_to.get(src)
            if value not in (None, ""):
                meta[dst] = value
    return meta


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    since = state.get("since", 0)

    result = _request(
        state["brnrd_url"],
        "GET",
        "/v1/daemons/inbox",
        token=state["token"],
        params={"since": since, "wait": _POLL_WAIT_S},
    )
    events = result.get("events", [])
    for ev in events:
        # Carry the origin platform's routing as discrete fields: the
        # final response only needs cloud_event_id (brnrd derives the
        # target from its own event row), but the live card/conversation
        # layer needs the origin platform and thread fingerprint. brnrd
        # never receives these back — they stay local.
        rt = ev.get("reply_to") or {}
        origin_meta = _origin_meta(rt)
        protocol.create_event(
            inbox_dir,
            source="cloud",
            body=ev.get("body") or "",
            cloud_event_id=ev["event_id"],
            **origin_meta,
        )
    cursor = result.get("cursor", since)
    if cursor > since:
        state["since"] = cursor
        _save_state(brr_dir, state)

    _deliver_responses(brr_dir, inbox_dir, responses_dir, state)


def _deliver_responses(
    brr_dir: Path, inbox_dir: Path, responses_dir: Path, state: dict
) -> None:
    def deliver(event: dict, body: str) -> None:
        cloud_event_id = event.get("cloud_event_id")
        if not cloud_event_id:
            raise RuntimeError("missing cloud_event_id")
        # Offload an over-long body to a gist on the user's own gh and
        # send the link, so the body fits the origin platform's single-
        # message limit. brnrd never needs gist creds; its own chunking
        # is just a safety net now (shape H — kb/design-managed-delivery.md).
        limit = _RESPONSE_LIMITS.get(event.get("cloud_platform") or "")
        if limit is not None:
            body = delivery.resolve_overflow(
                body, limit=limit, gist_fn=delivery.post_gist
            )
        _request(
            state["brnrd_url"],
            "POST",
            "/v1/daemons/responses",
            token=state["token"],
            json={"event_id": cloud_event_id, "body_markdown": body, "status": "done"},
        )

    runtime.deliver_responses(inbox_dir, responses_dir, "cloud", deliver)


# ── Live progress card (relayed to brnrd) ───────────────────────────


class _CloudCardTransport:
    """brnrd-relay transport for the shared card driver.

    ``send`` / ``edit`` become POSTs to brnrd's ``/v1/daemons/card``
    relay, executed there with the managed token. The daemon owns the
    platform message id (returned by ``send``, replayed on ``edit``);
    brnrd stores none of it. A 409 from brnrd (card vanished) surfaces as
    a raised error that the card driver turns into a fresh send.
    """

    def __init__(self, state: dict, event_id: str) -> None:
        self._state = state
        self._event_id = event_id

    def _post(self, body: dict) -> dict:
        return _request(
            self._state["brnrd_url"], "POST", "/v1/daemons/card",
            token=self._state["token"], json=body,
        )

    def send(self, text: str, *, reply_to: int | None = None) -> int | None:
        result = self._post({"event_id": self._event_id, "text": text})
        return result.get("message_id")

    def edit(self, message_id: int, text: str) -> None:
        self._post(
            {"event_id": self._event_id, "text": text, "message_id": message_id}
        )


def _card_text_for(
    brr_dir: Path, conv_key: str, task_id: str, platform: str
) -> str | None:
    """Render the progress card using the origin platform's presentation.

    Reuses the OSS gate's renderer so a managed card is identical to the
    self-hosted one. Only telegram-origin is wired today; an unknown
    origin yields no card and the relay simply stays quiet.
    """
    if platform == "telegram":
        from . import telegram

        return telegram.card_text(brr_dir, conv_key, task_id)
    return None


def render_update(brr_dir: Path, packet: Any) -> None:
    """Relay a live progress card for a cloud-sourced task to brnrd.

    Mirrors the OSS gates: render the card daemon-side from
    ``run_progress`` and drive the shared ``delivery.update_card``
    lifecycle — but over a transport that POSTs to brnrd's card relay
    instead of hitting a platform directly. Presentation is picked by the
    event's origin platform, so a telegram-origin card looks the same
    whether it came through the local telegram gate or the cloud gate.
    Failures are swallowed; the daemon must keep running.
    """
    if getattr(packet, "type", None) not in run_progress.CARD_PACKETS:
        return
    state = _load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return

    conv_key = getattr(packet, "conversation_key", "") or ""
    task_id = run_progress.task_id_from_packet(packet)
    if not conv_key or not task_id:
        return

    task = Task.from_file(brr_dir / "tasks" / f"{task_id}.md")
    if task is None or task.source != "cloud":
        return
    cloud_event_id = task.meta.get("cloud_event_id")
    if not cloud_event_id:
        return

    text = _card_text_for(
        brr_dir, conv_key, task_id, str(task.meta.get("cloud_platform") or "")
    )
    if text is None:
        return

    transport = _CloudCardTransport(state, str(cloud_event_id))
    delivery.update_card(
        brr_dir, "cloud", task_id, text,
        transport=transport, render_tag=getattr(packet, "type", None),
    )
