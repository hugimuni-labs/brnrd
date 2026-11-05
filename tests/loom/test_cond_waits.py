"""cond: waits: the tick's sweeps as conditions, each firing told once, to its seat or to a cond: park."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import conditions, waits
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.loom import notice, notice_id
from test_wishes import command, heard, ledger, view  # noqa: F401  (ledger is a fixture)


def later(**span):
    return datetime.now(timezone.utc) + timedelta(**span)


def told(home, now=None):
    """One evaluation; the letters it wrote."""
    facts = read_facts(home)
    before = {f.id for f in facts}
    conditions.observe_conditions(home, facts, True, now, force=True)
    return [f for f in read_facts(home) if f.kind == "letter" and f.id not in before]


def elsewhere(home, seat, line):
    """A wish on thread:other, which no seat holds."""
    letter = inject_letter(home.root, to="thread:other", body="Please: " + line)
    return command(home, seat, "wish", "new", "--line", line, "--say", letter, "--thread", "other")


def subscribe(home, seat, on="cond:all"):
    """The inbox's standing park: work's seat parks a wish on every condition."""
    ident = heard(home, seat, "Tend the house")
    command(home, seat, "wish", ident, "--read", "waiting: the house", "--wait", on)
    return ident


def test_a_cond_park_stands_and_names_its_conditions(ledger):
    home, seat = ledger
    ident = heard(home, seat, "Tend")
    with pytest.raises(ValueError, match="expected all or a comma list"):
        waits.canonical("cond:sometimes", later())
    assert waits.canonical("cond:wish-stale, ask-moot", later()) == "cond:ask-moot,wish-stale"
    assert waits.canonical("cond:all", later()) == "cond:" + ",".join(sorted(conditions.NAMES))
    from test_wake import cli
    parked = cli(["wish", ident, "--room", str(seat), "--read", "waiting: the house", "--wait", "cond:all"])
    assert parked.returncode == 0, parked.stderr
    wait = view(home, ident).reads[-1]["wait"]
    assert "until" not in wait and "turn" not in wait, "a subscription has no deadline and no turn"
    assert "parked: wakes thread:work on each firing of cond:ask-moot," in parked.stderr
    from brr.loom.runtime.wishes import told as found_of
    facts = read_facts(home)
    found = found_of(facts)
    [(wish, read)] = [row for row in waits.live(found) if row[0].id == ident]
    assert waits.outcome(wish, read, facts, found, {}, later(days=400)) is None, "it never resolves itself"


def test_a_moot_ask_wakes_the_seat_that_holds_its_thread_once(ledger):
    home, seat = ledger
    root = heard(home, seat, "Decide")
    ask = command(home, seat, "ask", "Which name?", "--for", root)
    assert told(home) == []
    command(home, seat, "wish", root, "--drop", "decided without it")
    [letter] = told(home)
    assert letter.id == notice_id("cond", f"ask-moot|{ask}|dropped")
    assert letter.data["to"] == "thread:work", "the seat holds work: no park needed"
    assert f"ask-moot: {ask}" in letter.data["body"] and f"{root}, which is dropped" in letter.data["body"]
    assert f"wish {ask} --drop" in letter.data["body"], "the act rides in the letter"
    assert told(home) == [], "the id derives from the firing: one letter"


def test_a_stale_wish_on_an_unheld_thread_waits_for_a_park_then_goes_to_it(ledger):
    home, seat = ledger
    stale = elsewhere(home, seat, "Forgotten")
    assert told(home, now=later(days=6)) == []
    assert told(home, now=later(days=8)) == [], "nobody holds other and nobody parks: not told yet"
    subscribe(home, seat, "cond:wish-stale")
    [letter] = [f for f in told(home, now=later(days=8))]
    assert letter.data["to"] == "thread:work", "the parker's thread, not the wish's"
    assert f"wish-stale: {stale}" in letter.data["body"] and "no left yet" in letter.data["body"]
    assert told(home, now=later(days=9)) == []
    command(home, seat, "wish", stale, "--left", "find out who wanted it")
    assert told(home, now=later(days=1)) == [], "touched: a new week starts"
    [again] = told(home, now=later(days=8))
    assert "last left: find out who wanted it" in again.data["body"], "stale again is a new episode"


def test_a_parked_or_asked_or_delivered_wish_is_never_stale(ledger):
    home, seat = ledger
    subscribe(home, seat)
    parked = elsewhere(home, seat, "Parked")
    command(home, seat, "wish", parked, "--read", "waiting: CI", "--wait", "at:+30d")
    root = elsewhere(home, seat, "Delivered")
    command(home, seat, "wish", root, "--result", "pr:https://example.com/1", "--line", "Done", "--deliver")
    letters = told(home, now=later(days=8))
    bodies = "\n".join(f.data["body"] for f in letters)
    assert parked not in bodies and root not in bodies


def test_an_unread_signal_goes_to_the_park_and_a_settled_one_never(ledger):
    home, seat = ledger
    subscribe(home, seat, "cond:signal-unread")
    facts = read_facts(home)
    notice(home, facts, "mirror", "push failed", "mirror: push to origin failed twice", "fix the remote",
           thread="other")
    ident = notice_id("mirror", "push failed")
    notice(home, read_facts(home), "seen", "passive", "cleared", "nothing", thread="other", passive=True)
    assert told(home, now=later(hours=23)) == []
    [letter] = told(home, now=later(hours=25))
    assert letter.data["to"] == "thread:work"
    assert f"signal-unread: {ident} to thread:other" in letter.data["body"]
    assert "mirror: push to origin failed twice" in letter.data["body"]
    assert told(home, now=later(hours=48)) == [], "its own notice is no new signal"


def test_a_kept_room_whose_pr_merged_is_told(ledger):
    home, seat = ledger
    subscribe(home, seat, "cond:room-kept")
    from brr.loom.runtime.reap import KEPT
    path = home.root / KEPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kept": {"s-aaaa-test": "leftovers in brnrd: 2 commits, 0 edits"}, "told": []}))
    assert told(home) == [], "no merge yet"
    append(home, Fact(kind="pr.merged", by="strand:s-aaaa-test", data={
        "thread": "work", "repo": "brnrd", "number": 31, "head": "loom/work", "merge_commit": "feedface00"}))
    [letter] = told(home)
    assert "room-kept: s-aaaa-test (thread:work) is kept for leftovers in brnrd" in letter.data["body"]
    assert "brnrd#31 merged as feedface" in letter.data["body"]
    assert "reap s-aaaa-test --force" in letter.data["body"]
    path.write_text(json.dumps({"kept": {"s-aaaa-test": "loose files: notes.txt"}, "told": []}))
    assert told(home) == [], "loose files are not in any pull request"


def test_the_inbox_parks_once_and_the_loom_wakes_it_with_the_fact(tmp_path, monkeypatch):
    """Through the jack: the body parks on cond:all and stops; a moot ask starts it with the fact."""
    from brr.loom.runtime.project import holder
    from brr.loom.runtime.selfrepo import init_self
    from _step import Loom, kill_bodies, wait_until, write_thread
    from test_lifecycle import bodies

    monkeypatch.setattr(conditions, "EVERY", 0)
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work", "parker", wait="0")
    bodies(root, f'''import json, subprocess, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
def verb(*args):
    proc = subprocess.run([sys.executable, "-m", "brr.loom.runtime", *args[:1], "--room", str(ctx.room), *args[1:]],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()
for letter in ctx.owed():
    if letter.body == "tend the house":
        tend = verb("wish", "new", "--line", "Tend the house", "--say", letter.id)
        root = verb("wish", "new", "--line", "Name it", "--say", letter.id)
        ask = verb("ask", "Which name?", "--for", root)
        verb("wish", tend, "--read", "waiting: the house", "--wait", "cond:all")
        atomic_write(Path({str(root / "parked")!r}), json.dumps({{"root": root, "ask": ask}}))
    else:
        atomic_write(Path({str(root / "woken")!r}), json.dumps({{"id": letter.id, "body": letter.body}}))
    ctx.send("thread:work", note="read", re=letter.id)
''')
    inject_letter(root, to="thread:work", body="tend the house")
    loom = Loom(root)
    loom.start()
    try:
        wait_until((root / "parked").exists, 20, loom.dump)
        wait_until(lambda: holder(loom.facts(), "work") is None, 20, loom.dump)
        assert not (root / "woken").exists(), "a standing park wakes nobody on its own"
        made = json.loads((root / "parked").read_text())
        from test_wake import cli
        dropped = cli(["wish", made["root"], "--home", str(root), "--drop", "named it myself"])
        assert dropped.returncode == 0, dropped.stderr
        wait_until((root / "woken").exists, 20, loom.dump)
        woken = json.loads((root / "woken").read_text())
        assert woken["id"].startswith("loom/cond-")
        assert f"ask-moot: {made['ask']}" in woken["body"] and "which is dropped" in woken["body"]
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors, loom.errors
