"""A machine condition that flaps is one episode: told once, silent per dip, cleared once."""

import sys

import pytest

from brr.loom.runtime import health, reap
from brr.loom.runtime.health import Health, degraded_line
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.reap import Reaper

from _step import write_thread

GB = 1024 ** 3


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def time(self):
        return self.now


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    # Every disk() call reads the volume, as the scripted readings here need.
    monkeypatch.setattr(reap, "DISK_EVERY", 0.0)


@pytest.fixture
def disk(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "inbox", "Brnrd", "inbox")
    home = Home(root)
    clock, free = Clock(), {"bytes": 20 * GB}
    monkeypatch.setattr(health, "time", clock)
    monkeypatch.setattr(reap, "floor", lambda _path: (free["bytes"], 12 * GB))
    monkeypatch.setattr(reap, "space", lambda _path: (free["bytes"], free["bytes"], 228 * GB))
    reaper = Reaper(home, lambda *_: None)

    def guard(health_):
        # The loop's own call: run() guards reaper.disk under step "disk".
        health_.guard("disk", reaper.disk, backoff=False)

    def at(minutes, gb):
        clock.now += minutes * 60
        free["bytes"] = int(gb * GB)

    return home, clock, guard, at


def _letters(home, kind):
    return [f for f in read_facts(home) if f.kind == "letter" and f.data.get("from") == "loom"
            and f.id.startswith(f"loom/{kind}-")]


def test_a_flapping_disk_is_told_once_and_its_dips_carry_the_episode(disk):
    home, clock, guard, at = disk
    h = Health(home, read_facts(home), lambda *_: None)
    for _dip in range(4):
        at(1, 11.3); guard(h)    # dips under the 12 GB floor
        at(6, 11.3); guard(h)    # still under after NOTICE_AFTER: told, once
        at(1, 12.5); guard(h)    # climbs back
        at(30, 12.5); guard(h)   # up for half an hour, inside CLEAR_AFTER
    [told] = _letters(home, "degraded")
    assert "DiskFloor" in told.data["body"]
    downs = sorted((f for f in read_facts(home) if f.kind == "loom.degraded"), key=lambda f: f.data["since"])
    assert [f.data["dips"] for f in downs] == [1, 2, 3, 4]
    assert {f.data["episode"] for f in downs} == {downs[0].data["since"]}
    line = degraded_line(read_facts(home), home.install_id(), now=clock.now)
    assert line.startswith("degraded: disk up since") and "after 4 dips" in line and "told, watching" in line
    at(1, 11.3); guard(h)
    line = degraded_line(read_facts(home), home.install_id(), now=clock.now)
    assert "dip 5 since" in line and "DiskFloor at reap.py" in line


def test_a_restart_inside_an_episode_tells_nothing_new(disk):
    home, _clock, guard, at = disk
    h = Health(home, read_facts(home), lambda *_: None)
    at(1, 11.3); guard(h); at(6, 11.3); guard(h); at(1, 12.5); guard(h)
    again = Health(home, read_facts(home), lambda *_: None)
    at(20, 11.3); guard(again); at(6, 11.3); guard(again)
    assert len(_letters(home, "degraded")) == 1
    downs = [f for f in read_facts(home) if f.kind == "loom.degraded"]
    assert max(f.data["dips"] for f in downs) == 2


def test_a_dip_past_a_grade_is_told_again(disk):
    home, _clock, guard, at = disk
    h = Health(home, read_facts(home), lambda *_: None)
    at(1, 11.3); guard(h); at(6, 11.3); guard(h); at(1, 12.5); guard(h)
    # Under half the floor: worse by a step, told again; back at the first grade: silent.
    at(10, 5.0); guard(h); at(6, 5.0); guard(h); at(1, 12.5); guard(h)
    at(10, 11.0); guard(h); at(6, 11.0); guard(h)
    told = _letters(home, "degraded")
    assert len(told) == 2


def test_an_episode_that_stays_up_is_cleared_once_and_owed_by_nobody(disk):
    home, _clock, guard, at = disk
    h = Health(home, read_facts(home), lambda *_: None)
    at(1, 11.3); guard(h); at(6, 11.3); guard(h); at(1, 12.5); guard(h)
    at(health.CLEAR_AFTER / 60 + 1, 12.5); guard(h); guard(h)
    [cleared] = _letters(home, "cleared")
    assert cleared.data["notice"] is True and "is clear" in cleared.data["body"]
    assert degraded_line(read_facts(home), home.install_id(), now=_clock.now) == ""
    # A dip after the clear is a new episode, told again.
    at(1, 11.3); guard(h); at(6, 11.3); guard(h)
    assert len(_letters(home, "degraded")) == 2


