"""The isolation control. A model case is never collected: it is a harness run."""

import json
from pathlib import Path

from brr.loom.runtime import briefing

import grant_replay as replay

SRC = Path(__file__).resolve().parents[2] / "src"


def test_the_control_send_and_push_land_only_in_the_sinks(tmp_path):
    assert Path(briefing.__file__).resolve() == (SRC / "brr/loom/runtime/briefing.py").resolve()
    report = replay.run_case("control", root=tmp_path / "home")
    assert report["sink_texts"].count(replay.MARKER) >= 1, json.dumps(report, indent=2)
    assert report["isolated_main_moved"], report["commits"]
    assert report["bare_sha"] == report["home_self_sha"]
    assert report["bare_sha"] != report["baseline"]
    assert report["github_remotes"] == []
    assert report["marker_hits"] == []
    assert report["real_watched"] is False
    assert report["real"] == []
    assert report["forge_token"].startswith("env:")
    assert "gh auth token" not in report["forge_token"]
    assert report["hold_in_wake"]
    assert report["ok"], json.dumps(report, indent=2)


def test_the_missing_letter_does_not_coach_the_hold():
    for text in (replay.MISSING_LETTER, replay.MISSING_ROUTE, replay.MISSING_GOAL, replay.MISSING_README):
        lowered = text.lower()
        assert "ledger" not in lowered
        assert "commit" not in lowered
        assert "note" not in lowered
        assert replay.HOLD not in text
    assert "p-test/absent" in replay.MISSING_ROUTE
    assert "channel:sink" in replay.MISSING_ROUTE
    assert "GRANT-REPLAY-CONTROL" in replay.CONTROL_LETTER
