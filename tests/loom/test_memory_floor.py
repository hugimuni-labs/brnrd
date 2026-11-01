"""Under memory pressure the loop starts no new body; clear pressure starts it (memory.py)."""

import time

import pytest

from brr.loom.runtime import memory
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts

from _step import Loom, wait_until, write_thread

GB = 1024 ** 3


@pytest.fixture
def kernel(monkeypatch):
    read = {"now": (memory.CRITICAL, (4 * GB, 1 * GB))}
    monkeypatch.setattr(memory, "reading", lambda: read["now"])
    monkeypatch.setattr(memory, "EVERY", 0.0)
    memory._LAST.clear()
    return read


@pytest.mark.parametrize("level, used, holds", [
    (memory.NORMAL, 4.0, False),
    (memory.WARN, 3.0, False),
    (memory.WARN, 3.1, True),
    (memory.CRITICAL, 0.0, True),
])
def test_the_floor_holds_at_critical_and_at_warn_with_swap_past_three_quarters(level, used, holds):
    assert (memory.judge((level, (4 * GB, int(used * GB)))) is not None) is holds
    assert memory.judge(None) is None


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
