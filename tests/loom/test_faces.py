"""A fact proposes a face at the boundary; the body takes it, wears another, or lets it lapse."""

import json

from brr.loom.runtime.faces import face_of, faces
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import Fact, append, inject_letter
from brr.loom.runtime.selfrepo import init_self

import pytest

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies


@pytest.mark.parametrize("raw", ["brnrd", "brvrd", "br^rd", "b^n^d", "b n d", "b^‿·d", "focused", "b^‿^d!"])
def test_a_face_is_the_face_itself_and_the_wordmark_never_moves(raw):
    with pytest.raises(ValueError, match="give the face itself"):
        face_of(raw)


def test_a_fact_proposes_the_body_decides_a_lapse_keeps_the_last_and_a_successor_wakes_faceless(tmp_path):
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "Work\n\n## Now", "work", wait="0")
    write_thread(root, "other", "Other", "other", wait="0")
    letter = inject_letter(root, to="thread:work", body="first ask")
    seen, woke, taken = root / "seen.json", root / "woke.json", root / "taken"
    bodies(root, f'''import json, subprocess, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.faces import faces
from brr.loom.runtime.home import atomic_write, home_of_room, strand_of_room
from brr.loom.runtime.ledger import Fact, read_facts
from brr.loom.runtime.project import fold
from brr.loom.runtime.router import _header
ctx = Ctx(Path(sys.argv[1]))
import traceback
sys.excepthook = lambda *e: Path({str(seen)!r} + ".err").write_text("".join(traceback.format_exception(*e)))
boundary = ctx.room / "port" / "in" / "boundary.md"
def face(*args):
    return subprocess.run([sys.executable, "-m", "brr.loom.runtime", "face", "--room", str(ctx.room), *args],
                          capture_output=True, text=True, env=ctx.env, cwd=ctx.room)
def until(test):
    for _ in range(300):
        text = boundary.read_text()
        if test(text):
            return text
        time.sleep(0.05)
    Path({str(seen)!r} + ".err").write_text(text)
    raise SystemExit("boundary never matched:\\n" + text)
def proposed(text):
    return [line for line in text.splitlines() if line.startswith("face? ")]
me = Fact(kind="letter", by="strand:" + strand_of_room(ctx.room))
if "on thread other" in ctx.wake():
    for letter in ctx.owed():
        ctx.send("thread:other", note="read", re=letter.id)
    ctx.jack("stop")
elif "carry on" not in ctx.wake():
    letters = ctx.owed()
    asked = until(proposed)
    wordmark = face("br^rd")
    named = face("focused")
    took = face("take")
    ctx.jack("post")
    early = face("take")
    for letter in letters:
        ctx.send("thread:work", note="faced", re=letter.id)
    lapsed = until(lambda text: not proposed(text))
    # The note lands through the port; a merge stamped before it would lapse at once.
    while not any(f.kind == "note" and f.by == me.by for f in read_facts(home_of_room(ctx.room))):
        time.sleep(0.05)
    Path({str(taken)!r}).write_text("1")
    for _ in range(300):
        # The body meets a proposal the way it meets a letter: at a post boundary.
        raw = ctx.jack("post").raw
        merged = json.loads(raw)["hookSpecificOutput"]["additionalContext"] if raw else ""
        if "face? b^‿^d" in merged:
            break
        time.sleep(0.05)
    again = ctx.jack("post").raw
    ctx.send("thread:other", "carrying on", cite=letters[0].id)
    after = until(lambda text: not proposed(text))
    facts = read_facts(home_of_room(ctx.room))
    atomic_write(Path({str(seen)!r}), json.dumps({{
        "early": [early.returncode, early.stderr], "asked": proposed(asked),
        "wordmark": [wordmark.returncode, wordmark.stderr], "named": [named.returncode, named.stderr],
        "took": took.stdout, "lapsed": proposed(lapsed), "merged": proposed(merged),
        "after": proposed(after), "again": again, "faces": faces(facts), "header": _header(fold(facts), me, facts)}}))
    ctx.molt("carry on")
    assert ctx.jack("stop").raw == ""
else:
    ctx.owed()
    time.sleep(0.5)
    facts = read_facts(home_of_room(ctx.room))
    atomic_write(Path({str(woke)!r}), json.dumps({{
        "faces": faces(facts), "boundary": proposed(boundary.read_text()),
        "header": _header(fold(facts), me, facts)}}))
''')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(taken.exists, 30, loom.dump)
        # A merge on this thread is a fact the body just met; publish writes this shape (test_receipts).
        [strand] = [f.data["strand"] for f in loom.facts() if f.kind == "lease"]
        append(Home(root), Fact(kind="pr.merged", by=f"strand:{strand}", id="pr.merged:work:brnrd:7",
                                data={"thread": "work", "repo": "brnrd", "number": 7,
                                      "head": "h", "merge_commit": "m"}))
        wait_until(seen.exists, 30, loom.dump)
        result = json.loads(seen.read_text())
        # Once decided, the next boundary proposes nothing, and take has nothing to wear.
        assert result["early"][0] == 1 and "nothing is proposed" in result["early"][1]
        short = letter.rpartition("/")[2]
        assert result["asked"] == [f"face? b°O°d for your {short} ({letter}): `brnrd runtime "
                                   "face take` wears it, `face <face>` wears another, your next letter lets it lapse"]
        assert result["wordmark"][0] == 1 and "give the face itself" in result["wordmark"][1]
        assert result["named"][0] == 1 and "give the face itself" in result["named"][1]
        assert result["took"] == "face b°O°d\n"
        assert result["lapsed"] == []
        assert result["merged"][0].startswith("face? b^‿^d for #7 merged (pr.merged:work:brnrd:7)")
        # Shown once: the next post has nothing new to say.
        assert result["again"] == ""
        # Lapsed: the body wrote its next letter without deciding; the last decided face stays.
        assert result["after"] == []
        assert result["faces"]["work"]["face"] == "b°O°d"
        assert result["header"] == "b°O°d ⌁ [ work ] · #7 merged"
        wait_until(woke.exists, 30, loom.dump)
        successor = json.loads(woke.read_text())
        # A new gen is a state, not a change: no proposal, no face, no voice.
        assert successor == {"faces": {}, "boundary": [], "header": "b·_·d ⌁ [ work ]"}
        written = [f.data["face"] for f in loom.facts() if f.kind == "face"]
        assert written == ["b°O°d"]
    finally:
        loom.halt()
    assert not loom.errors
