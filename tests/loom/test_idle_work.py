"""An idle wake works: ahead of pace the keepalive names what the thread could move (w-i1xohccy9ht0)."""
import json
import os
import re
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from brr.loom.runtime import idle, jack, loom, usage
from brr.loom.runtime.faces import PROPOSED
from brr.loom.runtime.ledger import Fact, append, inject_letter, read_facts
from brr.loom.runtime.port import render_boundary
from test_never_quit import STRAND, worker  # noqa: F401  (worker is a fixture)
from test_wake import commit
from test_wishes import command, heard, ledger  # noqa: F401  (ledger is a fixture)


def _gauge(left_pct: float, days_to_go: float) -> dict:
    reset = datetime.fromtimestamp(time.time() + days_to_go * 86400, timezone.utc).isoformat()
    return {"at": time.time(), "windows": [{"name": "session", "left_pct": 90.0, "resets_at": reset},
                                           {"name": "week", "left_pct": left_pct, "resets_at": reset}]}


AHEAD, BEHIND = _gauge(92.0, 5.5), _gauge(20.0, 5.5)
BLOCK = idle.render(['w-aaaa is heard, never read: "Decide" — `wish w-aaaa --read "<what it asks>"`'])


def _stop(room, monkeypatch, gauge):
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: gauge)
    (room / "port/in/idle.md").write_text(BLOCK)
    return json.loads(jack.execute("stop", room, "{}"))


def test_ahead_of_pace_the_keepalive_carries_the_acts_and_never_says_stop(worker, monkeypatch):
    room, _ = worker
    result = _stop(room, monkeypatch, AHEAD)
    assert result["decision"] == "block"
    reason = result["reason"]
    # pace() rounds the week fraction at call time. AHEAD is built at import, so a
    # long suite crosses the 79% into 78% edge. Compare against pace() now.
    assert reason.partition(chr(10))[2].startswith(
        f"cache keepalive, fuel ahead of pace ({idle.pace(AHEAD)})")
    assert "fuel to burn" not in reason and ": stop" not in reason
    assert "  - w-aaaa is heard, never read" in reason
    assert "or end with one line saying why not" in reason


def test_behind_pace_the_keepalive_says_what_it_said(worker, monkeypatch):
    room, _ = worker
    result = _stop(room, monkeypatch, BEHIND)
    assert result["reason"].partition(chr(10))[2].startswith("wakes at: the next letter")
    assert "w-aaaa" not in result["reason"]


def test_an_unknown_gauge_is_not_ahead(worker, monkeypatch):
    room, _ = worker
    assert _stop(room, monkeypatch, None)["reason"].partition(chr(10))[2].startswith("wakes at: the next letter")
    assert not idle.ahead({"windows": []})
    assert not idle.ahead({"windows": [AHEAD["windows"][0]]})


def test_pace_is_fuel_left_against_time_left():
    assert idle.ahead(_gauge(50.0, 3.0))
    assert not idle.ahead(_gauge(40.0, 3.0))


def _window(left: float, to_go: float, now: float) -> dict:
    reset = datetime.fromtimestamp(now + to_go * idle.WEEK, timezone.utc).isoformat()
    return {"at": now, "windows": [{"name": "week", "left_pct": left, "resets_at": reset}]}


def _today(gauge: dict, now: float) -> bool:
    """The comparison the gates used before a share existed."""
    week = next(w for w in gauge["windows"] if w.get("name") == "week")
    to_go = min(max(idle._reset(week) - now, 0.0) / idle.WEEK, 1.0)
    return float(week["left_pct"]) / 100 > to_go


def test_share_100_is_todays_test_on_a_few_gauges():
    now = time.time()
    gauges = [_window(left, to_go, now) for left, to_go in (
        (92, 5.5 / 7), (50, 3 / 7), (40, 3 / 7), (33, 0.24), (100, 1), (0, 0),
        (10, 6 / 7), (80, 1 / 7), (50, 0.5), (1, 0.99), (99, 0.01),
    )]
    for gauge in gauges:
        assert idle.ahead(gauge, now, share=100) is _today(gauge, now)
        assert idle.ahead(gauge, now) is _today(gauge, now)
    assert "initiative share" not in idle.pace(_window(33, 0.24, now), now, share=100)


