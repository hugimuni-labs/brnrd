"""Tests for gitops module."""

import subprocess
from pathlib import Path

import pytest

from brr.gitops import (
    BOT_EMAIL,
    BOT_NAME,
    branch_head,
    create_orphan_branch,
    current_branch,
    fast_forward_branch,
    main_worktree_root,
    shared_brr_dir,
)
from brr import forge_pr_cache, gitops, worktree
from brr.worktree import (
    build_worktree_hygiene_report,
    create,
    list_brr_worktrees,
    list_worktrees,
    remove,
)

from _helpers import (
    commit_files,
    init_git_repo,
)


def _init_repo(repo: Path) -> str:
    init_git_repo(repo)
    return "main"


def _committed_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    init_git_repo(repo)
    commit_files(repo, {"README.md": "base\n"})
    return repo


def test_bot_identity_uses_the_stable_github_numeric_id_form():
    assert BOT_NAME == "brnrd-bot"
    assert BOT_EMAIL == "289761152+brnrd-bot@users.noreply.github.com"


def test_commit_count_zero_for_missing_or_uninitialized_repo(tmp_path):
    assert gitops.commit_count(tmp_path / "nowhere") == 0
    bare_dir = tmp_path / "not-a-repo"
    bare_dir.mkdir()
    assert gitops.commit_count(bare_dir) == 0


