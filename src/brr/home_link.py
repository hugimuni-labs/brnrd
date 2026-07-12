"""One-question durability opt-in for a resolved brnrd home.

Every resolved home (``account.resolve_context``) carries two local-only
git repos: the dominion (resident memory, ``ctx.dominion_repo``) and the
knowledge base (``account.knowledge_path(ctx)``). Nothing wires either to a
remote — an operator who wants their agent's memory to survive a wiped
machine has always had to do it by hand, and nothing documented the repo
names to use. :func:`link_home` is the single idempotent action that does
both in one shot: adopt an existing GitHub repo if one already carries the
name, otherwise create a private one, wire ``origin``, and push.

Privacy is not negotiable here — these two repos carry agent memory and kb
prose, so every created repo is ``--private`` and there is no flag to
change that.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import account, gitops

DEFAULT_DOMINION_NAME = "brnrd-home"
DEFAULT_KNOWLEDGE_NAME = "brnrd-knowledge"

_GH_TIMEOUT = 20.0
_GH_MISSING_MESSAGE = (
    "gh (GitHub CLI) is not installed — install it from https://cli.github.com/ "
    "to back up the agent's memory and knowledge base, or skip this step."
)


class HomeLinkError(RuntimeError):
    """An actionable, user-facing failure — never a bare traceback."""


@dataclass(frozen=True)
class RepoLinkResult:
    """The outcome of linking one home-scoped repo."""

    slot: str  # "dominion" | "knowledge"
    path: Path
    remote_url: str
    action: str  # "already-linked" | "adopted" | "created"
    pushed: bool


# ── gh CLI plumbing ────────────────────────────────────────────────────


def gh_available() -> bool:
    """Return whether the ``gh`` binary is on PATH.

    Callers that offer this feature as an *optional* step (init's single
    question) should check this first and skip silently when it's False —
    init must never fail, or even ask, because ``gh`` is missing.
    """
    return shutil.which("gh") is not None


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HomeLinkError(_GH_MISSING_MESSAGE) from exc
    except subprocess.TimeoutExpired as exc:
        raise HomeLinkError(f"gh timed out running: gh {' '.join(args)}") from exc


def _require_gh_auth() -> None:
    if not gh_available():
        raise HomeLinkError(_GH_MISSING_MESSAGE)
    result = _run_gh(["auth", "status"])
    if result.returncode != 0:
        raise HomeLinkError(
            "gh is not authenticated — run `gh auth login` first, or skip this step."
        )


def resolve_owner(explicit: str | None = None) -> str:
    """Return the GitHub owner login to create/adopt repos under.

    *explicit* (an ``--owner`` flag) always wins; otherwise this shells out
    to ``gh api user`` — never prompts.
    """
    if explicit:
        return explicit
    result = _run_gh(["api", "user", "-q", ".login"])
    login = result.stdout.strip()
    if result.returncode != 0 or not login:
        detail = result.stderr.strip() or "gh api user failed"
        raise HomeLinkError(f"could not resolve the GitHub owner ({detail}) — pass --owner")
    return login


def _repo_view(owner: str, name: str) -> dict[str, Any] | None:
    """Return ``{"url": …, "visibility": …}`` for ``owner/name``, or None.

    ``visibility`` is not decoration: the *adopt* path wires origin to a repo
    that already exists, and adopting a **public** one would push the agent's
    memory and kb prose straight onto a public profile — the exact outcome
    this module's own docstring calls non-negotiable, arrived at through the
    one door that wasn't checking. (Same shape as the overflow gist that
    shipped ``--public`` against the design page arguing for
    data-minimization: the creating path was careful, the adopting path was
    never asked.)
    """
    result = _run_gh([
        "repo", "view", f"{owner}/{name}", "--json", "url,visibility",
    ])
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _repo_create(owner: str, name: str) -> str:
    """Create a private ``owner/name`` GitHub repo. Returns its URL."""
    result = _run_gh(["repo", "create", f"{owner}/{name}", "--private"])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "gh repo create failed"
        raise HomeLinkError(f"could not create {owner}/{name}: {detail}")
    info = _repo_view(owner, name)
    if info and info.get("url"):
        return str(info["url"])
    text = (result.stdout or "").strip()
    return text.splitlines()[-1] if text else f"https://github.com/{owner}/{name}"


# ── local git plumbing ─────────────────────────────────────────────────


def _ensure_git_repo(path: Path) -> None:
    if (path / ".git").exists():
        return
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )


def _current_or_symbolic_branch(repo_path: Path) -> str:
    """Return the checked-out branch name, including on an unborn HEAD.

    ``gitops.current_branch`` uses ``rev-parse --abbrev-ref HEAD``, which
    fails on a brand-new repo with no commits yet (returns ``"HEAD"``
    here) — the same unborn-branch case ``account.run_state_blob_url``
    already works around with ``symbolic-ref``.
    """
    branch = gitops.current_branch(repo_path)
    if branch and branch != "HEAD":
        return branch
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    resolved = result.stdout.strip() if result.returncode == 0 else ""
    return resolved or "main"


def _ensure_has_commit(repo_path: Path, message: str) -> None:
    check = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode == 0:
        return
    if gitops.worktree_dirty(repo_path) and gitops.commit_all(repo_path, message):
        return
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _push_current(repo_path: Path, remote: str) -> tuple[bool, str]:
    branch = _current_or_symbolic_branch(repo_path)
    _ensure_has_commit(repo_path, f"brnrd: seed {repo_path.name}")
    result = subprocess.run(
        ["git", "push", "-u", remote, f"HEAD:refs/heads/{branch}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git push failed"
        return False, detail
    return True, ""


def _clone_url(owner: str, name: str) -> str:
    return f"https://github.com/{owner}/{name}.git"


def _link_one(*, slot: str, repo_path: Path, owner: str, name: str) -> RepoLinkResult:
    info = _repo_view(owner, name)
    if info is not None:
        # Explicit PRIVATE or nothing: an unreadable visibility is not a
        # licence to push memory into it. Refusing is cheap and recoverable;
        # a public push is neither.
        visibility = str(info.get("visibility") or "unknown").strip().upper()
        if visibility != "PRIVATE":
            raise HomeLinkError(
                f"{slot}: {owner}/{name} already exists and is {visibility.lower()} — "
                f"refusing to push agent memory to a repo that isn't private. "
                f"Make it private on GitHub, or pass a different name "
                f"(--{slot}-name)."
            )
        url = str(info.get("url") or f"https://github.com/{owner}/{name}")
        action = "adopted"
    else:
        url = _repo_create(owner, name)
        action = "created"

    clone_url = _clone_url(owner, name)
    add = subprocess.run(
        ["git", "remote", "add", "origin", clone_url],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        raise HomeLinkError(
            f"{slot}: {action} {owner}/{name} but could not wire origin "
            f"({add.stderr.strip() or 'git remote add failed'})"
        )

    ok, detail = _push_current(repo_path, "origin")
    if not ok:
        raise HomeLinkError(
            f"{slot}: origin set to {url} but the initial push failed ({detail}) — "
            f"origin is wired; re-run `brnrd home link` once fixed."
        )
    return RepoLinkResult(slot=slot, path=repo_path, remote_url=url, action=action, pushed=True)


# ── entry point ─────────────────────────────────────────────────────────


def link_home(
    repo_root: Path,
    cfg: dict[str, Any] | None = None,
    *,
    owner: str | None = None,
    dominion_name: str = DEFAULT_DOMINION_NAME,
    knowledge_name: str = DEFAULT_KNOWLEDGE_NAME,
    on_result: Callable[[RepoLinkResult], None] | None = None,
) -> list[RepoLinkResult]:
    """Idempotently wire *repo_root*'s resolved home to two private GitHub repos.

    Does the whole two-repo job in one call — no per-repo prompting. Each
    of the dominion and knowledge repos, independently:

    - already has an ``origin`` → reported as ``"already-linked"``, left
      untouched (no forced re-push).
    - no origin, but ``owner/name`` already exists on GitHub → adopted:
      origin wired, pushed.
    - no origin, no existing repo → created ``--private``, wired, pushed.

    The GitHub owner is resolved lazily — only when a repo actually needs
    ``gh`` (create/adopt) — so a fully-linked re-run needs no ``gh`` call
    at all, and needs no network. Raises :class:`HomeLinkError` with a
    specific, actionable message on any failure; a repo whose origin was
    wired but whose initial push then failed is named exactly that in the
    message (never silently half-wired).

    *on_result* fires immediately after each repo finishes, so a caller
    that then hits a HomeLinkError on the second repo still knows the
    first repo's outcome.
    """
    cfg = cfg or {}
    ctx = account.resolve_context(repo_root, cfg)

    knowledge_root = account.knowledge_path(ctx)
    knowledge_root.mkdir(parents=True, exist_ok=True)
    _ensure_git_repo(knowledge_root)

    plan = [
        ("dominion", ctx.dominion_repo, dominion_name),
        ("knowledge", knowledge_root, knowledge_name),
    ]

    resolved_owner = owner
    results: list[RepoLinkResult] = []
    for slot, path, name in plan:
        existing_remote = gitops.default_remote(path)
        if existing_remote:
            url = gitops.remote_url(path, existing_remote) or ""
            result = RepoLinkResult(
                slot=slot, path=path, remote_url=url, action="already-linked", pushed=False,
            )
        else:
            if resolved_owner is None:
                _require_gh_auth()
                resolved_owner = resolve_owner(owner)
            result = _link_one(slot=slot, repo_path=path, owner=resolved_owner, name=name)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results
