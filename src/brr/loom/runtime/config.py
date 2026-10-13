"""``loom/config.toml``: the knobs one home's looms share.

Flat ``key = value`` lines only: ``[sections]`` are ignored, so the relay
switch is ``relay_state = "<dir>"``, never
``[channels.relay]``. A few-line file, parsed here, so step 3 does not take a TOML dependency
on Python 3.10. Unknown keys are ignored. ``stop_grace`` is how many seconds
a settled thread's body may take to leave before the loop ends it.
``BRNRD_LOOM_CLOCK_SKEW_S`` is
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
#: After ``settled:`` is on the boundary, a body may still land what it
#: was holding (the settle test's ``send-self``) and exit. A body that
#: never comes back is signalled at this deadline. 3s killed that land.
DEFAULT_STOP_GRACE = 12.0


@dataclass(frozen=True)
class LoomConfig:
    body: str | None = None
    hold_floor_pct: float = 3.0
    name: str | None = None
    router_ttl: float = DEFAULT_TTL
    max_skew: float = DEFAULT_SKEW
    margin: float = DEFAULT_MARGIN
    #: Seconds after a thread is settled before the loop signals its body.
    stop_grace: float = DEFAULT_STOP_GRACE
    #: The account's cloud gate state directory (holds ``gates/cloud.json``).
    relay_state: str | None = None
    #: ``Name <email>`` for every commit a body makes. Unset uses the loom's.
    author: str | None = None
    #: A file holding the forge token ``publish`` pushes with. Unset pushes
    #: with the machine's own git setup.
    forge_token_file: str | None = None


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
    author = found.get("author")
    if not isinstance(author, str) or not author.strip():
        author = None
    else:
        author = author.strip()
    forge_token_file = found.get("forge_token_file")
    if not isinstance(forge_token_file, str) or not forge_token_file.strip():
        forge_token_file = None
    else:
        forge_token_file = os.path.expanduser(forge_token_file.strip())

    return LoomConfig(
        body=found.get("body") or None,
        hold_floor_pct=number("hold_floor_pct", 3.0),
        name=name,
        relay_state=relay_state,
        author=author,
        forge_token_file=forge_token_file,
        router_ttl=number("router_ttl", DEFAULT_TTL) or DEFAULT_TTL,
        max_skew=number("max_skew", DEFAULT_SKEW),
        margin=number("margin", DEFAULT_MARGIN),
        stop_grace=number("stop_grace", DEFAULT_STOP_GRACE),
    )


def granting_window(now: float, until: float, max_skew: float, margin: float) -> bool:
    """True while a router may still grant. It stops at ``until - max_skew - margin``."""
    return now < until - max_skew - margin
