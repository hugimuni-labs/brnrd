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

from . import account, gitops, repo_deed

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


def _noninteractive_git_env() -> dict[str, str]:
    """Env for every git subprocess this module runs directly.

    Two problems, one fix. First, every call here names its own repo via
    ``cwd=``, so the ``GIT_DIR``/``GIT_WORK_TREE`` overrides a strand run
    inherits (#703) must not steer it at some other tree —
    :func:`gitops.explicit_repo_env` is the established scrub for exactly
    that. Second, and the reason this function exists (#1241): an unset
    ``GIT_TERMINAL_PROMPT`` lets git write a credential prompt
    (``Username for 'https://github.com':``) straight to the real
    ``/dev/tty`` — invisible to ``capture_output=True`` — and hang the
    whole run waiting on a human who isn't watching, with the actual
    failure captured and never shown. ``GIT_TERMINAL_PROMPT=0`` plus a
    no-op ``GIT_ASKPASS`` close the HTTPS side; ``GIT_SSH_COMMAND``'s
    ``BatchMode=yes`` closes the equivalent SSH-side hang (a passphrase or
    host-key prompt) so neither transport is silently exempt.
    """
    env = gitops.explicit_repo_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = shutil.which("true") or "/bin/true"
    ssh_command = env.get("GIT_SSH_COMMAND") or "ssh"
    if "batchmode" not in ssh_command.lower():
        env["GIT_SSH_COMMAND"] = f"{ssh_command} -o BatchMode=yes"
    return env


def _is_ssh_url(url: str) -> bool:
    return url.startswith("git@") or url.startswith("ssh://")


def _github_https_url(url: str) -> str | None:
    """Translate a GitHub SSH remote to the HTTPS spelling, if recognized."""
    if url.startswith("git@github.com:"):
        path = url.removeprefix("git@github.com:").strip("/")
    elif url.startswith("ssh://git@github.com/"):
        path = url.removeprefix("ssh://git@github.com/").strip("/")
    else:
        return None
    return f"https://github.com/{path}"


def _project_origin_is_ssh(repo_root: Path) -> bool:
    """Whether *repo_root*'s own ``origin`` (or first remote) is SSH.

    The signal #1241 asks for: a machine whose HTTPS credential helper is
    broken can still have a perfectly working SSH identity — the project
    the operator is standing in already proves it by using one. Any
    resolution failure (no origin, not a git repo yet, a git call that
    raises) reads as False, same as "no signal either way" — falls through
    to the HTTPS-first default rather than guessing.
    """
    try:
        remote = gitops.default_remote(repo_root)
        if not remote:
            return False
        url = gitops.remote_url(repo_root, remote)
    except Exception:
        return False
    return bool(url) and _is_ssh_url(url)


def _try_gh_setup_git() -> None:
    """Best-effort: wire git's credential helper to gh's stored token.

    Called once per :func:`link_home` call, before the first HTTPS push,
    when the SSH-preferred path isn't in play (#1241) — makes a bare ``gh
    auth login`` (no separate git credential config) *just work* for the
    HTTPS remote instead of leaving git to fall back to an interactive
    prompt this module has just gone to the trouble of disabling. Never
    raises: an unauthenticated or absent ``gh`` just leaves git exactly as
    it was, and the push after this surfaces its own actionable error.
    """
    if not gh_available():
        return
    try:
        _run_gh(["auth", "setup-git"])
    except HomeLinkError:
        pass


def _push_remedy(*, ssh: bool) -> str:
    """The one-line next step for a failed push, not a generic shrug."""
    if ssh:
        return (
            "origin is wired over SSH — verify your SSH identity for "
            "github.com (`ssh -T git@github.com`), then re-run `brnrd home link`."
        )
    if gh_available():
        return (
            "origin is wired — run `gh auth setup-git` (or `gh auth login` "
            "again) to give git a working GitHub credential, then re-run "
            "`brnrd home link`."
        )
    return (
        "origin is wired — install gh (https://cli.github.com/) or "
        "configure a git credential helper for github.com, then re-run "
        "`brnrd home link`."
    )


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


