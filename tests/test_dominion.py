"""Tests for the agent dominion bootstrap (`brr.dominion`)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from brr import account, dominion, gitops, prompts

from _helpers import commit_files, init_git_repo


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    """A git repo with a committed ``main`` and a ``.brr/`` runtime dir."""
    repo = tmp_path / name
    init_git_repo(repo)
    commit_files(repo, {"README.md": "main\n"}, message="init main")
    (repo / ".brr").mkdir()
    return repo


def _clone(remote: Path, dest: Path, *, name: str) -> Path:
    subprocess.run(
        ["git", "clone", str(remote), str(dest)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _git(dest, "config", "user.name", name)
    _git(dest, "config", "user.email", f"{name}@example.com")
    (dest / ".brr").mkdir()
    return dest


# ── Fresh bootstrap ──────────────────────────────────────────────────


def test_fresh_bootstrap_creates_orphan_branch_and_worktree(tmp_path):
    repo = _repo(tmp_path)

    path = dominion.ensure_dominion(repo, push=False)

    assert path == repo / ".brr" / "dominion"
    assert path.is_dir()
    assert gitops.branch_exists(repo, "brr-home")
    assert gitops.branch_checkout_path(repo, "brr-home").resolve() == path.resolve()
    # Seed files are present and committed.
    assert (path / "playbook.md").exists()
    assert (path / "self-inject").exists()
    # The README carries the user-facing "don't delete this branch" guidance
    # for a maintainer who notices brr-home in their branch list.
    readme = (path / "README.md").read_text(encoding="utf-8")
    assert "don't delete this branch" in readme.lower()


def test_orphan_history_is_independent_of_main(tmp_path):
    repo = _repo(tmp_path)
    dominion.ensure_dominion(repo, push=False)

    main_oid = gitops.rev_parse(repo, "main")
    home_oid = gitops.rev_parse(repo, "brr-home")
    assert main_oid and home_oid and main_oid != home_oid

    # Unrelated histories: no common ancestor.
    merge_base = subprocess.run(
        ["git", "merge-base", "main", "brr-home"],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert merge_base.returncode != 0


def test_custom_branch_name(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, branch="brr-dominion", push=False)
    assert gitops.branch_exists(repo, "brr-dominion")
    assert gitops.branch_checkout_path(repo, "brr-dominion").resolve() == path.resolve()


# ── Idempotency / returning ──────────────────────────────────────────


def test_restart_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
    first = dominion.ensure_dominion(repo, push=False)
    first_oid = gitops.rev_parse(repo, "brr-home")

    second = dominion.ensure_dominion(repo, push=False)

    assert first == second
    # No re-seed, no new commit.
    assert gitops.rev_parse(repo, "brr-home") == first_oid


def test_returning_reattaches_existing_branch(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    seed_oid = gitops.rev_parse(repo, "brr-home")

    # Simulate a fresh local checkout: drop the worktree, keep the branch.
    _git(repo, "worktree", "remove", "--force", str(path))
    assert gitops.branch_checkout_path(repo, "brr-home") is None

    again = dominion.ensure_dominion(repo, push=False)

    assert again.resolve() == path.resolve()
    assert path.is_dir()
    assert (path / "playbook.md").exists()
    # Re-attached to the same branch — not re-seeded.
    assert gitops.rev_parse(repo, "brr-home") == seed_oid


# ── Forge-backed continuity ──────────────────────────────────────────


def test_returning_from_remote_fetches_and_attaches(tmp_path):
    # A bare remote seeded with main only.
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    init_git_repo(seed)
    commit_files(seed, {"README.md": "main\n"}, message="init")
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(remote)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    # Clone A creates and publishes the dominion.
    clone_a = _clone(remote, tmp_path / "a", name="A")
    dominion.ensure_dominion(clone_a, push=True)

    # Clone B (a "second machine") reconstitutes it from the remote.
    clone_b = _clone(remote, tmp_path / "b", name="B")
    path_b = dominion.ensure_dominion(clone_b, push=False)

    assert path_b.is_dir()
    assert (path_b / "playbook.md").exists()  # fetched the seeded content
    assert gitops.branch_checkout_path(clone_b, "brr-home").resolve() == path_b.resolve()


def test_fresh_bootstrap_without_remote_does_not_raise(tmp_path):
    # No remote configured: stays local, still durable across runs.
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo)  # push defaults True; no-op without remote
    assert path.is_dir()
    assert gitops.branch_exists(repo, "brr-home")


# ── Self-inject resolution ───────────────────────────────────────────


def test_resolve_self_inject_includes_seeded_playbook(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)

    digest = dominion.resolve_self_inject(path)

    assert "Playbook — your standing orientation" in digest
    assert "self-inject: full playbook.md" in digest  # provenance marker
    # The rich seed (not the old stub) shipped and was injected in full.
    # Pin structure, not prose: the first and the last `## ` section both
    # arriving proves whole-file injection, and survives seed rewordings —
    # this test's own prose pins rotted on the 2026-07 register rewrite.
    assert "## Two memories" in digest
    assert "## Keep this place useful" in digest


def test_seed_playbook_fits_default_inject_budget_in_full(tmp_path):
    """The living playbook seed must inject *in full* under the default budget,
    with headroom for the agent's own entries.

    It silently grew past the budget once (2026-06-09: 13.3 KiB vs a
    12288-byte budget, so the closing section was clipped on every wake);
    the budget was bumped to fit. The 2026-06-30 identity-core split made the
    seed smaller again, but this guard still fails if the seed outgrows the
    budget, forcing a deliberate bump rather than silent loss.
    """
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)

    digest = dominion.resolve_self_inject(path)  # default budget

    # The playbook's closing section survives — nothing was clipped. Pinned
    # by heading, not prose: a phrase pin rots on every seed rewording while
    # asserting nothing more than the heading does.
    assert "## Keep this place useful" in digest
    assert "truncated to fit dominion inject budget" not in digest
    assert "collapsed" not in digest.split("\n", 1)[0]  # no collapse banner

    # The invariant, numerically: seed + its inject wrapper fit the default
    # budget with real headroom for the agent's own self-inject entries.
    seed = (path / dominion.PLAYBOOK_FILE).read_text(encoding="utf-8")
    wrapper = len(b"<!-- self-inject: full playbook.md -->\n")
    seed_bytes = len(seed.encode("utf-8")) + wrapper
    assert seed_bytes <= dominion.DEFAULT_INJECT_BUDGET_BYTES
    assert dominion.DEFAULT_INJECT_BUDGET_BYTES - seed_bytes >= 2048


def test_build_injected_context_matches_runner_injection(tmp_path):
    """`brnrd agent inject` (build_injected_context) hands a wrapper exactly
    the wake-context the runner path injects — same blocks, so a non-brr
    harness orients the resident with the identical self-inject semantic."""
    repo = _repo(tmp_path)
    dominion.ensure_dominion(repo, push=False)
    # Disable mode toggles so only base blocks are emitted; allows the
    # substring check against build_run_prompt (which never includes toggles).
    from brr import config as conf
    conf.write_config(repo, {"diffense.emit_pack": False, "introspect.enabled": False})

    context = prompts.build_injected_context(repo, task_text="fix the parser")

    # It carries the product-owned identity core and the resident-owned
    # dominion digest (playbook + self-inject)...
    assert "Resident Identity Core" in context
    assert "Your dominion (working memory)" in context
    assert "Playbook — your standing orientation" in context
    assert context.index("Resident Identity Core") < context.index(
        "Your dominion (working memory)"
    )
    # ...and is verbatim what the runner path embeds into a full prompt, so
    # whatever blocks we add to the runner show up in the tool with no drift.
    assert context in prompts.build_run_prompt("fix the parser", repo)


def test_build_injected_context_prefers_account_dominion(tmp_path):
    repo = _repo(tmp_path)
    legacy = dominion.ensure_dominion(repo, push=False)
    (legacy / "playbook.md").write_text("legacy playbook\n", encoding="utf-8")
    home = tmp_path / "account-home"
    from brr import config as conf

    conf.write_config(
        repo,
        {
            "home.path": str(home),
            "repo.label": "Gurio/brr",
            "diffense.emit_pack": False,
            "introspect.enabled": False,
        },
    )
    ctx = account.resolve_context(
        repo,
        {"home.path": str(home), "repo.label": "Gurio/brr"},
    )
    repo_dom = account.repo_dominion_path(ctx, "Gurio/brr")
    dominion.seed_account_dominion(repo_dom)
    (repo_dom / "playbook.md").write_text("account playbook\n", encoding="utf-8")

    context = prompts.build_injected_context(repo, task_text="fix the parser")

    assert "account playbook" in context
    assert "legacy playbook" not in context
    assert str(repo_dom) in context


def test_build_injected_context_includes_mode_toggles(tmp_path):
    """build_injected_context honors the diffense + introspect config toggles.

    When enabled, the context it returns matches what a real daemon wake
    receives: it is a substring of the corresponding daemon prompt (which
    includes all the same blocks plus the task bundle preamble/trailer).
    """
    repo = _repo(tmp_path)
    dominion.ensure_dominion(repo, push=False)
    from brr import config as conf
    conf.write_config(repo, {"diffense.emit_pack": True, "introspect.enabled": True})

    context = prompts.build_injected_context(repo, task_text="fix the parser")

    # Diffense and introspection blocks are present...
    assert "## Review pack (diffense)" in context
    assert "## Look at it" in context
    # ...and the inject context is a subset of the full daemon prompt, so
    # there is no drift between what the tool shows and what the wake sees.
    daemon_prompt = prompts.build_daemon_prompt(
        "fix the parser", "evt-1", "/tmp/resp.md", repo, diffense=True,
    )
    assert context in daemon_prompt


def test_dominion_block_surfaces_write_path_and_commit(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)

    block = prompts._build_dominion_block(repo)

    # The resident is given the absolute path so it can write to its
    # dominion from a worktree/container whose cwd is elsewhere...
    assert str(path) in block
    # ...and told to commit its own memory (no capture-at-sleep reliance —
    # an uncommitted note can vanish when a non-brr session ends).
    assert "commit what you mean to keep" in block
    # No divergence by default → no dynamic reconcile signal.
    assert "Reason on record" not in block


def test_dominion_block_surfaces_divergence_when_marked(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    dominion.mark_needs_sync(path.parent, "push of brr-home was rejected")

    block = prompts._build_dominion_block(repo)

    # The dynamic signal fires (distinct from the playbook's standing
    # guidance) and carries the recorded reason.
    assert "Reason on record" in block
    assert "push of brr-home was rejected" in block


def test_seed_account_dominion_preserves_existing_files(tmp_path):
    path = tmp_path / "home" / "repos" / "Gurio__brr" / "dominion"
    path.mkdir(parents=True)
    (path / "playbook.md").write_text("custom\n", encoding="utf-8")

    dominion.seed_account_dominion(path)

    assert (path / "playbook.md").read_text(encoding="utf-8") == "custom\n"
    assert (path / "self-inject").exists()
    assert "Default startup does not create a GitHub repo" in (
        path / "README.md"
    ).read_text(encoding="utf-8")


def test_resolve_self_inject_modes(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "notes.md").write_text(
        "alpha\nbeta\nGAMMA marker\ndelta\nepsilon\n", encoding="utf-8",
    )
    (path / "self-inject").write_text(
        "head:2 notes.md\ntail:1 notes.md\ngrep:GAMMA notes.md\n",
        encoding="utf-8",
    )

    digest = dominion.resolve_self_inject(path)

    assert "alpha\nbeta" in digest      # head:2
    assert "epsilon" in digest          # tail:1
    assert "GAMMA marker" in digest     # grep:GAMMA
    assert "delta" not in digest        # selected by no entry


def test_dominion_block_surfaces_schedule_lint_without_any_self_inject_entry(tmp_path):
    """#579's whole point: the lint is visible whether or not the resident
    lists `schedule.md` in its manifest.

    The first shape of this hung the block off a self-inject entry rendering
    `schedule.md` — which no seed manifest and no real dominion in this
    account actually has, so the feature shipped dark and its tests passed
    only because the fixture opted in. Here the manifest names the playbook
    and nothing else, exactly as production does.
    """
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "schedule.md").write_text(
        "## Followup\nat: 2020-01-01T00:00:00Z\ncheck CI\n", encoding="utf-8",
    )
    (path / "playbook.md").write_text("standing orientation\n", encoding="utf-8")
    (path / "self-inject").write_text("full playbook.md\n", encoding="utf-8")

    block = prompts._build_dominion_block(repo)

    assert "standing orientation" in block
    assert "Schedule lint" in block
    assert "stale-at" in block
    assert "followup" in block


def test_dominion_block_with_a_clean_schedule_is_byte_identical(tmp_path):
    """Zero findings render nothing — not even a clean-bill-of-health line.

    This is what makes an always-on surface affordable: the common case costs
    zero bytes of every wake, forever.
    """
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "playbook.md").write_text("standing orientation\n", encoding="utf-8")
    (path / "self-inject").write_text("full playbook.md\n", encoding="utf-8")

    without_schedule = prompts._build_dominion_block(repo)
    (path / "schedule.md").write_text(
        "## Reconcile\nevery: 24h\ndo upkeep\n", encoding="utf-8",
    )
    with_clean_schedule = prompts._build_dominion_block(repo)

    assert with_clean_schedule == without_schedule
    assert "Schedule lint" not in with_clean_schedule


def test_dominion_block_survives_an_unparseable_schedule(tmp_path):
    """A lint pass is a bonus, never a wake-blocking dependency."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "playbook.md").write_text("standing orientation\n", encoding="utf-8")
    (path / "self-inject").write_text("full playbook.md\n", encoding="utf-8")
    (path / "schedule.md").write_bytes(b"\xff\xfe not utf-8 at all")

    block = prompts._build_dominion_block(repo)

    assert "standing orientation" in block


