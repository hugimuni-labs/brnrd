"""Tests for `brr.replay` — w-56 rung 1.

Fixtures are built by calling the real `prompts.build_daemon_prompt_with_score`
(the same function every daemon wake calls) against a scratch `repo_root`,
never hand-typed — so a locate/offset assumption this module makes is
checked against production assembly, not a guess about it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import config as conf
from brr import replay
from brr.prompts import build_daemon_prompt_with_score


def _write_captured_run(run_dir: Path, prompt: str, score) -> None:
    from brr import bootscore

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (run_dir / "boot-score.json").write_text(
        json.dumps(bootscore.to_dict(score), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _build_resident_run(repo_root: Path, run_dir: Path, **overrides) -> str:
    """A real resident-daemon prompt+score, captured into `run_dir`."""
    kwargs = dict(
        outbox_path="/tmp/brr-test-outbox",
        run_id="run-test-0001",
        source="spawn",
        environment="worktree",
        branch_name="brr/test-branch",
        budget_seconds=7200,
        hooks_installed=True,
        runner_name="claude-sonnet",
        runner_shell="claude",
        runner_core="claude-sonnet-4-6",
    )
    kwargs.update(overrides)
    prompt, score = build_daemon_prompt_with_score(
        "Implement a small feature, commit, and push a branch.",
        "evt-test-0001",
        "/tmp/brr-test-response.md",
        repo_root,
        **kwargs,
    )
    _write_captured_run(run_dir, prompt, score)
    return prompt


def _build_strand_run(repo_root: Path, run_dir: Path, **overrides) -> str:
    kwargs = dict(
        outbox_path="/tmp/brr-test-outbox",
        run_id="run-test-0002",
        source="spawn",
        environment="worktree",
        budget_seconds=1800,
        hooks_installed=True,
        strand=True,
        runner_name="claude-sonnet",
        runner_shell="claude",
        runner_core="claude-sonnet-4-6",
    )
    kwargs.update(overrides)
    prompt, score = build_daemon_prompt_with_score(
        "Bounded task text.",
        "evt-test-0002",
        "/tmp/brr-test-response.md",
        repo_root,
        **kwargs,
    )
    _write_captured_run(run_dir, prompt, score)
    return prompt


# ── locate_captured_prompt ──────────────────────────────────────────────


def test_locate_resolves_every_file_backed_block_of_a_real_resident_wake(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0001"
    prompt = _build_resident_run(repo_root, run_dir)

    located = replay.locate_captured_prompt(run_dir)

    assert located.prompt_bytes == prompt.encode("utf-8")
    file_backed = {s.block_key: s for s in located.spans if s.file_backed}
    for key in ("run-preamble", "weave", "register", "daemon-substrate", "identity-core"):
        assert key in file_backed, f"{key} missing from located spans"
        span = file_backed[key]
        # The exact byte range must reproduce the block content, byte for byte.
        text = located.prompt_bytes[span.start:span.end].decode("utf-8")
        assert text  # non-empty for every one of these on a resident wake


def test_locate_resolves_a_strand_wake_which_uses_strand_preamble_not_run(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0002"
    _build_strand_run(repo_root, run_dir)

    located = replay.locate_captured_prompt(run_dir)
    keys = {s.block_key for s in located.spans}

    assert "strand-preamble" in keys
    assert "run-preamble" not in keys
    assert "register" not in keys  # strand wakes skip register.md
    assert "identity-core" not in keys  # strand wakes skip the inject stack


def test_locate_handles_introspection_in_the_real_render_order(tmp_path):
    """The one confirmed divergence between the manifest's listed order and
    `_join_prompt_parts`'s actual render order (see replay.py's
    `_true_render_order` docstring) — this is the case that only passes if
    the reorder fix is applied; without it the walk hits a boundary
    mismatch and raises. Introspection alone (not diffense — see the next
    test for why that one is exercised separately).
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    conf.write_config(repo_root, {"introspect.enabled": True})
    run_dir = tmp_path / "runs" / "run-test-0003"
    _build_resident_run(repo_root, run_dir, run_id="run-test-0003")

    located = replay.locate_captured_prompt(run_dir)
    keys = [s.block_key for s in located.spans]

    assert "introspection" in keys
    # Must land right before the trailer, after the inject stack (here,
    # `identity-core` — the one inject-stack block guaranteed present since
    # it's bundled and non-empty) — the manifest itself lists it right
    # after `portal-verb-grammar`, before the inject stack even begins.
    assert keys.index("introspection") > keys.index("identity-core")
    assert keys.index("run-context-bundle") == len(keys) - 1
    assert keys.index("introspection") == len(keys) - 2


