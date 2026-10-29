"""A held body's boundary is rendered from carried projections and only its opener's rows of the
thread table; across a run it must equal, byte for byte, the render with nothing carried."""
from __future__ import annotations

import fcntl
import json
import os

from brr.daemon2.facts import Fact
from brr.loom.runtime import loom, usage
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import _commit, git

from _step import write_thread
from boundary_reference import checked_boundaries

LABEL = "\n\nLoom-Label: taint=0; audience=self\n"


def _child(root, name, status='open'):
    write_thread(root, name, f"---\nid: {name}\nstatus: {status}\ntense: plan\nopened-by: thread:w-1\n---\n"
                 f"# Child {name}\n", "quit-now", wait="0")


def test_cached_boundary_equals_the_uncached_render_across_a_run(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "w-1", "# Parent\n", "quit-now", wait="0")
    write_thread(root, "other", "---\nid: other\nstatus: open\ntense: plan\nopened-by: thread:elsewhere\n---\n"
                 "# Not a child\n", "quit-now", wait="0")
    for name in ("w-2", "w-3", "w-4"):
        _child(root, name)
    _child(root, "w-5", status="settled")
    home = Home(root)
    router = Router(home)
    monkeypatch.setattr(usage, 'reading', lambda *a, **k: {'windows': []})
    loom.tick_once(home, {}, 'fake', 'haiku', router)
    install = home.install_id()
    strand = f's-{install}-held'
    append(home, Fact('lease', f'loom:{install}', {
        'thread': 'w-1', 'strand': strand, 'gen': 1, 'router_gen': router.lease.gen}))
    # Faces read a body's start: the facts it met are the ones after it.
    append(home, Fact('body.started', f'loom:{install}', {
        'strand': strand, 'gen': 1, 'pid': os.getpid(), 'shell': 'fake', 'core': 'haiku', 'readme': '',
        'lstart': ''}))
    room = home.room(strand)
    (room / 'port/in').mkdir(parents=True)

    def commit(message):
        git(root / "self", "add", "threads")
        _commit(root / "self", message + LABEL)

    steps = [
        lambda: append(home, Fact('letter', 'person:a', {
            'id': 'ask-1', 'to': 'thread:w-1', 'from': 'p-a', 'body': 'please'})),
        lambda: append(home, Fact('wish', f'strand:{strand}', {
            'wish': 'w-parent', 'thread': 'w-1', 'from': 'person', 'line': 'the parent wish',
            'says': [['ask-1', 0, None]]}, id='w-parent')),
        lambda: append(home, Fact('wish', f'strand:{strand}', {
            'wish': 'w-child', 'thread': 'w-2', 'from': 'person', 'line': 'the child wish',
            'says': [['ask-1', 0, None]]},
            id='w-child')),
        lambda: append(home, Fact('letter', f'strand:{strand}', {
            'id': 'to-child', 'to': 'thread:w-2', 'from': strand, 'gen': 1, 'body': 'go'})),
        lambda: append(home, Fact('shown', f'loom:{install}', {'strand': strand, 'gen': 1, 'ids': ['ask-1']})),
        lambda: append(home, Fact('note', f'strand:{strand}', {'re': 'ask-1', 'from': strand, 'gen': 1})),
        lambda: append(home, Fact('face', f'strand:{strand}', {'strand': strand, 'gen': 1, 'face': 'plain'})),
        lambda: append(home, Fact('pr.opened', f'strand:{strand}', {
            'thread': 'w-4', 'repo': 'brnrd', 'number': 7, 'url': 'https://github.com/o/brnrd/pull/7',
            'head': 'abc1234'})),
        lambda: (root / 'loom/prs.json').write_text(json.dumps({'brnrd#7': {'draft': False, 'ci': 'pending'}})),
        lambda: _child(root, "w-6"),
        lambda: ((home.thread_dir("w-3") / "README.md").write_text(
            (home.thread_dir("w-3") / "README.md").read_text().replace("status: open", "status: settled")),
            commit("settle: w-3")),
        lambda: append(home, Fact('wish.result', f'strand:{strand}', {
            'wish': 'w-child', 'kind': 'pr', 'ref': 'brnrd#7', 'line': 'done', 'delivered': True})),
        lambda: append(home, Fact('router.renewed', f'loom:{install}', {'gen': router.lease.gen})),
    ]
    seen: list = []
    with (room / '.body').open('a') as lock, checked_boundaries(seen):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        loom.tick_once(home, {}, 'fake', 'haiku', router)
        for step in steps:
            step()
            # Twice: the second tick renders from what the first one carried.
            loom.tick_once(home, {}, 'fake', 'haiku', router)
            loom.tick_once(home, {}, 'fake', 'haiku', router)
    assert len([row for row in seen if row[0] == strand]) == 2 * len(steps) + 1
    for strand_, gen, equal, written, reference in seen:
        assert equal, f"{strand_} gen {gen}:\n--- written\n{written}\n--- reference\n{reference}"
    texts = [written for *_, written, _ in seen]
    assert any("opened: w-2" in text for text in texts)
    assert any("idle: " in text for text in texts)
    assert any("face?" in text for text in texts)
    assert not any("opened: other" in text or "opened: w-5" in text for text in texts)
