"""A folder, including a passive fake, pairs through the one letter API."""
import getpass
import json
import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from brr.loom.runtime import connector, setup_channel
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import Home
from brr.loom.runtime.port import _front


def code_in(line):
    found = re.search(r'send the code (\w+)\.', line)
    return found and found.group(1)


@pytest.fixture(autouse=True)
def no_harness_on_path(monkeypatch):
    """The body step sees no claude or codex unless a test puts them there; this Mac's PATH would make it ask."""
    from brr.loom.runtime import service
    real = service._defaults
    monkeypatch.setattr(service, '_defaults', lambda: [])
    return real


def home_at(tmp_path):
    (tmp_path / 'loom').mkdir()
    return Home(tmp_path, 'aaaa')


def test_pair_sets_connectors_on_the_template_line_and_keeps_the_rest(tmp_path):
    from brr.loom.runtime.config import render_config, seed_config
    home = home_at(tmp_path)
    seed_config(tmp_path)
    before = render_config().splitlines()
    state = Path(connector.environment(home, 'desk')['BRNRD_STATE'])
    def send_code(line):
        if code_in(line):
            (state / 'in' / 'code.md').write_text(_front(dict(kind='letter', id='code',
                                                   to='42', **{'from': '42'}), code_in(line)))
    assert setup_channel.pair(home, 'fake', 'desk', 'ada', out=send_code) == 'desk:42'
    after = (tmp_path / 'loom/config.toml').read_text().splitlines()
    at = before.index('# connectors = []')
    assert after[at] == 'connectors = ["desk=fake"]'
    assert after[:at] + after[at + 1:] == before[:at] + before[at + 1:]


def test_fake_live_pair_saves_named_identity_and_keeps_unrelated_letters(tmp_path):
    home = home_at(tmp_path)
    state = Path(connector.environment(home, 'desk')['BRNRD_STATE'])
    def send_code(line):
        code = code_in(line)
        if not code:
            return
        drop = state / 'in'
        (drop / 'other.md').write_text(_front(dict(kind='letter', id='other',
                                      to='42', **{'from': '42'}), 'another letter'))
        (drop / 'code.md').write_text(_front(dict(kind='letter', id='code',
                                      to='42', **{'from': '42'}), code))
    assert setup_channel.pair(home, 'fake', 'desk', 'ada', out=send_code) == 'desk:42'
    assert load_config(tmp_path).connectors == ('desk=fake',)
    assert (tmp_path / 'self/people/ada/channels.toml').read_text() == 'channels = ["desk:42"]\n'
    sent = [json.loads(r) for r in (tmp_path / 'loom/channel-fake.jsonl').read_text().splitlines()]
    assert sent[0]['text'].startswith('Paired ✓')
    assert (state / 'in/other.md').exists() and not (state / 'in/code.md').exists()
    assert connector.person_dm(home, 'channel:desk/42') == 'person:ada'
    with pytest.raises(ValueError, match='already in use'):
        setup_channel.pair(home, 'fake', 'desk', 'ada')


def test_timeout_never_enables_or_enrols_channel(tmp_path):
    home = home_at(tmp_path)
    with pytest.raises(TimeoutError):
        setup_channel.pair(home, 'fake', 'desk', 'ada', timeout=0, out=lambda _: None)
    assert load_config(tmp_path).connectors == ()
    assert not (tmp_path / 'self/people/ada/channels.toml').exists()


def test_group_or_spoofed_identity_never_pairs(tmp_path):
    home = home_at(tmp_path)
    state = Path(connector.environment(home, 'desk')['BRNRD_STATE'])
    def send_code(line):
        if code_in(line):
            (state / 'in/code.md').write_text(_front(dict(kind='letter', id='code',
                        to='-42', **{'from': '42'}), code_in(line)))
    with pytest.raises(TimeoutError):
        setup_channel.pair(home, 'fake', 'desk', 'ada', timeout=.1, out=send_code)
    assert load_config(tmp_path).connectors == ()


