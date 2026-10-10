"""``loom/config.toml``: the knobs one home's looms share.

Flat ``key = value`` lines only: ``[sections]`` are ignored, so the relay
switch is ``relay_state = "<dir>"``, never
``[channels.relay]``. A few-line file, parsed here, so step 3 does not take a TOML dependency
on Python 3.10. Unknown keys are ignored. ``BRNRD_LOOM_CLOCK_SKEW_S`` is
test-only: production leaves it unset and ``loom_clock`` is ``time.time``.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TTL = 30.0
DEFAULT_SKEW = 5.0
DEFAULT_MARGIN = 1.0


@dataclass(frozen=True)
class LoomConfig:
    name: str | None = None
    router_ttl: float = DEFAULT_TTL
    max_skew: float = DEFAULT_SKEW
    margin: float = DEFAULT_MARGIN
    #: The account's cloud gate state directory (holds ``gates/cloud.json``).
    relay_state: str | None = None


def loom_clock() -> float:
    """Wall clock plus ``BRNRD_LOOM_CLOCK_SKEW_S``.

    Test-only. Two loom processes on one home export different values so a
    test can skew their lease clocks. The variable is read here and nowhere
    else; fact timestamps stay on the wall clock.
    """
    raw = os.environ.get("BRNRD_LOOM_CLOCK_SKEW_S", "")
    if not raw:
        return time.time()
    try:
        skew = float(raw)
    except ValueError:
        return time.time()
    return time.time() + skew


def _literal(raw: str) -> object:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        return raw[1:-1]
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return raw


def load_config(root: Path | str) -> LoomConfig:
    path = Path(root) / "loom" / "config.toml"
    found: dict[str, object] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            found[key.strip()] = _literal(raw.strip())
    name = found.get("name")
    if not isinstance(name, str) or not name.strip():
        name = None
    else:
        name = name.strip()

    def number(key: str, default: float) -> float:
        value = found.get(key, default)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        if value < 0:
            return default
        return value

    relay_state = found.get("relay_state")
    if not isinstance(relay_state, str) or not relay_state.strip():
        relay_state = None
    else:
        relay_state = os.path.expanduser(relay_state.strip())

    return LoomConfig(
        name=name,
        relay_state=relay_state,
        router_ttl=number("router_ttl", DEFAULT_TTL) or DEFAULT_TTL,
        max_skew=number("max_skew", DEFAULT_SKEW),
        margin=number("margin", DEFAULT_MARGIN),
    )


def granting_window(now: float, until: float, max_skew: float, margin: float) -> bool:
    """True while a router may still grant. It stops at ``until - max_skew - margin``."""
    return now < until - max_skew - margin
