"""Tests for cached runner capability hints."""

import json

import pytest

from brr import runner_capabilities as caps


def _clear_caches():
    # getattr-guard: a test may have monkeypatched a loader with a plain
    # function, and teardown runs before monkeypatch undo.
    for fn in (caps._load_raw, caps.load_capabilities, caps.load_shell_capabilities):
        clear = getattr(fn, "cache_clear", None)
        if clear:
            clear()


@pytest.fixture(autouse=True)
def _isolated_state_root(tmp_path, monkeypatch):
    """Point the loader at an empty tmp state root and reset all caches.

    Keeps every test hermetic: a real overlay on the host machine must never
    leak into packaged-floor assertions.
    """
    state_root = tmp_path / "state" / "brnrd"
    monkeypatch.setattr(caps, "_state_root", lambda: state_root)
    _clear_caches()
    yield state_root
    _clear_caches()


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


# --- state-root overlay (#535) -------------------------------------------


def _write_overlay(state_root, payload):
    state_root.mkdir(parents=True, exist_ok=True)
    path = state_root / "runner-capabilities.json"
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    _clear_caches()
    return path


def test_overlay_overrides_packaged_model_entry(_isolated_state_root):
    packaged = caps.capability_for_model("gpt-5-codex")
    assert packaged is not None and packaged.score < 0.75
    _write_overlay(
        _isolated_state_root,
        {
            "source": "overlay fixture",
            "freshness_date": "2026-07-22",
            "models": {"gpt-5-codex": {"swe_bench_verified": 90.0}},
        },
    )
    hint = caps.capability_for_model("gpt-5-codex")
    assert hint is not None
    assert hint.score == 0.9
    assert caps.derived_cost_class("gpt-5-codex") == "strong"
    # Entry-level override: the packaged entry is replaced wholly, so its
    # terminal_bench and per-entry provenance do not bleed through.
    assert hint.terminal_bench is None
    assert hint.source == "overlay fixture"
    assert hint.freshness_date == "2026-07-22"


def test_overlay_adds_model_unknown_to_packaged_data(_isolated_state_root):
    assert caps.capability_for_model("brand-new-model") is None
    _write_overlay(
        _isolated_state_root,
        {"models": {"brand-new-model": {"terminal_bench": 0.5}}},
    )
    hint = caps.capability_for_model("brand-new-model")
    assert hint is not None
    assert hint.score == 0.5
    # Packaged entries without an overlay counterpart survive untouched.
    assert caps.capability_for_model("gpt-5-codex") is not None


def test_absent_overlay_yields_packaged_floor_exactly():
    assert caps._load_raw() == caps._load_packaged()
    assert set(caps.load_capabilities()) == set(
        (caps._load_packaged().get("models") or {})
    )


def test_malformed_overlay_warns_and_uses_packaged_floor(
    _isolated_state_root, capsys
):
    _write_overlay(_isolated_state_root, "{not json")
    table = caps.load_capabilities()
    err = capsys.readouterr().err
    assert "warning" in err and "overlay" in err
    assert err.count("\n") == 1  # one warning line, not one per loader
    assert set(table) == set(caps._load_packaged().get("models") or {})
    # Never partial-apply: shells fall back to the packaged floor too.
    assert caps.web_research_for_shell("claude") is not None


def test_non_object_overlay_warns_and_uses_packaged_floor(
    _isolated_state_root, capsys
):
    _write_overlay(_isolated_state_root, ["not", "a", "mapping"])
    assert caps._load_raw() == caps._load_packaged()
    assert "warning" in capsys.readouterr().err


def test_repo_dot_brr_overlay_is_never_consulted(tmp_path, monkeypatch):
    # #533 trust split: a repo-writable .brr/ file must not steer the daemon's
    # view of its Shells, no matter what the current working directory is.
    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".brr" / "runner-capabilities.json").write_text(
        json.dumps({"models": {"gpt-5-codex": {"swe_bench_verified": 99.0}}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    _clear_caches()
    assert ".brr" not in str(caps._overlay_path())
    hint = caps.capability_for_model("gpt-5-codex")
    assert hint is not None
    assert hint.score != 0.99  # packaged floor, not the planted file


def test_overlay_overrides_shell_entry(_isolated_state_root):
    _write_overlay(
        _isolated_state_root,
        {
            "shells": {
                "gemini": {
                    "web_research": {
                        "native": True,
                        "tools": ["google_web_search"],
                        "execution": "server-side",
                    }
                }
            }
        },
    )
    cap = caps.web_research_for_shell("gemini")
    assert cap is not None
    assert cap.tools == ("google_web_search",)
    # Packaged shells survive alongside the overlay-added one.
    assert caps.web_research_for_shell("claude") is not None