def test_resolve_self_inject_skips_exec(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "danger.sh").write_text("echo pwned\n", encoding="utf-8")
    (path / "self-inject").write_text("exec danger.sh\n", encoding="utf-8")

    # exec is recognised but not run yet; nothing is injected from it.
    assert dominion.resolve_self_inject(path) == ""


def _mk_paragraphs(n: int, name: str = "Paragraph") -> str:
    """*n* distinct, meaningfully-sized blank-line-separated paragraphs, in
    priority order (``{name} 0`` first) — big enough that a one-line
    collapse stub is materially smaller than the paragraph it replaces."""
    return "\n\n".join(
        f"{name} {i}: " + ("filler prose word " * 15).strip() + "."
        for i in range(n)
    )


def test_resolve_self_inject_respects_budget(tmp_path):
    """Extended for the section-aware collapse this replaces the old
    byte-tail truncation with (incident #583's follow-up): the pre-fix
    version of this test asserted the whole digest fit in ``budget + 64``
    bytes — a bound that encoded a raw byte cut. Collapse can legitimately
    render *larger* than the budget once banner + stub overhead is counted
    (the banner counts toward the budget by spec, but nothing here fakes a
    fit by severing a paragraph), so the fit itself is no longer the
    assertion; what must hold is: the shortfall is loud, the highest-priority
    paragraph survives whole, and every later paragraph is named and sized
    rather than silently gone.
    """
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "big.md").write_text(_mk_paragraphs(5) + "\n", encoding="utf-8")
    (path / "self-inject").write_text("full big.md\n", encoding="utf-8")

    digest, overflow = dominion.resolve_self_inject_digest(path, budget_bytes=512)

    assert overflow is not None
    assert overflow.budget_bytes == 512
    assert overflow.clipped_entry == "full big.md"
    assert overflow.clipped_dropped_bytes > 0
    assert "collapsed" in digest.lower()
    # Paragraph 0 — highest priority, first in the file — survives whole;
    # paragraphs 1-4 are each replaced by a named, sized stub, not just gone.
    assert "Paragraph 0" in digest
    assert digest.count("§ collapsed") == 4


