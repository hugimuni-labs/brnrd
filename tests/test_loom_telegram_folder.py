"""Drive the actual connector executables against a local fake Bot API."""
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from brr.channels.telegram import utf16_len
from brr.loom.runtime import connector
from brr.loom.runtime.adapters import resolve
from brr.loom.runtime.home import Home
from brr.loom.runtime.port import _front, parse_frontmatter


@pytest.fixture
def bot(tmp_path, monkeypatch):
    class Bot:
        updates = []
        sent = []
        offsets = []
        failure = None
        downloads = 0
    bot = Bot()

    class API(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, value, status=200):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            method = self.path.rsplit('/', 1)[-1]
            if method == 'getUpdates':
                bot.offsets.append(data['offset'])
                updates = [u for u in bot.updates if u['update_id'] >= data['offset']]
                if not updates:
                    time.sleep(.01)
                self.reply(dict(ok=True, result=updates))
            elif method == 'getFile':
                self.reply(dict(ok=True, result=dict(file_path='photo.jpg')))
            else:
                if bot.failure is not None and len(bot.sent) >= bot.failure:
                    self.reply(dict(ok=False, error_code=403), 403)
                else:
                    bot.sent.append((method, data))
                    self.reply(dict(ok=True, result=dict(message_id=100 + len(bot.sent))))

        def do_GET(self):
            bot.downloads += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'PHOTO')

    server = ThreadingHTTPServer(('127.0.0.1', 0), API)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('BRNRD_TELEGRAM_API', f'http://127.0.0.1:{server.server_port}')
    home = Home(tmp_path, 'aaaa')
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/config.toml').write_text('connectors = ["home-tg=telegram"]')
    bot.home = home
    bot.env = connector.environment(home, 'home-tg')
    bot.state = Path(bot.env['BRNRD_STATE'])
    (bot.state / 'token').write_text('test-token')
    bot.folder = resolve(home, 'telegram', 'connectors')
    yield bot
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def message(ident=7, **extra):
    return dict(update_id=ident, message=dict(message_id=ident, chat=dict(id=42, type='private'),
                **{'from': dict(id=42, first_name='not-an-identity')}, text='hello', **extra))


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    pytest.fail('connector did not reach expected state')


def run_out(bot, tmp_path, body='hello', **fields):
    path = tmp_path / 'out.md'
    path.write_text(_front(dict(kind='letter', id='draft', to='42', **fields), body))
    return subprocess.run([str(bot.folder / 'out'), str(path)], env=bot.env,
                          capture_output=True, text=True, timeout=5)


def test_in_photo_reply_authenticated_sender_and_persistent_cursor(bot, tmp_path):
    bot.updates = [message(photo=[dict(file_id='p')], reply_to_message=dict(message_id=101))]
    drop = tmp_path / 'drop'
    child = subprocess.Popen([str(bot.folder / 'in'), str(drop)], env=bot.env)
    try:
        path = drop / '42:7.md'
        wait_for(path.exists)
        fm = parse_frontmatter(path.read_text())
        assert fm['from'] == '42'
        assert fm['to'] == '42'
        assert fm['id'] == '42:7' and fm['re'] == '42:101'
        assert (drop / '42:7.d/0.jpg').read_bytes() == b'PHOTO'
        wait_for(lambda: (bot.state / 'cursor').exists())
        assert (bot.state / 'cursor').read_text() == '8'
    finally:
        child.terminate()
        child.wait(timeout=5)
    path.unlink()
    bot.offsets.clear()
    child = subprocess.Popen([str(bot.folder / 'in'), str(drop)], env=bot.env)
    try:
        wait_for(lambda: bot.offsets)
        assert bot.offsets[0] == 8
        assert not path.exists()
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_out_splits_utf16_fences_without_truncation_and_returns_ids(bot, tmp_path):
    result = run_out(bot, tmp_path, '```python\n' + '🙂\n' * 5000 + '```', re='42:7')
    assert result.returncode == 0, result.stderr
    ids = json.loads(result.stdout)['ids']
    assert len(ids) == len(bot.sent) > 2
    assert ids[0] == '42:101'
    assert all(utf16_len(data['text']) <= 4000 for _, data in bot.sent)
    assert all(data['reply_parameters'] == dict(message_id=7) for _, data in bot.sent)
    assert sum(data['text'].count('🙂') for _, data in bot.sent) == 5000


def test_refused_before_send_and_partial_send_are_distinct(bot, tmp_path):
    bot.failure = 0
    assert run_out(bot, tmp_path).returncode == 3
    assert bot.sent == []
    bot.failure = 1
    assert run_out(bot, tmp_path, 'a' * 5000).returncode == 1
    assert len(bot.sent) == 1


def test_edit_and_wrong_chat_reply_validation(bot, tmp_path):
    assert run_out(bot, tmp_path, edit='42:101').returncode == 0
    assert bot.sent == [('editMessageText', dict(chat_id='42', text='hello', message_id=101))]
    assert run_out(bot, tmp_path, re='43:7').returncode == 3
    assert run_out(bot, tmp_path, 'x' * 5000, edit='42:101').returncode == 3
    assert len(bot.sent) == 1


def test_command_secret_resolved_at_each_use(bot, tmp_path):
    (bot.state / 'token').write_text('command: printf test-token')
    assert run_out(bot, tmp_path).returncode == 0
    assert bot.sent


def test_telegram_setup_stores_only_private_instance_secret(bot, monkeypatch):
    import runpy
    monkeypatch.setenv('BRNRD_STATE', str(bot.state))
    monkeypatch.setattr('getpass.getpass', lambda _: 'command: printf test-token')
    runpy.run_path(str(bot.folder / 'setup'), run_name='__main__')
    assert (bot.state / 'token').stat().st_mode & 0o777 == 0o600
    assert (bot.state / 'token').read_text().strip() == 'command: printf test-token'


def test_photo_answer_and_reply_bind_same_thread_through_named_instance(bot):
    from brr.daemon2.leases import LocalLeaseAuthority
    from brr.loom.runtime import speak
    from brr.loom.runtime.ledger import read_facts
    from test_loom_relay import speak as drafted

    home = bot.home
    for thread in ('brnrd', 'first'):
        directory = home.thread_dir(thread)
        directory.mkdir(parents=True)
        (directory / 'README.md').write_text(
            f'---\nid: {thread}\nstatus: open\ntense: plan\n---\n# {thread}\n')
    person = home.root / 'self/people/ada'
    person.mkdir(parents=True)
    (person / 'channels.toml').write_text('channels = ["home-tg:42"]\n')
    bot.updates = [message(photo=[dict(file_id='p')])]
    inputs = connector.Inputs(home)
    def received(ident):
        inputs.step()
        return next((f for f in read_facts(home) if f.id == ident), None)
    try:
        first = wait_for(lambda: received('letter:home-tg:42:7'))
        assert first.data['from'] == 'person:ada' and not first.data['label']['taint']
        key = drafted(home, 's-a', 'first', chat='home-tg/42', receipt=False)
        lease = LocalLeaseAuthority(home.root / 'ledger/leases').acquire('router', 'test', 120)
        assert speak.speak(home, lease, key, 'answer', channel='channel:home-tg/42',
                           context=dict(re='42:7'))[0] == 'sent'
        bot.updates.append(message(8, reply_to_message=dict(message_id=101)))
        reply = wait_for(lambda: received('letter:home-tg:42:8'))
        assert reply.data['to'] == 'thread:first'
    finally:
        inputs.close()
