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


def test_metadata_for_model_omits_empty_score_but_keeps_provenance(monkeypatch):
    monkeypatch.setattr(
        caps,
        "load_capabilities",
        lambda: {
            "no-score-model": caps.CapabilityHint(
                "no-score-model", source="test fixture", freshness_date="2026-06-29",
            ),
        },
    )
    meta = caps.metadata_for_model("no-score-model")
    assert "capability_score" not in meta
    assert meta["capability_freshness"] == "2026-06-29"


def test_metadata_for_model_reads_populated_score():
    meta = caps.metadata_for_model("gpt-5-codex")
    assert meta["capability_score"] == 0.443
    assert "Terminal-Bench 2.0 verified row" in meta["capability_source"]
    assert meta["capability_freshness"] == "2026-06-29"


def test_web_research_declared_for_claude_shell():
    cap = caps.web_research_for_shell("claude")
    assert cap is not None
    assert cap.native is True
    assert cap.tools == ("WebSearch", "WebFetch")
    assert cap.execution == "server-side"


def test_web_research_declared_for_codex_shell():
    cap = caps.web_research_for_shell("codex")
    assert cap is not None
    assert cap.native is True
    assert cap.tools == ("web.run",)
    assert cap.default_on is True
    assert cap.execution == "server-side"


def test_web_research_undeclared_for_unknown_or_missing_shell():
    assert caps.web_research_for_shell("gemini") is None
    assert caps.web_research_for_shell("my-custom-cli") is None
    assert caps.web_research_for_shell(None) is None
    assert caps.web_research_for_shell("  ") is None


def test_web_research_shell_lookup_is_case_insensitive():
    assert caps.web_research_for_shell("Claude") is not None
