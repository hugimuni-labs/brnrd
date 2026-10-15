"""Named instances separate addresses, senders, dedupe and state."""
from pathlib import Path

import pytest

from brr.loom.runtime import connector
from brr.loom.runtime.config import ConfigError, load_config
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.port import _front


def home_at(tmp_path, config):
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/config.toml').write_text(config)
    return Home(tmp_path, 'aaaa')


def test_two_instances_of_one_folder_are_distinct(tmp_path):
    home = home_at(tmp_path, 'connectors = ["one=fake", "two=fake"]')
    assert set(connector.carries(home)) == {'one', 'two'}
    for name in ('one', 'two'):
        env = connector.environment(home, name)
        assert env['BRNRD_CHANNEL'] == name
        assert Path(env['BRNRD_STATE']) == tmp_path / f'loom/connectors/{name}/state'
        drop = Path(env['BRNRD_STATE']) / 'in'
        drop.mkdir()
        (drop / '1.md').write_text(_front(dict(kind='letter', id='1', to=f'channel:{name}/42',
                                               **{'from': f'{name}:42'}), 'hello'))
        connector.drain(home, name, drop)
    sources = [f for f in read_facts(home) if f.kind == 'source']
    assert {f.id for f in sources} == {'source:one:1', 'source:two:1'}
    assert {f.data['from'] for f in sources} == {'stranger:one:42', 'stranger:two:42'}


@pytest.mark.parametrize('config', [
    'connectors = ["a=fake", "a=telegram"]',
    'connectors = ["a", "a=fake"]',
    'connectors = ["../a=fake"]', 'connectors = ["a=../fake"]',
    'connectors = ["a=fake"]\nconnectors = ["a=telegram"]',
])
def test_duplicate_or_unsafe_instance_names_refused(tmp_path, config):
    home = home_at(tmp_path, config)
    with pytest.raises(ConfigError):
        load_config(home.root)


@pytest.mark.parametrize('name', ['cloud', 'telegram'])
def test_named_relay_translates_platform_only_inside_folder(tmp_path, monkeypatch, name):
    import connector_support
    from test_loom_relay import FakeClient
    from test_loom_speak_relay import FakeClient as Sender

    home = home_at(tmp_path, f'connectors = ["{name}=relay"]')
    env = connector.environment(home, name)
    state = Path(env['BRNRD_STATE'])
    drop = state / 'in'
    drop.mkdir()
    monkeypatch.setenv('BRNRD_CHANNEL', name)
    event = dict(event_id='ev1', body='hi', reply_to=dict(
        platform='telegram', chat_id=42, user_id=42, message_id=7))
    connector_support.input_module.pull_once(drop, state, FakeClient([event]), 0)
    from brr.loom.runtime.port import parse_frontmatter
    fm = parse_frontmatter((drop / 'telegram:42:7.md').read_text())
    assert fm['to'] == f'channel:{name}/42' and fm['from'] == f'{name}:42'
    path = tmp_path / 'reply.md'
    path.write_text(_front(dict(kind='letter', id='draft', to=f'channel:{name}/42',
                               re='telegram:42:7'), 'answer'))
    client = Sender()
    assert connector_support.output_module.deliver(path, state, client) == 0
    assert client.sent == [dict(event_id='ev1', body_markdown='answer')]
