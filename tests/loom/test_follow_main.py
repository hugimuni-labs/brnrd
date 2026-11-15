"""Green main is offered once; the real runtime verb merges/checks before the idle load.
CI gates, unchecked tips, rollback and load receipts run against real git repositories."""

import json
import os
import subprocess
from pathlib import Path
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import loom, update
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.port import PortError, parse_boundary, render_boundary
from brr.loom.runtime.since import since_gone

from _step import Loom, wait_until, write_thread

STRAND = "s-ab12-aaaaaa"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class Forge:
    def __init__(self, verdict):
        self.verdict_of, self.asked, self.unchecked = verdict, [], set()

    def description(self, url, number):
        return {"number": number, "title": f"Change {number}", "body": f"Description {number}"}

    def ran(self, url, sha):
        return sha not in self.unchecked

    def gates(self, url, pull):
        return None

    def judge(self, url, sha, gates):
        self.asked.append(sha)
        return self.verdict_of, "Backend tests" if self.verdict_of == "red" else ""


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A home, an origin whose main is one merge ahead, and the checkout the loom runs from."""
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    origin, source, upstream = tmp_path / "origin.git", tmp_path / "source", tmp_path / "upstream"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(upstream))
    # A tiny candidate program, with the production config parser. The check
    # really imports this candidate in a fresh process, never the pytest tree.
    package = upstream / "src/brr/loom/runtime"
    package.mkdir(parents=True)
    for name in ("src/brr/__init__.py", "src/brr/loom/__init__.py",
                 "src/brr/loom/runtime/__init__.py", "src/brr/loom/runtime/loom.py",
                 "src/brr/loom/runtime/__main__.py"):
        (upstream / name).write_text("")
    shutil.copyfile(Path(update.__file__).with_name("config.py"), package / "config.py")
    (upstream / "loom.py").write_text("v1\n")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-qm", "v1")
    _git(upstream, "push", "-q", "origin", "HEAD:main")
    _git(tmp_path, "clone", "-q", str(origin), str(source))
    _git(upstream, "switch", "-qc", "loom/feature")
    (upstream / "feature.py").write_text("new\n")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-qm", "feature")
    _git(upstream, "switch", "-q", "main")
    _git(upstream, "merge", "-q", "--no-ff", "-m", "Merge pull request #7 from o/loom/feature",
         "loom/feature")
    _git(upstream, "push", "-q", "origin", "main")
    monkeypatch.setenv("BRNRD_LOOM_SOURCE", str(source))
    monkeypatch.setattr(update, "EVERY", 0)
    monkeypatch.setattr(update, "LOOK_EVERY", 0)
    execs = []
    monkeypatch.setattr(os, "execv", lambda *args: execs.append(args))

    class World:
        pass

    w = World()
    w.root, w.source, w.upstream, w.execs = root, source, upstream, execs
    w.old, w.tip = _git(source, "rev-parse", "HEAD"), _git(upstream, "rev-parse", "HEAD")
    w.forge = Forge("green")
    monkeypatch.setattr(update, "forge_for", lambda config: w.forge)
    return w


def test_single_branch_install_can_fetch_apply_and_pull(world, tmp_path):
    source = tmp_path / "single"
    _git(tmp_path, "clone", "--single-branch", "--branch", "main",
         str(world.upstream), str(source))
    assert _git(source, "config", "remote.origin.fetch") == "+refs/heads/main:refs/remotes/origin/main"
    assert _git(source, "rev-parse", "--is-shallow-repository") == "false"
    assert _git(source, "rev-list", "--count", "HEAD") == _git(world.upstream, "rev-list", "--count", "HEAD")
    (world.upstream / "next.py").write_text("next\n")
    _git(world.upstream, "add", "next.py")
    _git(world.upstream, "commit", "-qm", "next")
    dev = update.DevSource(source, world.forge)
    offer = dev.poll()
    assert isinstance(offer, update.Offer)
    assert dev.apply(offer)
    assert _git(source, "rev-parse", "HEAD") == _git(world.upstream, "rev-parse", "HEAD")
    (world.upstream / "next.py").write_text("later\n")
    _git(world.upstream, "commit", "-qam", "later")
    _git(source, "pull", "--ff-only", "origin", "main")
    assert _git(source, "rev-parse", "HEAD") == _git(world.upstream, "rev-parse", "HEAD")


def _degraded(root):
    return [f for f in read_facts(Home(root)) if f.kind == "loom.degraded" and f.data["step"] == "update"]


def _run(root, until, timeout=10):
    looming = Loom(root, tick=0.01)
    looming.start()
    try:
        wait_until(until, timeout, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors


def _offered(world):
    return update.latest(read_facts(Home(world.root)), Home(world.root), world.source, "update.offered")


def _offer(world):
    _run(world.root, lambda: _offered(world))


def _take(world, action="take", why=""):
    from brr.loom.runtime.__main__ import main
    return main(["update", action, "--home", str(world.root), *(["--why", why] if why else [])])


def _load(world):
    looming = Loom(world.root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: _offered(world), 10, looming.dump)
        assert _git(world.source, "rev-parse", "HEAD") == _offered(world).data["old"]
        assert not world.execs
        assert _take(world) == 0
        wait_until(lambda: world.execs, 10, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors


def test_a_move_names_squashed_pull_requests_and_not_a_branch_s_own_commits(world):
    # A squash lands as one parent titled "<title> (#N)"; a branch's commit with
    # a "(#N)" of its own is not a landing on main.
    _git(world.upstream, "switch", "-qc", "loom/other")
    (world.upstream / "other.py").write_text("x\n")
    _git(world.upstream, "add", "-A")
    _git(world.upstream, "commit", "-qm", "cherry-picked fix (#9)")
    _git(world.upstream, "switch", "-q", "main")
    _git(world.upstream, "merge", "-q", "--no-ff", "-m", "Merge pull request #10 from o/loom/other",
         "loom/other")
    (world.upstream / "loom.py").write_text("v2\n")
    _git(world.upstream, "commit", "-qam", "loom: drop the alias (#8)")
    # A revert names the reverted pull request inside its quotes: only its own (#N) lands.
    (world.upstream / "loom.py").write_text("v1\n")
    _git(world.upstream, "commit", "-qam", 'Revert "loom: drop the alias (#8)" (#11)')
    (world.upstream / "loom.py").write_text("v2\n")
    _git(world.upstream, "commit", "-qam", 'Revert "Revert "loom: drop the alias (#8)" (#11)"')
    _git(world.upstream, "push", "-q", "origin", "main")
    world.tip = _git(world.upstream, "rev-parse", "HEAD")
    _load(world)
    assert _git(world.source, "rev-parse", "HEAD") == world.tip
    _run(world.root, lambda: [f for f in read_facts(Home(world.root)) if f.kind == "loom.moved"])
    [moved] = [f for f in read_facts(Home(world.root)) if f.kind == "loom.moved"]
    assert moved.data["merges"] == ["#7", "#10", "#8", "#11"]
    assert update.moved_text(moved) == f"loom moved {world.old[:8]} → {world.tip[:8]}, loaded #7, #10, #8, #11, fast-forward over 0 own commits"


def test_a_green_tip_fast_forwards_re_execs_and_the_next_start_says_what_loaded(world):
    _load(world)
    assert _git(world.source, "rev-parse", "HEAD") == world.tip
    assert not _degraded(world.root)
    # The re-exec'd loom is the next start: it names the move and the merge it loaded.
    _run(world.root, lambda: [f for f in read_facts(Home(world.root)) if f.kind == "loom.moved"])
    [moved] = [f for f in read_facts(Home(world.root)) if f.kind == "loom.moved"]
    assert (moved.data["from"], moved.data["to"], moved.data["merges"]) == (world.old, world.tip, ["#7"])
    assert moved.id == f"loom.moved:{moved.data['started']}"
    assert update.moved_text(moved) == f"loom moved {world.old[:8]} → {world.tip[:8]}, loaded #7, fast-forward over 0 own commits"


def test_a_loom_the_shim_started_re_execs_through_the_shim(world, monkeypatch):
    # Python resolves a linked ``current`` into sys.executable: re-exec'ing that would keep the old code.
    import sys
    from brr.loom.runtime import entry
    entry.write_shim()
    monkeypatch.setattr(sys, "orig_argv", ["/old/bin/python", "-m", "brr", "runtime", "loom", "--home", "/h"])
    _load(world)
    assert world.execs[0] == (str(entry.shim()), [str(entry.shim()), "runtime", "loom", "--home", "/h"])


def _receipt(world, n=1):
    """Commits the receipt workflow pushes on main after a release: no check ever runs on them."""
    for i in range(n):
        (world.upstream / "README.md").write_text(f"receipt {i}\n")
        _git(world.upstream, "add", "-A")
        _git(world.upstream, "commit", "-qm", f"receipt: {i} commits on main")
        world.forge.unchecked.add(_git(world.upstream, "rev-parse", "HEAD"))
    _git(world.upstream, "push", "-q", "origin", "main")


def test_an_unchecked_tip_moves_the_loom_to_the_green_merge_below_it(world):
    _receipt(world, 2)
    _load(world)
    assert _git(world.source, "rev-parse", "HEAD") == world.tip
    assert world.forge.asked[-1] == world.tip


def test_only_unchecked_commits_ahead_offer_no_move(world):
    _git(world.source, "pull", "-q", "--ff-only", "origin", "main")
    _receipt(world)
    source = update.DevSource(world.source, world.forge)
    assert source.poll() is None
    assert world.forge.asked == []


def test_a_red_tip_waits_without_a_fact(world):
    world.forge.verdict_of = "red"
    _run(world.root, lambda: len(world.forge.asked) >= 3)
    assert _git(world.source, "rev-parse", "HEAD") == world.old
    assert not world.execs and not _degraded(world.root)


def _github(monkeypatch, world, tip_runs):
    """GitHub as the real ``GitHubChecks`` reads it: pull #7's head ran CI and a skipped
    docs deploy; main's tip ran ``tip_runs``."""
    import io
    pr_runs = [{"name": "Backend tests", "status": "completed", "conclusion": "success"},
               {"name": "deploy", "status": "completed", "conclusion": "skipped"}]
    bodies = {"pulls/7": {"head": {"sha": "pr7head"}, "title": "Change 7", "body": "Description 7"},
              "commits/pr7head/check-runs?per_page=100": {"check_runs": pr_runs},
              f"commits/{world.tip}/check-runs?per_page=100": {"check_runs": tip_runs}}

    def urlopen(req, timeout):
        path = req.full_url.removeprefix("https://api.github.com/repos/o/r/")
        return io.BytesIO(json.dumps(bodies[path]).encode())

    monkeypatch.setattr(update, "slug", lambda url: "o/r")  # origin is a local bare repo
    monkeypatch.setattr(update.urllib.request, "urlopen", urlopen)
    checks = update.GitHubChecks("t")
    monkeypatch.setattr(checks, "_token", lambda: "t")
    monkeypatch.setattr(update, "forge_for", lambda config: checks)