def test_resolve_self_inject_no_overflow_is_byte_identical_and_unmarked(tmp_path):
    """Happy-path guarantee: a manifest that fits the budget renders exactly
    as it did before #583 — no banner, no markers, no behaviour change."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "small.md").write_text("hello world\n", encoding="utf-8")
    (path / "self-inject").write_text("full small.md\n", encoding="utf-8")

    digest, overflow = dominion.resolve_self_inject_digest(path)
    plain = dominion.resolve_self_inject(path)

    assert overflow is None
    assert digest == plain
    assert "overflow" not in digest
    assert "dropped" not in digest
    assert "truncated" not in digest


def test_resolve_self_inject_overflow_never_drops_entry_silently(tmp_path):
    """Regression for #583: once one entry overflowed, every later entry
    used to vanish with no trace — a 4-entry manifest whose 2nd entry
    overflowed was indistinguishable, from inside the wake, from a 2-entry
    manifest. Entry 3 here must still produce a visible marker naming it
    and its byte size."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "a.md").write_text("A" * 100 + "\n", encoding="utf-8")
    (path / "b.md").write_text("B" * 5000 + "\n", encoding="utf-8")  # overflows
    (path / "c.md").write_text("C" * 100 + "\n", encoding="utf-8")  # must not vanish
    (path / "self-inject").write_text(
        "full a.md\nfull b.md\nfull c.md\n", encoding="utf-8",
    )

    digest, overflow = dominion.resolve_self_inject_digest(path, budget_bytes=512)

    assert overflow is not None
    assert "full a.md" in digest and "A" * 100 in digest  # entry 1 rendered whole
    assert overflow.clipped_entry == "full b.md"  # entry 2 is the clip
    assert overflow.dropped_entry_count == 1
    label, size = overflow.dropped_entries[0]
    assert label == "full c.md"
    assert size > 0
    # Entry 3 is never silent: it names itself and its size in the digest.
    assert "full c.md" in digest
    assert str(size) in digest
    assert "dropped" in digest.lower()