def test_an_untold_dip_never_clears_and_leaves_no_gauge(disk):
    home, clock, guard, at = disk
    h = Health(home, read_facts(home), lambda *_: None)
    at(1, 11.3); guard(h); at(1, 12.5); guard(h)   # two minutes under: never told
    assert degraded_line(read_facts(home), home.install_id(), now=clock.now) == ""
    at(health.CLEAR_AFTER / 60 + 1, 12.5); guard(h)
    assert _letters(home, "degraded") == [] and _letters(home, "cleared") == []


def test_the_wake_folds_an_episodes_dips_into_one_line(tmp_path):
    from brr.daemon2.facts import Fact
    from brr.loom.runtime.ledger import append
    from brr.loom.runtime.since import since_gone

    root = tmp_path / "home"
    write_thread(root, "inbox", "Brnrd", "inbox")
    home = Home(root, install="aaaa")
    by, first = "loom:aaaa", "2026-10-28T03:25:00.000Z"
    append(home, Fact(kind="lease", by=by, id="lease:inbox:prev",
                      data={"thread": "inbox", "strand": "s-aaaa-before", "gen": 1}))
    append(home, Fact(kind="shown", by=by, data={"strand": "s-aaaa-before", "gen": 1, "ids": []}))
    for dip, since in enumerate([first, "2026-10-28T04:19:00.000Z", "2026-10-28T05:16:00.000Z"], 1):
        for kind, data in [("loom.degraded", {"error": "DiskFloor: 11300 MB free", "where": "loom/runtime/reap.py:288",
                                              "episode": first, "dips": dip}),
                           ("loom.recovered", {"count": 12, "last": "DiskFloor: 11300 MB free", "episode": first,
                                               "told": "DiskFloor at reap.py:288", "grade": 0, "up": since})]:
            append(home, Fact(kind=kind, by=by, id=f"{kind}:aaaa:disk:{since}",
                              data={"install": "aaaa", "step": "disk", "since": since, **data}))
    append(home, Fact(kind="lease", by=by, id="lease:inbox:next",
                      data={"thread": "inbox", "strand": "s-aaaa-after", "gen": 1}))
    section = since_gone(read_facts(home), "s-aaaa-after", 1, "inbox", {}, "aaaa")
    assert section.count("disk ") == 1
    assert f"disk dipped 3 times since {first}, the last at 2026-10-28T05:16:00.000Z" in section


def test_a_pass_still_under_the_floor_prunes_caches_and_the_gauge_says_so(disk, monkeypatch):
    home, clock, guard, at = disk
    ran = []

    real = reap.subprocess.run

    def run(command, **kw):
        if command[0] not in {c[0] for c in reap.PRUNE}:
            return real(command, **kw)
        ran.append(" ".join(command))
        at(0, 13.0)   # the prune lifts the volume over the floor

    monkeypatch.setattr(reap.shutil, "which", lambda name: f"/bin/{name}" if name != "pip" else None)
    monkeypatch.setattr(reap.subprocess, "run", run)
    monkeypatch.setattr(reap.Reaper, "reap", lambda self: None)
    monkeypatch.setattr(reap, "time", clock)
    h = Health(home, read_facts(home), lambda *_: None)
    reaper = Reaper(home, lambda *_: None)
    at(1, 11.3)
    with pytest.raises(reap.DiskFloor):
        reaper.disk()
    guard(h); at(6, 11.3); guard(h)
    reaper._pass()
    assert reaper.failing is None
    assert ran == ["uv cache prune", "npm cache verify"]
    [pruned] = [f for f in read_facts(home) if f.kind == "loom.pruned"]
    assert pruned.data["bytes"] == int(13.0 * GB) - int(11.3 * GB)
    guard(h)
    line = degraded_line(read_facts(home), home.install_id(), now=clock.now)
    assert "did: pruned caches, 1.7 GB freed" in line and "told, watching" in line


def test_a_pass_over_the_floor_prunes_nothing(disk, monkeypatch):
    home, _clock, _guard, _at = disk
    real = reap.subprocess.run
    monkeypatch.setattr(reap.subprocess, "run", lambda command, **kw: real(command, **kw)
                        if command[0] not in {c[0] for c in reap.PRUNE} else pytest.fail("pruned over the floor"))
    monkeypatch.setattr(reap.Reaper, "reap", lambda self: None)
    reaper = Reaper(home, lambda *_: None)
    reaper.disk()
    reaper._pass()
    assert reaper.failing is None
    assert not [f for f in read_facts(home) if f.kind == "loom.pruned"]


def test_a_reap_that_clears_the_floor_prunes_nothing(disk, monkeypatch):
    home, _clock, _guard, at = disk
    real = reap.subprocess.run
    monkeypatch.setattr(reap.subprocess, "run", lambda command, **kw: real(command, **kw)
                        if command[0] not in {c[0] for c in reap.PRUNE} else pytest.fail("pruned over the floor"))
    monkeypatch.setattr(reap.Reaper, "reap", lambda self: at(0, 13.0))   # the reap lifts the volume over
    reaper = Reaper(home, lambda *_: None)
    at(1, 11.3)
    with pytest.raises(reap.DiskFloor):
        reaper.disk()
    reaper._pass()
    assert reaper.failing is None
    assert not [f for f in read_facts(home) if f.kind == "loom.pruned"]

