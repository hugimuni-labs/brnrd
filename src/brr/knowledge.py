"""Knowledge-source chain for wake injection and on-demand lookup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Iterable

from . import account, config as conf, forges, gitops

CHECKOUT_DIRNAME = ".brnrd-kb"
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
    return (
        "## Knowledge Sources\n\n"
        "Home knowledge, repo KB, and repo docs in source order. This is the "
        "wake-time slice; use `brnrd kb <query>` for the long tail.\n\n"
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
