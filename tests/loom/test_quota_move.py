"""Permission and crash replay for cross-subscription quota continuation."""
import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import adapters, usage
from brr.loom.runtime.config import ConfigError
from brr.loom.runtime.home import Home
from brr.loom.runtime.loom import _wake_choice


@pytest.mark.parametrize("override, expected", [
    ('', ('codex', '')),
    ('quota.inbox = []\n', None),
    ('quota.inbox = []\nquota.inbox-seat = ["codex/selected"]\n', ('codex', 'selected')),
])
def test_role_permission_overrides_default(tmp_path, monkeypatch, override, expected):
    home = Home(tmp_path)
    monkeypatch.setattr(Home, 'main_thread', lambda self: 'inbox-seat')
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text('quota.default = ["codex"]\n' + override)
    monkeypatch.setattr(adapters, 'installed_shells', lambda home: ['claude', 'codex'])
    monkeypatch.setattr(usage, 'reading', lambda *args: {'windows': [{'left_pct': 75}]})
    assert adapters.quota_choice(home, 'inbox-seat', 'claude') == expected


@pytest.mark.parametrize('gauge', [None, {'windows': []}, {'windows': [{'left_pct': 0}]},
    {'error': 'probe failed', 'windows': [{'left_pct': 90}]},
    {'windows': [{'left_pct': 90}, {'left_pct': 0}]}])
def test_unknown_failed_or_dry_target_is_not_permission_to_move(tmp_path, monkeypatch, gauge):
    (tmp_path / 'loom').mkdir()
    (tmp_path / 'loom/preferences.toml').write_text('quota.default = ["codex"]\n')
    monkeypatch.setattr(adapters, 'installed_shells', lambda home: ['claude', 'codex'])
    monkeypatch.setattr(usage, 'reading', lambda *args: gauge)
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
