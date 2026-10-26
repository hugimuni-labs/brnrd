import json
import subprocess
from pathlib import Path


from brr import forge_pr_cache, parked_branches
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


