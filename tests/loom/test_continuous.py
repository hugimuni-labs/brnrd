"""A resident never just sleeps (continuous): a park on someone takes turns while
the week's fuel is ahead of pace and the thread has work it could move."""
from datetime import datetime, timedelta, timezone

from brr.daemon2.facts import Fact
from brr.loom.runtime import waits
from brr.loom.runtime.ledger import append
from brr.loom.runtime.config import set_value
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import notice_id
from test_wishes import command, heard, ledger, view  # noqa: F401  (ledger is a fixture)

REPO = "hugimuni-labs/brnrd"
AHEAD = "week 92% left, 79% of it to go"


def at(read, **span):
    return datetime.fromisoformat(read["at"]) + timedelta(**span)


def ticked(home, now, pace):
    facts = read_facts(home)
    before = {f.id for f in facts}
    waits.observe_waits(home, facts, True, {}, now, pace=pace)
    return [f for f in read_facts(home) if f.kind == "letter" and f.id not in before]


def rest(home):
    """The fixture's body left: nothing holds the thread."""
    append(home, Fact(kind="released", by="loom:aaaa", data={"thread": "work", "strand": "s-aaaa-test", "gen": 1}))


def fresh(monkeypatch):
    monkeypatch.setattr(waits, "_JUDGED", set())
    monkeypatch.setattr(waits, "_TURNED", {})


def parked(home, seat, ident, condition):
    command(home, seat, "wish", ident, "--read", "waiting: his review", "--wait", condition)
    return view(home, ident).reads[-1]


def test_a_park_on_someone_carries_the_configured_turn_and_a_ci_park_none(ledger):
    home, seat = ledger
    one, two, three = heard(home, seat, "Review"), heard(home, seat, "Green"), heard(home, seat, "Later")
    assert parked(home, seat, one, f"pr:{REPO}#5")["wait"]["turn"] == 7200
    assert "turn" not in parked(home, seat, two, f"ci:{REPO}@abc1234")["wait"]
    set_value(home.root, "turn", 0)
    assert "turn" not in parked(home, seat, three, f"pr:{REPO}#6")["wait"]


def test_a_turn_wakes_the_thread_once_a_slot_only_ahead_of_pace_and_with_work_to_move(ledger, monkeypatch):
    fresh(monkeypatch)
    home, seat = ledger
    rest(home)
    ident = heard(home, seat, "Review")
    read = parked(home, seat, ident, f"pr:{REPO}#5")
    assert ticked(home, at(read, hours=3), lambda thread: AHEAD) == [], "nothing else on the thread to move"
    other = heard(home, seat, "Something else")
    assert ticked(home, at(read, hours=1), lambda thread: AHEAD) == [], "before the first turn"
    assert ticked(home, at(read, hours=3), lambda thread: AHEAD) == [], "slot 1 was judged once, and failed"
    assert ticked(home, at(read, hours=4, minutes=1), lambda thread: "") == [], "behind pace: rest"
    [letter] = ticked(home, at(read, hours=6, minutes=1), lambda thread: AHEAD)
    assert letter.id == notice_id("turn", waits.turn_subject(view(home, ident), read, 3))
    assert letter.data["to"] == "thread:work" and not letter.data.get("notice"), "owed, so it wakes the thread"
    body = letter.data["body"]
    assert body.startswith(f'turn: {ident} parked 6h on "his review" and still waits; {AHEAD}.')
    assert other in body and "the park stays; its next turn is" in body
    assert ticked(home, at(read, hours=7), lambda thread: AHEAD) == [], "one notice per slot"
    assert view(home, ident).reads[-1] == read, "a turn never ends the park"


def test_a_park_past_its_until_takes_no_turn_but_runs_out(ledger, monkeypatch):
    fresh(monkeypatch)
    home, seat = ledger
    rest(home)
    ident = heard(home, seat, "Review")
    heard(home, seat, "Something else")
    command(home, seat, "wish", ident, "--read", "waiting: x", "--wait", f"pr:{REPO}#5", "--until", "+3h")
    read = view(home, ident).reads[-1]
    [letter] = ticked(home, at(read, hours=4), lambda thread: AHEAD)
    assert "ran out" in letter.data["body"]


def test_a_thread_with_a_live_body_takes_no_turn_and_two_parks_share_one_pace(ledger, monkeypatch):
    fresh(monkeypatch)
    home, seat = ledger
    one, two = heard(home, seat, "Review"), heard(home, seat, "Reply")
    heard(home, seat, "Something else")
    read = parked(home, seat, one, f"pr:{REPO}#5")
    parked(home, seat, two, f"pr:{REPO}#6")
    assert ticked(home, at(read, hours=2, minutes=1), lambda thread: AHEAD) == [], "held: the keepalive has it"
    rest(home)
    turns = ticked(home, at(read, hours=4, minutes=1), lambda thread: AHEAD)
    assert len(turns) == 1, "one turn for the thread, not one per park"


def test_the_loom_reads_the_pace_through_the_shell_the_thread_runs_on(ledger, monkeypatch):
    import time
    from types import SimpleNamespace
    from brr.loom.runtime import loom, usage
    home, _ = ledger
    seen = []
    reset = datetime.fromtimestamp(time.time() + 5.5 * 86400, timezone.utc).isoformat()

    def reading(home, shell, *args, **kwargs):
        seen.append(shell)
        left = 92.0 if shell == "fake" else 20.0
        return {"at": time.time(), "windows": [{"name": "week", "left_pct": left, "resets_at": reset}]}
    monkeypatch.setattr(usage, "reading", reading)
    pace = loom._turn_pace(home, read_facts(home), SimpleNamespace(body="claude/sonnet"))
    assert pace("work") == "week 92% left, 79% of it to go"
    assert pace("other") == "" and seen == ["fake", "claude"], "no lease: the configured body's shell"
