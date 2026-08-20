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


def test_select_cost_aware_defaults_to_balanced_class():
    """The 2026-08-19 recalibration: a fresh install's default run lands on
    the Shell's balanced Core, not the weakest one installed. The first-ever
    wake writes the repo contract and the product's first impression — the
    cheapest core was measurably not up to it, and defaulting to it made
    every fresh install open on the worst available foot."""
    runners = [
        _profile("strong", **{"class": "strong", "cost_rank": 50}),
        _profile("eco-b", **{"class": "economy", "cost_rank": 20}),
        _profile("eco-a", **{"class": "economy", "cost_rank": 10}),
        _profile("balanced", **{"class": "balanced", "cost_rank": 30}),
    ]
    chosen = rs.select_runner(runners)
    assert chosen.name == "balanced"  # cheapest of the target class


def test_select_cost_aware_default_class_is_a_target_not_a_ceiling():
    runners = [
        _profile("eco", **{"class": "economy", "cost_rank": 10}),
        _profile("bal", **{"class": "balanced", "cost_rank": 30}),
        _profile("strong", **{"class": "strong", "cost_rank": 50}),
    ]
    # The target class wins even when a cheaper class exists below it —
    # this is the semantic that changed: a ceiling here always collapsed
    # to the cheapest economy profile, making the knob a no-op for any
    # value above `economy`.
    assert rs.select_runner(runners, default_class="balanced").name == "bal"
    # `default_class=economy` restores the pre-change default exactly.
    assert rs.select_runner(runners, default_class="economy").name == "eco"
    # A strong target picks the cheapest strong profile.
    assert rs.select_runner(runners, default_class="strong").name == "strong"


def test_select_falls_back_above_target_class_before_going_below():
    runners = [
        _profile("strong", **{"class": "strong", "cost_rank": 50}),
        _profile("eco", **{"class": "economy", "cost_rank": 10}),
    ]
    # No balanced profile exists: the target is a floor before it is a
    # ceiling ("at least balanced", maintainer 2026-08-20) — a machine
    # with no balanced Core lands on the cheapest strong one, and economy
    # is the last resort, not the first fallback.
    assert rs.select_runner(runners).name == "strong"


def test_select_falls_back_below_only_when_nothing_at_or_above():
    runners = [
        _profile("eco-b", **{"class": "economy", "cost_rank": 20}),
        _profile("eco-a", **{"class": "economy", "cost_rank": 10}),
    ]
    # Nothing at or above the target; the cheapest below is all there is.
    assert rs.select_runner(runners).name == "eco-a"


def test_select_unclassed_profile_never_fills_the_above_target_slot():
    runners = [
        _profile("mystery", **{"cost_rank": 99}),
        _profile("eco", **{"class": "economy", "cost_rank": 10}),
    ]
    # An unclassed legacy profile sorts past every local class, but "past
    # strong" is not a known class — it must not ride the above-target
    # preference (the #1524 guard, kept at first selection).
    assert rs.select_runner(runners).name == "eco"


def test_select_economy_target_climbs_before_it_settles():
    runners = [
        _profile("bal", **{"class": "balanced", "cost_rank": 30}),
        _profile("strong", **{"class": "strong", "cost_rank": 50}),
    ]
    # default_class=economy with no economy profile: the nearest class
    # above wins (cheapest above = balanced), never a silent jump to strong.
    assert rs.select_runner(runners, default_class="economy").name == "bal"


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


