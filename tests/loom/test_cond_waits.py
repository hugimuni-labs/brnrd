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
    command(home, seat, "wish", root, "--result", "url:https://example.com/1", "--line", "Done", "--deliver")
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


def _local(hour, minute=0, days=0):
    """A local clock time ``days`` ahead, on the machine's zone, as the pass reads it."""
    zone = datetime.now().astimezone()
    day = zone.date() + timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone.tzinfo)


def _utc(hour, minute=0, days=0):
    return _local(hour, minute, days).astimezone(timezone.utc)


def _arm_pass(monkeypatch):
    """The suite holds the pass off. These tests are the ones that drive it."""
    monkeypatch.delenv("BRNRD_LOOM_PASS", raising=False)


def test_the_morning_pass_fires_once_at_or_after_the_hour_to_the_home_thread(ledger, monkeypatch):
    """At the hour, to inbox, with pace and at most three candidates; not before, not twice, again tomorrow."""
    _arm_pass(monkeypatch)
    home, seat = ledger
    monkeypatch.setattr("brr.loom.runtime.loom._turn_pace",
                        lambda *args: (lambda thread: "week 90% left, 40% of it to go"))
    monkeypatch.setattr("brr.loom.runtime.idle.candidates",
                        lambda *args, **kwargs: ["move one", "move two", "move three", "move four"])
    subscribe(home, seat, "cond:all")
    assert told(home, _utc(7, 59)) == [], "before the hour, a park does not make it due"
    day = _local(8).date().isoformat()
    [letter] = told(home, _utc(8, 0))
    assert letter.data["to"] == "thread:inbox", "the home thread, not the parker's"
    body = letter.data["body"]
    assert body.startswith(
        f"pass: {day}. Read the last pass's line and check it; read the board; "
        "pick one move (two if both cheap) across every thread; act; "
        "write one line saying what you picked and why.")
    assert "week 90% left, 40% of it to go" in body
    assert "move one" in body and "move two" in body and "move three" in body
    assert "move four" not in body
    assert told(home, _utc(23, 30)) == [], "one notice for the local date"
    tomorrow = _local(8, days=1).date().isoformat()
    [again] = told(home, _utc(8, days=1))
    assert again.id != letter.id and again.data["body"].startswith(f"pass: {tomorrow}.")


def test_a_late_first_tick_fires_for_today_and_a_missed_day_is_not_made_up(ledger, monkeypatch):
    _arm_pass(monkeypatch)
    home, _seat = ledger
    today = _local(15).date().isoformat()
    [letter] = told(home, _utc(15))
    assert letter.data["to"] == "thread:inbox"
    assert letter.data["body"].startswith(f"pass: {today}.")
    missed = _local(9, days=1).date().isoformat()
    later = _local(9, days=2).date().isoformat()
    [again] = told(home, _utc(9, days=2))
    assert again.data["body"].startswith(f"pass: {later}.")
    assert missed not in again.data["body"]
    assert notice_id("cond", f"pass-due|{missed}") not in {f.id for f in read_facts(home)}
    assert told(home, _utc(12, days=2)) == []


def test_a_zero_share_sends_no_pass_and_any_share_above_still_fires(ledger, monkeypatch):
    """Share 0 on the home shell is no morning pass. Share 50 still fires, with no pace line when behind."""
    _arm_pass(monkeypatch)
    home, _seat = ledger
    from brr.loom.runtime.config import set_value
    now = datetime.now(timezone.utc).timestamp()
    reset = datetime.fromtimestamp(now + 0.24 * 7 * 86400, timezone.utc).isoformat()
    behind = {"at": now, "windows": [{"name": "week", "left_pct": 33, "resets_at": reset}]}
    monkeypatch.setattr("brr.loom.runtime.usage.reading", lambda *args, **kwargs: behind)
    set_value(home.root, "body", "claude")
    set_value(home.root, "initiative_share_claude", 0)
    assert conditions.pass_due(home, read_facts(home), _utc(8), set()) == []
    set_value(home.root, "initiative_share_claude", 50)
    [firing] = conditions.pass_due(home, read_facts(home), _utc(8), set())
    assert firing.name == "pass-due" and firing.thread == "inbox"
    assert not any(line.startswith("week ") for line in firing.can.splitlines())


def test_pass_hour_off_sends_nothing(ledger, monkeypatch):
    _arm_pass(monkeypatch)
    home, _seat = ledger
    [letter] = told(home, _utc(10))
    assert letter.data["to"] == "thread:inbox", "the same clock reaches the writer while the hour is on"
    from brr.loom.runtime.config import set_value
    set_value(home.root, "pass_hour", "off")
    assert told(home, _utc(10, days=1)) == []
    assert told(home, _utc(23, days=1)) == []


def test_the_pass_notice_wakes_the_home_thread(tmp_path, monkeypatch):
    """The letter is the wake: route starts the home thread, the same path as any owed notice."""
    _arm_pass(monkeypatch)
    from brr.loom.runtime.config import set_value
    from _step import Loom, kill_bodies, wait_until, write_thread
    from test_lifecycle import bodies
    root = tmp_path / "home"
    write_thread(root, "inbox", "Home", "home", wait="0")
    set_value(root, "pass_hour", 0)
    woken = root / "woken"
    bodies(root, f'''import sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
for letter in ctx.owed():
    if letter.body.startswith("pass:"):
        Path({str(woken)!r}).write_text(letter.body)
    ctx.send("thread:inbox", note="the pass", re=letter.id)
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(woken.exists, 20, loom.dump)
        text = woken.read_text()
        assert text.startswith("pass:")
        assert "pick one move (two if both cheap)" in text
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors, loom.errors


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


def test_a_quiet_stop_names_the_morning_pass_that_will_wake_the_resident(ledger, monkeypatch):
    from brr.loom.runtime import jack
    from brr.loom.runtime.loom import _prepare
    from brr.loom.runtime.project import sender_threads

    _arm_pass(monkeypatch)
    home, _ = ledger
    facts = read_facts(home)
    now = datetime.now(timezone.utc)
    # Choose a wall-clock hour still ahead, or mark today's pass sent.
    if now.astimezone().hour >= 8:
        told(home, now)
        facts = read_facts(home)
    wake = conditions.next_pass(home, facts, now)
    strand = "s-aaaa-rest"
    room = _prepare(home, strand, "inbox", 1, [], sender_threads(facts), facts, "hold")
    (room / "port/wait").write_text("0\n")
    monkeypatch.setenv("BRNRD_BODY_JACK", "hand")
    assert f"Resting until {wake:%H:%MZ} (morning pass in inbox, {wake:%Y-%m-%d}), or the next letter." in (
        (room / "port/in/boundary.md").read_text())
    assert jack.execute("stop", room, "{}") == ""
    assert told(home, wake - timedelta(seconds=1)) == []
    [letter] = told(home, wake)
    assert letter.data["to"] == "thread:inbox"
    assert letter.data["body"].startswith(f"pass: {wake.astimezone().date().isoformat()}.")
