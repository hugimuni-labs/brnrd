"""Copy a committed project kb into home knowledge, with explicit provenance.

Planning never creates a home or changes either repository. Applying shares
capture's lock, copies the source commit, and commits only the new scope.
Removing the old kb and mounting its replacement remain manual follow-ups.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import posixpath
import re
import subprocess
from urllib.parse import quote, unquote, urlsplit

from . import account, forges, gitops, knowledge
from .kb_preflight import _blank_code


@dataclass
class MigrationPlan:
    source: Path
    destination: Path
    source_commit: str = ""
    files: dict[str, bytes] = field(default_factory=dict, repr=False)
    modes: dict[str, int] = field(default_factory=dict, repr=False)
    rewrites: list[tuple[str, str, str]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    commit: str = ""

    def lines(self) -> list[str]:
        rows = [f"source: {self.source} @ {self.source_commit or 'unknown'}",
                f"destination: {self.destination}"]
        rows.extend(f"{self.source / name} -> {self.destination / name}"
                    for name in self.files)
        rows.append(f"links rewritten: {len(self.rewrites)}")
        rows.extend(f"  {name}: {old} -> {new}" for name, old, new in self.rewrites)
        rows.extend(f"refused: {reason}" for reason in self.conflicts)
        if self.commit:
            rows.append(f"copied and committed: {self.commit}; source untouched")
            rows.extend([
                "Follow-ups (run by hand):",
                f"1. Open a PR in {self.source.parent}: remove committed kb/; "
                "add /kb to Git's info/exclude (matches the symlink); add to AGENTS.md: "
                "'The kb is kb/, a mount; write pages there.'",
                f"2. From {self.source.parent}, run: brnrd kb mount --apply",
            ])
        elif not self.conflicts:
            rows.append("dry-run: no files changed; use --apply to copy and commit")
        return rows


def _git(root: Path, *args: str, commit: bool = False) -> bytes:
    env = gitops.bot_identity_env() if commit else gitops.explicit_repo_env()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(["git", *args], cwd=root, env=env,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


# Locate destination starts; consume balanced parentheses separately so code
# links like ../src/f(x).py do not get truncated. Reference definitions and
# images use the same URL rules as inline links. Code examples stay verbatim.
_LINK_START = re.compile(r"\]\(\s*|^ {0,3}\[[^\]\n]+\]:[ \t]*", re.MULTILINE)


def _destinations(text: str):
    visible = _blank_code(text)
    for match in _LINK_START.finditer(visible):
        start = match.end()
        if start >= len(text):
            continue
        if text[start] == '<':
            end = visible.find('>', start + 1)
            if end >= 0:
                yield start + 1, end
            continue
        end, depth = start, 0
        while end < len(visible):
            char = visible[end]
            if char == '\\' and end + 1 < len(visible):
                end += 2
                continue
            if char.isspace() or (char == ')' and depth == 0):
                break
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            end += 1
        if end > start:
            yield start, end


def _rewrite(text: str, name: str, remote: str, revision: str):
    changes = []
    replacements = []
    for start, end in _destinations(text):
        raw = text[start:end]
        url = urlsplit(raw)
        if url.scheme or url.netloc or not url.path or url.path.startswith('/'):
            continue
        path = unquote(re.sub(r"\\([\\() ])", r"\1", url.path))
        target = posixpath.normpath(posixpath.join('kb', posixpath.dirname(name), path))
        if target == 'kb' or target.startswith('kb/'):
            continue
        if target == '..' or target.startswith('../'):
            raise ValueError(f"{name}: link leaves the project; cannot derive a forge URL: {raw}")
        absolute = forges.view_blob_url(remote, revision, quote(target, safe='/'))
        if not absolute:
            raise ValueError(f"{name}: no supported forge remote for link: {raw}")
        if url.query:
            absolute += '?' + url.query
        if url.fragment:
            absolute += '#' + url.fragment
        replacements.append((start, end, absolute))
        changes.append((name, raw, absolute))
    for start, end, value in reversed(replacements):
        text = text[:start] + value + text[end:]
    return text, changes


def plan_migration(repo_root: Path, home: Path, label: str) -> MigrationPlan:
    repo_root, home = repo_root.resolve(), home.resolve()
    tree = home / 'knowledge'
    destination = tree / 'repos' / account.slug_repo_label(label)
    plan = MigrationPlan(repo_root / 'kb', destination)
    try:
        if not tree.is_dir():
            raise ValueError('home knowledge tree does not exist')
        if Path(_git(tree, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != tree:
            raise ValueError('knowledge/ must be its own Git checkout')
        if _git(tree, 'status', '--porcelain', '--untracked-files=all'):
            plan.conflicts.append('home has uncommitted changes under knowledge/')
        if (home / '.git').exists() and _git(
                home, 'status', '--porcelain', '--untracked-files=all', '--', 'knowledge'):
            plan.conflicts.append('home has uncommitted changes under knowledge/')
        if destination.exists() or destination.is_symlink():
            plan.conflicts.append(f'destination exists: {destination}')
        if destination.parent.is_symlink():
            raise ValueError('knowledge/repos must not be a symlink')
        if Path(_git(repo_root, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != repo_root:
            raise ValueError('project path must be the root of a Git checkout')
        if plan.source.is_symlink():
            raise ValueError('source kb/ is already a mount')
        plan.source_commit = _git(repo_root, 'rev-parse', 'HEAD').decode().strip()
        if _git(repo_root, 'status', '--porcelain', '--untracked-files=all', '--', 'kb'):
            plan.conflicts.append('source kb/ has uncommitted changes; commit them first')
        remote_name = gitops.default_remote(repo_root)
        remote = gitops.remote_url(repo_root, remote_name) if remote_name else ''
        entries = _git(repo_root, 'ls-tree', '-rz', plan.source_commit, '--', 'kb/')
        for entry in entries.split(b'\0'):
            if not entry:
                continue
            meta, raw_path = entry.split(b'\t', 1)
            mode, kind, oid = meta.split()
            name = raw_path.decode('utf-8').removeprefix('kb/')
            if mode not in (b'100644', b'100755') or kind != b'blob':
                raise ValueError(f'unsupported source entry (symlink or submodule): kb/{name}')
            body = _git(repo_root, 'cat-file', 'blob', oid.decode())
            if name.lower().endswith('.md'):
                rewritten, changes = _rewrite(body.decode('utf-8'), name, remote or '', plan.source_commit)
                body = rewritten.encode('utf-8')
                plan.rewrites.extend(changes)
            plan.files[name] = body
            plan.modes[name] = int(mode, 8) & 0o777
        if not plan.files:
            plan.conflicts.append('project has no committed files under kb/')
    except (ValueError, OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        plan.conflicts.append(str(exc))
    return plan


def migrate(repo_root: Path, home: Path, label: str, *, apply: bool = False) -> MigrationPlan:
    if not apply:
        return plan_migration(repo_root, home, label)
    # Refuse before even opening the lock if prerequisites fail. Re-plan
    # inside it: a capture or another migration may have changed the tree.
    plan = plan_migration(repo_root, home, label)
    if plan.conflicts:
        return plan
    with gitops.file_lock(home / knowledge.CAPTURE_LOCK_FILE, 10) as held:
        if not held:
            plan.conflicts.append('knowledge capture lock is busy; retry later')
            return plan
        plan = plan_migration(repo_root, home, label)
        if plan.conflicts:
            return plan
        try:
            plan.destination.mkdir(parents=True, exist_ok=False)
            for name, body in plan.files.items():
                path = plan.destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                path.chmod(plan.modes[name])
            tree = home.resolve() / 'knowledge'
            scope = plan.destination.relative_to(tree).as_posix()
            _git(tree, 'add', '--', scope)
            _git(tree, 'commit', '--only', '-m',
                 f'Migrate {label} kb from source commit {plan.source_commit}\n\n'
                 f'Copied from {plan.source}; source files retained.\n'
                 'External project links are pinned to that source commit.',
                 '--', scope, commit=True)
            plan.commit = _git(tree, 'rev-parse', 'HEAD').decode().strip()
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            plan.conflicts.append(f'apply failed: {exc}; inspect {plan.destination} before retrying')
        return plan
