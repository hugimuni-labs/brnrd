import pytest

from brr import protocol
from brr.gates import telegram


def test_token_only_state_is_configured(tmp_path):
    brr_dir = tmp_path / ".brr"
    telegram._save_state(brr_dir, {"token": "secret"})

    assert telegram.is_configured(brr_dir)


def test_loop_accepts_any_chat_and_records_message_chat(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    telegram._save_state(brr_dir, {"token": "secret"})

    def fake_api_call(token, method, params=None, *, poll=False):
        assert token == "secret"
        assert method == "getUpdates"
        assert poll is True
        return {
            "result": [
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 501,
                        "chat": {"id": 111},
                        "from": {"id": 41, "first_name": "Ada", "username": "ada_l"},
                        "text": "first task",
                    },
                },
                {
                    "update_id": 2,
                    "message": {
                        "message_id": 502,
                        "chat": {"id": 222},
                        "message_thread_id": 7,
                        "from": {
                            "id": 42,
                            "first_name": "Grace",
                            "username": "grace_h",
                        },
                        "text": "second task",
                    },
                },
            ],
        }

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)

    telegram._loop_once(brr_dir, inbox_dir, responses_dir)

    events = sorted(protocol.list_pending(inbox_dir), key=lambda event: event["body"])
    assert [event["body"] for event in events] == ["first task", "second task"]
    assert [event["telegram_chat_id"] for event in events] == [111, 222]
    assert events[0]["telegram_topic_id"] == ""
    assert events[1]["telegram_topic_id"] == 7
    # Source message id is captured so deliveries can reply-to it.
    assert [event["telegram_message_id"] for event in events] == [501, 502]
    assert [event["telegram_user_id"] for event in events] == [41, 42]
    assert [event["telegram_username"] for event in events] == ["ada_l", "grace_h"]


def test_replies_are_sent_to_originating_chat(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    telegram._save_state(brr_dir, {"token": "secret"})
    first = protocol.create_event(
        inbox_dir,
        source="telegram",
        body="first task",
        telegram_chat_id=111,
        telegram_topic_id="",
        telegram_message_id=501,
    )
    second = protocol.create_event(
        inbox_dir,
        source="telegram",
        body="second task",
        telegram_chat_id=222,
        telegram_topic_id=7,
        telegram_message_id=502,
    )
    first_event, second_event = sorted(
        protocol.list_pending(inbox_dir),
        key=lambda event: event["body"],
    )
    protocol.set_status(first_event, "done")
    protocol.set_status(second_event, "done")
    protocol.write_response(responses_dir, first_event["id"], "first answer")
    protocol.write_response(responses_dir, second_event["id"], "second answer")
    sent = []

    def fake_send_with_overflow(
        token, chat_id, topic_id, text, *, reply_to_message_id=None,
    ):
        sent.append((token, chat_id, topic_id, text, reply_to_message_id))

    monkeypatch.setattr(telegram, "_send_with_overflow", fake_send_with_overflow)

    telegram._deliver_responses(brr_dir, inbox_dir, responses_dir, "secret")

    # Final responses thread under the originating chat message: the
    # source ``message_id`` rides through to ``reply_to_message_id`` so
    # the bot's answer renders as a visible reply in the chat client.
    assert sent == [
        ("secret", 111, None, "first answer", 501),
        ("secret", 222, 7, "second answer", 502),
    ]
    assert not first.exists()
    assert not second.exists()


def test_replies_skip_reply_to_when_event_has_no_message_id(tmp_path, monkeypatch):
    # Legacy events created before the message-id capture landed do not
    # carry ``telegram_message_id``; delivery must still work without
    # threading rather than dropping the response.
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    telegram._save_state(brr_dir, {"token": "secret"})
    protocol.create_event(
        inbox_dir,
        source="telegram",
        body="legacy",
        telegram_chat_id=111,
        telegram_topic_id="",
    )
    event = protocol.list_pending(inbox_dir)[0]
    protocol.set_status(event, "done")
    protocol.write_response(responses_dir, event["id"], "ok")
    sent = []

    def fake_send_with_overflow(
        token, chat_id, topic_id, text, *, reply_to_message_id=None,
    ):
        sent.append((token, chat_id, topic_id, text, reply_to_message_id))

    monkeypatch.setattr(telegram, "_send_with_overflow", fake_send_with_overflow)
    telegram._deliver_responses(brr_dir, inbox_dir, responses_dir, "secret")

    assert sent == [("secret", 111, None, "ok", None)]


def test_send_message_passes_reply_to_message_id(monkeypatch):
    # ``_send_message`` is the single chokepoint through which both the
    # progress card and the final response reach Telegram, so pin the
    # exact API parameters it emits when threading is requested.
    captured: dict = {}

    def fake_api_call(token, method, params=None):
        captured["token"] = token
        captured["method"] = method
        captured["params"] = params
        return {"ok": True, "result": {"message_id": 9}}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)
    telegram._send_message(
        "secret", 111, "hi", topic_id=None,
        parse_mode="HTML", reply_to_message_id=42,
    )

    assert captured["method"] == "sendMessage"
    params = captured["params"]
    assert params["chat_id"] == 111
    assert params["text"] == "hi"
    assert params["parse_mode"] == "HTML"
    assert params["reply_to_message_id"] == 42
    # ``allow_sending_without_reply`` keeps delivery resilient when the
    # source message was deleted before the runner finished.
    assert params["allow_sending_without_reply"] is True


