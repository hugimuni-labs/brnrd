from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime import roomsense
from brr.loom.runtime.port import render_boundary
from brr.loom.runtime.roomsense import Letter, letters, room_line, shifts

REPLAY = Path(__file__).parent / "fixtures" / "room_sense_replay.csv"
# The fixture's first letter, 10-15 23:21:21Z: only the replay's checkpoints below need it.
T0 = datetime(2026, 10, 15, 23, 21, 21, tzinfo=timezone.utc).timestamp()


def _replay() -> list[Letter]:
    rows = [line.split(",") for line in REPLAY.read_text().splitlines() if not line.startswith("#")]
    return [Letter(i, T0 + int(at), door, int(n), int(w), int(u)) for i, at, door, n, w, u in rows]


def _at(history, ident):
    return next(x for x in history if x.id == ident)


def test_the_10_28_night_is_one_shift_driven_by_the_burst_and_nothing_else_in_13_days_is():
    history = _replay()
    found = shifts("a", history)
    # The false-positive count over 10-15→10-29 is zero: one shift in all.
    assert len(found) == 1
    [shift] = found
    ids = [x.id for x in history]
    assert ids.index("9757") <= ids.index(shift.since.id) <= ids.index("9777")
    assert ids.index(shift.last.id) <= ids.index("9777")
    # The letters where he was explaining, and the one long letter on the new door, don't drive it.
    explaining = {"5497", "5498", "5499", "5501", "5503", "9749", "9751", "9753", "9754", "9756", "9762"}
    assert shift.drivers and not explaining & set(shift.drivers)
    assert {"burst", "short", "door"} <= set(shift.measures)


def test_calm_letters_on_the_new_door_and_his_busiest_old_evening_raise_nothing():
    history = _replay()
    [shift] = shifts("a", history)
    calm = _at(history, "9783")
    assert shift.last.at < calm.at
    after = history.index(calm)
    assert shifts("a", history, after) == []
    # 10-27 19:18Z: the busiest 44 minutes before the night.
    busiest = datetime(2026, 10, 27, 19, 18, tzinfo=timezone.utc).timestamp()
    assert not any(s.since.at <= busiest + 44 * 60 and s.last.at >= busiest for s in shifts("a", history))


def _source(n, at, text, platform="mine", person="person:p"):
    stamp = datetime.fromtimestamp(at, timezone.utc).isoformat()
    return Fact(kind="source", by="loom:x", at=stamp, id=f"source:{n}",
                data={"from": person, "platform": platform, "origin": f"{platform}:1:{n}", "text": text})


def _household(start=1_800_000_000.0):
    """Ten days of two long letters a few hours apart, then a burst of short ones through a new door."""
    words = [f"word{chr(97 + i % 26)}{chr(97 + i // 26 % 26)}" for i in range(400)]
    facts, at, n = [], start, 0
    for day in range(10):
        for k in range(12):
            at = start + day * 86400 + k * 3 * 3600
            text = " ".join(words[(n * 7 + j) % 60] for j in range(30))
            facts.append(_source(n, at, text)); n += 1
    burst = at + 86400
    for k in range(8):
        facts.append(_source(n, burst + k * 40, f"{words[200 + k]} {words[300 + k]}", platform="cloud")); n += 1
    return facts, burst + 7 * 40


def test_the_line_names_the_person_and_its_drivers_and_closes_on_a_calm_letter():
    roomsense._CACHE[:] = [None, None]
    facts, end = _household()
    line = room_line(facts, now=end + 60)
    assert line.startswith("room: p's channel off baseline since ")
    assert "burst" in line and "door" in line and "ask them first" in line
    calm = " ".join(f"worda{chr(97 + j)}" for j in range(26)) * 2
    # Past the burst's window: the calm letter is scored on its own.
    facts.append(_source(999, end + 1800, calm))
    assert room_line(facts, now=end + 1860) == ""


def test_the_line_is_read_as_of_now_not_from_letters_after_it():
    roomsense._CACHE[:] = [None, None]
    facts, end = _household()
    calm = " ".join(f"worda{chr(97 + j)}" for j in range(26)) * 2
    facts.append(_source(999, end + 1800, calm))
    assert room_line(facts, now=end + 60).startswith("room: p's")


def test_the_line_expires_and_strangers_never_count():
    roomsense._CACHE[:] = [None, None]
    facts, end = _household()
    assert room_line(facts, now=end + roomsense.EXPIRE + 60) == ""
    strangers = [_source(i, datetime.fromisoformat(f.at).timestamp(), f.data["text"], person="stranger:x")
                 for i, f in enumerate(facts)]
    roomsense._CACHE[:] = [None, None]
    assert room_line(strangers, now=end + 60) == ""


def test_words_new_to_the_person_count_in_any_alphabet():
    facts = [_source(0, 1_800_000_000, "привет как дела"), _source(1, 1_800_000_060, "привет снова дела")]
    [one, two] = letters(facts)["p"]
    assert (one.words, one.unseen) == (3, 3)
    assert (two.words, two.unseen) == (3, 1)


def test_the_boundary_carries_the_room_line_under_the_status():
    text = render_boundary("s-x", 1, "t", [], set(), {}, room="room: p's channel off baseline")
    lines = text.splitlines()
    assert lines[1].startswith("status: ") and lines[2] == "room: p's channel off baseline"
    assert "room:" not in render_boundary("s-x", 1, "t", [], set(), {})
