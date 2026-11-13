"""Permission and crash replay for cross-subscription quota continuation."""
import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import adapters, usage
from brr.loom.runtime.config import ConfigError
from brr.loom.runtime.home import Home
from brr.loom.runtime.loom import _wake_choice


@pytest.mark.parametrize("override, expected", [
    ('', None),
    ('quota.inbox = ["codex"]\n', ('codex', '')),
    ('quota.inbox = []\n', None),
    ('quota.inbox = []\nquota.inbox-seat = ["codex/selected"]\n', ('codex', 'selected')),
])
def test_role_permission_overrides_default(tmp_path, monkeypatch, override, expected):
    home = Home(tmp_path)
    monkeypatch.setattr(Home, 'main_thread', lambda self: 'inbox-seat')
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text('quota.default = ["codex"]\n' + override)
    monkeypatch.setattr(adapters, 'installed_shells', lambda home: ['claude', 'codex'])
    monkeypatch.setattr(usage, 'reading', lambda *args, **kwargs: {'windows': [{'left_pct': 75}]})
    assert adapters.quota_choice(home, 'inbox-seat', 'claude') == expected


@pytest.mark.parametrize('gauge', [None, {'windows': []}, {'windows': [{'left_pct': 0}]},
    {'error': 'probe failed', 'windows': [{'left_pct': 90}]},
    {'windows': [{'left_pct': 90}, {'left_pct': 0}]}])
def test_unknown_failed_or_dry_target_is_not_permission_to_move(tmp_path, monkeypatch, gauge):
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text('quota.default = ["codex"]\n')
    monkeypatch.setattr(adapters, 'installed_shells', lambda home: ['claude', 'codex'])
    monkeypatch.setattr(usage, 'reading', lambda *args, **kwargs: gauge)
    assert adapters.quota_choice(Home(tmp_path), 'work', 'claude') is None


def test_birth_order_and_spend_are_not_move_permission(tmp_path, monkeypatch):
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text(
        'bodies.order = ["claude", "codex"]\nspend.burn = ["codex"]\nspend.keep = "claude"\n')
    monkeypatch.setattr(adapters, 'installed_shells', lambda home: ['claude', 'codex'])
    assert adapters.quota_choice(Home(tmp_path), 'work', 'claude') is None


@pytest.mark.parametrize('value', ['"codex"', '[1]', '["codex/"]'])
def test_invalid_move_permission_is_refused(tmp_path, value):
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text(f'quota.default = {value}\n')
    with pytest.raises(ConfigError):
        adapters.quota_choice(Home(tmp_path), 'work', 'claude')


def test_durable_move_routes_after_crash_and_manual_wake_wins():
    lease = Fact(kind='lease', by='loom:test', data={'thread': 'work'})
    move = Fact(kind='body.moved', by='loom:test', data={'thread': 'work', 'to_shell': 'codex', 'to_core': ''})
    wake = Fact(kind='letter.disposed', by='loom:test', data={
        'dispositions': [{'thread': 'work', 'body': 'claude/opus'}]})
    assert _wake_choice([lease, move], 'work') == ('codex', '')
    assert _wake_choice([lease, wake, move], 'work') == ('claude', 'opus')
    assert _wake_choice([lease, move, lease], 'work') is None


@pytest.mark.parametrize('permission, expected', [
    ('', None), ('quota.work = []\n', None),
    ('quota.work = ["codex"]\n', ('codex', '')),
])
def test_pinned_seat_needs_its_own_permission(tmp_path, monkeypatch, permission, expected):
    home = Home(tmp_path)
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text('quota.default = ["codex"]\n' + permission)
    seat = home.thread_dir('work') / 'seat'
    seat.parent.mkdir(parents=True)
    seat.write_text('body: claude/opus\n')
    monkeypatch.setattr(adapters, 'installed_shells', lambda home: ['claude', 'codex'])
    monkeypatch.setattr(usage, 'reading', lambda *args, **kwargs: {'windows': [{'left_pct': 75}]})
    assert adapters.quota_choice(home, 'work', 'claude') == expected


@pytest.mark.parametrize('order, expected', [
    ('["claude"]', None), ('["claude", "codex"]', ('codex', '')),
])
def test_installed_target_must_also_be_enabled(tmp_path, monkeypatch, order, expected):
    import shutil
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text(
        f'bodies.order = {order}\nquota.default = ["codex"]\n')
    monkeypatch.setattr(shutil, 'which', lambda shell: f'/fake/{shell}')
    monkeypatch.setattr(usage, 'reading', lambda *args, **kwargs: {'windows': [{'left_pct': 75}]})
    assert adapters.quota_choice(Home(tmp_path), 'work', 'claude') == expected


def test_preferences_cache_observes_edits_and_removal(tmp_path, monkeypatch):
    from brr.loom.runtime import config
    home = Home(tmp_path)
    path = tmp_path / 'loom/preferences.toml'
    path.parent.mkdir()
    path.write_text('quota.default = ["codex"]\n')
    real_read = config.read_values
    reads = []
    def read(path):
        reads.append(path)
        return real_read(path)
    monkeypatch.setattr(config, 'read_values', read)
    for _ in range(5):
        assert adapters._preferences(home)[1]['quota.default'] == ['codex']
    assert reads == [path]
    path.write_text('quota.default = []\n')
    assert adapters._preferences(home)[1]['quota.default'] == []
    path.unlink()
    assert adapters._preferences(home)[1] == {}
    assert reads == [path, path, path]


def test_target_choice_never_starts_a_collector(tmp_path, monkeypatch):
    import json
    import time
    home = Home(tmp_path)
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text('quota.default = ["codex"]\n')
    monkeypatch.setattr(adapters, 'installed_shells', lambda home: ['claude', 'codex'])
    def unexpected(*args, **kwargs):
        pytest.fail('quota target inspection must not launch a collector')
    monkeypatch.setattr(usage.threading, 'Thread', unexpected)
    path = tmp_path / 'loom/usage/codex.json'
    path.parent.mkdir()
    assert adapters.quota_choice(home, 'work', 'claude') is None
    value = {'at': time.time(), 'windows': [{'name': 'session', 'left_pct': 75}]}
    path.write_text(json.dumps(value))
    assert adapters.quota_choice(home, 'work', 'claude') == ('codex', '')
    value['at'] -= usage.CACHE_TTL + 1
    path.write_text(json.dumps(value))
    assert adapters.quota_choice(home, 'work', 'claude') is None
    assert path.resolve() not in usage._REFRESHING


def test_source_shell_is_skipped_even_with_another_core(tmp_path, monkeypatch):
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text(
        'quota.default = ["claude/sonnet", "codex"]\n')
    monkeypatch.setattr(adapters, 'installed_shells', lambda home: ['claude', 'codex'])
    monkeypatch.setattr(usage, 'reading', lambda *args, **kwargs: {'windows': [{'left_pct': 75}]})
    assert adapters.quota_choice(Home(tmp_path), 'work', 'claude') == ('codex', '')
