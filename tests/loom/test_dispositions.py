"""A person letter gets a disposition (a wish say or chatter) before a reply counts: through send."""
import os
import subprocess

from brr.daemon2.facts import Fact
from brr.loom.runtime.dispositions import disposed, place
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.port import fact_from_port, parse_frontmatter
from brr.loom.runtime.wishes import Say, wishes
from test_wake import cli
from test_wishes import ledger  # noqa: F401  (fixture)

BODY = "Hi! Please make the board show, for each letter, WHERE it is being worked on.\nThanks"


def send(seat, *args):
    return cli(["send", "--room", str(seat), "--to", "thread:other", *args])


def out_file(seat, letter_id):
    return seat / "port/out" / (letter_id.split("/")[1] + ".md")


def by_line(home, line):
    return next(w for w in wishes(read_facts(home)).values() if w.line == line)


def test_reply_to_a_person_letter_needs_a_disposition(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body=BODY)
    refused = send(seat, "--re", letter, "on it")
    assert refused.returncode == 1 and "no disposition yet" in refused.stderr
    assert not list((seat / "port/out").glob("*.md"))
    noted = send(seat, "--re", letter, "--note", "seen")
    assert noted.returncode == 1 and "--chatter" in noted.stderr

    chatted = send(seat, "--re", letter, "--chatter", "thanks")
    assert chatted.returncode == 0, chatted.stderr
    sent = chatted.stdout.strip()
    assert chatted.stdout == sent + "\n"
    assert "chatter ← (" + letter.split("/")[1] + ")" in chatted.stderr
    fm = parse_frontmatter(out_file(seat, sent).read_text())
    assert fm["chatter"] == letter
    # Unsent, the port file already disposes; ingested, the letter fact does.
    assert letter in disposed(read_facts(home), seat)
    again = send(seat, "--re", letter, "and a second reply")
    assert again.returncode == 0, again.stderr
    fact = fact_from_port(fm, "s-aaaa-test", 1)
    assert fact.data["chatter"] == [letter]
    assert letter in disposed([fact])
    # --chatter=<ids> cites other letters too; a bare --chatter never eats the body.
    other = inject_letter(home.root, to="thread:work", body="ok thanks")
    both = send(seat, "--re", other, f"--chatter={other},{letter}", "noted")
    assert both.returncode == 0, both.stderr
    fm = parse_frontmatter(out_file(seat, both.stdout.strip()).read_text())
    assert (fm["chatter"], fm["body"].strip()) == (f"{other} {letter}", "noted")


def test_a_resident_letter_is_not_gated(ledger):
    home, seat = ledger
    append(home, Fact(kind="letter", by="strand:s-bbbb-x", id="s-bbbb-x/aaaaa", data={
        "id": "s-bbbb-x/aaaaa", "from": "s-bbbb-x", "to": "thread:work", "body": "fyi", "gen": 1}))
    assert send(seat, "--re", "s-bbbb-x/aaaaa", "ok").returncode == 0


def test_says_are_loose_quotes_and_never_refused(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body=BODY)
    other = inject_letter(home.root, to="thread:work", body="Also: keep it lean")
    done = send(seat, "--re", letter, "--wish", "new:Board shows where",
                "--says", "where   it is being worked ON", "--says", "a quote nowhere in it",
                "--says", other + ":keep it, LEAN", "on it")
    assert done.returncode == 0, done.stderr
    w = by_line(home, "Board shows where")
    start = BODY.index("WHERE")
    assert w.says[0] == Say(letter, start, len("WHERE it is being worked on"))
    assert w.says[1] == Say(letter, 0, None, "a quote nowhere in it")
    assert w.says[2] == Say(other, "Also: keep it lean".index("keep"), len("keep it lean"))
    # The ledger holds records; the fold reads them back as the same says.
    [fact] = [f for f in read_facts(home) if f.kind == "wish" and f.data["wish"] == w.id]
    assert fact.data["says"] == [say.row() for say in w.says]
    assert fact.data["says"][1] == {"letter": letter, "offset": 0, "quote": "a quote nowhere in it"}
    assert '"WHERE it is being worked on"' in done.stderr
    assert '"a quote nowhere in it"?' in done.stderr


def test_says_name_a_colon_letter_id_by_its_last_segment(ledger):
    home, seat = ledger
    ident = "letter:mine:42:board-abc123"
    append(home, Fact(kind="letter", by="loom:aaaa", id=ident, data={
        "id": ident, "from": "person:test", "to": "thread:work", "body": "ship: the gate first"}))
    done = send(seat, "--re", ident, "--wish", "new:Gate first", "--says", "board-abc123:ship: the gate", "ok")
    assert done.returncode == 0, done.stderr
    assert by_line(home, "Gate first").says == [Say(ident, 0, len("ship: the gate"))]
    # The prefix names no letter: the whole value is a quote of the --re letter.
    done = send(seat, "--re", ident, "--wish", "new:Gate", "--says", "ship: the gate", "ok")
    assert by_line(home, "Gate").says == [Say(ident, 0, len("ship: the gate"))]


def test_refs_and_answered_bind_to_the_preceding_wish(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body=BODY)
    done = send(seat, "--re", letter,
                "--wish", "new:First", "--ref", "pr:2390", "--ref", f"pid:{os.getpid()}",
                "--wish", "new:Second", "--answered",
                "Here is the answer.\nMore")
    assert done.returncode == 0, done.stderr
    sent = done.stdout.strip()
    first, second = by_line(home, "First"), by_line(home, "Second")
    assert [(r["kind"], r["ref"], r["delivered"]) for r in first.results] == [
        ("pr", "2390", False), ("pid", str(os.getpid()), False)]
    assert "pytest" in first.results[1]["cmd"] or "python" in first.results[1]["cmd"]
    assert first.state == "granting" and first.open
    assert [(r["kind"], r["ref"], r["delivered"], r["line"]) for r in second.results] == [
        ("letter", sent, True, "Here is the answer.")]
    assert second.state == "delivered" and second.open
    assert f"{first.id} ← ({letter.split('/')[1]}) → refs: pr:2390, pid:{os.getpid()}" in done.stderr
    assert f"{second.id} ← ({letter.split('/')[1]}) → refs: answered" in done.stderr


def test_a_ref_before_any_wish_stays_the_clean_ref(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body=BODY)
    done = send(seat, "--re", letter, "--clean", "--kind", "done", "--ref", "README.md",
                "--wish", "new:Clean", "--ref", "pr:7")
    assert done.returncode == 0, done.stderr
    fm = parse_frontmatter(out_file(seat, done.stdout.strip()).read_text())
    assert fm["refs"] == "README.md"
    assert [r["ref"] for r in by_line(home, "Clean").results] == ["7"]


def test_bad_refs_and_unbound_flags_refuse_and_write_nothing(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body=BODY)
    dead = subprocess.Popen(["true"])
    dead.wait()
    for args in (["--wish", "new:X", "--ref", f"pid:{dead.pid}"],
                 ["--wish", "new:X", "--ref", "no-kind"],
                 ["--says", "a quote", "--wish", "new:X"],
                 ["--wish", "new:X", "--note", "n", "--answered"],
                 ["--chatter=p-test/nope"]):
        result = send(seat, "--re", letter, *args, *([] if "--note" in args else ["words"]))
        assert result.returncode != 0, args
    assert not list((seat / "port/out").glob("*.md"))
    assert not [w for w in wishes(read_facts(home)).values() if w.line == "X"]


def test_place_ignores_case_whitespace_and_punctuation():
    assert place("Hello,  World!", "hello world") == (0, len("Hello,  World"))
    assert place("abc", "...") is None
