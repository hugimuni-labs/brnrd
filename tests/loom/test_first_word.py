"""Exercise the boundary a body actually sees and the pre-tool refusal it receives."""
import json
from datetime import datetime, timezone

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import jack, port, tooltrace
from test_wishes import ledger  # noqa: F401


def setup_boundary(tmp_path, monkeypatch, age):
    room = tmp_path / 'rooms' / 's-ab12-aaaaaa'
    (room / 'port/in').mkdir(parents=True)
    (room / 'port/out').mkdir()
    letter = Fact(kind='letter', by='person:ada', id='p-ada/one',
                  at=datetime.fromtimestamp(1000, timezone.utc).isoformat(),
                  data={'id': 'p-ada/one', 'from': 'person:ada', 'to': 'thread:test', 'body': 'do work'})
    text = port.render_boundary(room.name, 1, 'test', [letter], set(), {},
                                channels={letter.id: 'channel:cloud/telegram:42'})
    (room / 'port/in/boundary.md').write_text(text)
    monkeypatch.setattr(jack.time, 'time', lambda: 1000 + age)
    monkeypatch.setattr(jack, '_guard_warnings', lambda *a: ('', False))
    monkeypatch.setattr(tooltrace, 'note', lambda *a: None)
    return room, letter


def pre(room, command='rg work'):
    return json.loads(jack.execute('pre', room, json.dumps({
        'tool_name': 'Bash', 'tool_input': {'command': command}})) or '{}').get('hookSpecificOutput', {})


@pytest.mark.parametrize('age,warning,deny', [(59, False, False), (61, True, False), (121, True, True)])
def test_live_age_and_once(tmp_path, monkeypatch, age, warning, deny):
    room, letter = setup_boundary(tmp_path, monkeypatch, age)
    from brr.loom.runtime import grants
    records = []
    monkeypatch.setattr(grants, 'refused', lambda *a: records.append(a))
    out = pre(room)
    assert ('one line to him first' in str(out)) == warning
    assert (out.get('permissionDecision') == 'deny') == deny
    assert len(records) == int(deny)
    again = pre(room)
    assert again.get('permissionDecision') != 'deny'
    assert len(records) == int(deny)
    if warning:
        assert f'for {age} s' in str(again)


def test_send_allowed_and_pending_reply_clears_clock(tmp_path, monkeypatch):
    room, letter = setup_boundary(tmp_path, monkeypatch, 121)
    assert pre(room, 'brnrd runtime send --re p-ada/one --to thread:test "Doing it"').get('permissionDecision') != 'deny'
    port.write_port(room, 'reply', port._front({'kind': 'letter', 'id': 'reply', 're': letter.id}, 'Doing it'))
    assert pre(room) == {}
    assert jack._first_word(room) == ('', False)


def test_post_reminds_even_when_boundary_news_is_empty(tmp_path, monkeypatch):
    room, _ = setup_boundary(tmp_path, monkeypatch, 61)
    monkeypatch.setattr(jack, '_left', lambda *a: '')
    monkeypatch.setattr(jack, '_people', lambda *a: '')
    monkeypatch.setattr(jack, '_touch', lambda *a: None)
    monkeypatch.setattr(jack, '_note_tool', lambda *a: None)
    monkeypatch.setattr(jack, '_lib_changes', lambda *a: '')
    for _ in range(2):
        result = json.loads(jack.execute('post', room, '{}'))
        assert 'owed p-ada/one for 61 s' in result['hookSpecificOutput']['additionalContext']


def test_first_word_is_per_person_letter_and_send_timestamp():
    def letter(ident, by, at, **data):
        return Fact(kind='letter', id=ident, by=by, at=datetime.fromtimestamp(at, timezone.utc).isoformat(), data=data)
    facts = [letter('a', 'person:ada', 1000, **{'from': 'person:ada', 'id': 'a'}),
             letter('b', 'person:ada', 1005, **{'from': 'person:ada', 'id': 'b'}),
             letter('c', 'strand:other', 1000, **{'from': 'other', 'id': 'c'}),
             letter('reply', 'strand:body', 1200, re='a', body='I will check', sent_at=1061),
             letter('reply2', 'strand:body', 1300, re='a', body='Done', sent_at=1100)]
    assert tooltrace.first_words(facts, {'a', 'b', 'c'}) == {'a': 61, 'b': None}


def test_refusal_is_in_ledger_and_survives_new_call(tmp_path, monkeypatch):
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import read_facts
    room, letter = setup_boundary(tmp_path, monkeypatch, 121)
    assert pre(room)['permissionDecision'] == 'deny'
    facts = read_facts(Home(tmp_path))
    refused = [f for f in facts if f.kind == 'guard.refused']
    assert len(refused) == 1
    assert refused[0].data['class'] == 'first-word'
    assert refused[0].data['target'] == letter.id
    assert pre(room).get('permissionDecision') != 'deny'


def test_send_timestamp_survives_port_and_attempt_evidence(ledger, monkeypatch):
    from brr.loom.runtime.ledger import inject_letter, read_facts
    from brr.loom.runtime.loom import _ingest, _end_attempt
    home, room = ledger
    ident = inject_letter(home.root, to='thread:work', body='please check')
    incoming = next(f for f in read_facts(home) if f.data.get('id') == ident)
    sent_at = datetime.fromisoformat(incoming.at).timestamp() + 12
    monkeypatch.setattr(port.time, 'time', lambda: sent_at)
    sent = port.write_send(room, to='thread:other', sender=room.name, re=ident,
                           body='I will check; about a minute', chatter=[])
    fm = port.parse_frontmatter(next((room / 'port/out').glob(sent.split('/')[-1] + '.md')).read_text())
    assert float(fm['sent_at']) == sent_at
    _ingest(home)
    facts = read_facts(home)
    assert tooltrace.first_words(facts, {ident}) == {ident: 12}
    facts = _end_attempt(home, facts, room.name, 'work', 1, 0, [incoming], 'done')
    ended = next(f for f in facts if f.kind == 'attempt.ended')
    assert ended.data['evidence']['first_word_s'] == {ident: 12}