def test_commit_count_tracks_the_real_history_length(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    assert gitops.commit_count(repo) == 0
    commit_files(repo, {"a.md": "1\n"}, message="one")
    assert gitops.commit_count(repo) == 1
    commit_files(repo, {"b.md": "2\n"}, message="two")
    assert gitops.commit_count(repo) == 2


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


def test_create_orphan_branch_refuses_to_clobber_a_concurrently_created_ref(
    tmp_path, monkeypatch,
):
    """#1309 item 1: the final ``update-ref`` must be old-value-checked.

    ``create_orphan_branch`` checks ``branch_exists`` once at entry, then
    does two non-mutating plumbing calls before its actual write. A
    concurrent process (two daemons racing ``ensure_dominion``, or a
    transient git/network hiccup upstream that made an adoption path look
    absent when it was not) can create the same branch, pointed at real
    history, in that window. Simulate the race by lying about
    ``branch_exists`` at entry while the branch genuinely exists underneath
    — the exact shape of "our own read said absent, git's ref store
    disagrees" the CAS write must catch.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    monkeypatch.setattr(gitops, "branch_exists", lambda *_: False)

    real_oid = create_orphan_branch(repo, "dominion", message="real history")
    assert real_oid is not None

    racer_oid = create_orphan_branch(repo, "dominion", message="racer")

    # The loser adopts the winner's commit rather than reporting failure —
    # mirroring the function's own entry-point "branch already exists"
    # short-circuit, and sparing ``ensure_dominion`` a spurious
    # RuntimeError over a branch that in fact now exists just fine.
    assert racer_oid == real_oid, "a losing race must adopt, not fail"
    assert branch_head(repo, "dominion") == real_oid, (
        "the branch that already existed must survive untouched"
    )


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


def _repo_with_two_worktrees(tmp_path: Path) -> tuple[Path, Path]:
    """A repo with one brr worktree and one worktree of it living elsewhere.

    The second is the shape the ``host`` environment mandates —
    ``git worktree add /tmp/brr-wt-<slug>`` — and the shape a path-prefix
    filter made invisible (#721).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True,
        stdout=subprocess.PIPE,
    )

    create(repo, "task-42")
    foreign = tmp_path / "elsewhere" / "brr-wt-mood"
    subprocess.run(
        ["git", "worktree", "add", "-b", "brr/mood", str(foreign)],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return repo, foreign


def test_list_worktrees_counts_and_tags_a_worktree_outside_brr(tmp_path):
    """#721: classify, don't filter.

    The old shape dropped every path outside ``.brr/worktrees/`` with a bare
    ``except ValueError: pass`` — no count, no trace — and the dropped set was
    not incidental: it is where the ``host`` invariant *requires* a run's work
    to live.
    """
    repo, foreign = _repo_with_two_worktrees(tmp_path)

    by_name = {wt.path.name: wt for wt in list_worktrees(repo)}
    assert set(by_name) == {"task-42", "brr-wt-mood"}

    brr_wt = by_name["task-42"]
    assert brr_wt.kind == "brr"
    assert brr_wt.run_id == "task-42"
    assert brr_wt.branch == "brr/task-42"

    external = by_name["brr-wt-mood"]
    assert external.kind == "external"
    assert external.branch == "brr/mood"
    assert external.path == foreign
    # Its directory name is a slug, not a run id. Handing it out as one would
    # send every consumer that joins it into a path at a manifest that does
    # not exist, and be answered with a plausible False.
    assert external.run_id is None


def test_list_worktrees_excludes_the_main_checkout(tmp_path):
    """Deliberate, not a filter leftover — the main checkout is the repo.

    Pinned with a second worktree present so the assertion cannot pass merely
    because the list came back empty.
    """
    repo, foreign = _repo_with_two_worktrees(tmp_path)

    paths = {wt.path.resolve() for wt in list_worktrees(repo)}
    assert repo.resolve() not in paths
    assert paths == {(repo / ".brr/worktrees/task-42").resolve(), foreign.resolve()}


def test_list_brr_worktrees_narrows_to_brr_managed(tmp_path):
    """The narrow accessor callers ask for by name instead of re-filtering."""
    repo, _foreign = _repo_with_two_worktrees(tmp_path)

    narrowed = list_brr_worktrees(repo)
    assert [wt.run_id for wt in narrowed] == ["task-42"]
    assert {wt.kind for wt in narrowed} == {"brr"}


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


# ── the forge-kind gate on the hygiene report (#1064) ─────────────────
#
# ``gh pr list --head <branch>`` here is cwd-resolved with no ``--repo``, so
# #852's *worse* outcome (a same-named github.com repo answering for this
# one) cannot happen on this path. What is left is a subprocess spent on a
# question this forge can never answer, degrading to a ``pr_lookup_error``
# that implies a retry might help. These drive the real entry point,
# ``build_worktree_hygiene_report``, exactly as ``brnrd`` calls it, and
# assert the *subprocess count* — a test that only reads the rendered string
# passes on a fix that still shells out.


def _gh_only(handler):
    """Intercept ``gh`` calls with *handler*; let git through to the real thing.

    ``worktree.subprocess`` *is* the stdlib module, so a blanket patch would
    also swallow the ``git worktree list`` / ``git rev-list`` reads the report
    is built from.
    """
    real_run = subprocess.run

    def dispatch(cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        if argv[:1] == ["gh"]:
            return handler(argv, **kwargs)
        return real_run(cmd, *args, **kwargs)

    return dispatch


def _hygiene_repo(tmp_path: Path, origin: str, *, worktrees: int = 2) -> Path:
    """A committed repo with *origin* and *worktrees* run worktrees on it."""
    repo = _committed_repo(tmp_path)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=repo, check=True)
    for index in range(worktrees):
        wt_path, _branch = create(repo, f"run-hygiene-{index}")
        commit_files(wt_path, {f"feature-{index}.txt": "wip\n"}, message="feature")
    return repo


def _branch_rows(reports):
    """Report rows for the run worktrees — the main checkout is not the subject."""
    return [row for row in reports if (row.branch or "").startswith("brr/run-hygiene-")]


def test_hygiene_report_never_shells_out_on_a_non_github_remote(tmp_path, monkeypatch):
    repo = _hygiene_repo(tmp_path, "https://gitlab.com/group/proj.git")
    calls: list[list[str]] = []

    def forbidden(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError(f"the hygiene report shelled out to gh: {cmd}")

    monkeypatch.setattr(worktree.subprocess, "run", _gh_only(forbidden))
    reports = build_worktree_hygiene_report(repo)

    assert calls == []  # the count, not just the wording
    rows = _branch_rows(reports)
    assert len(rows) == 2
    for row in rows:
        assert row.classification == "unknown"
        assert row.reason == (
            "PR state unsupported (gitlab) — this remote isn't GitHub, "
            "and gh is never queried for it"
        )
        assert "lookup failed" not in row.reason


def test_hygiene_report_resolves_the_forge_kind_once_per_report(tmp_path, monkeypatch):
    """Once per *report*, not once per branch — the whole point of #1064."""
    repo = _hygiene_repo(tmp_path, "https://gitlab.com/group/proj.git", worktrees=3)
    real = forge_pr_cache._forge_kind_and_label
    calls: list[Path] = []

    def counting(repo_root):
        calls.append(repo_root)
        return real(repo_root)

    monkeypatch.setattr(forge_pr_cache, "_forge_kind_and_label", counting)
    reports = build_worktree_hygiene_report(repo)

    assert len(_branch_rows(reports)) == 3
    assert len(calls) == 1


def test_hygiene_report_still_asks_gh_for_a_forge_it_cannot_name(tmp_path, monkeypatch):
    """``kind is None`` with a valid label stays **queryable** — and this
    narrows :func:`forge_pr_cache.refresh`'s condition on purpose (#1064).

    ``refresh`` must refuse to tell that shape apart from an explicit
    ``gitlab``, because it passes ``--repo OWNER/REPO`` and a wrong guess
    silently answers from a same-named repo on github.com — #852's hazard.
    **This call site passes no ``--repo``**; ``gh pr list --head`` is
    cwd-resolved, gh does its own host detection, and the hazard cannot
    occur. The rule transfers; its justification does not.

    What gating this shape would cost is not the subprocess — it is the
    sentence. ``git.example.com`` and a GitHub Enterprise host both land
    here, and ``PR state unsupported — this remote isn't GitHub`` is
    **false** for the second one, rendered with the ``(kind)`` parenthetical
    dropped precisely because brr could not name the forge. A diagnostic that
    cannot tell two cases apart must not pick the confident branch.

    So: one subprocess for the whole report (``__gh_global_error__``
    short-circuits the rest), and an honest ``PR lookup failed`` if gh cannot
    answer — never a claim about a forge brr never identified.
    """
    repo = _hygiene_repo(tmp_path, "https://git.example.com/group/proj.git")
    calls: list[list[str]] = []

    def failing_gh(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "no such host\n")

    monkeypatch.setattr(worktree.subprocess, "run", _gh_only(failing_gh))
    rows = _branch_rows(build_worktree_hygiene_report(repo))

    assert len(calls) == 1, "one call for the whole report, not one per branch"
    assert len(rows) == 2
    for row in rows:
        assert row.classification == "unknown"
        assert row.reason == "PR lookup failed: no such host"
        # never a claim about a forge brr could not name
        assert "unsupported" not in row.reason


def test_hygiene_report_gates_only_forges_brr_can_name(tmp_path, monkeypatch):
    """The gate is ``kind is not None and kind != "github"``.

    A self-hosted GitLab that ``forges.py`` *does* pattern-match is named, so
    it is gated exactly like ``gitlab.com`` — the saving this whole function
    exists for is kept. Pinned separately from the ``gitlab.com`` test so a
    future narrowing of ``_HOST_PATTERNS`` cannot silently un-gate the
    self-hosted case while the hosted one keeps passing.
    """
    repo = _hygiene_repo(tmp_path, "https://gitlab.internal.example.com/g/p.git")

    def forbidden(cmd, **kwargs):
        raise AssertionError(f"shelled out to gh for a named non-GitHub forge: {cmd}")

    monkeypatch.setattr(worktree.subprocess, "run", _gh_only(forbidden))
    rows = _branch_rows(build_worktree_hygiene_report(repo))

    assert len(rows) == 2
    for row in rows:
        assert row.classification == "unknown"
        assert row.reason.startswith("PR state unsupported (gitlab)")


def test_hygiene_report_honours_the_forge_kind_override(tmp_path, monkeypatch):
    """``.brr/config`` ``forge.kind`` moves a host **into** the gated set.

    Re-aimed with the gate (#1064 review). While the gate was
    ``label is not None and kind != "github"``, the interesting direction was
    ``forge.kind = github`` rescuing a GHE host from being gated. With the
    gate narrowed to forges brr can name, that host is queried anyway and the
    override no longer decides anything there — so the direction worth pinning
    is the other one: naming an unrecognised self-host as GitLab makes brr stop
    asking gh about it, exactly as if a pattern had matched.
    """
    repo = _hygiene_repo(tmp_path, "https://code.example.internal/group/proj.git")
    (repo / ".brr").mkdir(exist_ok=True)
    (repo / ".brr" / "config").write_text("forge.kind=gitlab\n", encoding="utf-8")

    def forbidden(cmd, **kwargs):
        raise AssertionError(f"the forge.kind override did not gate: {cmd}")

    monkeypatch.setattr(worktree.subprocess, "run", _gh_only(forbidden))
    rows = _branch_rows(build_worktree_hygiene_report(repo))

    assert len(rows) == 2
    for row in rows:
        assert row.reason.startswith("PR state unsupported (gitlab)")


def test_hygiene_report_still_queries_a_github_remote(tmp_path, monkeypatch):
    """The gate must not catch the case it is meant to leave alone."""
    repo = _hygiene_repo(tmp_path, "https://github.com/Gurio/brr.git")
    heads: list[str] = []

    def fake_gh(cmd, **kwargs):
        assert cmd[:3] == ["gh", "pr", "list"]
        assert "--repo" not in cmd  # cwd-resolved by design on this path
        head = cmd[cmd.index("--head") + 1]
        heads.append(head)
        rows = '[{"state": "OPEN"}]' if head.startswith("brr/run-hygiene-") else "[]"
        return subprocess.CompletedProcess(cmd, 0, rows, "")

    monkeypatch.setattr(worktree.subprocess, "run", _gh_only(fake_gh))
    reports = build_worktree_hygiene_report(repo)

    rows = _branch_rows(reports)
    assert len(rows) == 2
    assert sorted(heads) == ["brr/run-hygiene-0", "brr/run-hygiene-1", "main"]
    for row in rows:
        assert row.classification == "preserve"
        assert row.reason == "open PR"


def test_hygiene_report_keeps_the_global_error_for_a_broken_gh(tmp_path, monkeypatch):
    """A ``gh`` that is installed and broken is not an unsupported forge.

    Folding the two states together would lose exactly this distinction: the
    remote *is* GitHub, the question *is* answerable, and the failure is one a
    retry might fix — so it must still read as an error, and must still cost
    exactly one subprocess for the whole report (``__gh_global_error__``).
    """
    repo = _hygiene_repo(tmp_path, "https://github.com/Gurio/brr.git")
    calls: list[list[str]] = []

    def broken_gh(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "gh: not authenticated\n")

    monkeypatch.setattr(worktree.subprocess, "run", _gh_only(broken_gh))
    reports = build_worktree_hygiene_report(repo)

    assert len(calls) == 1  # the first failure poisons the report, once
    rows = _branch_rows(reports)
    assert len(rows) == 2
    for row in rows:
        assert row.classification == "unknown"
        assert row.reason == "PR lookup failed: gh: not authenticated"
        assert "unsupported" not in row.reason


def test_hygiene_report_still_asks_gh_when_the_remote_does_not_parse(tmp_path, monkeypatch):
    """``label is None`` is left alone, following ``_forge_kind_and_label``'s docstring.

    Nothing parsed out of the remote means nothing to base a verdict on, and
    this call site's ``gh`` invocation is cwd-resolved anyway — so gh's own
    host detection stays the fallback, and one honest failure beats a sentence
    claiming "this remote isn't GitHub" about a remote brr could not read.
    """
    repo = _committed_repo(tmp_path)  # no origin at all
    create(repo, "run-hygiene-0")
    calls: list[list[str]] = []

    def broken_gh(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "no git remotes found\n")

    monkeypatch.setattr(worktree.subprocess, "run", _gh_only(broken_gh))
    reports = build_worktree_hygiene_report(repo)

    assert len(calls) == 1
    rows = _branch_rows(reports)
    assert rows and all("unsupported" not in row.reason for row in rows)


# ── #652 close-keyword predicate ────────────────────────────────────────────
#
# Each test drives the *installed sh file on disk* via subprocess — acceptance
# item 6 prohibits Python re-implementations of the regex.  The helper below
# writes a message to a temp file and invokes the hook with /bin/sh so POSIX
# compliance is exercised rather than the bash built into the test runner.


# Refuse cases ---------------------------------------------------------------


# #653 — position (not vocabulary) refuse cases ──────────────────────────────
#
# #653 shipped as a mid-run steer, not the closed word list its own spec
# opened with. Driven against the installed hook, `aef7fa11` closed #413 for
# real from prose that was never at the start of a line (and `c91d3866`, in
# the same push, carried the identical hazard in its subject), and
# `85ed4735` closed #477 for real from "This does not close #477." — a
# leading word list has no entry for "does not" and would have missed it.
# GitHub's own keyword scanner does not read position or narrative framing,
# only adjacency, so the guard now matches that: a close keyword only
# "counts" as a deliberate close at the start of a line; anywhere else in a
# line it is refused outright, and a line-start close keeps #652's original
# trailing-qualifier check on top.


# Pass cases -----------------------------------------------------------------


# Regression: #565 still stamps the trailer ──────────────────────────────────


# Merge-commit shape (no trailing newline) ───────────────────────────────────


# ── #657 the tail after the ref ──────────────────────────────────────────────
#
# #652 checked the tail *negatively*: line-start close + whitespace + a char
# that is not ","/"#"/space.  The "," exemption was the deliberate multi-close
# carve-out — and it exempted everything after the comma with it, prose
# included; a "." bought the same exemption from the other side, since the
# rule wanted whitespace right after the digits.  Fourth face of the class,
# and, like the third, the hole was in the carve-out rather than in the rule.  _BRR_CLEAN replaces it with a
# positive one: after the ref, only a repeatable ", #MMM" list, then end of
# line or ":".
#
# The ":" allowance is measured, not assumed.  Driving the installed hook over
# this repo's last 300 commit messages: shipped 10 refusal lines / 5 commits;
# strict (no ":") 18 lines / 13 commits, the 8 extras all `Fix #NNN: <subject>`
# on line 1; this rule 10 lines / 5 commits — byte-identical to shipped, plus
# the two holes below closed.


# ── main_worktree_root ───────────────────────────────────────────────
#
# Three cases, discriminated by whether `git worktree list --porcelain`'s
# head entry names a *working tree*. Driven for #663 against git 2.43.


def _separate_git_dir_repo(tmp_path) -> tuple[Path, Path]:
    """``git init --separate-git-dir``. Returns (checkout, gitdir).

    The checkout's ``.git`` is a *file* pointing at the git dir; the
    reverse edge does not exist, which is the whole subject of #663.
    """
    checkout = tmp_path / "checkout"
    gitdir = tmp_path / "elsewhere" / "gitdir"
    gitdir.parent.mkdir(parents=True)
    checkout.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main", f"--separate-git-dir={gitdir}",
         str(checkout)],
        check=True, capture_output=True,
    )
    for key, value in (("user.email", "test@example.com"), ("user.name", "T")):
        subprocess.run(
            ["git", "-C", str(checkout), "config", key, value],
            check=True, capture_output=True,
        )
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "--allow-empty", "-m", "seed"],
        check=True, capture_output=True,
    )
    return checkout, gitdir