def test_a_tip_whose_only_failure_is_a_deploy_fast_forwards(world, monkeypatch):
    # 29f304c9 on 2026-11-01: the Scaleway registry push 500'd, every code check green, ~2 h held.
    _github(monkeypatch, world, [
        {"name": "Backend tests", "status": "completed", "conclusion": "success"},
        {"name": "Build and publish image", "status": "completed", "conclusion": "failure"},
        {"name": "Deploy the mirrored image", "status": "completed", "conclusion": "skipped"},
        {"name": "deploy", "status": "completed", "conclusion": "failure"}])
    _load(world)
    assert _git(world.source, "rev-parse", "HEAD") == world.tip


def test_a_tip_red_on_a_gate_stays_and_says_so_once(world, monkeypatch):
    _github(monkeypatch, world, [
        {"name": "Backend tests", "status": "completed", "conclusion": "failure"},
        {"name": "Build and publish image", "status": "completed", "conclusion": "success"}])
    line = f"update: {world.tip[:8]} is red on Backend tests; staying on {world.old[:8]}"
    log = world.root / "loom/loom.log"
    looming = Loom(world.root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: log.exists() and line in log.read_text(), 10, looming.dump)
        seen = update.SEEN["at"]
        wait_until(lambda: update.SEEN["at"] != seen, 10, looming.dump)  # a later poll, still red
    finally:
        looming.halt()
    assert not looming.errors
    assert _git(world.source, "rev-parse", "HEAD") == world.old
    assert not world.execs and not _degraded(world.root)
    assert log.read_text().count(line) == 1


