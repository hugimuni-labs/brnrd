"""The one-live-menu portal, from resident file to gate tap to wake event."""

from __future__ import annotations

import json

from brr import daemon, menus, prompts, protocol
from brr.gates import telegram
from brr.run import Run


def _task(thread: str = "telegram:555:") -> Run:
    return Run(
        id="run-menu",
        event_id="evt-lead",
        body="compose",
        env="worktree",
        status="running",
        source="telegram",
        conversation_key=thread,
        meta={},
    )


def _menu(menu_id: str, *, thread: str = "telegram:555:") -> dict:
    return {
        "menu_id": menu_id,
        "thread": thread,
        "options": [
            {
                "handle": "ship",
                "label": "Ship it",
                "detail": "Open the pull request.",
                "rec": True,
            },
            {"handle": "hold", "label": "Hold"},
        ],
    }


def _write_menu(outbox, payload) -> None:
    protocol._atomic_write(
        outbox / menus.MENU_NAME,
        json.dumps(payload, indent=2) + "\n",
    )


def _emit(brr_dir, thread="telegram:555:"):
    return daemon._WorkerEmit(
        brr_dir=brr_dir,
        conversation_key=thread,
        event_id="evt-lead",
    )


def test_menu_written_rendered_tapped_arrives_as_pending_event(
    tmp_path, monkeypatch,
):
    """Real path: outbox control → daemon promotion → Telegram → inbox."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-lead"
    outbox.mkdir(parents=True)
    telegram._save_state(
        brr_dir,
        {"token": "secret", "paired_user_id": 41},
    )
    _write_menu(outbox, _menu("deploy-1"))

    calls: list[tuple[str, dict]] = []
    callback_data: dict[str, str] = {}
    updates = {"value": []}

    def fake_api_call(token, method, params=None, *, poll=False):
        calls.append((method, params or {}))
        if method == "sendMessage":
            callback_data["value"] = params["reply_markup"]["inline_keyboard"][0][0][
                "callback_data"
            ]
            return {"result": {"message_id": 700}}
        if method == "getUpdates":
            return {"result": updates["value"]}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)
    state: dict[str, object] = {}
    # The generic chat outbox drain must treat menu.json as control state,
    # never as a Markdown reply whose JSON body leaks into the thread.
    assert daemon._outbox_message_files(outbox) == []
    assert daemon._drain_live_menu(
        _emit(brr_dir),
        _task(),
        outbox / menus.MENU_NAME,
        state,
        outbox_dir=outbox,
    )

    assert callback_data["value"] == "m:deploy-1:ship"
    sent = next(params for method, params in calls if method == "sendMessage")
    assert sent["chat_id"] == 555
    assert "1) ship — Ship it — recommended" in sent["text"]

    updates["value"] = [
        {
            "update_id": 10,
            "callback_query": {
                "id": "callback-1",
                "from": {
                    "id": 41,
                    "first_name": "Ada",
                    "username": "ada",
                },
                "data": callback_data["value"],
                "message": {
                    "message_id": 700,
                    "chat": {"id": 555},
                },
            },
        }
    ]
    telegram._loop_once(brr_dir, inbox, brr_dir / "responses")

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "menu_answer"
    assert event["menu_id"] == "deploy-1"
    assert event["option"] == "ship"
    assert event["menu_status"] == "live"
    assert event["conversation_key"] == "telegram:555:"
    assert json.loads(event["body"]) == {
        "label": "Ship it",
        "detail": "Open the pull request.",
        "menu_id": "deploy-1",
        "option": "ship",
        "option_known": True,
        "rec": True,
        "status": "live",
    }


def test_superseded_menu_tap_arrives_as_honest_stale_answer(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-lead"
    outbox.mkdir(parents=True)
    telegram._save_state(
        brr_dir,
        {"token": "secret", "paired_user_id": 41},
    )
    calls: list[tuple[str, dict]] = []
    updates = {"value": []}

    def fake_api_call(token, method, params=None, *, poll=False):
        calls.append((method, params or {}))
        if method == "sendMessage":
            return {"result": {"message_id": 701}}
        if method == "getUpdates":
            return {"result": updates["value"]}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)
    state: dict[str, object] = {}
    _write_menu(outbox, _menu("deploy-1"))
    assert daemon._drain_live_menu(
        _emit(brr_dir), _task(), outbox / menus.MENU_NAME, state,
        outbox_dir=outbox,
    )
    _write_menu(outbox, _menu("deploy-2"))
    assert daemon._drain_live_menu(
        _emit(brr_dir), _task(), outbox / menus.MENU_NAME, state,
        outbox_dir=outbox,
    )

    # Supersession edits the one existing Telegram menu instead of stacking
    # another inline keyboard in the thread.
    assert [method for method, _ in calls].count("sendMessage") == 1
    edits = [params for method, params in calls if method == "editMessageText"]
    assert edits[-1]["message_id"] == 701
    assert edits[-1]["reply_markup"]["inline_keyboard"][0][0][
        "callback_data"
    ] == "m:deploy-2:ship"

    updates["value"] = [
        {
            "update_id": 11,
            "callback_query": {
                "id": "callback-stale",
                "from": {"id": 41, "first_name": "Ada"},
                "data": "m:deploy-1:ship",
                "message": {"message_id": 701, "chat": {"id": 555}},
            },
        }
    ]
    telegram._loop_once(brr_dir, inbox, brr_dir / "responses")

    event = protocol.list_pending(inbox)[0]
    answer = json.loads(event["body"])
    assert event["menu_status"] == "stale"
    assert answer["status"] == "stale"
    assert answer["option"] == "ship"
    assert answer["superseded_by"] == "deploy-2"


def test_malformed_menu_records_notice_without_crashing_or_rendering(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    outbox = brr_dir / "outbox" / "evt-lead"
    outbox.mkdir(parents=True)
    protocol._atomic_write(outbox / menus.MENU_NAME, "{not json\n")
    rendered = []
    monkeypatch.setattr(
        daemon.updates,
        "emit",
        lambda _brr, packet: rendered.append(packet),
    )
    state: dict[str, object] = {}

    assert not daemon._drain_live_menu(
        _emit(brr_dir), _task(), outbox / menus.MENU_NAME, state,
        outbox_dir=outbox,
    )
    # Same malformed bytes on the next heartbeat do not spam another notice.
    assert not daemon._drain_live_menu(
        _emit(brr_dir), _task(), outbox / menus.MENU_NAME, state,
        outbox_dir=outbox,
    )

    notices = daemon._read_outbox_notices(outbox)
    assert len(notices) == 1
    assert "malformed menu.json ignored" in notices[0]["text"]
    assert rendered == []


def test_next_boot_renders_the_same_validated_live_generation(tmp_path):
    brr_dir = tmp_path / ".brr"
    stored, _ = menus.promote_menu(brr_dir, _menu("deploy-1"))
    live = menus.load_live_menu(brr_dir, "telegram:555:")
    assert live == stored

    prompt = prompts.build_daemon_prompt(
        "task",
        "evt-next",
        str(brr_dir / "responses" / "evt-next.md"),
        tmp_path,
        communication_snapshot={
            "current_thread": "telegram:555:",
            "live_menu": live,
        },
        event_body="next turn",
    )

    assert "Live menu — the same validated generation rendered at the gate" in prompt
    assert "1) `ship` — Ship it — recommended" in prompt
    assert "2) `hold` — Hold" in prompt


def test_expired_and_unknown_menu_answers_remain_pending_events(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    menu = _menu("deploy-old")
    menu["expires_at"] = "2026-01-01T00:00:00Z"
    menus.promote_menu(brr_dir, menu, now=1_700_000_000)

    expired = menus.create_answer_event(
        brr_dir,
        inbox,
        source="telegram",
        thread="telegram:555:",
        menu_id="deploy-old",
        option="hold",
        now=1_800_000_000,
    )
    unknown = menus.create_answer_event(
        brr_dir,
        inbox,
        source="telegram",
        thread="telegram:555:",
        menu_id="pruned-generation",
        option="anything",
        text="Use the safer path and explain why.",
    )

    assert protocol._read_event(expired)["menu_status"] == "expired"
    unknown_event = protocol._read_event(unknown)
    assert unknown_event["menu_status"] == "unknown"
    assert json.loads(unknown_event["body"])["text"] == (
        "Use the safer path and explain why."
    )
    assert len(protocol.list_pending(inbox)) == 2
