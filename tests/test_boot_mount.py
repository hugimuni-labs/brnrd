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

    # 5. And the *total* says so too, not just the key list. Clause 4 asserted that
    #    the ledger keeps *naming* every block, and for a while that was all any
    #    assertion here reached: `prompt_bytes` counted the prose alone, so a
    #    mounted wake reported itself 24% smaller than it was and `_cost_ledger`
    #    printed a negative `unattributed` line with shares summing to 132%. The
    #    mount moves bytes between channels; it is not a discount.
    moved = sum(len(text.encode("utf-8")) for text in sink.values())
    assert score_a.prompt_bytes == len(prose_only.encode("utf-8"))
    #    The teeth: under the old arithmetic these two were equal. The mounted
    #    total must exceed the prose it kept, by what left it.
    assert score_b.prompt_bytes > len(mounted_prose.encode("utf-8"))
    assert score_b.prompt_bytes == len(mounted_prose.encode("utf-8")) + moved
    #    Both arms therefore bill the same wake, up to the one line the mount
    #    itself adds (the kernel's `boot: mounted · <snapshot restored>` notice —
    #    measured at 91 B, and the only text either arm has that the other
    #    doesn't). Not exact equality: the mount is not free, it is just not a
    #    saving. What this rules out is a *shortfall the size of a block*.
    assert 0 <= score_b.prompt_bytes - score_a.prompt_bytes < 200


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


def test_never_mount_is_enforced_at_the_transcript_choke_point():
    """`NEVER_MOUNT` must hold for any caller, not only the daemon's wake.

    Positive control first: an ordinary file-backed block with the same shape
    *does* mount. Without it this test would pass on a transcript builder that
    seeds nothing at all — the failure mode `tools/mount_inheritance.py`'s own
    first run had, and the one this repo has paid for before.
    """
    score = BootScore(contracts=[
        ContractEntry(block_key="identity-core", label="identity", owner="product",
                      authority="identity", freshness=None, location="/tmp/identity.md",
                      present=True, bytes=10),
        ContractEntry(block_key="prior-run", label="last run", owner="resident",
                      authority="memory", freshness=None, location="/tmp/body.md",
                      present=True, bytes=10),
    ])
    block_text = {"identity-core": "who you are", "prior-run": "what you did last time"}
    t = tx.build_orientation_transcript(score, block_text=block_text, cwd="/tmp")
    seeded = [c.location for c in t.perceptions()]
    assert seeded == ["/tmp/identity.md"]  # positive control + the exclusion
    assert "prior-run" in tx.NEVER_MOUNT
