"""A thread's allocation: spend read from the harness, a warning past it, a raise by letter."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import loom, spend
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.port import fact_from_port, parse_boundary, parse_frontmatter, render_boundary
from brr.loom.runtime.selfrepo import init_self, room
from test_wake import cli, commit, write_thread

CHILD, PARENT = "s-aaaa-kid001", "s-aaaa-par001"


def _turn(ident, *, cache_read=0, cache_creation=0, output=0, inp=0, blocks=3):
    """One assistant message as Claude Code writes it: a row per content block."""
    usage = {"input_tokens": inp, "cache_read_input_tokens": cache_read,
             "cache_creation_input_tokens": cache_creation, "output_tokens": output}
    return "".join(json.dumps({"type": "assistant", "requestId": "r" + ident,
                               "message": {"id": ident, "usage": usage}}) + "\n" for _ in range(blocks))


def _transcripts(projects: Path, seat: Path) -> Path:
    folder = projects / re.sub(r"[^A-Za-z0-9]", "-", str(seat.resolve() / "self"))
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("BRNRD_ROOM", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(spend, "TTL", 0.0)
    spend._FILES.clear()
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root / "self", "parent", "The parent")
    folder = root / "self" / "threads" / "work"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text(
        "---\nid: work\nstatus: open\ntense: plan\nopened-by: thread:parent\n---\n# Work\n")
    commit(root / "self", "threads")
    home = Home(root, install="aaaa")
    append(home, Fact(kind="router", by="loom:aaaa", data={"gen": 1}))
    for strand, thread in ((CHILD, "work"), (PARENT, "parent")):
        room(root, strand)
        append(home, Fact(kind="lease", by="loom:aaaa", data={
            "thread": thread, "strand": strand, "gen": 1, "install": "aaaa",
            "router_gen": 1, "shell": "claude", "core": "opus", "repos": []}))
    return home


def _letter(home, sender, to, body, budget=None):
    data = {"id": f"{sender}/{len(read_facts(home)):05d}", "to": to, "from": sender, "body": body, "gen": 1}
    if budget is not None:
        data["budget"] = budget
    append(home, Fact(kind="letter", by=f"strand:{sender}", id=data["id"], data=data))


def test_claude_spend_counts_each_message_once_and_its_subagents(home, tmp_path):
    folder = _transcripts(tmp_path / "claude" / "projects", home.room(CHILD))
    session = folder / "s1.jsonl"
    session.write_text(_turn("m1", cache_read=100_000, output=1_000) + _turn("m2", cache_creation=8_000))
    (folder / "s1" / "subagents").mkdir(parents=True)
    (folder / "s1" / "subagents" / "agent-a.jsonl").write_text(_turn("m9", inp=4_000, blocks=2))
    # 100k*0.1 + 1k*5 + 8k*1.25 + 4k: the rows repeated per block count once.
    assert spend.claude_spent(home.room(CHILD)) == 10_000 + 5_000 + 10_000 + 4_000
    with session.open("a") as handle:
        handle.write(_turn("m3", output=200) + '{"type":"assistant","message":{"id":"m4","us')
    assert spend.claude_spent(home.room(CHILD)) == 30_000
    with session.open("a") as handle:
        handle.write('age":{"output_tokens":100}}}\n')
    assert spend.claude_spent(home.room(CHILD)) == 30_500


def test_codex_spend_is_the_rollouts_last_cumulative_total(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    seat = tmp_path / "seat"
    (seat / "port").mkdir(parents=True)
    ident = "01a14a3b-a921-7043-a1d8-44d2ba165bc8"
    (seat / "port" / "body.log").write_text(json.dumps({"type": "thread.started", "thread_id": ident},
                                                        separators=(",", ":")) + "\n")
    day = tmp_path / "codex" / "sessions" / "2026" / "10" / "21"
    day.mkdir(parents=True)
    rows = [{"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": total, "cached_input_tokens": total - 1_000, "output_tokens": 100}}}}
        for total in (10_000, 50_000)]
    (day / f"rollout-2026-10-21T00-00-00-{ident}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert spend.codex_spent(seat) == 1_000 + 49_000 * 0.1 + 500


def test_past_its_allocation_the_strand_is_warned_at_every_boundary_and_the_opener_raises_it(home, tmp_path):
    folder = _transcripts(tmp_path / "claude" / "projects", home.room(CHILD))
    (folder / "s1.jsonl").write_text(_turn("m1", output=500_000))  # 2.5M weighted
    _letter(home, PARENT, "thread:work", "Build it.", budget=3_000_000)
    facts = read_facts(home)
    context = SimpleNamespace(thread="work", fuel="", why="")
    loom._boundary(home, facts, CHILD, 1, context)
    boundary = home.room(CHILD) / "port" / "in" / "boundary.md"
    text = boundary.read_text()
    assert "· spent 2.5M/3M" in text and "budget:" not in text

    (folder / "s1.jsonl").write_text(_turn("m1", output=500_000) + _turn("m2", output=200_000))
    loom._boundary(home, facts, CHILD, 1, context)
    text = boundary.read_text()
    assert "· spent 3.5M/3M" in text.splitlines()[1]
    parse_boundary(text)  # the warning line is not a letter
    # Nothing else changed between these calls; the warning still comes each time.
    for _ in range(2):
        said = json.loads(execute("post", home.room(CHILD), "{}"))["hookSpecificOutput"]["additionalContext"]
        assert "\nbudget: thread work has spent 3.5M of 3M tokens. Write to thread:parent" in said
    # With nothing owed and nothing new, the warning is the whole message, each time.
    over = next(line for line in text.splitlines() if line.startswith("budget: "))
    boundary.write_text(render_boundary(CHILD, 1, "work", [], set(), {}, budget="spent 3.5M/3M", over=over))
    for _ in range(3):
        said = json.loads(execute("post", home.room(CHILD), "{}"))["hookSpecificOutput"]["additionalContext"]
        assert said == over

    # The child cannot raise its own: refused at send, and ignored in the fold.
    refused = cli(["send", "--room", str(home.room(CHILD)), "--to", "thread:parent", "--budget", "5m", "more"])
    assert refused.returncode == 1 and "only its opener (none)" in refused.stderr
    _letter(home, CHILD, "thread:work", "forged", budget=10_000_000)

    sent = cli(["send", "--room", str(home.room(PARENT)), "--to", "thread:work", "--budget", "2m", "Go on."])
    assert sent.returncode == 0, sent.stderr
    out = home.room(PARENT) / "port" / "out" / (sent.stdout.strip().split("/")[1] + ".md")
    fact = fact_from_port(parse_frontmatter(out.read_text()), PARENT, 1, home.room(PARENT))
    assert fact.data["budget"] == 2_000_000
    append(home, fact)
    loom._boundary(home, read_facts(home), CHILD, 1, context)
    text = boundary.read_text()
    assert "· spent 3.5M/5M" in text and "budget:" not in text
    assert "budget:" not in execute("post", home.room(CHILD), "{}")


def test_an_opening_without_budget_takes_the_homes_default_and_a_parentless_thread_has_none(home, tmp_path):
    _transcripts(tmp_path / "claude" / "projects", home.room(PARENT)).joinpath("s.jsonl").write_text(
        _turn("m1", output=1_000))
    _letter(home, PARENT, "thread:work", "Build it.")
    facts = read_facts(home)
    assert loom._budget(home, "work", facts) == {"budget": "spent 0/2M", "over": ""}
    assert loom._budget(home, "parent", facts) == {"budget": "spent 5k", "over": ""}
    assert spend.parse_tokens("1.5m") == 1_500_000
    with pytest.raises(ValueError):
        spend.parse_tokens("lots")


def test_a_snapshot_message_counts_its_largest_usage_once(home, tmp_path):
    folder = _transcripts(tmp_path / "claude" / "projects", home.room(CHILD))
    (folder / "s1.jsonl").write_text(_turn("m1", output=100, blocks=1) + _turn("m1", output=300, blocks=1)
                                     + _turn("m1", output=200, blocks=1))
    assert spend.claude_spent(home.room(CHILD)) == 1_500


def test_the_opener_sees_each_strand_it_opened_with_spend_and_a_report_as_submitted(home, tmp_path):
    folder = _transcripts(tmp_path / "claude" / "projects", home.room(CHILD))
    (folder / "s1.jsonl").write_text(_turn("m1", output=100_000))
    _letter(home, PARENT, "thread:work", "Build it.")
    context = SimpleNamespace(thread="parent", fuel="", why="")
    loom._boundary(home, read_facts(home), PARENT, 1, context)
    boundary = home.room(PARENT) / "port" / "in" / "boundary.md"
    text = boundary.read_text()
    row = next(line for line in text.splitlines() if line.startswith("opened: work "))
    assert row.endswith(" · spent 500k/2M · owed 1"), row
    assert " · thread parent · strands 1 · " in text.splitlines()[1] + " · "
    assert execute("start", home.room(PARENT), "{}")

    # Spend that moves is not news; the jack stays quiet.
    (folder / "s1.jsonl").write_text(_turn("m1", output=100_000) + _turn("m2", output=500_000))
    loom._boundary(home, read_facts(home), PARENT, 1, context)
    assert " · spent 3M/2M over · owed" in boundary.read_text()
    assert execute("post", home.room(PARENT), "{}") == ""

    # The child reports: the parent owes its letter, and the row says so.
    _letter(home, CHILD, "thread:parent", "Done: PR #1. Read it?")
    loom._boundary(home, read_facts(home), PARENT, 1, context)
    text = boundary.read_text()
    report = next(f.data["id"] for f in read_facts(home) if f.kind == "letter" and f.data["from"] == CHILD)
    assert f"opened: work submitted {report} · spent 3M/2M over · owed" in text
    assert " · strands 1 (1 submitted)" in text.splitlines()[1]
    parse_boundary(text)
