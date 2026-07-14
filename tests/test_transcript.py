"""Slice 4 — the boot as a transcript.

The thesis under test: a wake's opening is not just *content* at a position, it
is **grammar** at a position. A ``tool_result`` is the one turn type whose
natural continuation is an action, and the boot has never been able to use it,
because the whole 73 KB arrives as a single user message.

The most important test in this file is the one that says the boot may not lie.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from brr import transcript as tx
from brr.bootscore import BootScore, ContractEntry
from _helpers import init_git_repo


def _entry(key: str, location: str, *, present: bool = True, size: int | None = None):
    return ContractEntry(
        block_key=key,
        label=key,
        owner="product",
        authority="contract",
        freshness=None,
        location=location,
        present=present,
        bytes=size,
    )


# ── The safety rule ───────────────────────────────────────────────────────────


def test_the_boot_may_not_synthesize_an_action():
    """Perception may be seeded. Action may never be.

    A seeded ``Read`` is honest — the bytes really are in the wake's context, and
    that is the only sense in which any agent has read anything. A seeded
    ``Write`` is a forgery: the file was not written, and a resident that finds
    ``Write(.card)`` in what looks like its own history will believe the card
    exists and never write one. Silent, and *caused by the boot* — the exact bug
    class this whole line of work exists to kill.

    An earlier plan for this slice proposed seeding ``.card written`` among the
    orientation turns. This test is why that plan is not in the code.
    """
    for forged in ("Write", "Edit", "Bash", "TodoWrite"):
        with pytest.raises(tx.ForgedActionError, match="never action"):
            tx.ToolCall(tool=forged, input={}, result="done")

    tx.ToolCall(tool="Read", input={"file_path": "/AGENTS.md"}, result="…")


def test_replayable_tools_are_side_effect_free():
    """Whatever is in the set must be a tool that changes nothing by running."""
    assert tx.REPLAYABLE_TOOLS == {"Read"}


# ── Mounting the IR ───────────────────────────────────────────────────────────


def test_computed_blocks_stay_prose(tmp_path):
    """Live state has no honest ``Read``.

    The kernel, the run bundle, portal posture — these exist nowhere on disk. A
    ``Read`` returning them would be fiction, so ``location == "computed"`` is
    the principle that keeps them out. It also means a block added next year
    classifies *itself*, instead of waiting on someone to remember a list.
    """
    f = tmp_path / "AGENTS.md"
    f.write_text("the contract\n", encoding="utf-8")

    score = BootScore(contracts=[
        _entry("agents", str(f), size=len("the contract\n")),
        _entry("kernel", tx.COMPUTED),
        _entry("run-bundle", tx.COMPUTED),
    ])
    t = tx.build_orientation_transcript(
        score, block_text={"agents": "the contract\n", "kernel": "…", "run-bundle": "…"}
    )

    calls = list(t.tool_calls())
    assert len(calls) == 1
    assert calls[0].input["file_path"] == str(f)


def test_absent_blocks_are_not_mounted(tmp_path):
    f = tmp_path / "gone.md"
    score = BootScore(contracts=[_entry("gone", str(f), present=False)])
    t = tx.build_orientation_transcript(score, block_text={"gone": "x"})
    assert list(t.tool_calls()) == []


def test_a_trimmed_block_says_it_was_trimmed(tmp_path):
    """``kb/log.md`` is 800 KB and the wake gets its tail.

    Handing that over as the plain result of ``Read(kb/log.md)`` would teach the
    resident a false fact about what ``Read`` returns — and it would find out the
    hard way, the first time it re-read the file and got something else.
    """
    f = tmp_path / "log.md"
    f.write_text("x" * 5000, encoding="utf-8")

    score = BootScore(contracts=[_entry("log", str(f), size=100)])
    t = tx.build_orientation_transcript(score, block_text={"log": "recent tail"})

    result = next(t.tool_calls()).result
    assert result.startswith("recent tail")
    assert "rendered to 100 bytes" in result
    assert "5,000 bytes" in result


def test_an_untrimmed_block_carries_no_note(tmp_path):
    """No wolf-crying: a full block gets no disclaimer at all."""
    f = tmp_path / "AGENTS.md"
    body = "the whole contract\n"
    f.write_text(body, encoding="utf-8")

    score = BootScore(contracts=[_entry("agents", str(f), size=len(body))])
    t = tx.build_orientation_transcript(score, block_text={"agents": body})

    assert next(t.tool_calls()).result == body


# ── Rendering for the claude Shell ────────────────────────────────────────────


def test_claude_jsonl_puts_the_wake_in_tool_result_position(tmp_path):
    """The entire point of the slice, asserted as a shape.

    The last turn before the task must be a ``tool_result``. That is the position
    whose natural continuation is an action — and the position the old
    single-user-message boot could never occupy, no matter what it said.
    """
    f = tmp_path / "AGENTS.md"
    f.write_text("contract\n", encoding="utf-8")
    score = BootScore(contracts=[_entry("agents", str(f), size=9)])
    t = tx.build_orientation_transcript(
        score, block_text={"agents": "contract\n"},
        cwd="/repo", git_branch="main",
    )

    rows = [json.loads(l) for l in tx.render_claude_jsonl(t).splitlines()]
    assert len(rows) == 2

    assistant, result = rows
    assert assistant["type"] == "assistant"
    block = assistant["message"]["content"][0]
    assert block["type"] == "tool_use"
    assert block["name"] == "Read"

    assert result["type"] == "user"
    rblock = result["message"]["content"][0]
    assert rblock["type"] == "tool_result"
    assert rblock["content"] == "contract\n"
    # The pairing the Shell actually resolves on.
    assert rblock["tool_use_id"] == block["id"]


def test_turns_chain_by_parent_uuid(tmp_path):
    """A broken chain is a session the Shell will refuse to resume."""
    f1, f2 = tmp_path / "a.md", tmp_path / "b.md"
    f1.write_text("a\n", encoding="utf-8")
    f2.write_text("b\n", encoding="utf-8")
    score = BootScore(contracts=[
        _entry("a", str(f1), size=2), _entry("b", str(f2), size=2),
    ])
    t = tx.build_orientation_transcript(score, block_text={"a": "a\n", "b": "b\n"})

    rows = [json.loads(l) for l in tx.render_claude_jsonl(t).splitlines()]
    assert rows[0]["parentUuid"] is None
    for prev, cur in zip(rows, rows[1:]):
        assert cur["parentUuid"] == prev["uuid"]

    assert len({r["sessionId"] for r in rows}) == 1


def test_session_path_matches_where_claude_looks():
    p = tx.claude_session_path("/home/g/src/misc/brr", "abc-123", home=Path("/h"))
    assert p == Path("/h/.claude/projects/-home-g-src-misc-brr/abc-123.jsonl")


def test_empty_transcript_renders_empty_not_broken():
    t = tx.Transcript(turns=[], session_id="s", cwd="/x")
    assert tx.render_claude_jsonl(t) == ""


def test_unmounted_shell_is_refused_not_silently_mounted_as_claude(
    tmp_path, monkeypatch, capsys
):
    """`--runner codex` used to render a *claude* session labelled codex.

    It scored the wake for codex, stamped a codex core on the seeded turns, wrote
    claude JSONL into claude's session directory, printed a `claude --resume`
    command — and reported `body: codex / default` the whole way. A tool that
    cannot distinguish "mounted for codex" from "mounted for claude wearing a
    codex label" is the same bug as a green checkmark on a PR that never landed.
    """
    from brr.cli import main

    init_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main(["prompts", "transcript", "--runner", "codex", "--write"]) == 1

    err = capsys.readouterr().err
    assert "no transcript mount for shell 'codex'" in err
    assert "REPLAYABLE_TOOLS" in err  # says *why*, not just no

    # and nothing was written anywhere it could later be resumed by accident
    assert not list(tmp_path.rglob("*.jsonl"))


def test_mounted_shells_only_names_shells_with_a_renderer():
    """The registry is the guard; keep it honest about what actually exists."""
    assert tx.MOUNTED_SHELLS == frozenset({"claude"})
    assert hasattr(tx, "render_claude_jsonl")
