"""Tests for the runner-profile data model and deterministic selector.

These cover the foundation slice (``kb/design-runner-cores.md`` step 1): the
profile schema, the legacy implicit shim, available-profile filtering, and the
conservative cost-aware selector. Dispatch wiring is a later slice, so there
are no daemon/subprocess paths here.
"""

from brr import runner as runner_mod
from brr import runner_select as rs


def _profile(name, **kw):
    return rs.runner_from_profile(name, kw)


def test_implicit_runner_is_uncosted_local():
    r = rs.implicit_runner("codex")
    assert r.name == "codex"
    assert r.profile == "codex"
    assert r.owner == "user"
    assert r.cost_class is None
    assert r.cost_rank is None
    assert not r.is_relay
    # Unknown cost must sort *after* any costed profile, never as 0.
    assert r.rank == rs._UNKNOWN_COST_RANK


def test_runner_from_profile_parses_metadata():
    r = rs.runner_from_profile(
        "codex",
        {
            "cmd": "codex exec",
            "hooks": "codex",
            "provider": "openai",
            "owner": "user",
            "class": "balanced",
            "cost_rank": "25",
            "quota_source": "codex-local",
            "capability_score": "0.72",
            "capability_source": "benchmark-cache",
            "capability_freshness": "2026-06-29",
        },
    )
    assert r.provider == "openai"
    assert r.cost_class == "balanced"
    assert r.cost_rank == 25  # coerced from string
    assert r.quota_source == "codex-local"
    assert r.hooks == "codex"
    assert r.capability_score == 0.72
    assert r.capability_source == "benchmark-cache"
    assert r.capability_freshness == "2026-06-29"


def test_relay_profile_detected_by_owner_or_class():
    by_owner = _profile("relay-a", owner="brnrd", **{"class": "balanced"})
    by_class = _profile("relay-b", owner="user", **{"class": "relay"})
    assert by_owner.is_relay
    assert by_class.is_relay
    assert not _profile("local", owner="user", **{"class": "economy"}).is_relay


def test_summary_is_compact_and_tags_non_user_owner():
    local = _profile("codex", model="gpt-5", **{"class": "balanced"})
    assert local.summary() == "codex · gpt-5 (balanced)"
    relay = _profile("brnrd-codex", model="gpt-5", owner="brnrd", **{"class": "relay"})
    assert "brnrd" in relay.summary()


def test_select_cost_aware_prefers_cheapest_economy():
    runners = [
        _profile("strong", **{"class": "strong", "cost_rank": 50}),
        _profile("eco-b", **{"class": "economy", "cost_rank": 20}),
        _profile("eco-a", **{"class": "economy", "cost_rank": 10}),
        _profile("balanced", **{"class": "balanced", "cost_rank": 30}),
    ]
    chosen = rs.select_runner(runners)
    assert chosen.name == "eco-a"  # cheapest at-or-below economy


def test_select_cost_aware_respects_default_class_ceiling():
    runners = [
        _profile("eco", **{"class": "economy", "cost_rank": 10}),
        _profile("bal", **{"class": "balanced", "cost_rank": 30}),
        _profile("strong", **{"class": "strong", "cost_rank": 50}),
    ]
    # Ceiling at balanced still picks the cheapest at-or-below it (economy).
    assert rs.select_runner(runners, default_class="balanced").name == "eco"


def test_select_falls_back_when_no_profile_at_or_below_class():
    runners = [
        _profile("strong", **{"class": "strong", "cost_rank": 50}),
        _profile("balanced", **{"class": "balanced", "cost_rank": 30}),
    ]
    # No economy profile exists; selector falls back to cheapest of any class.
    assert rs.select_runner(runners).name == "balanced"


def test_select_never_auto_picks_relay():
    runners = [
        _profile("relay", owner="brnrd", **{"class": "relay", "cost_rank": 1}),
        _profile("local", **{"class": "balanced", "cost_rank": 30}),
    ]
    # The relay is cheapest by rank but must not be auto-selected.
    assert rs.select_runner(runners).name == "local"


def test_select_returns_none_when_only_relay_available():
    runners = [_profile("relay", owner="brnrd", **{"class": "relay", "cost_rank": 1})]
    assert rs.select_runner(runners) is None


def test_select_override_wins_even_over_cheaper():
    runners = [
        _profile("eco", **{"class": "economy", "cost_rank": 10}),
        _profile("strong", **{"class": "strong", "cost_rank": 50}),
    ]
    assert rs.select_runner(runners, override="strong").name == "strong"


def test_select_override_for_relay_is_honoured():
    runners = [
        _profile("relay", owner="brnrd", **{"class": "relay", "cost_rank": 1}),
        _profile("local", **{"class": "balanced", "cost_rank": 30}),
    ]
    # Explicit pick of relay is allowed (the consent flow gates spend elsewhere).
    assert rs.select_runner(runners, override="relay").name == "relay"


def test_fixed_policy_picks_cheapest_local_without_class_logic():
    runners = [
        _profile("strong", **{"class": "strong", "cost_rank": 50}),
        _profile("eco", **{"class": "economy", "cost_rank": 10}),
    ]
    assert rs.select_runner(runners, policy=rs.POLICY_FIXED).name == "eco"


def test_uncosted_profiles_sort_after_costed():
    runners = [
        _profile("uncosted"),  # no class, no rank
        _profile("eco", **{"class": "economy", "cost_rank": 10}),
    ]
    # Cost-aware: economy beats uncosted (unknown sorts last).
    assert rs.select_runner(runners).name == "eco"


def test_available_runners_filters_by_path(monkeypatch):
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
    runners = rs.available_runners()
    assert [r.name for r in runners] == ["codex"]
    assert runners[0].cost_class == "balanced"


def test_respawn_request_shape():
    req = rs.RespawnRequest(
        reason="quota exhausted",
        proposed_runner="claude-bare-api-only-opus",
        carry_forward="plan committed on brr/foo",
        consent="spend-plan",
        at="2026-06-29T01:00:00Z",
        defer_until="2026-06-29T01:00:00Z",
    )
    assert req.proposed_runner == "claude-bare-api-only-opus"
    assert req.consent == "spend-plan"
    assert req.at == "2026-06-29T01:00:00Z"
    assert req.defer_until == "2026-06-29T01:00:00Z"
