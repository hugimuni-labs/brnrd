"""The heddles reader (design-the-loom §20): signatures scored against rows."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from brr import heddles

NOW = 1_790_000_000.0  # a fixed epoch; every row below is dated against it


def iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


@pytest.fixture(autouse=True)
def _fresh_cache():
    heddles.reset_cache()
    yield
    heddles.reset_cache()


def write_topic(home: Path, slug: str, frontmatter: str, body: str | None = None) -> Path:
    directory = home / "surface" / "topics"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    text = f"---\n{frontmatter.strip()}\n---\n" + (body or f"# {slug}\n")
    path.write_text(text, encoding="utf-8")
    return path


def append_jsonl(path: Path, *rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def boundary(at: float, path: str | None) -> dict:
    return {"at": iso(at), "phase": "post-tool", "place": {"path": path, "commit": None}}


def by_slug(lit):
    return {h.slug: h for h in lit}


# ── the grammar ──────────────────────────────────────────────────────────


def test_parse_flow_and_block_lists_and_the_hash_ref_leniency(tmp_path):
    text = (
        "---\n"
        "rune: ⚒\n"
        "signature:\n"
        "  places: [src/brr/**, \"docs/*.md\"]  # the tree\n"
        "  words:\n"
        "    - ToS\n"
        "    - GDPR  # law\n"
        "  produce: [knot, #1975]\n"
        "  threads: []\n"
        "---\n"
        "# The workshop\n\nids: workshop old-shop\n\nBody.\n"
    )
    topic = heddles.parse_topic_text("the-workshop", tmp_path / "the-workshop.md", text)
    assert topic.rune == "⚒"
    assert topic.title == "The workshop"
    assert topic.signature.places == ("src/brr/**", "docs/*.md")
    assert topic.signature.words == ("ToS", "GDPR")
    assert topic.signature.produce == ("knot", "#1975")
    assert topic.signature.threads == ()
    assert topic.aliases == ("workshop", "old-shop")


def test_render_round_trips_and_quotes_hash_refs(tmp_path):
    topic = heddles.Topic(
        slug="a", path=tmp_path / "a.md", title="A", rune="♦",
        signature=heddles.Signature(places=("src/**",), produce=("#12", "page")),
        aliases=("b",), body="# A\n\nids: b\n\nBody.\n",
    )
    text = heddles.render_topic(topic)
    assert '"#12"' in text
    again = heddles.parse_topic_text("a", tmp_path / "a.md", text)
    assert again == topic


def test_a_file_without_frontmatter_is_a_topic_with_an_empty_signature(tmp_path):
    topic = heddles.parse_topic_text("legal", tmp_path / "legal.md", "# Legal\n\nGDPR.\n")
    assert topic.title == "Legal"
    assert topic.signature.is_empty()


def test_split_breadcrumbs_index_and_nested_files_are_not_loaded(tmp_path):
    write_topic(tmp_path, "live", "signature:\n  words: [x]")
    write_topic(tmp_path, "old", "signature:\n  words: [x]", "# Old\n\nsplit-into: a b\n")
    write_topic(tmp_path, "index", "signature:\n  words: [x]")
    retired = tmp_path / "surface" / "topics" / "retired"
    retired.mkdir()
    (retired / "gone.md").write_text("# Gone\n", encoding="utf-8")
    slugs = [t.slug for t in heddles.load_topics(heddles.topics_dir(tmp_path))]
    assert slugs == ["live"]


# ── each signature kind ──────────────────────────────────────────────────


def test_places_light_from_boundary_rows_relativised_against_the_roots(tmp_path):
    home = tmp_path / "home"
    write_topic(home, "the-workshop", "signature:\n  places: [src/brr/**]")
    write_topic(home, "the-loom", "signature:\n  places: [src/frontend/mockups/**]")
    run_dir = tmp_path / "runs" / "run-1"
    root = tmp_path / "repo"
    append_jsonl(
        run_dir / "boundaries.jsonl",
        boundary(NOW - 60, str(root / ".brr/worktrees/run-1/src/brr/hooks.py")),
        boundary(NOW - 30, None),  # a shell tool: no place
        boundary(NOW - 10, "/elsewhere/src/frontend/mockups/a.html"),  # under no root
    )
    lit = by_slug(heddles.light(home, run_dir, NOW, roots=[root]))
    assert lit["the-workshop"].matched_by == ("place",)
    assert lit["the-workshop"].last_match_at == iso(NOW - 60)
    assert lit["the-loom"].brightness == 0.0
    assert lit["the-loom"].last_match_at is None


def test_words_light_from_delivered_messages_only(tmp_path):
    home = tmp_path / "home"
    write_topic(home, "legal", "signature:\n  words: [ToS, terms]")
    node = tmp_path / "node"
    messages = node / "messages"
    messages.mkdir(parents=True)
    (messages / "000001-reply.md").write_text(
        f"---\nstatus: pending\ncreated_at: {iso(NOW - 5)}\n---\n\nthe tos draft\n",
        encoding="utf-8",
    )
    lit = by_slug(heddles.light(home, tmp_path / "runs" / "r", NOW, node_dir=node))
    assert lit["legal"].brightness == 0.0  # pending is not delivered
    (messages / "000001-reply.md").write_text(
        f"---\nstatus: delivered\ndelivered_at: {iso(NOW - 5)}\n---\n\nthe ToS draft\n",
        encoding="utf-8",
    )
    lit = by_slug(heddles.light(home, tmp_path / "runs" / "r", NOW, node_dir=node))
    assert lit["legal"].matched_by == ("word",)
    assert lit["legal"].last_match_at == iso(NOW - 5)


def test_words_match_whole_terms_case_insensitively(tmp_path):
    home = tmp_path / "home"
    write_topic(home, "adoption", "signature:\n  words: [stars]")
    node = tmp_path / "node"
    (node / "messages").mkdir(parents=True)
    (node / "messages" / "000001-a.md").write_text(
        f"---\nstatus: delivered\ndelivered_at: {iso(NOW)}\n---\n\nmegastarship\n",
        encoding="utf-8",
    )
    (node / "messages" / "000002-b.md").write_text(
        f"---\nstatus: delivered\ndelivered_at: {iso(NOW)}\n---\n\n12 STARS today\n",
        encoding="utf-8",
    )
    lit = by_slug(heddles.light(home, None, NOW, node_dir=node, outbox_dir=tmp_path / "ob"))
    assert lit["adoption"].matched_by == ("word",)


def test_produce_lights_by_kind_and_by_ref_from_the_ledger_and_relics(tmp_path):
    home = tmp_path / "home"
    write_topic(home, "pages", "signature:\n  produce: [page]")
    write_topic(home, "the-pr", "signature:\n  produce: ['#1975']")
    write_topic(home, "the-sha", "signature:\n  produce: [abc1234]")
    run_dir = tmp_path / "runs" / "r"
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    append_jsonl(
        run_dir / "produce.jsonl",
        {"kind": "knot", "ref": "abc1234def567", "at": iso(NOW - 100), "pr": 1975},
    )
    append_jsonl(outbox / ".relics.jsonl", {"kind": "kb", "path": "design-the-loom.md"})
    lit = by_slug(heddles.light(home, run_dir, NOW, outbox_dir=outbox))
    assert lit["the-pr"].last_match_at == iso(NOW - 100)
    assert lit["the-sha"].matched_by == ("produce",)
    # a relic carries no time: it is seen when the reader first reads it
    assert lit["pages"].last_match_at == iso(NOW)


def test_threads_light_from_the_run_itself_and_its_strands(tmp_path):
    home = tmp_path / "home"
    write_topic(home, "mine", "signature:\n  threads: [run-self]")
    write_topic(home, "child", "signature:\n  threads: [run-child]")
    run_dir = tmp_path / "runs" / "run-self"
    append_jsonl(run_dir / "boundaries.jsonl", boundary(NOW - 20, None))
    events = [{
        "id": "evt-1-aaaa", "source": "spawn_completed", "created": iso(NOW - 40),
        "spawned_by_run": "run-child",
    }]
    lit = by_slug(heddles.light(home, run_dir, NOW, run_id="run-self", events=events))
    assert lit["mine"].matched_by == ("thread",)
    assert lit["mine"].last_match_at == iso(NOW - 20)
    assert lit["child"].last_match_at == iso(NOW - 40)


# ── claims ───────────────────────────────────────────────────────────────


def test_a_topics_claim_is_a_match_at_its_own_mtime_through_aliases(tmp_path):
    home = tmp_path / "home"
    write_topic(home, "the-post", "signature:\n  words: [gate]", "# Post\n\nids: mail\n")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    claim = outbox / ".topics"
    claim.write_text("topics: mail nonsense\n", encoding="utf-8")
    os.utime(claim, (NOW - 1800, NOW - 1800))
    lit = by_slug(heddles.light(home, None, NOW, outbox_dir=outbox))
    assert lit["the-post"].matched_by == ("claim",)
    assert lit["the-post"].brightness == pytest.approx(0.7071, abs=1e-3)


def test_a_strands_topic_claim_is_a_match_at_its_dispatch(tmp_path):
    home = tmp_path / "home"
    write_topic(home, "the-loom", "signature:\n  places: [x/**]")
    claims = [{"topics": ["the-loom"], "at": iso(NOW - 3600), "event": "evt-9-zzzz"}]
    lit = by_slug(heddles.light(home, tmp_path / "runs" / "r", NOW, strand_claims=claims))
    assert lit["the-loom"].matched_by == ("claim",)
    assert lit["the-loom"].brightness == pytest.approx(0.5)


# ── decay, order, cache ──────────────────────────────────────────────────


@pytest.mark.parametrize("age,expected", [(0, 1.0), (3600, 0.5), (7200, 0.25), (4 * 3600, 0.0625)])
def test_brightness_halves_every_hour(age, expected):
    assert heddles.brightness(NOW - age, NOW) == pytest.approx(expected)
    assert heddles.brightness(None, NOW) == 0.0


def test_the_most_recent_match_of_any_kind_decides_and_order_is_brightest_first(tmp_path):
    home = tmp_path / "home"
    write_topic(home, "a", "signature:\n  places: [a/**]\n  produce: [knot]")
    write_topic(home, "b", "signature:\n  places: [b/**]")
    run_dir = tmp_path / "runs" / "r"
    append_jsonl(run_dir / "boundaries.jsonl", boundary(NOW - 7200, "a/x"), boundary(NOW - 60, "b/y"))
    append_jsonl(run_dir / "produce.jsonl", {"kind": "knot", "ref": "f00dbabe", "at": iso(NOW - 600)})
    lit = heddles.light(home, run_dir, NOW)
    assert [h.slug for h in lit] == ["b", "a"]
    assert lit[1].matched_by == ("produce", "place")
    assert lit[1].last_match_at == iso(NOW - 600)


def test_incremental_reads_only_what_was_appended(tmp_path, monkeypatch):
    home = tmp_path / "home"
    write_topic(home, "a", "signature:\n  places: [a/**]")
    run_dir = tmp_path / "runs" / "r"
    append_jsonl(run_dir / "boundaries.jsonl", boundary(NOW - 500, "a/1"))
    heddles.light(home, run_dir, NOW)
    seen: list[int] = []
    real = heddles._json_rows

    def counting(lines):
        seen.append(len(lines))
        return real(lines)

    monkeypatch.setattr(heddles, "_json_rows", counting)
    heddles.light(home, run_dir, NOW)
    assert sum(seen) == 0
    append_jsonl(run_dir / "boundaries.jsonl", boundary(NOW - 5, "a/2"))
    lit = heddles.light(home, run_dir, NOW)
    assert sum(seen) == 1
    assert lit[0].last_match_at == iso(NOW - 5)


def test_a_changed_signature_rescores_from_the_start(tmp_path):
    home = tmp_path / "home"
    path = write_topic(home, "a", "signature:\n  places: [a/**]")
    run_dir = tmp_path / "runs" / "r"
    append_jsonl(run_dir / "boundaries.jsonl", boundary(NOW - 50, "b/1"))
    assert heddles.light(home, run_dir, NOW)[0].brightness == 0.0
    path.write_text("---\nsignature:\n  places: [b/**]\n---\n# a\n", encoding="utf-8")
    os.utime(path, (NOW + 5, NOW + 5))
    assert heddles.light(home, run_dir, NOW)[0].last_match_at == iso(NOW - 50)


def test_an_empty_home_has_no_heddles(tmp_path):
    assert heddles.light(tmp_path / "nohome", tmp_path / "runs" / "r", NOW) == []
    (tmp_path / "home" / "surface" / "topics").mkdir(parents=True)
    assert heddles.light(tmp_path / "home", None, NOW) == []
    assert heddles.light(None, None, NOW) == []


def test_garbage_rows_never_raise(tmp_path):
    home = tmp_path / "home"
    write_topic(home, "a", "signature:\n  places: [a/**]")
    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)
    (run_dir / "boundaries.jsonl").write_text('not json\n[1]\n{"place": 5}\n{"at": 3}\n', encoding="utf-8")
    (run_dir / "produce.jsonl").write_text('{"kind": null}\n', encoding="utf-8")
    lit = heddles.light(home, run_dir, NOW, strand_claims=[None, {"topics": None}], events=[5])
    assert lit[0].brightness == 0.0


# ── the chip ─────────────────────────────────────────────────────────────


def test_chip_leads_with_the_brightest_three_and_parks_dimmer_after_a_dot():
    rows = [
        {"slug": "the-loom", "brightness": 0.8},
        {"slug": "the-workshop", "brightness": 1.0},
        {"slug": "summit", "brightness": 0.6},
        {"slug": "legal", "brightness": 0.2},
        {"slug": "the-post", "brightness": 0.1},
        {"slug": "adoption", "brightness": 0.01},
    ]
    assert heddles.chip_segment(rows) == "♦ workshop · loom · summit · (legal, post)"
    assert heddles.chip_segment(rows[:2]) == "♦ workshop · loom"
    assert heddles.chip_segment([{"slug": "x", "brightness": 0.0}]) is None
    assert heddles.chip_segment([]) is None


def test_the_hook_bar_renders_the_heddle_segment_from_the_portal():
    """The chip line before/after: before, the hook read `.topics` and
    rendered nothing from it; after, `♦` renders the frame's lit list,
    change-gated like any DELTA sign."""
    from brr import hooks

    payload = {
        "run": {"id": "run-x"},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"events": []},
        "card": {"active": True, "stale": False, "age_seconds": 5},
        "heddles": [
            {"slug": "the-workshop", "brightness": 0.97},
            {"slug": "the-loom", "brightness": 0.6},
            {"slug": "summit", "brightness": 0.52},
            {"slug": "the-post", "brightness": 0.12},
            {"slug": "legal", "brightness": 0.0},
        ],
    }
    chips: dict[str, str] = {}
    rendered = hooks.format_delta(payload, rendered_chips=chips)
    assert "♦ workshop · loom · summit · (post)" in rendered.splitlines()[0]
    assert hooks.SEGMENT_CLASS["heddles"] == hooks.DELTA
    again = hooks.format_delta(payload, last_chips=chips)
    assert again is None or "♦" not in again
    payload["heddles"] = []
    quiet = hooks.format_delta(payload)
    assert quiet is None or "♦" not in quiet
