"""Tests for the runner-media data model and deterministic selector.

These cover the foundation slice (``kb/design-runner-media.md`` step 1): the
medium schema, the legacy implicit shim, available-medium filtering, and the
conservative cost-aware selector. Dispatch wiring is a later slice, so there
are no daemon/subprocess paths here.
"""

from brr import runner as runner_mod
from brr import runner_media as rm


def _medium(name, **kw):
    return rm.medium_from_profile(name, kw)


def test_implicit_medium_is_uncosted_local():
    m = rm.implicit_medium("codex")
    assert m.name == "codex"
    assert m.profile == "codex"
    assert m.owner == "user"
    assert m.cost_class is None
    assert m.cost_rank is None
    assert not m.is_relay
    # Unknown cost must sort *after* any costed medium, never as 0.
    assert m.rank == rm._UNKNOWN_COST_RANK


def test_medium_from_profile_parses_metadata():
    m = rm.medium_from_profile(
        "codex",
        {
            "cmd": "codex exec",
            "hooks": "codex",
            "provider": "openai",
            "owner": "user",
            "class": "balanced",
            "cost_rank": "25",
            "quota_source": "codex-local",
        },
    )
    assert m.provider == "openai"
    assert m.cost_class == "balanced"
    assert m.cost_rank == 25  # coerced from string
    assert m.quota_source == "codex-local"
    assert m.hooks == "codex"


def test_relay_medium_detected_by_owner_or_class():
    by_owner = _medium("relay-a", owner="brnrd", **{"class": "balanced"})
    by_class = _medium("relay-b", owner="user", **{"class": "relay"})
    assert by_owner.is_relay
    assert by_class.is_relay
    assert not _medium("local", owner="user", **{"class": "economy"}).is_relay


def test_summary_is_compact_and_tags_non_user_owner():
    local = _medium("codex", model="gpt-5", **{"class": "balanced"})
    assert local.summary() == "codex · gpt-5 (balanced)"
    relay = _medium("brnrd-codex", model="gpt-5", owner="brnrd", **{"class": "relay"})
    assert "brnrd" in relay.summary()


def test_select_cost_aware_prefers_cheapest_economy():
    media = [
        _medium("strong", **{"class": "strong", "cost_rank": 50}),
        _medium("eco-b", **{"class": "economy", "cost_rank": 20}),
        _medium("eco-a", **{"class": "economy", "cost_rank": 10}),
        _medium("balanced", **{"class": "balanced", "cost_rank": 30}),
    ]
    chosen = rm.select_medium(media)
    assert chosen.name == "eco-a"  # cheapest at-or-below economy


def test_select_cost_aware_respects_default_class_ceiling():
    media = [
        _medium("eco", **{"class": "economy", "cost_rank": 10}),
        _medium("bal", **{"class": "balanced", "cost_rank": 30}),
        _medium("strong", **{"class": "strong", "cost_rank": 50}),
    ]
    # Ceiling at balanced still picks the cheapest at-or-below it (economy).
    assert rm.select_medium(media, default_class="balanced").name == "eco"


def test_select_falls_back_when_no_medium_at_or_below_class():
    media = [
        _medium("strong", **{"class": "strong", "cost_rank": 50}),
        _medium("balanced", **{"class": "balanced", "cost_rank": 30}),
    ]
    # No economy medium exists; selector falls back to cheapest of any class.
    assert rm.select_medium(media).name == "balanced"


def test_select_never_auto_picks_relay():
    media = [
        _medium("relay", owner="brnrd", **{"class": "relay", "cost_rank": 1}),
        _medium("local", **{"class": "balanced", "cost_rank": 30}),
    ]
    # The relay is cheapest by rank but must not be auto-selected.
    assert rm.select_medium(media).name == "local"


def test_select_returns_none_when_only_relay_available():
    media = [_medium("relay", owner="brnrd", **{"class": "relay", "cost_rank": 1})]
    assert rm.select_medium(media) is None


def test_select_override_wins_even_over_cheaper():
    media = [
        _medium("eco", **{"class": "economy", "cost_rank": 10}),
        _medium("strong", **{"class": "strong", "cost_rank": 50}),
    ]
    assert rm.select_medium(media, override="strong").name == "strong"


def test_select_override_for_relay_is_honoured():
    media = [
        _medium("relay", owner="brnrd", **{"class": "relay", "cost_rank": 1}),
        _medium("local", **{"class": "balanced", "cost_rank": 30}),
    ]
    # Explicit pick of relay is allowed (the consent flow gates spend elsewhere).
    assert rm.select_medium(media, override="relay").name == "relay"


def test_fixed_policy_picks_cheapest_local_without_class_logic():
    media = [
        _medium("strong", **{"class": "strong", "cost_rank": 50}),
        _medium("eco", **{"class": "economy", "cost_rank": 10}),
    ]
    assert rm.select_medium(media, policy=rm.POLICY_FIXED).name == "eco"


def test_uncosted_media_sort_after_costed():
    media = [
        _medium("uncosted"),  # no class, no rank
        _medium("eco", **{"class": "economy", "cost_rank": 10}),
    ]
    # Cost-aware: economy beats uncosted (unknown sorts last).
    assert rm.select_medium(media).name == "eco"


def test_available_media_filters_by_path(monkeypatch):
    monkeypatch.setattr(
        runner_mod,
        "_profiles_cache",
        {
            "codex": {"cmd": "codex exec", "class": "balanced", "cost_rank": 25},
            "gemini": {"cmd": "gemini -p", "class": "economy", "cost_rank": 10},
        },
    )
    monkeypatch.setattr(runner_mod, "_profiles_cache_key", "bundled:runners.md")
    monkeypatch.setattr(
        runner_mod.shutil,
        "which",
        lambda name: "/usr/bin/codex" if name == "codex" else None,
    )
    media = rm.available_media()
    assert [m.name for m in media] == ["codex"]
    assert media[0].cost_class == "balanced"


def test_respawn_request_shape():
    req = rm.RespawnRequest(
        reason="quota exhausted",
        proposed_medium="claude-bare-api-only-opus",
        carry_forward="plan committed on brr/foo",
        consent="spend-plan",
    )
    assert req.proposed_medium == "claude-bare-api-only-opus"
    assert req.consent == "spend-plan"
