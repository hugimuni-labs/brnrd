"""The messenger-door registry (#1465) — one owning place, per connector:
can it mint a deep link, from what derived config, what fallback shape.

Discipline named in the issue itself: the registry test asks the owning
module for the connector set directly (`messenger_doors.PLATFORMS`) rather
than hand-copying a list of platform names — a hand-copied list goes green
on the exact bug this exists to prevent (a connector added to the registry
with no corresponding renderer support, silently missing from the set an
older, stale test still asserts).
"""

from __future__ import annotations

import pytest

from brnrd import messenger_doors
from brnrd.config import Settings


def _settings(**overrides):
    base = dict(
        database_url="sqlite:///:memory:",
        telegram_bot_token="",
        telegram_bot_username="",
        whatsapp_access_token="",
        whatsapp_phone_number_id="",
    )
    base.update(overrides)
    return Settings(**base)


# --- the registry itself ----------------------------------------------------


def test_the_connector_set_is_non_empty_and_every_member_answers():
    """The registry test the issue's own discipline names: ask the owning
    module for the connector set, never hand-copy a list — and every
    member must resolve to a real `MessengerDoor` row with no error."""
    assert len(messenger_doors.PLATFORMS) > 0
    identities = messenger_doors.MessengerIdentities()
    doors = messenger_doors.messenger_doors(identities)
    assert {d.platform for d in doors} == set(messenger_doors.PLATFORMS)
    for door in doors:
        # Every member answers: a real bool, a wire shape that round-trips.
        assert isinstance(door.deep_link_available, bool)
        assert door.to_wire() == {"platform": door.platform, "deep_link_available": door.deep_link_available}


def test_telegram_and_whatsapp_are_available_when_their_identity_is_set():
    identities = messenger_doors.MessengerIdentities(
        telegram_bot_username="brnrd_bot", whatsapp_e164="15551234567"
    )
    doors = {d.platform: d for d in messenger_doors.messenger_doors(identities)}
    assert doors["telegram"].deep_link_available is True
    assert doors["whatsapp"].deep_link_available is True


def test_no_identity_no_door():
    identities = messenger_doors.MessengerIdentities()
    doors = {d.platform: d for d in messenger_doors.messenger_doors(identities)}
    assert doors["telegram"].deep_link_available is False
    assert doors["whatsapp"].deep_link_available is False


def test_slack_and_signal_never_have_a_deep_link_regardless_of_identities():
    """#1465 — declared `deep_link_available: false` unconditionally: no
    mint lane exists for either, so nothing in `MessengerIdentities` can
    ever flip them true. The set stays complete rather than the platform
    silently vanishing."""
    identities = messenger_doors.MessengerIdentities(
        telegram_bot_username="brnrd_bot", whatsapp_e164="15551234567"
    )
    doors = {d.platform: d for d in messenger_doors.messenger_doors(identities)}
    assert doors["slack"].deep_link_available is False
    assert doors["signal"].deep_link_available is False


# --- env_only_identities (the zero-network fallback) ------------------------


def test_env_only_identities_reads_a_shape_valid_telegram_username():
    settings = _settings(telegram_bot_username="brnrd_bot")
    assert messenger_doors.env_only_identities(settings).telegram_bot_username == "brnrd_bot"


def test_env_only_identities_strips_a_leading_at():
    settings = _settings(telegram_bot_username="@brnrd_bot")
    assert messenger_doors.env_only_identities(settings).telegram_bot_username == "brnrd_bot"


def test_env_only_identities_rejects_a_shape_invalid_telegram_username():
    # The #1242 spelling: the GitHub bot login, hyphenated.
    settings = _settings(telegram_bot_username="brnrd-bot")
    assert messenger_doors.env_only_identities(settings).telegram_bot_username == ""


def test_env_only_identities_never_carries_a_whatsapp_number():
    """#1465's own point: there is no hand-typed WhatsApp number env var to
    read — the Cloud API lookup is the only source, so the env-only
    fallback is always empty for WhatsApp."""
    settings = _settings(whatsapp_access_token="tok", whatsapp_phone_number_id="123")
    assert messenger_doors.env_only_identities(settings).whatsapp_e164 == ""


# --- derive_telegram_bot_username (startup-only, mocked network) -----------


def test_derive_telegram_skips_the_network_call_with_no_token():
    settings = _settings(telegram_bot_username="brnrd_bot")
    # No monkeypatch of `fetch_bot_username` at all — if this attempted a
    # real network call with no token it would raise/hang; reaching the
    # assertion at all proves it took the short-circuit path.
    assert messenger_doors.derive_telegram_bot_username(settings) == "brnrd_bot"


def test_derive_telegram_prefers_getme_over_env(monkeypatch):
    monkeypatch.setattr(messenger_doors_telegram(), "fetch_bot_username", lambda token, timeout=10.0: "from_getme")
    settings = _settings(telegram_bot_token="t", telegram_bot_username="from_env")
    assert messenger_doors.derive_telegram_bot_username(settings) == "from_getme"


