"""Exercise folders at the same subprocess and drop boundaries the loom uses."""

import json
import subprocess
from pathlib import Path

import pytest

from brr.daemon2.leases import LocalLeaseAuthority
from brr.loom.runtime import connector, speak
from brr.loom.runtime.adapters import resolve
from brr.loom.runtime.config import ConfigError, load_config
from brr.loom.runtime.home import Home, channel_parts
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.port import _front, write_send
from brr.loom.runtime.connector import drain


@pytest.fixture
def home(tmp_path):
    home = Home(tmp_path, 'aaaa')
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/config.toml').write_text('connectors = ["fake"]\n')
    directory = home.thread_dir('inbox')
    directory.mkdir(parents=True)
    (directory / 'README.md').write_text('---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n')
    return home


def folder(home, name, carries='', out='print(\'{"ids": ["m1", "m2"]}\')'):
    path = home.root / 'loom/connectors' / name
    path.mkdir(parents=True, exist_ok=True)
    (path / 'carries').write_text(carries + '\n')
    (path / 'in').mkdir()
    program = path / 'out'
    program.write_text('#!/usr/bin/env python3\n' + out + '\n')
    program.chmod(0o755)
    (home.root / 'loom/config.toml').write_text(f'connectors = ["{name}"]\n')
    return path


def lease(home):
    return LocalLeaseAuthority(home.root / 'ledger/leases').acquire('router', 'test', 120)


def test_default_disabled_and_list_values_checked(tmp_path):
    assert load_config(tmp_path).connectors == ()
    (tmp_path / 'loom').mkdir()
    for value in ('"fake"', '["../bad"]', '[4]', '["fake", "fake"]'):
        (tmp_path / 'loom/config.toml').write_text('connectors = ' + value)
        with pytest.raises(ConfigError):
            load_config(tmp_path)


def test_runtime_directories_do_not_shadow_the_bundled_folder(home):
    expected = resolve(home, 'fake', 'connectors')
    connector.environment(home, 'fake')
    assert resolve(home, 'fake', 'connectors') == expected
    local = folder(home, 'fake', 'edit')
    assert resolve(home, 'fake', 'connectors') == local.resolve()
    assert connector.carries(home)['fake'][2]


def test_duplicate_instances_refuse_before_starting_any_child(home, monkeypatch):
    folder(home, 'one')
    folder(home, 'two')
    (home.root / 'loom/config.toml').write_text('connectors = ["same=one", "same=two"]')
    monkeypatch.setattr(subprocess, 'Popen', lambda *_a, **_kw: pytest.fail('started child'))
    with pytest.raises(ConfigError, match='unique connector names'):
        connector.Inputs(home)


@pytest.mark.parametrize('chat', ['fake', 'telegram/../42', 'telegram//42', 'telegram/'])
def test_routing_keeps_address_validation_even_with_a_reply_thread(chat):
    assert connector.route_bare([], chat, reply_thread='untrusted') == 'thread:inbox'


def test_generic_address_and_real_out_with_multiple_receipts(home):
    folder(home, 'addon')
    assert channel_parts('channel:addon/dm', home) == ('addon', 'dm')
    with pytest.raises(ValueError):
        channel_parts('channel:relay/telegram:42', home)
    result, _, _ = speak.speak(home, lease(home), 'writer/a', 'hello', channel='channel:addon/dm')
    assert result == 'sent'
    assert [f.data['receipt']['message_id'] for f in read_facts(home) if f.kind == 'speech.part'] == ['m1', 'm2']

@pytest.mark.parametrize('exit_code,status', [(3, 'not-sent'), (1, 'maybe-sent')])
def test_out_exit_contract_and_recovery_never_repeat_the_effect(home, exit_code, status):
    count = home.root / 'calls'
    folder(home, 'addon', out=f'import sys\nfrom pathlib import Path\np=Path({str(count)!r})\np.write_text(p.read_text()+"x" if p.exists() else "x")\nprint("closed window", file=sys.stderr)\nsys.exit({exit_code})')
    grant = lease(home)
    for _ in range(2):
        state, fact, _ = speak.speak(home, grant, 'writer/a', 'hello', channel='channel:addon/dm')
        assert state == status
    assert count.read_text() == 'x'
    if status == 'not-sent':
        assert fact.data['reason'] == 'closed window'
        assert speak.speak(home, grant, 'writer/fresh', 'retry', channel='channel:addon/dm')[0] == status
        assert count.read_text() == 'xx'


def test_timeout_and_bad_receipt_are_uncertain(home, monkeypatch):
    folder(home, 'addon')
    def timeout(*_a, **kw):
        assert kw['timeout'] == 60
        raise subprocess.TimeoutExpired('out', 60)
    monkeypatch.setattr(subprocess, 'run', timeout)
    assert speak.speak(home, lease(home), 'writer/a', 'hello', channel='channel:addon/dm')[0] == 'maybe-sent'


