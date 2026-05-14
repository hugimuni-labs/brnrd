"""Tests for the kb graph-statistics module."""

from pathlib import Path

from brr import kb_health


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_compute_graph_stats_returns_zero_when_kb_missing(tmp_path):
    """A repo without a kb directory yields an empty snapshot so the
    formatter can drop the block trivially."""
    stats = kb_health.compute_graph_stats(tmp_path)
    assert stats.total_pages == 0
    assert stats.total_bytes == 0
    assert stats.pages_by_kind == {}
    assert stats.peer_orphans == []


def test_compute_graph_stats_classifies_pages_by_prefix(tmp_path):
    """Pages bucket by name-prefix so the maintenance prompt can see
    the distribution at a glance — too many plan-* pages without
    matching shipped/superseded markers is a real signal."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Subj](subject-foo.md)\n"
        "- [Dec](decision-foo.md)\n"
        "- [Plan](plan-foo.md)\n"
        "- [Des](design-foo.md)\n"
        "- [Res](research-foo.md)\n"
        "- [Note](notes-foo.md)\n"
        "- [Deck](deck-foo.md)\n"
        "- [Misc](misc-foo.md)\n"
    ))
    for name in (
        "subject-foo.md", "decision-foo.md", "plan-foo.md",
        "design-foo.md", "research-foo.md", "notes-foo.md",
        "deck-foo.md", "misc-foo.md",
    ):
        _write(tmp_path / "kb" / name, "Body.\n")
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    stats = kb_health.compute_graph_stats(tmp_path)

    assert stats.pages_by_kind == {
        "subject": 1,
        "decision": 1,
        "plan": 1,
        "design": 1,
        "research": 1,
        "notes": 1,
        "deck": 1,
        "other": 1,
        "index": 1,
        "log": 1,
    }
    # Log bytes is tracked separately so the synthesis total isn't
    # dominated by the chronological narrative.
    assert "log.md" not in {p for p, _ in stats.largest_pages}


def test_compute_graph_stats_tracks_log_separately(tmp_path):
    """log.md size/entry count populates the dedicated fields, not
    the body-total. The log is allowed to grow large."""
    _write(tmp_path / "kb" / "index.md", "# Index\n")
    _write(
        tmp_path / "kb" / "log.md",
        "# Log\n\n"
        "## [2026-04-08] implement | One\n\nA.\n\n"
        "## [2026-04-09] implement | Two\n\nB.\n",
    )

    stats = kb_health.compute_graph_stats(tmp_path)

    assert stats.log_entry_count == 2
    assert stats.log_bytes > 0
    # body total excludes log.md; only index.md here, which is tiny.
    assert stats.total_bytes < stats.log_bytes


def test_compute_graph_stats_peer_orphan_detection(tmp_path):
    """A page that is in the index but no peer kb page links to it
    is a candidate for absorption. The index alone doesn't count —
    that's what kb_preflight already enforces."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Hub](subject-hub.md)\n"
        "- [Connected](decision-connected.md)\n"
        "- [Orphan](decision-orphan.md)\n"
    ))
    _write(
        tmp_path / "kb" / "subject-hub.md",
        "# Hub\n\nLinks: [conn](decision-connected.md)\n",
    )
    _write(tmp_path / "kb" / "decision-connected.md", "# Connected\n")
    _write(tmp_path / "kb" / "decision-orphan.md", "# Orphan\n")
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    stats = kb_health.compute_graph_stats(tmp_path)

    assert "kb/decision-orphan.md" in stats.peer_orphans
    # subject-hub has no inbound peer links either, but it's not a
    # decision/plan/etc.; we still surface it because absorption may
    # still apply. The maintenance pass decides — we just provide
    # the data.
    assert "kb/subject-hub.md" in stats.peer_orphans
    assert "kb/decision-connected.md" not in stats.peer_orphans


