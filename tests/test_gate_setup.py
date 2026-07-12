from brr.gates import slack, telegram


def test_telegram_setup_saves_token_and_accepts_any_chat(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inputs = iter(["secret-token", ""])

    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(
        telegram,
        "_api_call",
        lambda token, method, params=None: {
            "result": {"username": "brrbot"},
        },
    )

    telegram.setup(brr_dir)

    assert telegram._load_state(brr_dir) == {"token": "secret-token"}


def test_slack_setup_saves_token_and_channel(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inputs = iter(["xoxb-secret", "C123"])
    calls = []

    def fake_slack_api(token, method, params=None):
        calls.append((token, method, params))
        return {"ok": True}

    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(slack, "_slack_api", fake_slack_api)

    slack.setup(brr_dir)

    assert slack._load_state(brr_dir) == {
        "token": "xoxb-secret",
        "channel": "C123",
    }
    assert calls == [
        ("xoxb-secret", "auth.test", None),
        ("xoxb-secret", "chat.postMessage", {"channel": "C123", "text": "brnrd bound."}),
    ]

