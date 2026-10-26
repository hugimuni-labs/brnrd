"""The heddles reader (design-the-loom §20): signatures scored against rows."""

from __future__ import annotations

import json
import time
from pathlib import Path


from brr import heddles

NOW = 1_790_000_000.0  # a fixed epoch; every row below is dated against it


def iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


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


# ── claims ───────────────────────────────────────────────────────────────


# ── decay, order, cache ──────────────────────────────────────────────────


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


# ── the chip ─────────────────────────────────────────────────────────────