def test_a_dirty_checkout_gets_an_offer_but_take_refuses_and_leaves_it_as_it_was(world, capsys):
    (world.source / "loom.py").write_text("my edit\n")
    _offer(world)
    assert _take(world) == 1
    error = capsys.readouterr().err
    assert "1 uncommitted change" in error and "loom.py" in error
    assert "commit tracked changes first" in error and "not touched" in error
    assert _offered(world).data["dirty"]
    assert not _degraded(world.root)
    assert _git(world.source, "rev-parse", "HEAD") == world.old
    assert (world.source / "loom.py").read_text() == "my edit\n"
    assert not world.execs


def test_a_diverged_checkout_on_its_own_branch_merges_and_loads(world):
    _git(world.source, "switch", "-qc", "my-code")
    (world.source / "mine.py").write_text("local\n")
    _git(world.source, "add", "-A")
    _git(world.source, "commit", "-qm", "local only")
    mine = _git(world.source, "rev-parse", "HEAD")
    _load(world)
    result = _git(world.source, "rev-parse", "HEAD")
    assert _git(world.source, "rev-parse", "HEAD^1") == mine
    assert _git(world.source, "rev-parse", "HEAD^2") == world.tip
    assert (world.source / "mine.py").read_text() == "local\n"
    assert _git(world.source, "branch", "--show-current") == "my-code"
    assert not _degraded(world.root)
    _run(world.root, lambda: any(f.kind == "loom.moved" for f in read_facts(Home(world.root))))
    moved = [f for f in read_facts(Home(world.root)) if f.kind == "loom.moved"][-1]
    assert moved.data["to"] == result
    starts = [f for f in read_facts(Home(world.root)) if f.kind == "loom.started"]
    assert "origin#7" in starts[-1].data["loaded"]
    from brr.loom.runtime.receipts import record_start
    restart = record_start(Home(world.root), read_facts(Home(world.root)), root=world.source)
    assert "origin#7" in restart.data["loaded"]
    from brr.loom.runtime.receipts import held_merge
    assert held_merge({"kind": "pr", "ref": "7"}, restart.data, root=world.source) == world.tip
    assert held_merge({"kind": "pr", "ref": "9"}, restart.data, root=world.source) is None
    # Rolling back before the take removes both the loaded claim and its proof.
    _git(world.source, "reset", "--hard", mine)
    rollback = record_start(Home(world.root), read_facts(Home(world.root)), root=world.source)
    assert "origin#7" not in rollback.data["loaded"]
    assert held_merge({"kind": "pr", "ref": "7"}, rollback.data, root=world.source) is None
    assert (moved.data["mode"], moved.data["own"], moved.data["merges"]) == ("merge", 1, ["#7"])


