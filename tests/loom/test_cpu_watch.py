"""The loom degrades ``cpu`` when its own CPU goes past 35% over ten minutes, and clears only under 28%."""
import pytest

from brr.loom.runtime.cpu import CpuHigh, CpuWatch


class Clock:
    def __init__(self):
        self.now, self.cpu = 0.0, 0.0

    def run(self, watch, seconds, share, step=10.0):
        """Advance ``seconds`` at ``share`` of a core, stepping the watch every ``step``."""
        raised = None
        for _ in range(int(seconds / step)):
            self.now += step
            self.cpu += step * share
            try:
                watch.step()
                raised = None
            except CpuHigh as exc:
                raised = exc
        return raised


def watch_for(clock):
    return CpuWatch(clock=lambda: clock.now, usage=lambda: clock.cpu)


def test_nothing_is_said_before_a_full_window():
    clock = Clock()
    watch = watch_for(clock)
    watch.step()
    assert clock.run(watch, 590, 0.9) is None


def test_sustained_past_the_limit_raises_and_back_under_clears():
    clock = Clock()
    watch = watch_for(clock)
    watch.step()
    assert clock.run(watch, 600, 0.2) is None
    high = clock.run(watch, 900, 0.86)
    assert high is not None and high.share == pytest.approx(0.86, abs=0.02)
    assert high.grade == 1 and "past 35%" in str(high)
    assert clock.run(watch, 900, 0.1) is None


def test_a_short_burst_inside_the_window_does_not_raise():
    clock = Clock()
    watch = watch_for(clock)
    watch.step()
    clock.run(watch, 600, 0.2)
    assert clock.run(watch, 60, 1.0) is None  # one busy minute: ~28% over the window


def test_ticks_between_samples_repeat_the_reading():
    clock = Clock()
    watch = watch_for(clock)
    watch.step()
    clock.run(watch, 660, 0.9)
    clock.now += 1  # a tick inside the sampling interval
    with pytest.raises(CpuHigh):
        watch.step()


def test_the_guard_writes_a_cpu_degradation_and_its_recovery(tmp_path):
    from brr.loom.runtime.health import Health
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import read_facts
    home = Home(tmp_path, install="ae66")
    home.facts_dir()
    health = Health(home, [], lambda home, text: None)
    clock = Clock()
    watch = watch_for(clock)

    def tick(seconds, share):
        for _ in range(int(seconds / 10)):
            clock.now += 10
            clock.cpu += 10 * share
            health.guard("cpu", watch.step, backoff=False)

    tick(600, 0.9)
    tick(60, 0.9)
    degraded = [f for f in read_facts(home) if f.kind == "loom.degraded" and f.data["step"] == "cpu"]
    assert len(degraded) == 1 and "past 35%" in degraded[0].data["error"]
    tick(900, 0.05)
    assert any(f.kind == "loom.recovered" and f.data["step"] == "cpu" for f in read_facts(home))


def test_a_streak_starts_one_profile_and_each_raise_names_it():
    clock, started = Clock(), []
    watch = CpuWatch(clock=lambda: clock.now, usage=lambda: clock.cpu,
                     on_high=lambda: started.append(1) or "loom/samples/x.collapsed")
    watch.step()
    high = clock.run(watch, 900, 0.9)
    assert started == [1] and str(high).endswith("; profile loom/samples/x.collapsed")
    assert clock.run(watch, 900, 0.1) is None
    clock.run(watch, 900, 0.9)
    assert started == [1, 1]  # a new streak, a new profile


def wander(clock, watch, shares, seconds=60):
    """Run each share for ``seconds``; return the raise (or None) seen at the end of each."""
    return [clock.run(watch, seconds, share) for share in shares]


def test_a_share_wandering_around_30_percent_raises_nothing():
    clock, profiles = Clock(), []
    watch = CpuWatch(clock=lambda: clock.now, usage=lambda: clock.cpu, on_high=lambda: profiles.append(1))
    watch.step()
    clock.run(watch, 600, 0.30)
    # Two hours wandering 29-32%: under the old single bar at 30% this raised and cleared at each crossing.
    raised = wander(clock, watch, [0.29, 0.32, 0.30, 0.31, 0.29, 0.32, 0.32, 0.30] * 15)
    assert raised == [None] * len(raised) and profiles == []


def test_one_spell_past_the_raise_bar_is_one_streak_until_under_the_clear_bar():
    from brr.loom.runtime.cpu import CLEAR, LIMIT
    assert CLEAR < 0.30 < LIMIT  # the bars sit around a working loom's ~30%, not on it
    clock, profiles = Clock(), []
    watch = CpuWatch(clock=lambda: clock.now, usage=lambda: clock.cpu,
                     on_high=lambda: profiles.append(1) or "loom/samples/x.collapsed")
    watch.step()
    clock.run(watch, 600, 0.30)
    shares, streaks, was = [], 0, None
    for share, seconds in [(0.45, 900), (0.30, 1200), (0.33, 600), (0.29, 600), (0.20, 900), (0.30, 600)]:
        for _ in range(int(seconds / 10)):
            clock.now, clock.cpu = clock.now + 10, clock.cpu + 10 * share
            try:
                watch.step()
                high = None
            except CpuHigh as exc:
                high = exc
            if high is not None and was is None:
                streaks += 1
            if high is not None:
                shares.append(high.share)
            was = high
    assert streaks == 1 and profiles == [1]
    assert max(shares) > LIMIT and CLEAR <= min(shares) < LIMIT  # it held between the bars, let go only under CLEAR
    assert was is None  # back at 30% after clearing: still nothing


def test_a_raise_between_the_bars_says_so():
    clock = Clock()
    watch = watch_for(clock)
    watch.step()
    clock.run(watch, 900, 0.5)
    high = clock.run(watch, 600, 0.31)
    assert high is not None and "back under 35%, not yet under 28%" in str(high)
