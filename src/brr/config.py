"""Config — flat key=value reader for ``.brr/config``.

brr-specific settings live in ``.brr/config`` (gitignored), not in
AGENTS.md.  AGENTS.md is a pure markdown playbook — universal across
tools.  This module reads the flat config file only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _parse_value(val: str) -> Any:
    """Coerce a string value to bool / int / str."""
    if val in ("true", "True"):
        return True
    if val in ("false", "False"):
        return False
    try:
        return int(val)
    except ValueError:
        return val


def load_config(repo_root: Path) -> dict[str, Any]:
    """Load brr config from ``.brr/config`` in the given repo root."""
    from . import gitops

    path = gitops.shared_brr_dir(repo_root) / "config"
    if not path.exists():
        return {}
    result: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        if not _:
            continue
        result[key.strip()] = _parse_value(val.strip())
    return result


def write_config(repo_root: Path, cfg: dict[str, Any]) -> None:
    """Write config to ``.brr/config``."""
    from . import gitops

    path = gitops.shared_brr_dir(repo_root) / "config"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in cfg.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
