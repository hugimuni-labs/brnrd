import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from brr import forge_pr_cache, gitops, parked_branches
from brr.run import Run


def _join_sweep(timeout: float = 5.0) -> None:
    """Wait for any in-flight background sweep to land before asserting.

    ``refresh_if_stale_async`` (the only thing that still calls ``detect``)
    always runs on a thread named ``"parked-branches"`` — same idiom as
    ``test_lane_liveness.py``'s own ``for thread in threading.enumerate()``
    join, because a test that reads the cache before its refresh thread
    finishes is racing the very asynchrony this module now provides on
    purpose.
    """
    for thread in threading.enumerate():
        if thread.name == "parked-branches":
            thread.join(timeout=timeout)


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


@pytest.fixture(autouse=True)
def _fresh_sweep_state():
    """This module's process-lifetime state, reset per test.

    Four globals now, all added or repurposed when the walk moved off the
    caller's own thread (2026-09-22, the-tick-that-breathes): `_WARNED`
    (which branches have been announced), `_cached_at` / `_cached_items`
    (the async-refreshed cache `read_cached` and `warn_new` both read
    instead of calling `detect` themselves), and `_refreshing` (the
    in-flight guard `refresh_if_stale_async` uses to avoid starting a
    second walk while one is still running). The TTL fixture this replaces
    was added 2026-09-11 after a pre-existing test cleared only `_WARNED`,
    passed alone, and failed in a full run — a second sweep inside the
    window returned before it could print anything. Same failure shape
    would recur here with any one of the four left dirty.

    Autouse rather than another `monkeypatch.setattr` line per test, because
    the failure mode is "a future test forgets one of them", and a fixture
    is the only version of this that a new test cannot omit.
    """
    def _reset():
        parked_branches._WARNED.clear()
        parked_branches._cached_at = None
        parked_branches._cached_items = []
        parked_branches._refreshing = False

    _reset()
    yield
    _join_sweep()
    _reset()


def test_ergo_warning_is_once_per_branch_per_daemon_lifetime(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        parked_branches, "detect",
        lambda _repo: [parked_branches.ParkedBranch("brr/x", 2, None)],
    )
    parked_branches.warn_new(tmp_path)
    _join_sweep()
    parked_branches.warn_new(tmp_path)
    _join_sweep()
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


def test_warn_new_sweeps_at_most_once_per_ttl(tmp_path, monkeypatch):
    """The walk is the expensive half, and it must never run twice per window.

    One sweep is a ``git cherry`` per local ``brr/*`` branch — 15.3s over 346
    branches (2026-09-11), 130.9s over 453 branches (2026-09-22, the walk's
    cost has grown 8.5x while the branch count grew 1.3x). Its output was
    already deduped per process (``_WARNED``), so every sweep after the first
    bought nothing at all. Pinned on ``detect`` call count, not on printed
    text: the defect was the *walk*, so the walk is what this has to count.
    Each ``warn_new`` call is joined before the next one fires, so the
    ``now=`` values below still pin the TTL boundary exactly the way the
    synchronous version of this test did — only the walk itself moved to a
    background thread, not the accounting.
    """
    calls: list[Path] = []
    monkeypatch.setattr(parked_branches, "detect", lambda root: calls.append(root) or [])

    parked_branches.warn_new(tmp_path, ttl=300.0, now=1_000.0)
    _join_sweep()
    parked_branches.warn_new(tmp_path, ttl=300.0, now=1_001.0)
    _join_sweep()
    parked_branches.warn_new(tmp_path, ttl=300.0, now=1_299.9)
    _join_sweep()
    assert len(calls) == 1, "a tick inside the TTL must not re-walk the branches"

    parked_branches.warn_new(tmp_path, ttl=300.0, now=1_300.0)
    _join_sweep()
    assert len(calls) == 2, "the sweep must resume once the TTL has elapsed"


