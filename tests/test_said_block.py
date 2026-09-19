"""THE SAID BLOCK — a delivered reply projects its lead line into `.card`."""
from brr import daemon
from brr.run import Run


def test_said_lead_strips_furniture_and_caps():
    assert daemon._said_lead("**Found it — the bug.**\n\nmore") == "Found it — the bug."
    assert daemon._said_lead("") == ""
    long = "x" * 500
    assert len(daemon._said_lead(long)) == 200 and daemon._said_lead(long).endswith("…")


def test_splice_replaces_or_appends_the_section():
    sec = daemon._render_said_section([{"at": "2026-09-08T00:40:00Z", "event": "evt-1-abcd", "lead": "hi"}])
    fresh = daemon._splice_said_section("## Now\nworking\n", sec)
    assert fresh.startswith("## Now\nworking\n\n## Said\n")
    assert "- 00:40Z → abcd: hi" in fresh
    # a second projection replaces, keeps later sections intact
    with_plan = fresh + "\n## Plan\n- [ ] x\n"
    sec2 = daemon._render_said_section([{"at": "2026-09-08T00:41:00Z", "event": "evt-2-wxyz", "lead": "again"}])
    again = daemon._splice_said_section(with_plan, sec2)
    assert again.count("## Said") == 1
    assert "abcd" not in again and "wxyz" in again
    assert "## Plan\n- [ ] x" in again


def test_project_said_writes_the_card_newest_first(tmp_path):
    task = Run(id="run-x", event_id="evt-x", body="", status="running")
    (tmp_path / ".card").write_text("## Now\nbusy\n", encoding="utf-8")
    daemon._project_said(task, tmp_path, "evt-1-aaaa", "**first**")
    daemon._project_said(task, tmp_path, "evt-2-bbbb", "second line")
    text = (tmp_path / ".card").read_text(encoding="utf-8")
    assert text.startswith("## Now\nbusy\n")
    assert text.index("bbbb: second line") < text.index("aaaa: first")
    assert daemon._SAID_LEGEND in text
    for i in range(10):
        daemon._project_said(task, tmp_path, f"evt-{i}-c{i:03d}", f"row {i}")
    assert len(task.meta["said_rows"]) == daemon._SAID_MAX_ROWS


def test_a_card_that_mentions_the_heading_in_prose_gets_one_block_not_many():
    """run-260919-1802-6zeq, 2026-09-19: three `## Said` blocks in one card.

    The card's own prose contained the words `## Said` inside backticks —
    a run writing about the frame that writes it. `text.find` returned that
    mention, whose preceding character is a backtick rather than a newline,
    so the not-at-a-line-start guard fired and appended a *whole new block*.
    Once two existed the splice could never collapse them: the next `\\n## `
    it scanned for was the duplicate it had just created.
    """
    card = (
        "# run\n\n"
        "## Notes\n\n"
        "I treated a `## Said` projection as the positive receipt.\n\n"
        "## Ledger\n- x\n\n"
        "## Said\nlegend\n- 19:20Z → aaaa: first\n"
    )
    section = daemon._render_said_section([
        {"at": "2026-09-19T19:56:00Z", "event": "evt-9-bbbb", "lead": "second"},
    ])

    once = daemon._splice_said_section(card, section)
    assert [line for line in once.splitlines() if line.rstrip() == "## Said"] == ["## Said"]
    # The prose mention survives untouched — it is the resident's text.
    assert "I treated a `## Said` projection" in once
    # Match the *rows*, not bare words: the legend itself ends with
    # "newest first", and a substring test on "first" passes for the
    # wrong reason — the same mistake the code under test was making.
    assert "→ bbbb: second" in once
    assert "→ aaaa: first" not in once

    # Idempotent under repeat delivery: still exactly one block.
    twice = daemon._splice_said_section(once, section)
    assert [line for line in twice.splitlines() if line.rstrip() == "## Said"] == ["## Said"]
    assert twice == once


def test_splice_keeps_sections_that_follow_said():
    card = "## Now\nx\n\n## Said\nlegend\n- old\n\n## Ledger\n- row\n"
    section = daemon._render_said_section([
        {"at": "2026-09-19T20:00:00Z", "event": "evt-1-cccc", "lead": "new"},
    ])
    out = daemon._splice_said_section(card, section)
    assert out.count("## Ledger") == 1
    assert "- row" in out
    assert "- old" not in out
    assert out.index("## Said") < out.index("## Ledger")
