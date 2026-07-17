"""Knowledge-source chain for wake injection and on-demand lookup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Iterable

from . import account, config as conf, forges, gitops

CHECKOUT_DIRNAME = ".brnrd-kb"
# One source of truth: ``account.relabel_scopes`` has to know every slug-keyed
# directory, and a second spelling of this name is how one gets left behind.
REPLIES_DIRNAME = account.REPLIES_PATH
SYNC_MARKER_FILE = "knowledge.needs-sync"
CAPTURE_LOCK_FILE = "knowledge.capture.lock"
_MAX_SOURCE_BYTES = 2048
_MAX_TOTAL_BYTES = 6144


@dataclass(frozen=True)
class KnowledgeSource:
    name: str
    root: Path
    kind: str


@dataclass(frozen=True)
class SearchHit:
    source: str
    path: Path
    line_no: int
    line: str


@dataclass(frozen=True)
class KnowledgeForgeLocation:
    """A knowledge scope projected through its local remotes to a forge."""

    remote_url: str
    branch: str
    scope_path: str
    repo_root: Path
    pushed_ref: str


def kb_base_url(repo_root: Path, cfg: dict | None = None) -> str | None:
    """Return the forge blob URL prefix for this repo's knowledge pages.

    Home knowledge checkouts commonly point at a local account repository,
    whose own remote points at the forge.  Follow that chain rather than
    stopping at the first filesystem URL.  The final branch must have a
    remote-tracking ref, so a local-only or never-pushed knowledge branch is
    deliberately not advertised.
    """

    location = _knowledge_forge_location(repo_root, cfg)
    if location is None:
        return None
    marker = "__brnrd_kb_page__.md"
    rel = "/".join(part for part in (location.scope_path, marker) if part)
    url = forges.view_blob_url(location.remote_url, location.branch, rel)
    if not url or not url.endswith(marker):
        return None
    return url[: -len(marker)]


def kb_page_url(
    repo_root: Path,
    page_path: str,
    cfg: dict | None = None,
) -> str | None:
    """Return a forge URL for *page_path* only when that page is pushed."""

    location = _knowledge_forge_location(repo_root, cfg)
    if location is None:
        return None
    rel = _knowledge_page_relpath(location.scope_path, page_path)
    if rel is None or not _path_matches_pushed_ref(
        location.repo_root, location.branch, location.pushed_ref, rel,
    ):
        return None
    return forges.view_blob_url(location.remote_url, location.branch, rel)


def _knowledge_forge_location(
    repo_root: Path, cfg: dict | None,
) -> KnowledgeForgeLocation | None:
    """Resolve the active knowledge scope through at most eight remotes."""

    cfg = cfg if cfg is not None else conf.load_config(repo_root)
    start, scope = _knowledge_repo_and_scope(repo_root, cfg)
    if start is None or scope is None:
        return None
    git_root = _git_toplevel(start)
    if git_root is None:
        return None
    try:
        scope_path = scope.resolve().relative_to(git_root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    if scope_path == ".":
        scope_path = ""

    branch = gitops.current_branch(git_root)
    seen: set[Path] = set()
    for _ in range(8):
        try:
            resolved_root = git_root.resolve()
        except OSError:
            return None
        if resolved_root in seen or branch in ("", "HEAD"):
            return None
        seen.add(resolved_root)
        remote = (
            gitops.branch_remote(git_root, branch)
            or gitops.default_remote(git_root)
        )
        if not remote:
            return None
        upstream = gitops.branch_upstream(git_root, branch)
        remote_branch = (
            upstream.split("/", 1)[1]
            if upstream and upstream.startswith(f"{remote}/") else branch
        )
        url = gitops.remote_url(git_root, remote)
        if not url:
            return None
        if forges.detect_forge(url) is not None:
            pushed_ref = f"refs/remotes/{remote}/{remote_branch}"
            if gitops.rev_parse(git_root, pushed_ref) is None:
                return None
            return KnowledgeForgeLocation(
                remote_url=url,
                branch=remote_branch,
                scope_path=scope_path,
                repo_root=git_root,
                pushed_ref=pushed_ref,
            )

        next_root = _local_remote_repo(git_root, url)
        if next_root is None:
            return None
        git_root = next_root
        branch = remote_branch
    return None


def _knowledge_repo_and_scope(
    repo_root: Path, cfg: dict,
) -> tuple[Path | None, Path | None]:
    checkout = repo_root / CHECKOUT_DIRNAME
    try:
        ctx = account.resolve_context(repo_root, cfg, create=False)
        split = (
            ctx.kind == "account"
            and account.knowledge_split_mode(cfg) == "per-repo"
        )
        label = account.repo_label(repo_root, cfg)
        if checkout.is_dir():
            scope = (
                checkout / "repos" / account.slug_repo_label(label)
                if split else checkout
            )
            return checkout, scope
        knowledge_root = account.knowledge_path(ctx)
        scope = account.repo_knowledge_path(ctx, label) if split else knowledge_root
        if knowledge_root.is_dir():
            return knowledge_root, scope
    except Exception:
        pass
    repo_kb = repo_root / "kb"
    if repo_kb.is_dir():
        return repo_root, repo_kb
    return None, None


def _git_toplevel(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=path,
        capture_output=True, text=True, check=False,
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else None


def _local_remote_repo(repo_root: Path, url: str) -> Path | None:
    raw = url[7:] if url.startswith("file://") else url
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    root = _git_toplevel(candidate) if candidate.is_dir() else None
    return root.resolve() if root is not None else None


def _knowledge_page_relpath(scope_path: str, page_path: str) -> str | None:
    raw = str(page_path or "").strip().replace("\\", "/").lstrip("/")
    checkout_prefix = f"{CHECKOUT_DIRNAME}/"
    if raw.startswith(checkout_prefix):
        raw = raw[len(checkout_prefix):]
    if not raw or any(part in ("", ".", "..") for part in raw.split("/")):
        return None
    if scope_path and not (raw == scope_path or raw.startswith(f"{scope_path}/")):
        # ``kb/foo.md`` was the historical relic spelling even for home
        # knowledge.  Its ``kb/`` is a logical label, not a directory there.
        if raw.startswith("kb/"):
            raw = raw[3:]
        raw = f"{scope_path}/{raw}"
    return raw


def _path_matches_pushed_ref(
    repo_root: Path, branch: str, pushed_ref: str, rel_path: str,
) -> bool:
    """Return whether the branch's page content is present on the forge ref."""

    local_blob = _object_id(repo_root, f"{branch}:{rel_path}")
    pushed_blob = _object_id(repo_root, f"{pushed_ref}:{rel_path}")
    return bool(local_blob and local_blob == pushed_blob)