def test_warn_new_throttles_even_when_a_sweep_raises(tmp_path, capsys, monkeypatch):
    """A failing sweep must not become a retry-every-tick loop.

    The walk now runs on its own background thread (`refresh_if_stale_async`),
    so a raising `detect` no longer propagates out of `warn_new` at all — it is
    caught and printed inside the thread, the same "an ergonomics note must
    never sink the caller" contract `daemon.py`'s own guard used to provide
    from the outside. What must still hold: the TTL is stamped *before* the
    walk runs, so a git failure does not restore the per-tick walk this
    throttle exists to stop — the pathological case, since a broken sweep is
    also the one most likely to be slow.
    """
    calls: list[Path] = []

    def _boom(root):
        calls.append(root)
        raise RuntimeError("git said no")

    monkeypatch.setattr(parked_branches, "detect", _boom)

    parked_branches.warn_new(tmp_path, ttl=300.0, now=2_000.0)
    _join_sweep()
    assert len(calls) == 1
    assert "parked-branch sweep failed" in capsys.readouterr().out
    assert not parked_branches._refreshing, "a raised sweep must clear the in-flight guard"

    # Second call inside the TTL returns without reaching `detect` at all —
    # if it did, `calls` would grow to 2.
    parked_branches.warn_new(tmp_path, ttl=300.0, now=2_010.0)
    _join_sweep()
    assert len(calls) == 1


def test_refresh_if_stale_async_returns_before_the_walk_completes(tmp_path, monkeypatch):
    """The whole point of this module's rewrite: the caller never waits.

    Before 2026-09-22 this walk ran inline in two hot paths — the daemon's
    main-loop tick and every dispatched run's own boot-prompt assembly — and
    measured 130.9s over 453 branches on this account. A fix that still
    blocks the caller until the walk finishes has fixed nothing; this test
    pins the actual property that matters.
    """
    started = threading.Event()
    release = threading.Event()

    def _slow_detect(_root):
        started.set()
        assert release.wait(timeout=5), "test setup: release was never signalled"
        return [parked_branches.ParkedBranch("brr/slow", 1, None)]

    monkeypatch.setattr(parked_branches, "detect", _slow_detect)

    t0 = time.monotonic()
    assert parked_branches.refresh_if_stale_async(tmp_path) is True
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, "refresh_if_stale_async must return before the walk completes"
    assert started.wait(timeout=5), "the background thread never started the walk"

    # A second call while the first is still in flight starts nothing new —
    # two concurrent walks would double-pay a cost this exists to spend once.
    assert parked_branches.refresh_if_stale_async(tmp_path) is False

    release.set()
    _join_sweep()
    assert [item.name for item in parked_branches.read_cached(tmp_path)] == ["brr/slow"]


def test_read_cached_is_empty_before_the_first_sweep_lands(tmp_path, monkeypatch):
    """A cold cache renders nothing rather than blocking the caller for it.

    This is the honest cost `read_cached`'s own docstring names: a fresh
    daemon process (or a test) that has never swept yet gets an empty list
    for up to one TTL window, never a wait for the walk to finish.
    """
    release = threading.Event()

    def _slow_detect(_root):
        assert release.wait(timeout=5), "test setup: release was never signalled"
        return [parked_branches.ParkedBranch("brr/slow", 1, None)]

    monkeypatch.setattr(parked_branches, "detect", _slow_detect)

    assert parked_branches.read_cached(tmp_path) == []

    release.set()
    _join_sweep()


def test_the_two_wiring_points_read_the_cache_not_the_walk():
    """A structural guard — neither call site is reachable from a unit test.

    Both live inside `daemon.start()`'s main loop and `prompts.py`'s boot
    bundle assembly, neither driven directly here. An edit to either that
    quietly went back to calling `detect(...)` inline would restore the
    130.9s synchronous walk this report exists to remove, and the suite
    would stay green without this pin — same shape as
    `test_lane_liveness.py`'s own `test_the_two_wiring_points_exist_in_the_daemon`.
    """
    from brr import daemon, prompts

    daemon_source = Path(daemon.__file__).read_text(encoding="utf-8")
    assert "parked_branches.warn_new(repo_root)" in daemon_source, (
        "the daemon's main loop no longer sweeps parked branches at all"
    )
    assert "parked_branches.detect(repo_root)" not in daemon_source, (
        "the daemon's main loop calls detect() directly again — that is the "
        "130.9s synchronous walk this module's cache exists to prevent"
    )

    prompts_source = Path(prompts.__file__).read_text(encoding="utf-8")
    assert "parked_branches.read_cached(repo_root)" in prompts_source, (
        "the boot bundle no longer reads the parked-branches cache"
    )
    assert "parked_branches.detect(repo_root)" not in prompts_source, (
        "the boot bundle calls detect() directly again — every dispatched "
        "run's boot would pay the full walk synchronously"
    )
