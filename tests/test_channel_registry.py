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


# ── Golden pins: the pre-registry outputs, byte for byte ─────────────
# These strings are on disk in every existing conversation directory;
# changing any of them is a migration, not a refactor.


# ── Signal: new rows, pinned deliberately ────────────────────────────
# Previously signal fell to the unlisted-source default ("signal:default"
# for every sender — one shared thread) and to no correspondent identity
# at all. The gate has shipped dark on every account (no on-disk
# threads), so listing it is a fix with no migration. If that premise is
# ever false, this is the test to consult before "fixing" it back.


