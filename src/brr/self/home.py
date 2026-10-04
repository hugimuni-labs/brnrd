"""Seed a home once and add Claude Code wiring without replacing user settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

TEMPLATES = Path(__file__).with_name("templates")
DIRECTORIES = ("kb", "obligations", "body", "bench", "journal/checkpoints")
CONFIG = ".self.json"


def write_text(path: Path, text: str) -> None:
    """Replace a generated file atomically, including on concurrent hook calls."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def resolve_home(home: str | Path | None = None, project: Path | None = None) -> Path:
    project = (project or Path.cwd()).resolve()
    configured = read_json(project / ".claude" / CONFIG).get("home")
    return Path(home or os.environ.get("BRNRD_SELF_HOME") or configured or
                "~/.local/share/brnrd/self").expanduser().resolve()


def require_home(home: Path) -> None:
    if not (home / "identity.md").is_file():
        raise ValueError(f"No self at {home}; run self init --home PATH first")


def init_home(home: Path, project: Path) -> list[str]:
    """Copy seeds once, then merge hooks and MCP registration.

    Read and validate existing integration files before writing anything.
    A different home on the same project is an explicit conflict, not a
    reason to silently leave two sets of capture hooks active.
    """
    home, project = home.resolve(), project.resolve()
    claude_dir = project / ".claude"
    settings = read_json(claude_dir / "settings.json")
    mcp = read_json(project / ".mcp.json")
    configured = read_json(claude_dir / CONFIG)
    if configured.get("home") and Path(configured["home"]).resolve() != home:
        raise ValueError("Project already uses another self home; remove .claude/.self.json "
                         "and its hooks/import/MCP entry before rewiring")
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings.json hooks must be an object")
    prefix = [sys.executable, "-m", "brr.self"]
    for event, verb in (("SessionStart", "wake"), ("PostToolUse", "encode"),
                        ("PreCompact", "checkpoint"), ("Stop", "checkpoint")):
        command = shlex.join(prefix + [verb, "--home", str(home), "--hook"])
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"settings.json hooks.{event} must be an array")
        if not any(h.get("command") == command
                   for g in groups if isinstance(g, dict)
                   for h in g.get("hooks", []) if isinstance(h, dict)):
            groups.append({"matcher": "", "hooks": [
                {"type": "command", "command": command, "timeout": 10}]})
    servers = mcp.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(".mcp.json mcpServers must be an object")
    server = {"type": "stdio", "command": sys.executable,
              "args": ["-m", "brr.self", "mcp", "--home", str(home)]}
    if "self" in servers and servers["self"] != server:
        raise ValueError(".mcp.json already has a different server named self")
    servers["self"] = server
    created = []
    for directory in DIRECTORIES:
        (home / directory).mkdir(parents=True, exist_ok=True)
    for seed in sorted(TEMPLATES.rglob("*")):
        if not seed.is_file():
            continue
        path = home / seed.relative_to(TEMPLATES)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves edited seeds, including empty files.
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(seed.read_text(encoding="utf-8"))
            created.append(str(path.relative_to(home)))
        except FileExistsError:
            pass
    write_text(claude_dir / "settings.json", json.dumps(settings, indent=2) + "\n")
    write_text(project / ".mcp.json", json.dumps(mcp, indent=2) + "\n")
    write_text(claude_dir / CONFIG, json.dumps({"home": str(home)}, indent=2) + "\n")
    memory = project / "CLAUDE.md"
    text = memory.read_text(encoding="utf-8") if memory.exists() else ""
    # Remove only the exact pair installed by earlier versions. SessionStart
    # supplies the fresh wake; an import would load a second, stale copy.
    line = '@' + str(home / 'wake.md').replace(' ', r'\ ')
    marker = "<!-- brnrd self: generated wake, authored home -->"
    lines = text.splitlines(keepends=True)
    cleaned = []
    i = 0
    while i < len(lines):
        if (lines[i].rstrip("\r\n") == marker and i + 1 < len(lines)
                and lines[i + 1].rstrip("\r\n") == line):
            i += 2
        else:
            cleaned.append(lines[i])
            i += 1
    if "".join(cleaned) != text:
        write_text(memory, "".join(cleaned))
    return created