def _object_id(repo_root: Path, spec: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", spec], cwd=repo_root,
        capture_output=True, text=True, check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def sources(repo_root: Path, cfg: dict | None = None) -> list[KnowledgeSource]:
    """Return knowledge sources in prompt/search order."""

    cfg = cfg if cfg is not None else conf.load_config(repo_root)
    result: list[KnowledgeSource] = []
    try:
        ctx = account.resolve_context(repo_root, cfg, create=False)
        if ctx.kind == "account" and account.knowledge_split_mode(cfg) == "per-repo":
            # Account homes span multiple repos — split so a wake sees its
            # own repo's knowledge first, account-wide (cross-repo) second.
            # A project home has exactly one repo, so there is nothing to
            # split; it always uses the flat bucket below.
            label = account.repo_label(repo_root, cfg)
            repo_knowledge = account.repo_knowledge_path(ctx, label)
            if repo_knowledge.is_dir():
                result.append(
                    KnowledgeSource("home knowledge (repo)", repo_knowledge, "home")
                )
            cross_repo_knowledge = account.account_knowledge_path(ctx)
            if cross_repo_knowledge.is_dir():
                result.append(
                    KnowledgeSource("home knowledge (account)", cross_repo_knowledge, "home")
                )
        else:
            home_knowledge = account.knowledge_path(ctx)
            if home_knowledge.is_dir():
                result.append(KnowledgeSource("home knowledge", home_knowledge, "home"))
    except Exception:
        pass

    checkout = repo_root / CHECKOUT_DIRNAME
    if checkout.is_dir():
        result.append(KnowledgeSource("knowledge checkout", checkout, "checkout"))

    repo_kb = repo_root / "kb"
    if repo_kb.is_dir():
        result.append(KnowledgeSource("repo KB", repo_kb, "repo-kb"))

    repo_docs = repo_root / "docs"
    if repo_docs.is_dir():
        result.append(KnowledgeSource("repo docs", repo_docs, "repo-docs"))
    return result


def active_kb_dir(repo_root: Path, cfg: dict | None = None) -> Path | None:
    """Return the one directory a maintenance scan should treat as *the* kb.

    Mirrors :func:`sources`' priority (home knowledge first, repo-committed
    ``kb/`` as the legacy fallback) but narrows to the single directory
    that actually holds authored kb pages — not the ``.brnrd-kb/``
    checkout clone or repo ``docs/``, neither of which the deterministic
    preflight (:mod:`brr.kb_preflight`) or graph stats
    (:mod:`brr.kb_health`) should walk. Returns ``None`` when this repo
    has no kb at all yet (fresh checkout, `brnrd init` not run).
    """
    for source in sources(repo_root, cfg):
        if source.kind in ("home", "repo-kb"):
            return source.root
    return None


def render_injection(repo_root: Path, cfg: dict | None = None) -> str:
    """Render a compact home→repo→docs knowledge block for the wake prompt."""

    blocks: list[str] = []
    used = 0
    for source in sources(repo_root, cfg):
        if source.kind == "checkout":
            continue
        text = _source_excerpt(source)
        if not text:
            continue
        encoded = text.encode("utf-8")
        if blocks and used + len(encoded) > _MAX_TOTAL_BYTES:
            break
        blocks.append(text)
        used += len(encoded)

    if not blocks:
        return ""

    # Name the *writable* directory, not just the readable slice. Without this the
    # wake can read every kb page it owns and still not know where to author one:
    # `run.md` used to point at `.brnrd-kb/`, which is the checkout clone — the one
    # directory `active_kb_dir()` exists to exclude (see its docstring). A resident
    # following the prompt literally lands in the wrong place and has to go *search
    # the filesystem for its own knowledge base*. That is a polling tax on a fact
    # the daemon already holds, so the wake carries it.
    where = ""
    kb_dir = active_kb_dir(repo_root, cfg)
    if kb_dir is not None:
        try:
            rel = kb_dir.relative_to(repo_root)
            shown = f"`{rel}/`"
        except ValueError:
            shown = f"`{kb_dir}/`"
        where = f" Authored pages are written to {shown} — that path, not the clone root.\n"

    return (
        "## Knowledge Sources\n\n"
        "Home knowledge, repo KB, and repo docs in source order. This is the "
        "wake-time slice; use `brnrd kb <query>` for the long tail.\n"
        + where
        + "\n"
        + "\n\n".join(blocks)
    )


def search(
    repo_root: Path,
    query: str,
    cfg: dict | None = None,
    *,
    limit: int = 20,
) -> list[SearchHit]:
    """Search knowledge sources for *query*, preserving source order."""

    needle = query.strip().lower()
    if not needle:
        return []
    hits: list[SearchHit] = []
    for source in sources(repo_root, cfg):
        for path in _iter_docs(source.root):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines, start=1):
                if needle in line.lower():
                    hits.append(SearchHit(source.name, path, idx, line.strip()))
                    if len(hits) >= limit:
                        return hits
    return hits


