"""Knowledge-source chain for wake injection and on-demand lookup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Iterable

from . import account, config as conf

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
    """Materialize the writable home-knowledge checkout beside *repo_root*."""

    cfg = cfg if cfg is not None else conf.load_config(repo_root)
    ctx = account.resolve_context(repo_root, cfg)
    home_knowledge = account.knowledge_path(ctx)
    home_knowledge.mkdir(parents=True, exist_ok=True)
    _init_git_repo(home_knowledge)

    checkout = repo_root / CHECKOUT_DIRNAME
    _exclude_from_project_git(repo_root, f"{CHECKOUT_DIRNAME}/")
    if checkout.exists():
        return checkout

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