def test_compute_graph_stats_in_degree_excludes_index(tmp_path):
    """The index links to every page by construction; counting its
    links would make every page look high-degree. We want signal
    about the *kb peer graph*, so index links don't count."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [A](subject-a.md)\n"
        "- [B](subject-b.md)\n"
    ))
    _write(
        tmp_path / "kb" / "subject-a.md",
        "# A\n\nSees [b](subject-b.md) and [b again](subject-b.md).\n",
    )
    _write(tmp_path / "kb" / "subject-b.md", "# B\n")
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    stats = kb_health.compute_graph_stats(tmp_path)

    by_path = dict(stats.in_degree_top)
    assert by_path.get("kb/subject-b.md", 0) == 2
    assert by_path.get("kb/subject-a.md", 0) == 0


def test_compute_graph_stats_largest_pages_sorted_descending(tmp_path):
    """``largest_pages`` is a short list, biggest first, so the
    maintenance prompt can read it left-to-right and act on the
    worst offenders without scanning the whole kb."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Small](subject-small.md)\n"
        "- [Big](subject-big.md)\n"
        "- [Mid](subject-mid.md)\n"
    ))
    _write(tmp_path / "kb" / "subject-small.md", "small\n")
    _write(tmp_path / "kb" / "subject-mid.md", "x" * 1000 + "\n")
    _write(tmp_path / "kb" / "subject-big.md", "y" * 5000 + "\n")
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    stats = kb_health.compute_graph_stats(tmp_path)

    paths_in_order = [path for path, _ in stats.largest_pages]
    # The biggest two are unambiguously the synthesis pages we wrote.
    assert paths_in_order[0] == "kb/subject-big.md"
    assert paths_in_order[1] == "kb/subject-mid.md"
    # Descending by size, regardless of which other pages slip in.
    sizes_in_order = [size for _, size in stats.largest_pages]
    assert sizes_in_order == sorted(sizes_in_order, reverse=True)
    # log.md is excluded from the largest list even when it would
    # dominate; the chronological narrative isn't a synthesis page.
    assert "kb/log.md" not in paths_in_order


def test_format_graph_stats_returns_empty_for_empty_kb(tmp_path):
    """The maintenance prompt can drop the block entirely when
    there's nothing to report."""
    stats = kb_health.compute_graph_stats(tmp_path)
    assert kb_health.format_graph_stats(stats) == ""


def test_format_graph_stats_includes_load_bearing_sections(tmp_path):
    """The rendered block carries all four core sections so the
    maintenance prompt has enough context to triage."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Hub](subject-hub.md)\n- [Orph](decision-orph.md)\n"
    ))
    _write(
        tmp_path / "kb" / "subject-hub.md",
        "# Hub\n\nLinks to [self-ref](decision-orph.md).\n",
    )
    _write(tmp_path / "kb" / "decision-orph.md", "# Orphan\n")
    _write(
        tmp_path / "kb" / "log.md",
        "# Log\n\n## [2026-04-08] implement | one\n\nA.\n",
    )

    stats = kb_health.compute_graph_stats(tmp_path)
    block = kb_health.format_graph_stats(stats)

    assert "Graph stats (kb shape)" in block
    assert "by kind:" in block
    assert "kb/log.md:" in block
    assert "largest pages" in block
    assert "most-referenced pages" in block


def test_compute_graph_stats_records_task_touched_count(tmp_path):
    """When the caller passes the list of files the preceding task
    changed, the snapshot records the count so the formatter can
    surface it."""
    _write(tmp_path / "kb" / "index.md", "# Index\n")
    _write(tmp_path / "kb" / "subject-hub.md", "# Hub\n")

    stats = kb_health.compute_graph_stats(
        tmp_path,
        task_touched=["kb/subject-hub.md", "kb/log.md", "AGENTS.md"],
    )

    assert stats.task_touched_count == 3


def test_compute_graph_stats_task_touched_defaults_to_zero(tmp_path):
    """Callers that don't pass the list (older sites, the
    skip-fast path) get a zero count and the formatter omits the
    line entirely."""
    _write(tmp_path / "kb" / "index.md", "# Index\n")
    _write(tmp_path / "kb" / "subject-hub.md", "# Hub\n")

    stats = kb_health.compute_graph_stats(tmp_path)

    assert stats.task_touched_count == 0


def test_format_graph_stats_surfaces_task_touched_count(tmp_path):
    """A non-zero touched count appears as a one-line context cue
    alongside the structural stats so the agent sees both views."""
    _write(tmp_path / "kb" / "index.md", "# Index\n")
    _write(tmp_path / "kb" / "subject-hub.md", "# Hub\n")

    stats = kb_health.compute_graph_stats(
        tmp_path,
        task_touched=["kb/subject-hub.md", "kb/log.md"],
    )
    block = kb_health.format_graph_stats(stats)

    assert "task touched 2 kb / AGENTS.md pages this run" in block


def test_format_graph_stats_omits_touched_line_when_zero(tmp_path):
    """Skip-fast and zero-touch runs shouldn't see a stale
    'task touched 0 ...' bullet."""
    _write(tmp_path / "kb" / "index.md", "# Index\n")
    _write(tmp_path / "kb" / "subject-hub.md", "# Hub\n")

    stats = kb_health.compute_graph_stats(tmp_path)
    block = kb_health.format_graph_stats(stats)

    assert "task touched" not in block
