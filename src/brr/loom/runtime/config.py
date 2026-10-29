"""``loom/config.toml``: the knobs one home's looms share.

Flat ``key = value`` lines only: ``[sections]`` are ignored. Enable folders
with ``connectors = ["inbox=addon"]`` (name=folder; a bare folder uses its own name). A few-line
file, parsed here, so the loom takes no TOML dependency on Python 3.10.

``KEYS`` is the one declaration: name, kind, default, help. The dataclass,
the parser, the file ``init`` seeds and ``brnrd loom config`` all derive from
it. An unknown key or a value of the wrong kind is refused at load with the
file, the key and the nearest known key; a typo never silently becomes a
default. ``BRNRD_LOOM_CLOCK_SKEW_S`` is test-only: production leaves it unset
and ``loom_clock`` is ``time.time``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

import ast
import difflib
import os
import re
import subprocess
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
    """One knob. ``kind`` is ``text``, ``path`` (``~`` expanded), ``secret``
    (how to get a value: ``command:``, ``file:`` or ``env:``), ``seconds``
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
        "The body a login service runs: a shell (e.g. claude), which uses the harness's own "
        "model choice, or shell/core to pin a model (e.g. claude/sonnet). "
        "`brnrd loom install` writes one when unset."),
    Key("author", "text", None,
        "`Name <email>` for every commit a body makes. Unset: the loom's own."),
    Key("forge_token", "secret", None,
        "How to get the forge token `publish` pushes with and a body's gh uses: "
        "\"command: gh auth token\", \"file: ~/path\" or \"env: NAME\". Read at each "
        "use and at body start, never stored. Install and loom start set it: \"command: gh auth "
        "token\" when gh is signed in, the App's `forge-token` verb once the home holds a "
        "cloud gate; any other value is left alone. Unset: the machine's own git setup."),
    Key("gh", "choice", "auto",
        "A body's `gh`: auto uses the forge token above when the home has one, "
        "else the machine's gh login; off signs it out.",
        choices=("auto", "off")),
    Key("update", "choice", "auto",
        "How the loom follows new code: dev fast-forwards the checkout it runs from to origin's "
        "main once main's CI is green, then re-execs at an idle tick; stable is the release "
        "channel (not built yet); off stays put. auto: dev for a git checkout, else off.",
        choices=("auto", "dev", "stable", "off")),
    Key("connectors", "names", (),
        "Enabled folders or named channels, e.g. [\"inbox=addon\"]. Home folders override bundled folders; default: none."),
    Key("relay_state", "path", None,
        "The account's cloud gate state directory (holds gates/cloud.json). "
        'Read by the account-backed connector folder.'),
    Key("guard", "choice", "on",
        "on: letters from strangers, web reads and tainted strands taint what they touch, "
        "and a tainted strand cannot publish, push to self or open threads. off: every "
        "strand is clean and nothing is shielded.",
        choices=("on", "off")),
    Key("hold_floor_pct", "percent", 3.0,
        "A quota window with this percent or less left counts as exhausted: a held body stays held."),
    Key("disk_floor_pct", "percent", 5.0,
        "The disk floor, as a percent of the home's volume (never under 5 GiB): below it the loom "
        "is degraded, reaps rooms at once and prunes package caches."),
    Key("max_held", "positive", None,
        "Most live bodies this install keeps warm, the home thread's not counted. Past it, the "
        "body held longest with nothing to do leaves at its stop hook; a working body is never "
        "cut, and the next letter to its thread starts a fresh one. Unset: every body holds."),
    Key("thread_budget", "positive", 2_000_000,
        "Weighted tokens a thread opened by another thread may spend before its strand is told, at "
        "every boundary, to write to its parent for more. `send --budget` overrides it."),
    Key("shed_excess", "positive", 60_000,
        "Context tokens a body grows past its boot before a mostly-idle scroll makes a shed due; "
        "fast quota pace lowers it, slack raises it, at most to double."),
    Key("shed_hold", "names", ("home",),
        "Threads whose bodies are never asked to shed; \"home\" names the home thread. "
        "Set to [] to release them."),
    Key("stop_grace", "seconds", DEFAULT_STOP_GRACE,
        "Seconds a settled thread's body may take to land and exit before it is signalled."),
    Key("router_ttl", "positive", DEFAULT_TTL,
        "Seconds a router lease lasts; a silent router is replaced after this."),
    Key("max_skew", "seconds", DEFAULT_SKEW,
        "Clock skew, in seconds, a router lease must tolerate; grants stop at until - max_skew - margin."),
    Key("margin", "seconds", DEFAULT_MARGIN,
        "Extra seconds, beyond max_skew, a router stops granting before its lease ends."),
    Key("lease_remote", "text", None,
        "The self's https URL whose refs/leases/inbox decides which install routes and runs "
        "connectors, so two machines can share one resident; that lease lasts router_ttl but at "
        "least 90 s. Unset, the router lease is this machine's alone."),
    Key("lease_mode", "choice", "contested",
        "contested: a holder that cannot reach lease_remote keeps routing unless a peer's "
        "ledger moved there in the last 10 minutes; strict: it stops at the end of its ttl.",
        choices=("contested", "strict")),
    Key("board_port", "text", None,
        "Loopback port the board's parts are served on (127.0.0.1); unset, the board is off. Read at start."),
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
    if raw.startswith("["):
        try:
            return ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return raw
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

    if key.kind == "names":
        if not isinstance(value, (list, tuple)) or any(
                not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:=[A-Za-z0-9][A-Za-z0-9._-]*)?", v) for v in value):
            raise refuse('an array of "folder" or "name=folder" strings')
        names = [v.split("=", 1)[0] for v in value]
        if len(set(names)) != len(names):
            raise refuse("unique connector names")
        return tuple(value)
    if key.kind in {"text", "path"}:
        if not isinstance(value, str):
            raise refuse("a quoted string")
        value = value.strip()
        if not value:
            return None
        return os.path.expanduser(value) if key.kind == "path" else value
    if key.kind == "secret":
        if not isinstance(value, str):
            raise refuse("a quoted string")
        value = value.strip()
        if not value:
            return None
        try:
            return _secret_source(value)
        except ConfigError:
            raise refuse('"command: <shell>", "file: <path>" or "env: <NAME>"') from None
    if key.kind == "choice":
        if value not in key.choices:
            raise refuse(" or ".join(f'"{c}"' for c in key.choices))
        return value
    if value == "" and key.default is None:
        # A number with no default renders as "" in the seeded file: unset.
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise refuse("a number")
    number = float(value)
    if key.kind == "positive" and number <= 0:
        raise refuse("greater than 0")
    if number < 0:
        raise refuse("0 or more")
    return number


