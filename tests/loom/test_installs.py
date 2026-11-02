"""A second machine joins the resident with its own install id, and a loom
whose id belongs to another machine or home refuses to start."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import loom
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import Home
from brr.loom.runtime.installs import JoinError, join, refusal
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import init_self
from brr.loom.runtime.verbs_machines import _machines


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def _leading(tmp_path, monkeypatch):
    """A self on a bare remote, its first machine leasing inbox there."""
    monkeypatch.setenv("BRNRD_MACHINE_ID", "imac")
    remote = tmp_path / "self.git"
    _git("init", "--bare", "-q", str(remote))
    first = Home(tmp_path / "imac")
    init_self(first.root, person="arseni", remote=str(remote))
    (first.root / "loom/config.toml").write_text(f'lease_remote = "{remote}"\nrouter_ttl = 90\n')
    append(first, Fact(kind="note", by="loom:" + first.install_id(),
                       id="note:imac:1", data={"n": 1}))
    router = Router(first)
    router.gate.clock = Clock(100)
    router.begin_tick(read_facts(first))
    return remote, first, router


def test_join_clones_the_self_with_a_new_install_and_one_union(tmp_path, monkeypatch):
    remote, first, router = _leading(tmp_path, monkeypatch)
    monkeypatch.setenv("BRNRD_MACHINE_ID", "laptop")
    out = tmp_path / "laptop"
    assert _machines(["--home", str(out), "join", str(remote)]) == 0

    second = Home(out)
    assert second.install_id() != first.install_id()
    # The self is the remote's, never a seed of its own.
    assert _git("-C", str(out / "self"), "rev-parse", "HEAD") == _git(
        "-C", str(remote), "rev-parse", "main")
    assert load_config(out).lease_remote == str(remote)
    assert refusal(second, Router(second).gate) == ""

    # One union: the second machine reads the first's facts off the shuttle.
    peer = Router(second)
    peer.gate.clock = Clock(100)
    peer.begin_tick(read_facts(second))
    assert "note:imac:1" in {f.id for f in read_facts(second)}


def test_join_mints_again_over_an_id_the_remote_holds(tmp_path, monkeypatch):
    remote, first, _router = _leading(tmp_path, monkeypatch)
    out = tmp_path / "laptop"
    (out / "loom").mkdir(parents=True)
    (out / "loom/install-id").write_text(first.install_id() + "\n")
    join(out, str(remote))
    assert Home(out).install_id() != first.install_id()


def test_join_refuses_a_remote_nobody_leases_on(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_MACHINE_ID", "imac")
    remote = tmp_path / "self.git"
    _git("init", "--bare", "-q", str(remote))
    init_self(tmp_path / "imac", remote=str(remote))
    # Its machine routes on a local lease: a second one would lead beside it.
    with pytest.raises(JoinError, match="no loom leases"):
        join(tmp_path / "laptop", str(remote))
    assert not (tmp_path / "laptop/self").exists()


def test_a_copied_loom_folder_is_refused_here_and_on_the_remote(tmp_path, monkeypatch):
    remote, first, _router = _leading(tmp_path, monkeypatch)
    assert refusal(first, _router.gate) == ""

    # The same folder on another home of this machine: the local claim says so.
    copy = tmp_path / "copy"
    shutil.copytree(first.root / "loom", copy / "loom")
    assert "was minted at" in refusal(Home(copy))

    # A copy on another machine that also lost the claim file: the remote's
    # ledger ref was pushed from the first machine.
    monkeypatch.setenv("BRNRD_MACHINE_ID", "laptop")
    (copy / "loom/install-host").unlink()
    other = Home(copy)
    assert "pushed from 'imac " in refusal(other, Router(other).gate)


def test_a_loom_with_a_copied_install_does_not_tick(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_MACHINE_ID", "imac")
    first = Home(tmp_path / "imac")
    first.install_id()
    refusal(first)  # writes its claim, as its loom's start would
    copy = tmp_path / "copy"
    shutil.copytree(first.root / "loom", copy / "loom")
    monkeypatch.setattr(loom, "tick_once", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ticked")))
    stop = threading.Event()
    thread = threading.Thread(target=loom.run, args=(copy,), kwargs={"stop": stop})
    thread.start()
    log = copy / "loom/loom.log"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and "not starting" not in (log.read_text() if log.exists() else ""):
        time.sleep(0.05)
    stop.set()
    thread.join(10)
    assert not thread.is_alive()
    assert "not starting: install " + first.install_id() in log.read_text()
