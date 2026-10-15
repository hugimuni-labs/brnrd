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
        assert Path(env['BRNRD_STATE']) == tmp_path / f'loom/connectors/{name}/state'
        drop = Path(env['BRNRD_STATE']) / 'in'
        drop.mkdir()
        (drop / '1.md').write_text(_front(dict(kind='letter', id='1', to='42', **{'from': '42'}), 'hello'))
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


def test_named_relay_is_an_instance_like_any_folder(tmp_path):
    home = home_at(tmp_path, 'connectors = ["home=relay"]')
    assert connector.instances(home) == {'home': 'relay'}
    assert connector.carries(home)['home'][0] == 'home'


def test_disabled_instance_is_refused_at_write_and_drafting_thread_hears(tmp_path):
    import time
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.ledger import append
    from brr.loom.runtime.port import PortError, write_send

    home = home_at(tmp_path, 'connectors = ["relay"]')
    home.thread_dir('work').mkdir(parents=True)
    (home.thread_dir('work') / 'README.md').write_text('# Work\n')
    append(home, Fact(kind='router', by='loom:aaaa', id='router:1',
                     data=dict(gen=1, until=time.time() + 120, install='aaaa')))
    append(home, Fact(kind='lease', by='loom:aaaa', id='lease:work:1',
                     data=dict(thread='work', strand='s-a', gen=1, router_gen=1, install='aaaa')))
    room = home.room('s-a')
    reason = 'not sent: no channel "telegram"; enabled: relay'
    with pytest.raises(PortError, match='not sent: no channel "telegram"; enabled: relay'):
        write_send(room, to='channel:telegram/123', sender='s-a', body='hello')
    assert not list((room / 'port/out').glob('*.md'))
    notice, = [f for f in read_facts(home) if f.kind == 'letter']
    assert notice.data['to'] == 'thread:work'
    assert reason in notice.data['body']


def test_person_channel_toml_uses_exact_ids_and_shared_comment_rules(tmp_path):
    home = home_at(tmp_path, 'connectors = ["home=fake"]')
    person = tmp_path / 'self/people/ada'
    person.mkdir(parents=True)
    (person / 'channels.md').write_text('home:99\n')  # Old prose is not an authority source.
    (person / 'channels.toml').write_text('channels = ["home:42", "home:ab#cd"] # comment\n')
    assert connector.person_dm(home, 'channel:home/42') == 'person:ada'
    assert connector.person_dm(home, 'channel:home/ab#cd') == 'person:ada'
    assert connector.person_dm(home, 'channel:home/4') is None
    assert connector.person_dm(home, 'channel:home/99') is None


@pytest.mark.parametrize('text', ['channels = "home:42"', 'channels = [42]',
                                 'channels = ["home:42"]\nchannels = ["home:99"]'])
def test_person_channel_toml_refuses_invalid_arrays(tmp_path, text):
    home = home_at(tmp_path, 'connectors = ["home=fake"]')
    person = tmp_path / 'self/people/ada'
    person.mkdir(parents=True)
    (person / 'channels.toml').write_text(text)
    with pytest.raises(ConfigError):
        connector.person_dm(home, 'channel:home/42')
