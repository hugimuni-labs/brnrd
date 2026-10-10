"""Lease acceptance, on hand-built facts."""

from brr.daemon2.facts import Fact

from brr.loom.runtime.config import granting_window
from brr.loom.runtime.project import fold, holder, lease_accepted, owed

STRAND = "s-aaaa-aaaaaa"
OTHER = "s-bbbb-bbbbbb"
LETTER = "p-test/ppppp"


def _fact(kind, by, data, ident, n):
    return Fact(
        kind=kind, by=by, data=data, id=ident, hlc=(n, 0, "aaaa"),
        at="2026-10-08T00:00:00+00:00",
    )


def _router(gen, n, install="aaaa"):
    return _fact(
        "router", f"loom:{install}",
        {"install": install, "gen": gen, "until": 0},
        f"router:{gen}", n,
    )


def _lease(strand, thread, gen, n, router_gen=1):
    return _fact(
        "lease", "loom:aaaa",
        {"thread": thread, "strand": strand, "gen": gen, "router_gen": router_gen},
        f"lease:{thread}:{gen}:{n}", n,
    )


def test_a_step_1_lease_still_counts_when_no_router_exists():
    lease = _fact(
        "lease", "loom:aaaa",
        {"thread": "ta", "strand": STRAND, "gen": 1},
        "lease:ta:1", 1,
    )
    assert lease_accepted(None, lease)
    assert holder([lease], "ta") == (STRAND, 1)


def test_a_stale_router_lease_written_after_the_new_router_counts_for_nothing():
    facts = [
        _router(1, 1),
        _lease(STRAND, "ta", 1, 2, router_gen=1),
        _router(2, 3, install="bbbb"),
        _lease(OTHER, "ta", 2, 4, router_gen=1),
    ]
    state = fold(facts)
    assert holder(facts, "ta") == (STRAND, 1)
    accepted = [fact.id for fact in state.accepted if fact.kind == "lease"]
    assert accepted == ["lease:ta:1:2"]


def test_a_lease_from_the_new_router_fences_the_previous_install():
    note = _fact(
        "note", f"strand:{STRAND}",
        {"re": LETTER, "why": "late", "gen": 1, "strand": STRAND},
        f"{STRAND}/late", 6,
    )
    letter = _fact(
        "letter", "person:p-test",
        {"id": LETTER, "to": "thread:ta", "body": "ping", "from": "p-test"},
        LETTER, 3,
    )
    facts = [
        _router(1, 1),
        _lease(STRAND, "ta", 1, 2, router_gen=1),
        letter,
        _router(2, 4, install="bbbb"),
        _lease(OTHER, "ta", 2, 5, router_gen=2),
        note,
    ]
    assert holder(facts, "ta") == (OTHER, 2)
    assert [fact.id for fact in owed(facts, "ta")] == [LETTER]


def test_the_router_stops_granting_before_a_fast_peer_can_acquire():
    # A's clock at 2, lease until 8, max_skew 5, margin 1: A is at the line.
    assert not granting_window(2, 8, 5, 1)
    # B is 3s ahead, so B's clock is 5, still short of until.
    assert 2 + 3 < 8
    # Later A is still stopped and B's clock has passed until.
    assert not granting_window(5.5, 8, 5, 1)
    assert 5.5 + 3 > 8
    # A fresh renew puts until far enough that A grants again.
    assert granting_window(5.5, 5.5 + 8, 5, 1)
