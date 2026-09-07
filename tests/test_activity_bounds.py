"""One over-long `phase` must not sink the whole activity PUT (2026-09-08)."""
from brr.gates import cloud_publisher as cp


def test_activity_phase_is_bounded_to_the_server_cap():
    rec = {"phase": "User-requested Fable continuation; Astra synthesis committed and published, quota nearly gone"}
    out = cp._bounded_activity_record(rec)
    assert len(out["phase"]) <= 64
    assert out["phase"].endswith("…")
    short = cp._bounded_activity_record({"phase": "turn_ended"})
    assert short["phase"] == "turn_ended"


def test_activity_snapshot_applies_the_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "_unbounded_activity_snapshot", lambda b, i: [{"phase": "x" * 200}])
    (rec,) = cp._activity_snapshot(tmp_path, tmp_path)
    assert len(rec["phase"]) == 64