def test_main_worktree_root_names_the_checkout_under_separate_git_dir(tmp_path):
    """Case 2: the porcelain head is the git dir, not a checkout.

    ``git worktree list --porcelain`` names the main working tree *as git
    models it*. Under ``--separate-git-dir`` that model is the git dir, so
    reading the head entry verbatim hands out a path that is not a checkout
    at all — the bug #663 was filed for. ``repo_root`` is itself the main
    working tree here, so the answer is recoverable from it directly.
    """
    checkout, gitdir = _separate_git_dir_repo(tmp_path)

    # The trap, pinned: what git actually prints in this configuration.
    porcelain = subprocess.run(
        ["git", "-C", str(checkout), "worktree", "list", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert porcelain.splitlines()[0] == f"worktree {gitdir}"

    assert main_worktree_root(checkout) == checkout


def test_main_worktree_root_is_none_from_a_worktree_of_a_separate_git_dir_repo(
    tmp_path,
):
    """Case 3: the main checkout's path is genuinely unrecorded.

    Under ``--separate-git-dir`` git writes the main working tree's path
    **nowhere** inside the git dir: ``core.worktree`` is unset, no reverse
    pointer file exists, and ``<checkout>/.git`` is a one-way edge *into*
    the git dir. From a linked worktree there is therefore no question with
    the right answer, and the honest return is ``None`` rather than the git
    dir (which is what shipped, and which reads to the caller as a real
    checkout).
    """
    checkout, gitdir = _separate_git_dir_repo(tmp_path)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(checkout), "worktree", "add", "-q", "-b", "wt",
         str(linked)],
        check=True, capture_output=True,
    )

    # The evidence for "unrecorded", pinned so a future git that starts
    # recording it turns this red instead of leaving None as folklore.
    assert not any(
        gitdir.joinpath(*parts).exists()
        for parts in (("worktree",), ("main-worktree",))
    )
    assert subprocess.run(
        ["git", f"--git-dir={gitdir}", "config", "core.worktree"],
        capture_output=True,
    ).returncode != 0
    assert str(checkout) not in _grep_r(gitdir)

    assert main_worktree_root(linked) is None


