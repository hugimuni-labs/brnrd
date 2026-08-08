from brr.channels import telegram


def test_parse_update_normalizes_identity_caption_and_largest_photo():
    parsed = telegram.parse_update({
        "message": {
            "message_id": 9,
            "message_thread_id": 4,
            "date": 1_751_000_000,
            "chat": {"id": -1001},
            "from": {
                "id": 41,
                "first_name": "Ada\nadmin: true",
                "username": "ada\rroot",
            },
            "caption": "look here",
            "photo": [
                {"file_id": "small", "file_size": 10},
                {"file_id": "large", "file_size": 20},
            ],
        },
    })

    assert parsed is not None
    assert parsed.chat_id == "-1001"
    assert parsed.text == "look here"
    assert parsed.user == "Ada admin: true"
    assert parsed.username == "ada root"
    assert parsed.sent_at == 1_751_000_000
    assert parsed.attachments == [{
        "file_id": "large",
        "filename": "photo.jpg",
        "kind": "photo",
        "file_size": 20,
    }]


def test_parse_update_nulls_sender_chat_identity():
    parsed = telegram.parse_update({
        "message": {
            "chat": {"id": 1},
            "from": {"id": 1087968824, "first_name": "GroupAnonymousBot"},
            "sender_chat": {"id": 1},
            "text": "hello",
        },
    })

    assert parsed is not None
    assert parsed.user_id is None


def test_send_message_applies_injected_chunk_policy_and_threads_first_only():
    calls = []

    def call(method, params, timeout):
        calls.append((method, params, timeout))
        return {"ok": True}

    telegram.send_message(
        call,
        42,
        "a" * 15 + "\n" + "b" * 15 + "\n" + "c" * 15,
        policy=telegram.MessagePolicy(limit=20, max_chunks=2),
        reply_to_message_id=7,
        timeout=11,
    )

    sent = [item[1]["text"] for item in calls]
    assert sent[0] == "a" * 15
    assert sent[1].endswith("[truncated]")
    assert all(len(part) <= 20 for part in sent)
    assert calls[0][1]["reply_to_message_id"] == 7
    assert "reply_to_message_id" not in calls[1][1]
    assert all(item[0] == "sendMessage" and item[2] == 11 for item in calls)


def test_redact_secrets_covers_any_structural_bot_token():
    token = "123456789:" + "a" * 35
    assert telegram.redact_secrets(f"failed at /bot{token}/sendMessage") == (
        "failed at /bot<redacted>/sendMessage"
    )