def test_share_50_flips_a_gauge_that_is_ahead_at_100_and_pace_names_it():
    now = time.time()
    gauge = _window(33, 0.24, now)
    assert idle.ahead(gauge, now, share=100)
    assert not idle.ahead(gauge, now, share=50)
    assert idle.pace(gauge, now, share=50) == "week 33% left, 24% of it to go, initiative share 50%"


def _seat(home, seat, monkeypatch, gauge):
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: gauge)
    letter = inject_letter(home.root, to="thread:work", body="Please decide the colour")
    wish = command(home, seat, "wish", "new", "--line", "Decide the colour", "--say", letter)
    (home.room("s-aaaa-test") / "port/in").mkdir(parents=True, exist_ok=True)
    return wish, SimpleNamespace(thread="work", shell="claude")


def test_a_quiet_face_ahead_of_pace_carries_the_threads_acts(ledger, monkeypatch):
    home, seat = ledger
    wish, context = _seat(home, seat, monkeypatch, AHEAD)
    quiet = {"face": PROPOSED["quiet"], "token": "quiet since 20:00", "fact": "quiet:x"}
    block = loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    assert block.startswith(idle.HEAD)
    assert wish in block
    # The jack's copy is the acts. A quiet face may add one unease line; the file never carries the count.
    filed = (home.room("s-aaaa-test") / "port/in/idle.md").read_text()
    assert "unease:" not in filed
    assert block == filed or (block.startswith(filed) and block[len(filed):].startswith("unease:"))
    assert block.rstrip("\n") in render_boundary("s-aaaa-test", 1, "work", [], set(), {}, idle=block)
    letter = {"face": PROPOSED["letter"], "token": "your x", "fact": "x"}
    assert loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, letter, ()) == ""


def test_acts_are_derived_at_most_once_per_stamp(ledger, monkeypatch):
    home, seat = ledger
    _, context = _seat(home, seat, monkeypatch, AHEAD)
    calls = []
    real = idle.candidates
    monkeypatch.setattr(idle, "candidates", lambda *args: calls.append(args) or real(*args))
    quiet = {"face": PROPOSED["quiet"], "token": "quiet since 20:00", "fact": "quiet:x"}
    first = loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    assert loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ()) == first
    assert len(calls) == 1
    path = home.room("s-aaaa-test") / "port/in/idle.md"
    old = time.time() - idle.EVERY - 1
    os.utime(path, (old, old))
    loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    assert len(calls) == 2


def test_a_render_behind_pace_derives_nothing(ledger, monkeypatch):
    home, seat = ledger
    _, context = _seat(home, seat, monkeypatch, BEHIND)
    path = home.room("s-aaaa-test") / "port/in/idle.md"
    path.write_text(BLOCK)
    monkeypatch.setattr(idle, "candidates", lambda *args: (_ for _ in ()).throw(AssertionError("derived")))
    quiet = {"face": PROPOSED["quiet"], "token": "quiet since 20:00", "fact": "quiet:x"}
    got = loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    # Behind pace derives no acts. Unease is not gated on pace, so a line may still be said.
    assert got == "" or got.startswith("unease:")
    assert idle.HEAD not in got
    # A stale list never outlives the pace that asked for it.
    assert not path.exists()


def test_a_child_quiet_for_days_with_nothing_in_hand_is_an_act(ledger, monkeypatch):
    home, seat = ledger
    row = {"thread": "kid", "now": "idle 4d", "owed": 0, "prs": [], "waits": "", "status": "open"}
    acts = idle.candidates(home, read_facts(home), "work", [row, {**row, "thread": "busy", "owed": 1},
                                                             {**row, "thread": "fresh", "now": "idle 2d"}])
    assert acts == ["thread kid quiet 4d with nothing in hand: write to it with its next step, or settle it"]


def test_a_prose_park_is_offered_as_its_wait_not_as_the_top(ledger, monkeypatch):
    home, seat = ledger
    wish, _ = _seat(home, seat, monkeypatch, AHEAD)
    command(home, seat, "wish", wish, "--read", "waiting: the colour chart to come back")
    acts = idle.candidates(home, read_facts(home), "work")
    assert [a for a in acts if a.startswith(wish)] == [next(a for a in acts if "parked" in a)]


