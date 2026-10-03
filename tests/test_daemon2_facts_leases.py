"""Real files and processes at the new daemon's durability boundary."""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact, FactStore, fold_letter, legacy_letter, union
from brr.daemon2.leases import LocalLeaseAuthority, StaleLease


def _compete(root: str, output: multiprocessing.Queue) -> None:
    authority = LocalLeaseAuthority(Path(root))
    lease = authority.acquire("letter:x", str(multiprocessing.current_process().pid), 10)
    output.put(lease is not None)


def test_fact_merge_is_idempotent_and_catches_conflicting_identity(tmp_path: Path) -> None:
    store = FactStore(tmp_path)
    first = Fact("pending", "door")
    second = Fact("claimed", "run", {"run": "r1", "gen": 1, "until": 100})
    store.append("letters", "x", first)
    store.merge("letters", "x", [second, first])
    assert [f.id for f in store.read("letters", "x")] == [f.id for f in union([first, second])]
    assert len(store.read("letters", "x")) == 2
    with pytest.raises(ValueError, match="conflicting"):
        store.append("letters", "x", Fact("retired", "other", id=first.id, at=first.at))


def test_letter_expired_claim_reopens_without_erasing_history(tmp_path: Path) -> None:
    store = FactStore(tmp_path)
    first = Fact("pending", "door", at="2026-01-01T00:00:00+00:00")
    claim = Fact("claimed", "run", {"gen": 1, "until": 20},
                 at="2026-01-01T00:00:01+00:00")
    store.merge("letters", "x", [claim, first])
    assert fold_letter(store.read("letters", "x"), now=19).state == "claimed"
    assert fold_letter(store.read("letters", "x"), now=21).state == "pending"
    assert legacy_letter("processing").state == "claimed"
    assert legacy_letter("delivered").state == "answered"


def test_lease_expiry_fences_old_generation_and_preserves_effect_receipt(tmp_path: Path) -> None:
    now = [100.0]
    authority = LocalLeaseAuthority(tmp_path, clock=lambda: now[0])
    first = authority.acquire("send:x", "a", 10)
    assert first is not None
    assert authority.acquire("send:x", "b", 10) is None
    calls: list[str] = []

    def send(key: str, gen: int) -> dict:
        calls.append(key)
        return {"id": "receipt", "gen": gen}

    assert authority.effect_once(first, "reply:1", send)["id"] == "receipt"
    assert authority.effect_once(first, "reply:1", send)["id"] == "receipt"
    assert calls == ["reply:1"]
    now[0] = 111
    second = authority.acquire("send:x", "b", 10)
    assert second is not None and second.gen == first.gen + 1
    assert authority.renew(first, 10) is None
    with pytest.raises(StaleLease):
        authority.effect_once(first, "reply:2", send)
    assert authority.effect_once(second, "reply:1", send)["id"] == "receipt"
    assert calls == ["reply:1"]


def test_two_processes_cannot_hold_same_lease(tmp_path: Path) -> None:
    output: multiprocessing.Queue = multiprocessing.Queue()
    children = [multiprocessing.Process(target=_compete, args=(str(tmp_path), output))
                for _ in range(2)]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=5)
        assert child.exitcode == 0
    assert sorted(output.get(timeout=1) for _ in children) == [False, True]


def test_capability_filter_and_release(tmp_path: Path) -> None:
    authority = LocalLeaseAuthority(tmp_path)
    assert authority.acquire("self", "box-a", 10, required={"gpu"}) is None
    lease = authority.acquire("self", "box-a", 10,
                              capabilities={"gpu", "telegram"}, required={"gpu"})
    assert lease is not None and authority.authorize(lease)
    assert authority.release(lease)
    assert not authority.authorize(lease)
    next_lease = authority.acquire("self", "box-b", 10)
    assert next_lease is not None and next_lease.gen == lease.gen + 1
