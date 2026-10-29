"""THE SAID BLOCK — a delivered reply projects its lead line into `.card`."""
from brr import daemon
from brr.run import Run


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


