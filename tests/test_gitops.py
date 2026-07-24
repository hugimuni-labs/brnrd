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


def test_ensure_run_id_hook_stamps_a_merge_commit_parseably(tmp_path, monkeypatch):
    """Merging a reviewed branch is this project's canonical produce event,
    and it is the one commit shape ``git`` hands the hook **without** a
    trailing newline: ``git merge -m`` writes the bare subject, so an
    unguarded ``interpret-trailers`` appends the trailer inside the
    subject's own paragraph. The line is then present in ``%B`` but
    invisible to ``%(trailers:…)`` — and `relics._commits_since_seed`'s
    identity filter reads exactly that placeholder, so every host-run merge
    would be silently dropped from produce by the very filter it satisfies.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    monkeypatch.setenv("BRR_RUN_ID", "run-merger")

    def _git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-q", "-m", "base")
    trunk = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    _git("checkout", "-q", "-b", "feature")
    (repo / "feature.txt").write_text("work\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-q", "-m", "feature work")
    _git("checkout", "-q", trunk)
    _git("merge", "--no-ff", "-q", "feature", "-m", "Merge feature")

    head = subprocess.run(
        [
            "git", "log", "-1",
            "--format=%s\x1f%(trailers:key=Brnrd-Run-Id,valueonly,separator=%x2C)",
        ],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    subject, _, trailer = head.partition("\x1f")
    assert trailer == "run-merger"
    # …and the subject stays a subject, not "Merge feature Brnrd-Run-Id: …"
    assert subject == "Merge feature"


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


# ── #652 close-keyword predicate ────────────────────────────────────────────
#
# Each test drives the *installed sh file on disk* via subprocess — acceptance
# item 6 prohibits Python re-implementations of the regex.  The helper below
# writes a message to a temp file and invokes the hook with /bin/sh so POSIX
# compliance is exercised rather than the bash built into the test runner.


def _run_hook(hook_path: Path, message: str, tmp: Path, *, cwd: Path) -> "subprocess.CompletedProcess[str]":
    """Write *message* to a temp file and invoke *hook_path* against it.

    Returns the CompletedProcess (returncode + stderr) for assertion.
    *cwd* is passed so ``git interpret-trailers`` runs inside a git repo.
    """
    msg_file = tmp / "COMMIT_EDITMSG"
    msg_file.write_text(message, encoding="utf-8")
    return subprocess.run(
        ["/bin/sh", str(hook_path), str(msg_file)],
        capture_output=True, text=True, cwd=str(cwd),
    )


# Refuse cases ---------------------------------------------------------------


def test_hook_refuses_closes_with_section_qualifier(tmp_path, monkeypatch):
    """``Closes #413 §7 S13.`` → refused — the canonical defect from #652.

    GitHub reads ``Closes #413`` and discards the qualifier, so the hook
    exits non-zero to surface the ambiguity before the commit lands.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-652")

    r = _run_hook(hook_path, "Closes #413 §7 S13.", tmp_path, cwd=repo)
    assert r.returncode != 0
    assert "Offending line" in r.stderr and "#413" in r.stderr


def test_hook_refuses_fixes_with_parenthetical(tmp_path, monkeypatch):
    """``Fixes #413 (partially)`` → refused."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-652")

    r = _run_hook(hook_path, "Fixes #413 (partially)", tmp_path, cwd=repo)
    assert r.returncode != 0
    assert "Offending line" in r.stderr and "#413" in r.stderr


def test_hook_refuses_resolves_with_prose_qualifier(tmp_path, monkeypatch):
    """``Resolves #413 for the daemon path only`` → refused."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-652")

    r = _run_hook(hook_path, "Resolves #413 for the daemon path only", tmp_path, cwd=repo)
    assert r.returncode != 0
    assert "Part of" in r.stderr  # must name the scoped-ref alternative
    assert "no-verify" in r.stderr  # must name the escape hatch


