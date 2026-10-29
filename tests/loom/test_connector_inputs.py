"""A poisoned connector drop must not starve the loop's bodies."""

from brr.loom.runtime import loom as runtime, connector as source
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.port import _front
from _step import Loom, mail, wait_until, write_thread


def test_failed_ingest_keeps_body_ticks_and_quarantines_after_three_tries(tmp_path, monkeypatch):
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Work', 'answer-fast', wait='0')
    drop = root / 'loom/connectors/fake/state/in'
    drop.mkdir(parents=True)
    poison = drop / 'poison.md'
    poison.write_text(_front(dict(kind='letter', id='poison', to='in'), 'poison'))
    attachments = drop / 'poison.d'
    attachments.mkdir()
    (attachments / '0').write_bytes(b'attachment')
    attempts, ticks = [], []
    def fail(*_args):
        attempts.append(1)
        raise RuntimeError('poison ingest')
    monkeypatch.setattr(source, 'ingest', fail)
    tick = runtime.tick_once
    def observe_tick(*args):
        ticks.append(len(attempts))
        return tick(*args)
    monkeypatch.setattr(runtime, 'tick_once', observe_tick)
    original = inject_letter(root, to='thread:work', body='still work')
    loop = Loom(root)
    loop.start()
    try:
        wait_until(lambda: (drop / 'refused/poison.md').exists()
                   and not mail(root, loop.facts(), 'work'), 8, loop.dump)
        # The router lease is taken in the first tick; ingest follows each one.
        assert ticks[:4] == [0, 1, 2, 3]
        assert len(attempts) == 3
        assert (drop / 'refused/poison.failures').read_text() == '3'
        assert (drop / 'refused/poison.d/0').read_bytes() == b'attachment'
        assert not poison.exists()
        notices = [f for f in loop.facts() if f.kind == 'letter' and 'ingest failed 3 times' in f.data.get('body', '')]
        assert len(notices) == 1
        assert any(f.kind == 'note' and f.data.get('re') == original for f in loop.facts())
    finally:
        loop.halt()


def test_connectors_wait_for_the_router_lease(tmp_path, monkeypatch):
    """Another install holding the router lease keeps this one's connectors shut."""
    from brr.daemon2.leases import LocalLeaseAuthority
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import append
    from brr.loom.runtime.router import loom_clock
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Work', 'answer-fast', wait='0')
    drop = root / 'loom/connectors/fake/state/in'
    drop.mkdir(parents=True)
    authority = LocalLeaseAuthority(root / 'ledger' / 'leases', clock=loom_clock)
    other = authority.acquire('router', 'ffff', 60)
    # The peer records its generation, as its own router does.
    append(Home(root, install='ffff'), Fact(
        kind='router', by='loom:ffff', id=f'router:{other.gen}',
        data={'install': 'ffff', 'gen': other.gen, 'until': other.until}))
    drains, ticks = [], []
    drain = source.drain
    def observe_drain(*args):
        drains.append(1)
        return drain(*args)
    monkeypatch.setattr(source, 'drain', observe_drain)
    tick = runtime.tick_once
    def observe_tick(*args):
        ticks.append(1)
        return tick(*args)
    monkeypatch.setattr(runtime, 'tick_once', observe_tick)
    loop = Loom(root)
    loop.start()
    try:
        wait_until(lambda: len(ticks) >= 5, 8, loop.dump)
        assert drains == []
        assert authority.release(other)
        wait_until(lambda: drains, 8, loop.dump)
    finally:
        loop.halt()
