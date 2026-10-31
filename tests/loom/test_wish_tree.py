"""Each wish carries what is left: the stop asks for it, molt refuses without it, the wake shows the tree."""
import json
from io import StringIO
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import jack
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.loom import _boundary, _ingest, tick_once
from brr.loom.runtime.port import PortError, write_molt
from brr.loom.runtime.selfrepo import run_wake
from brr.loom.runtime.wishes import wishes
from test_wishes import command, heard, ledger  # noqa: F401  (fixture)

STRAND = "s-aaaa-test"


@pytest.fixture
def body(ledger, monkeypatch):
    """The fixture's leased body, its port, and a jack whose clock the test owns."""
    home, seat = ledger
    (seat / "port/in").mkdir(parents=True, exist_ok=True)
    (seat / "port/out").mkdir(exist_ok=True)
    monkeypatch.setenv("BRNRD_THREAD", "work")
    monkeypatch.setenv("BRNRD_BODY_JACK", "hooks")
    monkeypatch.delenv("BRNRD_BODY_SHELL", raising=False)
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "100")
    now = [0.]
    monkeypatch.setattr(jack.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(jack.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return home, seat


def render(home):
    _boundary(home, read_facts(home), STRAND, 1, SimpleNamespace(thread="work", fuel="", why=""))
    return (home.room(STRAND) / "port/in/boundary.md").read_text()


def left_line(home):
    return next((line for line in render(home).splitlines() if line.startswith("left? ")), "")


def stop(seat):
    """A stop with no call since the last one."""
    return jack.execute("stop", seat, "{}")


def wake(seat):
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    return output.getvalue().split("## Wishes\n")[1].split("## Notes")[0]


def test_a_stop_is_refused_once_until_the_body_says_what_is_left(body):
    home, seat = body
    wish = heard(home, seat, "Hold the board still")
    assert left_line(home) == f"left? {wish} (no left yet)"
    refused = json.loads(stop(seat))
    assert refused["decision"] == "block"
    assert f"{wish} (no left yet)" in refused["reason"]
    assert f'wish <w-id> --left "<step>"' in refused["reason"]
    # Stopped again with nothing done: let through, and the ask is a fact through the port.
    assert "what is left" not in stop(seat)
    _ingest(home)
    assert [f.data["wishes"] for f in read_facts(home) if f.kind == "left.asked"] == [[wish]]
    assert left_line(home) == ""
    assert "what is left" not in stop(seat)
    # A left answers it, and the wake shows it under the wish.
    command(home, seat, "wish", wish, "--left", "freeze hover", "--left", "test on the phone")
    assert left_line(home) == ""
    assert wishes(read_facts(home))[wish].left["steps"] == ["freeze hover", "test on the phone"]
    assert "  left (gen 1, " in wake(seat) and "): freeze hover · test on the phone" in wake(seat)


def test_a_steer_after_the_left_is_asked_again_and_shown_at_wake(body):
    home, seat = body
    wish = heard(home, seat, "Hold the board still")
    command(home, seat, "wish", wish, "--left", "freeze hover")
    assert left_line(home) == ""
    steer = inject_letter(home.root, to="thread:work", body="Also while the sheet is open")
    command(home, seat, "wish", wish, "--say", steer)
    assert left_line(home) == f"left? {wish} (steered: {steer})"
    assert f'steered since: {steer} "Also while the sheet is open"' in wake(seat)
    assert f"{wish} (steered: {steer})" in json.loads(stop(seat))["reason"]
    command(home, seat, "wish", wish, "--left", "same")
    assert left_line(home) == ""
    tree = wake(seat)
    assert "steered since" not in tree and "): freeze hover" in tree


def test_a_left_written_just_before_the_stop_is_seen_before_refusing(body, monkeypatch):
    home, seat = body
    wish = heard(home, seat, "Hold the board still")
    assert left_line(home)
    command(home, seat, "wish", wish, "--left", "freeze hover")
    # The loom renders on its next tick, while the jack waits out its grace.
    ticks = []
    monkeypatch.setattr(jack.time, "sleep", lambda seconds: ticks.append(render(home)))
    assert "what is left" not in stop(seat)
    assert ticks and not list((seat / "port/out").glob("asked-*.md"))


def test_a_loom_that_does_not_render_within_the_grace_lets_the_stop_through(body):
    home, seat = body
    wish = heard(home, seat, "Hold the board still")
    assert left_line(home)
    command(home, seat, "wish", wish, "--read", "the board is a canvas")
    # No loom renders: the boundary stays older than the ledger, and the stop fails open.
    assert "what is left" not in stop(seat)
    assert not list((seat / "port/out").glob("asked-*.md"))
    assert "  left: none said yet" in wake(seat)


def test_a_fact_that_leaves_the_boundary_unchanged_still_gets_the_left_asked(body, monkeypatch):
    home, seat = body
    wish = heard(home, seat, "Hold the board still")
    elsewhere = command(home, seat, "wish", "new", "--line", "Other", "--thread", "other",
                        "--say", inject_letter(home.root, to="thread:other", body="Please: other"))
    before = render(home)
    # A reading on another thread's wish: the ledger moves, this boundary's text does not.
    command(home, seat, "wish", elsewhere, "--read", "looks small")
    now = [0.]
    monkeypatch.setattr(jack.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(jack.time, "sleep", lambda seconds: (render(home), now.__setitem__(0, now[0] + seconds)))
    assert (seat / "port/in/boundary.md").read_text() == before
    assert f"{wish} (no left yet)" in json.loads(stop(seat))["reason"]


def test_a_new_body_restates_the_only_wish_but_not_unnamed_ones(body):
    home, seat = body
    first = heard(home, seat, "Hold the board still")
    command(home, seat, "wish", first, "--left", "freeze hover")

    def lease(gen):
        append(home, Fact(kind="lease", by="loom:aaaa", data={
            "thread": "work", "strand": STRAND, "gen": gen, "install": "aaaa", "router_gen": 1,
            "shell": "fake", "core": "fake", "repos": []}))

    lease(2)
    # The thread's only wish: all of this body's work is its work.
    assert left_line(home) == f"left? {first} (this body's work)"
    command(home, seat, "wish", first, "--left", "same")
    second = heard(home, seat, "Overlay opens")
    command(home, seat, "wish", second, "--left", "draw it")
    lease(3)
    # Two wishes: the loom cannot tell which one unnamed work served, so it asks for neither.
    assert left_line(home) == ""
    command(home, seat, "wish", second, "--read", "the overlay is a sheet")
    assert left_line(home) == f"left? {second} (named by this body)"


def test_left_none_and_same_without_a_left_are_refused(body):
    home, seat = body
    wish = heard(home, seat, "Hold the board still")
    assert "--deliver" in command(home, seat, "wish", wish, "--left", "none", ok=False)
    assert "has no left yet" in command(home, seat, "wish", wish, "--left", "same", ok=False)
    assert "only the body" in command(home, seat, "wish", wish, "--left", "x", person=True, ok=False)
    assert not [f for f in read_facts(home) if f.kind == "wish.left"]


def test_molt_refuses_while_a_left_is_owed(body):
    home, seat = body
    wish = heard(home, seat, "Hold the board still")
    with pytest.raises(PortError, match=f"wish {wish} --left"):
        write_molt(seat, "context is heavy")
    assert not list((seat / "port/out").glob("*.md"))
    command(home, seat, "wish", wish, "--left", "freeze hover")
    readme = next(seat.glob("*/threads/work/README.md"))
    readme.write_text(readme.read_text() + "\n## Now\n- freeze hover\n")
    assert write_molt(seat, "context is heavy").startswith(STRAND + "/")


def test_a_pr_opened_on_a_one_wish_thread_is_its_result(body):
    home, seat = body
    wish = heard(home, seat, "Hold the board still")

    def opened(number):
        append(home, Fact(kind="pr.opened", by="strand:" + STRAND, data={
            "thread": "work", "repo": "brnrd", "number": number, "head": "a" * 40,
            "url": f"https://github.com/o/r/pull/{number}"}))

    opened(7)
    tick_once(home, {}, "fake", "haiku")
    tick_once(home, {}, "fake", "haiku")
    found = wishes(read_facts(home))
    assert [r["ref"] for r in found[wish].results] == ["https://github.com/o/r/pull/7"]
    assert "  done: https://github.com/o/r/pull/7" in wake(seat)
    other = heard(home, seat, "Overlay opens")
    opened(8)
    tick_once(home, {}, "fake", "haiku")
    found = wishes(read_facts(home))
    # Two open wishes: the pull request names neither, so the body's --result is the door.
    assert len(found[wish].results) == 1 and not found[other].results


def test_one_stop_shows_every_miss_at_once_until_each_is_cleared(body):
    home, seat = body
    wish = heard(home, seat, "Hold the board still")
    render(home)
    first = json.loads(stop(seat))["reason"]
    # The left and the ending in one block, each with its command: no maze of stops.
    assert f"{wish} (no left yet)" in first and 'wish <w-id> --left "<step>"' in first
    assert "has not said how it ends" in first and "settle work --why" in first
    jack.execute("post", seat, "{}")
    again = json.loads(stop(seat))["reason"]
    assert f"{wish} (no left yet)" in again and "has not said how it ends" in again
    command(home, seat, "wish", wish, "--left", "freeze hover")
    jack.execute("post", seat, "{}")
    render(home)
    third = json.loads(stop(seat))["reason"]
    # A left answers both (it says how the body ends); the letter still owed stays on the list.
    assert "what is left" not in third and "has not said how it ends" not in third
    assert third.startswith("the stop holds on 1 thing") and "--note" in third
    assert not list((seat / "port/out").glob("asked-*.md"))


def test_a_delivered_wish_takes_one_line_in_the_wake(body):
    # 10-31, gen 241: delivered wishes came in whole and crowded the README out.
    home, seat = body
    done = heard(home, seat, "Ship the switch")
    command(home, seat, "wish", done, "--left", "wait for CI")
    command(home, seat, "wish", done, "--result", "pr:https://example.com/9", "--line", "Shipped", "--deliver")
    live = heard(home, seat, "Paint the board")
    command(home, seat, "wish", live, "--left", "pick a palette")
    shown = wake(seat)
    assert f"{done} · delivered · Ship the switch · https://example.com/9" in shown
    assert "wait for CI" not in shown and "pick a palette" in shown
    assert sum(done in line for line in shown.splitlines()) == 1