def test_the_disk_floor_is_the_homes_setting(tmp_path, monkeypatch):
    from types import SimpleNamespace

    root = tmp_path / "home"
    write_thread(root, "inbox", "Brnrd", "inbox")
    monkeypatch.setattr(reap.shutil, "disk_usage", lambda _p: SimpleNamespace(total=228 * GB, free=11 * GB))
    monkeypatch.setattr(reap, "important", lambda _p: None)
    assert reap.floor(root) == (11 * GB, int(228 * GB * 0.05))
    (root / "loom").mkdir(exist_ok=True)
    (root / "loom/config.toml").write_text("disk_floor_pct = 2\n")
    assert reap.floor(root) == (11 * GB, reap.FLOOR_BYTES)   # 2% of 228 GB is under 5 GiB
    (root / "loom/config.toml").write_text("disk_floor_pct = 4\n")
    assert reap.floor(root)[1] == int(228 * GB * 0.04)


def test_the_floor_reads_purgeable_space_as_free_and_the_dip_says_both(tmp_path, monkeypatch):
    # 10-29: statvfs read 14381 MB while macOS would hand an important write 20050 MB.
    from types import SimpleNamespace

    root = tmp_path / "home"
    write_thread(root, "inbox", "Brnrd", "inbox")
    reaper = Reaper(Home(root), lambda *_: None)
    monkeypatch.setattr(reap, "swap", lambda _p: "")
    reading = {"plain": 11 * GB, "important": 17 * GB}
    monkeypatch.setattr(reap.shutil, "disk_usage",
                        lambda _p: SimpleNamespace(total=228 * GB, free=reading["plain"]))
    monkeypatch.setattr(reap, "important", lambda _p: reading["important"])
    assert reap.floor(root)[0] == 17 * GB
    reaper.disk()   # 11 GB by statvfs is under the 11.4 GB floor; with purgeable it is not
    assert not reaper.below

    reading.update(plain=10 * GB, important=11 * GB)
    with pytest.raises(reap.DiskFloor) as dip:
        reaper.disk()
    assert str(dip.value) == ("11264 MB free on the home's volume (10240 MB before macOS purges),"
                              f" under the floor of {int(228 * GB * 0.05) >> 20} MB")

    monkeypatch.setattr(reap, "important", lambda _p: None)   # off macOS: statvfs alone, one number
    with pytest.raises(reap.DiskFloor) as dip:
        reaper.disk()
    assert "purges" not in str(dip.value)


@pytest.mark.skipif(sys.platform != "darwin", reason="the purgeable reading is macOS's")
def test_important_reads_this_volume_on_macos(tmp_path):
    assert reap.important(tmp_path) > 0


def test_a_dip_names_swap_and_when_swap_is_large_the_process_behind_it(tmp_path, monkeypatch):
    # 10-27: 12.4 GB of the drop was swap behind Safari's WebKit.Networking, and a body hunted rooms for it.
    from types import SimpleNamespace

    root = tmp_path / "home"
    write_thread(root, "inbox", "Brnrd", "inbox")
    reaper = Reaper(Home(root), lambda *_: None)
    swapfiles = {"mb": "22528.00"}
    monkeypatch.setattr(reap.sys, "platform", "darwin")
    monkeypatch.setattr(reap.shutil, "disk_usage", lambda _p: SimpleNamespace(total=228 * GB, free=150 * GB))
    monkeypatch.setattr(reap, "floor", lambda _p: (4 * GB, 5 * GB))
    monkeypatch.setattr(reap, "space", lambda _p: (4 * GB, 4 * GB, 228 * GB))
    ran = []

    def run(command, **_kw):
        ran.append(command[0])
        if command[0] == "sysctl":
            return SimpleNamespace(stdout=f"total = {swapfiles['mb']}M  used = 11721.75M  free = 566.25M  (encrypted)\n")
        return SimpleNamespace(stdout="Processes: 612 total\n\nPID    COMMAND          MEM\n57632  com.apple.WebKit 12G\n")

    monkeypatch.setattr(reap.subprocess, "run", run)
    with pytest.raises(reap.DiskFloor) as dip:
        reaper.disk()
    assert str(dip.value).endswith("; swap holds 22528 MB, the largest footprint is pid 57632 com.apple.WebKit at 12G")

    swapfiles["mb"], ran[:] = "2048.00", []   # a quarter of the 78 GB in use is 19.5 GB: swap is named, nobody is
    with pytest.raises(reap.DiskFloor) as dip:
        reaper.disk()
    assert str(dip.value).endswith("; swap holds 2048 MB") and ran == ["sysctl"]

    monkeypatch.setattr(reap.sys, "platform", "linux")
    assert reap.swap(root) == ""
