"""A folder, including a passive fake, pairs through the one letter API."""
import json
import subprocess
from pathlib import Path

import pytest

from brr.loom.runtime import connector, setup_channel
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import Home
from brr.loom.runtime.port import _front


def home_at(tmp_path):
    (tmp_path / 'loom').mkdir()
    return Home(tmp_path, 'aaaa')


def test_fake_live_pair_saves_named_identity_and_keeps_unrelated_letters(tmp_path):
    home = home_at(tmp_path)
    state = Path(connector.environment(home, 'desk')['BRNRD_STATE'])
    def send_code(line):
        if not line.startswith('Send '):
            return
        code = line.split()[1]
        drop = state / 'in'
        (drop / 'other.md').write_text(_front(dict(kind='letter', id='other',
                                      to='channel:desk/42', **{'from': 'desk:42'}), 'another letter'))
        (drop / 'code.md').write_text(_front(dict(kind='letter', id='code',
                                      to='channel:desk/42', **{'from': 'desk:42'}), code))
    assert setup_channel.pair(home, 'fake', 'desk', 'ada', out=send_code) == 'desk:42'
    assert load_config(tmp_path).connectors == ('desk=fake',)
    assert (tmp_path / 'self/people/ada/channels.md').read_text() == 'desk:42\n'
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
    assert not (tmp_path / 'self/people/ada/channels.md').exists()


def test_group_or_spoofed_identity_never_pairs(tmp_path):
    home = home_at(tmp_path)
    state = Path(connector.environment(home, 'desk')['BRNRD_STATE'])
    def send_code(line):
        if line.startswith('Send '):
            (state / 'in/code.md').write_text(_front(dict(kind='letter', id='code',
                        to='channel:desk/-42', **{'from': 'desk:42'}), line.split()[1]))
    with pytest.raises(TimeoutError):
        setup_channel.pair(home, 'fake', 'desk', 'ada', timeout=.1, out=send_code)
    assert load_config(tmp_path).connectors == ()


def test_cli_setup_and_setup_channel_share_flow(tmp_path, monkeypatch):
    from brr.cli import main
    calls = []
    monkeypatch.setattr(setup_channel, 'pair', lambda *a, **kw: calls.append((a, kw)))
    for spelling in (['setup'], ['setup', 'channel']):
        assert main([*spelling, '--home', str(tmp_path), '--kind', 'fake',
                     '--name', 'desk', '--person', 'ada']) == 0
    assert len(calls) == 2
    assert all(args[1:] == ('fake', 'desk', 'ada') for args, _ in calls)


def test_relay_missing_browser_pairing_has_actionable_stub(tmp_path, capfd):
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
        if line.startswith('Send '):
            drop = Path(connector.environment(home, 'desk')['BRNRD_STATE']) / 'in'
            (drop / 'code.md').write_text(_front(dict(kind='letter', id='code',
                        to='channel:desk/42', **{'from': 'desk:42'}), line.split()[1]))
    with pytest.raises(ValueError, match='ids receipt'):
        setup_channel.pair(home, 'fake', 'desk', 'ada', out=send_code)
    assert load_config(tmp_path).connectors == ()
    assert not (tmp_path / 'self/people/ada/channels.md').exists()
