"""``loom/config.toml``: the knobs one home's looms share.

Flat ``key = value`` lines only: ``[sections]`` are ignored, so the relay
switch is ``relay_state = "<dir>"``, never ``[channels.relay]``. A few-line
file, parsed here, so the loom takes no TOML dependency on Python 3.10.

``KEYS`` is the one declaration: name, kind, default, help. The dataclass,
the parser, the file ``init`` seeds and ``brnrd loom config`` all derive from
it. An unknown key or a value of the wrong kind is refused at load with the
file, the key and the nearest known key; a typo never silently becomes a
default. ``BRNRD_LOOM_CLOCK_SKEW_S`` is test-only: production leaves it unset
and ``loom_clock`` is ``time.time``.
"""

from __future__ import annotations

import difflib
import os
import time
from dataclasses import dataclass, make_dataclass
from pathlib import Path


DEFAULT_TTL = 30.0
DEFAULT_SKEW = 5.0
DEFAULT_MARGIN = 1.0
#: After ``settled:`` is on the boundary, a body may still land what it
#: was holding and exit, and for a model that is one more turn. A body
#: that never comes back is signalled at this deadline. 3s killed the
#: settle test's ``send-self`` mid-land.
DEFAULT_STOP_GRACE = 60.0


class ConfigError(ValueError):
    """``loom/config.toml`` names a key the loom does not know, or misuses one."""


@dataclass(frozen=True)
class Key:
    """One knob. ``kind`` is ``text``, ``path`` (``~`` expanded), ``seconds``
    (a number >= 0), ``positive`` (a number > 0), ``percent`` or ``choice``."""

    name: str
    kind: str
    default: object
    help: str
    choices: tuple[str, ...] = ()


KEYS: tuple[Key, ...] = (
    Key("name", "text", None,
        "This loom's name. A thread whose README says `for: <name>` runs only on the loom of that name; unset matches none."),
    Key("body", "text", None,
        "The body a login service runs, as shell/core (e.g. claude/sonnet). "
        "Required by `brnrd loom install`."),
    Key("author", "text", None,
        "`Name <email>` for every commit a body makes. Unset: the loom's own."),
    Key("forge_token_file", "path", None,
        "A file holding the forge token `publish` pushes with. "
        "Unset: push with the machine's own git setup."),
    Key("gh", "choice", "auto",
        "A body's `gh`: auto uses the forge token above when the home has one, "
        "else the machine's gh login; off signs it out.",
        choices=("auto", "off")),
    Key("relay_state", "path", None,
        "The account's cloud gate state directory (holds gates/cloud.json). "
        "Set it to switch the relay on."),
    Key("hold_floor_pct", "percent", 3.0,
        "A quota window with this percent or less left counts as exhausted: a held body stays held."),
    Key("stop_grace", "seconds", DEFAULT_STOP_GRACE,
        "Seconds a settled thread's body may take to land and exit before it is signalled."),
    Key("router_ttl", "positive", DEFAULT_TTL,
        "Seconds a router lease lasts; a silent router is replaced after this."),
    Key("max_skew", "seconds", DEFAULT_SKEW,
        "Clock skew, in seconds, a router lease must tolerate; grants stop at until - max_skew - margin."),
    Key("margin", "seconds", DEFAULT_MARGIN,
        "Extra seconds, beyond max_skew, a router stops granting before its lease ends."),
)
BY_NAME = {k.name: k for k in KEYS}

#: Derived, not written out a second time: one field per ``KEYS`` row.
LoomConfig = make_dataclass(
    "LoomConfig",
    [(k.name, object, k.default) for k in KEYS],
    frozen=True,
    namespace={"__module__": __name__, "__doc__": "The effective config; fields mirror ``KEYS``."},
)


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


def config_path(root: Path | str) -> Path:
    return Path(root) / "loom" / "config.toml"


def _coerce(key: Key, value: object, path: Path) -> object:
    def refuse(want: str) -> ConfigError:
        return ConfigError(f"{path}: {key.name} must be {want}, not {value!r}")

    if key.kind in {"text", "path"}:
        if not isinstance(value, str):
            raise refuse("a quoted string")
        value = value.strip()
        if not value:
            return None
        return os.path.expanduser(value) if key.kind == "path" else value
    if key.kind == "choice":
        if value not in key.choices:
            raise refuse(" or ".join(f'"{c}"' for c in key.choices))
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise refuse("a number")
    number = float(value)
    if key.kind == "positive" and number <= 0:
        raise refuse("greater than 0")
    if number < 0:
        raise refuse("0 or more")
    return number


def load_config_report(root: Path | str) -> tuple[LoomConfig, frozenset[str]]:
    """The effective config and the keys the file set (the rest are defaults)."""
    path = config_path(root)
    found: dict[str, object] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            found[key.strip()] = _literal(raw.strip())
    values: dict[str, object] = {}
    for name, value in found.items():
        key = BY_NAME.get(name)
        if key is None:
            near = difflib.get_close_matches(name, list(BY_NAME), n=1)
            hint = f" (did you mean {near[0]}?)" if near else f" (known: {', '.join(BY_NAME)})"
            raise ConfigError(f"{path}: unknown key {name!r}{hint}")
        values[name] = _coerce(key, value, path)
    return LoomConfig(**values), frozenset(found)


def load_config(root: Path | str) -> LoomConfig:
    return load_config_report(root)[0]


def _render_value(value: object) -> str:
    if value is None:
        return '""'
    if isinstance(value, str):
        return f'"{value}"'
    return repr(value)


def render_config() -> str:
    """The file ``init`` seeds: every key commented out at its default."""
    out = [
        "# brnrd loom config: the knobs this home's looms share.",
        "# Flat `key = value` lines; [sections] are not read. Every line below is",
        "# commented out at its default: uncomment one to change it. An unknown",
        "# key, or a value of the wrong kind, is refused when the loom starts.",
        "# `brnrd loom config` prints what is in effect and where each came from.",
    ]
    for key in KEYS:
        out += ["", f"# {key.help}", f"# {key.name} = {_render_value(key.default)}"]
    return "\n".join(out) + "\n"


def seed_config(root: Path | str) -> bool:
    """Write the commented file unless one exists. True when it wrote."""
    path = config_path(root)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config(), encoding="utf-8")
    return True


def describe_config(root: Path | str) -> list[tuple[str, object, str, str]]:
    """``(key, effective value, source, help)`` for every key, in ``KEYS`` order."""
    cfg, from_file = load_config_report(root)
    return [
        (k.name, getattr(cfg, k.name), "file" if k.name in from_file else "default", k.help)
        for k in KEYS
    ]


def granting_window(now: float, until: float, max_skew: float, margin: float) -> bool:
    """True while a router may still grant. It stops at ``until - max_skew - margin``."""
    return now < until - max_skew - margin
