"""The one-live-menu portal, from resident file to gate tap to wake event."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from brr import daemon, menus, prompts, protocol, run_context
from brr.gates import telegram
from brr.run import Run


def _task(
    thread: str = "telegram:555:",
    *,
    correspondent: str = "",
) -> Run:
    return Run(
        id="run-menu",
        event_id="evt-lead",
        body="compose",
        env="worktree",
        status="running",
        source="telegram",
        conversation_key=thread,
        meta={"correspondent_key": correspondent} if correspondent else {},
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


@pytest.mark.parametrize(
    ("thread", "expected"),
    [
        ("telegram:155783668:", (155783668, None)),
        ("telegram:155783668:42", (155783668, 42)),
        ("cloud:telegram:155783668:", (155783668, None)),
        ("cloud:telegram:155783668:42", (155783668, 42)),
        ("schedule:release-push-dispatch-tick", None),
        ("slack:C123:", None),
        ("cloud:slack:C123:", None),
        ("telegram:notanint:", None),
    ],
)
def test_telegram_thread_target(thread, expected):
    assert telegram._telegram_thread_target(thread) == expected


def test_cloud_wrapped_telegram_live_menu_sends_reply_markup(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    thread = "cloud:telegram:155783668:42"
    outbox = brr_dir / "outbox" / "evt-lead"
    outbox.mkdir(parents=True)
    telegram._save_state(
        brr_dir,
        {"token": "secret", "paired_user_id": 41},
    )
    _write_menu(outbox, _menu("cloud-deploy", thread=thread))

    calls: list[tuple[str, dict]] = []

    def fake_api_call(token, method, params=None, *, poll=False):
        calls.append((method, params or {}))
        if method == "sendMessage":
            return {"result": {"message_id": 700}}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)
    assert daemon._drain_live_menu(
        _emit(brr_dir, thread),
        _task(thread),
        outbox / menus.MENU_NAME,
        {},
        outbox_dir=outbox,
    )

    sent = next(params for method, params in calls if method == "sendMessage")
    assert sent["chat_id"] == 155783668
    assert sent["message_thread_id"] == 42
    assert sent["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "★ Ship it",
        "callback_data": "m:cloud-deploy:ship",
    }


def test_cross_spelling_update_reuses_the_telegram_render_receipt(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    outbox = brr_dir / "outbox" / "evt-lead"
    outbox.mkdir(parents=True)
    telegram._save_state(
        brr_dir,
        {"token": "secret", "paired_user_id": 41},
    )
    calls: list[tuple[str, dict]] = []

    def fake_api_call(token, method, params=None, *, poll=False):
        calls.append((method, params or {}))
        if method == "sendMessage":
            return {"result": {"message_id": 700}}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)
    correspondent = "telegram:user-id:41"
    state: dict[str, object] = {}
    cloud = "cloud:telegram:555:"
    _write_menu(outbox, _menu("cloud-menu", thread=cloud))
    assert daemon._drain_live_menu(
        _emit(brr_dir, cloud),
        _task(cloud, correspondent=correspondent),
        outbox / menus.MENU_NAME,
        state,
        outbox_dir=outbox,
    )

    native = "telegram:555:"
    _write_menu(outbox, _menu("native-menu", thread=native))
    assert daemon._drain_live_menu(
        _emit(brr_dir, native),
        _task(native, correspondent=correspondent),
        outbox / menus.MENU_NAME,
        state,
        outbox_dir=outbox,
    )

    assert [method for method, _params in calls].count("sendMessage") == 1
    edits = [params for method, params in calls if method == "editMessageText"]
    assert edits[-1]["message_id"] == 700
    assert edits[-1]["reply_markup"]["inline_keyboard"][0][0][
        "callback_data"
    ] == "m:native-menu:ship"


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


# ── a live menu option must not outlive its own completion (#957) ──────
#
# Both wake-render callers join the same-pass forge state into the live
# menu so an option naming a PR the wake already knows is resolved renders
# struck instead of standing as a live control for finished work. Every
# test below goes through the caller the defect used — build_daemon_prompt
# / _render_communication_snapshot — not menus.render_numbered directly.


def _iso_ago(seconds: float) -> str:
    return datetime.fromtimestamp(
        time.time() - seconds, tz=timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _menu_pr_options(menu_id: str) -> dict:
    return {
        "menu_id": menu_id,
        "thread": "telegram:555:",
        "options": [
            {"handle": "m995", "label": "merge #995"},
            {"handle": "m998999", "label": "merge #998 + #999"},
            {"handle": "m997", "label": "merge #997"},
            {"handle": "hold", "label": "Hold"},
        ],
    }


def _pr_forge_snapshot() -> dict:
    """#995 and #998/#999 merged hours ago; #997 still open; no worktrees."""
    return {
        "worktrees": [],
        "pr_state": {
            "standalone": [
                {"number": 995, "state": "MERGED", "merged_at": _iso_ago(6 * 3600)},
                {"number": 998, "state": "MERGED", "merged_at": _iso_ago(5 * 3600)},
                {"number": 999, "state": "MERGED", "merged_at": _iso_ago(5 * 3600)},
                {"number": 997, "state": "OPEN"},
            ],
        },
    }


