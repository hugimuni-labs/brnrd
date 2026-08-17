from brr.channels import telegram
from brr.channels.telegram import split_message, utf16_len


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


# ── split_message: #the-wire-that-cuts-at-4096 ─────────────────────────
#
# A resident's long reply through the hosted forward path (or the local
# gate's own card send) truncated silently around 4,100 chars, mid-word.
# These pin the shape a fix must hold: word/line-boundary cuts, fence
# safety across a split, the exact-limit edge, and UTF-16 accounting
# (Telegram's own unit, not Python's `len()`).


def test_split_message_over_limit_never_cuts_mid_word():
    # One long run with no newlines anywhere — the case that used to fall
    # straight through to an index cut (`remaining[:limit]`).
    words = (
        "supercalifragilisticexpialidocious workspace deployment pipeline "
        "integration testing framework orchestration "
    ) * 60
    text = words.strip()
    assert utf16_len(text) > 4096

    parts = split_message(text, limit=4096, max_chunks=12)

    assert len(parts) > 1
    assert all(utf16_len(p) <= 4096 for p in parts)
    # Every chunk boundary lands on a word boundary: reassembling with a
    # single space between chunks reproduces the source, modulo the
    # whitespace the split itself consumed at the seam.
    rejoined = "".join(parts)
    assert rejoined.replace(" ", "") == text.replace(" ", "")
    # The reported symptom, directly: "workspace" never appears split as
    # "workspa" at the end of one chunk and "ce" at the start of the next.
    for cut_index in range(len(parts) - 1):
        assert not parts[cut_index].endswith("workspa")


def test_split_message_exact_limit_stays_one_chunk():
    text = "a" * 4096
    parts = split_message(text, limit=4096, max_chunks=12)
    assert parts == [text]


def test_split_message_one_over_limit_splits_in_two():
    text = "a" * 4097
    parts = split_message(text, limit=4096, max_chunks=12)
    assert len(parts) == 2
    assert all(utf16_len(p) <= 4096 for p in parts)
    assert "".join(parts) == text


def test_split_message_preserves_and_reopens_a_spanning_code_fence():
    body = (
        "intro\n```python\n"
        + "\n".join(f"line {i}" for i in range(80))
        + "\n```\noutro"
    )
    parts = split_message(body, limit=120, max_chunks=30)

    assert len(parts) > 1
    assert all(utf16_len(p) <= 120 for p in parts)

    def fence_count(chunk: str) -> int:
        return sum(1 for line in chunk.split("\n") if line.lstrip().startswith("```"))

    # Every delivered chunk is independently balanced — no chunk leaves an
    # open fence bleeding its formatting into the rest of the chat.
    assert all(fence_count(p) % 2 == 0 for p in parts)
    assert parts[0].startswith("intro\n```python\n")
    assert parts[-1].rstrip().endswith("outro")

    # No code line lost, duplicated, or reordered by the close/reopen.
    kept = [
        stripped
        for p in parts
        for stripped in p.split("\n")
        if stripped.startswith("line ")
    ]
    assert kept == [f"line {i}" for i in range(80)]


def test_split_message_utf16_accounts_for_astral_emoji():
    # U+1F600 is one Python character but two UTF-16 code units — the unit
    # Telegram's own limit actually counts in.
    emoji = "\U0001F600"
    text = emoji * 3000  # 3000 chars, 6000 UTF-16 units: over a 4096 limit
    assert len(text) < 4096  # a len()-only budget would call this one chunk
    assert utf16_len(text) > 4096

    parts = split_message(text, limit=4096, max_chunks=12)

    assert len(parts) > 1
    assert all(utf16_len(p) <= 4096 for p in parts)
    assert "".join(parts) == text
