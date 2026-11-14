"""Two install ledgers, local secrets, and the actual migration boundary."""
import json
import shutil

import pytest

from brr.loom.runtime import connector_ownership as ownership
from brr.loom.runtime.config import set_value
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.verbs_machines import _machines


def machine(root, install, connectors=('relay', 'telegram', 'web')):
    home = Home(root, install)
    (root / 'loom').mkdir(parents=True)
    (root / 'loom/config.toml').write_text(
        'lease_remote = "https://example.test/self"\nconnectors = ' + json.dumps(list(connectors)) + '\n')
    for entry in connectors:
        name, _, kind = entry.partition('=')
        if (kind or name) == 'telegram':
            state = root / 'loom/connectors' / name / 'state'
            state.mkdir(parents=True)
            (state / 'token').write_text('123456:private_bot_secret\n')
    return home


def exchange(a, b):
    source = a.root / 'ledger/facts' / a.install_id()
    target = b.root / 'ledger/facts' / a.install_id()
    shutil.copytree(source, target, dirs_exist_ok=True)


def test_migrates_bundled_entries_without_secrets_and_is_idempotent(tmp_path):
    home = machine(tmp_path, 'aaaa')
    ownership.migrate(home)
    ownership.migrate(home)
    rows = ownership.claims(home)
    assert len(rows) == 3
    assert {r['name']: (r['scope'], r['owner']) for r in rows} == {
        'relay': ('global', None), 'telegram': ('machine', 'aaaa'), 'web': ('machine', 'aaaa')}
    assert next(r for r in rows if r['name'] == 'telegram')['identity'] == 'telegram:123456'
    stored = '\n'.join(p.read_text() for p in (tmp_path / 'ledger/facts').rglob('*.jsonl'))
    assert 'private_bot_secret' not in stored
    assert '/token' not in stored
    assert all(ownership.problem(home, name) is None for name in ('relay', 'telegram', 'web'))
    assert all(f.kind == 'connector.declared' for f in read_facts(home))


@pytest.mark.parametrize('alias', ['telegram', 'other=telegram'])
def test_copied_bot_cannot_migrate_even_under_another_name(tmp_path, alias):
    owner = machine(tmp_path / 'owner', 'aaaa', ('relay', 'telegram'))
    peer = machine(tmp_path / 'peer', 'bbbb', ('relay', alias))
    ownership.migrate(owner)
    exchange(owner, peer)
    with pytest.raises(ownership.OwnershipError, match='conflicting|copied credentials'):
        ownership.migrate(peer)
    assert ownership.problem(peer, alias.split('=')[0])
    assert ownership.problem(peer, 'relay') is None
    assert not any(r['owner'] == 'bbbb' for r in ownership.claims(peer))
    assert ownership.problem(owner, 'telegram') is None


def test_concurrent_nominations_fail_closed_after_exchange(tmp_path):
    a = machine(tmp_path / 'a', 'aaaa', ('relay', 'telegram'))
    b = machine(tmp_path / 'b', 'bbbb', ('relay', 'telegram'))
    ownership.migrate(a)
    ownership.migrate(b)
    exchange(a, b)
    exchange(b, a)
    assert len(ownership.claims(a)) == len(ownership.claims(b)) == 4
    assert ownership.problem(a, 'telegram')
    assert ownership.problem(b, 'telegram')
    assert ownership.problem(a, 'relay') is None
    assert ownership.problem(b, 'relay') is None


def test_custom_scope_required_only_when_shared_and_missing_claim_has_repair(tmp_path):
    home = machine(tmp_path, 'aaaa', ('fake',))
    with pytest.raises(ownership.OwnershipError, match='/scope containing'):
        ownership.migrate(home)
    folder = tmp_path / 'loom/connectors/fake'
    folder.mkdir(parents=True)
    (folder / 'carries').write_text('')
    (folder / 'scope').write_text('machine')
    assert 'machines connectors migrate' in ownership.problem(home, 'fake')
    ownership.migrate(home)
    assert ownership.problem(home, 'fake') is None
    set_value(tmp_path, 'lease_remote', '')
    set_value(tmp_path, 'connectors', ['unknown'])
    ownership.migrate(home)
    assert ownership.problem(home, 'unknown') is None


