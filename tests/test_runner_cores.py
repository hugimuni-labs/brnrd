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
    assert haiku["model"] == "claude-haiku-4-5-20251001"
    assert "--model claude-haiku-4-5-20251001" in haiku["cmd"]


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
    assert sonnet["model"] == "claude-sonnet-4-6"
    assert sonnet["class"] == "balanced"
    assert sonnet["cost_rank"] == 30
    assert sonnet["auth_variant"] == "anthropic-api-key"
    assert sonnet["auth_env"] == "ANTHROPIC_API_KEY"
    assert "--bare" in sonnet["cmd"]
    assert "--model claude-sonnet-4-6" in sonnet["cmd"]


def test_generated_profile_entries_do_not_reintroduce_undeclared_shells():
    profiles = runner_cores.generated_profile_entries({"local-agent": {"cmd": "agent"}})
    assert profiles == {}


def test_generated_profile_entries_preserve_declared_override():
    profiles = runner_cores.generated_profile_entries(
        {
            "claude": {"cmd": "claude --print"},
            "claude-haiku": {"cmd": "custom"},
        }
    )
    assert "claude-haiku" not in profiles


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