def test_resolve_self_inject_overflow_banner_leads_with_shortfall(tmp_path):
    """Requirement: rendering overflows ⇒ the digest opens with a loud
    banner naming the byte shortfall, the clipped entry, and the
    percentage — before any injected content."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "big.md").write_text("x" * 5000, encoding="utf-8")
    (path / "self-inject").write_text("full big.md\n", encoding="utf-8")

    digest, overflow = dominion.resolve_self_inject_digest(path, budget_bytes=512)

    assert overflow is not None
    assert digest.startswith("> **self-inject overflow")
    # The banner is the *first* thing in the digest — strictly before the
    # real content marker for the clipped entry.
    banner_span = digest.index("\n\n<!-- self-inject: full big.md -->")
    assert digest.index("self-inject overflow") < banner_span
    assert f"{overflow.total_dropped_bytes:,}" in digest
    assert "full big.md" in digest
    assert f"{overflow.percent_dropped:.0f}%" in digest


def test_resolve_self_inject_overflow_no_h2_falls_back_to_block_collapse(tmp_path):
    """Degenerate input (no H2 headings at all): collapse falls back to
    blank-line-separated block granularity, bottom-up — never a mid-line
    byte cut. This supersedes the old paragraph-*boundary-clip* contract
    (a byte offset backed off to the nearest boundary, then severed with a
    "…[truncated]" suffix): the replacement never cuts a surviving block at
    all — a block is either whole or replaced by its own named, sized
    stub."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    para1 = "First paragraph, the condition, spelled out in enough words to matter here."
    para2 = "Second paragraph, the remedy — it must never leak in half-formed."
    (path / "rule.md").write_text(f"{para1}\n\n{para2}\n", encoding="utf-8")
    (path / "self-inject").write_text("full rule.md\n", encoding="utf-8")

    header = "<!-- self-inject: full rule.md -->\n"
    # Enough budget for the header + paragraph 1 whole, not enough for
    # paragraph 2 too — so paragraph 2 must collapse, not bleed in partway.
    budget = len((header + para1 + "\n\n").encode("utf-8")) + 5

    digest, overflow = dominion.resolve_self_inject_digest(path, budget_bytes=budget)

    assert overflow is not None
    assert overflow.clipped_entry == "full rule.md"
    assert para1 in digest  # paragraph 1 survives whole
    assert "Second paragraph" not in digest  # paragraph 2 never bleeds in
    assert "§ collapsed" in digest  # ...it's named and sized instead


