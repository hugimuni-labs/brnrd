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
