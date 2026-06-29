"""The boundary facet schema — the single definition every renderer projects."""

from brr import facets


def test_schema_is_wall_derived_and_ordered():
    keys = [f.key for f in facets.FACETS]
    assert keys == [
        "quota", "spend", "context_window", "coexisting_runs", "remote_scm"
    ]
    # Level facets are the walls; state facets are actionable posture.
    by_key = facets.FACETS_BY_KEY
    assert by_key["quota"].kind == facets.LEVEL
    assert by_key["spend"].kind == facets.LEVEL
    assert by_key["context_window"].kind == facets.LEVEL
    assert by_key["coexisting_runs"].kind == facets.STATE
    assert by_key["remote_scm"].kind == facets.STATE
    # coexisting_runs is the only optional facet (single-flight someday-nicety).
    assert by_key["coexisting_runs"].required is False
    assert all(s.required for s in facets.FACETS if s.key != "coexisting_runs")


def test_build_three_state_honesty_without_collector():
    res = facets.build(quota_summary="weekly 42% - resets 3d", branch="brr/x")
    assert res["quota"]["status"] == "known"
    # No level collector wired → spend/context_window read unimplemented.
    assert res["spend"]["status"] == "unimplemented"
    assert res["context_window"]["status"] == "unimplemented"
    assert res["coexisting_runs"]["status"] == "unimplemented"
    # No PR recorded → affirmative-absent, not unimplemented.
    assert res["remote_scm"]["status"] == "absent"


def test_build_level_collector_flips_empty_to_absent_and_known():
    empty = facets.build(levels_collector=True)
    assert empty["spend"]["status"] == "absent"
    assert empty["context_window"]["status"] == "absent"
    assert empty["quota"]["status"] == "absent"

    full = facets.build(
        levels_collector=True,
        levels={
            "quota": {"summary": "5h 58% left"},
            "spend": {"summary": "$0.42 this session"},
            "context_window": {"summary": "62% context left"},
        },
        branch="brr/x",
        pr_number="207",
    )
    assert full["quota"]["status"] == "known"
    assert full["spend"]["summary"] == "$0.42 this session"
    assert full["context_window"]["status"] == "known"
    assert full["remote_scm"]["status"] == "known"
    assert full["remote_scm"]["pr_number"] == "207"


def test_render_line_carries_every_schema_facet_in_order():
    res = facets.build(quota_summary="42%", branch="brr/x")
    line = facets.render_line(res)
    assert line.startswith("- resources: ")
    for spec in facets.FACETS:
        assert f"{spec.label}=" in line
    # Schema order is preserved in the woven line.
    assert line.index("quota=") < line.index("spend=") < line.index("remote-scm=")


def test_facet_value_renders_states():
    assert facets.facet_value({"status": "known", "summary": "42%"}) == "42%"
    assert (
        facets.facet_value({"status": "known", "pr_state": "open",
                            "pr_number": "9"}) == "PR #9"
    )
    assert facets.facet_value(
        {"status": "absent", "note": "no PR yet"}
    ) == "absent (no PR yet)"
    assert facets.facet_value({"status": "unimplemented"}) == "unimplemented"


def test_describe_facets_schema_only_and_with_live_status():
    rows = facets.describe_facets()
    assert [r["key"] for r in rows] == [f.key for f in facets.FACETS]
    assert all(r["fills"] for r in rows)
    assert all(r["status"] is None for r in rows)

    res = facets.build(quota_summary="42%", branch="brr/x")
    live = facets.describe_facets(res)
    quota = next(r for r in live if r["key"] == "quota")
    assert quota["status"] == "known"
    assert quota["value"] == "42%"


# ── runner governance block (step 3, design-runner-cores.md) ─────────────────


def test_build_runner_block_absent_without_runner_name():
    res = facets.build(quota_summary="42%")
    runner = res["runner"]
    assert runner["status"] == "absent"
    assert runner["summary"] is None


def test_build_runner_block_known_with_runner_name():
    res = facets.build(
        quota_summary="42%",
        runner_name="claude",
        runner_meta={
            "model": "claude-sonnet-4-6",
            "class": "balanced",
            "provider": "anthropic",
            "hooks": "claude",
            "cost_rank": 30,
            "capability_score": 0.73,
            "capability_source": "benchmark-cache",
            "capability_freshness": "2026-06-29",
        },
    )
    runner = res["runner"]
    assert runner["status"] == "known"
    assert runner["name"] == "claude"
    assert runner["model"] == "claude-sonnet-4-6"
    assert runner["class"] == "balanced"
    assert runner["provider"] == "anthropic"
    assert runner["hooks"] == "claude"
    assert runner["cost_rank"] == 30
    assert runner["capability_score"] == 0.73
    assert runner["capability_source"] == "benchmark-cache"
    assert runner["capability_freshness"] == "2026-06-29"
    assert runner["summary"] == "claude"


def test_build_runner_block_can_expose_quality_escalation_target():
    res = facets.build(
        runner_name="codex-mini",
        runner_meta={"class": "economy"},
        quality_escalation={
            "status": "known",
            "name": "claude-opus",
            "model": "claude-opus-4-8",
            "class": "strong",
        },
    )

    runner = res["runner"]
    assert runner["quality_escalation"]["name"] == "claude-opus"
    assert runner["quality_escalation"]["class"] == "strong"


def test_build_runner_block_known_with_name_only():
    """runner_meta is optional — name alone is enough for a known block."""
    res = facets.build(runner_name="codex")
    runner = res["runner"]
    assert runner["status"] == "known"
    assert runner["name"] == "codex"
    assert runner["model"] is None
    assert runner["class"] is None


def test_build_runner_block_can_expose_relay_consent():
    """relay_consent carries spending plan details when relay fallback is offered."""
    res = facets.build(
        runner_name="codex-mini",
        runner_meta={"class": "economy", "provider": "openai"},
        relay_consent={
            "status": "pending",
            "reason": "local_quota_exhausted",
            "model": "gpt-5-codex-mini",
            "provider": "openai",
            "provider_cost_usd": "0.10",
            "relay_service_fee_usd": "0.01",
            "total_estimated_cost_usd": "0.11",
            "per_run_cap_usd": "1.00",
            "relay_balance_usd": "5.00",
            "consent_state": "pending",
        },
    )

    runner = res["runner"]
    assert "relay_consent" in runner
    relay = runner["relay_consent"]
    assert relay["status"] == "pending"
    assert relay["reason"] == "local_quota_exhausted"
    assert relay["model"] == "gpt-5-codex-mini"
    assert relay["consent_state"] == "pending"


def test_render_line_does_not_include_runner_block():
    """render_line iterates FACETS (the level/state walls); runner is governance,
    not a wall, so it should NOT appear in the hook injection line."""
    res = facets.build(
        quota_summary="42%",
        runner_name="claude",
        runner_meta={"class": "balanced"},
    )
    line = facets.render_line(res)
    # The hook line should carry the schema facets, not the runner block
    assert "runner=" not in line
    assert "quota=" in line
