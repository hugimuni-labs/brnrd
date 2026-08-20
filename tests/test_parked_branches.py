import json
import subprocess

from brr import forge_pr_cache, gitops, parked_branches
from brr.run import Run


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "base").write_text("base")
    _git(tmp_path, "add", "base")
    _git(tmp_path, "commit", "-m", "base")
    return tmp_path


def _branch(repo, name):
    _git(repo, "switch", "-c", name, "main")
    path = repo / name.replace("/", "-")
    path.write_text(name)
    _git(repo, "add", path.name)
    _git(repo, "commit", "-m", name)
    _git(repo, "switch", "main")


def _cache(repo, prs):
    path = forge_pr_cache.cache_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": "2099-01-01T00:00:00Z", "prs": prs}))


def test_detects_parked_branch_and_excludes_open_pr_and_live_owner(tmp_path):
    repo = _repo(tmp_path)
    for name in ("brr/parked", "brr/has-pr", "brr/live"):
        _branch(repo, name)
    _cache(repo, [{"branch": "brr/has-pr", "state": "OPEN"}])
    Run(
        id="run-live", event_id="evt", body="", status="running",
        meta={"branch_name": "brr/live"},
    ).save(repo / ".brr" / "runs")

    assert [item.name for item in parked_branches.detect(repo)] == ["brr/parked"]


def test_live_branch_match_is_exact_not_prefix(tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "brr/work")
    _branch(repo, "brr/work-more")
    _cache(repo, [])
    Run(
        id="run-live", event_id="evt", body="", status="running",
        meta={"branch_name": "brr/work"},
    ).save(repo / ".brr" / "runs")

    assert [item.name for item in parked_branches.detect(repo)] == ["brr/work-more"]


def test_render_is_present_only_for_nonempty_detector_result():
    assert parked_branches.render([]) is None
    line = parked_branches.render(
        [parked_branches.ParkedBranch("brr/x", 2, 1000)], now=4600,
    )
    # "unmerged" is load-bearing, not decoration (#1544): the number counts
    # commits with no patch-equivalent on the default branch, and a reader who
    # takes it as "commits ahead" will keep reading diffs that were already
    # merged. The noun is the whole correction.
    assert line == "parked branches: brr/x (2 unmerged commits, pushed 1h ago)"


def test_ergo_warning_is_once_per_branch_per_daemon_lifetime(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        parked_branches, "detect",
        lambda _repo: [parked_branches.ParkedBranch("brr/x", 2, None)],
    )
    parked_branches._WARNED.clear()
    parked_branches.warn_new(tmp_path)
    parked_branches.warn_new(tmp_path)
    assert capsys.readouterr().out.count("[brnrd:ergo]") == 1


def _advance_main(repo, marker):
    """Move ``main`` on by an unrelated commit.

    Not scenery. A cherry-pick onto a ``main`` that has *not* moved produces a
    commit with the same tree, parent, message, author and date — so git
    writes the **same sha**, the branch and main become identical, and a test
    asserting "already merged" passes without patch-id equivalence ever being
    consulted. ``--no-ff`` does not help: it changes which ref moves, not which
    object is written. Diverging main first is what makes the replay a genuine
    second sha carrying the same patch, which is the whole thing under test.
    """
    path = repo / marker
    path.write_text(marker)
    _git(repo, "add", marker)
    _git(repo, "commit", "-m", f"main moves: {marker}")


def test_rebase_merged_branch_is_not_parked_work(tmp_path):
    """#1544: a branch whose work reached main by rebase holds nothing.

    The predicate used to be ``git rev-list --count main..branch`` — a
    *reachability* question. Rebase-merging a PR replays the same patch onto
    main under a new sha, so the old branch stays forever ahead in the graph
    while holding nothing main does not have. `detect` listed it as parked
    work, and the only way to find out otherwise was to read the diff — which
    is the one cost this surface exists to remove.

    Measured on the surface's first real day (2026-08-20): six branches
    rendered, four carried nothing.

    Driven red first: with ``ahead_count`` the last assertion reads
    ``["brr/rebased"]``.
    """
    repo = _repo(tmp_path)
    _branch(repo, "brr/rebased")
    _advance_main(repo, "unrelated")
    _git(repo, "cherry-pick", "brr/rebased")
    _cache(repo, [])

    # Still ahead by reachability — the branch's own commit object is
    # unreachable from main. That is the fact the old predicate read, and it
    # is true and useless.
    assert gitops.ahead_count(repo, "main", "brr/rebased") == 1
    # ...and holds nothing main lacks.
    assert gitops.unmerged_commit_count(repo, "main", "brr/rebased") == 0

    assert parked_branches.detect(repo) == []


def test_a_branch_with_real_work_beside_a_merged_one_still_shows(tmp_path):
    """The other half of #1544 — the fix must not silence genuine parked work.

    A leftover ref and a live contribution sat side by side on the surface
    that day, rendered identically. Exactly one of them should survive.
    """
    repo = _repo(tmp_path)
    _branch(repo, "brr/rebased")
    _branch(repo, "brr/still-owed")
    _advance_main(repo, "unrelated")
    _git(repo, "cherry-pick", "brr/rebased")
    _cache(repo, [])

    items = parked_branches.detect(repo)
    assert [item.name for item in items] == ["brr/still-owed"]
    assert items[0].commits == 1
    assert "1 unmerged commit" in parked_branches.render(items)


def test_unmerged_count_is_none_not_zero_when_git_refuses(tmp_path):
    """``None`` and ``0`` mean opposite things to `detect`.

    ``0`` says "this branch holds nothing" and drops it from the surface. A
    git refusal (unknown ref, not a repo) knows nothing about the branch, and
    collapsing that to ``0`` would hide real parked work behind an error —
    the empty-result class, in one return value.
    """
    repo = _repo(tmp_path)
    assert gitops.unmerged_commit_count(repo, "main", "brr/does-not-exist") is None