def test_cli_migration_and_status_read_back_declarations(tmp_path, capsys):
    machine(tmp_path, 'aaaa', ('relay',))
    args = ['--home', str(tmp_path), '--install', 'aaaa', 'connectors']
    assert _machines(args + ['migrate']) == 0
    migrated = json.loads(capsys.readouterr().out)
    assert _machines(args + ['status']) == 0
    assert json.loads(capsys.readouterr().out) == migrated
    assert migrated == [dict(name='relay', kind='relay', scope='global', owner=None)]


def test_loop_ticks_reuse_identity_and_index_declarations(tmp_path, monkeypatch):
    from brr.loom.runtime.ledger import LedgerView, shared_reader
    home = machine(tmp_path, 'aaaa', ('telegram', 'relay'))
    # Unrelated history must never be iterated by connector checks.
    class IndexedOnly(LedgerView):
        def __iter__(self):
            raise AssertionError('full declaration scan')
    reader = shared_reader(home)
    reader.ordered = IndexedOnly()
    calls = []
    run = ownership.subprocess.run
    def identity(*args, **kwargs):
        calls.append(args)
        return run(*args, **kwargs)
    monkeypatch.setattr(ownership.subprocess, 'run', identity)
    # Fixed monotonic time proves 100 loop ticks within the slow pace.
    monkeypatch.setattr(ownership.time, 'monotonic', lambda: 100.0)
    migrations = []
    migrate = ownership.migrate
    def observed(home):
        migrations.append(1)
        return migrate(home)
    monkeypatch.setattr(ownership, 'migrate', observed)
    for _ in range(100):
        ownership.migrate_if_due(home)
        assert ownership.problem(home, 'telegram') is None
        assert ownership.problem(home, 'relay') is None
    assert len(calls) == len(migrations) == 1
    token = tmp_path / 'loom/connectors/telegram/state/token'
    token.write_text('654321:new_secret')
    assert ownership.problem(home, 'telegram')
    assert len(calls) == 2


def test_slow_migration_and_adapter_changes_refresh(tmp_path, monkeypatch):
    home = machine(tmp_path, 'aaaa', ('relay',))
    clock = [100.0]
    monkeypatch.setattr(ownership.time, 'monotonic', lambda: clock[0])
    calls = []
    migrate = ownership.migrate
    def observed(home):
        calls.append(1)
        return migrate(home)
    monkeypatch.setattr(ownership, 'migrate', observed)
    ownership.migrate_if_due(home)
    ownership.migrate_if_due(home)
    assert len(calls) == 1
    clock[0] += 60
    ownership.migrate_if_due(home)
    assert len(calls) == 2
    folder = tmp_path / 'loom/connectors/relay'
    folder.mkdir(parents=True)
    (folder / 'carries').write_text('')
    (folder / 'scope').write_text('machine')
    with pytest.raises(ownership.OwnershipError, match='conflicting'):
        ownership.migrate_if_due(home)


def test_process_loop_paces_migration_with_telegram_and_relay(tmp_path, monkeypatch):
    import threading
    from brr.loom.runtime import loom, router, usage
    home = machine(tmp_path, 'aaaa', ('telegram', 'relay'))
    stop = threading.Event()
    calls, ticks = [], []
    run = ownership.subprocess.run
    def identity(*args, **kwargs):
        if args[0][0].endswith('/identity'):
            calls.append(1)
        return run(*args, **kwargs)
    monkeypatch.setattr(ownership.subprocess, 'run', identity)
    monkeypatch.setattr(usage, 'shells', lambda home: ())
    monkeypatch.setattr(router.Router, 'armed', lambda self: False)
    monkeypatch.setattr(loom, '_pause', lambda *args: False)
    monkeypatch.setattr(loom, '_converge_forge_token', lambda home: None)
    monkeypatch.setattr(ownership.time, 'monotonic', lambda: 100.0)
    scans = []
    claims = ownership.claims
    def observed(home):
        scans.append(1)
        return claims(home)
    monkeypatch.setattr(ownership, 'claims', observed)
    def tick(home, *args, **kwargs):
        ticks.append(1)
        assert ownership.problem(home, 'telegram') is None
        if len(ticks) == 100:
            stop.set()
    monkeypatch.setattr(loom, 'tick_once', tick)
    loom.run(tmp_path, install='aaaa', stop=stop, tick=.2)
    assert len(ticks) == 100
    assert len(calls) == len(scans) == 1
