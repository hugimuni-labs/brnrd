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

    ``strand.md`` says the spawning conversation "is not yours to hold or
    extend" — in prose, *below* the kernel.  The kernel won.  Which is the boot
    thesis confirmed from its ugly end: **the imperative list at the hot slot is
    what gets acted on; the prose contract beneath it is what gets skimmed.**
    """
    from brr.prompts import _build_orientation

    def actions(*, is_strand: bool) -> list[str]:
        return [
            s.action
            for s in _build_orientation(
                is_daemon=True,
                is_strand=is_strand,
                environment="worktree",
                pending_count=12,
                has_event_body=True,
            )
        ]

    assert not any("queued event" in a for a in actions(is_strand=True))
    # …and the resident still gets it: the fix is a gate, not a deletion.
    assert any("queued event" in a for a in actions(is_strand=False))


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


def test_a_spawned_worker_is_not_the_prior_wake(tmp_path: Path) -> None:
    """#987, the live case, pinned.

    ``run-260802-0730-cc2f`` booted with ``continuity: ✓ run-260802-0649-lcrd``
    — a codex worker ``run-260802-0632-v2ir`` had dispatched — while its own
    ``## Your last run`` block named ``v2ir``.  Two blocks in one prompt, two
    answers to *where was I*, and the anchor whose whole job is closing the loop
    across wakes was the one naming a child.

    A subspawn is not a previous self: different task, often a different Shell
    and Core, on a thread the parent never joined.  Newest-first is the right
    order; it just has to be newest-first *on this resident's line*.
    """
    runs = tmp_path / "runs"
    (runs / "run-260802-0632-v2ir").mkdir(parents=True)
    (runs / "run-260802-0632-v2ir" / "boot-score.json").write_text(
        _score("schedule"), encoding="utf-8"
    )
    # Dispatched by v2ir, and newer — which is exactly why it used to win.
    (runs / "run-260802-0649-lcrd").mkdir(parents=True)
    (runs / "run-260802-0649-lcrd" / "boot-score.json").write_text(
        _score("spawn"), encoding="utf-8"
    )

    c = cont_mod.build_continuity(tmp_path, current_run_id="run-260802-0730-cc2f")
    assert c.mount == "✓"
    assert c.last_run == "run-260802-0632-v2ir"


def test_a_respawn_handoff_is_still_the_prior_wake(tmp_path: Path) -> None:
    """The distinction the ticket refused to collapse — and it holds today.

    ``_queue_respawn_request`` derives its child's source as ``fm ->
    current.get("source") -> task.source -> "respawn"``: it *inherits the
    originating gate*.  So a resident that handed its own thought to a stronger
    Core writes ``source_gate: telegram`` (or ``schedule`` / ``cli``), never
    ``spawn`` — and remains the predecessor, because a respawn is the same
    thought continuing in a different body.

    Only ``_queue_spawn_request`` writes ``"spawn"``.  Skipping exactly that is
    the conservative rule: it skips what can be *proved* concurrent.
    """
    runs = tmp_path / "runs"
    (runs / "run-260802-0632-v2ir").mkdir(parents=True)
    (runs / "run-260802-0632-v2ir" / "boot-score.json").write_text(
        _score("schedule"), encoding="utf-8"
    )
    (runs / "run-260802-0649-resp").mkdir(parents=True)
    (runs / "run-260802-0649-resp" / "boot-score.json").write_text(
        _score("telegram"), encoding="utf-8"
    )

    c = cont_mod.build_continuity(tmp_path, current_run_id="run-260802-0730-cc2f")
    assert c.last_run == "run-260802-0649-resp"


def test_only_workers_are_skipped_ordinary_runs_are_untouched(tmp_path: Path) -> None:
    """The regression guard: the filter must not eat the ordinary case."""
    runs = tmp_path / "runs"
    for name, gate in (
        ("run-260802-0632-v2ir", "schedule"),
        ("run-260802-0649-aaaa", "telegram"),
    ):
        (runs / name).mkdir(parents=True)
        (runs / name / "boot-score.json").write_text(_score(gate), encoding="utf-8")

    c = cont_mod.build_continuity(tmp_path, current_run_id="run-260802-0730-cc2f")
    assert c.mount == "✓"
    assert c.last_run == "run-260802-0649-aaaa"


def test_a_score_with_no_source_field_behaves_as_it_did_before(tmp_path: Path) -> None:
    """Absent ⇒ today's behaviour, never ⇒ skip.

    Every score written before #987 predates the question, and a filter that
    read *silence* as *worker* would walk the whole history backwards looking
    for a field none of it has — turning a boot that named the wrong run into a
    boot that names ``✗ first wake`` on a resident with months of memory.
    Absent is not evidence.
    """
    runs = tmp_path / "runs"
    # Pre-#987 shape: no ``attention`` block at all.
    (runs / "run-260713-2251-ropg").mkdir(parents=True)
    (runs / "run-260713-2251-ropg" / "boot-score.json").write_text(
        json.dumps({"schema_version": "1"}), encoding="utf-8"
    )
    # Present-but-null: a wake whose event carried no source.
    (runs / "run-260713-2300-nsrc").mkdir(parents=True)
    (runs / "run-260713-2300-nsrc" / "boot-score.json").write_text(
        _score(None), encoding="utf-8"
    )

    c = cont_mod.build_continuity(tmp_path, current_run_id="run-260713-2331-qk3d")
    assert c.mount == "✓"
    assert c.last_run == "run-260713-2300-nsrc"


def test_an_unparseable_worker_score_is_still_a_broken_mount(tmp_path: Path) -> None:
    """A score the filter cannot read is reported, not quietly stepped over.

    ``build_continuity`` owes an unreadable score a ``✗ unreachable``.  A picker
    that skipped on parse failure would hand back a healthy ``✓`` for an older
    run while the broken memory went unmentioned — the mount lying in the
    reassuring direction, which is the one direction it must never lie in.
    """
    runs = tmp_path / "runs"
    (runs / "run-260802-0632-v2ir").mkdir(parents=True)
    (runs / "run-260802-0632-v2ir" / "boot-score.json").write_text(
        _score("schedule"), encoding="utf-8"
    )
    (runs / "run-260802-0649-lcrd").mkdir(parents=True)
    (runs / "run-260802-0649-lcrd" / "boot-score.json").write_text(
        "{not json", encoding="utf-8"
    )

    c = cont_mod.build_continuity(tmp_path, current_run_id="run-260802-0730-cc2f")
    assert c.mount == "✗ unreachable"
    assert c.last_run == "run-260802-0649-lcrd"


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


def test_daemon_gate_state_is_not_drift(tmp_path: Path) -> None:
    """Heartbeat-mutated gate state is machinery, not lost memory (#942).

    ``account/gates/*.json`` (health, server ack cursors) is rewritten by the
    daemon *after* every capture commit, so counting it fired ``capture net
    did not close`` on effectively every wake of a cloud-connected account —
    the third instalment of the same permanent-lie shape (``state.md``,
    ``messages/*``).  The fix is structural: daemon machinery is exempt by
    whole root, so the next bookkeeping file the daemon grows under
    ``account/`` joins the exempt class with no edit to continuity.
    """
    brr_dir = _brr_with_prior_wake(tmp_path)
    dom = tmp_path / "dominion"
    _git_repo(dom)
    gates = dom / "account" / "gates"
    gates.mkdir(parents=True)
    health = gates / "cloud.health.json"
    health.write_text('{"ok": true}', encoding="utf-8")
    subprocess.run(["git", "-C", str(dom), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(dom), "commit", "-qm", "capture"], check=True)
    # The heartbeat's post-capture mutation, plus a file capture never saw:
    health.write_text('{"ok": false}', encoding="utf-8")
    (gates / "cloud.server.json").write_text("{}", encoding="utf-8")

    c = cont_mod.build_continuity(brr_dir, dominion_repo=dom)
    assert c.mount == "✓"          # exercise the real path, not an early return
    assert c.drift == ()


def test_resident_memory_under_daemon_adjacent_roots_still_fires(
    tmp_path: Path,
) -> None:
    """The root exemption is scoped: real memory outside those roots counts.

    ``repos/<label>/dominion/`` and ``surface/`` sit beside ``account/`` in
    the same home repo; the exemption must not swallow them.
    """
    brr_dir = _brr_with_prior_wake(tmp_path)
    dom = tmp_path / "dominion"
    _git_repo(dom)
    note = dom / "repos" / "Gurio__brr" / "dominion" / "notes.md"
    note.parent.mkdir(parents=True)
    note.write_text("a thought nobody committed", encoding="utf-8")

    c = cont_mod.build_continuity(brr_dir, dominion_repo=dom)
    assert len(c.drift) == 1
    assert "capture net did not close" in c.drift[0]


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


# ── #1140: a stacked merge is not "shipped" ─────────────────────────────────


def _pr(number, base=None, merged_at="2026-07-13T23:20:00Z") -> dict:
    return {"number": number, "state": "MERGED", "merged_at": merged_at, "base": base}


def test_merged_since_ignores_default_branch_when_not_given() -> None:
    """No ``default_branch`` known ⇒ the pre-#1140 reading: everything MERGED
    since the cutoff counts as shipped, regardless of ``base``."""
    from brr import forge_pr_cache

    cutoff = forge_pr_cache.parse_iso("2026-07-13T23:00:00Z")
    prs = [_pr(1139, base="brr/the-child-speaks-for-itself"), _pr(1134, base="main")]
    assert cont_mod._merged_since(prs, cutoff) == ("#1134", "#1139")
    assert cont_mod._stacked_since(prs, cutoff) == ()


def test_merged_since_excludes_a_known_stacked_base() -> None:
    """A PR whose base is known and disagrees with ``default_branch`` moves
    out of ``shipped`` and into ``stacked`` — #1140's whole point."""
    from brr import forge_pr_cache

    cutoff = forge_pr_cache.parse_iso("2026-07-13T23:00:00Z")
    prs = [
        _pr(1139, base="brr/the-child-speaks-for-itself"),
        _pr(1134, base="main"),
        _pr(1130, base=None),  # unknown base never excludes — see docstring
    ]
    assert cont_mod._merged_since(prs, cutoff, default_branch="main") == (
        "#1130", "#1134",
    )
    assert cont_mod._stacked_since(prs, cutoff, default_branch="main") == (
        "#1139 (→ brr/the-child-speaks-for-itself)",
    )


def test_stacked_since_empty_without_a_default_branch() -> None:
    """Nothing is ever reclassified as stacked on missing information —
    an unreadable ``default_branch`` degrades to "say nothing", not to a
    guess."""
    from brr import forge_pr_cache

    cutoff = forge_pr_cache.parse_iso("2026-07-13T23:00:00Z")
    prs = [_pr(1139, base="brr/the-child-speaks-for-itself")]
    assert cont_mod._stacked_since(prs, cutoff) == ()


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
    host_i = next(i for i, ln in enumerate(lines) if ln.startswith("host:"))

    # Same position as `stale:` — directly under `host:`, above `next:` — so a
    # reader who has learned to check the first line after `host:` gets the
    # answer either way, instead of only when it's bad news.
    current = lines[host_i + 1]
    assert current.startswith("daemon image: current"), out
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
    host_i = next(i for i, ln in enumerate(lines) if ln.startswith("host:"))

    line = lines[host_i + 1]
    assert line == (
        "daemon image: not tracked · no fingerprint captured in this process"
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
    host_i = next(i for i, ln in enumerate(lines) if ln.startswith("host:"))

    assert lines[host_i + 1].startswith("  stale: ⚠"), out
    assert "daemon image: current" not in out
    assert "not tracked" not in out


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


def test_plan_injected_whole_leaves_the_orientation_walk(
    tmp_path: Path, monkeypatch,
) -> None:
    """The defect, reproduced and fixed: a plan handed over whole by the
    work-surface block must not also be billed as a walk entry.

    ``AGENTS.md`` and a touched ``subject-*.md`` hub are seeded alongside the
    plan so this also pins checks 4 and 5 from the #628 spec: neither
    mechanism is disturbed by the new subtraction — only the plan (the one
    candidate the surface block actually handed over whole) leaves the set.
    """
    from brr import prompts

    home = _seed_account_home_for_orientation(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")

    surface = home / "surface"
    plan_dir = surface / "plans" / "local__default"
    plan_dir.mkdir(parents=True)
    (plan_dir / "active.md").write_text("# Plan\n\nship it\n", encoding="utf-8")

    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "subject-boot-sequence.md").write_text("# hub\n", encoding="utf-8")
    monkeypatch.setattr(
        prompts, "_home_knowledge_log_path", lambda _root: kb / "log.md"
    )

    score = prompts.build_boot_score(
        tmp_path,
        is_daemon=True,
        runner_shell="claude",
        task_text="fix the boot sequence meter",
    )

    names = [Path(e.path).name for e in score.orientation_set]
    assert "active.md" not in names, (
        "the plan was injected whole by the work-surface block and must not "
        "also be billed as a walk entry"
    )
    assert names == ["AGENTS.md", "subject-boot-sequence.md"]


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


def test_codex_plan_whole_no_hub_match_empties_the_walk(tmp_path: Path) -> None:
    """On codex, with the plan injected whole and no touched hub, the walk
    is genuinely empty — and the rendered kernel must not show an ``orient:``
    line for it (closes #614 item 2: documented as differential, no longer a
    permanent fixture).
    """
    from brr import prompts
    from brr.bootscore import format_kernel

    home = _seed_account_home_for_orientation(tmp_path)
    surface = home / "surface"
    plan_dir = surface / "plans" / "local__default"
    plan_dir.mkdir(parents=True)
    (plan_dir / "active.md").write_text("# Plan\n\nship it\n", encoding="utf-8")
    # No AGENTS.md (codex reads it natively regardless), no subject hub.

    score = prompts.build_boot_score(tmp_path, is_daemon=True, runner_shell="codex")

    assert score.orientation_set == []
    assert "orient:" not in format_kernel(score)


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