def test_a_typed_park_is_not_offered_at_all(ledger, monkeypatch):
    # The loom checks pr:, ci:, wish:, letter:, at: and file: waits itself and wakes the thread.
    home, seat = ledger
    wish, _ = _seat(home, seat, monkeypatch, AHEAD)
    command(home, seat, "wish", wish, "--read", "waiting: CI on #1", "--wait", "at:2099-01-01T00:00:00Z")
    assert idle.candidates(home, read_facts(home), "work") == []


def test_an_ask_is_never_offered_as_heard_never_read(ledger, monkeypatch):
    home, seat = ledger
    wish, _ = _seat(home, seat, monkeypatch, AHEAD)
    ask = command(home, seat, "ask", "Pick a colour", "--for", wish)
    late = inject_letter(home.root, to="thread:work", body="And name the board")
    named = command(home, seat, "wish", "new", "--line", "Name the board", "--say", late)
    command(home, seat, "wish", named, "--read", "a name for the board")
    acts = idle.candidates(home, read_facts(home), "work")
    assert not [a for a in acts if ask in a]
    # The person's wish, heard and never read, is still offered beside the top.
    assert [a.split()[0] for a in acts] == [named, wish] and "is heard, never read" in acts[1]


def test_the_top_waiting_on_a_person_is_not_offered(ledger, monkeypatch):
    home, seat = ledger
    wish, _ = _seat(home, seat, monkeypatch, AHEAD)
    command(home, seat, "wish", wish, "--read", "a colour for the board")
    command(home, seat, "wish", wish, "--left", "his pick A or B, then paint")
    assert idle.candidates(home, read_facts(home), "work") == []
    # The person's own name says it as well as a pronoun.
    command(home, seat, "wish", wish, "--left", "test's yes on the chart")
    assert idle.candidates(home, read_facts(home), "work") == []
    command(home, seat, "wish", wish, "--left", "paint the board blue")
    assert [a.split()[0] for a in idle.candidates(home, read_facts(home), "work")] == [wish]
    # The newer of the park and the left decides: a park on the person after a movable left.
    command(home, seat, "wish", wish, "--read", "waiting: her word on blue")
    assert idle.candidates(home, read_facts(home), "work") == [
        next(a for a in idle.candidates(home, read_facts(home), "work"))]
    assert "parked" in idle.candidates(home, read_facts(home), "work")[0]


def test_nothing_to_move_is_stamped_too(ledger, monkeypatch):
    home, _ = ledger
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: AHEAD)
    (home.room("s-aaaa-test") / "port/in").mkdir(parents=True, exist_ok=True)
    calls = []
    monkeypatch.setattr(idle, "candidates", lambda *args: calls.append(args) or [])
    context = SimpleNamespace(thread="work", shell="claude")
    for _ in range(2):
        assert loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, None, ()) == ""
    assert (home.room("s-aaaa-test") / "port/in/idle.md").read_text() == ""
    assert len(calls) == 1


def _card(now: float, ci: str) -> dict:
    pushed = datetime.fromtimestamp(now - 2 * 86400 - 50, timezone.utc).isoformat()
    return {"state": "open", "ci": ci, "pushed": pushed}


def _pull(home, thread: str, number: int) -> None:
    append(home, Fact(kind="pr.opened", by="strand:s-aaaa-test", data={
        "thread": thread, "repo": "o/r", "number": number, "head": "abc",
        "url": f"https://github.com/o/r/pull/{number}"}))


def test_the_inbox_ranks_across_threads_and_a_thread_keeps_its_own_order(ledger, monkeypatch):
    home, seat = ledger
    wish, _ = _seat(home, seat, monkeypatch, AHEAD)
    now = time.time()
    _pull(home, "work", 8)
    _pull(home, "work", 9)
    _pull(home, "other", 7)
    (home.root / "loom").mkdir(exist_ok=True)
    (home.root / "loom" / "prs.json").write_text(json.dumps({
        "o/r#8": _card(now, "green"), "o/r#9": _card(now, "red"), "o/r#7": _card(now, "red")}))
    facts = read_facts(home)
    work = idle.candidates(home, facts, "work", now=now)
    assert work[0].startswith(wish) and "#8 green, untouched 2d" in work[1] and "#9 is red" in work[2]
    assert all(not line.startswith(("work:", "other:", "inbox:")) for line in work)
    assert not any("#7" in line for line in work)
    inbox = idle.candidates(home, facts, "inbox", now=now)
    assert inbox == [
        "other: #7 is red: read the failing run, fix and push",
        "work: #9 is red: read the failing run, fix and push",
        f"work: {work[0]}",
    ]


