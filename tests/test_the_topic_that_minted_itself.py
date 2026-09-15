"""Move 5d — minting made the natural move.

The suggested new slug (``heddles.suggest_slug`` → ``topic_suggested`` →
``new`` alone in ``.topic``), ``topic: rune``, and ``topic: show`` reshaped
into a query (kinds · depth · since · bench) delivered inline in the ask.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from brr import account, daemon, heddles, protocol, run_topic, topic_show
from brr.outbox import topic as topic_verb
from brr.run import Run

from test_the_event_carries_its_topic import _run_with_event, _seat, _topic


@pytest.fixture(autouse=True)
def _clean():
    heddles.reset_cache()
    yield
    heddles.reset_cache()


# ── 1. the suggested new slug ────────────────────────────────────────


@pytest.mark.parametrize("text,slug", [
    ("the deck v12 — slide 8's numbers are an August read", "the-deck-v12"),
    ("hey, can you do a reddit read on the launch thread?", "reddit-read"),
    ("Web Summit booth: print the banners before Friday", "web-summit-booth"),
])
def test_three_event_texts_suggest_three_slugs(tmp_path, text, slug):
    assert heddles.slug_phrase(text) == slug
    assert heddles.propose(tmp_path / "home", text) == (slug, "suggested")


@pytest.mark.parametrize("text,slug", [
    ("# Fix the flaky test\n\nit reds CI", "the-flaky-test"),      # a heading, a request verb
    ("```\ncode first\n```\nlet's talk about pricing tiers", "pricing-tiers"),
    ("look at `src/x.py` — [the loom](https://x.y) is lit", "the-loom"),
    ("Café crème order", "cafe-creme-order"),                          # the ASCII fold
    ("ok so", None),                                                   # only filler
    ("Привет, как дела", None),                                        # a script the fold drops
    ("", None),
])
def test_the_slugifier_edges(text, slug):
    assert heddles.slug_phrase(text) == slug


def test_a_collision_falls_back_to_a_numbered_suffix(tmp_path):
    home = tmp_path / "home"
    _topic(home, "reddit-read")
    text = "reddit read, again"
    assert heddles.suggest_slug(home, text) == "reddit-read-2"
    _topic(home, "the-post", ids=("reddit-read-2",))  # an alias is taken too
    assert heddles.suggest_slug(home, text) == "reddit-read-3"
    retired = home / "surface" / "topics" / "retired"
    retired.mkdir()
    (retired / "reddit-read-3.20260915T000000Z.md").write_text("# gone\n", encoding="utf-8")
    heddles.append_index(home, "reddit-read-4", kind="event", ref="evt-x")  # history under a name
    assert heddles.suggest_slug(home, text) == "reddit-read-5"
    # the proposal still wins over a suggestion, and no home suggests nothing
    assert heddles.propose(home, "reddit read") == ("reddit-read-5", "suggested")
    assert heddles.suggest_slug(None, text) is None


def test_prepare_stamps_the_suggestion_and_the_bundle_offers_it(tmp_path):
    import importlib

    from brr import prompts

    prepare = importlib.import_module("brr.worker.prepare")
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = account.resolve_context(repo, {"home.path": str(tmp_path / "home"), "repo.label": "o/r"})
    path = protocol.create_event(tmp_path / "inbox", "telegram", "the deck v12 is stale")
    event = protocol._read_event(path)
    task = Run.from_event(event)
    prepare._stamp_topic_proposal(event, task, ctx, tmp_path / ".brr", "telegram:42:")
    stamped = protocol._read_event(path)
    assert stamped["topic_suggested"] == "the-deck-v12"
    assert "topic_proposed" not in stamped and "topic_proposed_by" not in stamped
    assert task.meta[run_topic.META_SUGGESTED] == "the-deck-v12"
    assert Run.from_event(stamped).meta[run_topic.META_SUGGESTED] == "the-deck-v12"

    (line,) = prompts._topic_bundle_lines({"topic_suggested": "the-deck-v12"})
    assert line == ("- Topic: none matched — new `the-deck-v12`? one line in .topic: "
                    "that, an existing heddle, or null")
    # a proposal outranks a suggestion; an assignment outranks both
    (proposed,) = prompts._topic_bundle_lines({"topic_proposed": "the-post", "topic_suggested": "x"})
    assert "`the-post` proposed" in proposed


def test_the_kernel_says_minting_costs_one_line(tmp_path):
    from brr import prompts
    from brr.bootscore import format_kernel

    kernel = format_kernel(prompts.build_boot_score(
        tmp_path, is_daemon=True, environment="host", event_ids=("evt-1",), has_event_body=True,
    ))
    assert "this run's topic: an existing heddle ∨ `new <slug>` ∨ null (minting costs one line)" in kernel


@pytest.mark.parametrize("text,expected", [
    ("new\n", ("new", "")),
    ("topic: `new`\n", ("new", "")),
    ("new the-post\n", ("new", "the-post")),
    ("new two words\n", None),
])
def test_the_control_reads_new_alone(tmp_path, text, expected):
    (tmp_path / ".topic").write_text(text, encoding="utf-8")
    control = run_topic.read_control(tmp_path)
    assert (None if control is None else (control.op, control.slug)) == expected


def test_new_alone_mints_the_suggestion(tmp_path):
    task, home, inbox, outbox = _run_with_event(tmp_path, topic_suggested="the-deck-v12")
    notices: list[tuple[str, str]] = []
    (outbox / ".topic").write_text("new\n", encoding="utf-8")
    assert run_topic.settle(task, outbox_dir=outbox, account_home=home, inbox_dir=inbox,
                            notice=lambda k, t: notices.append((k, t))) == "the-deck-v12"
    assert (home / "surface" / "topics" / "the-deck-v12.md").is_file()
    assert protocol._read_event(inbox / f"{task.event_id}.md")["topic"] == "the-deck-v12"
    assert [r["ref"] for r in heddles.index(home, "the-deck-v12")] == [task.event_id]
    assert notices[-1][0] == "advisory" and "minted surface/topics/the-deck-v12.md" in notices[-1][1]


def test_new_alone_without_a_suggestion_is_refused_and_a_strand_still_cannot_mint(tmp_path):
    task, home, inbox, outbox = _run_with_event(tmp_path)
    notices: list[tuple[str, str]] = []
    (outbox / ".topic").write_text("new\n", encoding="utf-8")
    assert run_topic.settle(task, outbox_dir=outbox, account_home=home, inbox_dir=inbox,
                            notice=lambda k, t: notices.append((k, t))) is None
    assert notices[-1][0] == "refused" and "carries no suggested slug" in notices[-1][1]
    assert not (home / "surface" / "topics").exists()

    strand, home2, inbox2, outbox2 = _run_with_event(tmp_path / "s", topic_suggested="the-deck")
    (outbox2 / ".topic").write_text("new\n", encoding="utf-8")
    assert run_topic.settle(strand, outbox_dir=outbox2, account_home=home2, inbox_dir=inbox2,
                            notice=lambda k, t: notices.append((k, t)), is_strand=True) is None
    assert notices[-1][0] == "refused" and "new the-deck" in notices[-1][1]


# ── 2. `topic: rune` ─────────────────────────────────────────────────


def _rune(tmp_path, monkeypatch, value: str) -> dict:
    return _seat(tmp_path, monkeypatch, {"rune.md": f"---\ntopic: rune {value}\n---\n"}, control=None)


def test_rune_sets_then_changes_the_glyph_and_the_heddle_reads_it(tmp_path, monkeypatch):
    got = _rune(tmp_path, monkeypatch, "the-loom ᛗ")
    path = got["home"] / "surface" / "topics" / "the-loom.md"
    assert heddles.parse_topic_file(path).rune == "ᛗ"
    assert got["notices"][-1]["kind"] == "advisory"
    assert "topic rune the-loom → ᛗ" in got["notices"][-1]["text"]
    before = heddles.light(got["home"], tmp_path / "run", time.time(), run_id="run-seat")
    assert {h.slug: h.rune for h in before}["the-loom"] == "ᛗ"

    # change it: a second drain on the same home, a flag emoji (two code points)
    (got["outbox"] / "rune2.md").write_text("---\ntopic: rune the-loom 🇫🇷\n---\n", encoding="utf-8")
    _drain_again(tmp_path, got)
    topic = heddles.parse_topic_file(path)
    assert topic.rune == "🇫🇷" and topic.title == "the-loom"
    notice = daemon._read_outbox_notices(got["outbox"])[-1]
    assert "(was ᛗ)" in notice["text"]
    # the next boundary's reading carries the new rune — the fingerprint moved
    after = heddles.light(got["home"], tmp_path / "run", time.time(), run_id="run-seat")
    assert {h.slug: h.rune for h in after}["the-loom"] == "🇫🇷"


@pytest.mark.parametrize("value,why", [
    ("the-loom ᛗᛁ", "two graphemes"),
    ("the-loom abc", "3 code points"),
    ("the-lom ᛗ", "not a live heddle"),
])
def test_rune_refuses_a_long_glyph_or_a_slug_that_is_not_live(tmp_path, monkeypatch, value, why):
    got = _rune(tmp_path, monkeypatch, value)
    assert got["notices"][-1]["kind"] == "refused" and why in got["notices"][-1]["text"]
    assert heddles.parse_topic_file(got["home"] / "surface" / "topics" / "the-loom.md").rune == ""


def test_rune_names_the_owner_of_an_alias_and_its_grammar_is_a_verb(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control=None)
    _topic(got["home"], "the-post", ids=("letters",))
    (got["outbox"] / "rune.md").write_text("---\ntopic: rune letters ✉\n---\n", encoding="utf-8")
    _drain_again(tmp_path, got)
    notice = daemon._read_outbox_notices(got["outbox"])[-1]
    assert notice["kind"] == "refused" and "alias of the-post" in notice["text"]
    assert topic_verb.is_op("rune the-loom ᛗ")
    assert topic_verb.parse("rune the-loom") is None
    assert topic_verb.rune_problem("❤️") is None and topic_verb.rune_problem("é") is None
    assert topic_verb.rune_problem(" ") and topic_verb.rune_problem("́")


# ── 3. `topic: show` reshaped ────────────────────────────────────────


def _drain_again(tmp_path: Path, got: dict) -> None:
    task = got["task"]
    emit = daemon._WorkerEmit(brr_dir=got["brr_dir"], conversation_key="telegram:42:",
                              event_id=task.event_id)
    ctx = account.resolve_context(tmp_path / "repo", {"home.path": str(tmp_path / "home"),
                                                      "repo.label": "o/r"})
    daemon._drain_outbox(emit, task, got["brr_dir"] / "responses", task.event_id, got["outbox"],
                         got["inbox"], repo_root=tmp_path / "repo", account_context=ctx, stats={})


REPLY = "The verdict line.\n\nThe middle paragraph, which cut leaves out.\n\nThe last word.\n"


def _shown(tmp_path, monkeypatch, query: str, *, files=None) -> dict:
    got = _seat(tmp_path, monkeypatch, files if files is not None else {"reply.md": REPLY})
    home = got["home"]
    heddles.append_index(home, "the-loom", kind="produce", ref="d" * 40, run="run-seat")
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5 * 86400))
    heddles.append_index(home, "the-loom", kind="produce", ref="e" * 40, at=old, run="run-old")
    heddles.append_index(home, "the-loom", kind="bolt", ref="run-seat/bolt/1", run="run-seat")
    (got["outbox"] / "show.md").write_text(f"---\ntopic: show {query}\n---\n", encoding="utf-8")
    _drain_again(tmp_path, got)
    asks = [protocol._read_event(p) for p in got["inbox"].glob("*.md")
            if protocol._read_event(p).get("source") == "fold"]
    got["asks"] = asks
    got["bench"] = list((home / "bench").rglob("*.md")) if (home / "bench").exists() else []
    got["notices"] = daemon._read_outbox_notices(got["outbox"])
    return got


def test_show_defaults_messages_and_produce_whole_inline_and_no_bench(tmp_path, monkeypatch):
    got = _shown(tmp_path, monkeypatch, "the-loom")
    (ask,) = got["asks"]
    body = ask["body"]
    assert body.startswith("topic show the-loom · 3 acts\n\n# the-loom — the topic's index\n")
    assert "3 acts: message 1 · produce 2" in body
    assert "message `run-seat/000001-interim`\n  The verdict line.\n\n" \
           "  The middle paragraph, which cut leaves out.\n\n  The last word.\n" in body
    assert f"produce `{'d' * 40}` — `{'d' * 10}`" in body
    assert "bolt" not in body.split("\n\n", 1)[1] and "event `" not in body
    assert got["bench"] == [] and "focus_bench_path" not in ask
    assert "inline in" in got["notices"][-1]["text"]


@pytest.mark.parametrize("query,present,absent", [
    ("the-loom depth: heads", ["message `run-seat/000001-interim` — The verdict line.\n"],
     ["The last word."]),
    ("the-loom depth: cut", ["  The verdict line. … The last word."], ["middle paragraph"]),
    ("the-loom depth: whole", ["  The middle paragraph, which cut leaves out."], []),
    ("the-loom kinds: bolts", ["bolt `run-seat/bolt/1`"], ["message `", "produce `"]),
    ("the-loom kinds: events,produce", ["event `", "\n  look\n", "produce `"], ["message `"]),
    ("the-loom kinds: events depth: heads", ["` — look"], ["produce `"]),
    ("the-loom since 3d", [f"produce `{'d' * 40}`"], [f"produce `{'e' * 40}`"]),
    ("the-loom", [f"produce `{'e' * 40}`"], []),
])
def test_show_each_flag(tmp_path, monkeypatch, query, present, absent):
    got = _shown(tmp_path, monkeypatch, query)
    (ask,) = got["asks"]
    for text in present:
        assert text in ask["body"], text
    for text in absent:
        assert text not in ask["body"].split("\n\n", 1)[1], text


def test_show_the_combination_in_any_order_with_the_bench_page(tmp_path, monkeypatch):
    got = _shown(tmp_path, monkeypatch, "the-loom kinds: messages depth: cut since 3d")
    (ask,) = got["asks"]
    assert ask["body"].startswith(
        "topic show the-loom since 3d kinds: messages depth: cut · 1 acts\n\n")
    assert "1 acts since 3d: message 1" in ask["body"]
    assert "  The verdict line. … The last word." in ask["body"]
    assert "produce `" not in ask["body"] and got["bench"] == []

    again = _shown(tmp_path / "b", monkeypatch,
                   "the-loom bench: true since 3d depth: cut kinds: messages ,  produce")
    (ask,) = again["asks"]
    (page,) = again["bench"]
    text = page.read_text(encoding="utf-8")
    assert "kinds: messages, produce\ndepth: cut\n" in text
    assert ask["focus_bench_path"] == str(page) and f"bench: {page}" in ask["body"]
    # the page and the ask agree under the cap (the event store trims the trailing newline)
    assert text.rstrip("\n").endswith(ask["body"].split("\n\n", 1)[1].rstrip("\n"))


@pytest.mark.parametrize("query,why", [
    ("the-loom depth: deep", "heads · cut · whole"),
    ("the-loom kinds: messages, nope", "the kinds are"),
    ("the-loom bench: maybe", "true · false"),
    ("the-loom since forever", "not a span"),
    ("the-loom since 3d since 2d", "given twice"),
    ("the-loom please", "not a clause"),
])
def test_show_bad_clauses_are_dropped_with_the_reason(tmp_path, monkeypatch, query, why):
    got = _shown(tmp_path, monkeypatch, query)
    assert got["asks"] == []
    assert got["notices"][-1]["kind"] == "dropped" and why in got["notices"][-1]["text"]


def test_show_caps_the_inline_body_at_24_kb(tmp_path, monkeypatch):
    got = _seat(tmp_path, monkeypatch, {}, control=None)
    home = got["home"]
    node = home / "runs" / "o__r" / "run-big" / "messages"
    node.mkdir(parents=True)
    paragraph = ("word " * 199 + "word") + "\n"
    for n in range(1, 21):  # 20 × ~2 KB whole bodies ≈ 40 KB
        stem = f"{n:06d}-interim"
        (node / f"{stem}.md").write_text("---\nstatus: delivered\n---\n" + paragraph * 2,
                                         encoding="utf-8")
        heddles.append_index(home, "the-post", kind="message", ref=f"run-big/{stem}", run="run-big")
    (got["outbox"] / "show.md").write_text("---\ntopic: show the-post\n---\n", encoding="utf-8")
    _drain_again(tmp_path, got)
    (ask,) = [protocol._read_event(p) for p in got["inbox"].glob("*.md")
              if protocol._read_event(p).get("source") == "fold"]
    body = ask["body"]
    assert len(body.encode("utf-8")) <= topic_show.INLINE_CAP_BYTES
    kept = body.count("\n- ")
    assert 0 < kept < 20
    assert body.rstrip("\n").endswith(f"… {20 - kept} more acts; bench: true for the page")
    # uncapped, the same query renders every act
    assert topic_show.render(home, "the-post", kinds=("messages",), depth="whole").count("\n- ") == 20


def test_the_query_grammar_round_trips():
    query = topic_show.parse_query("the-loom  kinds:events ,messages depth: HEADS bench: true since 2h")
    assert query == topic_show.Query(slug="the-loom", since="2h", kinds=("events", "messages"),
                                     depth="heads", bench=True)
    assert topic_show.parse_query(query.describe()) == query
    assert topic_show.parse_query("the-loom") == topic_show.Query(slug="the-loom")
    assert topic_show.shape("only one\n", "cut") == "only one"
    assert topic_show.shape("a\n\nb\n\nc\n", "cut") == "a … c"


def test_hud_topic_takes_the_same_flags(tmp_path, monkeypatch, capsys):
    from brr import cli

    got = _seat(tmp_path, monkeypatch, {"reply.md": REPLY})
    home = got["home"]
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path / "repo")
    monkeypatch.setattr(account, "resolve_context",
                        lambda *_a, **_k: type("C", (), {"dispatch_inbox": got["inbox"]})())
    monkeypatch.setattr(account, "context_home_root", lambda _ctx: home)
    import types

    args = types.SimpleNamespace(topic="the-loom", since="3d", kinds="messages, events", depth="cut")
    assert cli._hud_topic(args) == 0
    out = capsys.readouterr().out
    assert "  The verdict line. … The last word." in out and "event `" in out
    args = types.SimpleNamespace(topic="the-loom", since=None, kinds=None, depth="deep")
    assert cli._hud_topic(args) == 1
    assert "heads · cut · whole" in capsys.readouterr().err
