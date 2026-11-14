"""Exercise the caller boundaries with real Git/local fences and fake wires."""
from pathlib import Path
import subprocess

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import connector, connector_ownership as ownership
from brr.loom.runtime.config import set_value
from brr.loom.runtime.connector_presence import Presence
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.port import _front
from brr.loom.runtime.router import Router
from brr.loom.runtime.speak import speak
from test_connector_ownership import machine, exchange


@pytest.fixture
def pair(tmp_path):
    remote = tmp_path / 'self.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    a = machine(tmp_path / 'a', 'aaaa', ('relay', 'telegram'))
    b = machine(tmp_path / 'b', 'bbbb', ('relay', 'copied=telegram'))
    for home in (a, b):
        set_value(home.root, 'lease_remote', str(remote))
        thread = home.thread_dir('inbox')
        thread.mkdir(parents=True)
        (thread / 'README.md').write_text('---\nid: inbox\nstatus: open\n---\n# Inbox\n')
    ownership.migrate(a)
    exchange(a, b)
    with pytest.raises(ownership.OwnershipError):
        ownership.migrate(b)
    return a, b


@pytest.fixture
def wires(monkeypatch):
    starts, effects, stops = [], [], []
    run, popen = subprocess.run, subprocess.Popen
    class Child:
        def poll(self):
            return None
        def terminate(self):
            stops.append(self)
        def wait(self, timeout=None):
            return 0
    def spawn(args, **kw):
        if Path(args[0]).name == 'in':
            starts.append(kw['env']['BRNRD_STATE'])
            return Child()
        return popen(args, **kw)
    def effect(args, **kw):
        if Path(args[0]).name in {'out', 'presence'}:
            effects.append((Path(args[0]).parent.name, Path(args[0]).name, kw.get('input')))
            return subprocess.CompletedProcess(args, 0, '{"ids": ["receipt"]}', '')
        return run(args, **kw)
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    monkeypatch.setattr(subprocess, 'run', effect)
    return starts, effects, stops


def drop(home, name, ident='one'):
    path = home.root / 'loom/connectors' / name / 'state/in' / f'{ident}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_front(dict(kind='letter', id=ident, to='42', **{'from': '42'}), 'hello'))
    return path


def pending_presence(home, name, authorize):
    worker = Presence(home, authorize=authorize)
    row = dict(channel=f'channel:{name}/42', chat='42', native='one')
    worker.snapshot = {'one': row}, {'one': row}
    return worker


def test_owner_holder_receives_sends_and_loss_fences_all_boundaries(pair, wires):
    a, b = pair
    starts, effects, stops = wires
    router = Router(a)
    router.begin_tick(read_facts(a))
    assert router.armed()
    inputs = connector.Inputs(a, authorize=router.armed)
    direct, global_ = drop(a, 'telegram'), drop(a, 'relay')
    inputs.step()
    assert not direct.exists() and not global_.exists()
    assert len([f for f in read_facts(a) if f.kind == 'letter']) == 2
    assert len(starts) == 2
    assert speak(a, router.lease, 'direct-send', 'yes', channel='channel:telegram/42',
                 authorize=router.armed)[0] == 'sent'
    worker = pending_presence(a, 'telegram', router.armed)
    worker.flush()
    assert [e[1] for e in effects] == ['out', 'presence']
    retained = drop(a, 'telegram', 'after-loss')
    # Invalidate the local fence after taking both input and presence snapshots.
    assert router.authority.release(router.lease)
    before = len(effects)
    inputs.step()
    worker.snapshot = {'two': dict(channel='channel:telegram/42')}, {}
    worker.flush()  # also suppresses queued working-off
    assert retained.exists() and len(stops) == 2
    assert speak(a, router.lease, 'lost-send', 'no', channel='channel:telegram/42',
                 authorize=router.armed)[0] == 'unavailable'
    assert len(effects) == before
    assert not any(f.id == 'letter:telegram:after-loss' for f in read_facts(a))
    inputs.close()


def test_lease_holder_cannot_use_copied_direct_bot_but_relay_still_works(pair, wires):
    a, b = pair
    starts, effects, _ = wires
    router = Router(b)
    router.begin_tick(read_facts(b))
    assert router.armed()
    inputs = connector.Inputs(b, authorize=router.armed)
    copied, global_ = drop(b, 'copied'), drop(b, 'relay')
    attachments = copied.with_suffix('.d')
    attachments.mkdir()
    (attachments / '0').write_bytes(b'keep')
    inputs.step()
    assert copied.exists() and not global_.exists()
    assert (attachments / '0').read_bytes() == b'keep'
    assert len(starts) == 1 and '/relay/' in starts[0]
    assert speak(b, router.lease, 'copied-send', 'no', channel='channel:copied/42',
                 authorize=router.armed)[0] == 'unavailable'
    pending_presence(b, 'copied', router.armed).flush()
    assert effects == []
    assert speak(b, router.lease, 'relay-send', 'yes', channel='channel:relay/42',
                 authorize=router.armed)[0] == 'sent'
    assert effects[0][:2] == ('relay', 'out')
    # A local lease alone and no live shared predicate cannot authorize speech.
    assert speak(b, router.lease, 'no-fence', 'no', channel='channel:relay/42')[0] == 'unavailable'
    inputs.close()


