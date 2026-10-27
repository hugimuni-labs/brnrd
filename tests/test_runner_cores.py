"""Tests for runner_cores — Task 2B dynamic Core registry."""

import pytest

from brr import runner_cores
from brr.runner_select import RunnerProfile


def test_available_cores_are_runner_profiles(monkeypatch):
    monkeypatch.setattr(runner_cores.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    profiles = runner_cores.available_cores()
    for p in profiles:
        assert isinstance(p, RunnerProfile)
        assert p.model is not None


def test_generated_profile_entries_derive_invokable_profiles_from_base_shell():
    profiles = runner_cores.generated_profile_entries(
        {
            "claude": {
                "cmd": "claude --print --output-format json",
                "hooks": "claude",
                "quota_source": "claude-local",
            }
        }
    )

    haiku = profiles["claude-haiku"]
    assert haiku["binary"] == "claude"
    assert haiku["hooks"] == "claude"
    assert haiku["quota_source"] == "claude-local"
    # Alias-first: model field holds the short alias; --model flag uses alias too.
    assert haiku["model"] == "haiku"
    assert "--model haiku" in haiku["cmd"]


def test_generated_profile_entries_materialize_auth_variant_from_core_registry():
    profiles = runner_cores.generated_profile_entries(
        {
            "claude": {"cmd": "claude --print", "hooks": "claude"},
            "claude-bare-api-only": {
                "binary": "claude",
                "shell": "claude",
                "cmd": "claude --print --bare",
                "auth_variant": "anthropic-api-key",
                "auth_env": "ANTHROPIC_API_KEY",
            },
        }
    )

    sonnet = profiles["claude-bare-api-only-sonnet"]
    assert sonnet["binary"] == "claude"
    assert sonnet["shell"] == "claude"
    # Alias-first: model holds the alias; --model uses the alias (no pin set).
    assert sonnet["model"] == "sonnet"
    assert sonnet["class"] == "balanced"
    assert sonnet["cost_rank"] == 30
    assert sonnet["auth_variant"] == "anthropic-api-key"
    assert sonnet["auth_env"] == "ANTHROPIC_API_KEY"
    assert "--bare" in sonnet["cmd"]
    assert "--model sonnet" in sonnet["cmd"]


def test_generated_profile_entries_do_not_reintroduce_undeclared_shells():
    profiles = runner_cores.generated_profile_entries({"local-agent": {"cmd": "agent"}})
    assert profiles == {}


def test_generated_profile_entries_emit_twin_for_declared_name():
    """A declared name no longer suppresses its registry twin.

    Declared profiles stay authoritative *per field*: the caller
    (``runner._selection_profiles``) overlays declared fields on the twin,
    so a declaration pinning only ``cmd`` inherits model/class/cost metadata
    instead of rendering as ``core=default`` in the Runner catalog.
    """
    profiles = runner_cores.generated_profile_entries(
        {
            "claude": {"cmd": "claude --print"},
            "claude-haiku": {"cmd": "custom"},
        }
    )
    twin = profiles["claude-haiku"]
    assert twin["model"] == "haiku"  # alias-first
    assert twin["class"] == "economy"


def test_generated_profile_entries_materialize_cli_probed_model(monkeypatch):
    monkeypatch.setattr(
        runner_cores,
        "probe_shell_models",
        lambda shell: ("claude-preview-9",) if shell == "claude" else (),
    )

    profiles = runner_cores.generated_profile_entries(
        {"claude": {"cmd": "claude --print", "hooks": "claude"}}
    )

    generated = profiles["claude-claude-preview-9"]
    assert generated["model"] == "claude-preview-9"
    assert generated["hooks"] == "claude"
    assert "--model claude-preview-9" in generated["cmd"]
    assert generated["freshness_source"] == "cli-help"


def test_probe_shell_models_parses_model_help(monkeypatch):
    class _Proc:
        stdout = "  --model <MODEL>  choices: gpt-5-codex, gpt-5.4-mini\n"
        stderr = ""

    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner_cores.subprocess, "run", lambda *a, **k: _Proc())
    runner_cores.probe_shell_models.cache_clear()

    assert runner_cores.probe_shell_models("codex") == ("gpt-5-codex", "gpt-5.4-mini")