def _mk_h2_doc(names: list[str], *, preamble: str = "Preamble sentence, the highest priority context.\n\n") -> str:
    """A synthetic dominion-style markdown document: *preamble* (highest
    priority — content before the first H2) followed by one H2 section per
    entry in *names*, each with a body large enough that a collapse stub is
    materially smaller than the section it replaces."""
    text = preamble
    for name in names:
        text += f"## {name}\n\n"
        text += "\n".join(
            f"{name} filler line {i} with enough words to be realistic prose."
            for i in range(6)
        )
        text += "\n\n"
    return text


def test_collapse_under_budget_is_byte_identical_passthrough(tmp_path):
    """Requirement 1: a section-structured file that already fits its
    budget renders byte-identical — no banner, no stub, no reordering.
    Collapse is a last resort, never a cosmetic rewrite."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    doc = _mk_h2_doc(["Section A", "Section B"])
    (path / "doc.md").write_text(doc, encoding="utf-8")
    (path / "self-inject").write_text("full doc.md\n", encoding="utf-8")

    digest, overflow = dominion.resolve_self_inject_digest(path)  # ample default budget

    assert overflow is None
    assert "§ collapsed" not in digest
    assert "self-inject collapsed" not in digest
    header = "<!-- self-inject: full doc.md -->\n"
    assert digest == (header + doc.rstrip())


def test_resolve_self_inject_collapses_sections_bottom_up(tmp_path):
    """Requirement 2: sections collapse from the bottom of the document
    upward — the lowest-priority material goes first, the topmost section
    (right after the preamble) is the last thing touched."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    doc = _mk_h2_doc(["Section A", "Section B", "Section C"])
    (path / "doc.md").write_text(doc, encoding="utf-8")
    (path / "self-inject").write_text("full doc.md\n", encoding="utf-8")

    # Sized to force collapsing C and B while A (topmost) survives whole —
    # see the exploratory run this was derived from in the commit that adds
    # it: 800 B lands past "everything collapsed" and short of "nothing
    # collapsed" for this fixture.
    digest, overflow = dominion.resolve_self_inject_digest(path, budget_bytes=800)

    assert overflow is not None
    # Section A's body (all 6 lines) survives whole — never trimmed.
    assert "Section A filler line 5" in digest
    assert "Section A\n_(§ collapsed" not in digest
    # B and C are each collapsed to their own stub under their own heading...
    assert "Section B\n_(§ collapsed" in digest
    assert "Section C\n_(§ collapsed" in digest
    assert "Section B filler line" not in digest
    assert "Section C filler line" not in digest
    # ...and named bottom-up: C (lowest priority) before B in the banner.
    banner_line = next(
        line for line in digest.splitlines() if line.startswith("> collapsed bottom-up")
    )
    assert banner_line.index("Section C") < banner_line.index("Section B")