# #653 — position (not vocabulary) refuse cases ──────────────────────────────
#
# #653 shipped as a mid-run steer, not the closed word list its own spec
# opened with. Driven against the installed hook, `c91d3866` and `aef7fa11`
# closed #413 for real from prose that was never at the start of a line, and
# `85ed4735` closed #477 for real from "This does not close #477." — a
# leading word list has no entry for "does not" and would have missed it.
# GitHub's own keyword scanner does not read position or narrative framing,
# only adjacency, so the guard now matches that: a close keyword only
# "counts" as a deliberate close at the start of a line; anywhere else in a
# line it is refused outright, and a line-start close keeps #652's original
# trailing-qualifier check on top.


def test_hook_refuses_leading_qualifier_partially_before_closes(tmp_path, monkeypatch):
    """``Partially closes #413`` → refused — the canonical #653 defect.

    The ref is bare (nothing follows ``#413``), so #652's trailing-qualifier
    predicate alone missed this; ``closes`` isn't at the start of the line
    either, so the position rule catches it independently of any word list.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-653")

    r = _run_hook(hook_path, "Partially closes #413", tmp_path, cwd=repo)
    assert r.returncode != 0
    assert "Offending line" in r.stderr and "#413" in r.stderr
    assert "start of a line" in r.stderr


def test_hook_refuses_narrative_close_mid_sentence(tmp_path, monkeypatch):
    """``review: test the merge that actually closed #413, not a stand-in
    for it`` → refused. This is ``c91d3866`` verbatim — GitHub's timeline
    confirms it really closed #413 on push, from a past-tense narrative
    clause with no leading qualifier word at all.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-653")

    r = _run_hook(
        hook_path,
        "review: test the merge that actually closed #413, not a stand-in for it",
        tmp_path, cwd=repo,
    )
    assert r.returncode != 0
    assert "#413" in r.stderr


def test_hook_refuses_negated_close_not_at_line_start(tmp_path, monkeypatch):
    """``This does not close #477.`` → refused. This is ``85ed4735``
    verbatim — GitHub's timeline shows it closed #477 two seconds after
    push despite the explicit "does not". No leading-qualifier word list
    has an entry for "does not"; position catches it for free.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-653")

    r = _run_hook(hook_path, "This does not close #477.", tmp_path, cwd=repo)
    assert r.returncode != 0
    assert "#477" in r.stderr


def test_hook_refuses_this_closes_reversing_the_original_spec(tmp_path, monkeypatch):
    """``This closes #413`` → refused.

    #653's opening spec required this to pass; the mid-run steer reverses
    it deliberately (see kb) once real GitHub history showed position, not
    vocabulary, is the actual discriminator — a keyword not at the start of
    a line still closes, "This" or no "This".
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-653")

    r = _run_hook(hook_path, "This closes #413", tmp_path, cwd=repo)
    assert r.returncode != 0
    assert "#413" in r.stderr


def test_hook_position_refusal_names_the_position_rule(tmp_path, monkeypatch):
    """A not-at-line-start refusal names the position rule and offers
    ``Part of #NNN`` — not the trailing-qualifier message's ``Closes #NNN.``
    remedy, which doesn't apply here (there's no line-start close to make
    bare).
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-653")

    r = _run_hook(hook_path, "Mostly fixes #200 now", tmp_path, cwd=repo)
    assert r.returncode != 0
    assert "start of a line" in r.stderr
    assert "Part of #NNN" in r.stderr
    assert "Closes #NNN." not in r.stderr
    assert "no-verify" in r.stderr


# Pass cases -----------------------------------------------------------------


def test_hook_passes_bare_close_with_period(tmp_path, monkeypatch):
    """``Closes #413.`` → passes."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-652")

    r = _run_hook(hook_path, "Closes #413.", tmp_path, cwd=repo)
    assert r.returncode == 0