def test_derive_telegram_falls_back_to_env_when_getme_fails(monkeypatch):
    monkeypatch.setattr(messenger_doors_telegram(), "fetch_bot_username", lambda token, timeout=10.0: None)
    settings = _settings(telegram_bot_token="t", telegram_bot_username="brnrd_bot")
    assert messenger_doors.derive_telegram_bot_username(settings) == "brnrd_bot"


def test_derive_telegram_falls_back_to_env_when_getme_returns_an_invalid_shape(monkeypatch):
    monkeypatch.setattr(
        messenger_doors_telegram(), "fetch_bot_username", lambda token, timeout=10.0: "bad-shape-name"
    )
    settings = _settings(telegram_bot_token="t", telegram_bot_username="brnrd_bot")
    assert messenger_doors.derive_telegram_bot_username(settings) == "brnrd_bot"


def test_derive_telegram_is_empty_when_neither_source_is_valid(monkeypatch):
    monkeypatch.setattr(messenger_doors_telegram(), "fetch_bot_username", lambda token, timeout=10.0: None)
    settings = _settings(telegram_bot_token="t", telegram_bot_username="")
    assert messenger_doors.derive_telegram_bot_username(settings) == ""


def messenger_doors_telegram():
    from brnrd.platforms import telegram

    return telegram


# --- derive_whatsapp_number (startup-only, mocked network) ------------------


def test_derive_whatsapp_skips_the_network_call_when_unconfigured():
    settings = _settings()
    assert messenger_doors.derive_whatsapp_number(settings) == ""


def test_derive_whatsapp_strips_formatting_to_bare_digits(monkeypatch):
    from brnrd.platforms import whatsapp as wa

    monkeypatch.setattr(wa, "fetch_display_phone_number", lambda *a, **k: "+1 555-123-4567")
    settings = _settings(whatsapp_access_token="tok", whatsapp_phone_number_id="123")
    assert messenger_doors.derive_whatsapp_number(settings) == "15551234567"


def test_derive_whatsapp_is_empty_when_the_lookup_fails(monkeypatch):
    from brnrd.platforms import whatsapp as wa

    monkeypatch.setattr(wa, "fetch_display_phone_number", lambda *a, **k: None)
    settings = _settings(whatsapp_access_token="tok", whatsapp_phone_number_id="123")
    assert messenger_doors.derive_whatsapp_number(settings) == ""


# --- mint_deep_link ----------------------------------------------------------


def test_mint_deep_link_builds_the_telegram_url():
    identities = messenger_doors.MessengerIdentities(telegram_bot_username="brnrd_bot")
    assert messenger_doors.mint_deep_link("telegram", identities, "PK-abc") == "https://t.me/brnrd_bot?start=PK-abc"


def test_mint_deep_link_builds_the_whatsapp_url():
    identities = messenger_doors.MessengerIdentities(whatsapp_e164="15551234567")
    assert (
        messenger_doors.mint_deep_link("whatsapp", identities, "PK-abc")
        == "https://wa.me/15551234567?text=PK-abc"
    )


def test_mint_deep_link_is_none_without_the_identity():
    identities = messenger_doors.MessengerIdentities()
    assert messenger_doors.mint_deep_link("telegram", identities, "PK-abc") is None
    assert messenger_doors.mint_deep_link("whatsapp", identities, "PK-abc") is None


@pytest.mark.parametrize("platform", ["slack", "signal", "unknown-platform"])
def test_mint_deep_link_is_none_for_a_platform_with_no_mint_lane(platform):
    identities = messenger_doors.MessengerIdentities(telegram_bot_username="brnrd_bot", whatsapp_e164="15551234567")
    assert messenger_doors.mint_deep_link(platform, identities, "PK-abc") is None


# --- pair_instructions --------------------------------------------------------


def test_pair_instructions_telegram_with_deep_link_names_start():
    text = messenger_doors.pair_instructions("telegram", "PK-abc", "https://t.me/brnrd_bot?start=PK-abc")
    assert "PK-abc" in text
    assert "https://t.me/brnrd_bot?start=PK-abc" in text


def test_pair_instructions_telegram_without_deep_link_falls_back_to_manual():
    text = messenger_doors.pair_instructions("telegram", "PK-abc", None)
    assert "/start PK-abc" in text


def test_pair_instructions_whatsapp_without_deep_link_names_the_bare_code():
    text = messenger_doors.pair_instructions("whatsapp", "PK-abc", None)
    assert "PK-abc" in text
    assert "no other words" in text


def test_pair_instructions_raises_for_an_unsupported_platform():
    with pytest.raises(ValueError):
        messenger_doors.pair_instructions("slack", "PK-abc", None)
