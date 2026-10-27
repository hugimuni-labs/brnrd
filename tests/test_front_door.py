"""Tests for bare ``brnrd`` — the narrated front door.

What these pin, in one sentence each: the empty argv reaches the door and
*only* the empty argv does; the door announces exactly the command it then
runs; it never touches ``input()`` or a mutating verb without a terminal;
and it never redoes a step that is already standing.
"""

from __future__ import annotations

import builtins
from pathlib import Path
import subprocess

import pytest

from brr import account, front_door

from _helpers import init_git_repo


@pytest.fixture(autouse=True)
def _no_stray_input(monkeypatch):
    """A prompt nobody asked for is the failure this suite exists to catch.

    Every test that *wants* a prompt replaces this; the rest inherit a
    hard error, so a step that slipped past its TTY gate fails loudly here
    instead of hanging a CI run on a blocking read.
    """
    def _refuse(*_args, **_kwargs):
        raise AssertionError("the front door asked a question it should not have")

    monkeypatch.setattr(builtins, "input", _refuse)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "proj"
    init_git_repo(root)
    monkeypatch.chdir(root)
    return root


# ── Dispatch ────────────────────────────────────────────────────────


def test_bare_argv_runs_the_loom_guided_setup(monkeypatch):
    from brr.cli import main
    from brr.loom.runtime import setup_channel

    calls = []
    monkeypatch.setattr(front_door, "run", lambda: calls.append("front_door") or 7)
    monkeypatch.setattr(setup_channel, "main", lambda argv: calls.append(argv) or 0)
    assert main([]) == 0
    assert calls == [[]]


# ── The narration is not a lie ──────────────────────────────────────


# ── No terminal, no blocking, no mutation ───────────────────────────


# ── Idempotence ─────────────────────────────────────────────────────


# ── The closing offer ───────────────────────────────────────────────


# ── Memory (front_door._step_memory) ───────────────────────────────
#
# The missing step this closes (measured 2026-08-14): the ladder said
# nothing about where a resident's memory lives, so a second-machine setup
# with an empty, silently-scaffolded home rendered identically to one
# holding years of kb pages and run history. Never blocks setup — it only
# names the home and, honestly, what it holds.


def test_step_memory_renders_the_manifest_line_for_a_populated_home(repo, capsys):
    ctx = account.resolve_context(repo, {})
    (account.work_surface_path(ctx) / "warp").mkdir(parents=True, exist_ok=True)
    (account.work_surface_path(ctx) / "warp" / "w-1.md").write_text(
        "# Ship\n\ntype: action\n", encoding="utf-8",
    )
    kb = account.knowledge_path(ctx)
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "design.md").write_text("# Design", encoding="utf-8")

    result = front_door._step_memory(repo, tty=False)

    out = capsys.readouterr().out
    assert str(account.context_home_root(ctx)) in out
    assert "1 kb page · 1 warp item · 0 topics · 0 run records" in out
    assert result is True


def test_the_manifest_line_agrees_with_itself_about_number(repo, capsys):
    """One page is not "1 kb pages".

    This step's whole job is making a home legible at a glance; a count
    that disagrees with its own noun spends some of the credibility the
    number is there to earn. Pins both sides of the boundary at once so a
    future edit cannot fix one and break the other.
    """
    ctx = account.resolve_context(repo, {})
    topics = account.work_surface_path(ctx) / "topics"
    topics.mkdir(parents=True, exist_ok=True)
    for name in ("a.md", "b.md"):
        (topics / name).write_text("# t", encoding="utf-8")
    kb = account.knowledge_path(ctx)
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "design.md").write_text("# Design", encoding="utf-8")

    front_door._step_memory(repo, tty=False)

    out = capsys.readouterr().out
    assert "1 kb page ·" in out and "1 kb pages" not in out
    assert "2 topics" in out
    assert "0 warp items" in out  # zero takes the plural, as English does


def test_step_memory_notes_local_only_on_a_non_tty(repo, capsys):
    ctx = account.resolve_context(repo, {})
    (account.knowledge_path(ctx) / "index.md").parent.mkdir(parents=True, exist_ok=True)
    (account.knowledge_path(ctx) / "index.md").write_text("# kb", encoding="utf-8")

    result = front_door._step_memory(repo, tty=False)

    out = capsys.readouterr().out
    assert "local-only" in out
    assert "brnrd home link" in out
    assert result is True


def test_step_memory_skips_the_local_only_note_when_fully_linked(repo, capsys, tmp_path):
    """`fully_linked` requires an actual push, not just a wired origin
    (#1422) — so this positive case needs a remote that can genuinely
    receive one, not a fake unreachable GitHub URL."""
    ctx = account.resolve_context(repo, {})
    (account.knowledge_path(ctx)).mkdir(parents=True, exist_ok=True)
    (account.knowledge_path(ctx) / "index.md").write_text("# kb", encoding="utf-8")

    def _push_to_a_real_bare_remote(repo_root: Path, name: str) -> None:
        remote = tmp_path / f"{name}.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo_root, check=True)
        subprocess.run(
            ["git", "push", "-u", "origin", "HEAD:refs/heads/main"],
            cwd=repo_root, check=True, capture_output=True,
        )

    # dominion already carries the founding commit `resolve_context` seeds.
    _push_to_a_real_bare_remote(account.context_home_root(ctx), "dominion-remote")
    # the (separate, independently-linked) knowledge repo needs its own.
    init_git_repo(account.knowledge_path(ctx))
    subprocess.run(
        ["git", "add", "-A"], cwd=account.knowledge_path(ctx), check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "founding"], cwd=account.knowledge_path(ctx), check=True,
    )
    _push_to_a_real_bare_remote(account.knowledge_path(ctx), "knowledge-remote")

    result = front_door._step_memory(repo, tty=False)

    out = capsys.readouterr().out
    assert "local-only" not in out
    assert result is True


def test_step_memory_reports_local_only_without_reasking_on_a_tty(repo, capsys, monkeypatch):
    """Durability consent belongs to account connect; this is a receipt."""
    ctx = account.resolve_context(repo, {})
    kb = account.knowledge_path(ctx)
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "design.md").write_text("# Design", encoding="utf-8")
    monkeypatch.setattr(builtins, "input", lambda _prompt: pytest.fail("memory receipt re-asked consent"))
    monkeypatch.setattr(front_door, "_invoke", lambda argv: pytest.fail(f"memory receipt ran {argv}"))

    result = front_door._step_memory(repo, tty=True)

    out = capsys.readouterr().out
    assert "local-only" in out
    assert "$ brnrd home link" in out
    assert result is True


def test_step_memory_resume_instruction_changes_nothing(repo, capsys, monkeypatch):
    ctx = account.resolve_context(repo, {})
    kb = account.knowledge_path(ctx)
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "design.md").write_text("# Design", encoding="utf-8")
    monkeypatch.setattr(builtins, "input", lambda _prompt: pytest.fail("memory receipt asked again"))
    monkeypatch.setattr(
        front_door, "_invoke",
        lambda argv: pytest.fail(f"ran {argv} on a declined offer"),
    )

    result = front_door._step_memory(repo, tty=True)

    out = capsys.readouterr().out
    assert "resume the backup whenever you like" in out
    assert result is True


