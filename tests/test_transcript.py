"""Slice 4 — the boot as a transcript.

The thesis under test: a wake's opening is not just *content* at a position, it
is **grammar** at a position. A ``tool_result`` is the one turn type whose
natural continuation is an action, and the boot has never been able to use it,
because the whole 73 KB arrives as a single user message.

The most important test in this file is the one that says the boot may not lie.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

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


def test_the_boot_cannot_even_say_an_action():
    """Perception may be seeded. Action may never be — and now cannot be *said*.

    A seeded read is honest: the bytes really are in the wake's context, which is
    the only sense in which any agent has read anything. A seeded ``Write`` is a
    forgery — the file was not written, and a resident that finds ``Write(.card)``
    in what looks like its own history will believe the card exists and never
    write one. Silent, and *caused by the boot*: the exact bug class this whole
    line of work exists to kill.

    This used to be a ``REPLAYABLE_TOOLS`` frozenset that a ``ToolCall.tool``
    string was checked against. That guard is gone, and the rule is *stronger* for
    it: :class:`Perceive` has nowhere to put a tool name, so the forgery is not
    rejected — it is **unrepresentable**. An allowlist can be widened by anyone in
    a hurry. A type cannot.

    So this test guards the *shape*: add a ``tool`` field back and it fires.
    """
    assert [f.name for f in dataclasses.fields(tx.Perceive)] == ["location", "result"]

    # The old escape hatches are gone, not merely unused.
    assert not hasattr(tx, "ToolCall")
    assert not hasattr(tx, "REPLAYABLE_TOOLS")
    assert not hasattr(tx, "ForgedActionError")


def test_the_renderer_spends_only_a_read(tmp_path):
    """The rule moved to the type; this is the one place it still needs watching.

    ``Perceive`` cannot *name* a tool — but a renderer, which must name one to
    speak its Shell's dialect, could name the wrong one. So: whatever a renderer
    emits into a session file, every ``tool_use`` in it is a read and nothing else.

    This is the assertion that ports. A future ``render_codex_jsonl`` spends
    ``exec`` with a ``cat`` the renderer itself authored — and the codex version of
    this test asserts exactly that command shape, for exactly this reason.
    """
    f = tmp_path / "AGENTS.md"
    f.write_text("contract\n", encoding="utf-8")
    score = BootScore(contracts=[_entry("agents", str(f), size=9)])
    t = tx.build_orientation_transcript(score, block_text={"agents": "contract\n"})

    rows = [json.loads(l) for l in tx.render_claude_jsonl(t).splitlines()]
    spent = [
        c["name"]
        for r in rows
        if r["type"] == "assistant"
        for c in r["message"]["content"]
        if c.get("type") == "tool_use"
    ]
    assert spent == [tx.CLAUDE_READ_TOOL] == ["Read"]


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

    calls = list(t.perceptions())
    assert len(calls) == 1
    assert calls[0].location == str(f)


def test_absent_blocks_are_not_mounted(tmp_path):
    f = tmp_path / "gone.md"
    score = BootScore(contracts=[_entry("gone", str(f), present=False)])
    t = tx.build_orientation_transcript(score, block_text={"gone": "x"})
    assert list(t.perceptions()) == []


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

    result = next(t.perceptions()).result
    assert result.startswith("recent tail")
    assert "rendered to 100 bytes" in result
    assert "5,000 bytes" in result


def test_an_untrimmed_block_carries_no_note(tmp_path):
    """No wolf-crying: a full block gets no *trim* disclaimer at all.

    (The snapshot fence is a different animal and always closes the seed — so the
    exact result is body + fence, and the trim note must be nowhere in it. Pinned
    exactly rather than loosened: the wolf-cry bug this guards against shipped
    once already, stapling a 137-byte "re-read it for the rest" onto four blocks
    that had lost nothing.)
    """
    f = tmp_path / "AGENTS.md"
    body = "the whole contract\n"
    f.write_text(body, encoding="utf-8")

    score = BootScore(contracts=[_entry("agents", str(f), size=len(body))])
    t = tx.build_orientation_transcript(score, block_text={"agents": body})

    result = next(t.perceptions()).result
    assert result == body + tx.SNAPSHOT_SEAM
    assert "rendered to" not in result


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
    assert rblock["content"].startswith("contract\n")
    # The pairing the Shell actually resolves on.
    assert rblock["tool_use_id"] == block["id"]


def test_snapshot_seam_never_costs_the_tool_result_position(tmp_path):
    """The fence rides *inside* the last result — it does not become a turn.

    This is a regression pin with a story. The seam's first cut was a closing
    ``Say`` turn, which read beautifully and quietly undid the feature: it put a
    prose ``user`` row in the last slot, so the task arrived at a model that had
    just been *read to* rather than one that had just *acted* — spending the
    mount's measured benefit (branch discipline, 3/3) to buy an unmeasured gain in
    honesty. It would also have emitted two adjacent ``user`` rows, whose failure
    mode is a mount that silently degrades to prose.

    So: the seed ends on a ``tool_result``, always, and the fence is carried by it.
    """
    f = tmp_path / "AGENTS.md"
    f.write_text("contract\n", encoding="utf-8")
    score = BootScore(contracts=[_entry("agents", str(f), size=9)])
    t = tx.build_orientation_transcript(
        score, block_text={"agents": "contract\n"}, cwd="/repo",
    )

    rows = [json.loads(l) for l in tx.render_claude_jsonl(t).splitlines()]
    last = rows[-1]
    assert last["type"] == "user"
    tail = last["message"]["content"][0]
    assert tail["type"] == "tool_result", "the seed must end in tool-result position"
    assert "<snapshot restored>" in tail["content"], "the fence must be in the seed"
    # And it must be the *end* of the seed, not floating mid-block.
    assert tail["content"].rstrip().endswith("]")


def test_no_snapshot_seam_when_nothing_was_mounted(tmp_path):
    """A fence with nothing behind it announces a restoration that never happened."""
    score = BootScore(contracts=[_entry("kernel", tx.COMPUTED, size=10)])
    t = tx.build_orientation_transcript(score, block_text={"kernel": "live"}, cwd="/r")
    assert t.turns == []
    assert "<snapshot restored>" not in tx.render_claude_jsonl(t)


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
    assert "missing renderer, not a safety wall" in err  # says *why*, not just no

    # and nothing was written anywhere it could later be resumed by accident
    assert not list(tmp_path.rglob("*.jsonl"))


def test_mounted_shells_only_names_shells_with_a_renderer():
    """The registry is the guard; keep it honest about what actually exists."""
    assert tx.MOUNTED_SHELLS == frozenset({"claude"})
    assert hasattr(tx, "render_claude_jsonl")