def test_the_inbox_prefixes_a_quiet_child_it_opened(ledger):
    home, _ = ledger
    row = {"thread": "kid", "now": "idle 4d", "owed": 0, "prs": [], "waits": "", "status": "open"}
    assert idle.candidates(home, read_facts(home), "inbox", [row]) == [
        "inbox: thread kid quiet 4d with nothing in hand: write to it with its next step, or settle it"]


def test_the_inbox_reads_each_shared_source_once(ledger, monkeypatch):
    home, _ = ledger
    counts = {}

    def wrap(module, name):
        real = getattr(module, name)
        def wrapped(*args, **kwargs):
            counts[name] = counts.get(name, 0) + 1
            return real(*args, **kwargs)
        monkeypatch.setattr(module, name, wrapped)

    from brr.loom.runtime import lifecycle, queues, receipts, threads, wishes
    wrap(wishes, "told")
    wrap(queues, "queues")
    wrap(threads, "pr_cards")
    wrap(receipts, "receipts")
    wrap(lifecycle, "main_snapshot")
    wrap(idle, "_people")
    idle.candidates(home, read_facts(home), "inbox")
    assert counts == {"told": 1, "queues": 1, "pr_cards": 1, "receipts": 1,
                      "main_snapshot": 1, "_people": 1}


def test_fuel_line_drops_an_engine_below_its_share_and_a_per_shell_override_wins(ledger, monkeypatch):
    from brr.loom.runtime.conditions import _pass_lines
    from brr.loom.runtime.config import load_config, set_value
    home, _ = ledger
    now = time.time()

    def gauge(pct, days):
        reset = datetime.fromtimestamp(now + days * 86400, timezone.utc).isoformat()
        return {"at": now, "windows": [{"name": "week", "left_pct": pct, "resets_at": reset}]}

    # codex is ahead at 100 and behind at 50. grok stays ahead at 50. claude is behind at 100.
    gauges = {"codex": gauge(65, 3.8), "grok": gauge(71, 1.5), "claude": gauge(20, 5.5),
              "fake": _window(33, 0.24, now)}
    monkeypatch.setattr(usage, "shells", lambda home: ["codex", "grok", "claude", "none"])
    monkeypatch.setattr(usage, "reading", lambda home, shell, *args, **kwargs: gauges.get(shell))
    set_value(home.root, "initiative_share", 50)
    assert idle.fuel_line(home, now) == "fuel readings: grok week 71% left, 1.5d to reset"
    assert not idle.wants_block(home, "claude", "work")
    # The room's lease shell is fake. Its override, not the default, decides the turn.
    set_value(home.root, "initiative_share", 100)
    set_value(home.root, "initiative_share_fake", 50)
    assert idle.share_for(home, "fake") == 50
    assert idle.share_for(home, "grok") == 100
    assert idle.ahead(gauges["fake"], now, share=100)
    assert idle.fuel_line(home, now) == (
        "fuel readings: codex week 65% left, 3.8d to reset; grok 71% left, 1.5d")
    said = loom._turn_pace(home, read_facts(home), load_config(home.root))("work")
    assert said == ""
    set_value(home.root, "body", "claude")
    set_value(home.root, "initiative_share_claude", 50)
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: _window(92, 5.5 / 7, time.time()))
    text = _pass_lines(home, read_facts(home))
    assert "initiative share 50%" in text.splitlines()[0]


def test_the_keepalive_rests_past_the_share_and_names_it_when_still_inside(worker, monkeypatch):
    from brr.loom.runtime.config import set_value
    from brr.loom.runtime.home import home_of_room
    room, _ = worker
    home = home_of_room(room)
    set_value(home.root, "initiative_share", 50)
    rested = _stop(room, monkeypatch, _window(33, 0.24, time.time()))
    assert rested["reason"].partition(chr(10))[2].startswith(
        "wakes at: the next letter")
    kept = _stop(room, monkeypatch, _window(92, 5.5 / 7, time.time()))
    assert kept["decision"] == "block"
    assert "initiative share 50%" in kept["reason"]
    assert ": stop" not in kept["reason"]


