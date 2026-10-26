"""The account's cloud gate lives in the home, and the forge token is minted from it.

``loom/gates/`` holds what ``brnrd account connect`` wrote (``cloud.json``,
``cloud.token``, ...) and the minted GitHub App token (``github.json``).
``relay_state`` in the config overrides where that directory's parent is;
unset, the home's own ``loom/`` is used once it holds a gate. Nothing
here reads a checkout, a worktree or the daemon's old account home, so
moving or deleting either breaks neither publish nor a body's gh
(threads/forge-auth).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import ConfigError, config_path, load_config

# What ``brnrd account connect`` and the relay write; a token lives beside cloud.json.
GATE_FILES = ("cloud.json", "cloud.token", "cloud.health.json", "cloud.server.json",
              "cloud.overflow.json")
TOKEN_FILE = "github.json"
# A cached token with less than this left is minted again (App tokens last an hour).
MARGIN = 600
MINT_PATH = "/v1/daemons/publishing-credential"


def state_dir(home_root: Path | str, config) -> Path | None:
    """The directory whose ``gates/`` holds the cloud gate, or None when not connected."""
    if config.relay_state:
        return Path(config.relay_state)
    own = Path(home_root) / "loom"
    return own if (own / "gates" / "cloud.json").is_file() else None


def forge_token(state: Path, *, request=None, now=time.time) -> str:
    """A GitHub App token for the forge: cached until MARGIN before it expires, else minted."""
    cache = state / "gates" / TOKEN_FILE
    try:
        held = json.loads(cache.read_text(encoding="utf-8"))
        if held["token"] and float(held["expires"]) - now() > MARGIN:
            return str(held["token"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    from brr.gates import cloud
    from brr.gates.cloud_credentials import _atomic_write_private
    gate = cloud._load_state_from_dir(state)
    if not gate.get("brnrd_url") or not gate.get("token"):
        raise RuntimeError(f"no cloud gate in {state / 'gates'}: run brnrd account connect")
    cred = (request or cloud._request)(gate["brnrd_url"], "POST", MINT_PATH,
                                       token=gate["token"], timeout=20)
    token = str(cred["token"])
    expires = datetime.fromisoformat(str(cred["expires_at"]).replace("Z", "+00:00")).timestamp()
    _atomic_write_private(cache, json.dumps({"token": token, "expires": expires}) + "\n")
    return token


def main(argv: list[str]) -> int:
    """``brnrd runtime forge-token [--home PATH]``: print the forge token, nothing else."""
    from .home import resolve_home
    home = None
    if argv[:1] == ["--home"] and len(argv) == 2:
        home = argv[1]
    elif argv:
        sys.stderr.write("usage: brnrd runtime forge-token [--home PATH]\n")
        return 2
    try:
        root, _how = resolve_home(home)
        state = state_dir(root, load_config(root))
        if state is None:
            raise RuntimeError(f"no cloud gate in {root / 'loom' / 'gates'}: "
                               "run brnrd account connect")
        print(forge_token(state))
    except Exception as exc:
        sys.stderr.write(f"forge-token: {exc}\n")
        return 1
    return 0


# ---- migrate:gates -------------------------------------------------------

_LEGACY = re.compile(r"/loom/bin/forge-token\b")


def legacy(home_root: Path | str, config) -> bool:
    """The gate still lives outside the home, or forge_token still runs a hand-written script."""
    outside = bool(config.relay_state) and not _inside(Path(config.relay_state), Path(home_root))
    return outside or bool(config.forge_token and _LEGACY.search(config.forge_token))


def migrate(home_root: Path | str, *, verify=None) -> dict:
    """Copy the gate home, verify it answers, then point the config at it.

    Copies, never moves: the old directory stays for one release and the
    returned data says where it was. A copy the cloud refuses leaves the
    config as it was and raises.
    """
    root = Path(home_root)
    config = load_config(root)
    data: dict = {}
    if config.relay_state and not _inside(Path(config.relay_state), root):
        old = Path(config.relay_state) / "gates"
        new = root / "loom" / "gates"
        new.mkdir(parents=True, exist_ok=True)
        copied = []
        for name in GATE_FILES:
            if (old / name).is_file() and not (new / name).exists():
                shutil.copy2(old / name, new / name)
                copied.append(name)
        (verify or _verify)(root / "loom")
        data.update(gates_from=str(old), gates_to=str(new), copied=copied)
    edits: dict[str, str | None] = {}
    if config.relay_state and not _inside(Path(config.relay_state), root):
        edits["relay_state"] = None
    if config.forge_token and _LEGACY.search(config.forge_token):
        from . import entry
        edits["forge_token"] = "command: " + entry.command("forge-token", "--home", str(root))
        data["forge_token_was"] = config.forge_token
    if edits:
        _rewrite(root, edits)
    return data


def _verify(state: Path) -> None:
    from brr.gates import cloud
    gate = cloud._load_state_from_dir(state)
    if not gate.get("brnrd_url") or not gate.get("token"):
        raise RuntimeError(f"copied gate in {state / 'gates'} has no URL or token")
    cloud._request(gate["brnrd_url"], "GET", "/v1/daemons/loom", token=gate["token"], timeout=20)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _rewrite(root: Path, edits: dict[str, str | None]) -> None:
    """Set or comment out each key in place; the loader must accept the result."""
    path = config_path(root)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    stamp = time.strftime("%Y-%m-%d")
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in edits:
            value = edits[key]
            out.append(f"# {line.rstrip()}  # moved by migrate:gates {stamp}\n")
            if value is not None:
                out.append(f"{key} = {json.dumps(value)}\n")
            continue
        out.append(line)
    text = "".join(out)
    draft = path.with_name(path.name + ".gates")
    draft.write_text(text, encoding="utf-8")
    try:
        _check(draft)
    except ConfigError:
        draft.unlink(missing_ok=True)
        raise
    draft.replace(path)


def _check(draft: Path) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        target = config_path(Path(tmp))
        target.parent.mkdir(parents=True)
        shutil.copy(draft, target)
        load_config(Path(tmp))
