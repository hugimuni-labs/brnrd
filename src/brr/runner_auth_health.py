"""Persist dispatch-proven runner authentication failures — and notice the relogin.

The daemon writes ``.brr/runner-auth-health.json`` after attempts and the
runner catalog reads it. A mark is a claim about *one credential*: the one
the Shell presented when the attempt failed. So every mark records that
credential's **fingerprint** (claude: the Keychain item's modification
stamp, read without the secret; codex: ``auth.json``'s mtime), and a mark
whose credential has since changed is stale by construction — the reader
drops it. That is how a relogin becomes observable without a dispatch,
which is the defect the first cut of this module designed in ("a manual
relogin is deliberately not observable"): the catalog hid every core of
the domain, the panel hid the shell, the tap that could have dispatched
the clearing run was gone with it, and the operator deleted this file by
hand three times in one day (2026-09-08/09).

Clearing, in order of arrival:

* the credential fingerprint changed (``_stale``) — read-time, no process;
* the daemon's periodic :func:`sweep` while a mark exists: fingerprint
  first, then the Shell's own status verb (``claude auth status`` /
  ``codex login status``) where a fingerprint cannot be read;
* this attempt's first *live* tool boundary (``daemon._run_worker``'s
  ``_emit_flush`` → :func:`clear_success`) — the run itself is the proof;
* a clean attempt exit (``daemon._record_runner_auth_health``).

Both marking and clearing replace the file atomically, so daemon restarts
preserve the latest verdict rather than resurrecting a cleared failure.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import gitops


FILENAME = "runner-auth-health.json"
_lock = threading.Lock()

_KEYCHAIN_SERVICE = "Claude Code-credentials"
_MDAT_RE = re.compile(r'"mdat"<timedate>=0x[0-9A-Fa-f]+\s+"([^"]+)"')


def failure_domain(profile: object) -> str | None:
    """Match runner selection's auth-sharing boundary."""
    from .runner_select import _failure_domain

    return _failure_domain(profile)


def _shell_of(profile: object) -> str:
    return str(getattr(profile, "shell", "") or getattr(profile, "profile", "") or "").strip().lower()


def credential_fingerprint(shell: str) -> str | None:
    """A stamp that changes when the Shell's stored credential changes.

    Never the secret itself: Keychain *metadata* for claude on macOS, the
    credential file's mtime elsewhere. ``None`` when nothing readable is
    found — the mark then falls back to dispatch-proven clearing.
    """
    shell = (shell or "").strip().lower()
    if shell == "claude":
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if proc.returncode == 0:
                match = _MDAT_RE.search(proc.stdout)
                if match:
                    stamp = match.group(1).replace("\\000", "").rstrip(chr(0))
                    return f"keychain:{stamp}"
        except (OSError, subprocess.SubprocessError):
            pass
        base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
        path = base / ".credentials.json"
    elif shell == "codex":
        base = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        path = base / "auth.json"
    else:
        return None
    try:
        return f"file:{path.stat().st_mtime_ns}"
    except OSError:
        return None


def probe_logged_in(shell: str) -> bool | None:
    """Ask the Shell whether it believes it is signed in. ``None`` = unknown.

    Secondary evidence: a Shell can report ``loggedIn`` on a token it can no
    longer refresh, so :func:`sweep` trusts this only where no fingerprint
    exists. Its *negative* is still worth recording on the mark.
    """
    shell = (shell or "").strip().lower()
    try:
        if shell == "claude":
            proc = subprocess.run(
                ["claude", "auth", "status"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            try:
                payload = json.loads(proc.stdout or "{}")
            except json.JSONDecodeError:
                return None
            value = payload.get("loggedIn") if isinstance(payload, dict) else None
            return bool(value) if isinstance(value, bool) else None
        if shell == "codex":
            proc = subprocess.run(
                ["codex", "login", "status"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            text = (proc.stdout + proc.stderr).lower()
            if "logged in" in text and "not logged in" not in text:
                return True
            if "not logged in" in text or proc.returncode != 0:
                return False
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    return None


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


def _stale(mark: object) -> bool:
    """The credential this mark was recorded against is no longer the one on disk."""
    if not isinstance(mark, dict):
        return True
    recorded = mark.get("credential_fingerprint")
    if not recorded:
        return False
    current = credential_fingerprint(str(mark.get("shell") or ""))
    return bool(current) and current != recorded


def _drop_stale(repo_root: Path) -> dict[str, object]:
    """Read the file and drop every mark whose credential changed. Caller holds ``_lock``."""
    state = _read(repo_root)
    marks = state["auth_error_domains"]
    assert isinstance(marks, dict)
    stale = [domain for domain, mark in marks.items() if _stale(mark)]
    for domain in stale:
        marks.pop(domain, None)
    if stale:
        _write(repo_root, state)
    return state


def auth_mark(repo_root: Path, profile: object) -> dict[str, object] | None:
    """The live mark for this profile's domain, or ``None`` — stale marks are dropped on read."""
    domain = failure_domain(profile)
    if not domain:
        return None
    with _lock:
        marks = _drop_stale(repo_root)["auth_error_domains"]
        assert isinstance(marks, dict)
        mark = marks.get(domain)
    return dict(mark) if isinstance(mark, dict) else None


def is_auth_failed(repo_root: Path, profile: object) -> bool:
    return auth_mark(repo_root, profile) is not None


def record_auth_error(repo_root: Path, profile: object) -> None:
    domain = failure_domain(profile)
    if not domain:
        return
    shell = _shell_of(profile)
    with _lock:
        state = _read(repo_root)
        marks = state["auth_error_domains"]
        assert isinstance(marks, dict)
        marks[domain] = {
            "profile": str(getattr(profile, "name", "") or ""),
            "shell": shell,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "credential_fingerprint": credential_fingerprint(shell),
        }
        _write(repo_root, state)


def clear_success(repo_root: Path, profile: object) -> None:
    domain = failure_domain(profile)
    if not domain:
        return
    with _lock:
        state = _read(repo_root)
        marks = state["auth_error_domains"]
        assert isinstance(marks, dict)
        if marks.pop(domain, None) is not None:
            _write(repo_root, state)


def sweep(repo_root: Path) -> list[str]:
    """Periodic re-check while any mark exists; returns the domains cleared.

    Order: fingerprint (exact, free) → the Shell's status verb only where no
    fingerprint could be read at mark time *and* none can be read now. A
    probe that answers ``False`` is written onto the mark as ``probe`` so
    the catalog can say "the Shell agrees: signed out".
    """
    cleared: list[str] = []
    with _lock:
        state = _drop_stale(repo_root)
        marks = state["auth_error_domains"]
        assert isinstance(marks, dict)
        if not marks:
            return cleared
        changed = False
        for domain, mark in list(marks.items()):
            if not isinstance(mark, dict):
                marks.pop(domain, None)
                changed = True
                continue
            shell = str(mark.get("shell") or "")
            if mark.get("credential_fingerprint") or credential_fingerprint(shell):
                # A fingerprint exists: `_drop_stale` already judged it.
                continue
            verdict = probe_logged_in(shell)
            stamp = datetime.now(timezone.utc).isoformat()
            if verdict is True:
                marks.pop(domain, None)
                cleared.append(domain)
                changed = True
            elif verdict is False:
                if mark.get("probe") != "signed-out":
                    mark["probe"] = "signed-out"
                    mark["probed_at"] = stamp
                    changed = True
        if changed:
            _write(repo_root, state)
    return cleared