def test_automatic_fallback_prefers_capability_within_eligible_pool():
    runners = [
        _profile(
            "claude-fable",
            provider="anthropic",
            quota_source="claude-local",
            capability_score=0.95,
            **{"class": "economy", "cost_rank": 15},
        ),
        _profile(
            "gemini-flash",
            provider="google",
            quota_source="gemini-local",
            **{"class": "economy", "cost_rank": 10},
        ),
        _profile(
            "codex-mini",
            provider="openai",
            quota_source="codex-local",
            capability_score=0.73,
            **{"class": "economy", "cost_rank": 20},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="claude-fable",
        failure_kind="quota_exhausted",
        tried=("claude-fable",),
    )

    assert chosen is not None
    assert chosen.name == "codex-mini"


def test_automatic_fallback_uses_cost_when_candidates_lack_capability_hints():
    runners = [
        _profile(
            "claude-fable",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "economy", "cost_rank": 15},
        ),
        _profile(
            "gemini-flash",
            provider="google",
            quota_source="gemini-local",
            **{"class": "economy", "cost_rank": 10},
        ),
        _profile(
            "codex-mini",
            provider="openai",
            quota_source="codex-local",
            **{"class": "economy", "cost_rank": 20},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="claude-fable",
        failure_kind="quota_exhausted",
        tried=("claude-fable",),
    )

    assert chosen is not None
    assert chosen.name == "gemini-flash"


def test_automatic_fallback_tiebreaks_equal_capability_by_cost():
    runners = [
        _profile(
            "claude-fable",
            provider="anthropic",
            quota_source="claude-local",
            capability_score=0.95,
            **{"class": "economy", "cost_rank": 15},
        ),
        _profile(
            "gemini-flash",
            provider="google",
            quota_source="gemini-local",
            capability_score=0.4,
            **{"class": "economy", "cost_rank": 10},
        ),
        _profile(
            "codex-mini",
            provider="openai",
            quota_source="codex-local",
            capability_score=0.4,
            **{"class": "economy", "cost_rank": 20},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="claude-fable",
        failure_kind="quota_exhausted",
        tried=("claude-fable",),
    )

    assert chosen is not None
    assert chosen.name == "gemini-flash"


def test_automatic_fallback_capability_sort_keeps_class_ceiling():
    runners = [
        _profile(
            "claude-fable",
            provider="anthropic",
            quota_source="claude-local",
            capability_score=0.95,
            **{"class": "economy", "cost_rank": 15},
        ),
        _profile(
            "codex-full",
            provider="openai",
            quota_source="codex-local",
            capability_score=0.99,
            **{"class": "strong", "cost_rank": 35},
        ),
        _profile(
            "gemini-flash",
            provider="google",
            quota_source="gemini-local",
            capability_score=0.2,
            **{"class": "economy", "cost_rank": 10},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="claude-fable",
        failure_kind="quota_exhausted",
        tried=("claude-fable",),
    )

    assert chosen is not None
    assert chosen.name == "gemini-flash"


def test_automatic_fallback_prefers_nearest_class_not_cheapest_core():
    # Regression for the measured defect (run-260819-1626-zdf0): codex-full
    # (strong) fails with auth_error and none of the candidates carry a
    # capability_score — the arrangement production actually has. The old
    # sort fell through to cost-rank-ascending across the whole eligible
    # pool and landed on claude-haiku (economy), the weakest installed
    # core. The fix must land on claude-opus: strong, like the failed
    # runner, cheapest *within* strong.
    runners = [
        _profile(
            "codex-full",
            provider="openai",
            quota_source="codex-local",
            **{"class": "strong", "cost_rank": 45},
        ),
        _profile(
            "claude-haiku",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "economy", "cost_rank": 10},
        ),
        _profile(
            "claude-sonnet",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "balanced", "cost_rank": 30},
        ),
        _profile(
            "claude-opus",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "strong", "cost_rank": 50},
        ),
        _profile(
            "claude-fable",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "strong", "cost_rank": 55},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="codex-full",
        failure_kind="auth_error",
        tried=("codex-full",),
    )

    assert chosen is not None
    assert chosen.name == "claude-opus"


def test_automatic_fallback_nearest_class_yields_to_capability_within_class():
    # Nearest-class wins the *class* choice; capability still breaks the tie
    # among same-class candidates once a class is settled.
    runners = [
        _profile(
            "codex-full",
            provider="openai",
            quota_source="codex-local",
            **{"class": "strong", "cost_rank": 45},
        ),
        _profile(
            "claude-haiku",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "economy", "cost_rank": 10},
        ),
        _profile(
            "claude-opus",
            provider="anthropic",
            quota_source="claude-local",
            capability_score=0.4,
            **{"class": "strong", "cost_rank": 50},
        ),
        _profile(
            "claude-fable",
            provider="anthropic",
            quota_source="claude-local",
            capability_score=0.9,
            **{"class": "strong", "cost_rank": 55},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="codex-full",
        failure_kind="auth_error",
        tried=("codex-full",),
    )

    assert chosen is not None
    assert chosen.name == "claude-fable"


def test_automatic_fallback_economy_current_stays_economy_or_nothing():
    # Pin: an economy current never escalates class even under the new
    # nearest-class preference — the class filter (class_rank <= current)
    # still runs before the sort sees anything.
    runners = [
        _profile(
            "codex-mini",
            provider="openai",
            quota_source="codex-local",
            **{"class": "economy", "cost_rank": 20},
        ),
        _profile(
            "claude-sonnet",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "balanced", "cost_rank": 30},
        ),
        _profile(
            "claude-opus",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "strong", "cost_rank": 50},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="codex-mini",
        failure_kind="quota_exhausted",
        tried=("codex-mini",),
    )

    assert chosen is None


def test_automatic_fallback_picks_same_or_cheaper_different_domain():
    runners = [
        _profile(
            "codex-full",
            provider="openai",
            quota_source="codex-local",
            **{"class": "balanced", "cost_rank": 35},
        ),
        _profile(
            "codex-mini",
            provider="openai",
            quota_source="codex-local",
            **{"class": "economy", "cost_rank": 20},
        ),
        _profile(
            "claude-haiku",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "economy", "cost_rank": 10},
        ),
        _profile(
            "claude-opus",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "strong", "cost_rank": 50},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="codex-full",
        failure_kind="quota_exhausted",
        tried=("codex-full",),
    )

    assert chosen is not None
    assert chosen.name == "claude-haiku"