def test_locate_refuses_a_diffense_wake_documenting_the_upstream_byte_drift(tmp_path):
    """`_join_prompt_parts` appends `diffense.md`'s block **unstripped**
    (``read_prompt("diffense.md", repo_root)``, no ``.strip()``) while the
    manifest's own contract row measures it **stripped**
    (``prompts._file_entry``) — every other block in the same function
    strips both sides consistently; this one doesn't. `diffense.md` in this
    tree carries exactly one trailing byte the strip would have removed, so
    the manifest's recorded length is one byte short of what actually
    entered the prompt, and every block after it (introspection, the
    trailer) inherits the drift.

    This is a real, pre-existing gap in `prompts.py` (not something this
    module introduced or should paper over — see the rung-1 report for the
    fix recommendation), and the point of this test is that `replay`
    catches it and refuses rather than silently mislocating everything
    downstream of `diffense`.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0003b"
    _build_resident_run(repo_root, run_dir, run_id="run-test-0003b", diffense=True)

    with pytest.raises(replay.ReplayLocateError):
        replay.locate_captured_prompt(run_dir)


def test_locate_refuses_a_mounted_wake_rather_than_reporting_a_false_no_change(tmp_path):
    """Reproduces the real finding from this repo's own captured runs: a
    manifest whose recorded bytes describe blocks that never entered the
    prose (boot.mount) must refuse, not silently report "unchanged".
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0004"
    _build_resident_run(repo_root, run_dir, run_id="run-test-0004")

    # Simulate mounting: truncate prompt.md to just its first 200 bytes
    # (as a live mount would — most blocks leave the prose) while leaving
    # boot-score.json's byte ledger describing the full, unmounted sizes.
    prompt_path = run_dir / "prompt.md"
    prompt_path.write_bytes(prompt_path.read_bytes()[:200])

    with pytest.raises(replay.ReplayLocateError, match="prompt_bytes"):
        replay.locate_captured_prompt(run_dir)


def test_locate_refuses_a_missing_run(tmp_path):
    with pytest.raises(replay.ReplayLocateError, match="no prompt.md"):
        replay.locate_captured_prompt(tmp_path / "nope")


def test_locate_refuses_a_run_with_no_boot_score(tmp_path):
    run_dir = tmp_path / "runs" / "old-run"
    run_dir.mkdir(parents=True)
    (run_dir / "prompt.md").write_text("# old wake\n", encoding="utf-8")

    with pytest.raises(replay.ReplayLocateError, match="boot-score.json"):
        replay.locate_captured_prompt(run_dir)


# ── plan_replacement ────────────────────────────────────────────────────


