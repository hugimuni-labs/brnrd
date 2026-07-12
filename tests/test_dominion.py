"""Tests for the agent dominion bootstrap (`brr.dominion`)."""

from __future__ import annotations

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
    assert "memory palace" in digest  # society-of-mind framing
    assert "workshop reading" in digest
    assert "build it like it's yours" in digest


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

    # The playbook's closing line survives — nothing was clipped.
    assert "build it like it's yours" in digest
    assert "truncated to fit dominion inject budget" not in digest

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


def test_resolve_self_inject_skips_exec(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "danger.sh").write_text("echo pwned\n", encoding="utf-8")
    (path / "self-inject").write_text("exec danger.sh\n", encoding="utf-8")

    # exec is recognised but not run yet; nothing is injected from it.
    assert dominion.resolve_self_inject(path) == ""


def test_resolve_self_inject_respects_budget(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "big.md").write_text("x" * 5000, encoding="utf-8")
    (path / "self-inject").write_text("full big.md\n", encoding="utf-8")

    digest = dominion.resolve_self_inject(path, budget_bytes=512)

    assert len(digest.encode("utf-8")) <= 512 + 64  # budget + truncation marker
    assert "truncated" in digest


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
