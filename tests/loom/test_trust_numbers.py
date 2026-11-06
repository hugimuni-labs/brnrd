"""The two trust numbers (threads/two-numbers): each ask's answer as took, changed, never or lapsed,
the calibrate table per kind, and irreversible acts without a grant, through the verbs and the jack."""
import json

from brr.loom.runtime import trust
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import inject_letter, read_facts
from test_never_guards import _room
from test_waits import later, told
from test_wake import cli
from test_wishes import command, heard, ledger, view  # noqa: F401  (ledger is a fixture)


def answers(home):
    return [f.data for f in read_facts(home) if f.kind == "ask.answer"]


def test_a_default_that_runs_out_writes_the_ask_lapsed_once(ledger):
    home, seat = ledger
    root = heard(home, seat, "Decide")
    ask = command(home, seat, "ask", "Merge #12?", "--for", root, "--rec", "yes",
                  "--kind", "merge", "--default-at", "+1h")
    assert view(home, ask).kind == "merge"
    assert told(home) == [] and answers(home) == []
    [letter] = told(home, now=later(hours=2))
    assert told(home, now=later(hours=3)) == []
    [fact] = answers(home)
    assert (fact["wish"], fact["as"]) == (ask, "lapsed")
    [row] = trust.calibrate(read_facts(home)).values()
    assert (row.asks, row.n("lapsed"), row.answered) == (1, 1, 0)
    table = command(home, seat, "asks", "--calibrate", "--days", "7")
    assert table.startswith("7d, asks with a rec") and "merge" in table and "100%" in table
    assert "1 lapsed" in command(home, seat, "trust", "--days", "14")


def test_a_reply_is_unread_until_the_body_says_took_or_changed(ledger):
    home, seat = ledger
    root = heard(home, seat, "Decide")
    first = command(home, seat, "ask", "Settle web-main?", "--for", root, "--rec", "settle it", "--kind", "settle")
    second = command(home, seat, "ask", "Settle landing?", "--for", root, "--rec", "settle it", "--kind", "settle")
    reply = inject_letter(home.root, to="thread:work", body="yes", re=first)
    inject_letter(home.root, to="thread:work", body="no, keep it open", re=second)
    assert trust.outcomes(read_facts(home))[first][0] == "unread"
    assert "unread" in command(home, seat, "asks", "--calibrate")
    command(home, seat, "wish", first, "--answer", "took")
    command(home, seat, "wish", second, "--answer", "changed")
    assert [(a["wish"], a["as"], a["ref"]) for a in answers(home)][0] == (first, "took", reply)
    [row] = trust.calibrate(read_facts(home)).values()
    assert (row.asks, row.answered, row.n("took"), row.n("changed"), len(row.ages)) == (2, 2, 1, 1, 2)
    line = command(home, seat, "trust")
    assert "took-rec 50% of 2 answered (settle 50% of 2)" in line and "0 unread" in line


def test_an_answer_needs_an_ask_with_a_rec_and_a_reply(ledger):
    home, seat = ledger
    root = heard(home, seat, "Decide")
    bare = command(home, seat, "ask", "Which colour?", "--for", root)
    assert "has no rec" in command(home, seat, "wish", bare, "--answer", "took", ok=False)
    assert "is not an ask" in command(home, seat, "wish", root, "--answer", "took", ok=False)
    waiting = command(home, seat, "ask", "Publish?", "--for", root, "--rec", "yes", "--kind", "publish")
    assert "no reply is bound" in command(home, seat, "wish", waiting, "--answer", "took", ok=False)
    assert cli(["ask", "x", "--room", str(seat), "--for", root, "--kind", "dance"]).returncode == 2
    # A person's verdict on the ask is their answer: accepted takes the rec.
    command(home, seat, "wish", waiting, "--accept", "word", person=True)
    assert trust.outcomes(read_facts(home))[waiting][0] == "took"
    # An ask without a kind still counts, under its own row.
    nokind = command(home, seat, "ask", "Spend?", "--for", root, "--rec", "yes")
    assert view(home, nokind).kind == ""
    assert "(no kind)" in trust.calibrate(read_facts(home))


def test_refusals_grants_and_misses_make_the_acts_number(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room, home = _room(tmp_path)
    said = json.loads(execute("pre", room, json.dumps({
        "tool_name": "Bash", "tool_input": {"command": "gh pr merge 2610 --squash"}, "cwd": str(room)})))
    assert said["hookSpecificOutput"]["permissionDecision"] == "deny"
    [refused] = [f.data for f in read_facts(home) if f.kind == "guard.refused"]
    assert (refused["class"], refused["target"], refused["thread"]) == ("merge-red", "2610", "ta")
    counted = trust.ungranted(read_facts(home), home.root / "self")
    assert counted == {"misses": 0, "granted": 0, "refused": 1}
    assert trust.line(read_facts(home), home.root / "self").startswith(
        "14d: 0 irreversible acts without a grant (0 granted, 1 refused by the never guards)")


def test_a_miss_on_main_counts_and_reads_only_its_section(tmp_path):
    from test_guards import _repo
    _repo(tmp_path / "self", {"threads/ta/README.md": (
        "# ta\n\n## Misses\n- send:channel:x@L9: told Sasha, no letter asked\n\n## Notes\n- not a miss\n"),
        "threads/tb/README.md": "# tb\n\n## Notes\n- nothing\n"})
    assert trust.misses(tmp_path / "self") == [("ta", "- send:channel:x@L9: told Sasha, no letter asked")]
    # Its key names no letter in the ledger, so it cannot be dated: it counts.
    assert trust.ungranted([], tmp_path / "self")["misses"] == 1