def _without_comment(line: str) -> str:
    """Keep hashes inside a quoted source (shell commands often need them)."""
    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quote == '"':
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char == "#":
            return line[:index]
    return line


def read_values(path: Path | str) -> dict[str, object]:
    """Read the shared flat TOML grammar, without applying a key registry."""
    path = Path(path)
    found: dict[str, object] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = _without_comment(line).strip()
            if not line or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            key = key.strip()
            if key in found:
                # The last line used to win, so a hand edit above an appended
                # line was silently ignored. Writers fold; a reader refuses.
                raise ConfigError(f"{path}: {key} is set twice; keep one line")
            found[key] = _literal(raw.strip())
    return found


def _set_key(line: str) -> str | None:
    """The key an uncommented ``key = value`` line sets, else None."""
    line = _without_comment(line).strip()
    if not line or "=" not in line:
        return None
    return line.partition("=")[0].strip()


_FOLDED = "# folded, an earlier duplicate the last line overrode: "


def fold_config(root: Path | str) -> list[str]:
    """Comment out every line a later line of the same key overrode.

    The value in effect does not change: the reader used to keep the last
    line. Returns the keys folded, so a caller can say so.
    """
    path = config_path(root)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    last: dict[str, int] = {}
    for index, line in enumerate(lines):
        key = _set_key(line)
        if key is not None:
            last[key] = index
    folded = []
    for index, line in enumerate(lines):
        key = _set_key(line)
        if key is not None and last[key] != index:
            lines[index] = _FOLDED + line
            folded.append(key)
    if folded:
        from .home import atomic_write
        atomic_write(path, "\n".join(lines) + "\n")
    return sorted(set(folded))


def set_value(root: Path | str, name: str, value: object) -> None:
    """Set one key in place, keeping every other line as written.

    The line that sets ``name`` is replaced; with none, the seeded template's
    commented ``# name = ...`` line is; with neither, the line is appended.
    Any other line setting ``name`` is folded, so the file says it once.
    """
    if name not in BY_NAME:
        raise ConfigError(f"{config_path(root)}: unknown key {name!r}")
    seed_config(root)
    path = config_path(root)
    lines = path.read_text(encoding="utf-8").splitlines()
    setting = f"{name} = {_render_value(value)}"
    live = [i for i, line in enumerate(lines) if _set_key(line) == name]
    template = [i for i, line in enumerate(lines)
                if re.match(rf"#\s*{re.escape(name)}\s*=", line.strip())]
    if live:
        lines[live[-1]] = setting
        for index in live[:-1]:
            lines[index] = _FOLDED + lines[index]
    elif template:
        lines[template[-1]] = setting
    else:
        lines.append(setting)
    from .home import atomic_write
    atomic_write(path, "\n".join(lines) + "\n")


