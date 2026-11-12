"""A thread's plan is its wishes; a reply to a person letter is told when it steered none."""
from types import SimpleNamespace

import pytest

from brr.loom.runtime import snapshot
from brr.loom.runtime.asks import asks
from brr.loom.runtime.config import load_config
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.lifecycle import main_snapshot, parse_plan
from brr.loom.runtime.loom import _boundary
from brr.loom.runtime.verbs_threads import _threads
from brr.loom.runtime.wishes import progress, wishes
from test_wake import cli, commit, write_thread
from test_wishes import command, heard, ledger  # noqa: F401  (fixture)

STRAND = "s-aaaa-test"


@pytest.mark.parametrize("caller", ["told", "plan"])
def test_only_a_loom_start_delivers_a_cached_merged_wish(ledger, monkeypatch, caller):
    from brr.daemon2.facts import Fact
    from brr.loom.runtime import receipts
    from brr.loom.runtime.ledger import LedgerView
    from brr.loom.runtime.loom import _plan
    from brr.loom.runtime.selfrepo import git
    from brr.loom.runtime.wishes import told

    home, seat = ledger
    source = home.root / "source"
    source.mkdir()
    git(source, "init")
    git(source, "commit", "--allow-empty", "-m", "Ship the switch (#7)")
    merged = git(source, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(receipts, "source_root", lambda: source)
    ident = heard(home, seat, "Ship the switch")
    command(home, seat, "wish", ident, "--result", "pr:7", "--line", "Switch merged")
    facts = LedgerView(read_facts(home))

    def read():
        return told(facts)[ident].state if caller == "told" else _plan(home, "work", facts)

    before = read()
    if caller == "told":
        assert before == "granting"
    else:
        assert "plan 0/1" in before
    count = len(facts)
    facts.append(Fact(kind="loom.started", by="loom:aaaa", data={"commit": merged}))
    assert len(facts) == count + 1
    assert [f.kind for f in facts.delta] == ["loom.started"]
    assert not facts.reset
    assert read() == ("delivered" if caller == "told" else "plan 1/1")
    assert told(facts)[ident].results[0]["held"] == merged


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
    command(home, seat, "wish", ident, "--result", "url:https://example.com/1", "--line", "Done", "--deliver")


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
    assert "work · s-aaaa-test · plan 2/2" in asks(read_facts(home), main_snapshot(home), "work", install=home.install_id())


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


def test_a_person_letter_carries_no_standing_steers(ledger):
    home, seat = ledger
    heard(home, seat, "Hovered column holds still")
    inject_letter(home.root, to="thread:work", body="also when the sheet is open")
    shown = boundary(home)
    assert "steers?" not in shown and "asks?" not in shown


def test_a_reply_that_touched_no_wish_says_so_once_and_names_what_the_letter_points_at(ledger):
    """His 10095: feedback after the act, never a list shown before it."""
    home, seat = ledger
    old = heard(home, seat, "Hovered column holds still")
    other = heard(home, seat, "Overlay opens")
    letter = inject_letter(home.root, to="thread:work", body="the hovered column still jumps")
    sent = cli(["send", "--room", str(seat), "--to", "thread:other", "--re", letter, "--chatter", "ok"])
    assert sent.returncode == 0, sent.stderr
    line = next(row for row in sent.stderr.splitlines() if "touched no wish" in row)
    assert line.startswith(f'{letter} touched no wish; its words point at {old} "Hovered column holds still"')
    assert other not in line and f"wish <w-id> --say {letter}" in line
    # A letter that points at nothing is still told, without a list.
    plain = inject_letter(home.root, to="thread:work", body="thanks")
    quiet = cli(["send", "--room", str(seat), "--to", "thread:other", "--re", plain, "--chatter", "ok"])
    assert f"{plain} touched no wish\n" in quiet.stderr
    # A reply that steers a wish touched one: nothing to say.
    steer = inject_letter(home.root, to="thread:work", body="hovered column again")
    stamped = cli(["send", "--room", str(seat), "--to", "thread:other", "--re", steer, "--wish", old, "ok"])
    assert stamped.returncode == 0 and "touched no wish" not in stamped.stderr


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
    assert parse_plan(text) == [(None, "Read the ledger · re: w-a"), (True, "Old tick"), (False, "Old open")]
