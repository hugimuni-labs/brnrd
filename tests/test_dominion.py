"""Tests for the agent dominion bootstrap (`brr.dominion`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from brr import dominion, gitops, prompts

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
    assert (path / "README.md").exists()


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
    assert "single-flight" in digest
    assert "is genuinely yours to shape" in digest


def test_dominion_block_surfaces_write_path_and_capture(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)

    block = prompts._build_dominion_block(repo)

    # The resident is given the absolute path so it can write to its
    # dominion from a worktree/container whose cwd is elsewhere...
    assert str(path) in block
    # ...and told brr captures it at sleep, so it needn't commit by hand.
    assert "commits whatever you leave" in block


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