def _grep_r(root: Path) -> str:
    """Concatenate every readable regular file under *root*."""
    chunks = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                chunks.append(path.read_bytes().decode("utf-8", "replace"))
            except OSError:  # pragma: no cover - unreadable in a temp tree
                pass
    return "\n".join(chunks)


def test_main_worktree_root_returns_an_ordinary_repo_itself(tmp_path):
    """Case 1, no linked worktrees: today's behaviour, not to be regressed."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "seed"],
        check=True, capture_output=True,
    )

    assert main_worktree_root(repo) == repo


def test_main_worktree_root_resolves_a_linked_worktree_of_an_ordinary_repo(
    tmp_path,
):
    """Case 1, from a linked worktree: the reason the function exists.

    This is the path ``account._connected_account_id`` depends on to find
    the registered main checkout from a run's worktree (#654). The
    ``--separate-git-dir`` handling must not cost it.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "seed"],
        check=True, capture_output=True,
    )
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "wt",
         str(linked)],
        check=True, capture_output=True,
    )

    assert main_worktree_root(linked) == repo
    assert main_worktree_root(repo) == repo


def test_main_worktree_root_is_none_outside_a_git_checkout(tmp_path):
    """Not a repo at all — nothing to name."""
    plain = tmp_path / "plain"
    plain.mkdir()

    assert main_worktree_root(plain) is None


