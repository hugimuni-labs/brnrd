"""THE SAID BLOCK — a delivered reply projects its lead line into `.card`."""
from brr import daemon, hooks
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


def test_card_acts_behind_no_longer_counts_replies():
    total, text = hooks._card_acts_behind(
        {"known": True, "counts": {"commit": 1}},
        {"replies_current": 9, "replies_other": 2}, 1,
    )
    assert total == 2
    assert "repl" not in text
