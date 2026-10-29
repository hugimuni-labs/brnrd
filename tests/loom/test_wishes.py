"""Exercise wish states through real CLI processes and the runtime wake."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts, fact_filename
from brr.loom.runtime.registry import UndeclaredFact
from brr.loom.runtime.selfrepo import init_self, room, run_wake
from brr.loom.runtime.wishes import Say, wishes
from test_wake import cli, commit, write_thread


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("BRNRD_ROOM", raising=False)
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root / "self", "work", "Wish work\n\n## Plan\n\n- [ ] Build\n\n## Notes\nKeep notes")
    write_thread(root / "self", "other", "Another thread")
    commit(root / "self", "threads")
    clone = room(root, "s-aaaa-test")
    home = Home(root, install="aaaa")
    append(home, Fact(kind="router", by="loom:aaaa", data={"gen": 1}))
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": "work", "strand": "s-aaaa-test", "gen": 1,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))
    return home, clone.parent


def command(home, seat, *args, person=False, ok=True):
    result = cli([args[0], "--home" if person else "--room", str(home.root if person else seat), *args[1:]])
    assert result.returncode == (0 if ok else 1), result.stderr
    return result.stdout.strip() if ok else result.stderr


def word(home):
    """The person opened work; its body settles it on their word."""
    return inject_letter(home.root, to="thread:work", body="done")


def heard(home, seat, line, *args):
    """A wish born for a letter: the ledger refuses one that cites nothing."""
    letter = inject_letter(home.root, to="thread:work", body="Please: " + line)
    return command(home, seat, "wish", "new", "--line", line, "--say", letter, *args)


def view(home, ident):
    return wishes(read_facts(home))[ident]


def test_full_lifecycle_and_wake(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please build a thing")
    ident = command(home, seat, "wish", "new", "--line", "Build a thing", "--say", letter)
    assert view(home, ident).state == "heard"
    assert view(home, ident).origin == "said"
    command(home, seat, "wish", ident, "--read", "Implement it")
    assert view(home, ident).state == "read"
    command(home, seat, "wish", ident, "--read", "Only the first part", person=True)
    assert view(home, ident).state == "scoped"
    command(home, seat, "wish", ident, "--result", "pr:https://example.com/1", "--line", "First part")
    assert view(home, ident).state == "granting"
    command(home, seat, "wish", ident, "--result", "pr:https://example.com/1", "--line", "First part", "--deliver")
    assert view(home, ident).state == "delivered"
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    text = output.getvalue()
    assert text.index("## Plan") < text.index("## Wishes") < text.index("## Notes")
    assert ident + " · delivered" in text
    command(home, seat, "wish", ident, "--accept", "act", "--ref", "merged", person=True)
    assert view(home, ident).state == "accepted"
    assert ident not in command(home, seat, "wishes")
    assert ident in command(home, seat, "wishes", "--all")
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    assert ident not in output.getvalue()
    command(home, seat, "wish", ident, "--say", letter)
    assert view(home, ident).state == "delivered"
    command(home, seat, "wish", ident, "--drop", "No longer needed")
    assert view(home, ident).state == "dropped"
    assert view(home, ident).verdict["ref"] == "No longer needed"
    command(home, seat, "wish", ident, "--line", "Try again", "--thread", "other")
    assert view(home, ident).state == "delivered"
    assert ident not in command(home, seat, "wishes", "--thread", "work")
    for f in read_facts(home):
        if f.kind.startswith("wish"):
            assert fact_filename(f) == ("loom.jsonl" if f.by.startswith("person:") else "s-aaaa-test.jsonl")


def test_says_asks_and_send_stamp(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="A long enough letter")
    said = command(home, seat, "wish", "new", "--line", "said", "--say", letter + "@2+4")
    derived = command(home, seat, "wish", "new", "--line", "derived", "--parent", said)
    ask = command(home, seat, "wish", "new", "--from", "resident", "--line", "Need your decision",
                  "--parent", said)
    assert view(home, derived).parent == view(home, ask).parent == said
    assert view(home, derived).derived_unconfirmed
    assert view(home, said).says == [Say(letter, 2, 4)]
    assert view(home, said).origin == "said"
    command(home, seat, "wish", derived, "--read", "confirmed", person=True)
    assert not view(home, derived).derived_unconfirmed
    out = StringIO()
    assert run_wake(seat, "work", stdout=out) == 0
    section = out.getvalue().split("## Wishes\n")[1].split("## Notes")[0]
    assert section.splitlines()[0].startswith(ask)
    sent = command(home, seat, "send", "--to", "thread:other", "--re", letter, "--wish", "new:Stamped reply", "A reply")
    stamped = next(w for w in wishes(read_facts(home)).values() if w.line == "Stamped reply")
    assert stamped.says == [Say(letter, 0, None)]
    assert (seat / "port/out" / (sent.split("/")[1] + ".md")).exists()
    command(home, seat, "send", "--to", "thread:other", "--re", letter, "--wish", said, "Followup")
    assert view(home, said).says == [Say(letter, 2, 4), Say(letter, 0, None)]
    assert "asks (yours)" in command(home, seat, "wishes")
    append(home, Fact(kind="letter", by="strand:s-aaaa-test", id="s-aaaa-test/source", data={
        "id": "s-aaaa-test/source", "from": "s-aaaa-test", "to": "thread:other", "body": "Inference", "gen": 1}))
    command(home, seat, "wish", said, "--say", "s-aaaa-test/source")
    assert view(home, said).origin == "derived"


def test_refused_writes_have_no_effect(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="four")
    before = read_facts(home)
    assert "span past" in command(home, seat, "wish", "new", "--line", "Bad", "--say", letter + "@2+3", ok=False)
    assert "no letter" in command(home, seat, "wish", "new", "--line", "Bad", "--say", "missing", ok=False)
    assert "no wish" in command(home, seat, "send", "--to", "thread:other", "--re", letter, "--wish", "absent", "reply", ok=False)
    assert "--say <letter> or --parent <wish>" in command(home, seat, "wish", "new", "--line", "Bad", ok=False)
    assert "--parent: no wish" in command(home, seat, "wish", "new", "--line", "Bad", "--parent", "w-absent", ok=False)
    assert read_facts(home) == before
    assert not (seat / "port/out").exists()
    with pytest.raises(UndeclaredFact, match="does not declare stored_state"):
        append(home, Fact(kind="wish", by="person:test", data={"wish": "w-bad", "says": [],
            "from": "person", "line": "Bad", "thread": "work", "stored_state": "heard"}))
    with pytest.raises(UndeclaredFact, match="a birth .* or a touch"):
        append(home, Fact(kind="wish", by="person:test", data={"wish": "w-bad", "says": [],
            "from": "person", "line": "Bad", "thread": "work"}))
    assert read_facts(home) == before
    ident = heard(home, seat, "Fine")
    assert "written by strand" in command(home, seat, "wish", ident, "--result", "file:x", "--line", "x", person=True, ok=False)


def test_unconfirmed_is_computed_at_read_time_and_fold_orders_facts(ledger):
    home, seat = ledger
    ident = command(home, seat, "wish", "new", "--line", "Old derived wish",
                    "--parent", heard(home, seat, "Parent"))
    fact = next(f for f in read_facts(home) if f.kind == "wish" and f.id == ident)
    old = replace(fact, at=(datetime.now(timezone.utc) - timedelta(days=61)).isoformat())
    w = wishes([old])[ident]
    assert w.unconfirmed()
    assert not w.unconfirmed(datetime.fromisoformat(old.at) + timedelta(days=59))
    command(home, seat, "wish", ident, "--read", "Read")
    command(home, seat, "wish", ident, "--accept", "word")
    command(home, seat, "wish", ident, "--line", "Reopen")
    facts = read_facts(home)
    assert wishes(reversed(facts))[ident].state == "read"
    assert wishes(facts)[ident].line == "Reopen"


def test_wake_bounds_each_line_and_reserves_its_budget(ledger, monkeypatch):
    home, seat = ledger
    ident = heard(home, seat, "recall\n" + "x" * 2000)
    command(home, seat, "wish", ident, "--result", "file:" + "r" * 1000,
            "--line", "Result", "--deliver")
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    prompt = output.getvalue()
    line = prompt.split("## Wishes\n")[1].splitlines()[0]
    assert len(line) < 400
    assert "recall x" in line
    monkeypatch.setenv("LOOM_BUDGET_BYTES", str(len(prompt.encode()) + 100))
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    assert len(output.getvalue().encode()) <= len(prompt.encode()) + 100
    assert ident in output.getvalue()


def test_room_cannot_use_home_to_accept_as_person(ledger):
    home, seat = ledger
    ident = heard(home, seat, "Own wish")
    before = read_facts(home)
    for room_args in ([], ["--room", str(seat)]):
        result = cli(["wish", ident, "--home", str(home.root), *room_args,
                      "--accept", "word"], env={"BRNRD_ROOM": str(seat)})
        assert result.returncode == 1
        assert "--home is not allowed" in result.stderr
    assert read_facts(home) == before
    result = cli(["wish", ident, "--read", "Resident reading"],
                 env={"BRNRD_ROOM": str(seat)})
    assert result.returncode == 0, result.stderr
    reading = next(f for f in read_facts(home) if f.kind == "wish.read")
    assert reading.by == "strand:s-aaaa-test"
    assert reading.data["by"] == "resident"


def test_failed_port_write_does_not_stamp_wish(ledger, monkeypatch):
    from brr.loom.runtime import port

    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please reply")
    before = read_facts(home)

    def fail(*args):
        raise OSError("port unavailable")

    monkeypatch.setattr(port, "write_port", fail)
    with pytest.raises(OSError, match="port unavailable"):
        port.write_send(seat, to="thread:other", sender="s-aaaa-test",
                        re=letter, wish="new:Unsent reply", body="reply")
    assert read_facts(home) == before
    assert not (seat / "port/out").exists()


def test_wake_caps_rows_with_asks_first(ledger):
    home, seat = ledger
    for i in range(70):
        append(home, Fact(kind="wish", by="strand:s-aaaa-test", id=f"w-{i:03}", data={
            "wish": f"w-{i:03}", "says": [["s-aaaa-test/x", 0, None]], "from": "resident" if i == 69 else "person",
            "line": "x" * 160, "thread": "work", **({"parent": "w-000"} if i == 69 else {})}))
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    section = output.getvalue().split("## Wishes\n")[1].split("## Notes")[0]
    lines = [line for line in section.strip().splitlines() if not line.startswith("  ")]
    assert lines[0].startswith("w-069 ·")
    assert len(lines) == 21
    assert lines[-1] == "+50 more · wishes --thread work"
    assert len(command(home, seat, "wishes").splitlines()) == 72


def delivered_wish(home, seat, line="Delivered work"):
    ident = heard(home, seat, line)
    command(home, seat, "wish", ident, "--result", "pr:https://example.com/1",
            "--line", "Ready", "--deliver")
    return ident


@pytest.mark.parametrize("retire", [False, True])
def test_settle_leaves_each_delivered_wish_delivered_on_the_closed_thread(ledger, retire):
    from brr.loom.runtime.lifecycle import read_thread

    home, seat = ledger
    ids = [delivered_wish(home, seat, str(i)) for i in range(2)]
    stamps = [f for f in read_facts(home) if f.kind == "wish"]
    command(home, seat, "settle", "work", "--why", "Finished", *(["--retire"] if retire else []))
    assert read_thread(home, "work")["status"] == ("retired" if retire else "settled")
    # Silence archives the thread, never accepts the wish: no verdict, no move.
    assert [f for f in read_facts(home) if f.kind == "wish"] == stamps
    assert not any(f.kind == "wish.verdict" for f in read_facts(home))
    for ident in ids:
        assert view(home, ident).state == "delivered"
        assert view(home, ident).thread == "work"


def test_settle_with_work_in_hand_still_needs_the_persons_word(ledger):
    from brr.loom.runtime.lifecycle import read_thread

    home, seat = ledger
    delivered_wish(home, seat)
    heard(home, seat, "Still heard")
    assert "--word" in command(home, seat, "settle", "work", "--why", "Finished", ok=False)
    assert read_thread(home, "work")["status"] == "open"


@pytest.mark.parametrize("verdict", [("--accept", "word"), ("--drop", "Cancelled")])
def test_settle_allows_delivered_wish_after_verdict(ledger, verdict):
    from brr.loom.runtime.lifecycle import read_thread

    home, seat = ledger
    ident = delivered_wish(home, seat)
    command(home, seat, "wish", ident, *verdict)
    command(home, seat, "settle", "work", "--word", word(home), "--why", "Resolved")
    assert read_thread(home, "work")["status"] == "settled"


def test_settle_hands_only_pending_delivered_wishes_to_new_thread(ledger):
    from brr.loom.runtime.lifecycle import read_thread

    home, seat = ledger
    ids = [delivered_wish(home, seat, str(i)) for i in range(2)]
    still = heard(home, seat, "Still heard")
    done = delivered_wish(home, seat, "Accepted")
    command(home, seat, "wish", done, "--accept", "word")
    command(home, seat, "settle", "work", "--word", word(home), "--why", "Review continues",
            "--hand-to", "other")
    assert read_thread(home, "work")["status"] == "settled"
    listing = command(home, seat, "wishes", "--thread", "other")
    assert all(ident in listing for ident in ids)
    assert still not in listing and done not in listing
    for ident in ids:
        assert view(home, ident).thread == "other"
        assert view(home, ident).state == "delivered"
        assert len(view(home, ident).results) == 1
    assert view(home, still).thread == view(home, done).thread == "work"
    moves = [f for f in read_facts(home) if f.kind == "wish" and f.data["thread"] == "other"]
    assert {f.data["wish"] for f in moves} == set(ids)
    assert all(f.by == "strand:s-aaaa-test" for f in moves)


@pytest.mark.parametrize("target", ["missing", "work"])
def test_invalid_settle_handoff_has_no_effect(ledger, target):
    from brr.loom.runtime.lifecycle import read_thread

    home, seat = ledger
    delivered_wish(home, seat)
    said = word(home)
    before = read_facts(home)
    assert "another thread on main" in command(
        home, seat, "settle", "work", "--word", said, "--why", "Done", "--hand-to", target, ok=False)
    assert read_facts(home) == before
    assert read_thread(home, "work")["status"] == "open"


def test_settle_without_wishes_still_settles(ledger):
    from brr.loom.runtime.lifecycle import read_thread

    home, seat = ledger
    command(home, seat, "settle", "work", "--word", word(home), "--why", "Done")
    assert read_thread(home, "work")["status"] == "settled"


def test_settle_judges_the_settled_childs_room_not_only_the_callers(ledger):
    from brr.loom.runtime.lifecycle import read_thread

    home, seat = ledger
    self_dir = home.root / "self"
    (self_dir / "threads/child").mkdir(parents=True)
    (self_dir / "threads/child/README.md").write_text(
        "---\nid: child\nstatus: open\ntense: plan\nopened-by: thread:work\n---\n# Child\n\nThe page.\n")
    (self_dir / "core/guards").mkdir(parents=True, exist_ok=True)
    (self_dir / "core/guards/notes.toml").write_text(
        'says = "notes.md stays as main has it."\nset-by = "letter:9"\n[[paths]]\nuntouched = ["notes.md"]\n')
    commit(self_dir, "child and guard")
    kid = room(home.root, "s-aaaa-kid")
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": "child", "strand": "s-aaaa-kid", "gen": 1,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))
    (kid / "notes.md").write_text("drift\n")
    error = command(home, seat, "settle", "child", "--why", "Done", ok=False)
    assert "s-aaaa-kid: guard notes" in error and "notes.md changed" in error
    assert read_thread(home, "child")["status"] == "open"
    (kid / "notes.md").unlink()
    command(home, seat, "settle", "child", "--why", "Done")
    assert read_thread(home, "child")["status"] == "settled"


@pytest.mark.parametrize("verdict", [None, "accepted", "dropped"])
@pytest.mark.parametrize("person", [False, True])
def test_bare_wish_is_a_read_even_when_closed(ledger, verdict, person):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Keep this wish")
    ident = command(home, seat, "wish", "new", "--line", "Keep this wish", "--say", letter)
    if verdict == "accepted":
        command(home, seat, "wish", ident, "--accept", "word", person=True)
    elif verdict == "dropped":
        command(home, seat, "wish", ident, "--drop", "No longer needed")
    before = read_facts(home)
    output = command(home, seat, "wish", ident, person=person)
    assert read_facts(home) == before
    assert output == f"{ident} · {verdict or 'heard'} · Keep this wish · -"
    assert "no wish" in command(home, seat, "wish", "w-missing", person=person, ok=False)
    assert read_facts(home) == before


def test_a_wish_born_with_no_citation_is_only_a_person_s_own():
    """A strand's new wish cites what was said; a person writing at their own
    terminal (setup's onboarding wish) is the citation, signed as them."""
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.registry import admit
    data = {"wish": "w-aaaaaaaaaaaa", "says": [], "from": "person", "line": "x", "thread": "inbox"}
    admit(Fact(kind="wish", by="person:ada", id="w-aaaaaaaaaaaa", data=data))
    with pytest.raises(ValueError, match="needs --say"):
        admit(Fact(kind="wish", by="strand:s-1", id="w-aaaaaaaaaaaa", data=data))
