"""A park names what it waits on; the loom checks it every tick and wakes the thread once."""
from datetime import datetime, timedelta, timezone

from brr.daemon2.facts import Fact
from brr.loom.runtime import waits
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.loom import notice_id
from brr.loom.runtime.wishes import wishes
from test_wishes import command, heard, ledger, view  # noqa: F401  (ledger is a fixture)

REPO = "hugimuni-labs/brnrd"


def later(**span):
    return datetime.now(timezone.utc) + timedelta(**span)


def told(home, now=None, forge=None):
    """One tick's observation; the notices it wrote, by subject."""
    facts = read_facts(home)
    before = {f.id for f in facts}
    waits.observe_waits(home, facts, True, forge or {}, now)
    return [f for f in read_facts(home) if f.kind == "letter" and f.id not in before]


def park(home, seat, ident, *args):
    command(home, seat, "wish", ident, "--read", "waiting: the merge", *args)
    return view(home, ident).reads[-1]


def test_a_pr_wait_resolves_on_the_merge_and_tells_the_thread_once(ledger):
    home, seat = ledger
    ident = heard(home, seat, "Ship it")
    read = park(home, seat, ident, "--wait", f"pr:{REPO}#12")
    assert read["wait"]["on"] == f"pr:{REPO}#12"
    assert read["wait"]["until"] > later(days=6).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert told(home) == []
    append(home, Fact(kind="pr.merged", by="strand:s-aaaa-test", data={
        "thread": "work", "repo": REPO, "number": 12, "head": "loom/x", "merge_commit": "abcdef1234"}))
    [letter] = told(home)
    assert letter.id == notice_id("wait", waits.subject(view(home, ident), read))
    assert letter.data["to"] == "thread:work" and letter.data["from"] == "loom"
    assert f"wait on {ident} resolved: pr:{REPO}#12 merged (abcdef12)" in letter.data["body"]
    assert told(home) == [], "the notice id derives from the read: one letter per park"


def test_a_forge_state_resolves_a_wait_the_ledger_never_saw(ledger):
    home, seat = ledger
    pr, ci = heard(home, seat, "Merged elsewhere"), heard(home, seat, "Green elsewhere")
    park(home, seat, pr, "--wait", f"pr:{REPO}#7")
    park(home, seat, ci, "--wait", f"ci:{REPO}@abc1234")
    assert waits.forge_wants(wishes(read_facts(home))) == {f"pr:{REPO}#7", f"ci:{REPO}@abc1234"}
    assert told(home, forge={f"ci:{REPO}@abc1234": {"state": "pending"}}) == []
    letters = told(home, forge={f"pr:{REPO}#7": {"state": "closed", "at": "x", "url": "u"},
                                f"ci:{REPO}@abc1234": {"state": "red"}})
    bodies = sorted(letter.data["body"].splitlines()[0] for letter in letters)
    assert bodies[0].startswith(f"wait on {ci} resolved: ci:{REPO}@abc1234 is red")
    assert bodies[1].startswith(f"wait on {pr} resolved: pr:{REPO}#7 closed")


def test_a_wish_wait_resolves_on_a_settled_state_or_a_citing_letter(ledger):
    home, seat = ledger
    waiter, target = heard(home, seat, "Waiter"), heard(home, seat, "Target")
    park(home, seat, waiter, "--wait", f"wish:{target}")
    assert "no wish w-nope" in command(home, seat, "wish", waiter, "--read", "waiting: x",
                                       "--wait", "wish:w-nope", ok=False)
    command(home, seat, "wish", target, "--read", "Reading it")
    assert told(home) == [], "a reading is not an outcome"
    command(home, seat, "wish", target, "--drop", "not needed")
    [letter] = told(home)
    assert f"{target} is dropped" in letter.data["body"]
    # The second park on a fresh target resolves when a letter cites it.
    other = heard(home, seat, "Other")
    park(home, seat, waiter, "--wait", f"wish:{other}")
    # The port turns a body's &ref into the letter's links; this is that letter.
    append(home, Fact(kind="letter", by="person:p-test", id="p-test/cite1", data={
        "id": "p-test/cite1", "to": "thread:work", "from": "p-test",
        "body": f"answering &{other}: yes", "links": [other]}))
    [letter] = told(home)
    assert f"{other} was answered by" in letter.data["body"]


def test_letter_and_at_waits(ledger):
    home, seat = ledger
    one, two = heard(home, seat, "Reply"), heard(home, seat, "Clock")
    question = inject_letter(home.root, to="thread:other", body="Any word?")
    park(home, seat, one, "--wait", f"letter:{question}")
    read = park(home, seat, two, "--wait", "at:+2h")
    assert read["wait"]["on"].startswith("at:20") and read["wait"]["on"].endswith("Z")
    assert told(home) == []
    inject_letter(home.root, to="thread:work", body="word", re=question)
    [letter] = told(home)
    assert f"{question} was answered by" in letter.data["body"]
    [letter] = told(home, now=later(hours=3))
    assert "has passed" in letter.data["body"] and two in letter.data["body"]


def test_a_typed_wait_runs_out_at_its_until_and_says_re_judge(ledger):
    home, seat = ledger
    ident = heard(home, seat, "Slow")
    park(home, seat, ident, "--wait", f"pr:{REPO}#3", "--until", "+1h")
    [letter] = told(home, now=later(hours=2))
    assert f"wait on {ident} ran out: nothing resolved by" in letter.data["body"]
    assert "re-judge" in letter.data["body"]


