"""Parse AGENTS.md frontmatter and manage project configuration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML frontmatter from AGENTS.md into a dict.

    Handles the restricted subset we use: one top-level key ('brr'),
    simple scalars, one-level nested dicts, and inline lists.
    No dependency on pyyaml.
    """
    m = re.match(r"^---\n(.*?\n)---", text, re.DOTALL)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    return _parse_block(lines, 0)[0]


def _parse_block(lines: list[str], base_indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(line) - len(stripped)
        if indent < base_indent:
            break
        if ":" not in stripped:
            i += 1
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if val:
            result[key] = _parse_value(val)
            i += 1
        else:
            # Check for nested block
            child_indent = indent + 2
            if i + 1 < len(lines):
                next_stripped = lines[i + 1].lstrip()
                next_indent = len(lines[i + 1]) - len(next_stripped)
                if next_indent >= child_indent and next_stripped:
                    child, consumed = _parse_block(lines[i + 1:], child_indent)
                    result[key] = child
                    i += 1 + consumed
                    continue
            result[key] = ""
            i += 1
            continue
    return result, i


def _parse_value(val: str) -> Any:
    if val == "[]":
        return []
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1]
        return [_parse_scalar(s.strip()) for s in inner.split(",") if s.strip()]
    return _parse_scalar(val)


def _parse_scalar(val: str) -> Any:
    if val in ("true", "True"):
        return True
    if val in ("false", "False"):
        return False
    if val in ("null", "None", "~"):
        return None
    # Strip quotes
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    try:
        return int(val)
    except ValueError:
        return val


def load_config(repo_root: Path) -> dict[str, Any]:
    """Load brr config from AGENTS.md in the given repo root."""
    agents_file = repo_root / "AGENTS.md"
    if not agents_file.exists():
        return {}
    text = agents_file.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    return fm.get("brr", fm)


def state_file_path(repo_root: Path, config: dict[str, Any] | None = None) -> Path:
    """Return the resolved path to the state file."""
    if config is None:
        config = load_config(repo_root)
    rel = config.get("state_file", "agent_state.md")
    return repo_root / rel
