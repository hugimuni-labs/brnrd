from brr.gates import signal, slack, telegram


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
        return {"ok": True, "user_id": "U_BOT"}

    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(slack, "_slack_api", fake_slack_api)

    slack.setup(brr_dir)

    assert slack._load_state(brr_dir) == {
        "token": "xoxb-secret",
        "bot_user_id": "U_BOT",
        "channel": "C123",
    }
    assert calls == [
        ("xoxb-secret", "auth.test", None),
        ("xoxb-secret", "chat.postMessage", {"channel": "C123", "text": "brnrd bound."}),
    ]


def test_signal_setup_saves_api_url_number_and_paired_sender(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inputs = iter([
        "http://127.0.0.1:8080",  # api_url (auth)
        "+15550000000",  # this gate's own number (auth)
        "+15551111111",  # paired principal (bind)
    ])
    calls = []

    def fake_api_get(api_url, path, *, params=None):
        calls.append(("GET", api_url, path, params))
        return {"version": "0.1"}

    def fake_api_post(api_url, path, payload):
        calls.append(("POST", api_url, path, payload))
        return {}

    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(signal, "_api_get", fake_api_get)
    monkeypatch.setattr(signal, "_api_post", fake_api_post)

    signal.setup(brr_dir)

    assert signal._load_state(brr_dir) == {
        "api_url": "http://127.0.0.1:8080",
        "number": "+15550000000",
        "paired_sender": "+15551111111",
    }
    assert calls == [
        ("GET", "http://127.0.0.1:8080", "/v1/about", None),
        (
            "POST",
            "http://127.0.0.1:8080",
            "/v2/send",
            {
                "message": "brnrd bound.",
                "number": "+15550000000",
                "recipients": ["+15551111111"],
            },
        ),
    ]
