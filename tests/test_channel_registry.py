"""The channel registry and its rendering engine (`conversations`).

The registry (`brr.channels.registry`) owns the member list; these tests
derive every parametrization from the registry itself, never from a
restated literal — a test that lists the same members as the
implementation goes green on exactly the bug it exists to prevent
(playbook: the guard must ask the owning module what the class contains).
"""

from __future__ import annotations

from brr import conversations
from brr.channels import registry


def _synthetic_thread_meta(rule: registry.ThreadRule) -> dict:
    """Build meta satisfying every part of *rule* from its own fields."""
    meta: dict = {"source": rule.channel}
    for part in rule.parts:
        for field in part.fields:
            meta[field] = "7" if part.as_int else "v"
            break  # first field is enough; later fields are fallbacks
    return meta


def test_registry_is_not_empty_and_keys_match_rows():
    # Sanity assertions: without these, a rename could turn every derived
    # parametrization below into a no-op passing over an empty set.
    assert len(registry.THREAD_RULES) >= 5
    assert "telegram" in registry.THREAD_RULES
    for key, rule in registry.THREAD_RULES.items():
        assert rule.channel == key
    assert len(registry.IDENTITY_RULES) >= 4


def test_every_thread_rule_yields_a_key_from_its_own_fields():
    for key, rule in registry.THREAD_RULES.items():
        meta = _synthetic_thread_meta(rule)
        result = conversations.gate_thread_key(meta)
        assert result is not None, key
        assert result.startswith(f"{key}:"), (key, result)


def test_every_thread_rule_fallback_is_honored_when_required_parts_missing():
    for key, rule in registry.THREAD_RULES.items():
        if not any(p.required for p in rule.parts):
            continue
        result = conversations.gate_thread_key({"source": key})
        assert result == rule.fallback, (key, result)


def test_every_native_channel_resolves_a_correspondent():
    # The consumer must answer for every member: each native channel row
    # (cloud is the relay, deliberately identity-resolved per origin
    # platform instead) has an identity rule, and it renders.
    for key in registry.THREAD_RULES:
        if key == "cloud":
            continue
        assert key in registry.IDENTITY_RULES, key
    for key, rule in registry.IDENTITY_RULES.items():
        meta = {"source": key, rule.fields[0].field: "Val"}
        result = conversations.correspondent_key_for_event(meta)
        assert result is not None, key
        assert result.startswith(f"{rule.platform}:{rule.fields[0].label}:"), (key, result)


def test_unlisted_source_threads_to_default_and_has_no_identity():
    assert conversations.gate_thread_key({"source": "schedule"}) == "schedule:default"
    assert conversations.correspondent_key_for_event({"source": "schedule"}) is None


# ── Golden pins: the pre-registry outputs, byte for byte ─────────────
# These strings are on disk in every existing conversation directory;
# changing any of them is a migration, not a refactor.


def test_golden_thread_keys_unchanged():
    assert (
        conversations.gate_thread_key(
            {"source": "telegram", "telegram_chat_id": 155, "telegram_topic_id": ""}
        )
        == "telegram:155:"
    )
    assert conversations.gate_thread_key({"source": "telegram"}) is None
    assert (
        conversations.gate_thread_key(
            {"source": "slack", "slack_channel": "C1", "slack_thread_ts": "8.8", "slack_ts": "9.1"}
        )
        == "slack:C1:8.8"
    )
    assert (
        conversations.gate_thread_key(
            {"source": "github", "github_repo": "o/r", "github_issue_number": "42"}
        )
        == "github:o/r:42"
    )
    # a non-numeric issue number voids the key, exactly as before
    assert (
        conversations.gate_thread_key(
            {"source": "github", "github_repo": "o/r", "github_issue_number": "x"}
        )
        is None
    )
    assert (
        conversations.gate_thread_key(
            {"source": "cloud", "cloud_platform": "telegram", "cloud_chat_id": 155, "cloud_topic_id": ""}
        )
        == "cloud:telegram:155:"
    )
    assert conversations.gate_thread_key({"source": "cloud"}) == "cloud:default"
    assert conversations.gate_thread_key({}) is None


def test_golden_correspondent_keys_unchanged():
    assert (
        conversations.correspondent_key_for_event(
            {"source": "telegram", "telegram_user_id": 155}
        )
        == "telegram:user-id:155"
    )
    assert (
        conversations.correspondent_key_for_event(
            {"source": "telegram", "telegram_username": "Stas"}
        )
        == "telegram:username:stas"
    )
    assert (
        conversations.correspondent_key_for_event(
            {"source": "cloud", "cloud_platform": "telegram", "cloud_user_id": 155}
        )
        == "telegram:user-id:155"
    )
    assert (
        conversations.correspondent_key_for_event(
            {"source": "cloud", "cloud_platform": "whatsapp", "cloud_user_id": "4917"}
        )
        == "whatsapp:user:4917"
    )
    assert (
        conversations.correspondent_key_for_event(
            {"source": "cloud", "cloud_platform": "github", "cloud_user": "Bob"}
        )
        == "github:login:bob"
    )
    assert (
        conversations.correspondent_key_for_event({"source": "github", "github_author": "Bob"})
        == "github:login:bob"
    )
    assert (
        conversations.correspondent_key_for_event({"source": "slack", "slack_user": "U1"})
        == "slack:user:u1"
    )
    assert (
        conversations.correspondent_key_for_event({"correspondent_key": "  x  y "}) == "x y"
    )


# ── Signal: new rows, pinned deliberately ────────────────────────────
# Previously signal fell to the unlisted-source default ("signal:default"
# for every sender — one shared thread) and to no correspondent identity
# at all. The gate has shipped dark on every account (no on-disk
# threads), so listing it is a fix with no migration. If that premise is
# ever false, this is the test to consult before "fixing" it back.


def test_signal_threads_per_sender_and_has_identity():
    assert (
        conversations.gate_thread_key({"source": "signal", "signal_sender": "+4917"})
        == "signal:+4917:"
    )
    assert conversations.gate_thread_key({"source": "signal"}) == "signal:default"
    assert (
        conversations.correspondent_key_for_event(
            {"source": "signal", "signal_sender": "+4917"}
        )
        == "signal:user:_4917"  # _identity_component maps '+' to '_'
    )
