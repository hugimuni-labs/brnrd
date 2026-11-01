"""Under memory pressure the loop starts no new body; clear pressure starts it (memory.py)."""

import time

import pytest

from brr.loom.runtime import memory
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.loom import MEMORY_WORD

from _step import Loom, mail, wait_until, write_thread

GB = 1024 ** 3


@pytest.fixture
def kernel(monkeypatch):
    read = {"now": (memory.CRITICAL, (4 * GB, 1 * GB))}
    monkeypatch.setattr(memory, "reading", lambda: read["now"])
    monkeypatch.setattr(memory, "EVERY", 0.0)
    memory._LAST.clear()
    return read


@pytest.mark.parametrize("level, total, used, holds", [
    (memory.NORMAL, 8, 8.0, False),
    # macOS grows the swap total on demand: 3.8 of 4 GB is the 11-01 iMac at work, not short.
    (memory.WARN, 4, 3.8, False),
    (memory.WARN, 8, 6.0, False),
    (memory.WARN, 8, 7.0, True),
    (memory.CRITICAL, 4, 0.0, True),
])
def test_the_floor_holds_at_critical_and_at_warn_past_an_absolute_swap(level, total, used, holds):
    assert (memory.judge((level, (total * GB, int(used * GB)))) is not None) is holds
    assert memory.judge(None) is None


def test_memory_swap_gb_moves_the_warn_bar(tmp_path, kernel):
    from brr.loom.runtime.config import load_config
    root = tmp_path / "home"
    write_thread(root, "work", "# work", "quit-now", wait="0")
    kernel["now"] = (memory.WARN, (8 * GB, 3 * GB))
    assert memory.pressed(root) is None
    with (root / "loom/config.toml").open("a") as cfg:
        cfg.write("memory_swap_gb = 2\n")
    assert load_config(root).memory_swap_gb == 2
    assert memory.pressed(root) is not None


def test_a_person_held_by_the_floor_hears_one_word_per_episode(tmp_path, monkeypatch, kernel):
    from test_lifecycle import _parked_home
    kernel["now"] = (memory.WARN, (8 * GB, 7 * GB))
    home = _parked_home(tmp_path, monkeypatch, left="70")
    looming = Loom(home.root)
    looming.start()
    try:
        wait_until(lambda: len(home.sent) == 1, 10, looming.dump)
        assert home.sent[0]["to"] == "telegram:42"
        assert home.sent[0]["re"] == "telegram:42:park"
        assert MEMORY_WORD in home.sent[0]["body"]
        home.person.says("again", "are you there?")
        wait_until(lambda: len(mail(home.root, looming.facts(), "inbox")) >= 2, 10, looming.dump)
        time.sleep(0.5)  # more ticks under the floor, a second letter in the same episode
        assert len(home.sent) == 1
        assert not any(f.kind == "body.started" for f in looming.facts())
    finally:
        looming.halt()
    assert not looming.errors


def test_a_pressed_kernel_holds_a_wanted_body_and_clear_pressure_starts_it(tmp_path, kernel):
    root = tmp_path / "home"
    write_thread(root, "work", "# work", "quit-now", wait="0")
    inject_letter(root, to="thread:work", body="start when there is room")
    looming = Loom(root, tick=0.02)
    looming.start()
    try:
        wait_until(lambda: any(f.kind == "body.pending" for f in looming.facts())
                   and any(f.kind == "loom.degraded" and f.data.get("step") == "memory"
                           for f in looming.facts()), 10, looming.dump)
        time.sleep(0.5)  # a few more ticks under the floor
        assert not any(f.kind == "body.started" for f in looming.facts())
        [pending] = [f for f in looming.facts() if f.kind == "body.pending"]
        assert pending.data["why"].startswith("memory pressure critical; swap 1024 of 4096 MB used")
        kernel["now"] = (memory.WARN, (4 * GB, 2 * GB))
        wait_until(lambda: any(f.kind == "body.started" for f in looming.facts())
                   and any(f.kind == "loom.recovered" for f in looming.facts()), 15, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors
    [down] = [f for f in read_facts(Home(root)) if f.kind == "loom.degraded"]
    assert down.data["error"].startswith("MemoryPressure: memory pressure critical")


def test_memory_floor_off_starts_under_pressure(tmp_path, kernel):
    root = tmp_path / "home"
    write_thread(root, "work", "# work", "quit-now", wait="0")
    with (root / "loom/config.toml").open("a") as cfg:
        cfg.write('memory_floor = "off"\n')
    inject_letter(root, to="thread:work", body="go anyway")
    looming = Loom(root, tick=0.02)
    looming.start()
    try:
        wait_until(lambda: any(f.kind == "body.started" for f in looming.facts()), 10, looming.dump)
    finally:
        looming.halt()
    facts = read_facts(Home(root))
    assert not any(f.kind == "loom.degraded" and f.data.get("step") == "memory" for f in facts)
    assert not any(f.kind == "body.pending" and f.data.get("why") for f in facts)


@pytest.mark.skipif(memory.sys.platform != "darwin", reason="the kernel read is darwin's")
def test_the_kernel_read_is_one_of_its_levels():
    memory._LAST.clear()
    level, swap = memory.reading()
    assert level in {memory.NORMAL, memory.WARN, memory.CRITICAL}
    assert swap is None or swap[1] <= swap[0]


def test_status_names_the_hold(tmp_path, kernel, capsys):
    from brr.loom.runtime import service
    root = tmp_path / "home"
    write_thread(root, "work", "# work", "quit-now", wait="0")
    kernel["now"] = (memory.WARN, (8 * GB, 7 * GB))
    service._print_status(state="running", pid=1, home=root)
    assert "held: memory pressure warn, swap 7.0 GB, no new body starts" in capsys.readouterr().out
    kernel["now"] = (memory.WARN, (4 * GB, int(3.8 * GB)))
    service._print_status(state="running", pid=1, home=root)
    assert "held:" not in capsys.readouterr().out
