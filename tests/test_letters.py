"""Shadow facts follow real event writes; status remains the authority."""

import json

from brr import letters, protocol


def _facts(path):
    return [json.loads(line) for line in letters.sidecar(path).read_text().splitlines()]


def test_create_claim_answer_and_release_are_shadowed(tmp_path):
    path = protocol.create_event(tmp_path / "inbox", "telegram", "hello")
    event = protocol._read_event(path)
    protocol.set_status(event, "processing")
    protocol.update_event_meta(event, run_id="run-real")
    protocol.set_status(event, "pending")  # interrupted/orphan return
    protocol.set_status(event, "processing")
    protocol.set_status(event, "done")
    protocol.set_status(event, "delivered")

    assert [row["kind"] for row in _facts(path)] == [
        "pending", "claimed", "claimed", "released", "claimed", "answered", "answered",
    ]
    assert _facts(path)[2]["data"]["run"] == "run-real"
    assert all(row["v"] == 1 and row["by"] and row["at"] for row in _facts(path))
    assert letters.fold(path) == "answered"
    assert letters.compare(path, "delivered", "test") == "agree"


def test_note_cancel_and_status_meta_writer_are_shadowed(tmp_path):
    inbox = tmp_path / "inbox"
    noted = protocol.create_event(inbox, "telegram", "thanks")
    protocol.set_status(protocol._read_event(noted), "noted")
    cancelled = protocol.create_event(inbox, "spawn", "stop")
    protocol.set_status(protocol._read_event(cancelled), "cancelled")
    meta = protocol.create_event(inbox, "queue", "close")
    protocol.update_event_meta(protocol._read_event(meta), status="done")

    assert _facts(noted)[-1]["data"]["why"] == "noted"
    assert _facts(cancelled)[-1]["data"]["why"] == "cancelled"
    assert [letters.fold(path) for path in (noted, cancelled, meta)] == [
        "retired", "retired", "answered",
    ]


def test_bypassed_writer_is_detected_and_logged(tmp_path):
    path = protocol.create_event(tmp_path / "inbox", "telegram", "hello")
    path.write_text(path.read_text().replace("status: pending", "status: done"))
    counts, disagreements, history = letters.check([path.parent], [letters.drift_path(path)])
    assert counts == {"agree": 0, "disagree": 1, "unknown": 0}
    assert disagreements and history == 1
    row = json.loads(letters.drift_path(path).read_text().splitlines()[0])
    assert row["event"] == path.stem and row["status"] == "done"
    assert row["fold"] == "pending" and row["site"] == "letters.check"


def test_fact_write_failure_does_not_block_status(tmp_path, monkeypatch):
    path = protocol.create_event(tmp_path / "inbox", "telegram", "hello")
    original = letters._append

    def fail_fact(target, row):
        if target == letters.sidecar(path):
            raise OSError("read only")
        return original(target, row)

    monkeypatch.setattr(letters, "_append", fail_fact)
    protocol.set_status(protocol._read_event(path), "done")
    assert protocol._read_event(path)["status"] == "done"