def test_probe_shell_models_reads_codex_models_cache(tmp_path, monkeypatch):
    """$CODEX_HOME/models_cache.json is the primary codex discovery source."""
    import json as _json

    cache = {
        "models": [
            {"slug": "gpt-9.9-nova", "visibility": "list"},
            {"slug": "codex-auto-review", "visibility": "hide"},
            {"slug": "gpt-9.9-mini", "visibility": "list"},
        ]
    }
    (tmp_path / "models_cache.json").write_text(_json.dumps(cache))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: f"/usr/bin/{name}")

    class _Proc:
        stdout = ""
        stderr = ""

    monkeypatch.setattr(runner_cores.subprocess, "run", lambda *a, **k: _Proc())
    runner_cores.probe_shell_models.cache_clear()

    assert runner_cores.probe_shell_models("codex") == ("gpt-9.9-nova", "gpt-9.9-mini")


def test_probe_shell_models_tolerates_malformed_models_cache(tmp_path, monkeypatch):
    (tmp_path / "models_cache.json").write_text("{not json")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: f"/usr/bin/{name}")

    class _Proc:
        stdout = ""
        stderr = ""

    monkeypatch.setattr(runner_cores.subprocess, "run", lambda *a, **k: _Proc())
    runner_cores.probe_shell_models.cache_clear()

    assert runner_cores.probe_shell_models("codex") == ()


def test_probe_shell_models_self_heals_on_path_flip_without_cache_clear(monkeypatch):
    """#1519: a shell installed *after* a negative probe must be picked up
    on the very next call — no ``cache_clear()``, no daemon restart.

    Before the fix, ``probe_shell_models`` was one ``lru_cache``-wrapped
    function whose own ``shutil.which`` check sat *behind* the cache: a
    shell probed while absent memoized ``()`` for the rest of the process's
    life, and nothing — including the shell later appearing on PATH — ever
    invalidated it. This pins the actual bug (a stale *negative*, not staleness
    in general) rather than the cache-clearing behaviour the other probe tests
    already cover.
    """
    runner_cores.probe_shell_models.cache_clear()

    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: None)
    assert runner_cores.probe_shell_models("codex") == ()

    class _Proc:
        stdout = "  --model <MODEL>  choices: gpt-5-codex, gpt-5.4-mini\n"
        stderr = ""

    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner_cores.subprocess, "run", lambda *a, **k: _Proc())

    # No cache_clear() call here — this is the regression the fix closes.
    assert runner_cores.probe_shell_models("codex") == ("gpt-5-codex", "gpt-5.4-mini")


def test_probe_shell_models_sees_a_disk_feed_rewrite_without_cache_clear(
    tmp_path, monkeypatch
):
    """A model appearing in ``models_cache.json`` mid-process is picked up on
    the very next call — no ``cache_clear()``, no subprocess re-probe, no
    restart. Regression for the bug fixed by moving ``_models_from_disk`` out
    of ``_probe_shell_models_cached``'s ``lru_cache`` (see that function's
    docstring); same shape as #1519, one layer up.
    """
    import json as _json

    cache_path = tmp_path / "models_cache.json"
    cache_path.write_text(_json.dumps({"models": [{"slug": "gpt-9.9-nova"}]}))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: f"/usr/bin/{name}")

    calls = {"n": 0}

    class _Proc:
        stdout = ""
        stderr = ""

    def _counting_run(*a, **k):
        calls["n"] += 1
        return _Proc()

    monkeypatch.setattr(runner_cores.subprocess, "run", _counting_run)
    runner_cores.probe_shell_models.cache_clear()

    assert runner_cores.probe_shell_models("codex") == ("gpt-9.9-nova",)
    calls_after_first = calls["n"]

    # Rewrite the feed with a previously-unseen model, same process, no
    # cache_clear() — the regression this test pins.
    cache_path.write_text(
        _json.dumps({"models": [{"slug": "gpt-9.9-nova"}, {"slug": "gpt-9.9-astra"}]})
    )

    assert runner_cores.probe_shell_models("codex") == (
        "gpt-9.9-nova",
        "gpt-9.9-astra",
    )
    # The expensive half (the subprocess spawn) must stay memoized — only the
    # cheap disk read is live.
    assert calls["n"] == calls_after_first


def test_codex_feed_omission_keeps_last_known_core_during_grace(tmp_path, monkeypatch):
    """One valid feed omission is not a negative availability probe."""
    import json as _json

    cache_path = tmp_path / "models_cache.json"
    cache_path.write_text(_json.dumps({"models": [{"slug": "gpt-9.9-nova"}]}))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(runner_cores.time, "time", lambda: 1_000.0)

    first = runner_cores._codex_feed_entries()
    assert first["gpt-9.9-nova"]["feed_state"] == "working"

    cache_path.write_text(_json.dumps({"models": []}))
    retained = runner_cores._codex_feed_entries()
    assert retained["gpt-9.9-nova"]["feed_state"] == "last-known"
    assert retained["gpt-9.9-nova"]["feed_age_seconds"] == 0


