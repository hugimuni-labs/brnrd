"""Tests for runner_cores — Task 2B dynamic Core registry."""

import pytest

from brr import runner_cores
from brr.runner_select import RunnerProfile


def test_all_cores_returns_non_empty_dict():
    cores = runner_cores.all_cores()
    assert isinstance(cores, dict)
    assert len(cores) > 0


def test_all_cores_entries_have_required_fields():
    for name, entry in runner_cores.all_cores().items():
        assert "shell" in entry, f"{name} missing 'shell'"
        assert "model" in entry, f"{name} missing 'model'"
        assert "class" in entry, f"{name} missing 'class'"
        assert entry["class"] in ("economy", "balanced", "strong"), (
            f"{name} has unknown class {entry['class']!r}"
        )
        assert "freshness_date" in entry, f"{name} missing 'freshness_date'"


def test_available_cores_returns_profiles_when_shell_on_path(monkeypatch):
    monkeypatch.setattr(runner_cores.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name == "claude" else None)
    profiles = runner_cores.available_cores()
    names = [p.name for p in profiles]
    # Claude cores should be in the list; codex/gemini should not.
    assert any("claude" in n for n in names)
    assert all("codex" not in n and "gemini" not in n for n in names)


def test_available_cores_returns_empty_when_no_shell(monkeypatch):
    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: None)
    assert runner_cores.available_cores() == []


def test_available_cores_sorted_cheapest_first(monkeypatch):
    monkeypatch.setattr(runner_cores.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name == "claude" else None)
    profiles = runner_cores.available_cores()
    ranks = [p.rank for p in profiles]
    assert ranks == sorted(ranks), "Profiles should be sorted cheapest first"


def test_available_cores_extra_overrides_bundled(monkeypatch):
    monkeypatch.setattr(runner_cores.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    extra = {
        "claude-sonnet": {
            "shell": "claude",
            "model": "claude-sonnet-99",  # override the bundled model
            "class": "balanced",
            "cost_rank": 30,
            "freshness_date": "2099-01-01",
        }
    }
    profiles = runner_cores.available_cores(extra=extra)
    sonnet = next((p for p in profiles if p.name == "claude-sonnet"), None)
    assert sonnet is not None
    assert sonnet.model == "claude-sonnet-99"


def test_available_cores_extra_adds_new_entry(monkeypatch):
    monkeypatch.setattr(runner_cores.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name == "claude" else None)
    extra = {
        "claude-preview": {
            "shell": "claude",
            "model": "claude-preview-x",
            "class": "strong",
            "cost_rank": 99,
            "freshness_date": "2099-01-01",
        }
    }
    profiles = runner_cores.available_cores(extra=extra)
    assert any(p.name == "claude-preview" for p in profiles)


def test_available_cores_adds_cli_probed_models(monkeypatch):
    monkeypatch.setattr(runner_cores.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name == "codex" else None)
    monkeypatch.setattr(
        runner_cores,
        "probe_shell_models",
        lambda shell: ("gpt-new-9",) if shell == "codex" else (),
    )

    profiles = runner_cores.available_cores()

    probed = next((p for p in profiles if p.model == "gpt-new-9"), None)
    assert probed is not None
    assert probed.name == "codex-gpt-new-9"
    assert probed.provider == "openai"


def test_cores_for_shell_returns_correct_subset():
    claude_cores = runner_cores.cores_for_shell("claude")
    assert all(p.profile == "claude" for p in claude_cores)
    assert len(claude_cores) > 0

    codex_cores = runner_cores.cores_for_shell("codex")
    assert all(p.profile == "codex" for p in codex_cores)


def test_cores_for_shell_empty_for_unknown():
    unknown = runner_cores.cores_for_shell("unknown-shell-xyz")
    assert unknown == []


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


def test_bundled_claude_entries_use_aliases():
    """Claude entries in the bundled registry should use short aliases, not exact IDs."""
    cores = runner_cores.all_cores()
    claude_aliases = {"haiku", "sonnet", "opus", "fable"}
    for name, entry in cores.items():
        if entry.get("shell") == "claude":
            assert entry["model"] in claude_aliases, (
                f"{name}: expected a short alias, got {entry['model']!r}"
            )


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


def test_effective_model_returns_pin_when_set():
    assert runner_cores.effective_model({"model": "sonnet", "pin": "claude-sonnet-4-6"}) == "claude-sonnet-4-6"


def test_effective_model_returns_model_when_no_pin():
    assert runner_cores.effective_model({"model": "haiku"}) == "haiku"


def test_stale_entries_flags_old_entries():
    import datetime

    registry = {
        "fresh": {"shell": "x", "model": "m", "freshness_date": "2026-07-19"},
        "stale-one": {"shell": "x", "model": "m2", "freshness_date": "2026-01-01"},
        "no-date": {"shell": "x", "model": "m3"},
    }
    now = datetime.date(2026, 7, 20)
    result = runner_cores.stale_entries(registry, now=now, threshold_days=30)
    assert "stale-one" in result
    assert "fresh" not in result
    assert "no-date" not in result  # missing date → not flagged as stale


def test_stale_entries_accepts_string_date():
    registry = {"old": {"freshness_date": "2025-01-01"}}
    result = runner_cores.stale_entries(registry, now="2026-07-20", threshold_days=30)
    assert "old" in result


def test_stale_entries_uses_today_by_default():
    import datetime

    today = datetime.date.today()
    long_ago = (today - datetime.timedelta(days=60)).isoformat()
    recently = (today - datetime.timedelta(days=5)).isoformat()
    registry = {
        "old": {"freshness_date": long_ago},
        "new": {"freshness_date": recently},
    }
    result = runner_cores.stale_entries(registry)
    assert "old" in result
    assert "new" not in result


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