def test_unreadable_drop_is_quarantined_with_one_notice(home):
    drop = home.root / 'loom/connectors/fake/state/in'
    drop.mkdir(parents=True)
    (drop / 'bad.md').write_bytes(b'\xff')
    for _ in range(2):
        drain(home, 'fake', drop)
    assert (drop / 'refused/bad.md').exists()
    assert len([f for f in read_facts(home) if f.kind == 'letter']) == 1


def test_retry_state_resets_with_inputs_and_keeps_only_quarantine_diagnostics(home, monkeypatch):
    drop = home.root / 'loom/connectors/fake/state/in'
    drop.mkdir(parents=True)
    path = drop / 'retry.md'
    path.write_text(_front(dict(kind='letter', id='retry', to='in'), 'retry'))
    attempts = []
    def fail(*_args):
        attempts.append(1)
        raise RuntimeError('unavailable ledger')
    monkeypatch.setattr(connector, 'ingest', fail)
    first = connector.Inputs(home)
    with pytest.raises(RuntimeError, match='unavailable ledger'):
        first.step()
    assert not path.with_suffix('.failures').exists()
    first.close()
    restarted = connector.Inputs(home)
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match='unavailable ledger'):
                restarted.step()
            assert path.exists()
        restarted.step()
        assert len(attempts) == 4
        assert (drop / 'refused/retry.failures').read_text() == '3'
        assert not path.exists()
    finally:
        restarted.close()


def test_passive_input_ingests_and_removes_the_letter(home):
    drop = home.root / 'loom/connectors/fake/state/in'
    drop.mkdir(parents=True)
    (drop / 'hello.md').write_text(_front(dict(kind='letter', id='hello', to='in', **{'from': 'visitor'}), 'hello'))
    inputs = connector.Inputs(home)
    try:
        inputs.step()
        letter, = [f for f in read_facts(home) if f.kind == 'letter']
        assert letter.data['from'] == 'stranger:fake:visitor'
        assert letter.data['label']['taint']
        assert not list(drop.glob('*.md'))
    finally:
        inputs.close()


def test_fake_runs_as_an_executable_and_replays_once(home):
    grant = lease(home)
    for _ in range(2):
        assert speak.speak(home, grant, 'writer/a', 'hello')[0] == 'sent'
    rows = (home.root / 'loom/channel-fake.jsonl').read_text().splitlines()
    assert [json.loads(row) for row in rows] == [dict(key='writer/a', text='hello')]


def test_relay_photo_answer_and_reply_through_real_folder_processes(home, tmp_path):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from brr.gates import cloud
    from test_loom_relay import speak as spoke

    sent = []
    class Relay(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def reply(self, value, binary=False):
            body = value if binary else json.dumps(value).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if '/attachments/' in self.path:
                self.reply(b'PHOTO', binary=True)
                return
            from urllib.parse import parse_qs, urlsplit
            since = int(parse_qs(urlsplit(self.path).query)['since'][0])
            events, cursor = [], since
            if since == 0 or (sent and since == 1):
                number = 1 if since == 0 else 2
                origin = dict(platform='telegram', chat_id=42, user_id=42, message_id=number)
                if number == 2:
                    origin['reply_to_message_id'] = 200
                events = [dict(event_id=f'ev{number}', body='photo' if number == 1 else 'reply',
                               reply_to=origin, attachments=[dict(filename='p.jpg')] if number == 1 else [])]
                cursor = number
            else:
                time.sleep(.02)
            self.reply(dict(events=events, cursor=cursor))
        def do_POST(self):
            sent.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.reply(dict(message_id=200))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Relay)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    state = tmp_path / 'cloud'
    cloud._save_state_to_dir(state, dict(brnrd_url=f'http://127.0.0.1:{server.server_port}', token='test'))
    (home.root / 'loom/config.toml').write_text(f'connectors = ["relay"]\nrelay_state = "{state}"\n')
    person = home.root / 'self/people/ada'
    person.mkdir(parents=True)
    (person / 'channels.toml').write_text('channels = ["relay:telegram:42"]\n')
    directory = home.thread_dir('first')
    directory.mkdir()
    (directory / 'README.md').write_text('---\nid: first\nstatus: open\ntense: plan\n---\n# First\n')
    inputs = connector.Inputs(home)
    try:
        def receive(ident):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                inputs.step()
                found = [f for f in read_facts(home) if f.id == ident]
                if found:
                    return found[0]
                time.sleep(.01)
            pytest.fail(f'no inbound {ident}')
        first = receive('letter:relay:telegram:42:1')
        assert first.data['from'] == 'person:ada' and not first.data['label']['taint']
        assert any(p.read_bytes() == b'PHOTO' for p in (home.root / 'blobs').iterdir())
        key = spoke(home, 's-a', 'first', chat='relay/telegram:42', receipt=False)
        assert speak.speak(home, lease(home), key, 'answer', channel='channel:relay/telegram:42',
                           context=dict(re='telegram:42:1'))[0] == 'sent'
        assert sent == [('/v1/daemons/responses', dict(event_id='ev1', body_markdown='answer', status='done'))]
        reply = receive('letter:relay:telegram:42:2')
        assert reply.data['to'] == 'thread:first'
    finally:
        inputs.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
