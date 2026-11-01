"""A shell is a folder; a home's folder wins over the bundled one."""

import os
from pathlib import Path
import re


def resolve(home, shell: str, group: str = "bodies") -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", shell):
        raise ValueError(f"invalid {group} name {shell!r}")
    local = home.root / "loom" / group / shell
    override = local.is_dir() if group == "bodies" else (local / "carries").is_file()
    path = local if override else Path(__file__).parent / group / shell
    if not path.is_dir() or (group == "bodies" and not (path / "run").is_file()):
        raise ValueError(f"no {group} folder for {shell!r}")
    return path.resolve()


def parse_body(choice) -> tuple[str, str]:
    """``(shell, core)`` from ``shell/core`` or a bare ``shell``.

    A bare shell has the empty core: the harness's own model choice.
    """
    shell, slash, core = str(choice).strip().partition("/")
    shell, core = shell.strip(), core.strip()
    if not shell or (slash and not core):
        raise ValueError(f"body must be shell or shell/core: {choice!r}")
    return shell, core


def body_label(shell: str, core: str) -> str:
    """``shell/core``, or the bare shell when the harness picks the model."""
    return f"{shell}/{core}" if core else shell


def codex_model() -> str | None:
    """The person's own codex default: top-level ``model`` in codex's config."""
    path = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "config.toml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            return None
        match = re.fullmatch(r'model\s*=\s*"([^"]+)"\s*(#.*)?', line)
        if match:
            return match.group(1)
    return None


