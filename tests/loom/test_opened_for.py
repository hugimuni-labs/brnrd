"""A thread is opened for a wish, and a wish is born for a letter or a parent: real verbs."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime.config import load_config
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.registry import UndeclaredFact
from brr.loom.runtime.selfrepo import git
from brr.loom.runtime import snapshot
from brr.loom.runtime.wishes import wishes
from test_wishes import command, ledger, view  # noqa: F401  (ledger is a fixture)

FIXTURE = Path(__file__).parent / "fixtures" / "live-wishes-2026-10-20.jsonl"
REFUSAL = '--wish <id> | --wish new:"<line>"'


def readme(home, thread):
    return git(home.root / "self", "show", f"main:threads/{thread}/README.md").stdout


def outbox(seat):
    out = seat / "port" / "out"
    return sorted(p.name for p in out.iterdir()) if out.exists() else []


def test_the_live_ledger_folds_whole_and_only_new_births_are_refused(ledger):
    home, seat = ledger
    shutil.copy(FIXTURE, home.facts_dir() / "live.jsonl")
    # A parentless birth that cites nothing, written before the rule: the
    # fold still reads it, since only append admits.
    with open(home.facts_dir() / "live.jsonl", "a") as handle:
        handle.write(json.dumps({"after": [], "at": "2026-10-01T00:00:00+00:00", "by": "strand:s-aaaa-old",
                                 "data": {"from": "person", "line": "old", "says": [], "thread": "work",
                                          "wish": "w-oldparentless"},
                                 "hlc": [1790000000000, 0, "aaaa"], "id": "w-oldparentless",
                                 "kind": "wish", "v": 1}) + "\n")
    told = wishes(read_facts(home))
    assert len(told) == 9
    assert "w-oldparentless" in command(home, seat, "wishes", "--thread", "work")
    live = {json.loads(line)["data"]["wish"] for line in FIXTURE.read_text().splitlines()}
    assert len(live) == 8 and live <= set(told)
    stderr = command(home, seat, "wish", "new", "--line", "orphan", ok=False)
    assert "--say <letter> or --parent <wish>" in stderr
    for raw in (Fact(kind="wish", by="strand:s-aaaa-test", id="w-raworphan", data={
                    "wish": "w-raworphan", "says": [], "from": "person", "line": "x", "thread": "work"}),
                Fact(kind="wish", by="strand:s-aaaa-test", data={
                    "wish": "w-autoid", "says": [], "from": "person", "line": "x", "thread": "work"})):
        try:
            append(home, raw)
        except UndeclaredFact:
            pass
        else:
            raise AssertionError(f"admitted {raw.id}")
    assert len(wishes(read_facts(home))) == 9


def test_an_open_without_a_wish_is_refused_and_leaves_nothing(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please split this out")
    before = [f.id for f in read_facts(home)]
    sent = outbox(seat)
    stderr = command(home, seat, "send", "--to", "thread:fresh", "--re", letter, "Fresh work", ok=False)
    assert REFUSAL in stderr
    assert "threads/fresh" not in git(home.root / "self", "ls-tree", "-r", "--name-only", "main").stdout
    assert outbox(seat) == sent
    assert [f.id for f in read_facts(home)] == before


def test_an_open_for_a_new_wish_cites_the_letter_and_the_readme_records_it(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please split this out")
    command(home, seat, "send", "--to", "thread:fresh", "--re", letter, "--wish", "new:Split it out", "Fresh work")
    behind = next(w for w in wishes(read_facts(home)).values() if w.line == "Split it out")
    assert (behind.says, behind.thread, behind.origin) == ([[letter, 0, None]], "fresh", "said")
    assert f"opened-for: [{behind.id}]\n" in readme(home, "fresh")
    assert "opened-by: thread:work\n" in readme(home, "fresh")


def test_an_open_with_nothing_to_cite_is_derived_from_the_opening_letter(ledger):
    home, seat = ledger
    sent = command(home, seat, "send", "--to", "thread:fresh", "--wish", "new:My own idea",
                   "--wish", "new:And another", "Fresh work")
    told = {w.line: w for w in wishes(read_facts(home)).values()}
    for line in ("My own idea", "And another"):
        assert told[line].says == [[sent, 0, None]]
        assert told[line].origin == "derived" and told[line].thread == "fresh"
    assert f"opened-for: [{told['My own idea'].id}, {told['And another'].id}]\n" in readme(home, "fresh")


def test_an_open_for_an_existing_wish_moves_it(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please do the thing")
    ident = command(home, seat, "wish", "new", "--line", "The thing", "--say", letter)
    assert view(home, ident).thread == "work"
    command(home, seat, "send", "--to", "thread:fresh", "--wish", ident, "Do the thing")
    moved = view(home, ident)
    assert (moved.thread, moved.says, moved.origin) == ("fresh", [[letter, 0, None]], "said")
    assert f"opened-for: [{ident}]\n" in readme(home, "fresh")
    stderr = command(home, seat, "send", "--to", "thread:other2", "--wish", "w-nosuchwish", "x", ok=False)
    assert "no wish w-nosuchwish" in stderr
    assert "threads/other2" not in git(home.root / "self", "ls-tree", "-r", "--name-only", "main").stdout


def test_a_wish_born_of_another_names_its_parent(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please build it")
    root = command(home, seat, "wish", "new", "--line", "Build it", "--say", letter)
    child = command(home, seat, "wish", "new", "--line", "Build the first half", "--parent", root)
    assert view(home, child).parent == root and view(home, child).origin == "derived"
    assert "no wish w-missing" in command(home, seat, "wish", "new", "--line", "x", "--parent", "w-missing", ok=False)


def test_snapshot_shows_why_a_thread_was_opened(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please split this out")
    command(home, seat, "send", "--to", "thread:fresh", "--re", letter, "--wish", "new:Split it out", "Fresh work")
    ident = next(w.id for w in wishes(read_facts(home)).values() if w.line == "Split it out")
    doc = snapshot.assemble(home, read_facts(home), load_config(home.root))
    fresh = next(t for t in doc["threads"] if t["id"] == "fresh")
    assert fresh["opened_by"] == "thread:work"
    assert fresh["opened_for"] == [{"wish": ident, "line": "Split it out", "state": "heard"}]
    assert "opened_for" not in next(t for t in doc["threads"] if t["id"] == "other")


def test_the_backfill_proposes_from_the_opening_letter_and_writes_nothing(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please take this to other")
    # What the loom writes when it carries `send --to thread:other --re <letter>`.
    append(home, Fact(kind="letter", by="strand:s-aaaa-test", id="s-aaaa-test/opens", data={
        "id": "s-aaaa-test/opens", "from": "s-aaaa-test", "to": "thread:other", "re": letter,
        "body": "Over to you", "gen": 1}))
    script = Path(__file__).parents[2] / "scripts" / "propose_thread_wishes.py"

    def state():
        self_repo = home.root / "self"
        return (git(self_repo, "rev-parse", "main").stdout, git(self_repo, "status", "--porcelain").stdout,
                sorted((p.name, p.read_bytes()) for p in home.facts_dir().iterdir()))

    before = state()
    proc = subprocess.run([sys.executable, str(script), "--home", str(home.root)],
                          text=True, capture_output=True, timeout=60,
                          env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")})
    assert proc.returncode == 0, proc.stderr
    assert state() == before
    lines = proc.stdout.splitlines()
    assert lines[lines.index("# other: derived from opening letter s-aaaa-test/opens") + 1] == (
        f"python -m brr.loom.runtime wish new --thread other --line 'Another thread' --say {letter}")
    command(home, seat, "wish", "new", "--line", "Built", "--say", letter, "--thread", "other")
    proc = subprocess.run([sys.executable, str(script), "--home", str(home.root)],
                          text=True, capture_output=True, timeout=60,
                          env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")})
    assert "--thread other" not in proc.stdout



LETTERS = Path(__file__).parent / "fixtures" / "live-letters-2026-10-20.jsonl"


def test_his_channel_letters_fold_as_his_say_and_a_loom_notice_does_not(ledger):
    """The connector writes his letters (by loom:<install>); the sender is data.from."""
    home, seat = ledger
    shutil.copy(FIXTURE, home.facts_dir() / "live.jsonl")
    shutil.copy(LETTERS, home.facts_dir() / "letters.jsonl")
    notice = Fact(kind="letter", by="loom:aaaa", id="notice:test",
                  data={"id": "notice:test", "from": "loom", "to": "thread:work", "body": "stuck"})
    append(home, notice)
    command(home, seat, "wish", "new", "--line", "unstick", "--say", "notice:test", "--thread", "work")
    told = wishes(read_facts(home))
    live = {json.loads(line)["data"]["wish"] for line in FIXTURE.read_text().splitlines()}
    assert {told[w].origin for w in live} == {"said"}
    assert next(w for w in told.values() if w.line == "unstick").origin == "derived"
    shown = command(home, seat, "wishes", "--all")
    assert "w-8rzhb6h7xyyz · " in shown and "unstick" in shown
    rows = [row for row in shown.splitlines() if any(w in row for w in live)]
    assert len(rows) == 8 and not any("derived" in row for row in rows)
    assert "derived" in next(row for row in shown.splitlines() if "unstick" in row)