def detect_identity() -> str | None:
    """Best-effort: the GitHub login already resolvable on this machine.

    Same resolution :func:`resolve_owner` uses (``gh auth token`` /
    ``gh api user``) — no new shell-out. Callers that only want to *state*
    the identity, never require it, get a plain optional back instead of a
    raised :class:`HomeLinkError`. ``init`` (stating who the developer is,
    without an account) and the init wake's facts block are both callers;
    neither may fail, or even ask, because ``gh`` is missing or signed out.
    """
    if not gh_available():
        return None
    try:
        return resolve_owner(None)
    except HomeLinkError:
        return None


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


def _ensure_git_repo(path: Path) -> bool:
    """Init a git repo at *path* iff absent. Returns True on a fresh init."""
    if (path / ".git").exists():
        return False
    result = subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        env=_noninteractive_git_env(),
    )
    return result.returncode == 0


def _current_or_symbolic_branch(repo_path: Path) -> str:
    """Return the checked-out branch name, including on an unborn HEAD.

    ``gitops.current_branch`` itself now resolves an unborn HEAD (a
    brand-new repo with no commits yet) to the real branch name via its
    own internal ``symbolic-ref`` fallback (#1340), so the local re-probe
    below is a backstop for two narrower cases only: a genuine measurement
    failure (``gitops.current_branch`` raises ``CurrentBranchUnresolvable``
    rather than returning a sentinel now) and a real detached HEAD (which
    legitimately has no branch name to give). Both still want *some*
    string back — this function's contract is "always answer", unlike
    ``gitops.current_branch``'s "answer or raise" — so both funnel into the
    same symbolic-ref-then-``"main"`` fallback that already existed here.
    """
    try:
        branch = gitops.current_branch(repo_path)
    except gitops.CurrentBranchUnresolvable:
        branch = ""
    if branch and branch != "HEAD":
        return branch
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
        env=_noninteractive_git_env(),
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
        env=_noninteractive_git_env(),
    )
    if check.returncode == 0:
        return
    if gitops.worktree_dirty(repo_path) and gitops.commit_all(repo_path, message):
        return
    # Authored as brnrd, like the ``commit_all`` path above it (#746): this
    # is the founding commit of a repo brnrd is creating for the user, so it
    # is brnrd's commit and not theirs — and pinning the identity is also
    # what makes it land on a machine with no git identity configured.
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
        env=gitops.bot_identity_env(_noninteractive_git_env()),
    )