_SEAT_KEYS = {"body", "repo", "watch"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class RepoSeatError(ValueError):
    """A seat's ``repo:`` lines cannot be used."""


# (self repo, thread) -> (main tip, seat): the router asks every tick, and a
# git process per thread per tick was a fifth of the live loop (loom-cpu).
_SEATS: dict = {}


def seat_text(home, thread: str) -> str:
    """The seat as it landed on main, or the file when the self is not git yet."""
    from .selfrepo import git
    if (home.root / "self/.git").exists():
        from .lifecycle import main_tip
        from .selfrepo import SelfError
        repo = home.root / "self"
        try:
            tip = main_tip(repo)
        except SelfError:
            return ""
        cached = _SEATS.get((repo, thread))
        if cached and cached[0] == tip:
            return cached[1]
        proc = git(repo, "show", f"{tip}:threads/{thread}/seat", check=False)
        text = proc.stdout if proc.returncode == 0 else ""
        _SEATS[(repo, thread)] = (tip, text)
        return text
    path = home.thread_dir(thread) / "seat"
    return path.read_text() if path.exists() else ""


def _entries(text: str):
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, colon, value = line.partition(":")
        key = key.strip()
        if not colon or key not in _SEAT_KEYS:
            raise ValueError(f"invalid seat line: {line!r}")
        yield key, value.strip().strip('"')


def repo_dirname(source: str) -> str:
    """Last path segment of a repo source, without a trailing ``.git``."""
    text = source.strip().rstrip("/")
    if not text:
        raise RepoSeatError("repo source is empty")
    if text.startswith("-"):
        raise RepoSeatError(f"repo source {text!r} begins with '-'")
    if ":" not in text and not text.startswith("/"):
        # git resolves a relative path against whichever directory it runs
        # in, and the mirror, the room and the checkout are three of them.
        raise RepoSeatError(f"repo source {text!r} is neither a URL nor a path from /")
    name = text.rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if name == "self" or not _NAME.fullmatch(name):
        raise RepoSeatError(f"repo name {name!r} from {source!r} is not a directory name")
    return name


def repos(text: str, home=None) -> list[tuple[str, str]]:
    """``repo:`` lines of a seat: ``(directory name, source)`` in seat order.

    A bare name is a library entry with a source (``library/<name>.md``),
    resolved through ``home``; with no home it is checked as a name only.
    Two lines whose sources name the same directory are refused. ``body:`` and
    ``watch:`` are ignored here.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    shelf = None
    for key, value in _entries(text):
        if key != "repo":
            continue
        if _NAME.fullmatch(value) and value != "self":
            name = value
            if home is not None:
                if shelf is None:
                    from .library import sources
                    shelf = sources(home)
                if name not in shelf:
                    raise RepoSeatError(f"repo {name!r}: no library/{name}.md with a source on main")
                value = shelf[name]
        else:
            name = repo_dirname(value)
        if name in seen:
            raise RepoSeatError(f"repo name {name!r} is declared twice")
        seen.add(name)
        found.append((name, value))
    return found


_ROLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _preferences(home):
    """``loom/preferences.toml`` read and checked: ``model.<role>`` keys, and ``bodies.order``."""
    from .config import ConfigError, read_values
    path = home.root / "loom" / "preferences.toml"
    found = read_values(path)
    for key in found:
        if key == "bodies.order":
            value = found[key]
            if not (isinstance(value, list) and all(isinstance(v, str) and _ROLE.fullmatch(v) for v in value)):
                raise ConfigError(f"{path}: bodies.order is a list of shells, e.g. [\"claude\", \"codex\"]")
            continue
        if not key.startswith("model.") or not _ROLE.fullmatch(key[6:]):
            raise ConfigError(f"{path}: unknown key {key!r} "
                              "(known: model.default, model.inbox, model.<thread>, bodies.order)")
    return path, found


def default_order() -> list[str]:
    """The harnesses setup offers, best first: ``bodies/order``, the one file that ranks them."""
    text = (Path(__file__).parent / "bodies" / "order").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def body_order(home) -> list[str]:
    """The person's harnesses, best first, the ones switched off left out: ``bodies.order``, else ``bodies/order``."""
    chosen = _preferences(home)[1].get("bodies.order")
    return list(chosen) if isinstance(chosen, list) else default_order()


def set_order(home, shells: list[str]) -> None:
    """Write ``bodies.order``; every other line of ``loom/preferences.toml`` stays as written."""
    from .config import _render_value, _set_key
    from .home import atomic_write
    if not shells or not all(_ROLE.fullmatch(s) for s in shells):
        raise ValueError(f"bodies.order needs at least one shell: {shells!r}")
    path, _found = _preferences(home)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    lines = [line for line in lines if _set_key(line) != "bodies.order"]
    lines.append(f"bodies.order = {_render_value(list(shells))}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "\n".join(lines) + "\n")


def fall_down(home, choice: tuple[str, str], usable) -> tuple[str, str]:
    """``choice``, or the next shell on the person's list when ``choice``'s cannot run now.

    Only a ranked shell falls (a seat's ``fake`` or a home's own folder never
    does), and only to one ``usable(shell)`` says can run: installed, with fuel.
    The fallback runs with the harness's own model.
    """
    order = body_order(home)
    if choice[0] not in default_order() or usable(choice[0]):
        return choice
    for shell in order:
        if shell != choice[0] and usable(shell):
            return shell, ""
    return choice


def preferred_body(home, thread: str) -> str:
    """The person's body for ``thread`` from ``loom/preferences.toml``, or "".

    ``model.<thread>`` first, then ``model.inbox`` for the home thread
    whatever its folder is called, then ``model.default``. A seat's ``body:``
    still wins over all of them; ``config.toml``'s ``body`` is the fallback.
    """
    _path, found = _preferences(home)
    roles = [thread]
    if thread == home.main_thread():
        roles.append("inbox")
    for role in [*roles, "default"]:
        if found.get(f"model.{role}"):
            return str(found[f"model.{role}"])
    return ""


def default_body(home) -> str:
    """The home's default body: ``model.default``, else ``config.toml``'s ``body``, else ""."""
    from .config import load_config
    choice = _preferences(home)[1].get("model.default") or load_config(home.root).body
    return str(choice).strip() if choice else ""


def prefer(home, role: str, body: str | None) -> None:
    """Set ``model.<role>`` to ``body`` in ``loom/preferences.toml``; None or "" clears it.

    ``role`` is ``default``, ``inbox`` or a thread id; ``body`` is checked as
    ``shell`` or ``shell/core``. Every other line stays as written.
    """
    from .config import _render_value, _set_key
    from .home import atomic_write
    if not _ROLE.fullmatch(role):
        raise ValueError(f"preference role must be default, inbox or a thread id: {role!r}")
    if body:
        body = body_label(*parse_body(body))
    path, _found = _preferences(home)
    key = f"model.{role}"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    live = [i for i, line in enumerate(lines) if _set_key(line) == key]
    if body and live:
        lines[live[0]] = f"{key} = {_render_value(body)}"
        live = live[1:]
    elif body:
        lines.append(f"{key} = {_render_value(body)}")
    lines = [line for i, line in enumerate(lines) if i not in live]
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "\n".join(lines) + "\n" if lines else "")


def at_birth(home, thread: str, default: tuple[str, str],
             text: str | None = None) -> tuple[str, str]:
    from .config import load_config
    if text is None:
        text = seat_text(home, thread)
    choice = None
    for key, value in _entries(text):
        if key == "body":
            choice = value
    choice = choice or preferred_body(home, thread) or load_config(home.root).body
    if not choice:
        return default
    return parse_body(choice)
