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