def test_the_fuel_line_names_every_engine_ahead_of_pace(ledger, monkeypatch):
    home, _ = ledger
    now = time.time()

    def gauge(pct, days):
        reset = datetime.fromtimestamp(now + days * 86400, timezone.utc).isoformat()
        return {"at": now, "windows": [{"name": "week", "left_pct": pct, "resets_at": reset}]}

    gauges = {"codex": gauge(65, 3.8), "grok": gauge(71, 1.5), "claude": gauge(20, 5.5)}
    monkeypatch.setattr(usage, "shells", lambda home: ["codex", "grok", "claude", "none"])
    monkeypatch.setattr(usage, "reading", lambda home, shell, *args, **kwargs: gauges.get(shell))
    assert idle.fuel_line(home, now) == "fuel readings: codex week 65% left, 3.8d to reset; grok 71% left, 1.5d"
    text = idle.render(["do the thing"], idle.fuel_line(home, now))
    assert text.startswith(idle.HEAD) and 'wish <id> --pick "<why>"' in text and "need most now" in text
    assert "\nfuel readings: codex week 65% left, 3.8d to reset; grok 71% left, 1.5d\n  - do the thing\n" in text
    out = idle.keepalive(text, AHEAD, "NEWS")
    assert out.startswith("cache keepalive, fuel ahead of pace (week 92% left")
    assert "fuel readings: codex" in out and "  - do the thing" in out and ": stop" not in out
    assert "or end with one line saying why not" in out
    # The inbox block shows when another engine is ahead, even if this shell is behind.
    (home.room("s-aaaa-test") / "port/in").mkdir(parents=True, exist_ok=True)
    context = SimpleNamespace(thread="inbox", shell="claude")
    quiet = {"face": PROPOSED["quiet"], "token": "quiet since 20:00", "fact": "quiet:x"}
    block = loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    assert "fuel readings: codex week 65% left, 3.8d to reset; grok 71% left, 1.5d" in block
    assert (home.room("s-aaaa-test") / "port/in/idle.md").read_text().count("fuel readings:") == 1
    work = SimpleNamespace(thread="work", shell="claude")
    got = loom._idle_acts(home, read_facts(home), "s-aaaa-test", work, quiet, ())
    assert "fuel readings" not in got and idle.HEAD not in got
    assert not (home.room("s-aaaa-test") / "port/in/idle.md").exists()


@pytest.mark.parametrize("prefix", ["fuel readings", "fuel to burn"])
def test_keepalive_follows_the_fuel_line_when_the_room_shell_is_behind(worker, monkeypatch, prefix):
    room, _ = worker
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: BEHIND)
    text = idle.render(["w-aaaa is heard, never read"], f"{prefix}: codex week 65% left, 3.8d to reset")
    (room / "port/in/idle.md").write_text(text)
    result = json.loads(jack.execute("stop", room, "{}"))
    assert result["decision"] == "block"
    reason = result["reason"]
    assert "codex week 65% left, 3.8d to reset" in reason and "w-aaaa is heard, never read" in reason
    assert ": stop" not in reason


def test_bytes_removed_counts_a_folded_section_and_not_the_note():
    wake = idle._seed_wake()
    body = "x" * 200 + "\n"
    original = "## Spec\nkeep\n## Notes\n" + body
    rendered = wake.fold(original, 50)
    assert idle._bytes_removed(original, rendered) == len(body.encode())


