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


# ── One list of gates, owned by one module (2026-08-05) ──────────────────


def test_the_cli_does_not_keep_its_own_list_of_gates():
    """`gates.BUILTIN_GATES` owns the set; the CLI derives it.

    Both files used to carry the same five-name literal *and* a comment
    calling itself the single source of truth for it. They agreed only
    because a human kept them agreeing — the property a derived name does
    not need. Asks the owning module rather than re-listing the members,
    so a gate added there and forgotten here fails instead of vanishing.
    """
    from brr import cli
    from brr import gates

    assert cli.GATES is gates.BUILTIN_GATES
    # Sanity: a rename that emptied the set would make every assertion in
    # this file pass over nothing.
    assert len(gates.BUILTIN_GATES) >= 5


def test_every_gate_the_cli_offers_can_actually_be_loaded():
    """The help string and the dispatcher answer for the same population."""
    from brr import cli

    for name in cli.GATES:
        module = cli._load_gate(name)
        assert hasattr(module, "auth"), name
        assert hasattr(module, "bind"), name


def test_a_channel_that_is_not_a_gate_gets_a_pointer_not_a_denial():
    """WhatsApp is publicly listed as supported and has no gate module.

    `brnrd gate setup whatsapp` is the command a user who reads the support
    matrix will type. Answering "unknown gate" is true of the code and false
    of the product.
    """
    import pytest

    from brr import cli, support_matrix

    slugs = {door.slug for door in support_matrix.DOORS}
    # The premise, asserted rather than remembered: whatsapp is advertised.
    assert "whatsapp" in slugs
    assert "whatsapp" not in cli.GATES

    with pytest.raises(SystemExit) as excinfo:
        cli._load_gate("whatsapp")
    message = str(excinfo.value)
    assert "cloud" in message
    assert "unknown gate" not in message

    # A name that is neither a gate nor a known channel still says so plainly.
    with pytest.raises(SystemExit) as excinfo:
        cli._load_gate("carrier-pigeon")
    assert "unknown gate" in str(excinfo.value)