def test_send_message_omits_reply_to_when_unset(monkeypatch):
    captured: dict = {}

    def fake_api_call(token, method, params=None):
        captured["params"] = params
        return {"ok": True, "result": {"message_id": 9}}

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)
    telegram._send_message("secret", 111, "hi", topic_id=None)

    assert "reply_to_message_id" not in captured["params"]
    assert "allow_sending_without_reply" not in captured["params"]


def test_api_call_uses_requests_json_and_typed_not_modified(monkeypatch):
    class FakeResponse:
        status_code = 400
        text = '{"description":"Bad Request: message is not modified"}'
        reason = "Bad Request"

        def json(self):
            return {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message is not modified",
            }

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(telegram._SESSION, "post", fake_post)

    with pytest.raises(telegram._TelegramNotModified):
        telegram._api_call("secret", "editMessageText", {"chat_id": 1})

    assert calls == [
        (
            "https://api.telegram.org/botsecret/editMessageText",
            {"json": {"chat_id": 1}, "timeout": 90},
        )
    ]


def test_run_loop_starts_dedicated_delivery_loop(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox_dir = brr_dir / "inbox"
    responses_dir = brr_dir / "responses"
    calls: list[tuple[str, float]] = []

    class FakeThread:
        def __init__(self, *, target, args=(), kwargs=None, **_ignored):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    def fake_run_loop(_loop_once, *, label, poll_interval=0.0, backoff_max=120):
        calls.append((label, poll_interval))

    monkeypatch.setattr(telegram.threading, "Thread", FakeThread)
    monkeypatch.setattr(telegram.runtime, "run_loop", fake_run_loop)

    telegram.run_loop(brr_dir, inbox_dir, responses_dir)

    assert calls == [
        ("telegram-delivery", telegram._DELIVERY_INTERVAL),
        ("telegram", 0.0),
    ]


def test_api_call_redacts_token_from_request_errors(monkeypatch):
    def fake_post(url, **kwargs):
        raise telegram.requests.RequestException(f"failed for {url}")

    monkeypatch.setattr(telegram._SESSION, "post", fake_post)

    with pytest.raises(RuntimeError) as caught:
        telegram._api_call("secret-token", "getMe")

    message = str(caught.value)
    assert "secret-token" not in message
    assert "<token>" in message
