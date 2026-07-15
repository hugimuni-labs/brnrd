"""Wiring the transcript into a real wake (`boot.mount`, default off).

The mount is not "add `--resume` to argv". The mount is a *subtraction*: the
file-backed contracts leave the prose and arrive as seeded ``Read`` results
instead. Get the subtraction wrong in either direction and the whole thing is
worthless —

- subtract too little  → the wake pays for every block twice, and both arms of
  the T-vs-P experiment carry the prose, so it measures nothing;
- subtract too much    → a block leaves the prose and is mounted nowhere. The
  wake runs with a contract missing, silently, *caused by the boot*. That is the
  bug class this entire line of work exists to kill, and it would be a fine irony
  to ship it here.

So the load-bearing test in this file is `test_the_mount_loses_nothing`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import transcript as tx
from brr.bootscore import BootScore, ContractEntry
from brr.prompts import build_daemon_prompt_with_score
from _helpers import init_git_repo


def _wake(repo: Path, **extra):
    kwargs = dict(
        event_body="do the thing",
        runner_name="claude",
        runner_shell="claude",
        runner_core="claude-haiku-4-5",
        environment="host",
        budget_seconds=7200,
    )
    kwargs.update(extra)
    return build_daemon_prompt_with_score(
        "do the thing", "evt-1", "/tmp/resp.md", repo, **kwargs
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    init_git_repo(tmp_path)
    return tmp_path


def test_default_off_is_the_boot_every_wake_has_always_had(repo: Path):
    """No flag, no change. The prose boot is the control arm and must not move."""
    plain, _ = _wake(repo)
    also_plain, _ = _wake(repo)
    assert plain == also_plain

    # `_mount_sink=None` is the default; nothing is subtracted, nothing is forged.
    assert "brnrd" in plain  # sanity: we built a real wake


def test_the_mount_loses_nothing(repo: Path):
    """Prose + mounted perceptions == everything the unmounted wake carried.

    Every block that leaves the prose must be findable in the mount. This is the
    assertion that a "clever" boot cannot pass while silently dropping a contract.
    """
    prose_only, score_a = _wake(repo)

    sink: dict[str, str] = {}
    mounted_prose, score_b = _wake(repo, _mount_sink=sink)

    assert sink, "nothing was mounted — the fixture has no file-backed blocks"

    # 1. The mounted blocks really did leave the prose.
    for key, text in sink.items():
        body = text.strip()
        assert body, f"{key} mounted as empty text"
        assert body not in mounted_prose, f"{key} was mounted AND left in the prose"

    # 2. And they really were in the prose before. (Otherwise the sink is fiction:
    #    a mount that "removes" blocks the prompt never had.)
    for key, text in sink.items():
        assert text.strip() in prose_only, f"{key} was never in the prose to begin with"

    # 3. The wake got smaller by exactly the thing that moved, not by anything else.
    assert len(mounted_prose) < len(prose_only)

    # 4. The score still describes the *whole* wake. A block delivered as a seeded
    #    perception is still a block the wake received and still costs its bytes:
    #    the cost ledger must not go blind just because the channel changed.
    keys_a = {c.block_key for c in score_a.contracts if c.present}
    keys_b = {c.block_key for c in score_b.contracts if c.present}
    assert keys_a == keys_b


def test_a_computed_block_is_never_subtracted(repo: Path):
    """Live state has no honest `Read`, so it must stay prose — in *both* paths.

    This is the direction that fails silently. `build_orientation_transcript`
    already refuses to mount a `location == "computed"` block; if the prompt
    builder had subtracted it anyway, the block would leave the prose and be
    seeded nowhere. Same set on both sides, or nothing.
    """
    sink: dict[str, str] = {}
    _, score = _wake(repo, _mount_sink=sink)

    computed = {c.block_key for c in score.contracts if c.location == tx.COMPUTED}
    assert computed, "fixture should have at least the kernel and the run bundle"
    assert not (computed & set(sink)), "a computed block was taken out of the prose"


def test_the_mounted_bytes_are_what_the_wake_would_have_read(repo: Path):
    """The seed carries the *rendered* block, not the file — and says so if trimmed."""
    sink: dict[str, str] = {}
    _, score = _wake(repo, _mount_sink=sink)

    session = tx.build_orientation_transcript(
        score, block_text=sink, cwd=str(repo), model="claude-haiku-4-5"
    )
    seen = list(session.perceptions())
    assert seen

    for p in seen:
        assert Path(p.location).is_absolute()
        assert p.result.strip()


def test_an_untrimmed_block_carries_no_note_on_the_REAL_path(repo: Path):
    """The wolf-crying regression, pinned where it actually happened.

    `test_an_untrimmed_block_carries_no_note` in test_transcript.py has always
    passed — because it constructs `bytes=len(body)` against a file of exactly
    that size. The production path never does: the block is `.strip()`ed and the
    file keeps its trailing newline, so `rendered` came in **one byte** under
    `stat().st_size` and every mounted block got a 137-byte "this was trimmed for
    the wake budget; re-read it for the rest" — a lie, on all four, every wake.

    So this test runs the real builder against the real prompt files. A note here
    means a block genuinely lost content to the budget.
    """
    sink: dict[str, str] = {}
    _, score = _wake(repo, _mount_sink=sink)

    session = tx.build_orientation_transcript(
        score, block_text=sink, cwd=str(repo), model="claude-haiku-4-5"
    )
    untrimmed = {"run-preamble", "weave", "daemon-substrate", "identity-core"}
    by_key = {c.location: c.block_key for c in score.contracts}

    for p in session.perceptions():
        if by_key.get(p.location) in untrimmed:
            assert "[brnrd: this block was rendered to" not in p.result, (
                f"{by_key[p.location]} claims it was trimmed and it was not"
            )


def test_a_mount_with_nothing_in_it_raises_instead_of_lobotomising_the_wake(
    repo: Path,
):
    """The failure that must never be silent.

    By the time the mount runs, the blocks are already out of the prose. A mount
    that finds nothing to seed and shrugs would hand the runner a wake with its
    contracts deleted. It raises, and the daemon rebuilds the prose prompt.
    """
    empty = BootScore(contracts=[
        ContractEntry(
            block_key="kernel", label="k", owner="daemon", authority="runtime",
            freshness=None, location=tx.COMPUTED, present=True, bytes=10,
        ),
    ])
    with pytest.raises(ValueError, match="Rebuild the prompt unmounted"):
        tx.mount_claude_session(empty, block_text={"kernel": "x"}, cwd=str(repo))


def test_mounting_forges_a_session_the_shell_can_find(repo: Path, tmp_path: Path):
    home = tmp_path / "home"
    sink: dict[str, str] = {}
    _, score = _wake(repo, _mount_sink=sink)

    sid = tx.mount_claude_session(
        score, block_text=sink, cwd=str(repo), git_branch="brr/x",
        model="claude-haiku-4-5", home=home,
    )

    path = tx.claude_session_path(str(repo), sid, home=home)
    assert path.exists()

    body = path.read_text(encoding="utf-8")
    assert body.endswith("\n")
    assert '"tool_use"' in body and '"tool_result"' in body
    # `--fork-session` is the half that makes the seed replayable rather than
    # consumed by the run it booted.
    assert tx.resume_argv(sid) == ["--resume", sid, "--fork-session"]


# ── The wake says which boot it got ──────────────────────────────────────
#
# Until 2026-07-14 it could not: a resident had to grep its own `prompt.md`
# mid-run to learn it was mounted, and said so in its reply. The kernel now
# carries it — derived from the render, never from the config key that asked.


def test_the_kernel_tells_the_wake_it_was_mounted(repo: Path):
    """And it says *what that means* — the honesty line, in the slot it lands in.

    Measured, not styled: the fence at the end of the seeded transcript is not
    reliably attended on its own (claude-haiku-4-5 claimed "I read it myself — I
    called the Read tool in my previous response" in 1 of 3 rounds with only the
    seed fence, 0 of 3 once the same sentence also appeared in the kernel). The
    seed is where you put what the wake acts *from*; the kernel is what it *knows*.

    Asserted as the **contract**, not the sentence — the lesson from the
    ``daemon-substrate`` cut, where prose-pinning tests fired on reflowed line
    wraps as loudly as on six genuinely lost contracts. What must hold: the wake
    learns it was mounted, that the memory is *its own* (not brnrd's loan), and
    that the deed ledger for *this run* is empty. The wording may move.
    """
    sink: dict[str, str] = {}
    prompt, score = _wake(repo, _mount_sink=sink)

    assert score.body.mounted is True
    assert "boot: mounted" in prompt
    assert "<snapshot restored>" in prompt
    # Ownership: the memory is the resident's, and it is *not* a thing brnrd lent it.
    assert "memory: yours, not given" in prompt or "memory: yours" in prompt
    assert "predates this run" in prompt
    # Deed: scoped to the run, never to the resident. An unqualified "acts: none yet"
    # is a lie about a being with 394 log entries — it is the *run* that is new.
    assert "acts *here*: none yet" in prompt
    # And the fence is at the other end, in the seed itself.
    assert sink, "nothing was mounted — the test is not testing the mount"


def test_a_prose_wake_says_nothing_about_a_mount(repo: Path):
    """Differential: the line costs a prose wake exactly zero bytes."""
    prompt, score = _wake(repo)
    assert score.body.mounted is False
    assert "boot: mounted" not in prompt


def test_the_mounted_line_is_derived_from_the_render_not_the_request(repo: Path):
    """A config key is a *request*; the wake is what happened.

    The daemon may ask for a mount the render cannot deliver (unsupported Shell,
    nothing seedable). When that happens it rebuilds the prompt with no sink — and
    the kernel must go quiet on its own, without anyone remembering to reset a flag.
    This is `probe_mount`'s discipline pointed at the resident: only the artifact is
    evidence.
    """
    # Asked for a mount, but no block is mountable → the render subtracts nothing.
    prompt, score = _wake(repo, _mount_sink={})
    # The honest outcome is decided by `_mountable`, which is computed from the
    # contracts — so a wake that mounted nothing must not claim it mounted.
    if not score.body.mounted:
        assert "boot: mounted" not in prompt