def test_automatic_fallback_does_not_escalate_cost_or_pick_relay():
    runners = [
        _profile(
            "codex-mini",
            provider="openai",
            quota_source="codex-local",
            **{"class": "economy", "cost_rank": 20},
        ),
        _profile(
            "claude-opus",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "strong", "cost_rank": 50},
        ),
        _profile(
            "brnrd-codex",
            provider="openai",
            owner="brnrd",
            **{"class": "relay", "cost_rank": 1},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="codex-mini",
        failure_kind="quota_exhausted",
        tried=("codex-mini",),
    )

    assert chosen is None


def test_automatic_provider_fallback_requires_different_provider():
    runners = [
        _profile(
            "claude-api",
            provider="anthropic",
            **{"class": "balanced", "cost_rank": 30},
        ),
        _profile(
            "claude-subscription",
            provider="anthropic",
            quota_source="claude-local",
            **{"class": "economy", "cost_rank": 10},
        ),
        _profile(
            "codex-mini",
            provider="openai",
            quota_source="codex-local",
            **{"class": "economy", "cost_rank": 20},
        ),
    ]

    chosen = rs.automatic_fallback_runner(
        runners,
        current="claude-api",
        failure_kind="provider_error",
        tried=("claude-api",),
    )

    assert chosen is not None
    assert chosen.name == "codex-mini"


def test_automatic_fallback_ignores_non_operational_failures():
    runners = [
        _profile("codex", provider="openai", **{"class": "balanced"}),
        _profile("claude", provider="anthropic", **{"class": "balanced"}),
    ]

    assert rs.automatic_fallback_runner(
        runners,
        current="codex",
        failure_kind="runner_error",
        tried=("codex",),
    ) is None


def test_quality_escalation_prefers_strong_local_when_available():
    runners = [
        _profile("codex-mini", **{"class": "economy", "cost_rank": 20}),
        _profile("claude-sonnet", **{"class": "balanced", "cost_rank": 30}),
        _profile("claude-opus", **{"class": "strong", "cost_rank": 50}),
        _profile("relay-opus", owner="brnrd", **{"class": "relay", "cost_rank": 1}),
    ]

    chosen = rs.quality_escalation_runner(runners, current="codex-mini")

    assert chosen is not None
    assert chosen.name == "claude-opus"


def test_quality_escalation_falls_back_to_cheapest_stronger_local():
    runners = [
        _profile("codex-mini", **{"class": "economy", "cost_rank": 20}),
        _profile("claude-sonnet", **{"class": "balanced", "cost_rank": 30}),
    ]

    chosen = rs.quality_escalation_runner(runners, current="codex-mini")

    assert chosen is not None
    assert chosen.name == "claude-sonnet"


def test_quality_escalation_returns_none_from_strong_runner():
    runners = [
        _profile("claude-opus", **{"class": "strong", "cost_rank": 50}),
        _profile("relay-opus", owner="brnrd", **{"class": "relay", "cost_rank": 1}),
    ]

    assert rs.quality_escalation_runner(
        runners, current="claude-opus",
    ) is None


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
        repo="Gurio/brr",
    )
    assert req.proposed_runner == "claude-bare-api-only-opus"
    assert req.consent == "spend-plan"
    assert req.at == "2026-06-29T01:00:00Z"
    assert req.defer_until == "2026-06-29T01:00:00Z"
    assert req.repo == "Gurio/brr"


def test_fallback_from_an_unclassed_runner_does_not_escalate_to_strong():
    """The merge-time guard on the nearest-class key (2026-08-19).

    A legacy bare-``shell=`` profile has no cost class, which ranks past
    every local class — so the distance term, unguarded, would hand an
    unclassed failure to the strongest installed core: silent escalation,
    the contract's own forbidden move. With no class to be near, the key
    goes neutral and cheapest-capable wins, exactly as before the change.
    """
    from brr import runner_select as rs
    from brr import runner_failures

    current = rs.implicit_runner("mystery")  # cost_class None
    candidates = [
        current,
        rs.RunnerProfile(name="cheap", profile="cheap", shell="x",
                         cost_class="economy", cost_rank=10,
                         quota_source="a"),
        rs.RunnerProfile(name="mighty", profile="mighty", shell="y",
                         cost_class="strong", cost_rank=50,
                         quota_source="b"),
    ]
    picked = rs.automatic_fallback_runner(
        candidates, current="mystery",
        failure_kind=runner_failures.AUTH_ERROR,
    )
    assert picked is not None and picked.name == "cheap"
