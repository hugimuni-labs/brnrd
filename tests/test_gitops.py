"""Tests for gitops module."""

import subprocess
from pathlib import Path

from brr.gitops import (
    branch_head,
    commit_all,
    current_branch,
    ensure_run_id_hook,
    fast_forward_branch,
    is_tracked,
    shared_brr_dir,
)
from brr.worktree import (
    WorktreeHygieneEntry,
    WorktreeHygieneSnapshot,
    classify_worktree_hygiene,
    create,
    format_worktree_hygiene_line,
    list_worktrees,
    parse_worktree_hygiene_list,
    remove,
)

from _helpers import init_git_repo


def _init_repo(repo: Path) -> str:
    init_git_repo(repo)
    return "main"


def test_is_tracked(tmp_path, monkeypatch):
    # Setup a temporary git repo
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "file.txt").write_text("data")
    # Initialise git
    _init_repo(repo)
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    # Change directory to repo
    monkeypatch.chdir(repo)
    assert is_tracked(Path("file.txt")) is True
    assert is_tracked(Path("nonexistent.txt")) is False


def test_fast_forward_branch_updates_checked_out_target(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    main_branch = _init_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    subprocess.run(["git", "checkout", "-b", "feature/worktree"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "file.txt").write_text("base\nfeature\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "feature"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", main_branch], cwd=repo, check=True, stdout=subprocess.PIPE)

    result = fast_forward_branch(repo, main_branch, "feature/worktree")

    assert result.success is True
    assert result.branch == main_branch
    assert result.commit
    assert "feature" in (repo / "file.txt").read_text(encoding="utf-8")


def test_fast_forward_branch_refuses_diverged_target(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    main_branch = _init_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    subprocess.run(["git", "checkout", "-b", "feature/diverge"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", main_branch], cwd=repo, check=True, stdout=subprocess.PIPE)

    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)

    result = fast_forward_branch(repo, main_branch, "feature/diverge")

    assert result.success is False
    assert result.detail


def test_fast_forward_branch_updates_unchecked_out_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    main_branch = _init_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    subprocess.run(["git", "checkout", "-b", "target"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", main_branch], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", "-b", "source"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "source.txt").write_text("source\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "source"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", main_branch], cwd=repo, check=True, stdout=subprocess.PIPE)

    result = fast_forward_branch(repo, "target", "source")

    assert result.success is True
    target_head = subprocess.run(
        ["git", "rev-parse", "target"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    source_head = subprocess.run(
        ["git", "rev-parse", "source"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert target_head == source_head


def test_list_worktrees_empty(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True,
        stdout=subprocess.PIPE,
    )

    assert list_worktrees(repo) == []


def test_list_worktrees_finds_brr_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True,
        stdout=subprocess.PIPE,
    )

    wt_path, branch = create(repo, "task-42")
    assert wt_path.exists()
    assert branch == "brr/task-42"

    wts = list_worktrees(repo)
    assert len(wts) == 1
    assert wts[0].run_id == "task-42"
    assert wts[0].branch == "brr/task-42"
    assert wts[0].path == wt_path

    remove(repo, "task-42", branch="brr/task-42", delete_branch=True, force=True)
    assert list_worktrees(repo) == []


def test_shared_brr_dir_uses_main_checkout_for_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True,
        stdout=subprocess.PIPE,
    )

    (repo / ".brr").mkdir()
    wt_path, _branch = create(repo, "task-42")
    assert shared_brr_dir(repo) == repo / ".brr"
    assert shared_brr_dir(wt_path) == repo / ".brr"
    assert current_branch(wt_path) == "brr/task-42"

    remove(repo, "task-42", branch="brr/task-42", delete_branch=True, force=True)


def test_worktree_branch_defaults_to_current_head(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("main\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", "-b", "feature/base"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "feature.txt").write_text("feature\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feature base"], cwd=repo, check=True, stdout=subprocess.PIPE)

    wt_path, _branch = create(repo, "task-43")
    try:
        merge_base = subprocess.run(
            ["git", "merge-base", "feature/base", "brr/task-43"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        feature_head = subprocess.run(
            ["git", "rev-parse", "feature/base"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        assert merge_base == feature_head
        assert (wt_path / "feature.txt").exists()
    finally:
        remove(repo, "task-43", branch="brr/task-43", delete_branch=True, force=True)


def test_worktree_branch_can_be_created_from_explicit_base_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("main\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", "-b", "feature/base"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "feature.txt").write_text("feature\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feature base"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)

    wt_path, _branch = create(repo, "task-44", base_ref="feature/base")
    try:
        assert (wt_path / "feature.txt").exists()
    finally:
        remove(repo, "task-44", branch="brr/task-44", delete_branch=True, force=True)


# ── worktree hygiene report ──────────────────────────────────────────


def test_parse_worktree_hygiene_list_handles_detached_and_branches():
    output = """\
worktree /repo
HEAD abc123
branch refs/heads/main

worktree /repo/.brr/worktrees/task-1
HEAD def456
detached

worktree /repo/.brr/worktrees/task-2
HEAD fedcba
branch refs/heads/brr/task-2
"""
    entries = parse_worktree_hygiene_list(output)
    assert entries == [
        WorktreeHygieneEntry(path=Path("/repo"), branch="main"),
        WorktreeHygieneEntry(path=Path("/repo/.brr/worktrees/task-1"), branch=None),
        WorktreeHygieneEntry(path=Path("/repo/.brr/worktrees/task-2"), branch="brr/task-2"),
    ]


def test_classify_worktree_hygiene_marks_clean_pushed_branch_reap_safe():
    snapshot = WorktreeHygieneSnapshot(
        path=Path("/repo/.brr/worktrees/task-1"),
        branch="brr/task-1",
        dirty=False,
        upstream_ref="origin/brr/task-1",
        commits_ahead=0,
    )
    report = classify_worktree_hygiene(snapshot)

    assert report.classification == "reap-safe"
    assert report.reason == "clean; no commits ahead of origin/brr/task-1; no open PR"
    assert format_worktree_hygiene_line(report) == (
        "/repo/.brr/worktrees/task-1 | brr/task-1 | reap-safe | "
        "clean; no commits ahead of origin/brr/task-1; no open PR"
    )


def test_classify_worktree_hygiene_preserves_dirty_even_without_branch():
    snapshot = WorktreeHygieneSnapshot(
        path=Path("/repo/.brr/worktrees/task-1"),
        branch=None,
        dirty=True,
    )
    report = classify_worktree_hygiene(snapshot)

    assert report.classification == "preserve"
    assert report.reason == "detached HEAD with dirty working tree"


def test_classify_worktree_hygiene_preserves_open_pr():
    snapshot = WorktreeHygieneSnapshot(
        path=Path("/repo/.brr/worktrees/task-1"),
        branch="brr/task-1",
        dirty=False,
        pr_states=("OPEN",),
        upstream_ref="origin/brr/task-1",
        commits_ahead=0,
    )
    report = classify_worktree_hygiene(snapshot)

    assert report.classification == "preserve"
    assert report.reason == "open PR"


def test_classify_worktree_hygiene_uses_origin_main_fallback_when_no_upstream():
    snapshot = WorktreeHygieneSnapshot(
        path=Path("/repo/.brr/worktrees/task-1"),
        branch="brr/task-1",
        dirty=False,
        origin_main_is_ancestor=True,
    )
    report = classify_worktree_hygiene(snapshot)

    assert report.classification == "reap-safe"
    assert report.reason == "clean; HEAD is an ancestor of origin/main; no open PR"


def test_classify_worktree_hygiene_preserves_when_no_upstream_and_not_main_ancestor():
    snapshot = WorktreeHygieneSnapshot(
        path=Path("/repo/.brr/worktrees/task-1"),
        branch="brr/task-1",
        dirty=False,
        origin_main_is_ancestor=False,
    )
    report = classify_worktree_hygiene(snapshot)

    assert report.classification == "preserve"
    assert report.reason == "HEAD is not an ancestor of origin/main"


def test_classify_worktree_hygiene_unknown_on_pr_lookup_failure():
    snapshot = WorktreeHygieneSnapshot(
        path=Path("/repo/.brr/worktrees/task-1"),
        branch="brr/task-1",
        dirty=False,
        pr_lookup_error="gh auth failed",
    )
    report = classify_worktree_hygiene(snapshot)

    assert report.classification == "unknown"
    assert report.reason == "PR lookup failed: gh auth failed"


def test_commit_all_stamps_conversation_trailer(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("data\n", encoding="utf-8")

    assert commit_all(
        repo, "brnrd-kb: capture", conversation_id="telegram:-1001234567890:"
    ) is True

    trailers = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Conversation-Id,valueonly)"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert trailers == "telegram:-1001234567890:"
    # The trailer decorates the message; the subject stays intact.
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert subject == "brnrd-kb: capture"


def test_commit_all_stamps_run_id_trailer(tmp_path):
    """#565 — the same trailer mechanism as conversation identity, but for
    the owning run: produce attribution filters a shared commit window by
    this trailer instead of trusting a bare time range."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("data\n", encoding="utf-8")

    assert commit_all(
        repo, "brnrd-kb: capture", run_id="run-260722-2337-nig2",
    ) is True

    trailers = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Run-Id,valueonly)"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert trailers == "run-260722-2337-nig2"


def test_commit_all_stamps_both_trailers_together(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("data\n", encoding="utf-8")

    assert commit_all(
        repo, "brnrd-kb: capture",
        conversation_id="telegram:-1001234567890:",
        run_id="run-260722-2337-nig2",
    ) is True

    body = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers)"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert body == (
        "Brnrd-Conversation-Id: telegram:-1001234567890:\n"
        "Brnrd-Run-Id: run-260722-2337-nig2"
    )


def test_commit_all_omits_trailer_without_conversation(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("data\n", encoding="utf-8")
    assert commit_all(repo, "no conversation") is True

    (repo / "file.txt").write_text("more\n", encoding="utf-8")
    assert commit_all(
        repo, "blank conversation", conversation_id="  ", run_id="  ",
    ) is True

    for ref in ("HEAD", "HEAD~1"):
        body = subprocess.run(
            ["git", "log", "-1", "--format=%(trailers)", ref],
            cwd=repo, check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert body == ""


def test_ensure_run_id_hook_installs_a_commit_msg_hook(tmp_path, monkeypatch):
    """#575 — the project checkout needs the identical stamping mechanism
    #565 installed on the account-knowledge checkout, so a resident's own
    ``git commit`` inside a host run also carries ``Brnrd-Run-Id``."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    ensure_run_id_hook(repo)

    hook_path = repo / ".git" / "hooks" / "commit-msg"
    assert hook_path.is_file()
    assert hook_path.stat().st_mode & 0o111

    (repo / "file.txt").write_text("data\n", encoding="utf-8")
    monkeypatch.setenv("BRR_RUN_ID", "run-hooked")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "resident: hand commit"],
        cwd=repo, check=True,
    )

    trailers = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Run-Id,valueonly)"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert trailers == "run-hooked"


def test_ensure_run_id_hook_no_env_leaves_commit_untouched(tmp_path, monkeypatch):
    """No ``$BRR_RUN_ID`` in the shell (a maintainer, logged in directly) ⇒
    the hook is a no-op — credited to no run, never a guess (#575)."""
    monkeypatch.delenv("BRR_RUN_ID", raising=False)
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)

    (repo / "file.txt").write_text("data\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "maintainer: hand edit"],
        cwd=repo, check=True,
    )

    trailers = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Run-Id,valueonly)"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert trailers == ""


def test_ensure_run_id_hook_is_idempotent_and_respects_hand_edits(tmp_path):
    """Re-running never rewrites its own hook needlessly, and a hook a
    maintainer customized by hand (no marker line) is left alone entirely —
    the same "respect an existing hook" contract #565 established."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"

    ensure_run_id_hook(repo)
    first_mtime = hook_path.stat().st_mtime_ns
    ensure_run_id_hook(repo)
    assert hook_path.stat().st_mtime_ns == first_mtime

    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("#!/bin/sh\necho hand-customized\n", encoding="utf-8")
    hook_path.chmod(0o755)

    ensure_run_id_hook(repo)
    assert hook_path.read_text(encoding="utf-8") == "#!/bin/sh\necho hand-customized\n"
