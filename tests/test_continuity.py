"""Slice 3 — continuity, and the provenance bug that started it.

Two subjects, one run:

1. The kernel's ``attention:`` line told its first live reader that its
   attention had arrived "from the dashboard spool rack" when the user had
   typed it into telegram.  The spool rack had only chosen the *Core*.  Root
   cause: **"body" is overloaded** — the resident's body (Shell+Core) versus
   the *event body* (the task text) — and one field served both six lines
   apart in the same kernel.  Nothing failed; the line rendered, well-formed
   and confident and wrong.  Pinned below so it cannot come back.

2. Continuity — the world's readout of what the resident last did.  Observed,
   never authored.  Its whole value is being **rare and true**, so the drift
   test is the load-bearing one: on its very first live render the drift line
   fired on the daemon's own ``run-state/`` file and would have cried wolf on
   every wake forever.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from brr import continuity as cont_mod
from brr.bootscore import (
    BootAttention,
    BootBody,
    BootContinuity,
    BootHost,
    BootScore,
    ContractEntry,
    format_kernel,
)


# ── The provenance bug ────────────────────────────────────────────────────────


def _kernel(**kw) -> str:
    return format_kernel(BootScore(**kw))


def test_body_provenance_renders_on_the_body_line() -> None:
    """Why this body is a fact *about the body*."""
    out = _kernel(
        body=BootBody(
            name="claude-fable",
            shell="claude",
            core="claude-fable-5",
            provenance="requested from the dashboard spool rack",
        ),
    )
    body_line = next(
        ln for ln in out.splitlines() if ln.startswith("body requested:")
    )
    assert "requested from the dashboard spool rack" in body_line


def test_attention_line_names_the_gate_not_the_runner() -> None:
    """The regression, pinned.

    The ``attention:`` line exists to say **who is speaking**.  It must name the
    gate the event arrived through — and must never present the *runner's*
    provenance as the attention's.
    """
    out = _kernel(
        body=BootBody(
            name="claude-fable",
            provenance="requested from the dashboard spool rack",
        ),
        attention=BootAttention(event_ids=("evt-xlqg",), source_gate="telegram"),
    )
    att = next(ln for ln in out.splitlines() if ln.startswith("attention:"))
    assert "via telegram" in att
    # The exact shape of the original bug: the runner note leaking onto the
    # attention line, where it asserted a falsehood in the wake's hottest slot.
    assert "spool rack" not in att


# ── The queue is the resident's, and only the resident's ──────────────────────


def test_worker_is_never_told_to_answer_the_residents_queue() -> None:
    """The 2026-07-13 incident, pinned.

    ``pending_count`` is the **parent's** queue — events addressed to the
    resident, in the resident's gate thread.  It leaked into the worker kernel,
    which handed a spawned worker, at position 1, in the imperative:

        next:
          2. answer 12 queued events — one outbox file each, `event: <id>`

    Two workers (claude-haiku, codex-mini) did precisely that: they answered
    twelve of the user's messages to the resident, in the resident's thread,
    with no context for any of them.

    ``worker.md`` says the spawning conversation "is not yours to hold or
    extend" — in prose, *below* the kernel.  The kernel won.  Which is the boot
    thesis confirmed from its ugly end: **the imperative list at the hot slot is
    what gets acted on; the prose contract beneath it is what gets skimmed.**
    """
    from brr.prompts import _build_orientation

    def actions(*, is_worker: bool) -> list[str]:
        return [
            s.action
            for s in _build_orientation(
                is_daemon=True,
                is_worker=is_worker,
                environment="worktree",
                pending_count=12,
                has_event_body=True,
            )
        ]

    assert not any("queued event" in a for a in actions(is_worker=True))
    # …and the resident still gets it: the fix is a gate, not a deletion.
    assert any("queued event" in a for a in actions(is_worker=False))


# ── Continuity ────────────────────────────────────────────────────────────────


def test_mount_failure_is_a_first_class_fact() -> None:
    """``✗`` is the load-bearing part; a mount that cannot fail is decoration."""
    assert "continuity: ✗ unreachable" in _kernel(
        continuity=BootContinuity(mount="✗ unreachable")
    )
    assert "continuity: ✗ first wake" in _kernel(
        continuity=BootContinuity(mount="✗ first wake")
    )


def test_no_brr_dir_is_unreachable_not_a_crash() -> None:
    """Continuity is an orientation aid; it must never take the wake down."""
    assert cont_mod.build_continuity(None).mount == "✗ unreachable"


def test_first_wake_when_no_prior_score(tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()
    assert cont_mod.build_continuity(tmp_path).mount == "✗ first wake"


def test_prior_wake_is_read_from_its_persisted_boot_score(tmp_path: Path) -> None:
    """The mount is already on disk — every wake since Slice 1 persists one."""
    runs = tmp_path / "runs"
    (runs / "run-260713-2251-ropg").mkdir(parents=True)
    (runs / "run-260713-2251-ropg" / "boot-score.json").write_text(
        json.dumps({"schema_version": "1"}), encoding="utf-8"
    )
    (runs / "run-260713-2331-qk3d").mkdir(parents=True)

    c = cont_mod.build_continuity(tmp_path, current_run_id="run-260713-2331-qk3d")
    assert c.mount == "✓"
    assert c.last_run == "run-260713-2251-ropg"


def test_unparseable_prior_score_is_a_broken_mount(tmp_path: Path) -> None:
    """A score that will not parse is not a ``✓``. Saying so is the honest move."""
    runs = tmp_path / "runs"
    (runs / "run-260713-2251-ropg").mkdir(parents=True)
    (runs / "run-260713-2251-ropg" / "boot-score.json").write_text(
        "{not json", encoding="utf-8"
    )
    assert cont_mod.build_continuity(tmp_path).mount == "✗ unreachable"


# ── Drift: rare and true, or worthless ────────────────────────────────────────


def _brr_with_prior_wake(tmp_path: Path) -> Path:
    """A ``.brr`` whose ``runs/`` holds one readable prior boot score.

    Drift tests must run against a ``✓`` mount, or they assert nothing: the
    early-return paths used to skip the drift check entirely, and a test that
    got its ``drift == ()`` from an early return was testing the early return.
    """
    brr_dir = tmp_path / ".brr"
    prior = brr_dir / "runs" / "run-260713-2251-ropg"
    prior.mkdir(parents=True)
    (prior / "boot-score.json").write_text(
        json.dumps({"schema_version": "1"}), encoding="utf-8"
    )
    return brr_dir


def _git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    (path / "seed").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-qm", "seed"], check=True
    )


def test_daemon_owned_run_state_is_not_drift(tmp_path: Path) -> None:
    """**The one that matters.**

    The dominion *always* carries one untracked file mid-wake:
    ``runs/<repo>/<run>/state.md``, written by the daemon at run start and
    committed by the capture net at run *end*.  Counting it meant ``drift: the
    capture net did not close`` would fire on **every wake, forever** — a
    permanent lie about a capture net that was working perfectly and simply had
    not run yet.

    Caught on the first live render.  A drift line that cries wolf gets skimmed
    by the third wake, and then gets skimmed the one time it is real.
    """
    brr_dir = _brr_with_prior_wake(tmp_path)
    dom = tmp_path / "dominion"
    _git_repo(dom)
    state = dom / "runs" / "Gurio__brr" / "run-260713-2331-qk3d" / "state.md"
    state.parent.mkdir(parents=True)
    state.write_text("live", encoding="utf-8")

    c = cont_mod.build_continuity(brr_dir, dominion_repo=dom)
    assert c.mount == "✓"          # exercise the real path, not an early return
    assert c.drift == ()


def test_delivery_mutated_message_records_are_not_drift(tmp_path: Path) -> None:
    """Post-capture delivery bookkeeping is the daemon's, not lost memory.

    The capture net commits at run end; the delivery pipeline then mutates the
    run's ``messages/*`` frontmatter (``status: pending → delivered``,
    ``platform_message_id``, ``delivered_at``) once the platform acks.  So any
    wake that delivered a reply left its message records *modified after* the
    capture commit — and counting them fired ``capture net did not close`` on
    three consecutive real wakes (260721–260722) about a net that had closed
    perfectly.  Same permanent-lie shape as ``state.md``, one seam later.
    """
    brr_dir = _brr_with_prior_wake(tmp_path)
    dom = tmp_path / "dominion"
    _git_repo(dom)
    msgs = dom / "runs" / "Gurio__brr" / "run-260722-0037-tqdp" / "messages"
    msgs.mkdir(parents=True)
    rec = msgs / "000001-outbound.md"
    rec.write_text("---\nstatus: pending\n---\nbody", encoding="utf-8")
    subprocess.run(["git", "-C", str(dom), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(dom), "commit", "-qm", "capture"], check=True)
    # The delivery pipeline's post-capture mutation:
    rec.write_text("---\nstatus: delivered\n---\nbody", encoding="utf-8")

    c = cont_mod.build_continuity(brr_dir, dominion_repo=dom)
    assert c.mount == "✓"          # exercise the real path, not an early return
    assert c.drift == ()


def test_uncommitted_resident_memory_is_drift(tmp_path: Path) -> None:
    """Real lost memory still fires — the check is scoped, not disabled."""
    brr_dir = _brr_with_prior_wake(tmp_path)
    dom = tmp_path / "dominion"
    _git_repo(dom)
    (dom / "notes.md").write_text("a thought nobody committed", encoding="utf-8")

    c = cont_mod.build_continuity(brr_dir, dominion_repo=dom)
    assert len(c.drift) == 1
    assert "capture net did not close" in c.drift[0]


def test_drift_is_reported_even_when_the_mount_fails(tmp_path: Path) -> None:
    """A failed mount is when drift matters *most*, not when it may be skipped.

    Drift used to be computed only after a ✓ mount, so a wake that could not
    find its predecessor also silently skipped the uncommitted-memory and
    rejected-push checks.  Found because a test written for something else was
    passing vacuously off the early return.
    """
    brr_dir = tmp_path / ".brr"
    (brr_dir / "runs").mkdir(parents=True)   # present but empty → first wake
    dom = tmp_path / "dominion"
    _git_repo(dom)
    (dom / "notes.md").write_text("memory nobody committed", encoding="utf-8")

    c = cont_mod.build_continuity(brr_dir, dominion_repo=dom)
    assert c.mount == "✗ first wake"
    assert len(c.drift) == 1

    # …and it must reach the rendered kernel, not just the IR.
    assert "drift:" in format_kernel(BootScore(continuity=c))


def test_shipped_only_counts_merges_after_the_last_wake() -> None:
    """``shipped`` is the world saying *you did this* — not a list of all PRs."""
    prs = [
        {"number": 386, "state": "MERGED", "merged_at": "2026-07-13T23:20:00Z"},
        {"number": 300, "state": "MERGED", "merged_at": "2026-07-01T10:00:00Z"},
        {"number": 999, "state": "OPEN"},
    ]
    # Cutoff parsed with the same parser production uses, so the test cannot
    # drift from the daemon's notion of time.
    from brr import forge_pr_cache

    cutoff = forge_pr_cache.parse_iso("2026-07-13T23:00:00Z")
    assert cont_mod._merged_since(prs, cutoff) == ("#386",)


# ── The stale image: a boot that knows it may be lying ────────────────────────


def test_stale_image_is_announced_in_the_kernel() -> None:
    """A spawn assembled by a superseded daemon says so, first thing.

    The 2026-07-13 failure: two children rendered the pre-#388 kernel — the
    worker-queue bug included — *after* the fix was in the tree, because the
    daemon assembles a spawn's whole prompt in its own process image and the
    re-exec that would refresh it waits on the resident doing the spawning.
    Nothing in either child's wake said so, so the floor measurement read as a
    verdict on the new boot when it was a verdict on the old one.
    """
    out = _kernel(host=BootHost(kind="daemon", environment="worktree", image_stale=True))
    lines = out.splitlines()
    host_i = next(i for i, ln in enumerate(lines) if ln.startswith("host:"))

    # Directly under `host:` — it is a fact about the host's image, and it is
    # above `next:`, so it cannot be reached by acting on the list first.
    stale = lines[host_i + 1]
    assert stale.startswith("  stale: ⚠"), out
    assert "superseded" in stale
    # Names *what* is stale. "The boot is stale" would send a reader to re-read
    # the prose, which is the one part that is always current.
    assert ".md is current" in stale
    assert "code is NOT" in stale


def test_healthy_image_costs_the_kernel_nothing() -> None:
    """Differential, like every other line here: zero bytes on a healthy wake.

    A warning that renders unconditionally is decoration, and a kernel that pays
    for it every wake has reintroduced exactly the always-true prose the kernel
    was built to evict.
    """
    out = _kernel(host=BootHost(kind="daemon", environment="host"))
    assert "stale:" not in out
    assert "⚠" not in out


# ── P1 — per-block content attestation, the kernel alarm (move 4a) ────────────
#
# review-boot-prompts-2026-07.md §P1: a trimmed block that kept an
# out-of-order "newest" entry used to render full and read as current.
# `attest_blocks` (bootscore.py) is the deterministic, no-model-in-the-loop
# check; these pin its one rendering site, modelled directly on
# `image_stale` above.


def _stale_ledger_entry(**overrides) -> ContractEntry:
    fields = dict(
        block_key="work-surface",
        label="Discovered work surface",
        owner="resident",
        authority="surface",
        freshness=None,
        location="computed",
        present=True,
        newest_item="2026-07-22",
        source_newest="2026-07-23",
        dropped=2,
        stale=True,
    )
    fields.update(overrides)
    return ContractEntry(**fields)


def test_stale_block_is_announced_in_the_kernel() -> None:
    """The ledger-tail-inversion class, surfaced where it can't be skimmed.

    Modelled on ``image_stale``: differential, costs nothing healthy, and on
    a stale wake is among the first things read.
    """
    out = _kernel(
        host=BootHost(kind="daemon", environment="worktree"),
        contracts=[_stale_ledger_entry()],
    )
    lines = [ln for ln in out.splitlines() if ln.startswith("attest:")]
    assert len(lines) == 1, out
    line = lines[0]
    assert "⚠" in line
    assert "Discovered work surface" in line
    assert "2026-07-22" in line
    assert "2026-07-23" in line
    assert "trimmed" in line


def test_healthy_blocks_cost_the_kernel_nothing() -> None:
    """No block is stale (the common case) → no ``attest:`` line at all."""
    out = _kernel(
        host=BootHost(kind="daemon", environment="worktree"),
        contracts=[_stale_ledger_entry(stale=False, newest_item=None, source_newest=None, dropped=None)],
    )
    assert "attest:" not in out


def test_undated_or_untrimmed_blocks_never_fire_the_alarm() -> None:
    """``stale=False`` is the default — every non-chronological block, and
    every not-attestable trim (undated headings), renders no alarm line.
    """
    out = _kernel(
        host=BootHost(kind="daemon", environment="worktree"),
        contracts=[ContractEntry(
            block_key="identity-core", label="Resident identity core",
            owner="product", authority="identity", freshness=None,
            location="computed", present=True,
        )],
    )
    assert "attest:" not in out


def test_attest_blocks_is_silent_when_nothing_is_stale() -> None:
    """Zero findings → an empty list, like every other deterministic preflight."""
    from brr.bootscore import attest_blocks

    assert attest_blocks([_stale_ledger_entry(stale=False, newest_item=None, source_newest=None)]) == []
    assert attest_blocks([]) == []


def test_attest_blocks_names_the_block_and_both_dates() -> None:
    from brr.bootscore import attest_blocks

    findings = attest_blocks([_stale_ledger_entry()])
    assert len(findings) == 1
    assert "Discovered work surface" in findings[0]
    assert "2026-07-22" in findings[0]
    assert "2026-07-23" in findings[0]


# ── The orientation ledger's score half (#513 Slice 9) ────────────────────────
#
# `orientation_set` (files a wake ought to READ, metered by the hooks as
# `orient x/y`) coexists with `orientation` (the kernel's `next:` action
# list). They are two halves of one steer — the list is what a wake does
# first, the set is what it inhabits by reading — and the tests below pin
# them apart so the naming collision this slice inherited cannot regrow.


def test_orientation_set_and_next_actions_are_distinct_kernel_blocks() -> None:
    from brr.bootscore import OrientationFile, OrientationStep

    kernel = _kernel(
        orientation=[OrientationStep(action="act", reason="go")],
        orientation_set=[
            OrientationFile(path="/repo/AGENTS.md", bytes=4120),
            OrientationFile(path="/home/kb/subject-envs.md", bytes=9801),
        ],
    )
    # The walk: named files, byte costs, and the skip declared as first-class.
    assert "orient: 2 file(s) · 13,921B" in kernel
    assert "  · /repo/AGENTS.md (4,120B)" in kernel
    assert "  · /home/kb/subject-envs.md (9,801B)" in kernel
    assert "skipping orientation" in kernel
    # The next-actions list is still its own block, untouched by the set.
    assert "next:" in kernel
    assert "  1. act — go" in kernel
    # The orient block precedes next: — posture, then the walk, then actions.
    assert kernel.index("orient:") < kernel.index("next:")


def test_empty_orientation_set_costs_the_kernel_nothing() -> None:
    from brr.bootscore import OrientationStep

    # Differential like every kernel line — and this negative can fail: the
    # positive twin above proves this same renderer emits `orient:` when the
    # set is non-empty.
    kernel = _kernel(orientation=[OrientationStep(action="act")])
    assert "orient:" not in kernel


def test_orientation_set_rides_to_dict() -> None:
    from brr.bootscore import OrientationFile, to_dict

    score = BootScore(
        orientation_set=[OrientationFile(path="/repo/AGENTS.md", bytes=7)]
    )
    assert to_dict(score)["orientation_set"] == [
        {"path": "/repo/AGENTS.md", "bytes": 7}
    ]


def test_orientation_set_names_only_provable_files(tmp_path: Path) -> None:
    from brr import prompts

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text("", encoding="utf-8")

    # Nothing provable → an empty set, never a padded one.
    assert prompts._build_orientation_set(repo) == []

    # An AGENTS.md that exists enters, with its true byte cost.
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    entries = prompts._build_orientation_set(repo)
    assert [Path(e.path).name for e in entries] == ["AGENTS.md"]
    assert entries[0].bytes == len("# Agents\n")

    # An empty file orients nobody and is excluded — the meter must never
    # ask for a Read with no reading.
    (repo / "AGENTS.md").write_text("", encoding="utf-8")
    assert prompts._build_orientation_set(repo) == []


def test_agents_md_leaves_the_walk_for_a_shell_that_already_read_it(
    tmp_path: Path,
) -> None:
    """codex holds ``AGENTS.md`` natively; the walk must not bill it for a Read.

    ``run.md`` has always said the file is Shell-dependent — *"some Shells read
    it natively (codex), others don't (claude)"* — while the set named it for
    every Shell alike.  On this repo ``AGENTS.md`` is 33 KB of a 38–64 KB set,
    so on codex the meter's largest entry was a file already in context: the
    polling tax the identity core names, charged by the instrument built to
    make orientation honest.
    """
    from brr import prompts

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")

    def names(shell: str | None) -> list[str]:
        return [
            Path(e.path).name
            for e in prompts._build_orientation_set(repo, runner_shell=shell)
        ]

    assert names("codex") == []
    assert names("codex exec --sandbox danger-full-access") == []

    # Every other Shell still walks it, and so does an unknown one: a walk
    # entry for a file already in context costs one redundant Read, a missing
    # entry for a file nobody read costs the orientation.  The cheap error is
    # the one to make.
    assert names("claude") == ["AGENTS.md"]
    assert names(None) == ["AGENTS.md"]
    assert names("") == ["AGENTS.md"]
    assert names("some-future-shell") == ["AGENTS.md"]


def test_shell_conditional_removes_only_agents_md(tmp_path: Path, monkeypatch) -> None:
    """The rest of the walk is Shell-independent and must stay.

    Guards the obvious over-correction: a Shell that reads one file natively
    has not read the plan or the kb hubs, and a codex wake that lost its whole
    orientation set would look exactly like this feature working.
    """
    from brr import prompts

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "subject-boot-sequence.md").write_text("# hub\n", encoding="utf-8")
    monkeypatch.setattr(
        prompts, "_home_knowledge_log_path", lambda _root: kb / "log.md"
    )

    entries = prompts._build_orientation_set(
        repo, task_text="fix the boot sequence meter", runner_shell="codex"
    )
    assert [Path(e.path).name for e in entries] == ["subject-boot-sequence.md"]


def test_build_boot_score_threads_the_shell_into_the_walk(tmp_path: Path) -> None:
    """The conditional is worthless if the caller never passes the Shell.

    The score already knew which body it was in; the set simply was not asked.
    Pinned end to end through the production builder so the wiring cannot be
    quietly dropped while the unit above stays green.
    """
    from brr import prompts

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text("", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")

    claude = prompts.build_boot_score(repo, is_daemon=True, runner_shell="claude")
    codex = prompts.build_boot_score(repo, is_daemon=True, runner_shell="codex")

    assert [Path(e.path).name for e in claude.orientation_set] == ["AGENTS.md"]
    assert codex.orientation_set == []


def test_touched_subject_hub_requires_every_slug_token(
    tmp_path: Path, monkeypatch,
) -> None:
    from brr import prompts

    repo = tmp_path / "repo"
    repo.mkdir()
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "subject-boot-sequence.md").write_text("# hub\n", encoding="utf-8")
    (kb / "subject-envs.md").write_text("# hub\n", encoding="utf-8")
    monkeypatch.setattr(
        prompts, "_home_knowledge_log_path", lambda _root: kb / "log.md"
    )

    # Both slug tokens present → the hub is provably touched.
    touched = prompts._build_orientation_set(
        repo, task_text="fix the boot sequence meter"
    )
    assert [Path(e.path).name for e in touched] == ["subject-boot-sequence.md"]

    # One token alone is a guess wearing a match's clothes — excluded.
    partial = prompts._build_orientation_set(
        repo, task_text="the boot kernel line"
    )
    assert partial == []

    # No task text → no hub can be *touched*, so none is named.
    assert prompts._build_orientation_set(repo) == []


def test_orientation_set_is_capped_never_padded(
    tmp_path: Path, monkeypatch,
) -> None:
    from brr import prompts

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    kb = tmp_path / "kb"
    kb.mkdir()
    for i in range(7):
        (kb / f"subject-boot-{i}.md").write_text("# hub\n", encoding="utf-8")
    monkeypatch.setattr(
        prompts, "_home_knowledge_log_path", lambda _root: kb / "log.md"
    )

    entries = prompts._build_orientation_set(
        repo, task_text="boot 0 1 2 3 4 5 6"
    )
    assert len(entries) == prompts._ORIENTATION_SET_MAX
    # Deterministic order: AGENTS.md first, then hubs in sorted-name order.
    assert [Path(e.path).name for e in entries] == [
        "AGENTS.md", "subject-boot-0.md", "subject-boot-1.md",
        "subject-boot-2.md", "subject-boot-3.md",
    ]


def test_build_boot_score_carries_the_orientation_set(tmp_path: Path) -> None:
    from brr import prompts

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text("", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")

    score = prompts.build_boot_score(repo, is_daemon=True, task_text="任务")
    assert [Path(e.path).name for e in score.orientation_set] == ["AGENTS.md"]


def test_rendered_kernel_names_every_file_the_persisted_score_meters(
    tmp_path: Path, monkeypatch,
) -> None:
    """The walk the wake is *told* to do == the walk the meter counts.

    Two ``build_boot_score`` calls back one wake: the kernel's (inside
    :func:`build_daemon_prompt`) and the persisted one (returned by
    :func:`build_daemon_prompt_with_score`, dumped to ``boot-score.json`` and
    read by ``hooks._orientation_progress`` as the ``orient x/y`` denominator).
    Until 2026-07-24 only the second was given ``task_text``, so a wake whose
    task touched a ``subject-*.md`` hub got a kernel naming N files and a meter
    counting N+k — unreachable hubs, and a meter no compliance could clear.

    Pinned on the *rendered text*, not on a second call to the builder: the
    kernel is the only surface that asks for a Read, so the assertion has to
    be about what it actually says.
    """
    from brr import prompts

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text("", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")

    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "subject-boot-sequence.md").write_text("# hub\n", encoding="utf-8")
    monkeypatch.setattr(
        prompts, "_home_knowledge_log_path", lambda _root: kb / "log.md"
    )

    prompt, score = prompts.build_daemon_prompt_with_score(
        "fix the boot sequence meter",
        "evt-orient-001",
        "/tmp/response.md",
        repo,
        runner_shell="claude",
        environment="worktree",
    )

    # The hub was selected — otherwise this test proves nothing about drift.
    assert [Path(e.path).name for e in score.orientation_set] == [
        "AGENTS.md", "subject-boot-sequence.md",
    ]
    # …and every selected file is named in the text the wake reads.
    for entry in score.orientation_set:
        assert entry.path in prompt, f"kernel never names {entry.path}"
    assert f"orient: {len(score.orientation_set)} file(s)" in prompt
