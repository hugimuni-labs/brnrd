"""The loom keeps src/frontend/build on its checkout's tree: a moved tree builds into a fresh dir that replaces the
served one only on success; a failed build keeps the old one served and raises (the loop's ``spa`` degraded step)."""
import subprocess

import pytest

from brr.loom.runtime.spabuild import BuildFailed, SpaBuild


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "repo"
    frontend = root / "src" / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text("{}")
    (frontend / "package-lock.json").write_text("{}")
    (frontend / "node_modules").mkdir()
    (frontend / "node_modules" / ".package-lock.json").write_text("{}")
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a", "--allow-empty")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b")
    return root, frontend


def settle(spa):
    spa.step()
    if spa.worker is not None:
        spa.worker.join(10)


def test_a_moved_tree_builds_and_swaps_only_on_success(checkout):
    root, frontend = checkout
    calls, outcome = [], {"fail": False}

    def run(args, cwd, env=None):
        calls.append(args)
        if outcome["fail"]:
            raise BuildFailed("npm run build exited 1: x.svelte: boom")
        out = cwd / env["BRNRD_SPA_OUT"]
        out.mkdir()
        (out / "200.html").write_text(f"shell {len(calls)}")

    (frontend / "build").mkdir()
    (frontend / "build" / "200.html").write_text("stale, unstamped")
    spa = SpaBuild(frontend, lambda text: None, run, every=0)
    settle(spa)
    assert calls == [["npm", "run", "build"]]  # node_modules is newer than the lock: no npm ci
    assert (frontend / "build" / "200.html").read_text() == "shell 1"
    assert spa.built() == spa.wanted()
    assert not (frontend / "build.next").exists() and not (frontend / "build.old").exists()
    settle(spa)
    assert len(calls) == 1  # same tree: nothing to do

    (frontend / "app.svelte").write_text("moved")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    outcome["fail"] = True
    settle(spa)
    assert (frontend / "build" / "200.html").read_text() == "shell 1"  # the old build stays served
    with pytest.raises(BuildFailed, match="boom.*serving the build of tree"):
        spa.step()
    assert len(calls) == 2  # a failed tree is not rebuilt every tick

    (frontend / "app.svelte").write_text("fixed")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "d")
    outcome["fail"] = False
    settle(spa)
    spa.step()  # recovered: no raise
    assert (frontend / "build" / "200.html").read_text() == "shell 3" and spa.built() == spa.wanted()


def test_a_newer_lock_installs_first(checkout):
    _, frontend = checkout
    calls = []

    def run(args, cwd, env=None):
        calls.append(args[1])
        if env:
            (cwd / env["BRNRD_SPA_OUT"]).mkdir()
            (cwd / env["BRNRD_SPA_OUT"] / "200.html").write_text("ok")

    import os
    os.utime(frontend / "node_modules" / ".package-lock.json", (0, 0))
    settle(SpaBuild(frontend, lambda text: None, run, every=0))
    assert calls == ["ci", "run"]


def test_head_is_read_once_per_interval_not_every_tick(checkout, monkeypatch):
    root, frontend = checkout
    clock, reads = [0.0], []
    spa = SpaBuild(frontend, lambda text: None, lambda *a, **k: None, every=30, clock=lambda: clock[0])
    monkeypatch.setattr(spa, "wanted", lambda: reads.append(clock[0]) or spa.built())
    for _ in range(10):
        spa.step()
        clock[0] += 0.2
    assert reads == [0.0]
    clock[0] = 30.0
    spa.step()
    assert reads == [0.0, 30.0]


def managed(root, frontend):
    (frontend / '.gitignore').write_text('build\nbuild.next\nbuild.old\nnode_modules\n')
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'ignore builds')
    git(root, 'remote', 'add', 'origin', 'https://github.com/hugimuni-labs/brnrd.git')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')


def test_managed_upstream_fetch_needs_no_npm(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    downloads = []

    def fetch(commit, tree, destination):
        downloads.append((commit, tree))
        destination.mkdir()
        (destination / '200.html').write_text('upstream')
        (destination / '.tree').write_text(tree)

    monkeypatch.setattr(board_asset, 'fetch', fetch)
    monkeypatch.setattr(spabuild.shutil, 'which', lambda name: None)
    spa = SpaBuild(frontend, lambda text: None, lambda *a: pytest.fail('npm ran'), every=0)
    settle(spa)
    settle(spa)
    assert len(downloads) == 1
    assert (frontend / 'build' / '200.html').read_text() == 'upstream'
    assert spa.built() == spa.wanted()


def test_changed_without_npm_keeps_upstream_board_and_says_once(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    (frontend / 'build').mkdir()
    (frontend / 'build' / '200.html').write_text('good')
    spa = SpaBuild(frontend, lambda text: messages.append(text), every=0)
    spa.wanted()
    (frontend / 'build' / '.tree').write_text(spa.upstream[1])
    (frontend / 'own.svelte').write_text('own edit')
    messages = []
    monkeypatch.setattr(board_asset, 'fetch', lambda *a: pytest.fail('unnecessary download'))
    monkeypatch.setattr(spabuild.shutil, 'which', lambda name: None)
    settle(spa)
    settle(spa)
    assert (frontend / 'build' / '200.html').read_text() == 'good'
    assert len(messages) == 1 and 'npm is unavailable' in messages[0]
    assert spa.failed is None


def test_missing_asset_does_not_fail_loom_or_build_upstream_locally(checkout, monkeypatch):
    from brr.loom.runtime import board_asset
    root, frontend = checkout
    managed(root, frontend)
    messages = []

    def missing(*args):
        raise OSError('404')

    monkeypatch.setattr(board_asset, 'fetch', missing)
    spa = SpaBuild(frontend, messages.append, lambda *a: pytest.fail('npm ran'), every=0)
    settle(spa)
    settle(spa)
    assert spa.failed is None
    assert not spa.build.exists()
    assert len(messages) == 1 and 'board' in messages[0] and 'npm ci' in messages[0]


def test_changed_frontend_with_npm_builds_locally(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    calls = []

    def fetch(commit, tree, destination):
        destination.mkdir()
        (destination / '200.html').write_text('upstream')
        (destination / '.tree').write_text(tree)

    def run(args, cwd, env=None):
        calls.append(args)
        if env:
            destination = cwd / env['BRNRD_SPA_OUT']
            destination.mkdir()
            (destination / '200.html').write_text('own board')

    monkeypatch.setattr(board_asset, 'fetch', fetch)
    monkeypatch.setattr(spabuild.shutil, 'which', lambda name: '/fixture/npm')
    (frontend / 'own.svelte').write_text('own change')
    spa = SpaBuild(frontend, lambda text: None, run, every=0)
    settle(spa)
    settle(spa)
    assert calls == [['npm', 'run', 'build']]
    assert (spa.build / '200.html').read_text() == 'own board'


def test_changed_without_npm_preserves_last_good_local_board(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    spa = SpaBuild(frontend, lambda _: None, every=0)
    spa.wanted()
    spa.build.mkdir()
    (spa.build / '200.html').write_text('last good local board')
    (spa.build / '.tree').write_text('previous local content hash')
    (spa.build / '.upstream').write_text(spa.upstream[1])
    (frontend / 'own.svelte').write_text('new edit')
    monkeypatch.setattr(board_asset, 'fetch', lambda *a: pytest.fail('discarded last good local build'))
    monkeypatch.setattr(spabuild.shutil, 'which', lambda _: None)
    settle(spa)
    assert (spa.build / '200.html').read_text() == 'last good local board'