def test_collapse_banner_byte_math_is_exact(tmp_path):
    """Requirement 4: the banner's byte math is exact, not approximate —
    source, budget, and rendered bytes must match reality precisely,
    including the banner's own contribution to the rendered total (the
    banner counts toward budget itself)."""
    doc = _mk_h2_doc(["Section A", "Section B", "Section C"])

    for budget in (300, 500, 700, 900):
        rendered, dropped_bytes = dominion._collapse_markdown_to_budget(
            doc, budget, source_label="doc.md",
        )
        m = re.search(
            r"source ([\d,]+) B, budget ([\d,]+) B, rendered ([\d,]+) B",
            rendered,
        )
        assert m, f"banner missing at budget={budget}"
        claimed_source, claimed_budget, claimed_rendered = (
            int(g.replace(",", "")) for g in m.groups()
        )
        assert claimed_source == len(doc.encode("utf-8"))
        assert claimed_budget == budget
        # The exact, load-bearing check: the banner's own "rendered" claim
        # matches the true byte length of what it opens — including itself.
        assert claimed_rendered == len(rendered.encode("utf-8"))
        assert dropped_bytes > 0


def test_resolve_self_inject_realistic_30kb_fixture_collapses_to_fit(tmp_path):
    """Drives a ~30 KB realistic playbook-shaped fixture through the real
    ``resolve_self_inject`` (not a synthetic unit call) to prove the whole
    chain — manifest resolution, entry rendering, and collapse — behaves
    under production-shaped input at production scale."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)

    def para(prefix: str, n: int = 7) -> str:
        return "\n".join(
            f"{prefix} line {i}: guidance prose padded out to a realistic "
            "sentence length for budget math."
            for i in range(n)
        )

    names = [
        "Two memories", "Working tree", "Contracts", "Reading the room",
        "Reading economically", "Delegation", "Environment shaping",
        "Identity and delivery", "Keep this place useful", "Closing notes",
    ]
    fixture = (
        "# Playbook\n\n"
        "This is the standing orientation. Read top to bottom; invariants "
        "are at the top and outrank everything below them.\n"
    )
    for name in names:
        fixture += f"\n## {name}\n\n"
        for p in range(4):
            fixture += para(f"{name} para{p}") + "\n\n"
    fixture_bytes = len(fixture.encode("utf-8"))
    assert 25_000 <= fixture_bytes <= 35_000  # keep this test "realistic ~30 KB"

    (path / "playbook.md").write_text(fixture, encoding="utf-8")
    (path / "self-inject").write_text("full playbook.md\n", encoding="utf-8")

    digest = dominion.resolve_self_inject(path, budget_bytes=dominion.DEFAULT_INJECT_BUDGET_BYTES)

    assert "self-inject collapsed" in digest  # mandatory banner fired
    assert len(digest.encode("utf-8")) <= dominion.DEFAULT_INJECT_BUDGET_BYTES
    # The opening orientation and the topmost section (highest priority)
    # survive whole...
    assert "Read top to bottom; invariants" in digest
    assert "Two memories para0 line 0" in digest
    # ...while lowest-priority trailing sections are named, sized stubs.
    assert "Closing notes\n_(§ collapsed" in digest
    assert "§ collapsed" in digest


def test_resolve_self_inject_stays_inside_dominion(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    # A path escaping the dominion is refused, not read.
    (path / "self-inject").write_text(
        "full ../../etc/hostname\n", encoding="utf-8",
    )

    assert dominion.resolve_self_inject(path) == ""


def test_resolve_missing_manifest_is_empty(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "self-inject").unlink()

    assert dominion.resolve_self_inject(path) == ""


# ── Wake-time injection into prompts ─────────────────────────────────


def test_run_prompt_injects_dominion_digest(tmp_path):
    repo = _repo(tmp_path)
    dominion.ensure_dominion(repo, push=False)

    prompt = prompts.build_run_prompt("do the thing", repo)

    assert "Your dominion (working memory)" in prompt
    assert "Playbook — your standing orientation" in prompt


def test_daemon_prompt_injects_dominion_digest(tmp_path):
    repo = _repo(tmp_path)
    dominion.ensure_dominion(repo, push=False)

    prompt = prompts.build_daemon_prompt(
        "do the thing", "evt-1", "/tmp/resp.md", repo,
    )

    assert "Your dominion (working memory)" in prompt


def test_daemon_prompt_names_thread_of_record_slot(tmp_path):
    repo = _repo(tmp_path)
    dom = dominion.ensure_dominion(repo, push=False)
    (dom / "thread-of-record.md").write_text("Current arc\n", encoding="utf-8")

    prompt = prompts.build_daemon_prompt(
        "do the thing", "evt-1", "/tmp/resp.md", repo,
    )

    assert "Thread of record" in prompt
    assert "thread-of-record.md" in prompt
    assert "brr points at the slot but does not synthesize" in prompt


def test_prompt_without_dominion_has_no_block(tmp_path):
    repo = _repo(tmp_path)  # .brr exists, but no dominion materialized

    prompt = prompts.build_run_prompt("do the thing", repo)

    assert "Your dominion (working memory)" not in prompt


def test_disabled_dominion_is_not_injected(tmp_path):
    repo = _repo(tmp_path)
    dominion.ensure_dominion(repo, push=False)
    from brr import config as conf

    conf.write_config(repo, {"dominion.enabled": False})

    prompt = prompts.build_run_prompt("do the thing", repo)

    assert "Your dominion (working memory)" not in prompt
