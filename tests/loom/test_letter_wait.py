"""Read timing from receipts written by the same jack that presents letters."""

from datetime import datetime, timezone
from types import SimpleNamespace

from brr.daemon2.facts import Fact
from brr.loom.runtime import jack
from brr.loom.runtime.__main__ import main
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.letter_wait import readings
from brr.loom.runtime.loom import _ingest
from test_attend import _boundary, body  # noqa: F401
from test_wishes import ledger  # noqa: F401


def _at(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def test_jack_measures_wake_and_first_post_that_shows_a_letter(body, monkeypatch, capsys):
    home, room = body
    wake = inject_letter(home.root, to="thread:work", body="At wake")
    arrival = next(f.at for f in read_facts(home) if f.id == wake)
    base = int(datetime.fromisoformat(arrival).timestamp()) + 10
    clock = SimpleNamespace(time=lambda: base, monotonic=lambda: 100, strftime=jack.time.strftime)
    monkeypatch.setattr(jack, "time", clock)
    _boundary(home, room)
    assert "At wake" in jack.execute("start", room, "{}")

    # An empty post still marks the previous tool boundary.
    clock.time = lambda: base + 10
    clock.monotonic = lambda: 110
    assert "At wake" not in jack.execute("post", room, "{}")

    late = append(home, Fact(kind="letter", by="loom:aaaa", id="loom/late", at=_at(base + 30),
                             data={"id": "loom/late", "from": "loom", "to": "thread:work",
                                   "body": "During thought"}))
    _boundary(home, room)
    clock.time = lambda: base + 370
    clock.monotonic = lambda: 470
    assert "During thought" in jack.execute("post", room, "{}")
    assert "During thought" not in jack.execute("post", room, "{}")

    _ingest(home)
    facts = read_facts(home)
    (wake_reading, wake_boundary), (late_reading, late_boundary) = readings(facts, "work", 2)
    assert wake_reading == {"letter": wake, "sender": "person", "arrival_at": arrival,
                            "seconds": round(base - datetime.fromisoformat(arrival).timestamp(), 3)}
    assert wake_boundary["event"] == "wake"
    assert wake_boundary["boundary_at"] == base
    assert wake_boundary["previous_gap"] is None
    assert late_reading == {"letter": late.id, "sender": "loom", "arrival_at": late.at, "seconds": 340}
    assert late_boundary["event"] == "post"
    assert late_boundary["boundary_at"] == base + 370
    assert late_boundary["previous_gap"] == 360
    assert len(readings(facts, "work", 20)) == 2
    assert readings(facts, "other", 20) == []

    assert main(["letter-wait", "work", "--limit", "2"]) == 0
    rows = capsys.readouterr().out.splitlines()
    assert rows[0].startswith(wake + " · person · wait ")
    assert rows[0].endswith("wake · previous boundary —")
    assert rows[1] == f"{late.id} · loom · wait 340.000s · post · previous boundary 360.000s"
    assert main(["letter-wait", "work", "--limit", "1"]) == 0
    assert capsys.readouterr().out.splitlines() == rows[1:]