def test_a_prose_park_backs_off_and_asks_for_a_typed_wait(ledger):
    home, seat = ledger
    ident = heard(home, seat, "Prose")
    steps = []
    for _ in range(4):
        read = park(home, seat, ident)
        steps.append((read["wait"]["step"], read["wait"]["until"]))
    assert [step for step, _ in steps] == [1, 2, 3, 3]
    # +24h, then +3d, then +7d, each from its own re-park.
    spans = [datetime.strptime(until, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
             - datetime.now(timezone.utc) for _, until in steps]
    assert [round(span / timedelta(hours=1)) for span in spans] == [24, 72, 168, 168]
    assert told(home, now=later(days=6)) == []
    [letter] = told(home, now=later(days=8))
    assert "re-check 3 of a prose park" in letter.data["body"] and "--wait" in letter.data["body"]
    # A typed park in between restarts the back-off.
    park(home, seat, ident, "--wait", f"pr:{REPO}#1")
    assert park(home, seat, ident)["wait"]["step"] == 1


def test_a_result_or_a_new_reading_ends_the_wait(ledger):
    home, seat = ledger
    ident = heard(home, seat, "Ends")
    park(home, seat, ident, "--wait", "at:+1h")
    command(home, seat, "wish", ident, "--read", "Doing it now")
    assert told(home, now=later(hours=2)) == []
    park(home, seat, ident, "--wait", "at:+1h")
    command(home, seat, "wish", ident, "--result", "pr:https://example.com/1", "--line", "Done")
    assert told(home, now=later(hours=2)) == []


def test_wait_flags_need_a_park_and_a_known_grammar(ledger):
    home, seat = ledger
    ident = heard(home, seat, "Flags")
    assert "go with --read" in command(home, seat, "wish", ident, "--wait", "at:+1h", ok=False)
    assert "expected one of" in command(home, seat, "wish", ident, "--read", "waiting: x",
                                        "--wait", "merge soon", ok=False)
    # --wait on a reading without the prefix makes it a park.
    command(home, seat, "wish", ident, "--read", "the deploy", "--wait", "at:+1h")
    assert view(home, ident).reads[-1]["reading"] == "waiting: the deploy"


def test_ask_default_at_parks_the_parent_and_wakes_it_with_the_rec(ledger):
    home, seat = ledger
    root = heard(home, seat, "Decide")
    assert "needs --rec" in command(home, seat, "ask", "Name?", "--for", root, "--default-at", "+1h", ok=False)
    ask = command(home, seat, "ask", "Which name?", "--for", root, "--rec", "Call it loom",
                  "--default-at", "+1h")
    read = view(home, root).reads[-1]
    assert read["wait"]["on"] == f"wish:{ask}" and read["wait"]["default"] == "Call it loom"
    assert told(home) == []
    [letter] = told(home, now=later(hours=2))
    assert "nobody answered" in letter.data["body"] and "Your rec was: Call it loom" in letter.data["body"]


def test_the_loop_polls_the_forge_and_its_notice_wakes_the_parked_thread(tmp_path, monkeypatch):
    """A body parks on a PR; the Watcher reads it merged; the loom's letter starts a body again."""
    import json
    from brr.loom.runtime.project import holder
    from brr.loom.runtime.selfrepo import init_self
    from _step import Loom, kill_bodies, wait_until, write_thread
    from test_lifecycle import bodies

    state = {"state": "open", "url": "u"}
    polled = []

    class Forge:
        def pull(self, repo, number):
            polled.append((repo, number))
            return dict(state)

        def ci(self, repo, sha):
            return {"state": "pending"}

    monkeypatch.setattr(waits, "forge_for", lambda config: Forge())
    monkeypatch.setattr(waits, "EVERY", 0)
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
    if letter.body == "begin":
        wish = verb("wish", "new", "--line", "Ship it", "--say", letter.id)
        verb("wish", wish, "--read", "waiting: the merge", "--wait", "pr:{REPO}#5")
        atomic_write(Path({str(root / "parked")!r}), wish)
    else:
        atomic_write(Path({str(root / "woken")!r}), json.dumps({{"id": letter.id, "body": letter.body}}))
    ctx.send("thread:work", note="read", re=letter.id)
''')
    inject_letter(root, to="thread:work", body="begin")
    loom = Loom(root)
    loom.start()
    try:
        wait_until((root / "parked").exists, 20, loom.dump)
        wait_until(lambda: holder(loom.facts(), "work") is None, 20, loom.dump)
        wait_until(lambda: polled, 10, loom.dump)
        assert not (root / "woken").exists(), "an open PR wakes nobody"
        state.update(state="merged", at="2026-10-27T12:00:00Z")
        wait_until((root / "woken").exists, 20, loom.dump)
        woken = json.loads((root / "woken").read_text())
        wish = (root / "parked").read_text()
        assert woken["id"].startswith("loom/wait-")
        assert f"wait on {wish} resolved: pr:{REPO}#5 merged" in woken["body"]
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors, loom.errors
    assert sum(1 for f in loom.facts() if f.kind == "letter" and f.id.startswith("loom/wait-")) == 1
