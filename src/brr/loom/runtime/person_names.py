"""Display labels from the self's main. Addresses and ledger identities stay literal."""
from __future__ import annotations

import re
from pathlib import Path

_CACHE: dict[Path, tuple[str, dict[str, str]]] = {}


def names(repo: Path) -> dict[str, str]:
    from .lifecycle import git, main_tip
    repo = Path(repo)
    if not (repo / ".git").exists():
        return {}
    tip = main_tip(repo)
    cached = _CACHE.get(repo)
    if cached and cached[0] == tip:
        return dict(cached[1])
    result = {}
    paths = git(repo, "ls-tree", "-r", "--name-only", tip, "--", "people").stdout.splitlines()
    for path in paths:
        match = re.fullmatch(r'people/([^/]+)/README\.md', path)
        if not match:
            continue
        text = git(repo, "show", f"{tip}:{path}").stdout
        title = next((line[2:].strip() for line in text.splitlines()
                      if line.startswith('# ') and line[2:].strip()), '')
        if title:
            result[match[1]] = title
    _CACHE[repo] = (tip, result)
    return dict(result)


def label(identity: str, resolved: dict[str, str] | None = None) -> str:
    for prefix in ('person:', 'p-'):
        if identity.startswith(prefix):
            slug = identity[len(prefix):]
            return (resolved or {}).get(slug, slug)
    return identity
