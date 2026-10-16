"""What an idle tick costs. The loop runs five times a second, so a git
process per thread per tick is the whole loom standing still."""

from __future__ import annotations

import subprocess
from pathlib import Path

from brr.loom.runtime.home import Home
from brr.loom.runtime.lifecycle import main_snapshot, main_tip
from brr.loom.runtime.loom import tick_once
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import _commit, git, init_self
from brr.loom.runtime.threads import thread_table
from brr.loom.runtime.ledger import read_facts

from _step import write_thread

THREADS = 12
LABEL = "\n\nLoom-Label: taint=0; audience=self\n"


def _home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    init_self(root, person="test")
    for n in range(THREADS):
        write_thread(root, f"w-{n + 1}", f"# thread {n + 1}\n", "quit-now", wait="0")
    return root


def _git_calls(monkeypatch) -> list:
    seen: list = []
    real = subprocess.run

    def counted(argv, *args, **kwargs):
        if argv and argv[0] == "git":
            seen.append(list(argv))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counted)
    return seen


def test_an_idle_tick_starts_no_git_process(tmp_path: Path, monkeypatch) -> None:
    root = _home(tmp_path)
    home, router, bodies = Home(root), Router(Home(root)), {}
    tick_once(home, bodies, "fake", "haiku", router)
    seen = _git_calls(monkeypatch)
    for _ in range(3):
        tick_once(home, bodies, "fake", "haiku", router)
    assert seen == []
    assert len(thread_table(home, read_facts(home))) >= THREADS


def test_a_commit_rereads_only_the_thread_it_touched(tmp_path: Path, monkeypatch) -> None:
    root = _home(tmp_path)
    repo = root / "self"
    home, router, bodies = Home(root), Router(Home(root)), {}
    tick_once(home, bodies, "fake", "haiku", router)
    readme = repo / "threads/w-3/README.md"
    readme.write_text(readme.read_text().replace("status: open", "status: settled"))
    git(repo, "add", "threads")
    _commit(repo, "settle: done" + LABEL)
    seen = _git_calls(monkeypatch)
    tick_once(home, bodies, "fake", "haiku", router)
    touched = [argv for argv in seen if any("w-3" in part for part in argv)]
    others = [argv for argv in seen
              if any("threads/w-" in part and "w-3" not in part for part in argv)]
    assert others == []
    assert touched
    assert main_snapshot(home)["w-3"]["status"] == "settled"
    row = next(r for r in thread_table(home, read_facts(home)) if r["thread"] == "w-3")
    assert row["status"] == "settled" and row["now"].startswith("settled")


def test_main_tip_agrees_with_git_loose_and_packed(tmp_path: Path) -> None:
    root = _home(tmp_path)
    repo = root / "self"
    asked = git(repo, "rev-parse", "main").stdout.strip()
    assert main_tip(repo) == asked
    git(repo, "pack-refs", "--all")
    assert not (repo / ".git/refs/heads/main").exists()
    assert main_tip(repo) == asked
    (repo / "memory/stance.md").open("a").write("\nmore\n")
    git(repo, "add", "-A")
    _commit(repo, "Touch." + LABEL)
    moved = git(repo, "rev-parse", "main").stdout.strip()
    assert moved != asked and main_tip(repo) == moved


def test_idle_ticks_do_not_reparse_an_8k_ledger(tmp_path, monkeypatch):
    """Zero decoded rows per warm idle tick, independent of CPU/CI speed."""
    import json
    from brr.daemon2.facts import Fact
    from brr.loom.runtime import ledger, project

    root = _home(tmp_path)
    home = Home(root)
    path = home.facts_dir() / 'loom.jsonl'
    path.write_text(''.join(json.dumps(Fact(
        kind='router.renewed', by='loom:abcd', id=f'renew-{n}',
        data={'install': 'abcd', 'gen': 1, 'until': n},
        hlc=(n, 0, 'abcd'),
    ).row()) + '\n' for n in range(8000)))
    router, bodies = Router(home), {}
    tick_once(home, bodies, 'fake', 'haiku', router)
    # Load the router acquisition written by the first tick.
    router.clock = lambda fixed=router.lease.until - router.config.router_ttl: fixed
    tick_once(home, bodies, 'fake', 'haiku', router)
    parsed, sorted_streams = [], []
    real = ledger.json.loads
    real_union = project.union

    def counted(raw, *args, **kwargs):
        result = real(raw, *args, **kwargs)
        if isinstance(result, dict) and 'kind' in result and 'hlc' in result:
            parsed.append(result)
        return result

    def counted_union(*streams):
        sorted_streams.append(True)
        return real_union(*streams)

    monkeypatch.setattr(ledger.json, 'loads', counted)
    monkeypatch.setattr(project, 'union', counted_union)
    # Lease time stays fixed: this checks idle work rather than timed appends.
    for _ in range(5):
        tick_once(home, bodies, 'fake', 'haiku', router)
    assert parsed == []
    assert sorted_streams == []