def test_the_move_and_the_re_exec_wait_for_an_idle_tick(world, monkeypatch):
    busy = [True]
    monkeypatch.setattr(loom, "_idle", lambda home, young=None: not busy[0])
    looming = Loom(world.root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: _offered(world), 10, looming.dump)
        assert _take(world) == 0
        wait_until(lambda: any("waiting for an idle tick" in line for line in
                              (world.root / "loom/loom.log").read_text().splitlines()), 10, looming.dump)
        assert _git(world.source, "rev-parse", "HEAD") == world.tip and not world.execs
        busy[0] = False
        wait_until(lambda: world.execs, 10, looming.dump)
    finally:
        looming.halt()
    assert _git(world.source, "rev-parse", "HEAD") == world.tip


def test_an_open_tool_call_of_a_live_body_is_not_idle(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    home = Home(root)
    strand = f"s-{home.install_id()}-aaaaaa"
    room = home.room(strand)
    (room / "port").mkdir(parents=True)

    class Folded:
        holder = {"brnrd": (strand, 1)}

    monkeypatch.setattr(loom, "fold", lambda facts: Folded)
    monkeypatch.setattr(loom, "body_alive", lambda r: True)
    assert loom._idle(home)
    from brr.loom.runtime import tooltrace
    call = '{"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "ls"}}'
    tooltrace.note(room, call, "pre")
    assert not loom._idle(home)
    tooltrace.note(room, call, "post")
    assert loom._idle(home)


def test_a_move_kept_waiting_by_busy_bodies_loosens_its_gate_and_lands(world, monkeypatch):
    # Seated bodies with long calls seldom all pause at once (2026-10-26: 35 s of 44 min).
    monkeypatch.setattr(update, "PATIENCE", ((0, None), (0.3, 60)))
    asked = []

    def idle(home, young=None):
        asked.append(young)
        return young is not None  # a call is always open, but none is younger than a minute
    monkeypatch.setattr(loom, "_idle", idle)
    _load(world)
    assert asked[0] is None and asked[-1] == 60
    assert _git(world.source, "rev-parse", "HEAD") == world.tip
    log = (world.root / "loom/loom.log").read_text()
    assert f"update: {world.tip[:8]} checked; waiting for an idle tick" in log
    assert "(open calls under 60s held it)" in log


def test_an_open_call_older_than_the_limit_does_not_hold_the_move(tmp_path, monkeypatch):
    import time
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    home = Home(root)
    strand = f"s-{home.install_id()}-aaaaaa"
    room = home.room(strand)
    (room / "port").mkdir(parents=True)

    class Folded:
        holder = {"brnrd": (strand, 1)}

    monkeypatch.setattr(loom, "fold", lambda facts: Folded)
    monkeypatch.setattr(loom, "body_alive", lambda r: True)
    from brr.loom.runtime import tooltrace
    tooltrace.note(room, '{"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "gh pr checks --watch"}}', "pre")
    later = time.time() + 120
    monkeypatch.setattr(time, "time", lambda: later)
    assert not loom._idle(home)
    assert not loom._idle(home, 300)
    assert loom._idle(home, 60)


def test_a_run_with_a_conclusion_is_done_though_its_status_says_in_progress(monkeypatch):
    # 1a35ba71 on 2026-10-26: "Repo hygiene" stayed in_progress with conclusion success.
    import io
    runs = [{"status": "completed", "conclusion": "success"},
            {"status": "in_progress", "conclusion": "success"}]
    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda req, timeout: io.BytesIO(json.dumps({"check_runs": runs}).encode()))
    checks = update.GitHubChecks("t")
    monkeypatch.setattr(checks, "_token", lambda: "t")
    url = "https://github.com/o/r.git"
    assert checks.verdict(url, "abc") == "green"
    runs.append({"status": "in_progress", "conclusion": None})
    assert checks.verdict(url, "abc") == "pending"
    runs.append({"status": "in_progress", "conclusion": "failure"})
    assert checks.verdict(url, "abc") == "red"


def test_a_failure_older_than_a_live_run_is_a_rerun_and_reads_pending(monkeypatch):
    # #2506 on 2026-10-29: attempt 1's "Backend tests" failed at 01:42, the shard reran from 01:44.
    import io
    runs = [{"name": "Backend tests", "status": "completed", "conclusion": "failure",
             "started_at": "2026-10-29T01:42:00Z", "completed_at": "2026-10-29T01:42:03Z"},
            {"name": "Backend shard 3/3", "status": "in_progress", "conclusion": None,
             "started_at": "2026-10-29T01:44:05Z", "completed_at": None}]
    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda req, timeout: io.BytesIO(json.dumps({"check_runs": runs}).encode()))
    checks = update.GitHubChecks("t")
    monkeypatch.setattr(checks, "_token", lambda: "t")
    url = "https://github.com/o/r.git"
    assert checks.verdict(url, "abc") == "pending"
    runs[1]["started_at"] = "2026-10-29T01:40:00Z"  # still running from before the failure: fail fast
    assert checks.verdict(url, "abc") == "red"
    runs[1]["started_at"] = None  # queued, not started: newer than any failure
    assert checks.verdict(url, "abc") == "pending"


