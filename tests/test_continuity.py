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
        ln for ln in out.splitlines() if ln.lstrip().startswith("body:")
    )
    assert "req-origin←dashboard spool rack" in body_line


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
    att = next(ln for ln in out.splitlines() if ln.lstrip().startswith("attention:"))
    assert "←telegram" in att
    # The exact shape of the original bug: the runner note leaking onto the
    # attention line, where it asserted a falsehood in the wake's hottest slot.
    assert "spool rack" not in att


# ── Wake age and retry provenance (#1491) ──────────────────────────────────
#
# A run couldn't tell a six-hour-old message from a live one: the wake
# carried no event timestamp anywhere. #1491's own measurement — an
# `evt-…-bpgk` sent 14:58:06Z, woken at 21:04:03Z (6h06m) — is used verbatim
# below as the canonical stale case.


def test_attention_line_stale_event_renders_age_and_soft_language() -> None:
    """The regression this issue names, pinned at the kernel: past the
    threshold the attention line must not leave the reader to do
    arithmetic, and must say *why* in words a skim won't miss."""
    out = _kernel(
        attention=BootAttention(
            event_ids=("evt-1787065086940542000-bpgk",),
            source_gate="telegram",
            created="2026-08-18T14:58:06Z",
            age_seconds=6 * 3600 + 6 * 60,
        ),
    )
    att = next(ln for ln in out.splitlines() if ln.lstrip().startswith("attention:"))
    assert "←telegram" in att
    assert "sent 2026-08-18T14:58:06Z" in att
    assert "6h06m ago" in att
    assert "daemon was only asleep" in att


def test_attention_line_fresh_event_stays_quiet() -> None:
    """The common path: a fresh event renders neither an elapsed age nor
    the threshold wording — the stamp alone is enough."""
    out = _kernel(
        attention=BootAttention(
            event_ids=("evt-fresh",),
            source_gate="telegram",
            created="2026-08-18T21:33:00Z",
            age_seconds=12.0,
        ),
    )
    att = next(ln for ln in out.splitlines() if ln.lstrip().startswith("attention:"))
    assert "sent 2026-08-18T21:33:00Z" in att
    assert "ago" not in att
    assert "asleep" not in att


def test_attention_line_missing_created_degrades_quietly() -> None:
    """No ``created`` (an ad-hoc/test caller, an event predating this
    field) renders exactly like today's output — no age line, no crash."""
    out = _kernel(
        attention=BootAttention(event_ids=("evt-x",), source_gate="telegram"),
    )
    att = next(ln for ln in out.splitlines() if ln.lstrip().startswith("attention:"))
    assert att.strip() == "attention: evt-x ←telegram"


def test_attention_line_names_a_carry_beside_the_inherited_source() -> None:
    """2026-09-23: a seat minted by its predecessor's ``halt:`` woke to
    ``attention: evt-… ←spawn_completed`` — the source a carry inherits by
    construction — and spent its first minutes looking for a strand that had
    finished. The speaker on a carry is the halted seat; the line says so."""
    out = _kernel(
        attention=BootAttention(
            event_ids=("evt-1790144370611246000-9gal",),
            source_gate="spawn_completed",
            carry_of="run-260923-0008-88ed",
            carry_reason="the scroll is at ~310k tokens after six hours",
        ),
    )
    att = next(ln for ln in out.splitlines() if ln.lstrip().startswith("attention:"))
    assert "←spawn_completed" in att
    assert "carry ⇐ run-260923-0008-88ed (the scroll is at ~310k tokens after six hours)" in att


