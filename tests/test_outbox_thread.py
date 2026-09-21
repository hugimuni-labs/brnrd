"""Thread-addressed sends keep closed letters closed and use their gate address."""

import json
import time
from types import SimpleNamespace

import pytest

from brr import cli, conversations, daemon, do, protocol
from brr.gates import cloud, runtime


@pytest.fixture
def lane(tmp_path, monkeypatch):
    brr = tmp_path / '.brr'
    inbox, responses = brr / 'inbox', brr / 'responses'
    outbox = brr / 'outbox' / 'evt-own'
    outbox.mkdir(parents=True)
    emit = daemon._WorkerEmit(brr, 'cloud:telegram:1:', 'evt-own')
    task = SimpleNamespace(id='run-thread', meta={})
    monkeypatch.setattr(daemon, '_gate_can_deliver', lambda *_: True)
    monkeypatch.setattr(daemon, '_ambiguous_bare_gate', lambda *_: None)
    posts = []
    monkeypatch.setattr(cloud, '_request', lambda *a, **kw: posts.append(kw['json']) or {})

    def inbound(tier='owner', *, source='cloud'):
        meta = dict(cloud_event_id='remote-event', cloud_platform='telegram',
                    cloud_chat_id='2', cloud_topic_id='7') if source == 'cloud' else dict(
                        telegram_chat_id='2', telegram_topic_id='7')
        path = protocol.create_event(inbox, source, 'Please send results later',
                                     status='delivered', trust_tier=tier, **meta)
        event = protocol._read_event(path)
        key = conversations.conversation_key_for_event(event)
        conversations.append_event(brr, key, event)
        return key, event

    def drain():
        return daemon._drain_outbox(emit, task, responses, 'evt-own', outbox, inbox)

    def deliver():
        cloud._deliver_responses(brr, inbox, responses,
                                 {'brnrd_url': 'https://invalid.test', 'token': 'fake'})

    return SimpleNamespace(brr=brr, inbox=inbox, responses=responses, outbox=outbox,
                           inbound=inbound, drain=drain, deliver=deliver, posts=posts)


def test_closed_owner_thread_delivers_once_and_records_target(lane):
    key, event = lane.inbound()
    do.stage_thread(lane.outbox, key, 'Here are the results')
    assert protocol.list_pending(lane.inbox) == []
    assert lane.drain() == 1
    lane.deliver()
    lane.deliver()
    assert lane.posts == [{'event_id': 'remote-event', 'body_markdown': 'Here are the results',
                           'status': 'done', 'conversation_id': key}]
    assert protocol._read_event(event['_path'])['status'] == 'delivered'
    records = conversations.read_records(lane.brr, key)
    assert records[-1]['body'] == 'Here are the results'
    assert records[-1]['artifact_kind'] == 'outbound_message'
    assert not conversations.read_records(lane.brr, 'cloud:telegram:1:')
    assert not protocol.list_pending(lane.inbox)


@pytest.mark.parametrize('case', ['unknown', 'collaborator', 'untrusted', 'missing_event'])
def test_thread_refuses_without_dispatch(lane, case):
    key = 'cloud:telegram:404:'
    if case != 'unknown':
        key, _ = lane.inbound()
        if case == 'missing_event':
            _, event = lane.inbound()
            event['_path'].unlink()
        else:
            lane.inbound(case)  # Latest inbound wins over the earlier owner.
    do.stage_thread(lane.outbox, key, 'Do not send')
    assert lane.drain() == 0
    lane.deliver()
    assert lane.posts == []
    assert not list(lane.responses.glob('*.md'))
    notices = daemon._read_outbox_notices(lane.outbox)
    reason = 'unknown conversation' if case == 'unknown' else 'correspondent is not an account user'
    assert any('thread refused: ' + reason in n['text'] for n in notices)


def test_native_thread_uses_recorded_address_not_supplied_override(lane):
    key, _ = lane.inbound(source='telegram')
    do.stage_message(lane.outbox, 'send.md', meta={
        'thread': key, 'telegram_chat_id': 'attacker', 'trust_tier': 'owner',
    }, body='Native result')
    assert lane.drain() == 1
    calls = []
    runtime.deliver_stream(lane.inbox, lane.responses, 'telegram',
                           lambda event, body: calls.append((event, body)))
    assert len(calls) == 1
    assert str(calls[0][0]['telegram_chat_id']) == '2'
    assert calls[0][1] == 'Native result'


@pytest.mark.parametrize('known', [True, False])
def test_do_thread_reads_real_drain_verdict(lane, monkeypatch, capsys, tmp_path, known):
    key = lane.inbound()[0] if known else 'cloud:telegram:404:'
    body = tmp_path / 'body.md'
    body.write_text('The result')
    monkeypatch.setenv('BRR_OUTBOX_DIR', str(lane.outbox))
    state = lane.outbox / 'portal-state.json'
    state.write_text(json.dumps({'notices': [], 'attention': {'pending_event_count': 0}}))

    def consume(_):
        lane.drain()
        state.write_text(json.dumps({'notices': daemon._read_outbox_notices(lane.outbox)}))

    monkeypatch.setattr(time, 'sleep', consume)
    assert cli.main(['do', '--to-thread', key, '--body-file', str(body)]) == (0 if known else 1)
    output = capsys.readouterr().out
    assert ('✓' if known else 'thread refused: unknown conversation') in output
    lane.deliver()
    assert len(lane.posts) == int(known)


def test_missing_cloud_address_never_falls_back_to_default_chat(lane):
    key, event = lane.inbound()
    protocol.update_event_meta(event, cloud_event_id="")
    do.stage_thread(lane.outbox, key, "Do not send to default")
    assert lane.drain() == 0
    lane.deliver()
    assert not lane.posts
    assert "no usable gate address" in daemon._read_outbox_notices(lane.outbox)[-1]["text"]
