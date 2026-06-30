"""Account-scoped local state for the daemon.

CS4 lifts the daemon's organizing scope from "this checkout" to "this
account, with repo-scoped runs underneath it".  This module owns the small
local account context used by the daemon: a repo registry, a default repo, an
account-owned dispatch inbox, and a durable run-state home.

The store is local-first.  By default it lives under the user's XDG state
directory (or ``~/.local/state``) and is initialized as a plain git repo so a
future brnrd projection can mirror it without changing the local contract.
Tests and explicit installs can override the location with
``account.dominion_path`` / ``BRR_ACCOUNT_DOMINION``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import gitops

DEFAULT_ACCOUNT_ID = "default"
DEFAULT_REPO_LABEL = "local/default"
REGISTRY_PATH = "account/repos.json"
DISPATCH_INBOX_PATH = "dispatch/inbox"
RESPONSES_PATH = "dispatch/responses"
RUN_STATE_PATH = "run-state"


@dataclass(frozen=True)
class AccountRepo:
    """A repo registered under one local account daemon."""

    label: str
    root: Path


@dataclass(frozen=True)
class AccountContext:
    """Resolved account-level state for one daemon process."""

    account_id: str
    dominion_repo: Path
    dispatch_inbox: Path
    responses_dir: Path
    run_state_dir: Path
    repos: dict[str, AccountRepo]
    default_repo: AccountRepo
    enabled: bool = True

    def repo_for_label(self, label: str | None) -> AccountRepo | None:
        if not label:
            return None
        return self.repos.get(label)


def _truthy(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"", "1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._")
    return text or DEFAULT_ACCOUNT_ID


def _expand_path(raw: object, *, base: Path | None = None) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(os.path.expandvars(text)).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path


def _xdg_state_home() -> Path:
    raw = os.environ.get("XDG_STATE_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "state"


def _default_account_root(account_id: str) -> Path:
    return _xdg_state_home() / "brr" / "accounts" / _slug(account_id)


def _is_git_worktree(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _init_git_repo(path: Path) -> None:
    if (path / ".git").exists():
        return
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _load_registry(path: Path) -> tuple[dict[str, AccountRepo], str | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, None
    repos: dict[str, AccountRepo] = {}
    for item in raw.get("repos", []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        root = _expand_path(item.get("path"))
        if label and root is not None:
            repos[label] = AccountRepo(label=label, root=root)
    default_repo = str(raw.get("default_repo") or "").strip() or None
    return repos, default_repo


def _write_registry(
    path: Path,
    repos: dict[str, AccountRepo],
    default_repo: str,
    *,
    account_id: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "account_id": account_id,
        "default_repo": default_repo,
        "repos": [
            {"label": label, "path": str(repo.root)}
            for label, repo in sorted(repos.items())
        ],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def repo_label(repo_root: Path, cfg: dict[str, Any] | None = None) -> str:
    """Best-effort label for a repo registry entry."""

    cfg = cfg or {}
    for key in ("repo.label", "repo_label", "github_repo", "repo_full_name"):
        value = str(cfg.get(key) or "").strip()
        if value:
            return value
    try:
        remote = gitops.default_remote(repo_root)
        if remote:
            url = gitops.remote_url(repo_root, remote)
            if url:
                from .gates.github.parse import parse_origin_url

                parsed = parse_origin_url(url)
                if parsed:
                    return parsed
    except Exception:
        pass
    return repo_root.name or DEFAULT_REPO_LABEL


def event_repo_label(event: dict[str, Any]) -> str | None:
    """Return the repo label/address carried by an event, if any."""

    for key in ("github_repo", "repo_full_name", "repo", "repo_label", "repo_id"):
        value = str(event.get(key) or "").strip()
        if value:
            return value
    return None


def _configured_repos(
    cfg: dict[str, Any],
    *,
    base: Path,
) -> dict[str, AccountRepo]:
    repos: dict[str, AccountRepo] = {}
    prefix = "account.repo."
    for key, value in cfg.items():
        if not key.startswith(prefix):
            continue
        label = key[len(prefix):].strip()
        root = _expand_path(value, base=base)
        if label and root is not None:
            repos[label] = AccountRepo(label=label, root=root)
    return repos


def resolve_context(
    repo_root: Path,
    cfg: dict[str, Any] | None = None,
    *,
    create: bool = True,
) -> AccountContext:
    """Resolve the account context for a daemon started from *repo_root*.

    Existing single-repo installs remain valid: the current checkout is always
    registered as the default repo when no broader registry exists.  The
    account dominion repo is auto-created only from a real git checkout (or
    when an explicit account path is configured), which keeps lightweight unit
    tests and scratch directories from writing into a user's home directory.
    """

    cfg = cfg or {}
    account_id = str(
        cfg.get("account.id")
        or cfg.get("account_id")
        or cfg.get("forge.identity")
        or DEFAULT_ACCOUNT_ID
    ).strip() or DEFAULT_ACCOUNT_ID
    explicit_dominion = (
        _expand_path(os.environ.get("BRR_ACCOUNT_DOMINION"))
        or _expand_path(cfg.get("account.dominion_path"))
        or _expand_path(cfg.get("account.dominion_repo"))
    )
    dominion_repo = explicit_dominion or (_default_account_root(account_id) / "dominion")
    should_create = create and _truthy(cfg.get("account.autocreate"), True) and (
        explicit_dominion is not None or _is_git_worktree(repo_root)
    )
    if should_create:
        dominion_repo.mkdir(parents=True, exist_ok=True)
        _init_git_repo(dominion_repo)

    registry_path = dominion_repo / REGISTRY_PATH
    repos, registry_default = _load_registry(registry_path)
    repos.update(_configured_repos(cfg, base=repo_root))

    current_label = repo_label(repo_root, cfg)
    repos.setdefault(current_label, AccountRepo(label=current_label, root=repo_root))
    default_label = str(
        cfg.get("account.default_repo")
        or cfg.get("account_default_repo")
        or registry_default
        or current_label
    ).strip()
    if default_label not in repos:
        repos[default_label] = AccountRepo(label=default_label, root=repo_root)
    default_repo = repos[default_label]

    if should_create:
        _write_registry(
            registry_path,
            repos,
            default_label,
            account_id=account_id,
        )
        for rel in (DISPATCH_INBOX_PATH, RESPONSES_PATH, RUN_STATE_PATH):
            (dominion_repo / rel).mkdir(parents=True, exist_ok=True)

    return AccountContext(
        account_id=account_id,
        dominion_repo=dominion_repo,
        dispatch_inbox=dominion_repo / DISPATCH_INBOX_PATH,
        responses_dir=dominion_repo / RESPONSES_PATH,
        run_state_dir=dominion_repo / RUN_STATE_PATH,
        repos=repos,
        default_repo=default_repo,
        enabled=_truthy(cfg.get("account.enabled"), True),
    )


def slug_repo_label(label: str) -> str:
    """Filesystem-safe repo label for account-store paths."""

    return _slug(label.replace("/", "__"))
