"""One message is one letter: a twin door or a new lead cannot admit it again.

Seen 2026-11-18 02:41Z: install 46a2 took the lead and its relay, named
``relay``, delivered five Telegram messages that ae66's relay, named ``cloud``,
had admitted and answered; each landed a second time as ``letter:relay:…``.
"""
from brr.daemon2.facts import Fact
from brr.loom.runtime import connector, connector_ownership as ownership
from brr.loom.runtime.config import set_value
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import _home_streak
from brr.loom.runtime.port import _front
from test_connector_ownership import machine, exchange

NATIVE = 'telegram:42:11342'


def drop(home, name, ident):
    path = home.root / 'loom/connectors' / name / 'state/in' / f'{ident}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_front(dict(kind='letter', id=ident, to='42', **{'from': '42'}), 'Thanks!'))
    return path


def admit(home, name, ident):
    path = drop(home, name, ident)
    connector.drain(home, name, path.parent, authorize=lambda: True)
    assert not path.exists()  # consumed either way, never retried or refused


def letters(home):
    return sorted(f.id for f in read_facts(home) if f.kind == 'letter')


def test_new_lead_does_not_readmit_what_the_old_lead_admitted(tmp_path):
    remote = tmp_path / 'self.git'
    old = machine(tmp_path / 'ae66', 'ae66', ('cloud=relay', 'mine=telegram'))
    new = machine(tmp_path / '46a2', '46a2', ('relay',))
    for home in (old, new):
        set_value(home.root, 'lease_remote', str(remote))
        thread = home.thread_dir('inbox')
        thread.mkdir(parents=True)
        (thread / 'README.md').write_text('---\nid: inbox\nstatus: open\n---\n# Inbox\n')
    ownership.migrate(old)
    exchange(old, new)
    ownership.migrate(new)
    exchange(new, old)
    assert ownership.twins(new, 'relay') == ('cloud',)
    assert ownership.twins(old, 'mine') == ()

    admit(old, 'cloud', NATIVE)
    exchange(old, new)  # the cloud copy is in the new lead's ledger
    assert letters(new) == [f'letter:cloud:{NATIVE}']

    admit(new, 'relay', NATIVE)
    assert letters(new) == [f'letter:cloud:{NATIVE}']
    assert not any(f.id == f'source:relay:{NATIVE}' for f in read_facts(new))

    # A message the old lead never saw still lands through the new door.
    admit(new, 'relay', 'telegram:42:11343')
    assert f'letter:relay:telegram:42:11343' in letters(new)

    # A direct bot is another door: the same chat and number are another message.
    admit(old, 'mine', NATIVE)
    assert f'letter:mine:{NATIVE}' in letters(old)


def test_home_streak_survives_two_ends_of_one_gen():
    def ended(strand, gen, home_failure):
        return Fact(kind='attempt.ended', by='loom:test', id=f'attempt.ended:{strand}:{gen}',
                    data=dict(thread='room-sense', gen=gen,
                              evidence=dict(home_failure=home_failure)))
    facts = [ended('s-ae66-a', 4, False), ended('s-ae66-b', 5, True), ended('s-46a2-c', 5, True)]
    assert _home_streak(facts, 'room-sense', 6) == 5