def test_live_menu_strikes_options_naming_already_resolved_prs(tmp_path):
    """An option whose text names a MERGED PR renders struck, with the
    reason and age the same-pass forge block already knows."""
    brr_dir = tmp_path / ".brr"
    menus.promote_menu(brr_dir, _menu_pr_options("release-check"))
    live = menus.load_live_menu(brr_dir, "telegram:555:")

    prompt = prompts.build_daemon_prompt(
        "task",
        "evt-next",
        str(brr_dir / "responses" / "evt-next.md"),
        tmp_path,
        communication_snapshot={
            "current_thread": "telegram:555:",
            "live_menu": live,
            "forge": _pr_forge_snapshot(),
        },
        event_body="next turn",
    )

    assert "1) `m995` — ~~merge #995~~ — #995 merged 6h ago" in prompt
    assert (
        "2) `m998999` — ~~merge #998 + #999~~ — "
        "#998 merged 5h ago; #999 merged 5h ago"
    ) in prompt


def test_a_detail_naming_merged_prs_never_strikes_its_option(tmp_path):
    """Only the label may resolve to an artifact — the detail is prose.

    Caught by driving this repo's own live menu through the join
    (run-260802-2128-913s). The option read ``restart the daemon``, and its
    detail read *"serves: (#1013), the reserved seat (#968) and the vigil
    guard (#961) are merged and dormant until you do"* — three resolved PRs
    given as the **reason to act**. Scanning the detail struck out the one
    option on the page that had not happened: #957 inverted, a control
    reading as complete while it is the only thing outstanding.

    The rule the fix encodes: the label is the act, the detail is prose about
    the act, and prose carries numbers for every reason except naming its own
    target.
    """
    brr_dir = tmp_path / ".brr"
    menus.promote_menu(
        brr_dir,
        {
            "menu_id": "detail-is-prose",
            "thread": "telegram:555:",
            "options": [
                {
                    "handle": "restart",
                    "label": "restart the daemon",
                    "detail": "#995 and #998 are merged and dormant until you do",
                },
            ],
        },
    )
    live = menus.load_live_menu(brr_dir, "telegram:555:")

    prompt = prompts.build_daemon_prompt(
        "task",
        "evt-next",
        str(brr_dir / "responses" / "evt-next.md"),
        tmp_path,
        communication_snapshot={
            "current_thread": "telegram:555:",
            "live_menu": live,
            "forge": _pr_forge_snapshot(),
        },
        event_body="next turn",
    )

    assert "1) `restart` — restart the daemon" in prompt
    assert "~~restart the daemon~~" not in prompt
    assert "merged 6h ago" not in prompt, "no reason tail either — nothing resolved"


def test_live_menu_leaves_an_open_pr_option_unchanged(tmp_path):
    """An option naming an OPEN PR is still live work — unchanged."""
    brr_dir = tmp_path / ".brr"
    menus.promote_menu(brr_dir, _menu_pr_options("release-check-open"))
    live = menus.load_live_menu(brr_dir, "telegram:555:")

    prompt = prompts.build_daemon_prompt(
        "task",
        "evt-next",
        str(brr_dir / "responses" / "evt-next.md"),
        tmp_path,
        communication_snapshot={
            "current_thread": "telegram:555:",
            "live_menu": live,
            "forge": _pr_forge_snapshot(),
        },
        event_body="next turn",
    )

    assert "3) `m997` — merge #997" in prompt
    assert "~~merge #997~~" not in prompt


def test_live_menu_leaves_an_option_with_no_forge_artifact_unchanged(tmp_path):
    """An option naming no PR at all has nothing to join against."""
    brr_dir = tmp_path / ".brr"
    menus.promote_menu(brr_dir, _menu_pr_options("release-check-hold"))
    live = menus.load_live_menu(brr_dir, "telegram:555:")

    prompt = prompts.build_daemon_prompt(
        "task",
        "evt-next",
        str(brr_dir / "responses" / "evt-next.md"),
        tmp_path,
        communication_snapshot={
            "current_thread": "telegram:555:",
            "live_menu": live,
            "forge": _pr_forge_snapshot(),
        },
        event_body="next turn",
    )

    assert "4) `hold` — Hold" in prompt


