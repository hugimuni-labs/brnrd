"""A folder, including a passive fake, pairs through the one letter API."""
import json
import re
import shutil
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
    assert sent[0]['text'] == 'heard you as 42'
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


def test_relay_setup_explains_existing_account_pairing(tmp_path, capfd):
    home = home_at(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        setup_channel.pair(home, 'relay', 'cloud', 'ada', out=lambda _: None)
    assert 'brnrd account connect' in capfd.readouterr().err
    assert load_config(tmp_path).connectors == ()


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
