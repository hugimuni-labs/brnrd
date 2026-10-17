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


def test_closed_thread_idle_tick_starts_no_git_process(tmp_path, monkeypatch):
    root = _home(tmp_path)
    repo = root / 'self'
    path = repo / 'threads/w-3/README.md'
    path.write_text(path.read_text().replace('status: open', 'status: settled'))
    git(repo, 'add', 'threads')
    _commit(repo, 'settle: done' + LABEL)
    home = Home(root)
    router = Router(home)
    tick_once(home, {}, 'fake', 'haiku', router)
    seen = _git_calls(monkeypatch)
    for _ in range(3):
        tick_once(home, {}, 'fake', 'haiku', router)
    assert seen == []
    assert any(f.kind == 'thread.closed' and f.data['thread'] == 'w-3'
               for f in read_facts(home))


def test_ownership_cache_tracks_main_edits_and_appended_facts(monkeypatch):
    from brr.daemon2.facts import Fact
    from brr.loom.runtime import owners
    from brr.loom.runtime.owners import letter_owners
    from brr.loom.runtime.project import projection_cache

    letter = Fact(kind='letter', by='person:test', id='question',
                  data={'id': 'question', 'to': 'thread:work', 'body': 'Do it'})
    facts = [letter]
    main = {'work': {'status': 'open', 'plan_rows': [(False, 'Do it')]}}
    calls = []
    compute = owners._letter_owners
    def counted(facts, main):
        calls.append(True)
        return compute(facts, main)
    monkeypatch.setattr(owners, '_letter_owners', counted)
    with projection_cache():
        assert letter_owners(facts, main)['question'].plan_rows == ()
        for _ in range(5):
            assert letter_owners(list(facts), main)['question'].owner == 'loom'
        assert len(calls) == 1
        main['work']['plan_rows'] = [(False, 'Do it · re: question')]
        assert letter_owners(facts, main)['question'].plan_rows == (1,)
        main['work']['status'] = 'settled'
        assert letter_owners(facts, main)['question'].owner == 'operator'
        facts.append(Fact(kind='note', by='person:test', id='settled',
                          data={'re': 'question', 'why': 'done'}))
        assert letter_owners(facts, main)['question'].done
        assert len(calls) == 4


def test_held_body_ticks_reuse_ledger_rows(tmp_path, monkeypatch):
    """Exercise the real locked _start path excluded by the idle benchmark."""
    import json
    from brr.daemon2.facts import Fact
    from brr.loom.runtime import ledger, loom

    home = Home(_home(tmp_path))
    router = Router(home)
    tick_once(home, {}, 'fake', 'haiku', router)
    router.clock = lambda: router.lease.until - router.config.router_ttl
    strand = f's-{home.install_id()}-held'
    ledger.append(home, Fact(kind='lease', by=f'loom:{home.install_id()}',
        data={'strand': strand, 'thread': 'w-1', 'gen': 1,
              'router_gen': router.lease.gen, 'install': home.install_id()}))
    advanced = []
    def no_advance(home, strand, thread, gen, bodies, facts, **kwargs):
        advanced.append(strand)
        return facts
    monkeypatch.setattr(loom, '_advance_body', no_advance)
    tick_once(home, {}, 'fake', 'haiku', router)
    parsed = []
    real = json.loads
    def counted(raw, *args, **kwargs):
        value = real(raw, *args, **kwargs)
        if isinstance(value, dict) and 'hlc' in value:
            parsed.append(value)
        return value
    monkeypatch.setattr(ledger.json, 'loads', counted)
    advanced.clear()
    for _ in range(3):
        tick_once(home, {}, 'fake', 'haiku', router)
    assert advanced == [strand] * 3
    assert parsed == []


def test_cached_start_refreshes_lease_before_advancing(tmp_path, monkeypatch):
    from brr.daemon2.facts import Fact
    from brr.loom.runtime import ledger, loom

    home = Home(_home(tmp_path))
    strand = f's-{home.install_id()}-old'
    def lease(who, gen):
        return Fact(kind='lease', by=f'loom:{home.install_id()}',
                    data={'strand': who, 'thread': 'w-1', 'gen': gen})
    ledger.append(home, lease(strand, 1))
    reader = ledger.LedgerReader()
    stale = reader.read(home)
    ledger.append(home, lease(f's-{home.install_id()}-new', 2))
    advanced = []
    monkeypatch.setattr(loom, '_advance_body', lambda *a, **k: advanced.append(True))
    current = loom._start(home, strand, 'w-1', 1, {}, stale, reader=reader)
    assert advanced == []
    assert len(current) == 2