def _push_current(repo_path: Path, remote: str, *, founding_message: str) -> tuple[bool, str]:
    branch = _current_or_symbolic_branch(repo_path)
    _ensure_has_commit(repo_path, founding_message)
    result = subprocess.run(
        ["git", "push", "-u", remote, f"HEAD:refs/heads/{branch}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
        env=_noninteractive_git_env(),
    )
    if result.returncode != 0:
        raw = result.stderr.strip() or result.stdout.strip() or "git push failed"
        # One meaningful line, not the whole captured blob — same
        # first-line convention ``sync._fetch`` already uses for a git
        # failure surfaced to a human.
        detail = raw.splitlines()[0] if raw else raw
        return False, detail
    return True, ""


def _clone_url(owner: str, name: str) -> str:
    return f"https://github.com/{owner}/{name}.git"


def _clone_url_ssh(owner: str, name: str) -> str:
    return f"git@github.com:{owner}/{name}.git"


def _link_one(
    *,
    slot: str,
    repo_path: Path,
    owner: str,
    name: str,
    ssh: bool = False,
    prepare_push: Callable[[], None] | None = None,
) -> RepoLinkResult:
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

    clone_url = _clone_url_ssh(owner, name) if ssh else _clone_url(owner, name)
    add = subprocess.run(
        ["git", "remote", "add", "origin", clone_url],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
        env=_noninteractive_git_env(),
    )
    if add.returncode != 0:
        raise HomeLinkError(
            f"{slot}: {action} {owner}/{name} but could not wire origin "
            f"({add.stderr.strip() or 'git remote add failed'})"
        )

    # The link seam is where this repo lands on the user's own GitHub
    # profile — the deed README is what renders there, so seed (and commit)
    # it before the push. Write-if-absent: an owner's existing README wins.
    # On an unborn HEAD the deed commit *is* the founding commit; the
    # `_ensure_has_commit` fallback below keeps the same named message for
    # the deed-write-failed edge.
    repo_deed.ensure_deed(repo_path, slot)

    if not ssh and prepare_push is not None:
        prepare_push()

    ok, detail = _push_current(
        repo_path, "origin",
        founding_message=repo_deed.founding_commit_message(slot),
    )
    if not ok:
        raise HomeLinkError(
            f"{slot}: origin set to {url} but the initial push failed: {detail} — "
            f"{_push_remedy(ssh=ssh)}"
        )
    return RepoLinkResult(slot=slot, path=repo_path, remote_url=url, action=action, pushed=True)


def _retry_push_if_needed(
    *,
    slot: str,
    repo_path: Path,
    url: str,
    ssh: bool,
    prepare_push: Callable[[], None] | None,
) -> RepoLinkResult:
    """The already-linked branch's own push attempt (#1422).

    ``existing_remote`` only proves ``origin`` is wired, not that anything
    ever reached it: a first-run push failure (origin wired at :377, push
    failed at :402-410) used to leave every later ``link_home`` call
    reporting ``already-linked, pushed=False`` forever, with nothing that
    ever retried. This is the retry.

    Zero-network on the common healthy case: :func:`gitops.has_pushed_upstream`
    reads the local upstream-tracking record ``git push -u`` itself writes
    on success — a free, already-there "was this ever pushed" fact, not a
    ``git ls-remote`` probe run on every call. Only a repo with **no** such
    record attempts a push, reusing :func:`_push_current` with the same
    founding-message machinery :func:`_link_one` uses for a first link — so
    an interrupted repo with no commit yet still gets one, exactly as a
    fresh link would.
    """
    if gitops.has_pushed_upstream(repo_path):
        return RepoLinkResult(
            slot=slot, path=repo_path, remote_url=url, action="already-linked", pushed=True,
        )

    if not ssh and prepare_push is not None:
        prepare_push()

    ok, detail = _push_current(
        repo_path, "origin", founding_message=repo_deed.founding_commit_message(slot),
    )
    if not ok:
        raise HomeLinkError(
            f"{slot}: origin is wired to {url} but the push failed: {detail} — "
            f"{_push_remedy(ssh=ssh)}"
        )
    return RepoLinkResult(
        slot=slot, path=repo_path, remote_url=url, action="already-linked", pushed=True,
    )


# ── entry point ─────────────────────────────────────────────────────────


def existing_home_remotes(
    repo_root: Path,
    cfg: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """Both home slots already wired *and* pushed → ``{slot: url}``; else ``None``.

    The read behind "don't ask for consent to work that is already done".
    :func:`link_home` has always been idempotent, but the *question* in
    front of it was not: ``brnrd connect`` asked whether to back the home
    up on every interactive run, including on a machine whose home had
    been living in two private repos for weeks (2026-08-29, adding a
    second repo to an account that already had one).

    Deliberately the same predicate :func:`link_home` uses to decide a
    slot needs nothing — an ``origin`` **and**
    :func:`gitops.has_pushed_upstream` — so this never reports durable for
    a state ``link_home`` would still act on. Anything short of that reads
    as ``None``: an unresolvable home, a missing knowledge checkout, one
    slot linked and the other not. A partially-linked home has a real
    question to answer, and answering it for the user is the failure this
    guards against in the other direction.

    Zero network and zero ``gh``: git config reads only, so it is safe on
    the interactive path where the caller has not yet paid for either.
    """
    try:
        ctx = account.resolve_context(repo_root, cfg or {}, create=False)
    except Exception:
        return None
    if ctx.kind != "account":
        return None

    slots = {
        "dominion": ctx.dominion_repo,
        "knowledge": account.knowledge_path(ctx),
    }
    linked: dict[str, str] = {}
    for slot, path in slots.items():
        if path is None or not Path(path).exists():
            return None
        remote = gitops.default_remote(path)
        if not remote:
            return None
        url = gitops.remote_url(path, remote)
        if not url or not gitops.has_pushed_upstream(path):
            return None
        linked[slot] = url
    return linked


def link_home(
    repo_root: Path,
    cfg: dict[str, Any] | None = None,
    *,
    owner: str | None = None,
    dominion_name: str = DEFAULT_DOMINION_NAME,
    knowledge_name: str = DEFAULT_KNOWLEDGE_NAME,
    ssh: bool = False,
    on_result: Callable[[RepoLinkResult], None] | None = None,
) -> list[RepoLinkResult]:
    """Idempotently wire *repo_root*'s resolved home to two private GitHub repos.

    Does the whole two-repo job in one call — no per-repo prompting. Each
    of the dominion and knowledge repos, independently:

    - already has an ``origin`` **and** a record of a successful push
      (:func:`gitops.has_pushed_upstream`) → reported as
      ``"already-linked"``, ``pushed=True``, left untouched (no network).
    - already has an ``origin`` but no such record (a first push that
      never landed, #1422) → ``"already-linked"``, and this call retries
      the push before returning.
    - no origin, but ``owner/name`` already exists on GitHub → adopted:
      origin wired, pushed.
    - no origin, no existing repo → created ``--private``, wired, pushed.

    The GitHub owner is resolved lazily — only when a repo actually needs
    ``gh`` (create/adopt) — so a healthy, already-pushed re-run needs no
    ``gh`` call at all, and needs no network. Raises :class:`HomeLinkError`
    with a specific, actionable message on any failure; a repo whose
    origin was wired but whose push then failed is named exactly that in
    the message (never silently half-wired, and never silently un-retried).

    *on_result* fires immediately after each repo finishes, so a caller
    that then hits a HomeLinkError on the second repo still knows the
    first repo's outcome.

    Home repositories use HTTPS through ``gh auth setup-git`` by default.
    The checkout's own transport is unrelated ambient state: inheriting its
    SSH URL can select a different GitHub identity from the one ``gh`` just
    authenticated and used to create these private repos. ``ssh=True`` is
    the explicit override for operators who deliberately want that route.
    """
    cfg = cfg or {}
    ctx = account.resolve_context(repo_root, cfg)

    knowledge_root = account.knowledge_path(ctx)
    knowledge_root.mkdir(parents=True, exist_ok=True)
    if _ensure_git_repo(knowledge_root):
        # Born just now — deed at birth, so even the already-linked early
        # return below (which never reaches ``_link_one``) leaves a founded,
        # self-explaining repo behind.
        repo_deed.ensure_deed(knowledge_root, "knowledge")

    plan = [
        ("dominion", ctx.dominion_repo, dominion_name),
        ("knowledge", knowledge_root, knowledge_name),
    ]

    use_ssh = ssh
    setup_git_tried = False

    def _prepare_https_push() -> None:
        nonlocal setup_git_tried
        if setup_git_tried:
            return
        setup_git_tried = True
        _try_gh_setup_git()

    resolved_owner = owner
    results: list[RepoLinkResult] = []
    failures: list[str] = []
    for slot, path, name in plan:
        try:
            existing_remote = gitops.default_remote(path)
            if existing_remote:
                url = gitops.remote_url(path, existing_remote) or ""
                if not use_ssh and (https_url := _github_https_url(url)):
                    changed = subprocess.run(
                        ["git", "remote", "set-url", existing_remote, https_url],
                        cwd=path,
                        capture_output=True,
                        text=True,
                        check=False,
                        env=_noninteractive_git_env(),
                    )
                    if changed.returncode != 0:
                        raise HomeLinkError(
                            f"{slot}: could not move the interrupted SSH origin to authenticated HTTPS "
                            f"({changed.stderr.strip() or 'git remote set-url failed'})"
                        )
                    url = https_url
                result = _retry_push_if_needed(
                    slot=slot, repo_path=path, url=url, ssh=use_ssh,
                    prepare_push=None if use_ssh else _prepare_https_push,
                )
            else:
                if resolved_owner is None:
                    _require_gh_auth()
                    resolved_owner = resolve_owner(owner)
                result = _link_one(
                    slot=slot, repo_path=path, owner=resolved_owner, name=name,
                    ssh=use_ssh,
                    prepare_push=None if use_ssh else _prepare_https_push,
                )
        except HomeLinkError as exc:
            failures.append(str(exc))
            continue
        else:
            results.append(result)
            if on_result is not None:
                on_result(result)
    if failures:
        raise HomeLinkError("; ".join(failures))
    return results
