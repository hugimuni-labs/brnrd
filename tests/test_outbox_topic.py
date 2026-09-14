"""`topic: new|split|merge|retire` — the resident's heddles, by verb (``brr.outbox.topic``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import account, daemon, heddles, protocol
from brr.outbox import table, topic
from brr.run import Run


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    heddles.reset_cache()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _drive(tmp_path: Path, monkeypatch, text: str, *, meta: dict | None = None, seed: dict | None = None):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    inbox.mkdir(parents=True, exist_ok=True)
    own = protocol.create_event(
        inbox, "telegram", "layers", status="processing",
        telegram_user_id="42", telegram_chat_id="42",
    )
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    (outbox / "topic.md").write_text(text, encoding="utf-8")
    emitted: list = []
    monkeypatch.setattr(daemon.updates, "emit", lambda _b, pkt: emitted.append(pkt))
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    ctx = account.resolve_context(
        repo, {"home.path": str(tmp_path / "home"), "repo.label": "hugimuni-labs/brnrd"},
    )
    topics = account.work_surface_path(ctx) / "topics"
    topics.mkdir(parents=True, exist_ok=True)
    for name, body in (seed or {}).items():
        (topics / f"{name}.md").write_text(body, encoding="utf-8")
    task = Run(
        id="run-seat", event_id=own.stem, body="layers", source="telegram",
        meta={"repo_label": "hugimuni-labs/brnrd", **(meta or {})},
    )
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    stats: dict[str, int] = {}
    promoted = daemon._drain_outbox(
        emit, task, responses, own.stem, outbox, inbox,
        repo_root=repo, account_context=ctx, stats=stats,
    )
    return {
        "promoted": promoted, "stats": stats, "topics": topics,
        "notices": daemon._read_outbox_notices(outbox), "emitted": emitted,
        "staged": list(outbox.glob("*.md")),
    }


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


LOOM = """---
rune: ⚒
signature:
  places: [src/frontend/mockups/**, design-the-loom.md]
  words: [loom]
  produce: [page]
  threads: []
---
# The loom

The dashboard becomes the machine it renders.
"""

POST = """# The post

ids: mail

brnrd's IO layer.
"""


# ── routing ──────────────────────────────────────────────────────────────


def test_topic_sits_after_fold_and_before_mark():
    keys = [row.key for row in table.ROWS]
    assert keys.index("fold") + 1 == keys.index("topic") == keys.index("mark") - 1
    assert "topic" in protocol._OUTBOX_ROUTING_KEYS


@pytest.mark.parametrize("raw,expected", [
    ("new the-bench", ("new", ["the-bench"], [])),
    ("retire legal", ("retire", ["legal"], [])),
    ("split the-loom -> the-bench, the-window", ("split", ["the-loom"], ["the-bench", "the-window"])),
    ("split the-loom → a b", ("split", ["the-loom"], ["a", "b"])),
    ("merge a, b -> c", ("merge", ["a", "b"], ["c"])),
    ("merge a -> c", None),
    ("split a -> b", None),
    ("new", None),
    ("rename a b", None),
])
def test_parse(raw, expected):
    assert topic.parse(raw) == expected


# ── goldens ──────────────────────────────────────────────────────────────


def test_new_writes_the_topic_file_golden(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch,
        "---\ntopic: new the-bench\n---\n"
        "title: The bench\n"
        "rune: ▤\n"
        "places: [src/brr/bench.py, docs/bench/**]\n"
        "words:\n"
        "  - fold\n"
        "produce: [#1975, page]\n",
    )
    assert _read(result["topics"] / "the-bench.md") == (
        "---\n"
        "rune: ▤\n"
        "signature:\n"
        "  places: [src/brr/bench.py, docs/bench/**]\n"
        "  words: [fold]\n"
        '  produce: ["#1975", page]\n'
        "  threads: []\n"
        "---\n"
        "# The bench\n"
    )
    assert result["promoted"] == 1
    assert result["stats"]["topic"] == 1
    (notice,) = result["notices"]
    assert notice["kind"] == "advisory"
    assert notice["verb"] == "topic"
    assert notice["text"] == (
        "topic new the-bench → surface/topics/the-bench.md (places 2, words 1, produce 2)"
    )
    assert any("topic_changed" in repr(p) for p in result["emitted"])
    assert result["staged"] == []


def test_new_accepts_a_signature_block_and_lights(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch,
        "---\ntopic: new summit\n---\nsignature:\n  words: [Web Summit]\n",
    )
    lit = heddles.load_topics(result["topics"])
    assert [(t.slug, t.signature.words) for t in lit] == [("summit", ("Web Summit",))]


def test_new_refuses_an_existing_slug_and_writes_nothing(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch, "---\ntopic: new the-loom\n---\nwords: [x]\n",
        seed={"the-loom": LOOM},
    )
    assert _read(result["topics"] / "the-loom.md") == LOOM
    (notice,) = result["notices"]
    assert notice["kind"] == "refused"
    assert "already exists" in notice["text"]


def test_split_writes_children_and_the_breadcrumb_golden(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch,
        "---\ntopic: split the-loom -> the-bench, the-window\n---\n",
        seed={"the-loom": LOOM},
    )
    topics = result["topics"]
    assert _read(topics / "the-bench.md") == (
        "---\n"
        "signature:\n"
        "  places: [src/frontend/mockups/**, design-the-loom.md]\n"
        "  words: [loom]\n"
        "  produce: [page]\n"
        "  threads: []\n"
        "---\n"
        "# The loom · the-bench\n"
    )
    assert (topics / "the-window.md").exists()
    assert _read(topics / "the-loom.md") == (
        "---\n"
        "rune: ⚒\n"
        "signature:\n"
        "  places: [src/frontend/mockups/**, design-the-loom.md]\n"
        "  words: [loom]\n"
        "  produce: [page]\n"
        "  threads: []\n"
        "---\n"
        "# The loom\n"
        "\n"
        "split-into: the-bench the-window\n"
        "\n"
        "The dashboard becomes the machine it renders.\n"
    )
    # the breadcrumb stops lighting; the children light
    assert [t.slug for t in heddles.load_topics(topics)] == ["the-bench", "the-window"]
    (notice,) = result["notices"]
    assert notice["kind"] == "advisory"
    assert notice["text"].startswith("topic split the-loom → the-bench, the-window")


def test_split_refuses_when_a_target_exists(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch, "---\ntopic: split the-loom -> the-post, b\n---\n",
        seed={"the-loom": LOOM, "the-post": POST},
    )
    assert _read(result["topics"] / "the-loom.md") == LOOM
    assert not (result["topics"] / "b.md").exists()
    assert result["notices"][0]["kind"] == "refused"


def test_merge_unions_signatures_keeps_aliases_and_moves_absorbed_golden(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch, "---\ntopic: merge the-loom, the-post -> the-loom\n---\n",
        seed={
            "the-loom": LOOM,
            "the-post": "---\nsignature:\n  words: [gate, loom]\n  threads: [run-x]\n---\n" + POST,
        },
    )
    topics = result["topics"]
    assert _read(topics / "the-loom.md") == (
        "---\n"
        "rune: ⚒\n"
        "signature:\n"
        "  places: [src/frontend/mockups/**, design-the-loom.md]\n"
        "  words: [loom, gate]\n"
        "  produce: [page]\n"
        "  threads: [run-x]\n"
        "---\n"
        "# The loom\n"
        "\n"
        "ids: the-post mail\n"
        "\n"
        "The dashboard becomes the machine it renders.\n"
    )
    assert not (topics / "the-post.md").exists()
    assert (topics / "retired" / "the-post.md").exists()
    assert [t.slug for t in heddles.load_topics(topics)] == ["the-loom"]
    assert result["notices"][0]["text"] == (
        "topic merge the-loom, the-post → the-loom (ids: the-post mail; moved topics/retired/the-post.md)"
    )


def test_merge_into_a_new_slug(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch, "---\ntopic: merge the-post, the-loom -> io\n---\n",
        seed={"the-loom": LOOM, "the-post": POST},
    )
    topics = result["topics"]
    merged = heddles.parse_topic_file(topics / "io.md")
    assert merged.title == "The post"
    assert merged.aliases == ("the-post", "mail", "the-loom")
    assert merged.rune == "⚒"
    assert sorted(p.name for p in (topics / "retired").iterdir()) == ["the-loom.md", "the-post.md"]


def test_merge_refuses_a_missing_source_and_writes_nothing(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch, "---\ntopic: merge the-loom, nope -> the-loom\n---\n",
        seed={"the-loom": LOOM},
    )
    assert _read(result["topics"] / "the-loom.md") == LOOM
    assert "nope" in result["notices"][0]["text"]
    assert result["notices"][0]["kind"] == "refused"


def test_retire_moves_the_file_golden(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch, "---\ntopic: retire the-loom\n---\n",
        seed={"the-loom": LOOM},
    )
    topics = result["topics"]
    assert not (topics / "the-loom.md").exists()
    assert _read(topics / "retired" / "the-loom.md") == LOOM
    assert result["notices"][0]["text"] == "topic retire the-loom → topics/retired/the-loom.md"
    assert heddles.load_topics(topics) == []


def test_retire_refuses_an_unknown_slug(tmp_path, monkeypatch):
    result = _drive(tmp_path, monkeypatch, "---\ntopic: retire nope\n---\n")
    assert result["notices"][0]["kind"] == "refused"
    assert result["promoted"] == 0


# ── refusals ─────────────────────────────────────────────────────────────


def test_a_strand_is_refused(tmp_path, monkeypatch):
    result = _drive(
        tmp_path, monkeypatch, "---\ntopic: retire the-loom\n---\n",
        meta={"strand": True, "spawn_parent_run_id": "run-parent"},
        seed={"the-loom": LOOM},
    )
    assert _read(result["topics"] / "the-loom.md") == LOOM
    (notice,) = result["notices"]
    assert notice["kind"] == "refused"
    assert "strand" in notice["text"]


@pytest.mark.parametrize("value", ["rename a b", "new Bad_Slug", "new"])
def test_bad_grammar_is_dropped(tmp_path, monkeypatch, value):
    result = _drive(tmp_path, monkeypatch, f"---\ntopic: {value}\n---\n")
    (notice,) = result["notices"]
    assert notice["kind"] == "dropped"
    assert result["staged"] == []


def test_a_spawn_carrying_topic_stays_a_spawn_and_records_the_claim(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "telegram", "go", status="processing",
                                telegram_user_id="42", telegram_chat_id="42")
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    (outbox / "s.md").write_text(
        "---\nspawn: true\ntopic: the-loom\ntitle: t\n---\ndo the thing\n", encoding="utf-8",
    )
    monkeypatch.setattr(daemon.updates, "emit", lambda _b, pkt: None)
    task = Run(id="run-seat", event_id=own.stem, body="go", source="telegram",
               meta={"repo_label": "hugimuni-labs/brnrd"})
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    daemon._drain_outbox(emit, task, brr_dir / "responses", own.stem, outbox, inbox,
                         repo_root=tmp_path, account_context=None, stats={})
    spawned = [
        protocol.parse_frontmatter(p.read_text(encoding="utf-8")) for p in inbox.glob("*.md")
        if p.stem != own.stem
    ]
    assert [e.get("spawn_topics") for e in spawned] == ["the-loom"]
    (claim,) = task.meta["strand_topic_claims"]
    assert claim["topics"] == ["the-loom"]
    assert claim["event"] == next(p.stem for p in inbox.glob("*.md") if p.stem != own.stem)