@pytest.mark.parametrize("forge", [None, {}, {"worktrees": [], "pr_state": {}}])
def test_live_menu_strike_join_is_a_noop_without_resolved_forge_data(
    tmp_path, forge,
):
    """Absent/empty forge data changes nothing — no crash, no strike. A
    polling tax to fix a rendering bug would be a regression, not a fix, so
    an option renders unchanged whenever its completion isn't already in
    the snapshot this wake computed."""
    brr_dir = tmp_path / ".brr"
    menus.promote_menu(brr_dir, _menu_pr_options("release-check-absent"))
    live = menus.load_live_menu(brr_dir, "telegram:555:")

    snapshot = {"current_thread": "telegram:555:", "live_menu": live}
    if forge is not None:
        snapshot["forge"] = forge

    prompt = prompts.build_daemon_prompt(
        "task",
        "evt-next",
        str(brr_dir / "responses" / "evt-next.md"),
        tmp_path,
        communication_snapshot=snapshot,
        event_body="next turn",
    )

    assert "1) `m995` — merge #995" in prompt
    assert "~~" not in prompt


def test_run_context_render_shares_the_same_strike_join(tmp_path):
    """The sibling context-file renderer joins the identical forge state —
    a fact added to one copy only re-creates the defect it was added to
    fix (the established pattern this module's other near-duplicate
    renderers already guard against)."""
    brr_dir = tmp_path / ".brr"
    menus.promote_menu(brr_dir, _menu_pr_options("release-check-ctx"))
    live = menus.load_live_menu(brr_dir, "telegram:555:")

    rendered = run_context._render_communication_snapshot({
        "current_thread": "telegram:555:",
        "live_menu": live,
        "forge": _pr_forge_snapshot(),
    })

    assert "~~merge #995~~ — #995 merged 6h ago" in rendered
    assert "3) `m997` — merge #997" in rendered
    assert "~~merge #997~~" not in rendered


@pytest.mark.parametrize(
    ("written_thread", "read_thread"),
    [
        ("cloud:telegram:555:", "telegram:555:"),
        ("telegram:555:", "cloud:telegram:555:"),
    ],
)
def test_correspondent_menu_renders_across_thread_spellings(
    tmp_path,
    written_thread,
    read_thread,
):
    brr_dir = tmp_path / ".brr"
    correspondent = "telegram:user-id:41"
    stored, _ = menus.promote_menu(
        brr_dir,
        _menu("shared-menu", thread=written_thread),
        correspondent_key=correspondent,
    )

    assert menus.menu_store_key(read_thread, correspondent) == (
        "correspondent:telegram:user-id:41"
    )
    assert menus.load_live_menu(
        brr_dir,
        read_thread,
        correspondent_key=correspondent,
    ) == stored


def test_legacy_thread_menus_migrate_newest_and_retire_stale(tmp_path):
    brr_dir = tmp_path / ".brr"
    native = "telegram:555:"
    cloud = "cloud:telegram:555:"
    correspondent = "telegram:user-id:41"
    menus.promote_menu(
        brr_dir,
        _menu("native-old", thread=native),
        now=1_700_000_000,
    )
    menus.promote_menu(
        brr_dir,
        _menu("cloud-new", thread=cloud),
        now=1_700_000_100,
    )

    live = menus.load_live_menu(
        brr_dir,
        native,
        correspondent_key=correspondent,
        legacy_threads=[native, cloud],
    )

    assert live is not None
    assert live["menu_id"] == "cloud-new"
    assert live["thread"] == cloud
    assert not menus._live_path(brr_dir, native).exists()
    assert not menus._live_path(brr_dir, cloud).exists()
    stale = menus.load_generation(
        brr_dir,
        cloud,
        "native-old",
        correspondent_key=correspondent,
    )
    assert stale is not None
    assert stale["state"] == "superseded"
    assert stale["superseded_by"] == "cloud-new"


def test_distinct_correspondents_never_share_a_menu(tmp_path):
    brr_dir = tmp_path / ".brr"
    thread = "telegram:-100123:"
    ada = "telegram:user-id:41"
    grace = "telegram:user-id:42"
    menus.promote_menu(
        brr_dir,
        _menu("ada-menu", thread=thread),
        correspondent_key=ada,
    )
    menus.promote_menu(
        brr_dir,
        _menu("grace-menu", thread=thread),
        correspondent_key=grace,
    )

    ada_live = menus.load_live_menu(
        brr_dir, thread, correspondent_key=ada,
    )
    grace_live = menus.load_live_menu(
        brr_dir, thread, correspondent_key=grace,
    )
    assert ada_live is not None and ada_live["menu_id"] == "ada-menu"
    assert grace_live is not None and grace_live["menu_id"] == "grace-menu"


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
