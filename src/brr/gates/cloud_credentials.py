"""Managed GitHub publishing credentials for the cloud relay gate."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class CredentialContext:
    """Relay-owned state and HTTP dependencies, supplied without a cycle."""

    request: Callable[..., dict]
    load_state: Callable[[Path], dict]
    state_dir: Path | None
    token_expires_at: float
    set_token_expires_at: Callable[[float], None]
    retry_at: float
    set_retry_at: Callable[[float], None]
    lock: threading.Lock


_context_factory: Callable[[], CredentialContext] | None = None


def configure_context(factory: Callable[[], CredentialContext]) -> None:
    global _context_factory
    _context_factory = factory


def _context() -> CredentialContext:
    if _context_factory is None:
        raise RuntimeError("cloud credential context is not configured")
    return _context_factory()

_PUBLISHING_TOKEN_REFRESH_S = 10 * 60
_PUBLISHING_TOKEN_DISPATCH_MIN_S = 50 * 60
_GITHUB_CREDENTIAL_SUBPATH = ("credentials", "github")
_CREDENTIAL_DIR_MODE = 0o700
_CREDENTIAL_FILE_MODE = 0o600

def publishing_token_seconds_remaining() -> float:
    """Seconds of life left on the managed token, or ``0.0`` when there is none.

    Reported rather than inferred: callers that want to *say* how much runway
    they handed a runner should read it here instead of restating the policy
    constant, which is the number most likely to drift.
    """
    if os.environ.get("GH_TOKEN"):
        # Operator-supplied identity: brnrd never minted it and cannot date it.
        return float("inf")
    if not os.environ.get("BRNRD_MANAGED_GITHUB_TOKEN"):
        return 0.0
    return max(0.0, _context().token_expires_at - time.time())


def ensure_publishing_credential_fresh(
    brr_dir: Path | None = None,
    *,
    min_remaining_s: float = _PUBLISHING_TOKEN_DISPATCH_MIN_S,
) -> float:
    """Renew the managed token if it has less than *min_remaining_s* left.

    Called when a runner environment is built. The poll loop's renewal is
    paced for the daemon's own needs and can legitimately leave a token with
    ten minutes on it; a dispatched runner holds its snapshot for the whole
    run and has no way to ask for a newer one, so dispatch forces the check
    here rather than hoping the loop happened to fire recently.

    Best-effort by construction: a cloud gate that is unconfigured, offline,
    or mid-deploy must never block a run from starting — the run simply
    proceeds with whatever credential it already had, exactly as before this
    check existed. Returns the seconds of token life the caller is handing
    over, for logging.
    """
    if os.environ.get("GH_TOKEN"):
        return float("inf")
    target = brr_dir if brr_dir is not None else _context().state_dir
    if target is None:
        # No cloud gate running in this process — nothing mints a managed
        # token here, so there is nothing to keep fresh.
        return publishing_token_seconds_remaining()
    try:
        state = _context().load_state(target)
    except Exception:
        return publishing_token_seconds_remaining()
    if not state.get("token") or not state.get("brnrd_url"):
        return publishing_token_seconds_remaining()
    _try_refresh_publishing_credential(state, min_remaining_s=min_remaining_s, brr_dir=target)
    return publishing_token_seconds_remaining()


def github_credentials_dir(brr_dir: Path | None = None) -> Path | None:
    """Absolute path of the daemon-refreshed GitHub credential pointer dir.

    ``None`` when no cloud gate state dir is known in this process — nothing
    mints or refreshes a managed token here, so there is no pointer to read.
    Callers building a runner env point ``GH_CONFIG_DIR`` and a git credential
    helper at this directory rather than freezing a token value into the env.
    """
    target = brr_dir if brr_dir is not None else _context().state_dir
    if target is None:
        return None
    return Path(os.path.abspath(target)).joinpath(*_GITHUB_CREDENTIAL_SUBPATH)


def _atomic_write_private(path: Path, content: str) -> None:
    """Write *content* to *path* atomically with owner-only POSIX mode.

    Same discipline as the gate state store (#499): the temp file is made
    private *before* it holds the secret, then renamed into place, so a
    concurrent reader never sees a partial token and a permissive existing
    mode is repaired on every rewrite.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(path.parent, _CREDENTIAL_DIR_MODE)
        except OSError:
            pass
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        if os.name == "posix":
            os.fchmod(fd, _CREDENTIAL_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(content)
        os.replace(tmp_name, path)
    finally:
        if fd != -1:
            os.close(fd)
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _write_github_credential_pointer(brr_dir: Path | None, token: str) -> None:
    """Publish the managed token as a gh-shaped pointer dir (issue #477).

    Best-effort: a failure to write the pointer must never break credential
    renewal itself — the daemon still holds the token in memory for its own
    pushes even if the runner-facing pointer could not be refreshed.
    """
    pointer_dir = github_credentials_dir(brr_dir)
    if pointer_dir is None:
        return
    try:
        # gh reads `<GH_CONFIG_DIR>/hosts.yml` at each invocation, so writing
        # the current token here keeps every `gh` call authenticated as the
        # managed identity without an env snapshot.
        hosts_yml = (
            "github.com:\n"
            f"    oauth_token: {token}\n"
            "    user: x-access-token\n"
            "    git_protocol: https\n"
        )
        _atomic_write_private(pointer_dir / "token", token + "\n")
        _atomic_write_private(pointer_dir / "hosts.yml", hosts_yml)
    except OSError as exc:
        print(f"[brnrd:cloud] github credential pointer write failed: {exc}")


def _refresh_publishing_credential(
    state: dict,
    *,
    force: bool = False,
    min_remaining_s: float = _PUBLISHING_TOKEN_REFRESH_S,
    brr_dir: Path | None = None,
) -> None:
    """Keep the managed GitHub App token in memory, never cloud state.

    *min_remaining_s* is the amount of token life the caller needs. The poll
    loop asks for the default (renew only when nearly expired); dispatch asks
    for a much larger floor, because it is handing the token to a process that
    cannot come back for a fresh one.

    Every renewal also rewrites the runner-facing pointer dir (issue #477) so
    a run already in flight — which cannot re-read an exported env value —
    still resolves the fresh token through ``GH_CONFIG_DIR`` and the git
    credential helper.
    """
    if os.environ.get("GH_TOKEN"):
        return
    now = time.time()
    ctx = _context()
    with ctx.lock:
        if not force and ctx.token_expires_at - now > min_remaining_s:
            return
        credential = _context().request(
            state["brnrd_url"],
            "POST",
            "/v1/daemons/publishing-credential",
            token=state["token"],
            timeout=20,
        )
        expires_at = datetime.fromisoformat(str(credential["expires_at"]).replace("Z", "+00:00"))
        token = str(credential["token"])
        os.environ["BRNRD_MANAGED_GITHUB_TOKEN"] = token
        ctx.set_token_expires_at(expires_at.timestamp())
        _write_github_credential_pointer(brr_dir, token)
        print(
            f"[brnrd:cloud] publishing as {credential.get('login') or 'GitHub App'} "
            f"(credential expires {expires_at.isoformat()})"
        )


def _try_refresh_publishing_credential(
    state: dict,
    *,
    force: bool = False,
    min_remaining_s: float = _PUBLISHING_TOKEN_REFRESH_S,
    brr_dir: Path | None = None,
) -> None:
    """Refresh best-effort without letting publishing auth stall chat ingress."""
    now = time.time()
    if not force and now < _context().retry_at:
        return
    try:
        _refresh_publishing_credential(
            state, force=force, min_remaining_s=min_remaining_s, brr_dir=brr_dir
        )
    except Exception as exc:
        _context().set_retry_at(now + 5 * 60)
        print(f"[brnrd:cloud] publishing credential unavailable: {exc}")
    else:
        _context().set_retry_at(0.0)