def test_relay_setup_signs_in_and_writes_the_gate_the_relay_reads(tmp_path):
    """The cloud choice is a sign-in, not a pointer to `brnrd account connect`."""
    import http.server
    import threading
    from brr.gates import cloud
    seen, polls = [], iter([{'status': 'pending'}])

    class Pairing(http.server.BaseHTTPRequestHandler):
        def _send(self, doc):
            raw = json.dumps(doc).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            size = int(self.headers.get('Content-Length') or 0)
            seen.append(('POST', self.path, json.loads(self.rfile.read(size) or b'null'),
                         self.headers.get('Authorization')))
            self._send({'pair_code': 'BR-T', 'pair_url': 'http://approve.example/BR-T',
                        'poll_secret': 's'} if self.path == '/v1/accounts/pair' else {})

        def do_GET(self):
            seen.append(('GET', self.path.split('?')[0], None, None))
            self._send(next(polls, {'status': 'paired', 'account_id': 'acc_x', 'repo_id': 'repo_x',
                                    'daemon_token': 'bd_tok'}))

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Pairing)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home = home_at(tmp_path)
    subprocess.run(['git', 'init', '-q', str(tmp_path / 'self')], check=True)
    env = dict(connector.environment(home, 'cloud'), BRNRD_URL=f'http://127.0.0.1:{server.server_port}')
    try:
        done = subprocess.run([str(Path(setup_channel.__file__).parent / 'connectors/relay/setup')],
                              env=env, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    assert 'Open http://approve.example/BR-T and sign in' in done.stdout
    assert 'Only approve a code you started yourself: the page shows BR-T for ' in done.stdout
    assert 'Signed in to brnrd.dev' in done.stdout
    # Bound to the home's self repo, which has no remote: the server makes a local repo for it.
    assert seen[0][:2] == ('POST', '/v1/accounts/pair') and seen[0][2]['forge'] == 'local'
    # The approve page names the computer that asked, beside the code.
    assert seen[0][2]['hostname'] == socket.gethostname() and seen[0][2]['os_user'] == getpass.getuser()
    assert seen[-1][:2] == ('POST', '/v1/daemons/register') and seen[-1][3] == 'Bearer bd_tok'
    assert load_config(tmp_path).relay_state == str(tmp_path / 'loom')
    state = cloud._load_state_from_dir(tmp_path / 'loom')
    assert (state['token'], state['account_id'], state['repo_id']) == ('bd_tok', 'acc_x', 'repo_x')
    assert 'token' not in json.loads((tmp_path / 'loom/gates/cloud.json').read_text())
    # A home already signed in is left as it is.
    again = subprocess.run([str(Path(setup_channel.__file__).parent / 'connectors/relay/setup')],
                           env=env, capture_output=True, text=True, timeout=60)
    assert again.returncode == 0 and again.stdout == ''


def test_first_setup_can_create_the_default_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('BRNRD_HOME', raising=False)
    roots = []
    monkeypatch.setattr(setup_channel, 'pair', lambda home, *_a, **_kw: roots.append(home.root))
    assert setup_channel.main(['--kind', 'fake', '--name', 'desk', '--person', 'ada']) == 0
    assert roots == [tmp_path / 'brnrd']


@pytest.mark.parametrize('receipt', ['[]', '{"ids": [null]}'])
def test_bad_out_receipt_never_enrols_person(tmp_path, receipt):
    home = home_at(tmp_path)
    folder = tmp_path / 'loom/connectors/fake'
    folder.mkdir(parents=True)
    (folder / 'carries').write_text('')
    program = folder / 'out'
    program.write_text('#!/usr/bin/env python3\nprint(' + repr(receipt) + ')\n')
    program.chmod(0o755)
    def send_code(line):
        if code_in(line):
            drop = Path(connector.environment(home, 'desk')['BRNRD_STATE']) / 'in'
            (drop / 'code.md').write_text(_front(dict(kind='letter', id='code',
                        to='42', **{'from': '42'}), code_in(line)))
    with pytest.raises(ValueError, match='ids receipt'):
        setup_channel.pair(home, 'fake', 'desk', 'ada', out=send_code)
    assert load_config(tmp_path).connectors == ()
    assert not (tmp_path / 'self/people/ada/channels.toml').exists()


def test_home_folder_pairs_ingests_and_sends_native_ids(tmp_path):
    from brr.daemon2.leases import LocalLeaseAuthority
    from brr.loom.runtime import speak
    from brr.loom.runtime.ledger import read_facts

    home = home_at(tmp_path)
    folder = tmp_path / 'loom/connectors/postbox'
    folder.mkdir(parents=True)
    (folder / 'carries').write_text('')
    (folder / 'in').mkdir()  # A passive input, just like the bundled file transport.
    outgoing = folder / 'out'
    outgoing.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from brr.loom.runtime.port import parse_frontmatter
letter = parse_frontmatter(Path(sys.argv[1]).read_text())
assert letter['to'] == 'native:42'
with (Path(os.environ['BRNRD_STATE']) / 'sent').open('a') as stream:
    stream.write(json.dumps(letter) + '\\n')
print('{"ids": ["native-receipt"]}')
''')
    outgoing.chmod(0o755)
    state = Path(connector.environment(home, 'desk')['BRNRD_STATE'])
    def wire(ident, text):
        (state / 'in' / f'{ident}.md').write_text(_front(dict(kind='letter', id=ident,
                                   to='native:42', **{'from': 'native:42'}), text))
    def send_code(line):
        if code_in(line):
            wire('pair', code_in(line))
    assert setup_channel.pair(home, 'postbox', 'desk', 'ada', out=send_code) == 'desk:native:42'
    assert (tmp_path / 'self/people/ada/channels.toml').read_text() == 'channels = ["desk:native:42"]\n'
    wire('message', 'hello')
    connector.drain(home, 'desk', state / 'in')
    source, = [f for f in read_facts(home) if f.kind == 'source']
    assert source.id == 'source:desk:message'
    assert source.data['channel'] == 'channel:desk/native:42'
    assert source.data['from'] == 'person:ada'
    lease = LocalLeaseAuthority(tmp_path / 'ledger/leases').acquire('router', 'test', 120)
    assert speak.speak(home, lease, 'draft', 'answer', channel='channel:desk/native:42')[0] == 'sent'
    sent = [json.loads(row) for row in (state / 'sent').read_text().splitlines()]
    assert [row['to'] for row in sent] == ['native:42', 'native:42']
    assert sent[-1]['body'] == 'answer'


def test_setup_seeds_a_self_so_a_letter_has_a_thread_to_land_on(tmp_path, monkeypatch):
    # The shape a pairing left before setup seeded anything: a bare people file.
    channels = tmp_path / 'self/people/ada/channels.toml'
    channels.parent.mkdir(parents=True)
    channels.write_text('channels = ["desk:42"]\n')
    monkeypatch.setattr(setup_channel, 'pair', lambda *a, **kw: None)
    assert setup_channel.main(['--home', str(tmp_path), '--kind', 'fake',
                               '--name', 'desk', '--person', 'ada']) == 0
    home = Home(tmp_path, 'aaaa')
    assert home.main_thread() == 'inbox' and home.routable('inbox')
    tracked = subprocess.run(['git', '-C', str(tmp_path / 'self'), 'ls-tree', '-r', '--name-only', 'main'],
                             capture_output=True, text=True, check=True).stdout.split()
    assert 'threads/inbox/README.md' in tracked and 'people/ada/agreement.md' in tracked
    # The pairing's file survives the seed, outside the commit.
    assert channels.read_text() == 'channels = ["desk:42"]\n'
    assert 'people/ada/channels.toml' not in tracked
    assert connector._sender(home, 'desk', '42') == 'person:ada'
    # A second run finds the self and leaves it be.
    assert setup_channel.main(['--home', str(tmp_path), '--kind', 'fake',
                               '--name', 'desk', '--person', 'ada']) == 0


def test_pairing_prompt_says_the_person_writes_first(tmp_path):
    lines = []
    with pytest.raises(TimeoutError):
        setup_channel.pair(home_at(tmp_path), 'fake', 'desk', 'ada', timeout=0, out=lines.append)
    assert code_in(lines[0]) and 'does not write first' in lines[0]


@pytest.mark.parametrize('door, linked', [
    ('print(json.dumps(dict(name="@ada_bot", link="https://t.me/ada_bot?start=" + sys.argv[1])))', True),
    ('sys.exit(1)', False),
    ('print("not json")', False),
])
def test_a_door_puts_its_link_in_the_prompt_and_a_broken_one_falls_back(tmp_path, door, linked):
    home = home_at(tmp_path)
    folder = tmp_path / 'loom/connectors/fake'
    shutil.copytree(Path(setup_channel.__file__).parent / 'connectors/fake', folder)
    (folder / 'door').write_text('#!/usr/bin/env python3\nimport json, sys\n' + door + '\n')
    (folder / 'door').chmod(0o755)
    lines = []
    with pytest.raises(TimeoutError):
        setup_channel.pair(home, 'fake', 'desk', 'ada', timeout=0, out=lines.append)
    code = code_in(lines[0])
    assert code and 'does not write first' in lines[0]
    assert (f'https://t.me/ada_bot?start={code}' in lines[0]) == linked


def test_a_door_link_is_also_a_qr_code_a_phone_can_scan(tmp_path):
    segno = pytest.importorskip('segno')
    home = home_at(tmp_path)
    folder = tmp_path / 'loom/connectors/fake'
    shutil.copytree(Path(setup_channel.__file__).parent / 'connectors/fake', folder)
    (folder / 'door').write_text('#!/usr/bin/env python3\nimport json, sys\n'
                                 'print(json.dumps(dict(name="@ada_bot", link="https://t.me/ada_bot?start=" + sys.argv[1])))\n')
    (folder / 'door').chmod(0o755)
    lines = []
    with pytest.raises(TimeoutError):
        setup_channel.pair(home, 'fake', 'desk', 'ada', timeout=0, out=lines.append, qr=True)
    code = code_in(lines[0])
    assert 'scan the code below' in lines[0] and len(lines) == 2
    # Every module is painted explicitly, black or white, and they spell the link.
    painted = [[cell == '40' for cell in re.findall(r'\x1b\[(4[07])m  ', row)] for row in lines[1].splitlines()]
    want = [[bool(dark) for dark in row]
            for row in segno.make(f'https://t.me/ada_bot?start={code}', error='l').matrix_iter(border=2)]
    assert painted == want

    lines = []
    with pytest.raises(TimeoutError):
        setup_channel.pair(Home(tmp_path, 'aaaa'), 'fake', 'desk2', 'ada', timeout=0, out=lines.append, qr=False)
    assert len(lines) == 1 and 'scan' not in lines[0] and 'https://t.me/ada_bot?start=' in lines[0]


def test_guided_setup_asks_in_plain_words_and_derives_the_channel_name(tmp_path, monkeypatch, capsys):
    import io
    calls = []
    monkeypatch.setattr(setup_channel, 'pair', lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(setup_channel, '_full_name', lambda: 'Sasha Petrova')
    monkeypatch.setattr(setup_channel.shutil, 'which', lambda name: f'/bin/{name}')
    monkeypatch.setattr('brr.loom.runtime.service._windows', lambda home, shell: None)
    monkeypatch.setattr('brr.loom.runtime.service._defaults', lambda: [('claude', ''), ('codex', '')])
    # Piped answers: codex, Enter for Telegram, Enter for the OS name.
    monkeypatch.setattr('sys.stdin', io.StringIO('2\n\n\n'))
    assert setup_channel.main(['--home', str(tmp_path)]) == 0
    out = capsys.readouterr().out
    for asked in ('Which AI should brnrd think with?', 'How do you want to talk to brnrd?',
                  'What should brnrd call you?', 'brnrd will call you Sasha Petrova'):
        assert asked in out
    for gone in ('Channel kind', 'Name this channel', 'Your person name', 'fake'):
        assert gone not in out
    assert [a[1:] for a in calls] == [('telegram', 'telegram', 'sasha-petrova')]
    assert (tmp_path / 'loom' / 'preferences.toml').read_text().startswith('model.default = "codex"')
    assert load_config(tmp_path).body is None


def test_a_second_channel_of_a_kind_gets_a_numbered_name(tmp_path):
    home = home_at(tmp_path)
    (tmp_path / 'loom').mkdir(exist_ok=True)
    (tmp_path / 'loom/config.toml').write_text('connectors = ["telegram=telegram"]\n')
    assert setup_channel._channel_name(Home(tmp_path), 'telegram') == 'telegram-2'
    assert setup_channel._person_id('  Ada Lovelace! ') == 'ada-lovelace'


def test_a_running_loom_hears_a_channel_paired_after_it_started(tmp_path):
    from brr.loom.runtime.ledger import read_facts
    home = home_at(tmp_path)
    inbox = home.thread_dir('inbox')
    inbox.mkdir(parents=True)
    (inbox / 'README.md').write_text('---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n')
    inputs = connector.Inputs(home)  # the loom, started before any channel exists
    try:
        inputs.step()
        state = Path(connector.environment(home, 'desk')['BRNRD_STATE'])
        def send_code(line):
            code = code_in(line)
            if not code:
                return
            drop = state / 'in'
            # The start link and the typed code: the same code arrives twice.
            for ident in ('code', 'again'):
                (drop / f'{ident}.md').write_text(_front(dict(kind='letter', id=ident,
                                                  to='42', **{'from': '42'}), code))
        assert setup_channel.pair(home, 'fake', 'desk', 'ada', out=send_code) == 'desk:42'
        assert not list((state / 'in').glob('*.md'))
        (state / 'in/hello.md').write_text(_front(dict(kind='letter', id='hello',
                                           to='42', **{'from': '42'}), 'hello brnrd'))
        inputs.step()
        letter, = [f for f in read_facts(home) if f.kind == 'letter']
        assert letter.data['body'] == 'hello brnrd'
        assert letter.data['to'] == 'thread:inbox'
        assert letter.data['from'] == 'person:ada'
    finally:
        inputs.close()


def test_setup_is_done_when_the_first_letter_reaches_a_body(tmp_path):
    import threading
    import time
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.ledger import append, read_facts
    home = home_at(tmp_path)
    inbox = home.thread_dir('inbox')
    inbox.mkdir(parents=True)
    (inbox / 'README.md').write_text('---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n')
    (tmp_path / 'self/people/ada').mkdir(parents=True)
    (tmp_path / 'self/people/ada/channels.toml').write_text('channels = ["desk:42"]\n')
    (tmp_path / 'loom/config.toml').write_text('connectors = ["desk=fake"]\n')
    said, result = [], {}
    def wait():
        try:
            result['strand'] = setup_channel.first_letter(home, 'ada', timeout=30, out=said.append,
                                                          running=lambda: True, sleep=lambda s: time.sleep(.02))
        except Exception as exc:
            result['error'] = exc
    inputs = connector.Inputs(home)
    waiter = threading.Thread(target=wait)
    waiter.start()
    try:
        inputs.step()
        state = Path(connector.environment(home, 'desk')['BRNRD_STATE'])
        (state / 'in/hello.md').write_text(_front(dict(kind='letter', id='hello',
                                           to='42', **{'from': '42'}), 'hello brnrd'))
        inputs.step()
        letter, = [f for f in read_facts(home) if f.kind == 'letter']
        assert waiter.is_alive()  # landed is not done: no body has read it
        append(home, Fact(kind='shown', by='strand:s-1', id='shown:s-1:1',
                          data={'strand': 's-1', 'gen': 1, 'ids': [letter.data['id']]}))
        waiter.join(30)
    finally:
        inputs.close()
    assert result == {'strand': 's-1'}
    words = '\n'.join(said)
    assert 'brnrd got your message' in words and 'brnrd is reading it now (s-1)' in words


def test_setup_says_when_nothing_is_running_to_hear_the_first_letter(tmp_path):
    home = home_at(tmp_path)
    said = []
    with pytest.raises(TimeoutError, match='No message from you reached brnrd'):
        setup_channel.first_letter(home, 'ada', timeout=.2, out=said.append, running=lambda: False,
                                   sleep=lambda s: None)
    assert said == [said[0]] and '`brnrd loom install`' in said[0]


def test_brnrd_setup_passes_wait_through(monkeypatch):
    """`brnrd setup --wait 15` was refused by the outer parser before the guided setup saw it."""
    from brr import cli
    seen = []
    monkeypatch.setattr(setup_channel, 'main', lambda argv: seen.append(argv) or 0)
    cli.main(['setup', '--kind', 'fake', '--wait', '15'])
    assert seen == [['channel', '--kind', 'fake', '--timeout', '180', '--wait', '15.0']]


def test_the_body_step_shows_every_harness_with_its_fuel_and_keeps_a_set_one_on_enter(tmp_path, monkeypatch, capsys,
                                                                                       no_harness_on_path):
    """Sasha's install: claude was already set, codex (no model in its config) was neither offered nor shown."""
    import io
    from brr.loom.runtime.adapters import default_body, prefer
    home = home_at(tmp_path)
    prefer(home, 'default', 'claude')
    monkeypatch.setattr(setup_channel.shutil, 'which', lambda name: f'/bin/{name}')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'no-codex-config'))
    fuel = {'claude': [{'name': 'session', 'left_pct': 71}, {'name': 'week', 'left_pct': 50}],
            'codex': [{'name': 'session', 'left_pct': 100}]}
    monkeypatch.setattr('brr.loom.runtime.service._windows', lambda home, shell: fuel[shell])
    monkeypatch.setattr('brr.loom.runtime.service._defaults', no_harness_on_path)
    monkeypatch.setattr('sys.stdin', io.StringIO('\n'))
    setup_channel._choose_body(home)
    out = capsys.readouterr().out
    assert '1. claude: session 71% · week 50% left' in out
    assert '2. codex: session 100% left' in out
    assert 'claude (kept)' in out and default_body(home) == 'claude'
    monkeypatch.setattr('sys.stdin', io.StringIO('2\n'))
    setup_channel._choose_body(home)
    assert default_body(home) == 'codex'
