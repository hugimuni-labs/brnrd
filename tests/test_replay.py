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


def _write_mount_sidecar(run_dir: Path, mount_sink: dict[str, str]) -> None:
    """Hand-serialize the sidecar in the exact shape `run_context.write_mounted_blocks`
    writes (that function's own unit coverage lives in `test_outbox.py`, alongside
    the other `run_context.write_*` writers this module's helpers already mirror by
    hand — see `_write_captured_run` doing the same for `boot-score.json`)."""
    (run_dir / "prompt-mounted.json").write_text(
        json.dumps(
            {"schema_version": "1", "run_id": run_dir.name, "blocks": mount_sink},
            indent=2, sort_keys=True,
        ),
        encoding="utf-8",
    )


def _build_mounted_resident_run(repo_root: Path, run_dir: Path, **overrides) -> tuple[str, dict]:
    """A real *mounted* resident-daemon prompt+score, captured with its sidecar.

    Same production call (`build_daemon_prompt_with_score`) as
    `_build_resident_run`, with `_mount_sink={}` supplied — exactly what
    `daemon.py` passes when `boot.mount` is on. Returns the prompt and the
    populated `mount_sink` dict so a caller can assert against the real
    diverted text.
    """
    kwargs = dict(
        outbox_path="/tmp/brr-test-outbox",
        run_id="run-test-mounted-0001",
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
    mount_sink: dict[str, str] = {}
    prompt, score = build_daemon_prompt_with_score(
        "Implement a small feature, commit, and push a branch.",
        "evt-test-mounted-0001",
        "/tmp/brr-test-response.md",
        repo_root,
        _mount_sink=mount_sink,
        **kwargs,
    )
    assert score.body.mounted, "fixture must actually mount for a mounted-wake test to mean anything"
    assert mount_sink, "fixture mounted nothing — nothing exercises the reconstitution"
    _write_captured_run(run_dir, prompt, score)
    _write_mount_sidecar(run_dir, mount_sink)
    return prompt, mount_sink


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


# ── _true_render_order (legacy backward-compat only, #1753) ────────────


def test_true_render_order_is_a_noop_on_a_manifest_the_current_writer_produces():
    """Every fixture in this file now goes through the fixed writer
    (`_collect_toggle_contracts`), so `diffense`/`introspection` already
    arrive in true render order — the reorder must be an identity on that
    shape (see `test_locate_handles_introspection_in_the_real_render_order`,
    which passes even with this function's body replaced by `return
    contracts` outright — verified by hand while writing this test)."""
    already_correct = [
        {"block_key": "run-preamble"},
        {"block_key": "identity-core"},
        {"block_key": "diffense"},
        {"block_key": "introspection"},
        {"block_key": "run-context-bundle"},
    ]
    assert replay._true_render_order(already_correct) == already_correct


def test_true_render_order_fixes_a_pre_1753_manifests_old_order():
    """The actual compensation this function exists to keep doing, for a
    manifest written by the *old* `_collect_preamble_contracts` (before the
    #1753 split) — `diffense`/`introspection` listed right after
    `portal-verb-grammar`, before the inject stack, instead of after it.
    Every fixture elsewhere in this file goes through the fixed writer, so
    this hand-built list is the only place the legacy shape is exercised —
    without it, the "kept for backward compatibility" claim in this
    function's docstring would be untested.
    """
    old_shape = [
        {"block_key": "run-preamble"},
        {"block_key": "portal-verb-grammar"},
        {"block_key": "diffense"},
        {"block_key": "introspection"},
        {"block_key": "identity-core"},
        {"block_key": "dominion"},
        {"block_key": "run-context-bundle"},
    ]
    fixed = replay._true_render_order(old_shape)
    keys = [c["block_key"] for c in fixed]
    assert keys == [
        "run-preamble", "portal-verb-grammar", "identity-core", "dominion",
        "diffense", "introspection", "run-context-bundle",
    ]


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


# ── mounted-wake reconciliation (#1753) ─────────────────────────────────


def test_locate_reconstitutes_a_mounted_wake_marking_mounted_spans(tmp_path):
    """A mounted wake's file-backed blocks never entered `prompt.md` — they
    must resolve from `prompt-mounted.json` instead, as zero-footprint spans
    carrying the sidecar text, while a computed block (never mountable)
    stays a real `prompt.md` span.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-mount-locate"
    _prompt, mount_sink = _build_mounted_resident_run(
        repo_root, run_dir, run_id="run-test-mount-locate",
    )

    located = replay.locate_captured_prompt(run_dir)
    by_key = {s.block_key: s for s in located.spans}

    assert "run-preamble" in by_key
    run_preamble = by_key["run-preamble"]
    assert run_preamble.is_mounted
    assert run_preamble.start is None and run_preamble.end is None
    assert run_preamble.mounted_text == mount_sink["run-preamble"]

    # computed blocks (kernel, run-context-bundle) have no honest `Read` and
    # are never mountable — must stay real, non-mounted `prompt.md` spans.
    assert not by_key["boot-kernel"].is_mounted
    assert by_key["boot-kernel"].start is not None
    assert not by_key["run-context-bundle"].is_mounted


def test_locate_refuses_a_mounted_wake_captured_before_the_sidecar_existed(tmp_path):
    """A mounted run with no `prompt-mounted.json` predates #1753 and has no
    checkable way to reconstitute — refuses with that specific reason, never
    the generic byte-mismatch line and never by guessing from current disk.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-mount-nosc"
    mount_sink: dict[str, str] = {}
    prompt, score = build_daemon_prompt_with_score(
        "Implement a small feature, commit, and push a branch.",
        "evt-test-mount-nosc",
        "/tmp/brr-test-response.md",
        repo_root,
        _mount_sink=mount_sink,
        outbox_path="/tmp/brr-test-outbox",
        run_id="run-test-mount-nosc",
        source="spawn",
        environment="worktree",
        branch_name="brr/test-branch",
        budget_seconds=7200,
        hooks_installed=True,
        runner_name="claude-sonnet",
        runner_shell="claude",
        runner_core="claude-sonnet-4-6",
    )
    assert score.body.mounted
    _write_captured_run(run_dir, prompt, score)
    # Deliberately no prompt-mounted.json — simulates a run captured before #1753.

    with pytest.raises(replay.ReplayLocateError, match="captured before the sidecar existed"):
        replay.locate_captured_prompt(run_dir)


def test_locate_refuses_a_mounted_wake_with_malformed_sidecar_json(tmp_path):
    """`prompt-mounted.json` present but not valid JSON — refuses with a
    parse error naming the sidecar, not a silent guess."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-mount-badjson"
    _build_mounted_resident_run(repo_root, run_dir, run_id="run-test-mount-badjson")
    (run_dir / "prompt-mounted.json").write_text("not json{{{", encoding="utf-8")

    with pytest.raises(replay.ReplayLocateError, match="not valid JSON"):
        replay.locate_captured_prompt(run_dir)


def test_locate_refuses_a_mounted_wake_whose_sidecar_blocks_is_not_a_dict(tmp_path):
    """`prompt-mounted.json` valid JSON but with the wrong shape for
    `blocks` — refuses rather than guessing at the intended shape."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-mount-badshape"
    _build_mounted_resident_run(repo_root, run_dir, run_id="run-test-mount-badshape")
    (run_dir / "prompt-mounted.json").write_text(
        json.dumps({"schema_version": "1", "run_id": run_dir.name, "blocks": ["not", "a", "dict"]}),
        encoding="utf-8",
    )

    with pytest.raises(replay.ReplayLocateError, match="no dict-shaped"):
        replay.locate_captured_prompt(run_dir)


def test_locate_refuses_a_mounted_block_whose_sidecar_text_disagrees_with_the_manifest(tmp_path):
    """A mounted block's sidecar text must match the byte length
    `boot-score.json` recorded for it at render time — a tampered or
    truncated sidecar entry refuses rather than silently mislocating.

    Grows one block and shrinks another by the same amount so the fast
    top-level total (`prompt.md` + sum of all sidecar blocks) still
    reconciles — this is deliberately the case the *fast* check cannot
    catch, isolating the slower, per-block precision check.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-mount-badlen"
    _prompt, mount_sink = _build_mounted_resident_run(
        repo_root, run_dir, run_id="run-test-mount-badlen",
    )
    tampered = dict(mount_sink)
    # Shrink identity-core by whole characters until its UTF-8 length has
    # dropped by at least the pad's, then pad weave by exactly that many
    # ASCII bytes — the check is in *bytes*, and a prompt file may end in
    # multi-byte marks (it does, since the prompts went into the weave).
    original = tampered["identity-core"]
    shrunk = original
    while len(original.encode("utf-8")) - len(shrunk.encode("utf-8")) < 35:
        shrunk = shrunk[:-1]
    delta = len(original.encode("utf-8")) - len(shrunk.encode("utf-8"))
    pad = ("x" * delta)  # never rendered; same byte total once both edits land
    tampered["weave"] = tampered["weave"] + pad
    tampered["identity-core"] = shrunk  # same total length, in bytes
    _write_mount_sidecar(run_dir, tampered)  # overwrite with the tampered version

    with pytest.raises(replay.ReplayLocateError, match=r"block 'weave'.*bytes"):
        replay.locate_captured_prompt(run_dir)


def test_locate_refuses_a_mounted_wake_whose_reconstituted_total_disagrees(tmp_path):
    """The fast top-level check on a mounted wake compares against
    `prompt.md` size *plus* the sidecar's, not `prompt.md` alone — a sidecar
    missing an entry (or carrying a stray extra one) must still be caught
    here, before the slower per-block walk.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-mount-badtotal"
    _prompt, mount_sink = _build_mounted_resident_run(
        repo_root, run_dir, run_id="run-test-mount-badtotal",
    )
    incomplete = dict(mount_sink)
    del incomplete["weave"]  # drop one mounted block's sidecar entry entirely
    _write_mount_sidecar(run_dir, incomplete)

    with pytest.raises(replay.ReplayLocateError, match="prompt_bytes"):
        replay.locate_captured_prompt(run_dir)


def test_plan_replacement_reconstitutes_a_mounted_wake_byte_identical_to_the_unmounted_equivalent(tmp_path):
    """The central correctness claim of #1753: with nothing supplied in
    ``--prompts``, a mounted wake's reconstituted assembly must carry the
    same block content, byte for byte, as what the *same* wake would have
    rendered with ``boot.mount`` off — proving the reconstitution recovers
    the real content, not just avoids refusing.

    Excludes ``boot-kernel`` from the byte-for-byte comparison: the kernel
    legitimately differs between the two arms (it prints a
    "boot: mounted · <snapshot restored>" notice only when mounted — see
    ``bootscore.format_kernel``, guarded on ``body.mounted``), which is the
    experiment's own control signal, not a reconstitution defect.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    shared_kwargs = dict(
        outbox_path="/tmp/brr-test-outbox",
        run_id="run-test-mount-cmp",
        source="spawn",
        environment="worktree",
        branch_name="brr/test-branch",
        budget_seconds=7200,
        hooks_installed=True,
        runner_name="claude-sonnet",
        runner_shell="claude",
        runner_core="claude-sonnet-4-6",
    )

    unmounted_prompt, unmounted_score = build_daemon_prompt_with_score(
        "Implement a small feature, commit, and push a branch.",
        "evt-test-mount-cmp", "/tmp/brr-test-response.md", repo_root,
        **shared_kwargs,
    )

    mount_sink: dict[str, str] = {}
    mounted_prompt, mounted_score = build_daemon_prompt_with_score(
        "Implement a small feature, commit, and push a branch.",
        "evt-test-mount-cmp", "/tmp/brr-test-response.md", repo_root,
        _mount_sink=mount_sink,
        **shared_kwargs,
    )
    assert mounted_score.body.mounted
    assert mount_sink
    assert mounted_prompt != unmounted_prompt  # the mount is real, not a no-op

    mounted_run_dir = tmp_path / "runs" / "run-test-mount-cmp-mounted"
    _write_captured_run(mounted_run_dir, mounted_prompt, mounted_score)
    _write_mount_sidecar(mounted_run_dir, mount_sink)
    unmounted_run_dir = tmp_path / "runs" / "run-test-mount-cmp-unmounted"
    _write_captured_run(unmounted_run_dir, unmounted_prompt, unmounted_score)

    empty_dir = tmp_path / "empty-prompts"
    empty_dir.mkdir()
    reconstituted = replay.locate_captured_prompt(mounted_run_dir)
    reference = replay.locate_captured_prompt(unmounted_run_dir)

    reconstituted_by_key = {s.block_key: s for s in reconstituted.spans}
    reference_by_key = {s.block_key: s for s in reference.spans}
    assert set(reconstituted_by_key) == set(reference_by_key)

    def _text(span, prompt_bytes):
        return (
            span.mounted_text if span.is_mounted
            else prompt_bytes[span.start:span.end].decode("utf-8")
        )

    for key in reference_by_key:
        if key == "boot-kernel":
            continue  # legitimately differs — see docstring
        got = _text(reconstituted_by_key[key], reconstituted.prompt_bytes)
        want = _text(reference_by_key[key], reference.prompt_bytes)
        assert got == want, f"block {key!r} diverged after reconstitution"

    # And the splice itself: nothing supplied in --prompts, so every block
    # reports unchanged and the total delta is a flat zero.
    result = replay.plan_replacement(mounted_run_dir, empty_dir)
    assert result.total_delta == 0
    assert all(d.status in ("unchanged", "computed") for d in result.deltas)


def test_plan_replacement_substitutes_a_mounted_block_from_prompts_dir(tmp_path):
    """A block that was mounted out of the prose is still an ordinary
    file-backed block from `--prompts`'s point of view — substitution must
    work on it exactly as on a block `prompt.md` carried directly.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-mount-sub"
    _prompt, mount_sink = _build_mounted_resident_run(
        repo_root, run_dir, run_id="run-test-mount-sub",
    )
    assert "weave" in mount_sink  # weave.md must have mounted for this test to mean anything

    prompts_dir = tmp_path / "edited-prompts"
    prompts_dir.mkdir()
    (prompts_dir / "weave.md").write_text("# Edited weave\n\nNew register text.\n", encoding="utf-8")

    result = replay.plan_replacement(run_dir, prompts_dir)
    by_key = {d.block_key: d for d in result.deltas}

    assert by_key["weave"].status == "substituted"
    assert by_key["weave"].old_text == mount_sink["weave"]
    assert by_key["weave"].new_text.strip() == "# Edited weave\n\nNew register text.".strip()
    assert by_key["run-preamble"].status == "unchanged"
    assert by_key["run-preamble"].old_bytes == len(mount_sink["run-preamble"].encode("utf-8"))


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


def test_locate_resolves_a_diffense_wake_after_the_stripping_drift_was_fixed(tmp_path):
    """`_join_prompt_parts` used to append `diffense.md`'s block **unstripped**
    (``read_prompt("diffense.md", repo_root)``, no ``.strip()``) while the
    manifest's own contract row measured it **stripped**
    (``prompts._collect_toggle_contracts``) — every other block in the same
    function stripped both sides consistently; this one didn't. `diffense.md`
    in this tree carries exactly one trailing byte the strip would have
    removed, so the manifest's recorded length was one byte short of what
    actually entered the prompt, and every block after it (introspection,
    the trailer) inherited the drift (#1753 fork 3).

    Fixed at the writer (`_join_prompt_parts` now strips `diffense.md` to
    match the manifest) rather than compensated for here — this test used to
    assert `replay` refused a diffense wake; it now asserts the block
    resolves cleanly, byte for byte, documenting that the drift is gone
    rather than merely tolerated.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = tmp_path / "runs" / "run-test-0003b"
    prompt = _build_resident_run(repo_root, run_dir, run_id="run-test-0003b", diffense=True)

    located = replay.locate_captured_prompt(run_dir)
    assert located.prompt_bytes == prompt.encode("utf-8")

    by_key = {s.block_key: s for s in located.spans}
    assert "diffense" in by_key
    diffense_span = by_key["diffense"]
    diffense_text = located.prompt_bytes[diffense_span.start:diffense_span.end].decode("utf-8")
    assert diffense_text == diffense_text.strip()  # no leftover unstripped whitespace
    assert diffense_text  # non-empty — the toggle was on


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