def test_attention_line_renders_retry_provenance() -> None:
    out = _kernel(
        attention=BootAttention(
            event_ids=("evt-1787065086940542000-bpgk",),
            source_gate="telegram",
            retry_of="run-260818-1834-lcu3",
            retry_failure_kind="host_interrupted",
        ),
    )
    att = next(ln for ln in out.splitlines() if ln.lstrip().startswith("attention:"))
    assert "retry of run-260818-1834-lcu3 (host interrupt)" in att


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

    ``strand.md`` says the spawning conversation "is not yours to hold or
    extend" — in prose, *below* the kernel.  The kernel won.  Which is the boot
    thesis confirmed from its ugly end: **the imperative list at the hot slot is
    what gets acted on; the prose contract beneath it is what gets skimmed.**
    """
    from brr.bootscore import BootPosture

    def kernel(*, is_strand: bool) -> str:
        return _kernel(posture=BootPosture(pending_count=12, is_strand=is_strand))

    assert "pending" not in kernel(is_strand=True)
    assert "the return message on stdout (to the parent" in kernel(is_strand=True)
    # …and the resident still gets it: the fix is a gate, not a deletion.
    assert "pending×12" in kernel(is_strand=False)
    assert "op⇐reply owed" in kernel(is_strand=False)


# ── Continuity ────────────────────────────────────────────────────────────────


def test_mount_failure_is_a_first_class_fact() -> None:
    """``✗`` is the load-bearing part; a mount that cannot fail is decoration."""
    assert "continuity: ✗ unreachable" in _kernel(
        continuity=BootContinuity(mount="✗ unreachable")
    )
    assert "continuity: ✗ first wake" in _kernel(
        continuity=BootContinuity(mount="✗ first wake")
    )


def _score(source_gate: str | None = None) -> str:
    """A boot score as ``to_dict`` writes it, carrying only what the picker reads.

    ``source_gate`` left ``None`` is the pre-#987 shape *and* the shape of any
    wake whose event had no source: the ``attention`` block is present and the
    field is null.  Both must read as "an ordinary run", never as "skip it".
    """
    return json.dumps(
        {
            "schema_version": "1",
            "attention": {"event_ids": ["evt-x"], "source_gate": source_gate},
        }
    )


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


# ── #1140: a stacked merge is not "shipped" ─────────────────────────────────


def _pr(number, base=None, merged_at="2026-07-13T23:20:00Z") -> dict:
    return {"number": number, "state": "MERGED", "merged_at": merged_at, "base": base}


def test_build_continuity_splits_shipped_and_stacked(tmp_path: Path) -> None:
    """End-to-end: ``build_continuity`` itself carries the split through to
    :class:`BootContinuity`, not just the two private helpers."""
    import time

    brr_dir = _brr_with_prior_wake(tmp_path)
    # ``since`` is the prior score file's real mtime — merges must postdate
    # it, so stamp them off the clock rather than a fixed literal.
    score_mtime = (brr_dir / "runs" / "run-260713-2251-ropg" / "boot-score.json").stat().st_mtime
    merged_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(score_mtime + 60))
    prs = [
        _pr(1139, base="brr/the-child-speaks-for-itself", merged_at=merged_at),
        _pr(1134, base="main", merged_at=merged_at),
    ]
    c = cont_mod.build_continuity(brr_dir, prs=prs, default_branch="main")
    assert c.shipped == ("#1134",)
    assert c.stacked == ("#1139 (→ brr/the-child-speaks-for-itself)",)

    # The rendered kernel carries both words, distinctly.
    from brr.bootscore import BootScore, format_kernel

    rendered = format_kernel(BootScore(continuity=c))
    assert "shipped #1134" in rendered
    assert "stacked #1139 (→ brr/the-child-speaks-for-itself)" in rendered


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
    host_i = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith("host:"))

    # Directly under `host:` — it is a fact about the host's image, and it is
    # above `next:`, so it cannot be reached by acting on the list first.
    stale = lines[host_i + 1]
    assert stale.lstrip().startswith("stale: ⚠"), out
    assert "superseded" in stale
    # Names *what* is stale. "The boot is stale" would send a reader to re-read
    # the prose, which is the one part that is always current.
    assert ".md is current" in stale
    assert "code is NOT" in stale


def test_healthy_image_never_renders_the_stale_warning() -> None:
    """The drift warning stays differential even though #822 made its neighbour
    unconditional (see the two tests below): `stale:` / `⚠` only cost a byte
    when the image has actually moved.
    """
    out = _kernel(
        host=BootHost(
            kind="daemon",
            environment="host",
            image_digest="8f3a91c2ab",
            image_captured_at="2026-07-27T16:32:59Z",
        )
    )
    assert "stale:" not in out
    assert "⚠" not in out


# ── #822 — a current image says so, not just a stale one ──────────────────────
#
# The gap the ticket names: `image_stale` collapsed "verified current" and
# "never checked" into the same silent `False`, so three ticks answered "is
# the daemon on my merge?" from memory because nothing distinguished them.
# These pin the three mutually exclusive states the `daemon image:` line
# renders — current / not tracked / (stale, covered above).


def test_current_image_is_announced_in_the_kernel() -> None:
    """A captured, unmoved fingerprint renders a positive fact, not silence."""
    out = _kernel(
        host=BootHost(
            kind="daemon",
            environment="worktree",
            image_digest="8f3a91c2ab",
            image_captured_at="2026-07-27T16:32:59Z",
        )
    )
    lines = out.splitlines()
    host_i = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith("host:"))

    # Same position as `stale:` — directly under `host:`, above `next:` — so a
    # reader who has learned to check the first line after `host:` gets the
    # answer either way, instead of only when it's bad news.
    current = lines[host_i + 1]
    assert current.lstrip().startswith("image: current"), out
    assert "8f3a91c2ab" in current
    assert "2026-07-27T16:32:59Z" in current
    assert "stale:" not in out


def test_untracked_image_is_not_the_same_word_as_current() -> None:
    """"Cannot be stale" (an ad-hoc run) is not "verified current" — #822's ask.

    Rendering a never-captured fingerprint as `current` would recreate the
    exact defect this ticket exists to kill, one layer over.
    """
    out = _kernel(host=BootHost(kind="ad-hoc"))
    lines = out.splitlines()
    host_i = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith("host:"))

    line = lines[host_i + 1].strip()
    assert line == (
        "image: untracked · fingerprint∅ (none captured in this process)"
    ), out
    assert "current" not in line


def test_stale_line_wins_over_the_current_line() -> None:
    """A drifted image is stale, not "current with a warning attached" —
    the two lines are mutually exclusive, never both rendered."""
    out = _kernel(
        host=BootHost(
            kind="daemon",
            environment="worktree",
            image_stale=True,
            image_digest="deadbeef01",
            image_captured_at="2026-07-27T16:32:59Z",
        )
    )
    lines = out.splitlines()
    host_i = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith("host:"))

    assert lines[host_i + 1].lstrip().startswith("stale: ⚠"), out
    assert "image: current" not in out
    assert "untracked" not in out


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
    lines = [ln for ln in out.splitlines() if ln.lstrip().startswith("attest:")]
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


def test_orientation_files_render_as_reach_not_as_a_row() -> None:
    # v2 (the boot lobotomy): the walk's files render under `reach:` in the
    # `::receive` beat — full absolute paths, byte costs intact — as a fact
    # of what the wake did not carry, never as a row the resident owes.
    from brr.bootscore import OrientationFile

    kernel = _kernel(
        orientation_set=[
            OrientationFile(path="/repo/AGENTS.md", bytes=4120),
            OrientationFile(path="/home/kb/subject-envs.md", bytes=9801),
        ],
    )
    assert "reach: files×2 · 13,921B ∉ wake" in kernel
    assert "· /repo/AGENTS.md · 4,120B" in kernel
    assert "· /home/kb/subject-envs.md · 9,801B" in kernel
    assert "assignments:" not in kernel
    assert __import__("re").search(r"↗\d+b", kernel) is None  # no escalation windows
    # The retired blocks stay retired.
    assert "next:" not in kernel
    assert "orient:" not in kernel


def test_orientation_set_rides_to_dict() -> None:
    from brr.bootscore import OrientationFile, to_dict

    score = BootScore(
        orientation_set=[OrientationFile(path="/repo/AGENTS.md", bytes=7)]
    )
    assert to_dict(score)["orientation_set"] == [
        {"path": "/repo/AGENTS.md", "bytes": 7}
    ]


# ── #628 — the walk must name only what the wake was NOT already handed ──
#
# The active plan is injected *whole* by the ``## Work surface`` block
# (`_build_work_surface_block_scored`) earlier in the same prompt, yet
# `_build_orientation_set` used to list it unconditionally — asking the wake
# to Read a file it was already holding. After #625 made `AGENTS.md`
# Shell-conditional, the plan was the walk's only remaining entry on codex,
# so the whole codex walk was billing a file already in context. Fixed by
# threading the work-surface block's own "what did I actually hand over
# whole" fact (`injected_whole`) into the set as a subtraction — never a
# structural exclusion, so a plan the surface block trimmed or skipped for
# budget reasons still gets walked (see the budget-exhausted test below).


def _seed_account_home_for_orientation(
    tmp_path: Path, *, extra_config: str = "",
) -> Path:
    """Seed a minimal account dominion home for orientation-set tests.

    ``repo.label=local/default`` makes the plan slug ``local__default``
    predictable, mirroring ``tests/test_prompts.py::_seed_account_home`` —
    duplicated rather than imported, so this file's fixtures stay
    self-contained.
    """
    home = tmp_path / "acct-home"
    home.mkdir(parents=True)
    (tmp_path / ".brr").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".brr" / "config").write_text(
        f"home.path={home}\nrepo.label=local/default\n{extra_config}",
        encoding="utf-8",
    )
    return home


def test_workflow_reserve_rescues_the_old_starvation_fixture(tmp_path: Path) -> None:
    """The measured defect (#1061) fixed at the source, proven with the exact
    fixture that used to reproduce it.

    Formerly ``test_dropped_workflow_page_is_named_in_the_walk``: ``aaa.md``
    sorts before ``workflow.md`` and, whole and untrimmed itself, used to eat
    the entire (tiny) shared budget before the walk ever reached the
    contract page — measured four wakes running on this account, each of
    them acting on remembered merge permissions. #1061's named reserve
    (``prompts._SURFACE_RESERVE_PAGE_BYTES``) now charges ``workflow.md``'s
    floor *before* the alphabetical walk starts, so this exact scenario no
    longer starves it — it rides whole, and the orientation-set fallback
    this test used to require is correctly silent (#628: already handed over
    whole must not also be billed as a walk entry). The one case the reserve
    genuinely cannot rescue — a page bigger than its own floor *and* the
    whole budget — is
    ``test_workflow_too_big_for_the_reserve_still_names_the_walk`` below.
    """
    from brr import prompts
    from brr import account as acc_mod
    from brr import config as conf_mod

    home = _seed_account_home_for_orientation(
        tmp_path, extra_config="dominion.surface_inject_budget_bytes=200\n",
    )
    surface = home / "surface"
    surface.mkdir(exist_ok=True)
    (surface / "aaa.md").write_text("x" * 180, encoding="utf-8")
    (surface / "workflow.md").write_text(
        "# Workflow\n\nself-merge conditions 1-3\n", encoding="utf-8",
    )

    trim, whole = prompts._build_work_surface_block_scored(tmp_path)
    cfg = conf_mod.load_config(tmp_path)
    ctx = acc_mod.resolve_context(tmp_path, cfg, create=False)
    workflow = (acc_mod.work_surface_path(ctx) / "workflow.md").resolve()

    # The fixture's old claim inverted: the reserve now hands it over whole.
    assert workflow in whole
    assert "self-merge conditions" in trim.text

    entries = prompts._build_orientation_set(
        tmp_path, runner_shell="claude", injected_whole=whole,
    )
    assert workflow not in {Path(e.path) for e in entries}, (
        "already injected whole by the reserve — naming it again in the "
        "walk would bill the wake for a Read of a file it already holds"
    )


def test_workflow_too_big_for_the_reserve_still_names_the_walk(tmp_path: Path) -> None:
    """The one case #1061's reserve cannot rescue: a page bigger than both
    its own floor and the entire shared budget. The surface block still
    can't hand it over whole, so the orientation-set fallback (#628) must
    still name it — the invariant the pre-reserve starvation fixture used
    to guard, now reproduced with a fixture the reserve genuinely can't fit.
    """
    from brr import prompts
    from brr import account as acc_mod
    from brr import config as conf_mod

    home = _seed_account_home_for_orientation(
        tmp_path, extra_config="dominion.surface_inject_budget_bytes=200\n",
    )
    surface = home / "surface"
    surface.mkdir(exist_ok=True)
    (surface / "workflow.md").write_text(
        "# Workflow\n\n" + ("w" * 10_000), encoding="utf-8",
    )

    trim, whole = prompts._build_work_surface_block_scored(tmp_path)
    cfg = conf_mod.load_config(tmp_path)
    ctx = acc_mod.resolve_context(tmp_path, cfg, create=False)
    workflow = (acc_mod.work_surface_path(ctx) / "workflow.md").resolve()

    # The fixture's own claim: even the reserve's floor can't fit this.
    assert workflow not in whole

    entries = prompts._build_orientation_set(
        tmp_path, runner_shell="claude", injected_whole=whole,
    )
    assert workflow in {Path(e.path) for e in entries}, (
        "the contract page still couldn't ride whole and must be named in "
        "the walk — otherwise the wake never learns it went missing"
    )


def test_workflow_page_injected_whole_leaves_the_walk(tmp_path: Path) -> None:
    """The other half, and the one a blanket "always name it" would break.

    A surface with room hands ``workflow.md`` over whole; naming it again in
    the walk would bill the wake for a Read of a file it is already holding —
    the #628 subtraction, applied to the page #1061 is about. Without this
    the fix trades one wasted budget for another and the guard above would
    still be green.
    """
    from brr import prompts
    from brr import account as acc_mod
    from brr import config as conf_mod

    home = _seed_account_home_for_orientation(tmp_path)
    surface = home / "surface"
    surface.mkdir(exist_ok=True)
    (surface / "workflow.md").write_text(
        "# Workflow\n\nself-merge conditions 1-3\n", encoding="utf-8",
    )

    trim, whole = prompts._build_work_surface_block_scored(tmp_path)
    cfg = conf_mod.load_config(tmp_path)
    ctx = acc_mod.resolve_context(tmp_path, cfg, create=False)
    workflow = (acc_mod.work_surface_path(ctx) / "workflow.md").resolve()

    assert workflow in whole
    assert "self-merge conditions" in trim.text

    entries = prompts._build_orientation_set(
        tmp_path, runner_shell="claude", injected_whole=whole,
    )
    assert workflow not in {Path(e.path) for e in entries}


def test_active_plan_reserve_rescues_the_old_starvation_fixture(tmp_path: Path) -> None:
    """The load-bearing case option 1 (structural exclusion) would break —
    proven with the exact fixture that used to need the walk fallback.

    Formerly ``test_budget_exhausted_plan_stays_in_the_walk``: ``aaa.md``
    sorts before ``plans/...`` and, whole and untrimmed itself, used to
    consume nearly the entire (tiny) shared budget before the walk reached
    the plan. #1061's named reserve now charges the plan's floor before the
    alphabetical walk starts, so this exact scenario no longer starves it —
    it rides whole, and #628's subtraction correctly keeps it out of the
    orientation-set fallback. The one case the reserve genuinely cannot
    rescue is
    ``test_active_plan_too_big_for_the_reserve_still_names_the_walk`` below.
    """
    from brr import prompts
    from brr import account as acc_mod
    from brr import config as conf_mod

    home = _seed_account_home_for_orientation(
        tmp_path, extra_config="dominion.surface_inject_budget_bytes=200\n",
    )
    surface = home / "surface"
    surface.mkdir(exist_ok=True)
    # Sorts before "plans/..." (a < p) — used to consume nearly the entire
    # (tiny) shared budget before the walk ever reached the plan.
    (surface / "aaa.md").write_text("x" * 180, encoding="utf-8")
    plan_dir = surface / "plans" / "local__default"
    plan_dir.mkdir(parents=True)
    (plan_dir / "active.md").write_text("# Plan\n\nship it\n", encoding="utf-8")

    trim, whole = prompts._build_work_surface_block_scored(tmp_path)
    cfg = conf_mod.load_config(tmp_path)
    ctx = acc_mod.resolve_context(tmp_path, cfg, create=False)
    plan_path = acc_mod.active_plan_path(ctx, "local/default").resolve()

    # The fixture's old claim inverted: the reserve now hands it over whole.
    assert plan_path in whole
    assert "ship it" in trim.text

    entries = prompts._build_orientation_set(
        tmp_path, runner_shell="claude", injected_whole=whole,
    )
    assert plan_path not in {Path(e.path) for e in entries}, (
        "already injected whole by the reserve — naming it again in the "
        "walk would bill the wake for a Read of a file it already holds"
    )


def test_active_plan_too_big_for_the_reserve_still_names_the_walk(tmp_path: Path) -> None:
    """The one case #1061's reserve cannot rescue: a plan bigger than both
    its own floor and the entire shared budget still needs the
    orientation-set fallback (#628) — the invariant the pre-reserve
    starvation fixture used to guard, reproduced with a fixture the reserve
    genuinely can't fit.
    """
    from brr import prompts
    from brr import account as acc_mod
    from brr import config as conf_mod

    home = _seed_account_home_for_orientation(
        tmp_path, extra_config="dominion.surface_inject_budget_bytes=200\n",
    )
    surface = home / "surface"
    surface.mkdir(exist_ok=True)
    plan_dir = surface / "plans" / "local__default"
    plan_dir.mkdir(parents=True)
    (plan_dir / "active.md").write_text(
        "# Plan\n\n" + ("p" * 10_000), encoding="utf-8",
    )

    trim, whole = prompts._build_work_surface_block_scored(tmp_path)
    cfg = conf_mod.load_config(tmp_path)
    ctx = acc_mod.resolve_context(tmp_path, cfg, create=False)
    plan_path = acc_mod.active_plan_path(ctx, "local/default").resolve()

    # The fixture's own claim: even the reserve's floor can't fit this.
    assert plan_path not in whole

    entries = prompts._build_orientation_set(
        tmp_path, runner_shell="claude", injected_whole=whole,
    )
    assert plan_path in {Path(e.path) for e in entries}
