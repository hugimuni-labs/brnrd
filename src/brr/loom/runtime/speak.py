"""At most once to people. ``effect_once`` retries a lost receipt; people do not get that."""

from __future__ import annotations

import json
import os
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.daemon2.leases import EffectInFlight, Lease, LocalLeaseAuthority, StaleLease

from .config import loom_clock
from .home import Home
from .ledger import append


CHANNEL = "channel:fake"
_SENT = "sent"
_MAYBE = "maybe-sent"
_STEP = "stepped-down"


def _store_key(key: str) -> str:
    """``effect_once`` builds a lease named ``send:<key>`` and rejects ``/``.

    Letter ids are ``<sender>/<stem>``. The send store uses the id with
    ``/`` turned into ``_``. The channel file and the attention row keep
    the real id. daemon2 is not edited; this is the wrap.
    """
    safe = key.replace("/", "_")
    if not safe or "/" in safe:
        raise ValueError(f"no send key for {key!r}")
    return safe


def _channel(home: Home) -> Path:
    return home.root / "loom" / "channel-fake.jsonl"


def _already(path: Path, key: str) -> bool:
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("key") == key:
            return True
    return False


def _append_line(home: Home, key: str, text: str, gen: int) -> None:
    path = _channel(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _already(path, key):
        return
    line = json.dumps(
        {"key": key, "text": text, "gen": gen}, sort_keys=True, separators=(",", ":"),
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _speech(home: Home, key: str, state: str, gen: int) -> Fact:
    return append(home, Fact(
        kind="speech", by=f"loom:{home.install_id()}", id=f"speech:{state}:{key}",
        data={"key": key, "state": state, "router_gen": gen},
    ))


def speak(home: Home, router_lease: Lease, key: str, text: str) -> tuple[str, Fact | None]:
    """Send ``key`` once. An ``intended`` with no ``sent`` is not retried.

    Returns ``(status, ledger fact)``. ``status`` is ``sent``, ``maybe-sent``,
    or ``stepped-down``. The fact is what the attention view folds.
    """
    if not key:
        raise ValueError("speak: empty key")
    authority = LocalLeaseAuthority(
        Path(home.root) / "ledger" / "leases", clock=loom_clock,
    )
    if not authority.authorize(router_lease):
        return _STEP, None
    safe = _store_key(key)
    prior = list(authority.sends.read("sends", safe))
    if any(fact.kind == "sent" for fact in prior):
        return _SENT, _speech(home, key, "sent", router_lease.gen)
    if any(fact.kind == "intended" for fact in prior):
        return _MAYBE, _speech(home, key, "intended", router_lease.gen)

    def effect(effect_key: str, gen: int) -> dict:
        del effect_key
        _append_line(home, key, text, gen)
        return {"key": key, "gen": gen}

    try:
        authority.effect_once(router_lease, safe, effect)
    except EffectInFlight:
        return _MAYBE, _speech(home, key, "intended", router_lease.gen)
    except StaleLease:
        return _STEP, None
    return _SENT, _speech(home, key, "sent", router_lease.gen)
