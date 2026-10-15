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


def test_named_relay_refused_at_config_load(tmp_path):
    home = home_at(tmp_path, 'connectors = ["home=relay"]')
    with pytest.raises(ConfigError, match='multi-platform folder.*connectors'):
        load_config(home.root)