def test_a_readme_cut_at_wake_names_the_recipes_own_numbers(ledger, monkeypatch):
    home, seat = ledger
    path = home.root / "self" / "threads" / "work" / "README.md"
    path.write_text("line of the readme\n" * 4000, encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    root = home.root / "self"
    stat = path.stat()
    key = (str(root), "work", stat.st_mtime_ns, stat.st_size)
    idle._FIXED.clear()
    idle._AROUND.clear()
    monkeypatch.setenv("LOOM_BUDGET_BYTES", "10000000")
    wake = idle._seed_wake()
    total = idle._fixed_total(wake, root, "work", key)
    reserved = idle._around(home, root, "work", read_facts(home), key)
    excess = 2500
    monkeypatch.setenv("LOOM_BUDGET_BYTES", str(total - excess + reserved))
    key_line, line = idle.unease(home, read_facts(home), "work", seat)
    size = len(text.encode())
    folded = wake.fold(text, excess)
    saved = size - len(folded.encode())
    recipe_limit = total - excess
    rendered = wake.clip(folded, excess - saved, recipe_limit) if excess - saved > 0 else folded
    cut = idle._bytes_removed(text, rendered)
    note = re.search(r"Cut (\d+) of (\d+) README bytes", rendered)
    assert note and int(note.group(1)) == cut and int(note.group(2)) == size
    assert (key_line, line) == ("readme:work",
                                f"unease: cut {cut} of {size} README bytes at wake. Whole on disk.")


def test_the_oldest_unanswered_ask_is_the_inbox_unease(ledger, monkeypatch):
    home, seat = ledger
    monkeypatch.setenv("LOOM_BUDGET_BYTES", "10000000")
    idle._FIXED.clear()
    idle._AROUND.clear()
    wish, _ = _seat(home, seat, monkeypatch, AHEAD)
    ask = command(home, seat, "ask", "Pick a colour", "--for", wish)
    from brr.loom.runtime.wishes import wishes
    born = idle._epoch(wishes(read_facts(home))[ask].born)
    room = home.room("s-aaaa-test")
    assert not idle.unease(home, read_facts(home), "inbox", room, born + idle.WEEK)[0].startswith("ask:")
    key, line = idle.unease(home, read_facts(home), "inbox", room, born + 8 * 86400 + 60)
    assert (key, line) == (f"ask:{ask}",
                           f"unease: ask {ask} to test unanswered 8d: follow it up once, or close it")
    assert not idle.unease(home, read_facts(home), "work", room, born + 8 * 86400 + 60)[0].startswith("ask:")


def test_unanswered_ask_unease_names_the_waiting_thread_and_counts_its_wishes(ledger, monkeypatch):
    from brr.loom.runtime.wishes import wishes

    home, seat = ledger
    monkeypatch.setenv("LOOM_BUDGET_BYTES", "10000000")
    first, _ = _seat(home, seat, monkeypatch, AHEAD)
    ask = command(home, seat, "ask", "Pick a colour", "--for", first)
    second = heard(home, seat, "Paint the door")
    for waiter in (first, second):
        command(home, seat, "wish", waiter, "--read", "waiting: for a colour", "--wait", f"wish:{ask}",
                "--until", "+10d")

    facts = read_facts(home)
    born = idle._epoch(wishes(facts)[ask].born)
    room = home.room("s-aaaa-test")
    assert idle.unease(home, facts, "inbox", room, born + 8 * 86400) == (
        f"ask:{ask}", f"unease: ask {ask} to test unanswered 8d: write to thread:work first · 2 ride on this",
    )


def test_unease_is_recomputed_at_most_once_per_window(ledger, monkeypatch):
    home, seat = ledger
    _, context = _seat(home, seat, monkeypatch, AHEAD)
    calls = []
    real = idle.unease
    monkeypatch.setattr(idle, "unease", lambda *args, **kwargs: calls.append(args) or real(*args, **kwargs))
    quiet = {"face": PROPOSED["quiet"], "token": "quiet since 20:00", "fact": "quiet:x"}
    loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    assert len(calls) == 1
    path = home.room("s-aaaa-test") / "port/in/unease.md"
    old = time.time() - idle.EVERY - 1
    os.utime(path, (old, old))
    loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    assert len(calls) == 2


def _count(room, key="readme:work"):
    return json.loads((room / "port" / "unease.json").read_text())[key]


def test_twenty_renders_of_one_quiet_face_count_once(ledger, monkeypatch):
    home, seat = ledger
    _, context = _seat(home, seat, monkeypatch, AHEAD)
    room = home.room("s-aaaa-test")
    line = "unease: cut 10 of 20 README bytes at wake. Whole on disk."
    idle.publish_unease(room, "readme:work", line)
    fact = "quiet:s-aaaa-test:1:2026-11-22T05:33:00+00:00"
    quiet = {"face": PROPOSED["quiet"], "token": "quiet since 05:33", "fact": fact}
    last = ""
    for _ in range(20):
        last = loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    record = _count(room)
    assert record["n"] == 1
    assert record["mark"] == fact
    assert line in last and "nothing changed" not in last
    nxt = "quiet:s-aaaa-test:1:2026-11-22T06:26:00+00:00"
    last = loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, {**quiet, "fact": nxt}, ())
    record = _count(room)
    assert record["n"] == 2 and record["mark"] == nxt
    assert line in last and "nothing changed" not in last


