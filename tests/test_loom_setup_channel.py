"""A folder, including a passive fake, pairs through the one letter API."""
import getpass
import json
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from brr.loom.runtime import connector, setup_channel
from brr.loom.runtime.config import load_config, set_value
from brr.loom.runtime.home import Home
from brr.loom.runtime.port import _front


def code_in(line):
    found = re.search(r'send the code ([\w-]+)\.', line)
    return found and found.group(1)


@pytest.fixture(autouse=True)
def no_harness_on_path(monkeypatch):
    """The body step sees no claude or codex unless a test puts them there; this Mac's PATH would make it ask."""
    from brr.loom.runtime import service
    real = service._defaults
    monkeypatch.setattr(service, '_defaults', lambda: [])
    return real


@pytest.fixture(autouse=True)
def no_gh_login(monkeypatch):
    """Setup's GitHub offers see no gh login unless a test gives one; this Mac's gh would make it create repos."""
    monkeypatch.setattr(setup_channel, '_gh_login', lambda: (None, 'needs gh signed in'))


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
    # The same kind may pair again (a door that went dark); another kind may not take the name.
    with pytest.raises(ValueError, match='already in use'):
        setup_channel.pair(home, 'telegram', 'desk', 'ada')


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
    assert 'board' not in seen[0][2]  # nobody ticked the board offer
    assert seen[-1][:2] == ('POST', '/v1/daemons/register') and seen[-1][3] == 'Bearer bd_tok'
    assert load_config(tmp_path).relay_state == str(tmp_path / 'loom')
    state = cloud._load_state_from_dir(tmp_path / 'loom')
    assert (state['token'], state['account_id'], state['repo_id']) == ('bd_tok', 'acc_x', 'repo_x')
    assert 'token' not in json.loads((tmp_path / 'loom/gates/cloud.json').read_text())
    # A home already signed in is left as it is.
    again = subprocess.run([str(Path(setup_channel.__file__).parent / 'connectors/relay/setup')],
                           env=env, capture_output=True, text=True, timeout=60)
    assert again.returncode == 0 and again.stdout == ''
    # So is a home whose gate was migrated into loom/gates with no relay_state line.
    config = tmp_path / 'loom' / 'config.toml'
    config.write_text('\n'.join(line for line in config.read_text().splitlines()
                                if not line.startswith('relay_state')) + '\n')
    assert load_config(tmp_path).relay_state is None
    migrated = subprocess.run([str(Path(setup_channel.__file__).parent / 'connectors/relay/setup')],
                              env=env, capture_output=True, text=True, timeout=60)
    assert migrated.returncode == 0 and migrated.stdout == ''
    # Sasha's home: relay_state still set, cloud.json gone. Not connected: it pairs afresh.
    set_value(tmp_path, 'relay_state', str(tmp_path / 'loom'))
    (tmp_path / 'loom/gates/cloud.json').unlink()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Pairing)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env['BRNRD_URL'] = f'http://127.0.0.1:{server.server_port}'
    try:
        fresh = subprocess.run([str(Path(setup_channel.__file__).parent / 'connectors/relay/setup')],
                               env=env, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
    assert fresh.returncode == 0 and 'Traceback' not in fresh.stderr, fresh.stderr
    assert 'Signed in to brnrd.dev' in fresh.stdout
    # The approve page's board box starts unticked (Planet49): setup never asks it ticked.
    assert [s[2].get('board') for s in seen if s[1] == '/v1/accounts/pair'] == [None, None], seen
    assert cloud._load_state_from_dir(tmp_path / 'loom')['brnrd_url'] == env['BRNRD_URL']


def test_a_door_with_no_gate_says_so_and_setup_shows_the_plain_code(tmp_path):
    """relay_state set and no cloud.json: the door exits with a line, not a KeyError."""
    home = home_at(tmp_path)
    set_value(tmp_path, 'relay_state', str(tmp_path / 'loom'))
    folder = Path(setup_channel.__file__).parent / 'connectors/relay'
    env = connector.environment(home, 'cloud')
    done = subprocess.run([str(folder / 'door'), 'BR-1'], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode != 0 and 'Traceback' not in done.stderr, done.stderr
    assert setup_channel._door(folder, env, 'BR-1') is None


def test_the_body_step_reads_every_fuel_at_once_and_names_a_slow_one(tmp_path):
    """Two readers taking 0.4 s each finish in about 0.4 s; one that hangs is SLOW."""
    import threading
    hang = threading.Event()

    def read(root, shell):
        if shell == 'stuck':
            hang.wait(5)
        time.sleep(0.4)
        return [{'name': 'session', 'left_pct': 50}]
    began = time.monotonic()
    got = setup_channel._read_fuel(tmp_path, ['claude', 'codex', 'stuck'], read, timeout=1.0)
    took = time.monotonic() - began
    hang.set()
    assert got['claude'] == got['codex'] == [{'name': 'session', 'left_pct': 50}]
    assert got['stuck'] is setup_channel.SLOW
    assert took < 1.5


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


@pytest.mark.parametrize('code, says', [(3, 'was not sent'), (1, 'may or may not have been sent')])
def test_a_heard_code_pairs_even_when_the_answer_fails_and_says_why(tmp_path, code, says):
    home = home_at(tmp_path)
    folder = tmp_path / 'loom/connectors/fake'
    folder.mkdir(parents=True)
    (folder / 'carries').write_text('')
    program = folder / 'out'
    program.write_text('#!/usr/bin/env python3\nimport sys\n'
                       "print('relay: event not found for this account', file=sys.stderr)\n"
                       f'sys.exit({code})\n')
    program.chmod(0o755)
    said = []
    def send_code(line):
        said.append(line)
        if code_in(line):
            drop = Path(connector.environment(home, 'desk')['BRNRD_STATE']) / 'in'
            (drop / 'code.md').write_text(_front(dict(kind='letter', id='code',
                        to='42', **{'from': '42'}), '/start ' + code_in(line)))
    assert setup_channel.pair(home, 'fake', 'desk', 'ada', out=send_code) == 'desk:42'
    warning = next(line for line in said if line.startswith('Your code arrived'))
    assert says in warning and 'event not found for this account' in warning
    assert load_config(tmp_path).connectors == ('desk=fake',)
    assert (tmp_path / 'self/people/ada/channels.toml').read_text() == 'channels = ["desk:42"]\n'


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
    assert 'core/onboarding.md' in tracked
    # The pairing's file survives the seed, outside the commit.
    assert channels.read_text() == 'channels = ["desk:42"]\n'
    assert 'people/ada/channels.toml' not in tracked
    assert connector._sender(home, 'desk', '42') == 'person:ada'
    # A second run finds the self and leaves it be.
    assert setup_channel.main(['--home', str(tmp_path), '--kind', 'fake',
                               '--name', 'desk', '--person', 'ada']) == 0


def test_setup_turns_the_onboarding_list_into_asks_the_first_wake_shows(tmp_path, monkeypatch):
    from brr.loom.runtime.ledger import read_facts
    from brr.loom.runtime.wishes import waits_on, wake_wishes, wishes
    from brr.loom.runtime.lifecycle import main_snapshot
    monkeypatch.setattr(setup_channel, 'pair', lambda *a, **kw: None)
    argv = ['--home', str(tmp_path), '--kind', 'fake', '--name', 'desk', '--person', 'ada']
    assert setup_channel.main(argv) == 0
    home = Home(tmp_path)
    found = wishes(read_facts(home))
    # The resident's own wish, written by the loom: the person said nothing yet.
    [own] = [w for w in found.values() if w.line == setup_channel.ONBOARDING_WISH]
    assert own.sender == 'resident' and not own.says and not own.parent
    asks = [w for w in found.values() if w.parent == own.id]
    assert [w.line for w in sorted(asks, key=lambda w: w.born)][0] == 'What should I call you?'
    # gh is not signed in here, so the backup offer was left off: brnrd asks it later.
    assert [w.line for w in asks][3:] == [setup_channel.LATER['repo']]
    assert len(asks) == 4 and {w.sender for w in asks} == {'resident'}
    assert not [f for f in read_facts(home) if f.kind == 'wish' and not f.by.startswith('loom:')]
    # They wait on the person, and the home thread's next body wakes with them.
    heads = waits_on(found, main_snapshot(home))
    assert {heads[w.id] for w in asks} == {'operator'}
    wake = wake_wishes(read_facts(home), home.main_thread())
    assert 'What should I call you?' in wake and 'What do you want help with first?' in wake
    # The list is the self's: a line added later is asked, none twice.
    listed = tmp_path / 'self/core/onboarding.md'
    listed.write_text(listed.read_text() + '- Which language do you write in?\n')
    assert setup_channel.main(argv) == 0
    lines = [w.line for w in wishes(read_facts(home)).values() if w.parent == own.id]
    assert len(lines) == 5 and lines.count('What should I call you?') == 1


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
    monkeypatch.setattr('getpass.getuser', lambda: 'Sasha.Petrova')
    monkeypatch.setattr(setup_channel.shutil, 'which', lambda name: f'/bin/{name}')
    monkeypatch.setattr('brr.loom.runtime.service._windows', lambda home, shell: None)
    monkeypatch.setattr('brr.loom.runtime.service._defaults', lambda: [('claude', ''), ('codex', '')])
    # Piped answers: codex, then switch brnrd cloud off and the own bot on (the page stays on); nothing asks a name.
    monkeypatch.setattr('sys.stdin', io.StringIO('2\n1 2\n'))
    assert setup_channel.main(['--home', str(tmp_path)]) == 0
    out = capsys.readouterr().out
    for asked in ('Which AI should brnrd think with?', 'How should brnrd reach you?'):
        assert asked in out
    # The name is the resident's first question, not a form field.
    for gone in ('Channel kind', 'Name this channel', 'Your person name', 'fake', 'call you', 'Your name'):
        assert gone not in out
    assert [a[1:] for a in calls] == [('telegram', 'telegram', 'sasha.petrova'), ('web', 'web', 'sasha.petrova')]
    assert (tmp_path / 'loom' / 'preferences.toml').read_text().startswith('model.default = "codex"')
    assert load_config(tmp_path).body is None


def test_a_door_connected_again_keeps_its_name_and_a_taken_name_is_numbered(tmp_path):
    home = home_at(tmp_path)
    (tmp_path / 'loom').mkdir(exist_ok=True)
    (tmp_path / 'loom/config.toml').write_text('connectors = ["telegram=telegram"]\n')
    assert setup_channel._channel_name(Home(tmp_path), 'telegram') == 'telegram'
    (tmp_path / 'loom/config.toml').write_text('connectors = ["telegram=fake"]\n')
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
    assert seen == [['--kind', 'fake', '--timeout', '180', '--wait', '15.0']]


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


def test_a_plain_start_pairs_nothing_and_the_chat_asks_for_the_code(tmp_path):
    home = home_at(tmp_path)
    state = Path(connector.environment(home, 'desk')['BRNRD_STATE'])
    drop = state / 'in'
    lines = []
    def press_start_then_send(line):
        lines.append(line)
        if code_in(line):
            (drop / 'start.md').write_text(_front(dict(kind='letter', id='start',
                                           to='42', **{'from': '42'}), '/start'))
        elif line.startswith('Start was pressed'):
            (drop / 'code.md').write_text(_front(dict(kind='letter', id='code',
                                          to='42', **{'from': '42'}), code_in(lines[0])))
    assert setup_channel.pair(home, 'fake', 'desk', 'ada', out=press_start_then_send) == 'desk:42'
    sent = [json.loads(r)['text'] for r in (tmp_path / 'loom/channel-fake.jsonl').read_text().splitlines()]
    assert sent[0] == 'Send me the code your terminal shows, and this chat is paired.'
    assert sent[1].startswith('Paired ✓')
    assert code_in(lines[0]) in lines[1] and not (drop / 'start.md').exists()


def test_a_shared_bot_is_given_its_own_code_and_no_qr(tmp_path):
    home = home_at(tmp_path)
    folder = tmp_path / 'loom/connectors/fake'
    shutil.copytree(Path(setup_channel.__file__).parent / 'connectors/fake', folder)
    (folder / 'door').write_text('#!/usr/bin/env python3\nimport json, sys\n'
                                 'print(json.dumps(dict(name="@brnrd_bot", link="https://t.me/brnrd_bot?start=PK-AB12",'
                                 ' code="PK-AB12", qr=False)))\n')
    (folder / 'door').chmod(0o755)
    lines = []
    with pytest.raises(TimeoutError):
        setup_channel.pair(home, 'fake', 'desk', 'ada', timeout=0, out=lines.append, qr=True)
    assert len(lines) == 1 and 'scan' not in lines[0]
    assert 'open @brnrd_bot yourself and send the code PK-AB12.' in lines[0]


def test_setup_run_again_shows_the_home_and_asks_nothing(tmp_path, monkeypatch, capsys):
    from brr.loom.runtime.config import seed_config
    seed_config(tmp_path)
    set_value(tmp_path, 'connectors', ['telegram=telegram'])
    def asked(*_a, **_kw):
        raise AssertionError('a set-up home was asked something')
    monkeypatch.setattr('builtins.input', asked)
    monkeypatch.setattr(setup_channel, 'pair', asked)
    monkeypatch.setattr(setup_channel, '_choose_body', asked)
    assert setup_channel.main(['--home', str(tmp_path)]) == 0
    shown = capsys.readouterr().out
    assert 'brnrd is set up here' in shown and 'talks over telegram' in shown
    assert 'brnrd setup channel' in shown


def test_the_code_is_typed_by_a_person_never_hex():
    for _ in range(200):
        code = setup_channel.pairing_code()
        assert re.fullmatch(r'[A-HJKMNP-Z2-9]{3}-[A-HJKMNP-Z2-9]{3}', code)
    assert setup_channel._says_code(' k7q m9x ', 'K7Q-M9X')
    assert setup_channel._says_code('/start K7Q-M9X', 'K7Q-M9X')
    assert not setup_channel._says_code('/start', 'K7Q-M9X')


def test_the_offers_start_on_and_enter_keeps_them(capsys):
    rows = [('repo', 'Back up', ''), ('app', 'App', ''), ('board', 'Board', '')]
    assert setup_channel.checklist(rows, keys=iter(['\r'])) == {'repo', 'app', 'board'}
    # Down once, space: the App goes off; up past the top wraps to the board.
    assert setup_channel.checklist(rows, keys=iter(['\x1b[B', ' ', '\r'])) == {'repo', 'board'}
    assert setup_channel.checklist(rows, keys=iter(['\x1b[A', ' ', '\n'])) == {'repo', 'app'}
    assert 'Enter confirms' in capsys.readouterr().out


def test_an_offer_that_cannot_be_had_shows_off_with_its_reason_and_cannot_be_switched_on(capsys):
    rows = [('repo', 'Back up', 'needs gh signed in'), ('app', 'App', '')]
    assert setup_channel.checklist(rows, keys=iter([' ', '\r'])) == {'app'}
    assert setup_channel.checklist(rows[:1], keys=iter([])) == set()
    out = capsys.readouterr().out
    assert 'needs gh signed in' in out and out.count('Enter confirms') == 1  # the lone dead row asks nothing


def test_piped_setup_keeps_the_offers_on_enter_and_numbers_leave_them_out(monkeypatch):
    import io
    rows = [('repo', 'Back up', ''), ('app', 'App', ''), ('board', 'Board', '')]
    monkeypatch.setattr('sys.stdin', io.StringIO('\n'))
    assert setup_channel.checklist(rows) == {'repo', 'app', 'board'}
    monkeypatch.setattr('sys.stdin', io.StringIO('1, 3\n'))
    assert setup_channel.checklist(rows) == {'app'}


def test_cloud_setup_offers_backup_and_app_and_brnrd_asks_what_was_left_off(tmp_path, monkeypatch):
    import io
    from brr.loom.runtime.ledger import read_facts
    from brr.loom.runtime.wishes import wishes
    paired, made = [], []
    monkeypatch.setattr(setup_channel, '_gh_login', lambda: ('ada', ''))
    monkeypatch.setattr(setup_channel, 'backup_repo', lambda home, login: setup_channel.SELF_REPO)
    monkeypatch.setattr(setup_channel, 'back_up_self', lambda home, login, repo: made.append(login) or ('u', True))
    monkeypatch.setattr(setup_channel, 'pair', lambda *a, **kw: paired.append(a[1]))
    # Piped: Enter for the body, then leave the App (2) out.
    monkeypatch.setattr('sys.stdin', io.StringIO('2\n'))
    argv = ['--home', str(tmp_path), '--kind', 'relay', '--name', 'cloud', '--person', 'ada']
    assert setup_channel.main(argv) == 0
    assert made == ['ada'] and paired == ['relay']
    lines = {w.line for w in wishes(read_facts(Home(tmp_path))).values()}
    assert setup_channel.LATER['app'] in lines
    assert setup_channel.LATER['repo'] not in lines and 'board' not in setup_channel.LATER


def test_an_own_bot_setup_offers_only_the_backup(tmp_path):
    from brr.loom.runtime.selfrepo import init_self
    home = home_at(tmp_path)
    init_self(tmp_path, person='ada')
    assert [k for k, _, _ in setup_channel.offers(home, 'telegram', 'ada')] == ['repo']
    assert [k for k, _, _ in setup_channel.offers(home, 'relay', 'ada')] == ['repo', 'app']
    subprocess.run(['git', '-C', str(tmp_path / 'self'), 'remote', 'add', 'origin', 'https://x/y.git'], check=True)
    assert setup_channel.offers(home, 'telegram', 'ada') == []


def test_the_backup_adopts_only_a_private_repo(tmp_path, monkeypatch):
    from brr import home_link
    from brr.loom.runtime.selfrepo import init_self
    home = home_at(tmp_path)
    init_self(tmp_path, person='ada')
    created = []
    monkeypatch.setattr(home_link, '_repo_view', lambda owner, name: {'visibility': 'PUBLIC'})
    with pytest.raises(ValueError, match='not private'):
        setup_channel.back_up_self(home, 'ada')
    monkeypatch.setattr(home_link, '_repo_view', lambda owner, name: None)
    monkeypatch.setattr(home_link, '_repo_create', lambda owner, name: created.append(f'{owner}/{name}'))
    url, _pushed = setup_channel.back_up_self(home, 'ada')
    assert created == ['ada/brnrd-self'] and url == 'https://github.com/ada/brnrd-self.git'
    got = subprocess.run(['git', '-C', str(tmp_path / 'self'), 'remote', 'get-url', 'origin'],
                         capture_output=True, text=True).stdout.strip()
    assert got == url


def _backed_up_self(tmp_path, monkeypatch):
    """An earlier install's self, pushed to a private ``ada/brnrd-self`` (a bare repo here)."""
    from brr import home_link
    from brr.loom.runtime.selfrepo import init_self
    init_self(tmp_path / 'before', person='ada')
    remote = tmp_path / 'remote' / 'brnrd-self.git'
    subprocess.run(['git', 'clone', '-q', '--bare', str(tmp_path / 'before/self'), str(remote)], check=True)
    monkeypatch.setattr(home_link, '_repo_view',
                        lambda owner, name: {'visibility': 'PRIVATE'} if name == 'brnrd-self' else None)
    monkeypatch.setattr(setup_channel, '_repo_url', lambda login, name: str(tmp_path / 'remote' / f'{name}.git'))
    return subprocess.run(['git', '-C', str(remote), 'rev-parse', 'main'],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_a_new_install_restores_the_memory_its_backup_holds(tmp_path, monkeypatch):
    """Item 7 (Sasha's reinstall): setup looks at the backup first and takes the memory back, never seeding over it."""
    (tmp_path / 'home').mkdir()
    home = home_at(tmp_path / 'home')
    sha = _backed_up_self(tmp_path, monkeypatch)
    assert setup_channel.backup_repo(home, 'ada', ask=lambda question, default: default) == 'brnrd-self'
    self_dir = tmp_path / 'home/self'
    assert subprocess.run(['git', '-C', str(self_dir), 'rev-parse', 'main'],
                          capture_output=True, text=True).stdout.strip() == sha
    assert (self_dir / '.git/hooks/pre-receive').is_file()
    # The restored self has its origin, so the backup offer is gone.
    assert setup_channel.offers(home, 'telegram', 'ada') == []


def test_a_declined_restore_backs_up_to_a_new_name_and_never_pushes_over_the_old(tmp_path, monkeypatch):
    from brr.loom.runtime.selfrepo import init_self
    (tmp_path / 'home').mkdir()
    home = home_at(tmp_path / 'home')
    _backed_up_self(tmp_path, monkeypatch)
    assert setup_channel.backup_repo(home, 'ada', ask=lambda question, default: 'no') == 'brnrd-self-2'
    assert not (tmp_path / 'home/self').exists()
    init_self(tmp_path / 'home', person='ada')
    with pytest.raises(ValueError, match='already holds a self'):
        setup_channel.back_up_self(home, 'ada', 'brnrd-self')


def test_a_refused_pairing_answer_says_what_the_person_does():
    """Item 6: Telegram's own reason is carried through, and the message says what to do."""
    told = setup_channel._what_to_do('Telegram refused sendMessage (HTTP 403: Forbidden: bot was blocked by the user)',
                                     '@ada_bot')
    assert 'Restart' in told
    assert '@ada_bot' in setup_channel._what_to_do('Telegram refused sendMessage (HTTP 403)', '@ada_bot')
    assert setup_channel._what_to_do('relay: event not found for this account', None) == ''


def test_telegram_errors_carry_telegrams_reason_and_never_the_token(tmp_path, monkeypatch):
    import importlib.util
    import io
    from urllib.error import HTTPError
    path = Path(setup_channel.__file__).parent / 'connectors/telegram/common.py'
    spec = importlib.util.spec_from_file_location('telegram_common_test', path)
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    (tmp_path / 'token').write_text('123:SECRET\n')
    monkeypatch.setenv('BRNRD_STATE', str(tmp_path))

    def refuse(code, text):
        def urlopen(request, timeout):
            raise HTTPError(request.full_url, code, 'x', {},
                            io.BytesIO(json.dumps({'ok': False, 'description': text}).encode()))
        return urlopen
    monkeypatch.setattr(common, 'urlopen', refuse(403, 'Forbidden: bot was blocked by the user'))
    with pytest.raises(common.Refused) as refused:
        common.call('sendMessage', {})
    assert 'blocked by the user' in str(refused.value) and 'SECRET' not in str(refused.value)
    monkeypatch.setattr(common, 'urlopen', refuse(409, 'Conflict: terminated by other getUpdates request'))
    with pytest.raises(RuntimeError, match='another program is reading this bot'):
        common.call('getUpdates', {})


def test_setup_refuses_zero_doors_and_connects_every_door_picked(tmp_path, monkeypatch, capsys):
    """Item 2: cloud and own bot are one list, not either-or, and brnrd needs at least one."""
    import io
    calls = []
    monkeypatch.setattr(setup_channel, 'pair', lambda home, kind, name, person, **kw: calls.append((kind, name)))
    monkeypatch.setattr(setup_channel, '_take_offers', lambda home, picked, rows, login, repo: [])
    # A first setup starts on brnrd cloud and the page. Switch both off (nothing left: refused), then from the
    # start again add the own bot: all three doors are offered and each one ticked is walked in turn.
    monkeypatch.setattr('sys.stdin', io.StringIO('1 3\n2\n\n'))
    assert setup_channel.main(['--home', str(tmp_path), '--person', 'ada']) == 0
    out = capsys.readouterr().out
    assert 'at least one way to reach you' in out
    assert all(label in out for label in setup_channel.CHOICES.values())
    assert calls == [('relay', 'relay'), ('telegram', 'telegram'), ('web', 'web')]


def test_the_web_door_needs_no_code_and_its_page_is_the_link_and_the_board_writes_through_it(tmp_path):
    """The page on this computer as a door: setup gives it a loopback port, the board's letters come from it."""
    from brr.loom.runtime import board
    from brr.loom.runtime.selfrepo import init_self
    home = home_at(tmp_path)
    init_self(tmp_path, person='ada')
    setup_channel._enroll(home, 'fake', 'desk', 'ada', ['desk:42'])
    links = []
    assert setup_channel.pair(home, 'web', 'web', 'ada', links=links) == 'web:local'
    port = load_config(tmp_path).board_port
    assert port and port.isdigit()
    assert links == [('In your browser', f'http://127.0.0.1:{port}')]
    assert setup_channel.door_states(home)['web'] == ('web', True, f'at http://127.0.0.1:{port}')
    # Run again: the port stays, so the page keeps its address.
    setup_channel.pair(home, 'web', 'web', 'ada')
    assert load_config(tmp_path).board_port == port
    # The page comes first though the chat was enrolled first: a letter typed there is answered there.
    assert board.person_of(home, None) == ('ada', 'web', 'local')
    board.Parts(home, lambda: load_config(tmp_path), None).send('hello')
    connector.drain(home, 'web', tmp_path / 'loom/connectors/web/state/in', {})
    from brr.loom.runtime.ledger import read_facts
    letter = next(f for f in read_facts(home) if f.kind == 'letter')
    assert letter.data['from'] == 'person:ada'


def test_the_web_door_goes_dark_when_its_port_is_unset_and_setup_mends_it(tmp_path):
    home = home_at(tmp_path)
    setup_channel.pair(home, 'web', 'web', 'ada')
    set_value(tmp_path, 'board_port', '')
    assert setup_channel.door_states(home)['web'][1] is False
    setup_channel.pair(home, 'web', 'web', 'ada')
    assert setup_channel.door_states(home)['web'][1] is True


def _walk(monkeypatch, tmp_path, answers, outcomes):
    """``_walk_doors`` with piped answers; ``outcomes[kind]`` is a list of what each try at that door does."""
    import io
    monkeypatch.setattr('sys.stdin', io.StringIO(answers))
    tried = []

    def connect(kind):
        tried.append(kind)
        what = outcomes[kind].pop(0)
        if isinstance(what, BaseException):
            raise what
        return what
    kinds = ['fake', 'relay', 'telegram', 'web']
    return tried, lambda doors: setup_channel._walk_doors(home_at(tmp_path), doors, kinds, connect)


def test_a_door_skipped_with_ctrl_c_goes_on_and_the_page_still_connects(tmp_path, monkeypatch, capsys):
    """9989: every door step can be skipped; with nothing else connected, the page is the door."""
    tried, walk = _walk(monkeypatch, tmp_path, '\n', {'relay': [KeyboardInterrupt()], 'web': ['web:local']})
    assert walk(['web', 'relay']) == (['web:local'], ['relay'])
    assert tried == ['relay', 'web']  # the page last: it needs nothing, the others may
    out = capsys.readouterr().out
    assert 'Ctrl-C skips this one' in out and 'on the page for now; add more: brnrd setup channel' in out


def test_back_goes_to_the_list_and_q_stops(tmp_path, monkeypatch):
    # b: the list again (piped: switch the own bot on), and the walk goes on from the new picks, relay again included.
    tried, walk = _walk(monkeypatch, tmp_path, 'b\n2\n', {'relay': [KeyboardInterrupt(), 'relay:1'],
                                                         'telegram': ['telegram:1'], 'web': ['web:local']})
    assert walk(['relay', 'web']) == (['relay:1', 'telegram:1', 'web:local'], [])
    assert tried == ['relay', 'relay', 'telegram', 'web']
    (tmp_path / 'q').mkdir()
    tried, walk = _walk(monkeypatch, tmp_path / 'q', 'q\n', {'relay': [KeyboardInterrupt()]})
    with pytest.raises(KeyboardInterrupt):
        walk(['relay'])


def test_with_the_page_switched_off_a_dead_end_brings_the_list_back_once(tmp_path, monkeypatch, capsys):
    # Relay fails; the list comes back once (piped: Enter keeps its picks, relay again), it fails again: setup stops.
    tried, walk = _walk(monkeypatch, tmp_path, '3\n', {'relay': [ValueError('no'), ValueError('no')]})
    with pytest.raises(ValueError, match='No door was connected'):
        walk(['relay'])
    assert capsys.readouterr().out.count('No door connected yet; pick again') == 1
    assert tried == ['relay', 'relay']