def test_hook_passes_bare_close_no_period(tmp_path, monkeypatch):
    """``Closes #413`` (no trailing period) → passes."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-652")

    r = _run_hook(hook_path, "Closes #413", tmp_path, cwd=repo)
    assert r.returncode == 0


def test_hook_passes_scoped_part_of_reference(tmp_path, monkeypatch):
    """``Part of #413 §7 S13.`` → passes (no close keyword)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-652")

    r = _run_hook(hook_path, "Part of #413 §7 S13.", tmp_path, cwd=repo)
    assert r.returncode == 0


def test_hook_passes_multi_close(tmp_path, monkeypatch):
    """``Closes #413, #414`` → passes (genuine multi-close, no qualifier)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-652")

    r = _run_hook(hook_path, "Closes #413, #414", tmp_path, cwd=repo)
    assert r.returncode == 0


def test_hook_passes_clean_close_in_multiline_body(tmp_path, monkeypatch):
    """``Closes #413`` as one line of a multi-line body → passes (predicate is per-line)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-652")

    msg = "feat: do the thing\n\nSome prose mentioning #413 in passing.\n\nCloses #413\n"
    r = _run_hook(hook_path, msg, tmp_path, cwd=repo)
    assert r.returncode == 0


def test_hook_passes_helps_with_remedy_form(tmp_path, monkeypatch):
    """``Helps with #234`` — the remedy the refusal message now suggests —
    passes: ``with`` is not a close keyword, so there's no keyword+ref
    adjacency at all."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-653")

    r = _run_hook(hook_path, "Helps with #234", tmp_path, cwd=repo)
    assert r.returncode == 0


def test_hook_passes_indented_line_start_close(tmp_path, monkeypatch):
    """A bare close indented under leading whitespace still counts as
    line-start (#653's position rule explicitly allows it)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-test-653")

    r = _run_hook(hook_path, "  Closes #413", tmp_path, cwd=repo)
    assert r.returncode == 0


def test_hook_brr_run_id_unset_bypasses_close_check(tmp_path, monkeypatch):
    """``BRR_RUN_ID`` unset → even a dirty close passes unchanged (#652 §fork)."""
    monkeypatch.delenv("BRR_RUN_ID", raising=False)
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"

    r = _run_hook(hook_path, "Closes #413 §7 S13.", tmp_path, cwd=repo)
    assert r.returncode == 0


# Regression: #565 still stamps the trailer ──────────────────────────────────


def test_hook_still_stamps_trailer_on_clean_close(tmp_path, monkeypatch):
    """Hook stamps ``Brnrd-Run-Id`` trailer on an accepted message (#565 not broken)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-stamp-652")

    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("Closes #413.\n", encoding="utf-8")
    subprocess.run(
        ["/bin/sh", str(hook_path), str(msg_file)],
        capture_output=True, text=True, check=True, cwd=str(repo),
    )
    assert "Brnrd-Run-Id: run-stamp-652" in msg_file.read_text(encoding="utf-8")


# Merge-commit shape (no trailing newline) ───────────────────────────────────


def test_hook_accepts_merge_commit_no_trailing_newline(tmp_path, monkeypatch):
    """Merge commit message without trailing newline → accepted, trailer stamped.

    ``git merge -m`` hands the hook a file with no trailing newline; the
    newline guard is load-bearing here.  Assert the accepted shape still works
    after adding the close-keyword predicate.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-merge-652")

    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_bytes(b"Merge feature")  # deliberately no trailing newline
    r = subprocess.run(
        ["/bin/sh", str(hook_path), str(msg_file)],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert r.returncode == 0
    assert "Brnrd-Run-Id: run-merge-652" in msg_file.read_text(encoding="utf-8")


def test_hook_refuses_dirty_close_in_merge_commit_no_trailing_newline(tmp_path, monkeypatch):
    """Merge commit body with a scoped close → refused even without trailing newline."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    hook_path = repo / ".git" / "hooks" / "commit-msg"
    monkeypatch.setenv("BRR_RUN_ID", "run-merge-refuse-652")

    msg_file = tmp_path / "COMMIT_EDITMSG"
    # Two-line merge message, no trailing newline — the defect shape
    msg_file.write_bytes(b"Merge slice S13\nCloses #413 for slice S13.")
    r = subprocess.run(
        ["/bin/sh", str(hook_path), str(msg_file)],
        capture_output=True, text=True, cwd=str(repo),
    )
    assert r.returncode != 0
    assert "#413" in r.stderr


def test_hook_refuses_a_real_git_merge_and_creates_no_merge_commit(tmp_path, monkeypatch):
    """The acceptance test at the level the defect actually happened.

    Every other merge test here writes a no-trailing-newline file and invokes
    the hook script directly. That *simulates* a merge — and a fixture that
    invokes a function directly has silently chosen a moment. `git merge` is
    the path that closed #413 (`79abe94e`), and whether git runs `commit-msg`
    on a non-fast-forward merge at all is the assumption every one of those
    tests rests on without stating it. State it: drive a real merge.

    Also pins the recovery shape. A refused merge leaves the repo mid-merge
    with MERGE_HEAD present and the merge staged — standard git behaviour for
    any commit-msg rejection, but a run that hits it needs to know the merge
    is half-applied rather than absent.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    monkeypatch.setenv("BRR_RUN_ID", "run-real-merge-652")

    def git(*args, **kw):
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, **kw
        )

    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git("add", "base.txt")
    git("commit", "-m", "base")
    trunk = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    git("checkout", "-b", "side")
    (repo / "side.txt").write_text("side\n", encoding="utf-8")
    git("add", "side.txt")
    git("commit", "-m", "side work")

    git("checkout", trunk)
    (repo / "trunk.txt").write_text("trunk\n", encoding="utf-8")
    git("add", "trunk.txt")
    git("commit", "-m", "trunk work")

    before = git("rev-parse", "HEAD").stdout.strip()
    merged = git("merge", "--no-ff", "side", "-m", "Merge side\n\nCloses #413 §7 S13.")

    assert merged.returncode != 0, "a scope-qualified close must not reach a merge commit"
    assert "#413" in merged.stderr
    assert git("rev-parse", "HEAD").stdout.strip() == before, "no merge commit was created"
    assert (repo / ".git" / "MERGE_HEAD").exists(), (
        "a refused merge stays staged — the recovery path is `git commit` with a "
        "corrected message, or `git merge --abort`"
    )

    # And the corrected message completes the same merge, trailer intact.
    fixed = git("commit", "-m", "Merge side\n\nPart of #413 §7 S13.")
    assert fixed.returncode == 0, fixed.stderr
    body = git("log", "-1", "--format=%B").stdout
    assert "Part of #413" in body
    assert "Brnrd-Run-Id: run-real-merge-652" in body


def test_hook_refuses_a_real_git_commit_reproducing_the_477_closure(tmp_path, monkeypatch):
    """The acceptance test at the level #653's actual defect happened
    through: a real ``git commit``, not a direct hook invocation, using the
    exact subject line of ``85ed4735`` — the commit GitHub's own timeline
    confirms closed #477 despite the "does not" — mirrors
    ``test_hook_refuses_a_real_git_merge_and_creates_no_merge_commit`` for
    the ordinary (non-merge) commit path.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    ensure_run_id_hook(repo)
    monkeypatch.setenv("BRR_RUN_ID", "run-real-commit-653")

    (repo / "file.txt").write_text("data\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    r = subprocess.run(
        ["git", "commit", "-m", "This does not close #477."],
        cwd=repo, capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "#477" in r.stderr

    # The corrected message completes the same commit, trailer intact.
    fixed = subprocess.run(
        ["git", "commit", "-m", "Part of #477: does not close it"],
        cwd=repo, capture_output=True, text=True,
    )
    assert fixed.returncode == 0, fixed.stderr
    trailers = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Run-Id,valueonly)"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert trailers == "run-real-commit-653"