def read_array(path: Path, key: str) -> list[str]:
    values = read_values(path).get(key, [])
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ConfigError(f"{path}: {key} must be an array of strings")
    return values


def load_config_report(root: Path | str) -> tuple[LoomConfig, frozenset[str]]:
    """The effective config and the keys the file set (the rest are defaults)."""
    path = config_path(root)
    found = read_values(path)
    values: dict[str, object] = {}
    for name, value in found.items():
        key = BY_NAME.get(name)
        if key is None:
            near = difflib.get_close_matches(name, list(BY_NAME), n=1)
            hint = f" (did you mean {near[0]}?)" if near else f" (known: {', '.join(BY_NAME)})"
            raise ConfigError(f"{path}: unknown key {name!r}{hint}")
        values[name] = _coerce(key, value, path)
    return LoomConfig(**values), frozenset(found)


_TICK_CONFIG = ContextVar("loom_tick_config", default=None)


@contextmanager
def tick_config(root, config):
    token = _TICK_CONFIG.set((Path(root).resolve(), config))
    try:
        yield
    finally:
        _TICK_CONFIG.reset(token)


def load_config(root: Path | str) -> LoomConfig:
    snapshot = _TICK_CONFIG.get()
    if snapshot is not None and snapshot[0] == Path(root).resolve():
        return snapshot[1]
    return load_config_report(root)[0]


def _render_value(value: object) -> str:
    if isinstance(value, (list, tuple)):
        import json
        return json.dumps(list(value))
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
    """Write the commented file unless one exists. True when it wrote.

    An existing file is folded instead, so a home written by an appending
    writer loads under the reader that refuses duplicates.
    """
    path = config_path(root)
    if path.exists():
        fold_config(root)
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


SECRET_SOURCES = ("command", "file", "env")
_SECRET_TIMEOUT = 30


def _secret_source(spec: str) -> str:
    """``spec`` normalised to ``<source>: <arg>``; a ``file:`` path is ``~``-expanded."""
    source, colon, arg = spec.partition(":")
    source, arg = source.strip(), arg.strip()
    if not colon or source not in SECRET_SOURCES or not arg:
        raise ConfigError(f"not a secret source: {spec!r}")
    if source == "env" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", arg):
        raise ConfigError(f"not a secret source: {spec!r}")
    if source == "file":
        arg = os.path.expanduser(arg)
    return f"{source}: {arg}"


def resolve_secret(spec: str, *, env: dict[str, str] | None = None) -> str:
    """Run a secret source now and return the value.

    Called at each use, never once at load: the bot's App token expires
    hourly, so a value read at start goes stale under a running loom. The
    value is returned, never logged; an error names the source, not the value.
    ``env``, when given, is a command source's whole environment.
    """
    source, _, arg = _secret_source(spec).partition(": ")
    if source == "command":
        try:
            proc = subprocess.run(["sh", "-c", arg], capture_output=True, text=True,
                                  timeout=_SECRET_TIMEOUT, stdin=subprocess.DEVNULL, env=env)
        except subprocess.TimeoutExpired:
            raise ConfigError(f"secret command timed out after {_SECRET_TIMEOUT}s: {arg}") from None
        except OSError:
            raise ConfigError(f"secret command could not start: {arg}") from None
        except UnicodeError:
            raise ConfigError(f"secret command returned non-UTF-8 text: {arg}") from None
        if proc.returncode != 0:
            raise ConfigError(f"secret command exited {proc.returncode}: {arg}")
        value = proc.stdout
    elif source == "file":
        try:
            value = Path(arg).read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"secret file {arg}: {exc.strerror or type(exc).__name__}") from None
        except UnicodeError:
            raise ConfigError(f"secret file {arg}: not UTF-8 text") from None
    else:
        value = os.environ.get(arg, "")
    value = value.strip()
    if not value:
        raise ConfigError(f"secret {source}: {arg} gave nothing")
    return value


def secret_shell(spec: str) -> str:
    """A shell snippet that prints the secret when run: what a ``gh`` shim
    evaluates at each call."""
    import shlex
    source, _, arg = _secret_source(spec).partition(": ")
    if source == "command":
        return arg
    if source == "file":
        return f"cat {shlex.quote(arg)}"
    return f"printenv {shlex.quote(arg)}"


def granting_window(now: float, until: float, max_skew: float, margin: float) -> bool:
    """True while a router may still grant. It stops at ``until - max_skew - margin``."""
    return now < until - max_skew - margin