def test_the_jack_counts_each_unmarked_show(tmp_path):
    room = tmp_path / "room"
    line = "unease: cut 10 of 20 README bytes at wake. Whole on disk."
    idle.publish_unease(room, "readme:work", line)
    for n in range(1, 4):
        assert jack._unease(room) == line
        assert _count(room)["n"] == n
    assert "mark" not in _count(room)
    # A keepalive counts, and leaves the quiet face's mark, so the next render of it does not.
    fact = "quiet:s-aaaa-test:1:2026-11-22T05:33:00+00:00"
    assert idle.present_unease(room, mark=fact) == line
    assert _count(room)["n"] == 4 and _count(room)["mark"] == fact
    shown = jack._unease(room)
    assert shown.startswith(line) and "shown 5 times" in shown
    assert _count(room)["n"] == 5 and _count(room)["mark"] == fact
    assert idle.present_unease(room, mark=fact) == shown
    assert _count(room)["n"] == 5


def test_a_new_unease_key_resets_the_shown_count(tmp_path):
    room = tmp_path / "room"
    now = datetime(2026, 11, 22, 5, 33, tzinfo=timezone.utc).timestamp()
    first = "unease: cut 10 of 20 README bytes at wake. Whole on disk."
    idle.publish_unease(room, "readme:work", first)
    for _ in range(6):
        idle.present_unease(room, now)
    assert _count(room)["n"] == 6
    nxt = "unease: ask w-1 to test unanswered 8d: follow it up once, or close it"
    idle.publish_unease(room, "ask:w-1", nxt)
    assert idle.present_unease(room, now) == nxt
    kept = json.loads((room / "port" / "unease.json").read_text())
    assert list(kept) == ["ask:w-1"] and kept["ask:w-1"]["n"] == 1


def test_a_shown_key_that_stops_is_dropped(tmp_path):
    room = tmp_path / "room"
    line = "unease: memory/scars/x.md blocked 4 times in 7d: make it a check, or rewrite it"
    idle.publish_unease(room, "scar:memory/scars/x.md", line)
    now = datetime(2026, 11, 22, 1, 10, tzinfo=timezone.utc).timestamp()
    for _ in range(4):
        assert idle.present_unease(room, now) == line
    assert idle.present_unease(room, now) == (
        line + " (shown 5 times since 2026-11-22 01:10Z, nothing changed: "
        "act, take it to the person, or retire it)")
    nxt = "unease: ask w-1 to test unanswered 8d: follow it up once, or close it"
    idle.publish_unease(room, "ask:w-1", nxt)
    assert idle.present_unease(room, now) == nxt
    assert list(json.loads((room / "port" / "unease.json").read_text())) == ["ask:w-1"]
    idle.publish_unease(room, "", "")
    assert not (room / "port" / "unease.json").exists()
    # The empty file is the stamp. A show of nothing must not delete it.
    assert (room / "port" / "in" / "unease.md").read_text() == ""
    assert idle.present_unease(room, now) == ""
    assert (room / "port" / "in" / "unease.md").read_text() == ""


def test_the_stop_shows_unease_behind_pace(worker, monkeypatch):
    room, _ = worker
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: BEHIND)
    (room / "port/in/idle.md").write_text(BLOCK)
    idle.publish_unease(room, "readme:work",
                        "unease: cut 10 of 20 README bytes at wake. Whole on disk.")
    result = json.loads(jack.execute("stop", room, "{}"))
    assert "wakes at: the next letter" in result["reason"]
    assert "unease: cut " in result["reason"]
    assert json.loads((room / "port" / "unease.json").read_text())["readme:work"]["n"] == 1


