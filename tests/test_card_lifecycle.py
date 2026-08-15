"""Progress-card edit lifecycle — ``CardGone`` vs a transport hiccup.

The 2026-08-15 card storm: a night of server 502s made every failed
``editMessageText`` mint a brand-new status message, because ``update_card``
used to treat *any* exception from ``transport.edit`` as proof the message
was gone. It wasn't proof of anything except that the edit failed once.

``CardGone`` narrows "re-send" to the one case a transport can actually
confirm: the platform itself says the message no longer exists. Everything
else — timeouts, 5xx, rate limits — keeps the stored ``message_id`` and
retries the same edit on the next render.

Three pieces, each pinned below:

- ``delivery.update_card`` itself, driven only through its public surface
  (a fake ``CardTransport``), never by poking transport internals;
- the cloud transport's 409-to-``CardGone`` mapping (the server maps
  Telegram's own ``CardGone`` to 409 on the card endpoint);
- the direct Telegram transport's "message to edit not found" mapping,
  kept distinct from "message is not modified" (``CardUnchanged``).
"""

from __future__ import annotations

import pytest

from brr.gates import cloud, delivery, runtime, telegram


class _FakeTransport:
    """A ``CardTransport`` whose ``edit`` raises whatever the test wants."""

    def __init__(self, edit_raises: Exception | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edited: list[tuple[int, str]] = []
        self._edit_raises = edit_raises

    def send(self, text: str, *, reply_to: int | None = None) -> int | None:
        message_id = 100 + len(self.sent)
        self.sent.append((message_id, text))
        return message_id

    def edit(self, message_id: int, text: str) -> None:
        self.edited.append((message_id, text))
        if self._edit_raises is not None:
            raise self._edit_raises


# ── update_card, through the caller ──────────────────────────────────


def test_a_generic_edit_failure_does_not_resend_and_keeps_the_message_id(tmp_path):
    """A 502-shaped exception: not proof the message is gone."""
    gate, run_id = "telegram", "run-1"
    runtime.save_run_card(tmp_path, gate, run_id, {"message_id": 42, "last_text": "old"})
    transport = _FakeTransport(edit_raises=RuntimeError("502 Bad Gateway"))

    delivery.update_card(tmp_path, gate, run_id, "new", transport=transport)

    assert transport.sent == [], "a transport hiccup must not mint a duplicate card"
    entry = runtime.load_run_card(tmp_path, gate, run_id)
    assert entry["message_id"] == 42, "the next render must retry this same edit"
    assert entry["last_text"] == "old"


def test_card_gone_sends_exactly_one_replacement_and_saves_its_new_id(tmp_path):
    gate, run_id = "telegram", "run-1"
    runtime.save_run_card(tmp_path, gate, run_id, {"message_id": 42, "last_text": "old"})
    transport = _FakeTransport(edit_raises=delivery.CardGone("message to edit not found"))

    delivery.update_card(tmp_path, gate, run_id, "new", transport=transport)

    assert len(transport.sent) == 1
    new_id, sent_text = transport.sent[0]
    assert sent_text == "new"
    entry = runtime.load_run_card(tmp_path, gate, run_id)
    assert entry["message_id"] == new_id
    assert entry["last_text"] == "new"


def test_card_unchanged_neither_sends_nor_loses_state(tmp_path):
    gate, run_id = "telegram", "run-1"
    runtime.save_run_card(tmp_path, gate, run_id, {"message_id": 42, "last_text": "old"})
    transport = _FakeTransport(edit_raises=delivery.CardUnchanged())

    delivery.update_card(tmp_path, gate, run_id, "new", transport=transport)

    assert transport.sent == []
    entry = runtime.load_run_card(tmp_path, gate, run_id)
    assert entry["message_id"] == 42


def test_a_clean_edit_updates_last_text_with_no_send(tmp_path):
    gate, run_id = "telegram", "run-1"
    runtime.save_run_card(tmp_path, gate, run_id, {"message_id": 42, "last_text": "old"})
    transport = _FakeTransport(edit_raises=None)

    delivery.update_card(tmp_path, gate, run_id, "new", transport=transport)

    assert transport.sent == []
    assert transport.edited == [(42, "new")]
    entry = runtime.load_run_card(tmp_path, gate, run_id)
    assert entry["message_id"] == 42
    assert entry["last_text"] == "new"


# ── cloud transport: 409 is the only status that means "gone" ─────────


def test_cloud_transport_maps_409_to_card_gone(tmp_path, monkeypatch):
    def fake_request(base_url, method, path, **kwargs):
        err = RuntimeError("brnrd POST /v1/daemons/card -> 409: card not editable")
        err.status_code = 409  # type: ignore[attr-defined]
        raise err

    monkeypatch.setattr(cloud, "_request", fake_request)
    transport = cloud._CloudCardTransport({"brnrd_url": "https://x", "token": "t"}, "ev_1")

    with pytest.raises(delivery.CardGone):
        transport.edit(42, "new")


def test_cloud_transport_reraises_other_statuses(tmp_path, monkeypatch):
    def fake_request(base_url, method, path, **kwargs):
        err = RuntimeError("brnrd POST /v1/daemons/card -> 502: gateway")
        err.status_code = 502  # type: ignore[attr-defined]
        raise err

    monkeypatch.setattr(cloud, "_request", fake_request)
    transport = cloud._CloudCardTransport({"brnrd_url": "https://x", "token": "t"}, "ev_1")

    with pytest.raises(RuntimeError):
        transport.edit(42, "new")


# ── direct Telegram transport: "not found" vs "not modified" ──────────


def test_telegram_transport_maps_message_gone_to_card_gone(monkeypatch):
    def fake_api_call(token, method, params=None, *, poll=False):
        raise telegram._TelegramMessageGone("Bad Request: message to edit not found")

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)
    transport = telegram._CardTransport("secret", 555, None)

    with pytest.raises(delivery.CardGone):
        transport.edit(42, "new")


def test_telegram_transport_keeps_not_modified_as_card_unchanged(monkeypatch):
    def fake_api_call(token, method, params=None, *, poll=False):
        raise telegram._TelegramNotModified("Bad Request: message is not modified")

    monkeypatch.setattr(telegram, "_api_call", fake_api_call)
    transport = telegram._CardTransport("secret", 555, None)

    with pytest.raises(delivery.CardUnchanged):
        transport.edit(42, "new")


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_telegram_api_call_classifies_gone_vs_not_modified(monkeypatch):
    """The raw classification ``_CardTransport.edit`` relies on."""
    responses = iter([
        _FakeResponse(400, {"ok": False, "description": "Bad Request: message to edit not found"}),
        _FakeResponse(400, {"ok": False, "description": "Bad Request: message is not modified"}),
    ])
    monkeypatch.setattr(
        telegram._SESSION, "post", lambda *a, **k: next(responses)
    )

    with pytest.raises(telegram._TelegramMessageGone):
        telegram._api_call("secret", "editMessageText", {})

    with pytest.raises(telegram._TelegramNotModified):
        telegram._api_call("secret", "editMessageText", {})
