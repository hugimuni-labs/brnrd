"""The served page's build identity, shared by both HTTP doors."""

import json
from pathlib import Path


def frontend_tree(directory: Path | None) -> str | None:
    if directory is None:
        return None
    try:
        value = json.loads((directory / "_app" / "version.json").read_text()).get("version")
        return value if isinstance(value, str) else None
    except (OSError, ValueError, AttributeError):
        return None
