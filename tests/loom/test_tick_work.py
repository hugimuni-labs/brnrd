"""Work the loop repeats while the ledger holds still: counted, never timed.

A derivation over the ledger that a tick, a boundary or the snapshot asks for
is rebuilt only when a fact of a kind it reads arrives. A renewal, or a fact
it does not read, keeps every result; a letter rebuilds each one once, not
once per caller (tick-cost-3 in the self).
"""
from collections import Counter

from brr.loom.runtime import dispositions, letter_presence, pull, snapshot, spend, wishes as wishes_mod
from brr.loom.runtime.config import load_config
from brr.loom.runtime.connector_presence import Presence
from brr.loom.runtime.faces import triggers
from brr.loom.runtime.ledger import Fact, append, inject_letter, shared_reader
from brr.loom.runtime.project import projection_cache
from brr.loom.runtime.pull import flavour
from brr.loom.runtime.wishes import told
from test_requests_raises import TO, asks, channel, person_wish
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)

STRAND = "s-aaaa-test"
COUNTED = [(wishes_mod, "wishes"), (dispositions, "letter_index"), (spend, "_allocation"),
           (pull, "move"), (letter_presence, "steps")]


def counting(monkeypatch):
    calls = Counter()
    for module, name in COUNTED:
        real = getattr(module, name)

        def counted(*args, _real=real, _name=name, **kwargs):
            calls[_name] += 1
            return _real(*args, **kwargs)
        monkeypatch.setattr(module, name, counted)
    return calls


def tick(home, presence, config):
    """What one held tick asks of the ledger: the snapshot, a body's boundary
    reads for two threads, and the connector's presence."""
    facts = shared_reader(home).refresh(home)
    with projection_cache():
        snapshot.assemble(home, facts, config)
        found = told(facts)
        for thread in ("work", "other"):
            spend.allocation(facts, thread, "thread:inbox", {}, 1000)
        triggers(facts, STRAND, 1, "work")
        for ask in (w for w in found.values() if w.sender == "resident"):
            flavour(facts, ask, found, None)
        presence.update(facts)
    return facts


def test_a_still_ledger_rebuilds_nothing_and_a_letter_rebuilds_each_once(ledger, monkeypatch):
    home, seat = ledger
    channel(home)
    person_wish(home, seat, "Ship the parser")
    command(home, seat, "send", "--to", TO, "Merge #12?")
    assert len(asks(home)) == 1
    append(home, Fact(kind="body.started", by="loom:aaaa", data={
        "strand": STRAND, "gen": 1, "pid": 1, "shell": "fake", "core": "fake", "readme": "", "lstart": ""}))
    config, presence = load_config(home.root), Presence(home)
    calls = counting(monkeypatch)

    tick(home, presence, config)
    first = dict(calls)
    # Each derivation ran, once: the snapshot and every boundary share one result.
    assert first == {"wishes": 1, "letter_index": 1, "_allocation": 2, "move": 1, "steps": 1}

    # Ten more ticks over the same ledger, and a renewal: nothing is rebuilt.
    for _ in range(5):
        tick(home, presence, config)
    append(home, Fact(kind="router.renewed", by="loom:aaaa", data={"gen": 1, "install": "aaaa"}))
    for _ in range(5):
        tick(home, presence, config)
    assert dict(calls) == first

    # A letter is news to each of them: one rebuild apiece, however many ticks follow.
    inject_letter(home.root, to="thread:work", body="back now")
    for _ in range(3):
        tick(home, presence, config)
    assert dict(calls) == {name: 2 * count for name, count in first.items()}


def test_the_snapshot_and_the_boundary_read_the_same_wishes(ledger):
    """One derivation: the board's wish states are the boundary's, leases and loom start included."""
    home, seat = ledger
    channel(home)
    person_wish(home, seat, "Ship the parser")
    command(home, seat, "send", "--to", TO, "Merge #12?")
    facts = shared_reader(home).refresh(home)
    with projection_cache():
        view = snapshot.View(home, facts, load_config(home.root))
        assert view.told is told(facts)
