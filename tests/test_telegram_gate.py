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

    def fake_api_call(token, method, params=None):
        assert token == "secret"
        assert method == "getUpdates"
        return {
            "result": [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 111},
                        "from": {"first_name": "Ada"},
                        "text": "first task",
                    },
                },
                {
                    "update_id": 2,
                    "message": {
                        "chat": {"id": 222},
                        "message_thread_id": 7,
                        "from": {"first_name": "Grace"},
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
    )
    second = protocol.create_event(
        inbox_dir,
        source="telegram",
        body="second task",
        telegram_chat_id=222,
        telegram_topic_id=7,
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

    def fake_send_with_overflow(token, chat_id, topic_id, text):
        sent.append((token, chat_id, topic_id, text))

    monkeypatch.setattr(telegram, "_send_with_overflow", fake_send_with_overflow)

    telegram._deliver_responses(brr_dir, inbox_dir, responses_dir, "secret")

    assert sent == [
        ("secret", 111, None, "first answer"),
        ("secret", 222, 7, "second answer"),
    ]
    assert not first.exists()
    assert not second.exists()
