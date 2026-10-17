"""4401 replay through the boundary writer and the operator's thread command."""
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _boundary
from brr.loom.runtime.port import parse_boundary
from brr.loom.runtime.verbs_threads import _threads
from _step import write_thread

ASK = 'letter:mine:155783668:4401'
CHANNEL = 'channel:mine/155783668'
STRAND = 's-ae66-xo04cv'


def replay(tmp_path):
    root = tmp_path / 'home'
    write_thread(root, 'inbox', 'Inbox\n\n## Plan\n- [x] Answer the person · re: ' + ASK, '', wait='0')
    home = Home(root)
    def record(kind, ident, data, by='loom:ae66'):
        return append(home, Fact(kind=kind, id=ident, by=by, data=data))
    record('source', 'source:4401', {'channel': CHANNEL})
    record('letter', ASK, dict(id=ASK, to='thread:inbox', body='The ask',
                              **{'from': 'person:arseni'}, cites='source:4401'))
    record('lease', 'lease:inbox:7', dict(thread='inbox', strand=STRAND, gen=7, install='ae66'))
    record('attempt.ended', 'attempt.ended:s-ae66-xo04cv:7', dict(
        thread='inbox', strand=STRAND, gen=7, ids=[ASK], cause='failed', code=3,
        evidence={'tail': 'wake over budget; Plan 7345 B'},
        dispositions=[dict(id=ASK, outcome='done', reason='failure message owed to author',
                           successor=dict(id='loom/failed-message-d8454633cf',
                                          to=CHANNEL, body='Send again, or leave it.',
                                          re=ASK, about=ASK, **{'from': 'loom'}))]))
    # The real 4401 history has no standalone reply here, but its disposition
    # embeds this delivered failure notice. That is still not an answer.
    record('speech.part', 'speech.part:loom/failed-message-d8454633cf#1', dict(
        key='loom/failed-message-d8454633cf', part='loom/failed-message-d8454633cf#1',
        channel=CHANNEL, receipt={'message_id': '155783668:4403'}))
    (home.room(STRAND) / 'port/in').mkdir(parents=True)
    return home, record


def view(home, capsys):
    _boundary(home, read_facts(home), STRAND, 7, SimpleNamespace(thread='inbox', fuel='', why=''))
    boundary = (home.room(STRAND) / 'port/in/boundary.md').read_text()
    assert _threads(['--home', str(home.root)]) == 0
    lines = [line.split(' · ') for line in capsys.readouterr().out.splitlines()]
    row = next(dict(zip(lines[0], line)) for line in lines[1:] if line[0] == 'inbox')
    return boundary, row


def reply(record, ident='reply', channel=CHANNEL):
    record('letter', ident, dict(id=ident, to=channel, body='Answer', re=ASK,
                                gen=7, **{'from': STRAND}), by='strand:' + STRAND)


def receipt(record, key='reply', channel=CHANNEL):
    record('speech.part', 'speech.part:' + key + '#1', dict(
        key=key, part=key + '#1', channel=channel, receipt={'message_id': 'delivered'}))


def test_4401_done_and_cited_is_unanswered_until_delivery(tmp_path, capsys):
    home, record = replay(tmp_path)
    boundary, row = view(home, capsys)
    assert 'unanswered: ' + ASK in boundary
    assert 'letters without a row:' not in boundary
    assert row['unanswered'] == '1' and row['owed'] == '0'
    # This information does not become owed ids or a new stop condition.
    assert parse_boundary(boundary).ids == []
    record('note', 'resident-note', dict(re=ASK, why='done', gen=7), by='strand:' + STRAND)
    reply(record)
    record('speech', 'speech:sent:reply', dict(key='reply', state='sent'))
    assert view(home, capsys)[1]['unanswered'] == '1'
    receipt(record)
    boundary, row = view(home, capsys)
    assert 'unanswered:' not in boundary
    assert row['unanswered'] == '0'


@pytest.mark.parametrize('wrong', ['channel', 'receipt-channel', 're', 'stale-writer', 'empty-receipt'])
def test_only_a_matching_delivered_reply_answers(tmp_path, capsys, wrong):
    home, record = replay(tmp_path)
    data = dict(id='reply', to=CHANNEL, body='Answer', re=ASK, gen=7, **{'from': STRAND})
    if wrong == 'channel':
        data['to'] = 'channel:mine/other'
    if wrong == 're':
        data['re'] = 'another-ask'
    if wrong == 'stale-writer':
        data['gen'] = 6
    record('letter', 'reply', data, by='strand:' + STRAND)
    if wrong == 'empty-receipt':
        record('speech.part', 'empty', dict(key='reply', channel=CHANNEL, receipt={}))
    else:
        receipt(record, channel='channel:mine/other' if wrong == 'receipt-channel' else data['to'])
    assert view(home, capsys)[1]['unanswered'] == '1'


def test_own_hand_note_answers_but_another_person_does_not(tmp_path, capsys):
    from _step import hand_note
    home, record = replay(tmp_path)
    hand_note(home.root, ASK)  # person:test is not this letter's author
    assert view(home, capsys)[1]['unanswered'] == '1'
    # The documented hand.jsonl interface, with the actual author's identity.
    path = home.facts_dir() / 'hand.jsonl'
    path.write_text(path.read_text().replace('person:test', 'person:arseni'))
    assert view(home, capsys)[1]['unanswered'] == '0'


def test_each_citing_thread_counts_distinct_asks_even_when_settled(tmp_path, capsys):
    home, record = replay(tmp_path)
    write_thread(home.root, 'child', '---\nid: child\nstatus: settled\ntense: plan\n---\n# Child\n\n## Plan\n'
                 '- [x] One · re: ' + ASK + '\n- [x] Two · re: ' + ASK, '')
    assert _threads(['--home', str(home.root)]) == 0
    text = capsys.readouterr().out
    lines = [line.split(' · ') for line in text.splitlines()]
    rows = {line[0]: dict(zip(lines[0], line)) for line in lines[1:]}
    assert rows['inbox']['unanswered'] == rows['child']['unanswered'] == '1'
    receipt(record)  # A receipt without a letter cannot answer anything.
    assert view(home, capsys)[1]['unanswered'] == '1'