# ── #1340: current_branch's failure vs. state distinction ──────────────
#
# The pre-fix bug: ``rev-parse --abbrev-ref HEAD`` failing (non-zero exit)
# collapsed into the same "HEAD" string a genuine detached HEAD also
# returns — so every caller downstream read a git failure as a state.
# A genuine detached HEAD never actually reaches the failure branch at all
# (``rev-parse --abbrev-ref HEAD`` exits 0 and prints "HEAD" for it); the
# two things that *do* fail alike are an unborn HEAD (fresh repo, no
# commit yet — a real, common, benign state) and an actual measurement
# failure. The tests below establish, against real git, that those two are
# in fact distinguishable via ``symbolic-ref``, then verify the function's
# resulting contract.


def test_current_branch_on_unborn_head_resolves_the_real_branch_name(tmp_path):
    """A fresh repo with no commits is not detached — it's on `main`."""
    repo = tmp_path / "unborn"
    init_git_repo(repo)  # git init -b main, no commit
    assert current_branch(repo) == "main"


def test_current_branch_on_real_detached_head_returns_HEAD(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    first = commit_files(repo, {"a.txt": "1\n"}, message="first")
    commit_files(repo, {"a.txt": "2\n"}, message="second")
    subprocess.run(
        ["git", "checkout", first], cwd=repo, check=True, capture_output=True,
    )
    assert current_branch(repo) == "HEAD"


def test_current_branch_raises_when_git_genuinely_cannot_answer(tmp_path):
    """Neither `rev-parse --abbrev-ref HEAD` nor `symbolic-ref` can answer.

    Verified against real git 2.43: a repo whose ``.git/HEAD`` is missing
    fails *both* probes identically (git no longer even recognises the
    directory as a repository) — the case that must raise rather than
    silently answer "HEAD", indistinguishable from the unborn/detached
    cases above.
    """
    repo = tmp_path / "broken"
    init_git_repo(repo)
    (repo / ".git" / "HEAD").unlink()
    with pytest.raises(gitops.CurrentBranchUnresolvable):
        current_branch(repo)


def test_current_branch_not_a_repo_at_all_raises(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(gitops.CurrentBranchUnresolvable):
        current_branch(plain)


# ── Callers: the fix must not just move the bug to a new crash site ────
#
# "Tests go through the caller the defect reaches, not only through
# current_branch directly" (#1340's own instruction) — a unit test on
# current_branch alone can't show that a caller mistook a raise for
# something it should catch, or vice versa. These monkeypatch
# gitops.current_branch itself so the caller's own handling is what's
# under test, independent of which real git state would trigger it.


def test_default_branch_survives_current_branch_failure(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    init_git_repo(repo)  # no main/master to find, no remote — reaches current_branch

    def _raise(_repo_root):
        raise gitops.CurrentBranchUnresolvable("simulated")

    monkeypatch.setattr(gitops, "current_branch", _raise)
    # No commit exists either, so the final rev_parse(HEAD) fallback also
    # can't resolve — best-effort function, honest None instead of a raise.
    assert gitops.default_branch(repo) is None


def test_fast_forward_branch_refuses_instead_of_guessing_on_probe_failure(
    tmp_path, monkeypatch,
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1\n"}, message="base")
    subprocess.run(
        ["git", "branch", "topic"], cwd=repo, check=True, capture_output=True,
    )
    commit_files(repo, {"a.txt": "2\n"}, message="advance main")

    def _raise(_repo_root):
        raise gitops.CurrentBranchUnresolvable("simulated")

    monkeypatch.setattr(gitops, "current_branch", _raise)
    result = gitops.fast_forward_branch(repo, "topic", "main")
    # Whether `topic` is the checked-out branch decides between two
    # different mutation strategies — guessing wrong when git can't answer
    # risks update-ref against a branch actually checked out elsewhere.
    # Refusing is the only safe answer.
    assert result.success is False
    assert "current" in result.detail.lower() or "branch" in result.detail.lower()


def test_has_pushed_upstream_reads_probe_failure_as_false(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "1\n"}, message="base")

    def _raise(_repo_root):
        raise gitops.CurrentBranchUnresolvable("simulated")

    monkeypatch.setattr(gitops, "current_branch", _raise)
    # Docstring already promises "any git failure reads as False" —
    # this is that promise, now that the probe raises instead of
    # returning a sentinel.
    assert gitops.has_pushed_upstream(repo) is False


def test_every_current_branch_caller_handles_the_raise():
    """Structural guard: every call site, found fresh, not a fixed list.

    #1340's own warning: "a class defined by listing its members meets the
    member nobody listed" — a test that hardcodes today's known call
    sites goes green over the fifteenth site a future change adds. This
    walks every ``.py`` file under ``src/brr`` at test time and requires
    each call to either sit inside a ``try`` that catches
    ``CurrentBranchUnresolvable`` (or a broader ``RuntimeError`` /
    ``Exception`` — the class is a ``RuntimeError``, so either already
    catches it) or carry an explicit ``# current-branch: propagates``
    comment on the call line, marking a conscious decision to let it
    reach the caller's own crash handling instead of a silent oversight.

    The final assertion is the guard's own sanity check: if
    ``current_branch`` is ever renamed, the AST matcher below would
    silently match nothing and this test would vacuously pass, reporting
    a clean bill of health for a codebase it never actually looked at.
    Asserting a floor count turns that into a loud failure instead.
    """
    import ast

    src_root = Path(gitops.__file__).resolve().parent
    propagates_marker = "current-branch: propagates"
    # A handler catches the raise if it's CurrentBranchUnresolvable itself
    # or anything it's an instance of (RuntimeError, Exception,
    # BaseException) — checked by name since resolving arbitrary handler
    # expressions to a live type via pure AST isn't worth the complexity
    # this guard needs to stay readable.
    catching_names = {
        "CurrentBranchUnresolvable",
        "RuntimeError",
        "Exception",
        "BaseException",
    }

    unhandled: list[str] = []
    total_calls_found = 0

    for path in sorted(src_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        lines = text.splitlines()
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node

        def _is_current_branch_call(node: ast.AST, *, in_gitops: bool) -> bool:
            if not isinstance(node, ast.Call):
                return False
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "current_branch":
                return isinstance(fn.value, ast.Name) and fn.value.id == "gitops"
            if in_gitops and isinstance(fn, ast.Name) and fn.id == "current_branch":
                return True
            return False

        def _enclosing_try_catches(node: ast.AST) -> bool:
            current = node
            while id(current) in parents:
                current = parents[id(current)]
                if isinstance(current, ast.Try):
                    for handler in current.handlers:
                        if handler.type is None:  # bare `except:`
                            return True
                        # `except gitops.CurrentBranchUnresolvable:` stores
                        # the exception name as an Attribute's `.attr`
                        # string, not a bare Name — collect both forms
                        # (also handles a tuple of exception types, which
                        # ast.walk descends into either way).
                        names = [
                            n.id for n in ast.walk(handler.type)
                            if isinstance(n, ast.Name)
                        ] + [
                            n.attr for n in ast.walk(handler.type)
                            if isinstance(n, ast.Attribute)
                        ]
                        if any(n in catching_names for n in names):
                            return True
                if isinstance(current, ast.FunctionDef):
                    # A call inside `def current_branch` itself (the
                    # definition, not a call site) — never reached, since
                    # the definition body has no ast.Call to itself, but
                    # stop climbing at function boundaries regardless: a
                    # try in an *outer* function does not protect a call
                    # inside a nested one.
                    break
            return False

        in_gitops = path == Path(gitops.__file__).resolve()
        for node in ast.walk(tree):
            if not _is_current_branch_call(node, in_gitops=in_gitops):
                continue
            # Skip the function definition site itself (`def current_branch`
            # has no self-call, but guard anyway for safety).
            total_calls_found += 1
            line_idx = node.lineno - 1
            line_text = lines[line_idx] if 0 <= line_idx < len(lines) else ""
            if propagates_marker in line_text:
                continue
            if _enclosing_try_catches(node):
                continue
            unhandled.append(f"{path.relative_to(src_root.parent.parent)}:{node.lineno}")

    # Sanity floor: #1340's audit found 14 external call sites plus 3
    # inside gitops.py itself. A future rename that silently zeroes out
    # `total_calls_found` must fail loudly here, not read as "all clean".
    assert total_calls_found >= 15, (
        f"expected to find at least 15 gitops.current_branch call sites, "
        f"found {total_calls_found} — has current_branch been renamed? "
        f"(this assertion is the guard's own sanity check, #1340)"
    )
    assert not unhandled, (
        "gitops.current_branch call site(s) with no visible failure "
        "handling — wrap in try/except catching CurrentBranchUnresolvable "
        "(or a broader RuntimeError/Exception), or mark deliberate "
        "propagation with a `# current-branch: propagates` comment on the "
        "call line:\n  " + "\n  ".join(unhandled)
    )