def test_follower_owner_has_no_intake_or_presence_or_output(pair, wires):
    a, b = pair
    leader = Router(b)
    leader.begin_tick(read_facts(b))
    follower = Router(a)
    follower.begin_tick(read_facts(a))
    assert leader.armed() and not follower.armed()
    retained = drop(a, 'telegram')
    connector.Inputs(a, authorize=follower.armed).step()
    pending_presence(a, 'telegram', follower.armed).flush()
    assert wires[0] == wires[1] == [] and retained.exists()
    assert ownership.allowed(a, 'telegram', follower.armed) is False


def test_missing_declaration_fails_closed_on_each_caller(tmp_path, wires):
    home = machine(tmp_path, 'aaaa', ('relay',))
    retained = drop(home, 'relay')
    connector.Inputs(home, authorize=lambda: True).step()
    pending_presence(home, 'relay', lambda: True).flush()
    from brr.daemon2.leases import LocalLeaseAuthority
    lease = LocalLeaseAuthority(home.root / 'ledger/leases').acquire('router', 'aaaa', 120)
    assert speak(home, lease, 'missing', 'no', channel='channel:relay/42',
                 authorize=lambda: True)[0] == 'unavailable'
    with pytest.raises(ownership.Unavailable):
        connector.ingest(home, 'relay', dict(id='one', to='channel:relay/42'), [], lambda: True)
    assert retained.exists() and wires[0] == wires[1] == []


def test_admission_losing_fence_after_read_retains_drop_without_poison(pair, monkeypatch):
    a, _ = pair
    router = Router(a)
    router.begin_tick(read_facts(a))
    retained = drop(a, 'telegram')
    parse = connector.parse_frontmatter
    def lose_after_read(text):
        fm = parse(text)
        router.authority.release(router.lease)
        return fm
    monkeypatch.setattr(connector, 'parse_frontmatter', lose_after_read)
    connector.drain(a, 'telegram', retained.parent, {}, router.armed)
    assert retained.exists() and not (retained.parent / 'refused').exists()
    assert not any(f.kind in {'source', 'letter'} for f in read_facts(a))


def test_router_keeps_unavailable_draft_pending_and_sends_after_repair(pair, wires):
    a, _ = pair
    router = Router(a)
    router.begin_tick(read_facts(a))
    person = a.root / 'self/people/ada'
    person.mkdir(parents=True)
    (person / 'channels.toml').write_text('channels = ["telegram:42"]\n')
    strand = 's-aaaa-draft'
    append(a, Fact(kind='lease', by='loom:aaaa', id='lease:inbox:1',
                   data=dict(strand=strand, thread='inbox', gen=1, router_gen=router.lease.gen)))
    key = strand + '/reply'
    append(a, Fact(kind='letter', by='strand:' + strand, id=key,
                   data=dict(id=key, **{'from': strand}, to='channel:telegram/42',
                             body='hello', gen=1, router_gen=router.lease.gen)))
    from brr.loom.runtime.lifecycle import main_snapshot
    from brr.loom.runtime.owners import letter_owners
    assert letter_owners(read_facts(a), main_snapshot(a))[key].owner == 'channel'
    token = a.root / 'loom/connectors/telegram/state/token'
    credential = token.read_text()
    token.write_text('999999:wrong_bot')
    router.speak_out(read_facts(a))
    facts = read_facts(a)
    assert wires[1] == []
    assert letter_owners(facts, main_snapshot(a))[key].owner == 'channel'
    assert any('connector-unavailable' in f.id for f in facts)
    token.write_text(credential)
    router.speak_out(read_facts(a))
    assert len(wires[1]) == 1
    assert letter_owners(read_facts(a), main_snapshot(a))[key].done


@pytest.mark.parametrize('loss', ['lease', 'ownership'])
@pytest.mark.parametrize('outcome', ['sent', 'maybe-sent'])
def test_loss_immediately_before_transport_keeps_safe_retry(pair, wires, monkeypatch, loss, outcome):
    a, _ = pair
    router = Router(a)
    router.begin_tick(read_facts(a))
    # Flip only once the local send intention exists, inside the real dispatcher.
    from brr.loom.runtime import speak as module
    original = module.tempfile.TemporaryDirectory
    token = a.root / 'loom/connectors/telegram/state/token'
    credential = token.read_text()
    class LoseBeforeDispatch(original):
        def __enter__(self):
            directory = super().__enter__()
            if loss == 'lease':
                router.granting = False
            else:
                token.write_text('999999:another_bot')
            return directory
    monkeypatch.setattr(module.tempfile, 'TemporaryDirectory', LoseBeforeDispatch)
    assert speak(a, router.lease, 'retry', 'yes', channel='channel:telegram/42',
                 authorize=router.armed)[0] == 'unavailable'
    assert wires[1] == []
    monkeypatch.setattr(module.tempfile, 'TemporaryDirectory', original)
    router.granting = True
    token.write_text(credential)
    if outcome == 'maybe-sent':
        run = subprocess.run
        def lose_receipt(args, **kw):
            result = run(args, **kw)
            if Path(args[0]).name == 'out':
                raise TimeoutError('accepted, receipt lost')
            return result
        monkeypatch.setattr(subprocess, 'run', lose_receipt)
    assert speak(a, router.lease, 'retry', 'yes', channel='channel:telegram/42',
                 authorize=router.armed)[0] == outcome
    assert speak(a, router.lease, 'retry', 'yes', channel='channel:telegram/42',
                 authorize=router.armed)[0] == outcome
    assert len(wires[1]) == 1
