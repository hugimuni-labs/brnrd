"""Gate state: token resolution, login validation, on-disk JSON state.

State lives at ``.brr/gates/github.json``. The token resolver picks
the first available of: stored token (operator paste), ``gh auth
token`` shell-out, ``GITHUB_TOKEN`` / ``GH_TOKEN`` env.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import client
from .client import GitHubAPIError
from .paths import user as _user_path


def _state_path(brr_dir: Path) -> Path:
    return brr_dir / "gates" / "github.json"


def _load_state(brr_dir: Path) -> dict:
    path = _state_path(brr_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_state(brr_dir: Path, state: dict) -> None:
    path = _state_path(brr_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _gh_cli_token() -> str | None:
    """Read a token from ``gh auth token`` if the binary is available."""
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def _env_token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(name)
        if token:
            return token.strip()
    return None


def resolve_token(state: dict) -> str | None:
    """Return the active token, preferring stored > gh CLI > env.

    Stored tokens win because they are explicit operator intent — they
    are only saved when the operator pasted one during ``setup``. The
    gh CLI and env fallbacks are first-time setup conveniences.
    """
    stored = state.get("token")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return _gh_cli_token() or _env_token()


def _validate_token(token: str) -> str:
    """Return the authenticated user's login. Raises on failure."""
    payload = client._api_get(token, _user_path())
    if not isinstance(payload, dict) or not payload.get("login"):
        raise GitHubAPIError(0, "no login in /user response")
    return str(payload["login"])