def ensure_checkout(repo_root: Path, cfg: dict | None = None) -> Path:
    """Materialize the writable home-knowledge checkout beside *repo_root*.

    Re-clones when a checkout already exists but its ``origin`` no longer
    matches the currently-resolved home-knowledge path, instead of treating
    "directory exists" as "checkout is current". Account resolution can
    change after a checkout is first made (a cloud-gate connect fills in
    ``account_id`` where it used to fall back to the literal ``"connected"``,
    an account switch, ``home.path`` moving) — without this check the old
    checkout just sits there forever, silently orphaned, pointed at a path
    nothing else reads or writes. Caught live 2026-07-09: a repo's
    ``.brnrd-kb`` still had ``origin`` set to an early decoy account slot
    weeks after ``cloud.json`` started carrying the real one, at 0 commits.
    """

    cfg = cfg if cfg is not None else conf.load_config(repo_root)
    ctx = account.resolve_context(repo_root, cfg)
    home_knowledge = account.knowledge_path(ctx)
    home_knowledge.mkdir(parents=True, exist_ok=True)
    _init_git_repo(home_knowledge)
    _allow_push_to_checkout(home_knowledge)

    checkout = repo_root / CHECKOUT_DIRNAME
    _exclude_from_project_git(repo_root, f"{CHECKOUT_DIRNAME}/")
    if checkout.exists():
        if _checkout_origin_matches(checkout, home_knowledge):
            return checkout
        shutil.rmtree(checkout, ignore_errors=True)

    result = subprocess.run(
        ["git", "clone", str(home_knowledge), str(checkout)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        checkout.mkdir(parents=True, exist_ok=True)
        _init_git_repo(checkout)
    return checkout


def _checkout_origin_matches(checkout: Path, home_knowledge: Path) -> bool:
    """Return whether *checkout*'s ``origin`` remote still resolves to
    *home_knowledge* — the check :func:`ensure_checkout` skipped before
    2026-07-09, letting a stale clone survive an account-resolution change
    indefinitely."""

    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=checkout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    origin = result.stdout.strip()
    if not origin:
        return False
    try:
        return Path(origin).resolve() == home_knowledge.resolve()
    except OSError:
        return origin == str(home_knowledge)


# ── Capture net (checkout → account knowledge → forge) ───────────────
#
# The dominion has had a capture net since it existed: the daemon commits
# and best-effort pushes it after every thought, so a forgetful thought
# loses nothing it wrote. Knowledge had *none* — no code path in brnrd
# ever committed or pushed the kb. Residents hand-ran a three-step
# workaround out of a pitfall note, and when a run forgot, the writes just
# sat in a working tree: #357 recorded a log entry that stayed uncommitted
# (and therefore absent from every later wake's injected log tail) for a
# day, and on 2026-07-12 the account's knowledge repo was found five
# commits ahead of its forge remote — three log entries that existed
# nowhere but one laptop, in the repo whose entire job is durability.
#
# The chain has three links and each one had a way to silently not happen:
#
#     .brnrd-kb ──push──> home/knowledge ──push──> forge
#     (repo-local          (account-scoped         (the durable,
#      checkout)            working tree)           auth-gated archive)
#
# Pushing into ``home/knowledge`` used to bounce outright: it is a *non-bare*
# clone with ``main`` checked out, and git refuses that by default.
# ``receive.denyCurrentBranch=updateInstead`` is git's own answer — accept
# the push and update the working tree, provided that tree is clean — so
# capture commits stray direct writes *first*, then pushes.


def needs_sync(brr_dir: Path) -> str | None:
    """Return the knowledge sync-needed reason, or ``None`` when in sync.

    Same divergence protocol as the dominion's marker — one mechanism
    (:func:`brr.gitops.read_sync_marker`), two memories.
    """
    return gitops.read_sync_marker(brr_dir, SYNC_MARKER_FILE)


def mark_needs_sync(brr_dir: Path, reason: str) -> None:
    gitops.write_sync_marker(brr_dir, SYNC_MARKER_FILE, reason)


def clear_needs_sync(brr_dir: Path) -> None:
    gitops.clear_sync_marker(brr_dir, SYNC_MARKER_FILE)


def capture(
    repo_root: Path,
    message: str,
    *,
    cfg: dict | None = None,
    brr_dir: Path | None = None,
    lock_timeout: float = 30.0,
    captured_pages: list[str] | None = None,
) -> bool:
    """Commit and push the knowledge chain. Best-effort; never raises.

    Returns True when something was committed or pushed. When
    ``captured_pages`` is supplied, it is extended with the repo-scoped
    markdown pages this capture found dirty (relative to the kb root); this
    lets closeout derive kb relics from the same evidence it commits instead
    of relying on resident bookkeeping. Symmetric with
    :func:`brr.dominion.commit`: a clean chain is a no-op, a *rejected*
    push to the forge sets a ``needs_sync`` marker (the remote diverged —
    reconciling is the resident's judgement, not something to paper over),
    and a successful push clears it.
    """

    try:
        cfg = cfg if cfg is not None else conf.load_config(repo_root)
        ctx = account.resolve_context(repo_root, cfg, create=False)
        home_knowledge = account.knowledge_path(ctx)
    except Exception:  # noqa: BLE001 - capture must never break the thought
        return False
    if not (home_knowledge / ".git").exists():
        return False
    brr_dir = brr_dir if brr_dir is not None else gitops.shared_brr_dir(repo_root)
    checkout = repo_root / CHECKOUT_DIRNAME
    has_checkout = (checkout / ".git").exists()

    moved = False
    try:
        lock = home_knowledge.parent / CAPTURE_LOCK_FILE
        with gitops.file_lock(lock, lock_timeout) as held:
            if not held:
                return False
            _allow_push_to_checkout(home_knowledge)

            if captured_pages is not None:
                seen = set(captured_pages)
                for page in _pending_capture_pages(
                    repo_root, cfg, ctx, home_knowledge, checkout if has_checkout else None,
                ):
                    if page not in seen:
                        captured_pages.append(page)
                        seen.add(page)

            # 1. The repo-local checkout, if a resident wrote through it.
            if has_checkout and gitops.worktree_dirty(checkout):
                moved |= gitops.commit_all(checkout, message)

            # 2. Direct writes into the account tree — today's common path,
            #    since ``active_kb_dir`` points residents straight at it.
            #    Committing these *before* the push is also what makes
            #    ``updateInstead`` accept it (it refuses a dirty tree).
            if gitops.worktree_dirty(home_knowledge):
                moved |= gitops.commit_all(home_knowledge, message)

            branch = gitops.current_branch(home_knowledge)
            if not branch or branch == "HEAD":
                return moved

            # 3. checkout → account. Non-ff (the account tree just took a
            #    stray commit, or a sibling run pushed) → rebase and retry.
            if has_checkout and _ahead_of_upstream(checkout):
                if not gitops.push_branch(checkout, "origin", branch):
                    _pull_rebase(checkout, "origin", branch)
                    if gitops.push_branch(checkout, "origin", branch):
                        moved = True
                    else:
                        mark_needs_sync(
                            brr_dir,
                            f"the knowledge checkout ({checkout}) could not push to "
                            f"the account knowledge repo ({home_knowledge}); "
                            f"reconcile by hand (fetch / rebase / push)",
                        )
                else:
                    moved = True

            # 4. account → forge. The only link that makes the archive
            #    durable off this machine.
            remote = gitops.default_remote(home_knowledge)
            if not remote:
                return moved
            if _ahead_of_upstream(home_knowledge) or needs_sync(brr_dir):
                if gitops.push_branch(home_knowledge, remote, branch):
                    moved = True
                    clear_needs_sync(brr_dir)
                    if has_checkout:
                        gitops.fetch_branch(checkout, "origin", branch)
                else:
                    mark_needs_sync(
                        brr_dir,
                        f"push of {branch} to {remote} was rejected — the knowledge "
                        f"remote has diverged; reconcile by hand (fetch / merge / "
                        f"push) in {home_knowledge}",
                    )
    except Exception:  # noqa: BLE001 - capture is best-effort, never fatal
        return moved
    return moved


def _pending_capture_pages(
    repo_root: Path,
    cfg: dict,
    ctx: account.AccountContext,
    home_knowledge: Path,
    checkout: Path | None,
) -> list[str]:
    """Repo-scoped markdown pages the next knowledge capture will commit."""

    split = (
        ctx.kind == "account"
        and account.knowledge_split_mode(cfg) == "per-repo"
    )
    label = account.repo_label(repo_root, cfg)
    home_scope = (
        account.repo_knowledge_path(ctx, label) if split else home_knowledge
    )
    scopes = [(home_knowledge, home_scope)]
    if checkout is not None:
        checkout_scope = (
            checkout / "repos" / account.slug_repo_label(label)
            if split else checkout
        )
        scopes.append((checkout, checkout_scope))

    pages: set[str] = set()
    for git_root, scope in scopes:
        pages.update(_changed_markdown_paths(git_root, scope))
    return sorted(pages)


def _changed_markdown_paths(git_root: Path, scope: Path) -> set[str]:
    """Changed, non-deleted markdown paths relative to one kb scope."""

    try:
        scope_rel = scope.resolve().relative_to(git_root.resolve())
    except (OSError, ValueError):
        return set()
    pathspec = scope_rel.as_posix() or "."
    commands = (
        ["git", "diff", "--name-only", "--diff-filter=ACMRTUXB", "-z", "HEAD", "--", pathspec],
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", pathspec],
    )
    changed: set[str] = set()
    for command in commands:
        result = subprocess.run(
            command, cwd=git_root, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            continue
        for raw in result.stdout.split("\0"):
            if not raw:
                continue
            path = git_root / raw
            try:
                rel = path.resolve().relative_to(scope.resolve())
            except (OSError, ValueError):
                continue
            if (
                path.is_file()
                and path.suffix.lower() == ".md"
                and (not rel.parts or rel.parts[0] != REPLIES_DIRNAME)
            ):
                changed.add(rel.as_posix())
    return changed


def archive_reply(
    repo_root: Path,
    *,
    run_id: str,
    body: str,
    repo_label: str | None = None,
    meta: dict[str, str] | None = None,
    cfg: dict | None = None,
) -> str | None:
    """Persist a run's terminal user-facing reply into the knowledge repo.

    The reply that reaches the user is the run's answer of record — the one
    artifact a chat client shows and then buries under the next hundred
    messages. Written here it becomes a durable, auth-gated, *linkable*
    page in the same private archive the kb lives in, and the run's relics
    can point at it (``{"kind": "reply", "url": ...}``).

    Terminal replies only, deliberately: interim outbox messages are the
    run thinking out loud, the terminal reply is what it concluded. Stored
    outside the kb page tree (``replies/<repo>/<run-id>.md``, not
    ``repos/<repo>/``) so the kb graph, the preflight scan, and wake
    injection never see them as pages.

    Returns the path relative to the knowledge repo root, or None.
    """

    text = (body or "").strip()
    if not text:
        return None
    try:
        cfg = cfg if cfg is not None else conf.load_config(repo_root)
        ctx = account.resolve_context(repo_root, cfg, create=False)
        home_knowledge = account.knowledge_path(ctx)
        label = repo_label or account.repo_label(repo_root, cfg)
    except Exception:  # noqa: BLE001
        return None
    if not home_knowledge.is_dir() or not run_id:
        return None

    rel = f"{REPLIES_DIRNAME}/{account.slug_repo_label(label)}/{run_id}.md"
    front = {"run": run_id, "repo": label, **{k: v for k, v in (meta or {}).items() if v}}
    lines = ["---"]
    lines += [f"{key}: {value}" for key, value in front.items()]
    lines += ["---", "", text, ""]
    try:
        path = home_knowledge / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        return None
    return rel


def knowledge_file_url(
    repo_root: Path,
    rel_path: str,
    cfg: dict | None = None,
) -> str | None:
    """Forge URL for a path relative to the *knowledge repo root*.

    :func:`kb_page_url` resolves paths inside the repo's kb *scope*
    (``repos/<slug>/``); this resolves anything else in the same repo —
    reply archives today. Same guarantee: a page that isn't on the forge
    gets no link, because a link to an unpushed page is a 404.
    """

    location = _knowledge_forge_location(repo_root, cfg)
    if location is None:
        return None
    rel = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or any(part in ("", ".", "..") for part in rel.split("/")):
        return None
    if not _path_matches_pushed_ref(
        location.repo_root, location.branch, location.pushed_ref, rel,
    ):
        return None
    return forges.view_blob_url(location.remote_url, location.branch, rel)


def _allow_push_to_checkout(home_knowledge: Path) -> None:
    """Let the account's non-bare knowledge clone accept pushes (#357).

    Git refuses a push into a checked-out branch by default — sound, since
    it would desync the working tree from HEAD. ``updateInstead`` is the
    supported alternative: take the push *and* update the working tree, but
    only when that tree is clean. Set idempotently on every capture, so
    accounts created before this existed are repaired rather than left
    bouncing forever.
    """
    subprocess.run(
        ["git", "config", "receive.denyCurrentBranch", "updateInstead"],
        cwd=home_knowledge, capture_output=True, text=True, check=False,
    )


def _ahead_of_upstream(repo: Path) -> bool:
    """True when the current branch has commits its upstream doesn't.

    Guards the network: a clean, already-pushed chain touches no remote.
    An unknown upstream (never pushed) counts as ahead — that's the case
    that most needs the push.
    """
    result = subprocess.run(
        ["git", "rev-list", "--count", "@{upstream}..HEAD"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return True
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return True


def _pull_rebase(repo: Path, remote: str, branch: str) -> bool:
    result = subprocess.run(
        ["git", "pull", "--rebase", remote, branch],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=repo, capture_output=True, text=True, check=False,
        )
        return False
    return True


def _source_excerpt(source: KnowledgeSource) -> str:
    if source.kind == "repo-docs":
        docs = list(_iter_docs(source.root))
        if not docs:
            return ""
        lines = [f"### {source.name}", ""]
        for path in docs[:20]:
            lines.append(f"- {path.relative_to(source.root).as_posix()}")
        if len(docs) > 20:
            lines.append(f"- ... {len(docs) - 20} more")
        return "\n".join(lines)

    for name in ("index.md", "README.md", "log.md"):
        path = source.root / name
        if path.is_file():
            body = path.read_text(encoding="utf-8", errors="replace").strip()
            if len(body.encode("utf-8")) > _MAX_SOURCE_BYTES:
                body = body.encode("utf-8")[:_MAX_SOURCE_BYTES].decode(
                    "utf-8", errors="ignore",
                ).rstrip() + "\n\n..."
            return f"### {source.name} — {name}\n\n{body}"

    docs = list(_iter_docs(source.root))
    if not docs:
        return ""
    lines = [f"### {source.name}", ""]
    for path in docs[:20]:
        lines.append(f"- {path.relative_to(source.root).as_posix()}")
    return "\n".join(lines)


def _iter_docs(root: Path) -> Iterable[Path]:
    for suffix in ("*.md", "*.txt", "*.rst"):
        yield from sorted(p for p in root.rglob(suffix) if p.is_file())


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


def _exclude_from_project_git(repo_root: Path, pattern: str) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return
    exclude = repo_root / result.stdout.strip()
    if not exclude.is_absolute():
        exclude = (repo_root / exclude).resolve()
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if pattern in {line.strip() for line in current.splitlines()}:
        return
    with exclude.open("a", encoding="utf-8") as fh:
        if current and not current.endswith("\n"):
            fh.write("\n")
        fh.write(f"{pattern}\n")
