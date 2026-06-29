"""Tests for cached runner capability hints."""

from brr import runner_capabilities as caps


def test_load_capabilities_reads_packaged_cache():
    table = caps.load_capabilities()
    assert "gpt-5-codex" in table
    assert table["gpt-5-codex"].freshness_date == "2026-06-29"


def test_hint_score_averages_normalized_benchmarks():
    hint = caps.CapabilityHint(
        model="m",
        swe_bench_verified=80.0,
        terminal_bench=0.6,
    )
    assert hint.score == 0.7


def test_derived_cost_class_from_cached_score():
    table = {
        "cheap-model": caps.CapabilityHint("cheap-model", swe_bench_verified=0.2),
        "balanced-model": caps.CapabilityHint("balanced-model", terminal_bench=0.6),
        "strong-model": caps.CapabilityHint("strong-model", swe_bench_verified=0.9),
    }
    assert caps.derived_cost_class("cheap-model", table=table) == "economy"
    assert caps.derived_cost_class("balanced-model", table=table) == "balanced"
    assert caps.derived_cost_class("strong-model", table=table) == "strong"


def test_missing_scores_do_not_invent_class():
    table = {"unknown": caps.CapabilityHint("unknown")}
    assert caps.derived_cost_class("unknown", table=table) is None


def test_metadata_for_model_omits_empty_score_but_keeps_provenance():
    meta = caps.metadata_for_model("gemini-2.0-flash")
    assert "capability_score" not in meta
    assert meta["capability_freshness"] == "2026-06-29"


def test_metadata_for_model_reads_populated_score():
    meta = caps.metadata_for_model("gpt-5-codex")
    assert meta["capability_score"] == 0.443
    assert "Terminal-Bench 2.0 verified row" in meta["capability_source"]
    assert meta["capability_freshness"] == "2026-06-29"
