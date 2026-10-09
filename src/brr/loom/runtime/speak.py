"""At most once to people. ``effect_once`` retries a lost receipt; people do not get that.

A channel names where a letter goes: ``channel:fake`` (a jsonl file, for
tests and demos) or ``channel:<platform>/<chat>`` through a registered
effect (step 5: the relay). A body longer than the channel's limit goes out
as parts, and each part is its own at-most-once send keyed ``<key>#<n>``:
losing part 2 is one ``maybe-sent`` row, never a resent whole.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from brr.daemon2.facts import Fact
from brr.daemon2.leases import EffectInFlight, Lease, LocalLeaseAuthority, StaleLease
from brr.gates.cloud import _RESPONSE_LIMITS as LIMITS

from .config import loom_clock
from .home import Home, channel_parts
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


def _fake_effect(home: Home, channel: str, key: str, text: str, context: dict) -> dict:
    path = home.root / "loom" / "channel-fake.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"key": key, "text": text}, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return {}


#: ``effect(home, channel, part_key, text, context) -> dict`` per channel
#: kind. ``context`` carries what the router knows (``event_id`` when the
#: letter answers a relay event). The dict is the receipt, kept on the fact.
EFFECTS: dict[str, Callable[..., dict]] = {"fake": _fake_effect}


def channel_kind(channel: str) -> str:
    """``channel:telegram/555`` → ``telegram``; ``channel:fake`` → ``fake``."""
    return channel_parts(channel)[0]


def split(text: str, limit: int | None) -> list[str]:
    """Cut at the last newline (else space) before ``limit``; never lose a byte."""
    if not limit or len(text) <= limit:
        return [text]
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip("\n") if rest[cut:cut + 1] == "\n" else rest[cut:]
    parts.append(rest)
    return parts


def speak(home: Home, router_lease: Lease, key: str, text: str, *,
          channel: str = CHANNEL, context: dict | None = None) -> tuple[str, Fact | None]:
    """Send ``key`` once. An ``intended`` with no ``sent`` is not retried.

    Returns ``(status, ledger fact)``. ``status`` is ``sent``, ``maybe-sent``,
    or ``stepped-down``. The fact is what the attention view folds.
    """
    if not key:
        raise ValueError("speak: empty key")
    kind = channel_kind(channel)
    effect = EFFECTS.get("fake" if kind == "fake" else "relay")
    if effect is None:
        raise ValueError(f"speak: no effect for {channel!r}")
    authority = LocalLeaseAuthority(
        Path(home.root) / "ledger" / "leases", clock=loom_clock,
    )
    if not authority.authorize(router_lease):
        return _STEP, None
    parts = split(text, LIMITS.get(kind))
    maybe = False
    for n, part in enumerate(parts, 1):
        part_key = key if len(parts) == 1 else f"{key}#{n}"
        safe = _store_key(part_key)
        prior = list(authority.sends.read("sends", safe))
        if any(fact.kind == "sent" for fact in prior):
            continue
        if any(fact.kind == "intended" for fact in prior):
            maybe = True
            continue

        def run(effect_key: str, gen: int, _part=part, _part_key=part_key, _n=n) -> dict:
            return effect(home, channel, _part_key, _part, dict(context or {}, first=_n == 1)) or {}

        try:
            receipt = authority.effect_once(router_lease, safe, run)
        except EffectInFlight:
            maybe = True
            continue
        except StaleLease:
            return _STEP, None
        except Exception:  # noqa: BLE001 — the effect may have reached the person
            # ``intended`` is recorded and ``sent`` is not: this part is
            # maybe-sent and is never retried. The other parts still go.
            maybe = True
            continue
        # The platform's receipt (its message id) is what a reply binds to.
        if receipt.get("message_id") not in (None, ""):
            append(home, Fact(
                kind="speech.part", by=f"loom:{home.install_id()}",
                id=f"speech.part:{part_key}",
                data={"key": key, "part": part_key, "channel": channel, "receipt": receipt},
            ))
    state = "intended" if maybe else "sent"
    fact = append(home, Fact(
        kind="speech", by=f"loom:{home.install_id()}", id=f"speech:{state}:{key}",
        data={"key": key, "state": state, "router_gen": router_lease.gen},
    ))
    return (_MAYBE if maybe else _SENT), fact