_GOAL_LINE = ("goal growth: 1 of 4 accounts pays · next: 3 paying · now: find why 3 of 4 do not pay"
              " · stake: brnrd has no future without paying users")
_GOAL_CLOSE = ("No letter owed. Which of these goals matters most now, and what is its next move? "
               "Make that move, or record what it waits on.")


def _write_goal(home, name, status="active", date=None):
    repo = home.root / "self"
    path = repo / "goals" / f"{name}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        f"---\nstatus: {status}\n"
        'stands: "1 of 4 accounts pays"\nnext: 3 paying\n'
        "now: find why 3 of 4 do not pay\nstake: brnrd has no future without paying users\n"
        "---\n# Growth\n\nRoute and history.\n", encoding="utf-8")
    commit(repo, f"goal {name}", date)


def _inbox_idle(home):
    context = SimpleNamespace(thread="inbox", shell="claude")
    quiet = {"face": PROPOSED["quiet"], "fact": "quiet:x"}
    return loom._idle_acts(home, read_facts(home), STRAND, context, quiet, ())


def test_inbox_idle_shows_goals_and_stakes_without_a_pick_menu(ledger, monkeypatch):
    home, seat = ledger
    wish, context = _seat(home, seat, monkeypatch, AHEAD)
    _write_goal(home, "growth")
    _write_goal(home, "retention")
    monkeypatch.setattr(idle, "fuel_line", lambda home: "fuel readings: codex week 65% left, 3.8d to reset")
    monkeypatch.setattr(idle, "unease", lambda *args: ("goal:test", "unease: check growth"))
    quiet = {"face": PROPOSED["quiet"], "fact": "quiet:x"}
    old_menu = loom._idle_acts(home, read_facts(home), STRAND, context, quiet, ())
    assert wish in old_menu and "--pick" in old_menu

    block = _inbox_idle(home)
    assert block.splitlines() == [
        _GOAL_LINE,
        _GOAL_LINE.replace("growth:", "retention:", 1),
        "fuel readings: codex week 65% left, 3.8d to reset",
        "unease: check growth",
        _GOAL_CLOSE,
    ]


def test_paused_goals_are_absent_from_the_inbox_idle_block(ledger, monkeypatch):
    home, seat = ledger
    _seat(home, seat, monkeypatch, AHEAD)
    _write_goal(home, "paused-goal", status="paused")
    _write_goal(home, "growth")
    block = _inbox_idle(home)
    assert _GOAL_LINE in block
    assert "paused-goal" not in block


def test_inbox_goal_is_unmoved_at_three_days_since_its_last_landed_change(ledger, monkeypatch):
    home, seat = ledger
    _seat(home, seat, monkeypatch, AHEAD)
    now = int(time.time())
    monkeypatch.setattr(idle.time, "time", lambda: now)
    _write_goal(home, "growth", date=datetime.fromtimestamp(now - 3 * 86400, timezone.utc).isoformat())
    _write_goal(home, "recent", date=datetime.fromtimestamp(now - 3 * 86400 + 1, timezone.utc).isoformat())
    lines = _inbox_idle(home).splitlines()
    assert lines[:2] == [_GOAL_LINE + " · unmoved 3d", _GOAL_LINE.replace("growth:", "recent:", 1)]


def test_inbox_idle_with_no_goals_prompts_for_one(ledger, monkeypatch):
    home, seat = ledger
    _seat(home, seat, monkeypatch, AHEAD)
    block = _inbox_idle(home)
    prompt = "no active goal: write one under goals/"
    assert block.splitlines()[0] == prompt
    assert block.count(prompt) == 1


def test_inbox_keepalive_shows_goals_before_unease_and_the_closing_line(worker, monkeypatch):
    room, _ = worker
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: BEHIND)
    fuel = "fuel readings: codex week 65% left, 3.8d to reset"
    (room / "port/in/idle.md").write_text(f"{_GOAL_LINE}\n{fuel}\n", encoding="utf-8")
    idle.publish_unease(room, "goal:test", "unease: check growth")
    reason = json.loads(jack.execute("stop", room, "{}"))["reason"]
    assert "wakes at: the next letter\n" in reason
    assert reason.endswith(f"{_GOAL_LINE}\n{fuel}\nunease: check growth\n{_GOAL_CLOSE}\n")
    assert "--pick" not in reason
