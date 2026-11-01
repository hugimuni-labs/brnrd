"""Under memory pressure a second worker body waits; the inbox and a first worker start (memory.py)."""

import time

import pytest

from brr.loom.runtime import memory
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.loom import MEMORY_WORD

from _step import Loom, wait_until, write_thread

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


def _starts(looming, *threads):
    """Body starts on ``threads``; the inbox may start again for a loom notice."""
    facts = looming.facts()
    on = {f.data["strand"] for f in facts if f.kind == "lease" and f.data.get("thread") in threads}
    return len([f for f in facts if f.kind == "body.started" and f.data.get("strand") in on])


def test_at_critical_the_inbox_and_a_first_worker_start_and_a_second_worker_waits(tmp_path, kernel):
    root = tmp_path / "home"
    write_thread(root, "inbox", "# inbox", "quit-now", wait="0")
    write_thread(root, "one", "# one", "hold", wait="0")
    write_thread(root, "two", "# two", "quit-now", wait="0")
    looming = Loom(root, tick=0.02)
    looming.start()
    try:
        inject_letter(root, to="thread:inbox", body="the inbox always starts")
        inject_letter(root, to="thread:one", body="the first worker starts")
        wait_until(lambda: _starts(looming, "inbox") >= 1 and _starts(looming, "one") == 1, 10, looming.dump)
        inject_letter(root, to="thread:two", body="a second worker waits")
        wait_until(lambda: any(f.kind == "body.pending" and f.data.get("why") for f in looming.facts()),
                   10, looming.dump)
        time.sleep(0.5)  # a few more ticks under the floor
        assert _starts(looming, "two") == 0
        [pending] = [f for f in looming.facts() if f.kind == "body.pending" and f.data.get("why")]
        assert pending.data["why"].startswith("memory pressure critical; swap 1024 of 4096 MB used")
        kernel["now"] = (memory.WARN, (4 * GB, 2 * GB))
        wait_until(lambda: _starts(looming, "two") == 1
                   and any(f.kind == "loom.recovered" for f in looming.facts()), 15, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors


def test_a_person_held_by_the_floor_hears_one_word_per_episode(tmp_path, monkeypatch, kernel):
    """The person's DM is faked; the hold, its episode and the notice are the loop's own."""
    from brr.loom.runtime import loom as loom_module
    root = tmp_path / "home"
    write_thread(root, "one", "# one", "hold", wait="0")
    write_thread(root, "two", "# two", "quit-now", wait="0")
    letters = []
    monkeypatch.setattr(loom_module, "_person_dms", lambda home, facts, thread: [
        (f, "fake") for f in facts if thread == "two" and f.kind == "letter" and f.data.get("id") in letters])
    looming = Loom(root, tick=0.02)
    looming.start()
    words = lambda: [f for f in looming.facts() if f.kind == "letter" and f.id.startswith("loom/memory-") and f.data.get("notice")]
    try:
        inject_letter(root, to="thread:one", body="busy")
        wait_until(lambda: _starts(looming, "one") == 1, 10, looming.dump)
        letters.append(inject_letter(root, to="thread:two", body="are you there?"))
        wait_until(lambda: len(words()) == 1, 10, looming.dump)
        letters.append(inject_letter(root, to="thread:two", body="hello?"))
        time.sleep(0.5)  # more ticks under the floor, a second letter in the same episode
        [word] = words()
        assert word.data["to"] == "fake" and MEMORY_WORD in word.data["body"]
        [held] = [f for f in looming.facts() if f.kind == "letter" and f.id.startswith("loom/memory-held-")]
        assert held.data["to"] == "thread:inbox"
        assert "Thread two waits on the memory floor (memory pressure critical" in held.data["body"]
        assert "brnrd runtime threads --force two" in held.data["body"]
        assert _starts(looming, "two") == 0
    finally:
        looming.halt()
    assert not looming.errors


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
    assert "held: memory pressure warn, swap 7.0 GB, no second worker body starts" in capsys.readouterr().out
    kernel["now"] = (memory.WARN, (4 * GB, int(3.8 * GB)))
    service._print_status(state="running", pid=1, home=root)
    assert "held:" not in capsys.readouterr().out
