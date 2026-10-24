"""A thread's plan is its wishes; a person letter is asked which wish it steers."""
from types import SimpleNamespace

from brr.loom.runtime import snapshot
from brr.loom.runtime.asks import asks
from brr.loom.runtime.config import load_config
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.lifecycle import main_snapshot, parse_plan
from brr.loom.runtime.loom import _boundary
from brr.loom.runtime.verbs_threads import _threads
from brr.loom.runtime.wishes import progress, wishes
from test_wake import commit, write_thread
from test_wishes import command, heard, ledger  # noqa: F401  (fixture)

STRAND = "s-aaaa-test"


def boundary(home):
    (home.room(STRAND) / "port/in").mkdir(parents=True, exist_ok=True)
    _boundary(home, read_facts(home), STRAND, 1, SimpleNamespace(thread="work", fuel="", why=""))
    return (home.room(STRAND) / "port/in/boundary.md").read_text()


def status(home):
    return boundary(home).splitlines()[1]


def table(home, capsys):
    capsys.readouterr()
    assert _threads(["--home", str(home.root)]) == 0
    lines = [line.split(" · ") for line in capsys.readouterr().out.splitlines()]
    return {line[0]: dict(zip(lines[0], line)) for line in lines[1:]}


def deliver(home, seat, ident):
    command(home, seat, "wish", ident, "--result", "pr:https://example.com/1", "--line", "Done", "--deliver")


def test_progress_is_delivered_and_accepted_over_person_wishes(ledger, capsys):
    home, seat = ledger
    # The work README holds a `- [ ] Build` row and no wishes: no plan.
    assert " · plan " not in status(home)
    assert table(home, capsys)["work"]["plan"] == "—"
    first = heard(home, seat, "First")
    second = heard(home, seat, "Second")
    assert status(home).endswith("plan 0/2 · next: First")
    deliver(home, seat, first)
    assert status(home).endswith("plan 1/2 · next: Second")
    assert table(home, capsys)["work"]["plan"] == "1/2"
    command(home, seat, "wish", first, "--accept", "word", person=True)
    dropped = heard(home, seat, "Dropped")
    command(home, seat, "wish", dropped, "--drop", "Not wanted")
    # An ask is the thread's question, not its plan.
    command(home, seat, "wish", "new", "--from", "resident", "--line", "Decide", "--parent", second)
    assert progress(wishes(read_facts(home)), "work") == {"done": 1, "total": 2, "next": "Second"}
    deliver(home, seat, second)
    assert status(home).endswith("plan 2/2")
    assert "work · s-aaaa-test · plan 2/2" in asks(read_facts(home), main_snapshot(home), "work")


def test_ticked_rows_count_for_nothing(ledger, capsys):
    home, seat = ledger
    write_thread(home.root / "self", "ticked", "Ticked\n\n## Plan\n- [x] One\n- [x] Two\n- [ ] Three")
    commit(home.root / "self", "ticked")
    assert table(home, capsys)["ticked"]["plan"] == "—"
    doc = snapshot.assemble(home, read_facts(home), load_config(home.root))
    assert "plan" not in next(t for t in doc["threads"] if t["id"] == "ticked")


def test_snapshot_plan_is_the_wishes(ledger):
    home, seat = ledger
    first = heard(home, seat, "Board holds still")
    heard(home, seat, "Overlay opens")
    deliver(home, seat, first)
    doc = snapshot.assemble(home, read_facts(home), load_config(home.root))
    work = next(t for t in doc["threads"] if t["id"] == "work")
    assert work["plan"] == {"done": 1, "total": 2, "next": "Overlay opens"}


def test_person_letter_shows_steers_until_disposed(ledger):
    home, seat = ledger
    old = heard(home, seat, "Hovered column holds still")
    letter = inject_letter(home.root, to="thread:work", body="also when the sheet is open")
    shown = boundary(home)
    assert f'steers? {old} "Hovered column holds still" · or new — send --re {letter} --wish <w-id>' in shown
    assert shown.index(letter) < shown.index("steers?")
    sent = command(home, seat, "send", "--to", "thread:other", "--re", letter, "--wish", old, "on it")
    assert sent
    assert "steers?" not in boundary(home)


def test_steers_names_three_newest_and_the_rest(ledger):
    home, seat = ledger
    ids = [heard(home, seat, f"Wish {i}") for i in range(5)]
    letter = inject_letter(home.root, to="thread:work", body="one more thing")
    line = next(row for row in boundary(home).splitlines() if "steers?" in row)
    assert [i for i in ids if i in line] == ids[2:]
    assert line.index(ids[4]) < line.index(ids[3]) < line.index(ids[2])
    assert "+2 more: wishes --thread work" in line and letter in line


def test_refusal_lists_the_open_wishes(ledger):
    home, seat = ledger
    old = heard(home, seat, "Hovered column holds still")
    letter = inject_letter(home.root, to="thread:work", body="and the sheet")
    refused = command(home, seat, "send", "--to", "thread:other", "--re", letter, "ok", ok=False)
    assert "no disposition yet; if it steers a wish on work: " in refused
    assert f'--wish {old} ("Hovered column holds still"); else --wish new:"<line>"' in refused
    assert "--chatter" in refused


def test_refusal_without_open_wishes_is_unchanged(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="hello")
    refused = command(home, seat, "send", "--to", "thread:other", "--re", letter, "ok", ok=False)
    assert 'carry one: --wish <w-id>|new:"<line>"' in refused and "steers" not in refused


def test_new_beside_open_wishes_echoes_the_steer(ledger):
    home, seat = ledger
    from test_wake import cli
    old = heard(home, seat, "Hovered column holds still")
    letter = inject_letter(home.root, to="thread:work", body="and the inspect sheet")
    sent = cli(["send", "--room", str(seat), "--to", "thread:other", "--re", letter,
                "--wish", "new:Sheet holds still", "ok"])
    assert sent.returncode == 0, sent.stderr
    new = next(w.id for w in wishes(read_facts(home)).values() if w.line == "Sheet holds still")
    assert f"new {new} beside open {old}: if it steers one, say so on it: wish {old} --say {letter}" in sent.stderr
    alone = inject_letter(home.root, to="thread:work", body="x")
    quiet = cli(["send", "--room", str(seat), "--to", "thread:other", "--re", alone, "--chatter", "ok"])
    assert "beside open" not in quiet.stderr


def test_parse_plan_reads_plain_rows():
    text = "# T\n\n## Plan\n- Read the ledger · re: w-a\n- [x] Old tick\n- [ ] Old open\n\n## Notes\n- not a row\n"
    assert parse_plan(text) == [(False, "Read the ledger · re: w-a"), (True, "Old tick"), (False, "Old open")]
