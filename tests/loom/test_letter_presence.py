"""Receipts drive the shared clock and generic connector verbs."""
import json
import runpy
import sys
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime import letter_presence as presence
from brr.loom.runtime.connector_presence import Presence
from test_wishes import ledger  # noqa: F401


def fact(kind, ident, when, by='loom:aaaa', **data):
    return Fact(kind=kind, id=ident, by=by,
                at=datetime.fromtimestamp(when, timezone.utc).isoformat(), data=data)


def incoming():
    return [fact('source', 'source:tg:42:1', 1000,
                 channel='channel:tg/42', chat='42', origin='tg:42:1'),
            fact('letter', 'letter:tg:42:1', 1000,
                 **{'from': 'person:ada', 'cites': 'source:tg:42:1', 'body': 'check'})]


def boot():
    return [fact('shown', 'shown', 1001, strand='s-test', gen=1, ids=['letter:tg:42:1']),
            fact('body.started', 'start', 1002, strand='s-test', gen=1)]


def test_each_step_and_first_word_clock(ledger):
    home, _ = ledger
    facts = incoming() + boot() + [fact('note', 'stamp', 1003, by='strand:s-test',
        re='letter:tg:42:1', triage='silent test')]
    facts += [fact('letter', 'reply', 1200, by='strand:s-test',
                   re='letter:tg:42:1', body='Checking', sent_at=1012)]
    rows = presence.steps(facts)
    assert [(r['step'], r['at']) for r in rows] == [
        ('admitted', 1000), ('booted', 1002), ('stamped', 1003), ('first-word', 1012)]
    assert all(r['native'] == '42:1' for r in rows)
    presence.observe(home, facts)
    count = len(facts)
    presence.observe(home, facts)
    assert len(facts) == count  # replay does not write duplicate progress
    assert presence.first_words(facts, {'letter:tg:42:1'}) == {'letter:tg:42:1': 12}


def test_unstarted_show_does_not_boot():
    facts = incoming() + boot()[:1]
    assert [r['step'] for r in presence.steps(facts)] == ['admitted']


def worker(home, monkeypatch):
    worker = Presence(home)
    worker.began = 0
    worker.thread = object()  # Drive the real flush synchronously; never sleep in tests.
    calls = []
    monkeypatch.setattr('brr.loom.runtime.connector_presence.instances', lambda h: {'tg': 'telegram'})
    monkeypatch.setattr('brr.loom.runtime.connector_presence.subprocess.run',
                        lambda args, **kw: calls.extend(json.loads(kw['input'])))
    return worker, calls


def test_admission_boot_first_word_and_transport_failure(ledger, monkeypatch):
    home, _ = ledger
    worker_, calls = worker(home, monkeypatch)
    facts = incoming()
    worker_.update(facts)
    worker_.flush()
    assert [c['verb'] for c in calls] == ['seen']
    calls.clear()
    worker_.update(facts + boot())
    worker_.flush()
    worker_.flush()
    assert [(c['verb'], c.get('on')) for c in calls] == [('working', True)] * 2
    calls.clear()
    worker_.update(facts + boot() + [fact('letter', 'reply', 1010, by='strand:s-test',
                   re='letter:tg:42:1', body='Checking', sent_at=1005)])
    worker_.flush()
    worker_.flush()
    assert [(c['verb'], c.get('on')) for c in calls] == [('working', False)]
    monkeypatch.setattr('brr.loom.runtime.connector_presence.subprocess.run',
                        lambda *a, **kw: (_ for _ in ()).throw(OSError('offline')))
    worker_.update(incoming() + boot())
    worker_.flush()  # Side-channel outage cannot fail the loom.


def test_body_end_stops_working(ledger, monkeypatch):
    home, _ = ledger
    worker_, calls = worker(home, monkeypatch)
    facts = incoming() + boot()
    worker_.update(facts)
    worker_.flush()
    calls.clear()
    facts += [fact('body.exited', 'end', 1009, strand='s-test', gen=1)]
    worker_.update(facts)
    worker_.flush()
    assert [(c['verb'], c.get('on')) for c in calls] == [('working', False)]


def test_native_telegram_verbs(monkeypatch):
    folder = Path(presence.__file__).parent / 'connectors/telegram'
    # Folder executables use their sibling common.py; isolate it from relay's module.
    import importlib.util
    spec = importlib.util.spec_from_file_location('common', folder / 'common.py')
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    monkeypatch.setitem(sys.modules, 'common', common)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    module = runpy.run_path(str(folder / 'presence'))
    calls = []
    letter = dict(chat='42:7', native='42:1')
    module['apply']([dict(verb='seen', letter=letter),
                     dict(verb='working', letter=letter, on=True),
                     dict(verb='working', letter=letter, on=False)],
                    lambda method, payload, **kw: calls.append((method, payload)))
    assert calls == [('setMessageReaction', dict(chat_id='42', message_id=1,
                    reaction=[dict(type='emoji', emoji='👀')])),
                    ('sendChatAction', dict(chat_id='42', message_thread_id=7, action='typing'))]


def test_relay_verbs_resolve_native_event(tmp_path, monkeypatch):
    import importlib.util
    folder = Path(presence.__file__).parent / 'connectors/relay'
    spec = importlib.util.spec_from_file_location('common', folder / 'common.py')
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    monkeypatch.setitem(sys.modules, 'common', common)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    module = runpy.run_path(str(folder / 'presence'))
    (tmp_path / 'messages.json').write_text(json.dumps({'telegram:42:1': 'ev-1'}))
    calls = []
    from types import SimpleNamespace
    module['apply']([dict(verb='seen', letter=dict(native='telegram:42:1')),
                     dict(verb='working', on=True, letter=dict(native='telegram:42:1')),
                     dict(verb='seen', letter=dict(native='missing'))],
                    tmp_path, SimpleNamespace(presence=calls.append))
    assert calls == [[dict(event_id='ev-1', verb='seen', on=False),
                      dict(event_id='ev-1', verb='working', on=True)]]


def test_stamped_by_wish_says_and_clock_consumes_presence_rows():
    facts = incoming() + [fact('wish', 'wish', 1001,
                              says=[{'letter': 'letter:tg:42:1', 'start': 0, 'size': None}])]
    assert [r['step'] for r in presence.steps(facts)] == ['admitted', 'stamped']
    facts += [fact('letter.presence', 'word', 1020, letter='letter:tg:42:1',
                   step='first-word', at=1011)]
    assert presence.first_words(facts, {'letter:tg:42:1'}) == {'letter:tg:42:1': 11}


def test_worker_does_not_react_to_old_letters_or_type_for_peer(ledger, monkeypatch):
    home, _ = ledger
    worker_, calls = worker(home, monkeypatch)
    worker_.began = 1001
    starts = boot()
    starts[1] = fact('body.started', 'peer-start', 1002, by='loom:peer', strand='s-test', gen=1)
    worker_.update(incoming() + starts)
    worker_.flush()
    assert calls == []


def test_router_lease_loss_and_stale_snapshot_stop_typing(ledger, monkeypatch):
    home, _ = ledger
    worker_, calls = worker(home, monkeypatch)
    worker_.update(incoming() + boot())
    worker_.flush()
    calls.clear()
    worker_.updated -= 11
    worker_.flush()
    assert [(c['verb'], c.get('on')) for c in calls] == [('working', False)]
    calls.clear()
    worker_.update(incoming() + boot(), armed=False)
    worker_.flush()
    assert calls == []