def test_codex_feed_removal_expires_last_known_core(tmp_path, monkeypatch):
    """Sustained removal expires the row instead of claiming it still works."""
    import json as _json

    cache_path = tmp_path / "models_cache.json"
    cache_path.write_text(_json.dumps({"models": [{"slug": "gpt-9.9-nova"}]}))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    clock = {"now": 1_000.0}
    monkeypatch.setattr(runner_cores.time, "time", lambda: clock["now"])
    runner_cores._codex_feed_entries()

    clock["now"] += runner_cores.CODEX_FEED_GRACE_SECONDS + 1
    cache_path.write_text(_json.dumps({"models": []}))
    expired = runner_cores._codex_feed_entries()
    assert expired["gpt-9.9-nova"]["feed_state"] == "expired"
    assert expired["gpt-9.9-nova"]["feed_age_seconds"] > runner_cores.CODEX_FEED_GRACE_SECONDS


def test_generated_profile_entries_derive_class_when_missing(monkeypatch):
    monkeypatch.setattr(
        runner_cores,
        "_BUNDLED_CORES",
        {
            "claude-preview": {
                "shell": "claude",
                "model": "claude-preview-x",
                "cost_rank": 42,
                "freshness_date": "2099-01-01",
            }
        },
    )
    monkeypatch.setattr(
        runner_cores.runner_capabilities,
        "derived_cost_class",
        lambda model: "strong" if model == "claude-preview-x" else None,
    )
    monkeypatch.setattr(
        runner_cores.runner_capabilities,
        "metadata_for_model",
        lambda model: {
            "capability_score": 0.91,
            "capability_source": "test-cache",
            "capability_freshness": "2099-01-01",
        },
    )

    profiles = runner_cores.generated_profile_entries(
        {"claude": {"cmd": "claude --print"}}
    )

    preview = profiles["claude-preview"]
    assert preview["class"] == "strong"
    assert preview["capability_score"] == 0.91
    assert preview["capability_source"] == "test-cache"


def test_generated_profile_entries_keep_hand_set_class(monkeypatch):
    monkeypatch.setattr(
        runner_cores,
        "_BUNDLED_CORES",
        {
            "claude-preview": {
                "shell": "claude",
                "model": "claude-preview-x",
                "class": "economy",
                "cost_rank": 42,
                "freshness_date": "2099-01-01",
            }
        },
    )
    monkeypatch.setattr(
        runner_cores.runner_capabilities,
        "derived_cost_class",
        lambda model: "strong",
    )

    profiles = runner_cores.generated_profile_entries(
        {"claude": {"cmd": "claude --print"}}
    )

    assert profiles["claude-preview"]["class"] == "economy"


# ── New: alias-first + pin, stale_entries, dedupe ───────────────────────────


def test_pin_overrides_alias_in_generated_cmd():
    """When a bundled entry has a 'pin' field, the --model flag uses the pin, not the alias."""
    import copy

    cores_with_pin = copy.deepcopy(runner_cores._BUNDLED_CORES)
    cores_with_pin["claude-sonnet"]["pin"] = "claude-sonnet-4-6"

    import types
    import brr.runner_cores as rc_mod

    orig = rc_mod._BUNDLED_CORES
    try:
        rc_mod._BUNDLED_CORES = cores_with_pin
        profiles = runner_cores.generated_profile_entries(
            {"claude": {"cmd": "claude --print"}}
        )
    finally:
        rc_mod._BUNDLED_CORES = orig

    sonnet = profiles["claude-sonnet"]
    assert sonnet["model"] == "sonnet"              # alias stays in model field
    assert sonnet["pin"] == "claude-sonnet-4-6"     # pin is preserved
    assert "--model claude-sonnet-4-6" in sonnet["cmd"]  # pin used for cmd


# ── Freshness classification: alias-tracked vs pinned ────────────────────────


# ── #503: probe fabrication is bundled-shells-only ─────────────────────────


def test_bundled_shells_set():
    assert runner_cores.BUNDLED_SHELLS == frozenset({"claude", "codex"})


def test_probe_fabrication_skipped_for_declared_custom_shell(monkeypatch):
    """A custom declared profile gets no fabricated `<name>-<model>` twins.

    Before #503 the probe spliced `--model X` into a custom cmd and
    auto-selection preferred the fabricated variant over the profile the
    user actually declared.
    """
    monkeypatch.setattr(
        runner_cores, "probe_shell_models", lambda shell: ("mymodel-9",)
    )

    profiles = runner_cores.generated_profile_entries(
        {"mycli": {"cmd": "mycli --go"}}
    )

    assert not any(name.startswith("mycli") for name in profiles)