def _at(minutes):
    return (datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def test_a_seated_body_hears_the_move_once_and_a_new_body_reads_it_in_its_wake(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    home = Home(root)
    install = home.install_id()
    append(home, Fact(kind="body.started", by=f"loom:{install}", at=_at(0), data={
        "strand": STRAND, "gen": 1, "pid": 1, "shell": "fake", "core": "x", "readme": "", "lstart": ""}))
    append(home, Fact(kind="loom.moved", by=f"loom:{install}", at=_at(5), id="loom.moved:x", data={
        "install": install, "from": "a" * 40, "to": "b" * 40, "merges": ["#7"], "started": "x"}))
    line = update.moved_line(read_facts(home), STRAND, 1, install)
    assert line == "moved: loom moved aaaaaaaa → bbbbbbbb, loaded #7"
    # A body started after the move has no boundary line: its wake carries it.
    assert update.moved_line(read_facts(home), STRAND, 2, install) == ""
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    text = render_boundary(STRAND, 1, "brnrd", [], set(), {}, moved=line)
    parse_boundary(text)
    (room / "port/in/boundary.md").write_text(render_boundary(STRAND, 1, "brnrd", [], set(), {}))
    execute("start", room, "{}")
    (room / "port/in/boundary.md").write_text(text)
    assert line in json.loads(execute("post", room, "{}"))["hookSpecificOutput"]["additionalContext"]
    assert execute("post", room, "{}") == ""
    # The wake's since-section names it for a body whose predecessor's cut precedes it.
    rows = [Fact(kind="lease", by="loom", at=_at(-10), data={"thread": "brnrd", "strand": STRAND, "gen": 1}),
            Fact(kind="attempt.ended", by="loom", at=_at(-5), data={
                "thread": "brnrd", "strand": STRAND, "gen": 1, "cause": "settled", "code": 0}),
            *[f for f in read_facts(home) if f.kind == "loom.moved"],
            Fact(kind="lease", by="loom", at=_at(10), data={"thread": "brnrd", "strand": STRAND, "gen": 2})]
    gone = since_gone(rows, STRAND, 2, "brnrd", {}, install)
    assert "loom moved aaaaaaaa → bbbbbbbb, loaded #7 (loom.moved:x)" in gone


def test_no_test_loom_follows_the_checkout_under_test():
    from brr.loom.runtime.receipts import source_root
    assert not (source_root() / ".git").exists(), source_root()


def test_a_wake_says_what_the_loom_runs_against_main_and_what_it_loaded(world, monkeypatch):
    from brr.loom.runtime.receipts import record_start
    monkeypatch.setattr(update, "SEEN", {})
    home = Home(world.root)
    record_start(home, read_facts(home), root=world.source)
    assert loom._loom_line(home, read_facts(home)) == (
        f"loom: runs {world.old[:8]}; main not fetched by this loom (update off, or not polled yet)\n")
    assert isinstance(update.DevSource(world.source, Forge("red")).poll(), update.Held)
    assert loom._loom_line(home, read_facts(home)) == (
        f"loom: runs {world.old[:8]}; main {world.tip[:8]} is 2 commits ahead, not loaded: #7\n")
    _git(world.source, "merge", "-q", "--ff-only", world.tip)
    record_start(home, read_facts(home), root=world.source)
    assert loom._loom_line(home, read_facts(home)) == (
        f"loom: runs {world.tip[:8]}; main {world.tip[:8]}, nothing behind; "
        f"last loom moved {world.old[:8]} → {world.tip[:8]}, loaded #7\n")


def test_a_jack_skips_a_boundary_line_kind_it_does_not_know(tmp_path):
    """A held jack keeps the code it imported; after a move the loom writes kinds that
    code never listed (moved: at a0552d77 raised and dropped the boundary)."""
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    (room / "port/in/boundary.md").write_text(render_boundary(STRAND, 1, "brnrd", [], set(), {}))
    execute("start", room, "{}")
    letter = "- s-ab12-bbbbbb/x1 · from s-ab12-bbbbbb · reply thread:other — the defect"
    text = (f"<!-- port {STRAND} gen 1 · owed s-ab12-bbbbbb/x1 -->\n"
            "status: strand · gen 1\nweather? a kind from a later loom\n"
            f"moved: loom moved aaaaaaaa → bbbbbbbb, loaded #7\nowed:\n{letter}\n")
    assert [view.id for view in parse_boundary(text).letters] == ["s-ab12-bbbbbb/x1"]
    (room / "port/in/boundary.md").write_text(text)
    shown = json.loads(execute("post", room, "{}"))["hookSpecificOutput"]["additionalContext"]
    assert "s-ab12-bbbbbb/x1" in shown
    assert not (room / "port" / "jack-errors.log").exists()
    # A "- " line is still a letter or corrupt.
    with pytest.raises(PortError):
        parse_boundary(text.replace(letter, "- not a letter"))


def _next_tip(world, name="another.py", text="next\n", message="Another change (#8)"):
    (world.upstream / name).write_text(text)
    _git(world.upstream, "add", "-A")
    _git(world.upstream, "commit", "-qm", message)
    _git(world.upstream, "push", "-q", "origin", "main")
    world.tip = _git(world.upstream, "rev-parse", "HEAD")


def _update_letters(world):
    return [f for f in read_facts(Home(world.root))
            if f.kind == "letter" and f.id.startswith("loom/update-")]


def test_an_untouched_checkout_gets_one_letter_and_never_moves_until_take(world):
    _run(world.root, lambda: len(world.forge.asked) >= 3 and _update_letters(world))
    [letter] = _update_letters(world)
    assert "#7 · Change 7\nDescription 7" in letter.data["body"]
    assert "0 own commits" in letter.data["body"]
    assert "tracked files are committed" in letter.data["body"]
    assert letter.data["to"] == f"thread:{Home(world.root).main_thread()}"
    assert _git(world.source, "rev-parse", "HEAD") == world.old
    assert not world.execs
    # Restarting the loom preserves the offer's identity.
    prior = len(world.forge.asked)
    _run(world.root, lambda: len(world.forge.asked) >= prior + 2)
    assert len(_update_letters(world)) == 1
    assert not world.execs


def test_a_conflict_aborts_without_changing_head_index_or_files_then_manual_merge_can_be_taken(world, capsys):
    _next_tip(world, "loom.py", "upstream\n")
    (world.source / "loom.py").write_text("my code\n")
    _git(world.source, "commit", "-qam", "my code")
    (world.source / "untracked.txt").write_bytes(b"keep\x00me")
    before = _git(world.source, "rev-parse", "HEAD")
    status = _git(world.source, "status", "--porcelain")
    _offer(world)
    assert _take(world) == 1
    assert "Conflicting files: loom.py" in capsys.readouterr().err
    assert _git(world.source, "rev-parse", "HEAD") == before
    assert _git(world.source, "status", "--porcelain") == status
    assert (world.source / "loom.py").read_text() == "my code\n"
    assert (world.source / "untracked.txt").read_bytes() == b"keep\x00me"
    assert not (world.source / ".git/MERGE_HEAD").exists()
    assert not world.execs
    # Keep the running loop on the pre-merge commit while reconciling by hand.
    looming = Loom(world.root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: len([f for f in read_facts(Home(world.root)) if f.kind == "loom.started"]) >= 2,
                   10, looming.dump)
        result = subprocess.run(["git", "merge", "--no-edit", world.tip], cwd=world.source, capture_output=True)
        assert result.returncode != 0
        (world.source / "loom.py").write_text("both codes\n")
        _git(world.source, "add", "loom.py")
        _git(world.source, "commit", "-qm", "reconcile by hand")
        reconciled = _git(world.source, "rev-parse", "HEAD")
        assert _take(world) == 0
        wait_until(lambda: world.execs, 10, looming.dump)
        assert _git(world.source, "rev-parse", "HEAD") == reconciled
        assert update.open_offer(read_facts(Home(world.root)), Home(world.root), world.source) is None
    finally:
        looming.halt()
    assert not looming.errors


@pytest.mark.parametrize("failure", ["import", "config"])
@pytest.mark.parametrize("own", [False, True])
def test_a_failing_check_undoes_the_merge_and_keeps_the_offer(world, capsys, failure, own):
    path = "src/brr/loom/runtime/" + ("loom.py" if failure == "import" else "config.py")
    code = "raise RuntimeError('candidate failed import')\n" if failure == "import" else (
        (world.upstream / path).read_text() + "\ndef load_config(root):\n    raise ValueError('candidate failed config')\n")
    _next_tip(world, path, code)
    if own:
        (world.source / "mine.py").write_text("mine\n")
        _git(world.source, "add", "-A")
        _git(world.source, "commit", "-qm", "my addition")
    before = _git(world.source, "rev-parse", "HEAD")
    tree = _git(world.source, "rev-parse", "HEAD^{tree}")
    _offer(world)
    assert _take(world) == 1
    assert "merged tree check failed" in capsys.readouterr().err
    assert _git(world.source, "rev-parse", "HEAD") == before
    assert _git(world.source, "write-tree") == tree
    assert _git(world.source, "status", "--porcelain") == ""
    if own:
        assert (world.source / "mine.py").read_text() == "mine\n"
    assert update.open_offer(read_facts(Home(world.root)), Home(world.root), world.source)
    assert not world.execs


def test_hold_records_why_settles_the_letter_and_the_next_tip_offers_again(world):
    _offer(world)
    letter = _update_letters(world)[0]
    assert _take(world, "hold", "need to reconcile my scheduler") == 0
    facts = read_facts(Home(world.root))
    held = update.latest(facts, Home(world.root), world.source, "update.decided")
    assert held.data["why"] == "need to reconcile my scheduler"
    assert held.data["action"] == "hold"
    from brr.loom.runtime.project import fold
    assert letter.id in fold(facts).handled
    prior = len(world.forge.asked)
    _run(world.root, lambda: len(world.forge.asked) >= prior + 2)
    assert len(_update_letters(world)) == 1
    _next_tip(world)
    _run(world.root, lambda: _offered(world).data["new"] == world.tip)
    assert len(_update_letters(world)) == 2
    assert _git(world.source, "rev-parse", "HEAD") == world.old
    assert not world.execs


def test_a_new_tip_updates_the_open_offer_without_a_second_letter_and_take_uses_it(world, capsys):
    _offer(world)
    letter = _update_letters(world)[0]
    _next_tip(world)
    looming = Loom(world.root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: _offered(world).data["new"] == world.tip, 10, looming.dump)
        assert len(_update_letters(world)) == 1
        assert _offered(world).data["offer"] == letter.id
        assert [p["number"] for p in _offered(world).data["pulls"]] == [7, 8]
        from brr.loom.runtime.__main__ import main
        assert main(["update", "--home", str(world.root)]) == 0
        assert world.tip in capsys.readouterr().out
        assert _take(world) == 0
        wait_until(lambda: world.execs, 10, looming.dump)
    finally:
        looming.halt()
    assert _git(world.source, "rev-parse", "HEAD") == world.tip
    assert len(_update_letters(world)) == 1


def test_update_off_does_not_offer_or_take(world):
    with (world.root / "loom/config.toml").open("a") as file:
        file.write('\nupdate = "off"\n')
    from brr.loom.runtime.config import load_config
    updater = update.Updater(Home(world.root), lambda *args: None, world.source)
    updater.step(load_config(world.root), lambda young: True)
    assert updater.worker is None
    assert _take(world) == 1
    assert _git(world.source, "rev-parse", "HEAD") == world.old
    assert not _update_letters(world)


def test_the_check_can_import_the_actual_product_and_parse_a_home(world):
    root = Path(update.__file__).resolve().parents[4]
    update.check_tree(root, Home(world.root))


@pytest.mark.parametrize("fails", [False, True])
def test_the_source_observer_cannot_reload_during_validation_or_after_rollback(world, monkeypatch, fails):
    import threading
    # Observe the fixture checkout, as a production loop observes its own tree.
    def fingerprint():
        return repr([(str(p), p.stat().st_mtime_ns) for p in sorted(world.source.rglob("*.py"))])
    monkeypatch.setattr(loom, "_source_fingerprint", fingerprint)
    monkeypatch.setattr(loom, "SOURCE_EVERY", 0)
    entered, release, blocked = threading.Event(), threading.Event(), threading.Event()
    from contextlib import contextmanager
    original_lock = loom.checkout_lock
    @contextmanager
    def observing_lock(root, **kwargs):
        with original_lock(root, **kwargs) as available:
            if not available:
                blocked.set()
            yield available
    monkeypatch.setattr(loom, "checkout_lock", observing_lock)
    original = update.check_tree
    def check(root, home):
        entered.set()
        assert release.wait(10)
        original(root, home)
        if fails:
            raise update.Refused("test validation failed")
    monkeypatch.setattr(update, "check_tree", check)
    looming = Loom(world.root, tick=0.01)
    looming.start()
    results = []
    taking = threading.Thread(target=lambda: results.append(_take(world)))
    try:
        wait_until(lambda: _offered(world), 10, looming.dump)
        # Hold a real take after merge, before validation completes.
        taking.start()
        assert entered.wait(10)
        assert _git(world.source, "rev-parse", "HEAD") == world.tip
        # The loop can continue ticking, but its observer cannot get the lock.
        with update.checkout_lock(world.source, blocking=False) as acquired:
            assert not acquired
        assert blocked.wait(10)
        assert not world.execs
        release.set()
        taking.join(10)
        assert not taking.is_alive()
        if fails:
            assert results == [1]
            assert _git(world.source, "rev-parse", "HEAD") == world.old
            asked = len(world.forge.asked)
            wait_until(lambda: len(world.forge.asked) > asked + 1, 10, looming.dump)
            assert not world.execs
        else:
            assert results == [0]
            wait_until(lambda: world.execs, 10, looming.dump)
    finally:
        release.set()
        taking.join(10)
        looming.halt()
    assert not looming.errors


def test_an_own_commit_after_a_take_still_loads_and_polling_goes_on(world):
    _load(world)
    _run(world.root, lambda: any(f.kind == "loom.moved" for f in read_facts(Home(world.root))))
    (world.source / "mine.py").write_text("mine\n")
    _git(world.source, "add", "-A")
    _git(world.source, "commit", "-qm", "my own code after the take")
    prior = update.SEEN["at"]
    _run(world.root, lambda: _degraded(world.root) or update.SEEN["at"] != prior)
    assert not _degraded(world.root), _degraded(world.root)[0].data

    _next_tip(world)
    _run(world.root, lambda: _offered(world).data["new"] == world.tip)
    assert _take(world) == 0
    assert _git(world.source, "rev-parse", "HEAD^2") == world.tip
    assert (world.source / "mine.py").read_text() == "mine\n"
    assert not _degraded(world.root)


@pytest.mark.parametrize("error", [TimeoutError("forge timeout"), RuntimeError("404")])
def test_description_failure_falls_back_and_is_not_refetched(world, monkeypatch, error):
    calls = []
    def description(url, number):
        calls.append(number)
        raise error
    monkeypatch.setattr(world.forge, "description", description)
    _run(world.root, lambda: len(world.forge.asked) >= 3 and _offered(world))
    assert calls == [7]
    [pull] = _offered(world).data["pulls"]
    assert pull["number"] == 7
    assert pull["title"] == "Merge pull request #7 from o/loom/feature"
    assert pull["url"].endswith("#pull/7")
    assert not _degraded(world.root)
    assert _take(world) == 0


def test_an_open_offer_keeps_descriptions_when_the_target_advances(world, monkeypatch):
    calls = []
    original = world.forge.description
    def description(url, number):
        calls.append(number)
        return original(url, number)
    monkeypatch.setattr(world.forge, "description", description)
    looming = Loom(world.root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: len(world.forge.asked) >= 3 and _offered(world), 10, looming.dump)
        assert calls == [7]
        _next_tip(world)
        wait_until(lambda: _offered(world).data["new"] == world.tip, 10, looming.dump)
        assert calls == [7, 8]
    finally:
        looming.halt()
    assert not looming.errors


def test_fifteen_large_pr_bodies_reach_a_real_wake_under_budget(world, monkeypatch, tmp_path, capsys):
    from test_wake import cli, commit, write_thread as wake_thread
    from brr.loom.runtime import selfrepo
    for number in range(8, 22):
        _next_tip(world, name=f"change-{number}.py", message=f"Change {number} (#{number})")
    monkeypatch.setattr(world.forge, "description",
                        lambda url, number: {"number": number, "title": f"Change {number}",
                                             "body": "é" * 1500})
    _offer(world)
    [letter] = _update_letters(world)
    offered = _offered(world).data
    assert offered["omitted"] > 0
    assert len(json.dumps(offered).encode()) < 10000
    assert len(letter.data["body"].encode()) < 6000
    assert "and " in letter.data["body"] and "more: brnrd runtime update" in letter.data["body"]
    assert "excerpt: first 300 characters" in letter.data["body"]
    assert all(len(p["body"]) < 350 and p["url"] for p in offered["pulls"])
    from brr.loom.runtime.__main__ import main
    assert main(["update", "--home", str(world.root)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert len(shown["landings"]) == 15

    # Exercise the installed wake recipe, with the real offer letter as occasion.
    home = tmp_path / "wake-home"
    assert cli(["init", "--home", str(home)]).returncode == 0
    wake_thread(home / "self", "w-1", "Review the update")
    commit(home / "self", "thread")
    clone = selfrepo.room(home, "update-wake")
    owed = tmp_path / "owed.json"
    owed.write_text(json.dumps([{"id": letter.id, "from": "loom", "body": letter.data["body"]}]))
    import io
    out = io.StringIO()
    assert selfrepo.run_wake(clone, "w-1", owed, stdout=out) == 0
    text = out.getvalue()
    assert len(text.encode()) <= 24000
    occasion = text.split("## Occasion\n", 1)[1]
    assert letter.data["body"].strip() in occasion
    print(f"offer={len(json.dumps(offered).encode())} bytes; "
          f"letter={len(letter.data['body'].encode())} bytes; "
          f"wake={len(text.encode())}/24000 bytes")


def test_source_observer_reloads_an_own_edit_after_a_taken_update_loaded(world, monkeypatch):
    _load(world)
    _run(world.root, lambda: any(f.kind == "loom.moved" for f in read_facts(Home(world.root))))
    world.execs.clear()
    monkeypatch.setattr(loom, "SOURCE_EVERY", 0)
    observed = []
    def fingerprint():
        observed.append(True)
        return (world.source / "src/brr/loom/runtime/loom.py").read_text()
    monkeypatch.setattr(loom, "_source_fingerprint", fingerprint)
    looming = Loom(world.root, tick=0.01)
    looming.start()
    try:
        wait_until(lambda: len(observed) >= 3, 10, looming.dump)
        path = world.source / "src/brr/loom/runtime/loom.py"
        path.write_text("# my own runtime change\n")
        _git(world.source, "add", "-A")
        _git(world.source, "commit", "-qm", "own runtime edit")
        wait_until(lambda: world.execs, 10, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors
    assert not _degraded(world.root)
    _next_tip(world)
    _run(world.root, lambda: _offered(world).data["new"] == world.tip)
    world.execs.clear()
    looming = Loom(world.root, tick=0.01)
    looming.start()
    try:
        assert _take(world) == 0
        wait_until(lambda: world.execs, 10, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors
    assert (world.source / "src/brr/loom/runtime/loom.py").read_text() == "# my own runtime change\n"
    assert not _degraded(world.root)