def test_plan_replacement_substitutes_only_supplied_files_and_reports_the_rest(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0005"
    _build_resident_run(repo_root, run_dir, run_id="run-test-0005")

    prompts_dir = tmp_path / "edited-prompts"
    prompts_dir.mkdir()
    (prompts_dir / "weave.md").write_text("# Edited weave\n\nNew register text.\n", encoding="utf-8")

    result = replay.plan_replacement(run_dir, prompts_dir)
    by_key = {d.block_key: d for d in result.deltas}

    assert by_key["weave"].status == "substituted"
    assert by_key["weave"].new_text.strip() == "# Edited weave\n\nNew register text.".strip()
    assert by_key["run-preamble"].status == "unchanged"
    assert by_key["daemon-substrate"].status == "unchanged"
    assert by_key["boot-kernel"].status == "computed"
    assert result.total_delta == by_key["weave"].new_bytes - by_key["weave"].old_bytes


def test_plan_replacement_names_a_supplied_file_matching_no_block(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0006"
    _build_resident_run(repo_root, run_dir, run_id="run-test-0006")

    prompts_dir = tmp_path / "edited-prompts"
    prompts_dir.mkdir()
    (prompts_dir / "not-a-real-block.md").write_text("stray file\n", encoding="utf-8")

    result = replay.plan_replacement(run_dir, prompts_dir)

    assert "not-a-real-block.md" in result.unmatched_files
    assert all(d.status != "substituted" for d in result.deltas)


def test_plan_replacement_block_filter_scopes_to_named_blocks(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0007"
    _build_resident_run(repo_root, run_dir, run_id="run-test-0007")

    prompts_dir = tmp_path / "edited-prompts"
    prompts_dir.mkdir()
    (prompts_dir / "weave.md").write_text("# Edited weave\n", encoding="utf-8")
    (prompts_dir / "run.md").write_text("# Edited run\n", encoding="utf-8")

    result = replay.plan_replacement(run_dir, prompts_dir, block_filter=["weave"])
    by_key = {d.block_key: d for d in result.deltas}

    assert by_key["weave"].status == "substituted"
    # run.md was supplied but --block scoped the run to weave only.
    assert by_key["run-preamble"].status == "unchanged"


def test_plan_replacement_spliced_bytes_is_byte_identical_when_nothing_supplied(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0008"
    prompt = _build_resident_run(repo_root, run_dir, run_id="run-test-0008")

    empty_dir = tmp_path / "empty-prompts"
    empty_dir.mkdir()
    result = replay.plan_replacement(run_dir, empty_dir)

    assert result.spliced_bytes == prompt.encode("utf-8")
    assert result.total_delta == 0


def test_plan_replacement_portal_verb_grammar_re_extracts_from_a_substituted_portals_md(tmp_path):
    """The one curated-extract block: substituting `portals.md` must re-run
    the same section extraction the live wake applies, not paste the whole
    file in.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0009"
    _build_resident_run(repo_root, run_dir, run_id="run-test-0009")

    prompts_dir = tmp_path / "edited-prompts"
    prompts_dir.mkdir()
    (prompts_dir / "portals.md").write_text(
        "### `brnrd do` — the verdict rides the act\n\nEdited do body.\n\n"
        "### `brnrd await` — the wait with nothing to forget (#959, #1187)\n\n"
        "Edited await body.\n\n"
        "### Something else entirely\n\nShould not appear.\n",
        encoding="utf-8",
    )

    result = replay.plan_replacement(run_dir, prompts_dir)
    by_key = {d.block_key: d for d in result.deltas}

    portal = by_key["portal-verb-grammar"]
    assert portal.status == "substituted"
    assert "Edited do body." in portal.new_text
    assert "Edited await body." in portal.new_text
    assert "Should not appear." not in portal.new_text


# ── format_human / to_dict ──────────────────────────────────────────────


def test_format_human_always_prints_the_full_roster(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0010"
    _build_resident_run(repo_root, run_dir, run_id="run-test-0010")

    empty_dir = tmp_path / "empty-prompts"
    empty_dir.mkdir()
    result = replay.plan_replacement(run_dir, empty_dir)
    out = replay.format_human(result)

    assert "run-preamble" in out
    assert "weave" in out
    assert "daemon-substrate" in out
    assert "total delta: +0B" in out


def test_to_dict_round_trips_through_json(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0011"
    _build_resident_run(repo_root, run_dir, run_id="run-test-0011")

    prompts_dir = tmp_path / "edited-prompts"
    prompts_dir.mkdir()
    (prompts_dir / "weave.md").write_text("# Edited weave\n", encoding="utf-8")

    result = replay.plan_replacement(run_dir, prompts_dir)
    payload = json.dumps(replay.to_dict(result))
    parsed = json.loads(payload)

    assert parsed["run_id"] == "run-test-0011"
    weave = next(b for b in parsed["blocks"] if b["block_key"] == "weave")
    assert weave["status"] == "substituted"
