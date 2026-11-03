"""A body meets the topic page its act needs: at wake, in related:, in recall, in INDEX.md, in the trace."""
import json
import os
import subprocess

from brr.loom.runtime import selfrepo, tooltrace
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import inject_letter
from test_index import run_index
from test_wake import cli, commit, write_thread
from test_wishes import ledger  # noqa: F401  (fixture)

FUEL = """# Fuel: reading each shell's quota

A wake shows session and week quota per harness in one status line.

### 2026-10-28 · The wake shows fuel as one status line · [fuel-display](../threads/fuel-display/README.md)
Chose: session % and week % per harness. Why: bodies ended turns on a stale quota guess.

### 2026-10-21 · A thread carries a token allocation · [track-quota](../threads/track-quota/README.md)
Chose: an allocation per thread, warned past, never stopped.
"""
SITE = """# Site: brnrd.dev

Entries newest first.

### 2026-10-30 · The landing page is a greybox beside the dashboard · [web-main](../threads/web-main/README.md)
Chose: the old app moves to /dashboard.
"""


def _topics(self_dir):
    (self_dir / "topics").mkdir(exist_ok=True)
    (self_dir / "topics" / "README.md").write_text("# Topics: what was decided, and why\n\nThe shape.\n")
    (self_dir / "topics" / "fuel.md").write_text(FUEL)
    (self_dir / "topics" / "site.md").write_text(SITE)


def _wake(clone, thread, letter, tmp_path, **env):
    owed = tmp_path / "owed.json"
    owed.write_text(json.dumps([{"id": "l1", "from": "inbox", "body": letter}]))
    part = tmp_path / "part"
    part.write_text("loom part\n")
    out = tmp_path / "out"
    old = dict(os.environ)
    os.environ.update(env)
    try:
        with out.open("w", encoding="utf-8") as stdout:
            assert selfrepo.run_wake(clone, thread, owed, loom_part=part, stdout=stdout) == 0
    finally:
        os.environ.clear()
        os.environ.update(old)
    return out.read_text(encoding="utf-8")


def test_the_wake_shows_the_closest_head_before_the_tree_and_nothing_below_the_floor(ledger, tmp_path):
    home, seat = ledger
    clone = seat / "self"
    _topics(clone)
    letter = "The wake should show the session and week quota per harness, fuel as one status line"
    text = _wake(clone, "work", letter, tmp_path)
    block = text[text.index("## Topics\n"):text.index("## Tree\n")]
    assert "topics/fuel.md · Fuel: reading each shell's quota (2 entries)" in block
    assert "newest: 2026-10-28 · The wake shows fuel as one status line" in block
    assert "fuel-display/README.md" not in block and "site.md" not in block
    assert len(block.encode()) <= 600
    # An occasion no page is close to costs the wake nothing.
    assert "## Topics" not in _wake(clone, "work", "Rename the pancake recipe", tmp_path)


def test_a_thread_names_its_own_pages_and_their_bytes_come_out_of_the_budget(ledger, tmp_path):
    home, seat = ledger
    clone = seat / "self"
    _topics(clone)
    readme = clone / "threads" / "work" / "README.md"
    readme.write_text(readme.read_text().replace("tense: plan\n", "tense: plan\ntopic: [site]\n"))
    for n in range(40):
        write_thread(clone, f"filler-{n:02d}", f"Filler thread number {n} with a long enough headline")
    commit(clone, "fill")
    loose = _wake(clone, "work", "Rename the pancake recipe", tmp_path)
    assert "this thread's topic pages" in loose and "topics/site.md · Site: brnrd.dev (1 entry" in loose
    assert "Entries newest first" not in loose
    tight = len(loose.encode()) - 200
    text = _wake(clone, "work", "Rename the pancake recipe", tmp_path, LOOM_BUDGET_BYTES=str(tight))
    assert "topics/site.md" in text
    # The tree gave way; only the recipe's own omission receipt sits outside the count.
    receipt = next(line for line in text.splitlines() if line.startswith("omitted "))
    assert len(text.encode()) - len(receipt) - 1 <= tight


def test_related_names_the_topic_page_before_the_threads(ledger):
    home, seat = ledger
    _topics(home.root / "self")
    commit(home.root / "self", "topics")
    letter = inject_letter(home.root, to="thread:work", body="Please")
    result = cli(["wish", "--room", str(seat), "new", "--line",
                  "The wake shows each harness's session and week quota as one fuel status line", "--say", letter])
    assert result.returncode == 0, result.stderr
    assert "related: topics/fuel.md (2)" in result.stderr
    assert "closest topic pages, then threads" in result.stderr


def test_recall_prints_heads_then_threads(ledger):
    home, seat = ledger
    _topics(home.root / "self")
    write_thread(home.root / "self", "fuel-display", "The wake shows session and week quota as fuel")
    commit(home.root / "self", "topics")
    result = cli(["recall", "--room", str(seat), "session and week quota fuel at wake"])
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("topics/fuel.md · Fuel")
    assert lines[1] == "A wake shows session and week quota per harness in one status line."
    assert lines[-1].startswith("threads: fuel-display")
    nothing = cli(["recall", "--room", str(seat), "pancakes"])
    assert nothing.returncode == 0 and nothing.stdout.startswith("nothing shares two words")


def test_the_index_lists_topic_pages_first(tmp_path):
    repo = tmp_path / "self"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _topics(repo)
    write_thread(repo, "web-main", "brnrd.dev's main page")
    commit(repo, "seed")
    out = run_index(repo)
    assert out.returncode == 0, out.stderr
    text = out.stdout
    assert text.index("## topics (2)") < text.index("## threads")
    assert "[topics/README.md](topics/README.md)" in text
    assert "- **[fuel](topics/fuel.md)** · Fuel: reading each shell's quota · 2 entries · " in text
    assert "- **[site](topics/site.md)** · Site: brnrd.dev · 1 entry · " in text
    assert "**[README]" not in text


def test_the_trace_records_a_read_under_the_self_by_its_self_path(tmp_path):
    room = tmp_path / "rooms" / "s-ab12-aaaaaa"
    (room / "port").mkdir(parents=True)
    (room / "self" / "core" / "loom").mkdir(parents=True)
    calls = [
        {"tool_name": "Read", "tool_input": {"file_path": str(room / "self" / "threads" / "wiki" / "design.md")}},
        {"tool_name": "Read", "tool_input": {"file_path": "topics/fuel.md"}, "cwd": str(room / "self")},
        {"tool_name": "Read", "tool_input": {"file_path": str(room / "brnrd" / "README.md")}},
        {"tool_name": "Read", "tool_input": {"file_path": str(room / "self" / ".." / "brnrd" / "x.md")}},
    ]
    for n, call in enumerate(calls):
        execute("pre", room, json.dumps(dict(call, tool_use_id=f"t{n}")))
        execute("post", room, json.dumps(dict(call, tool_use_id=f"t{n}", duration_ms=5)))
    rows = tooltrace.read(room)
    assert [(r["arg"], r["path"]) for r in rows] == [
        ("design.md", "threads/wiki/design.md"), ("fuel.md", "topics/fuel.md"), ("README.md", ""), ("x.md", ""),
    ]
