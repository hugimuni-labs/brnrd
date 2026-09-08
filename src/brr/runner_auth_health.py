"""Persist dispatch-proven runner authentication failures.

The daemon writes ``.brr/runner-auth-health.json`` after attempts and the
runner catalog reads it. A manual relogin is deliberately not observable on
its own; the mark clears only on the next dispatch-proven auth in the same
domain — either a clean attempt exit (``daemon._record_runner_auth_health``,
post-attempt) or, sooner, this attempt's first *live* tool boundary
(``daemon._run_worker``'s ``_emit_flush``, which calls
:func:`clear_success` the moment a boundary flush proves the Shell
authenticated). The boundary clear exists because a long-lived seat
(``await`` / ``hold:``) can sit *inside* an attempt for hours after a
relogin — the exit-only clear left the catalog and dashboard reading the
whole domain dead for that entire span even while the run was working.
Both marking and clearing replace the file atomically, so daemon restarts
preserve the latest verdict rather than resurrecting a cleared failure.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import gitops


FILENAME = "runner-auth-health.json"
_lock = threading.Lock()


def failure_domain(profile: object) -> str | None:
    """Match runner selection's auth-sharing boundary."""
    from .runner_select import _failure_domain

    return _failure_domain(profile)


def _path(repo_root: Path) -> Path:
    return gitops.shared_brr_dir(repo_root) / FILENAME


def _read(repo_root: Path) -> dict[str, object]:
    try:
        value = json.loads(_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "auth_error_domains": {}}
    if not isinstance(value, dict) or not isinstance(value.get("auth_error_domains"), dict):
        return {"version": 1, "auth_error_domains": {}}
    return value


def _write(repo_root: Path, value: dict[str, object]) -> None:
    path = _path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{FILENAME}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def is_auth_failed(repo_root: Path, profile: object) -> bool:
    domain = failure_domain(profile)
    with _lock:
        marks = _read(repo_root)["auth_error_domains"]
    return bool(domain and domain in marks)


def record_auth_error(repo_root: Path, profile: object) -> None:
    domain = failure_domain(profile)
    if not domain:
        return
    with _lock:
        state = _read(repo_root)
        marks = state["auth_error_domains"]
        marks[domain] = {
            "profile": str(getattr(profile, "name", "") or ""),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        _write(repo_root, state)


def clear_success(repo_root: Path, profile: object) -> None:
    domain = failure_domain(profile)
    if not domain:
        return
    with _lock:
        state = _read(repo_root)
        marks = state["auth_error_domains"]
        if marks.pop(domain, None) is not None:
            _write(repo_root, state)