def test_probe_fabrication_for_declared_custom_shell_with_opt_in(monkeypatch):
    monkeypatch.setattr(
        runner_cores, "probe_shell_models", lambda shell: ("mymodel-9",)
    )

    profiles = runner_cores.generated_profile_entries(
        {"mycli": {"cmd": "mycli --go", "probe_models": "true"}}
    )

    generated = profiles["mycli-mymodel-9"]
    assert generated["model"] == "mymodel-9"
    assert "--model mymodel-9" in generated["cmd"]
    assert generated["freshness_source"] == "cli-help"


def test_probe_fabrication_opt_in_false_spelling_stays_off(monkeypatch):
    monkeypatch.setattr(
        runner_cores, "probe_shell_models", lambda shell: ("mymodel-9",)
    )

    profiles = runner_cores.generated_profile_entries(
        {"mycli": {"cmd": "mycli --go", "probe_models": "false"}}
    )

    assert not any(name.startswith("mycli") for name in profiles)


def test_probe_fabrication_still_runs_for_bundled_shell(monkeypatch):
    """No behavior change for bundled shells: claude keeps probing."""
    monkeypatch.setattr(
        runner_cores,
        "probe_shell_models",
        lambda shell: ("claude-preview-9",) if shell == "claude" else (),
    )

    profiles = runner_cores.generated_profile_entries(
        {"claude": {"cmd": "claude --print"}}
    )

    assert "claude-claude-preview-9" in profiles


def test_probe_eligible_shells_mixed_catalog():
    declared = {
        "claude": {"cmd": "claude --print"},
        "gemini": {"cmd": "gemini"},
        "mycli": {"cmd": "mycli --go"},
        "othercli": {"cmd": "othercli", "probe_models": True},
    }
    assert runner_cores._probe_eligible_shells(declared) == {
        "claude",
        "othercli",
    }


def test_declared_gemini_profile_remains_custom_without_implicit_probing(monkeypatch):
    monkeypatch.setattr(
        runner_cores, "probe_shell_models", lambda shell: ("gemini-private",)
    )

    profiles = runner_cores.generated_profile_entries(
        {"gemini": {"cmd": "gemini --private"}}
    )

    assert not any(name.startswith("gemini") for name in profiles)


def test_feed_priority_is_placement_not_price(tmp_path, monkeypatch):
    """The codex feed's `priority` (1 = the flagship it shows first) used to
    be copied straight into `cost_rank`, so GPT-6-Astra sorted to the top of
    the rack as the cheapest core with no tier at all (2026-09-09, "the
    order feels random"). The tier comes from the vendor's own sentence; the
    rank lands inside that tier's band, flagship most expensive."""
    import json as _json

    cache = {
        "models": [
            {"slug": "gpt-9-astra", "visibility": "list", "priority": 1,
             "display_name": "GPT-9-Astra",
             "description": "Our most capable model for complex, demanding work."},
            {"slug": "gpt-8.6-luna", "visibility": "list", "priority": 8,
             "description": "Fast, lightweight responses."},
            {"slug": "gpt-8.6-terra", "visibility": "list", "priority": 7,
             "description": "A solid everyday model."},
            {"slug": "gpt-8.5", "visibility": "list", "priority": 12},
        ]
    }
    (tmp_path / "models_cache.json").write_text(_json.dumps(cache))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: f"/usr/bin/{name}")

    class _Proc:
        stdout = ""
        stderr = ""

    monkeypatch.setattr(runner_cores.subprocess, "run", lambda *a, **k: _Proc())
    monkeypatch.setattr(runner_cores.runner_capabilities, "derived_cost_class", lambda model: None)
    runner_cores.probe_shell_models.cache_clear()
    for fn in ("_codex_cache_payload", "_codex_disk_entries", "_codex_cache_path"):
        cached = getattr(runner_cores, fn, None)
        if hasattr(cached, "cache_clear"):
            cached.cache_clear()

    profiles = runner_cores.generated_profile_entries({"codex": {"cmd": "codex exec", "hooks": "codex"}})
    astra = profiles["codex-gpt-9-astra"]
    luna = profiles["codex-gpt-8.6-luna"]
    terra = profiles["codex-gpt-8.6-terra"]
    bare = profiles["codex-gpt-8.5"]
    assert (astra["class"], luna["class"], terra["class"]) == ("strong", "economy", "balanced")
    assert astra["vendor_priority"] == 1
    # bands: economy < balanced < strong; the flagship is the dearest seat
    assert luna["cost_rank"] < terra["cost_rank"] < astra["cost_rank"]
    assert astra["cost_rank"] > 45  # dearer than a hand-ranked `full` (45)
    # no words ⇒ no tier, no rank: last place, never a guess
    assert bare["class"] is None and bare["cost_rank"] is None
