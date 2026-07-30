#!/usr/bin/env python3
"""Keep the legal export pack's hash pins true to the files they pin.

``docs/legal/export/SHA256SUMS`` is the pack's integrity claim: *these are the
exact bytes counsel reviewed*. It was written by hand on 2026-07-27 and nothing
recomputed it afterwards, so by 2026-07-30 six of its twenty entries — the DPA
and the Article 30 record among them — pinned bytes that no longer existed. A
manifest that drifts silently is worse than no manifest: it converts "unknown"
into a signed-looking "verified".

The same hashes also appear as a human-readable table in
``document-manifest.md``. That duplication is deliberate (counsel reads the
table, a machine reads the sums file) and therefore has to be *checked*, not
trusted — one fact stored twice is repaired once.

    python scripts/legal_manifest.py            # check, exit 1 on drift
    python scripts/legal_manifest.py --write    # recompute both copies

``tests/test_legal_export_manifest.py`` runs the check, so drift fails the
gate rather than waiting for someone to re-read the pack.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORT_DIR = REPO_ROOT / "docs" / "legal" / "export"
SUMS = EXPORT_DIR / "SHA256SUMS"
MANIFEST = EXPORT_DIR / "document-manifest.md"

_HASH_CELL = re.compile(r"`([0-9a-f]{64})`")


def listed_paths() -> list[str]:
    """The pinned set, defined by the sums file itself."""
    out = []
    for line in SUMS.read_text().splitlines():
        if line.strip():
            out.append(line.split(None, 1)[1].strip())
    return out


def digest(rel: str) -> str:
    return hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()


def unpinned_legal_docs() -> list[str]:
    """Every ``docs/legal/*.md`` outside the pack must be pinned by it.

    The pinned set is otherwise a hand-written list, and a hand-written list
    meets the member nobody added to it. This is the one part of the set with
    a structural definition, so it gets a structural check.
    """
    listed = set(listed_paths())
    found = []
    for path in sorted((REPO_ROOT / "docs" / "legal").glob("*.md")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel not in listed:
            found.append(rel)
    return found


def check() -> list[str]:
    """Return human-readable problems; empty means the pack is honest."""
    problems: list[str] = []
    for line in SUMS.read_text().splitlines():
        if not line.strip():
            continue
        pinned, rel = line.split(None, 1)
        rel = rel.strip()
        if not (REPO_ROOT / rel).exists():
            problems.append(f"{rel}: pinned but missing")
            continue
        if digest(rel) != pinned:
            problems.append(f"{rel}: pin {pinned[:12]}… != file {digest(rel)[:12]}…")

    by_path = {rel: digest(rel) for rel in listed_paths() if (REPO_ROOT / rel).exists()}
    for line in MANIFEST.read_text().splitlines():
        if not line.startswith("|"):
            continue
        cell = _HASH_CELL.search(line)
        if not cell:
            continue
        if cell.group(1) not in by_path.values():
            target = re.search(r"\[`([^`]+)`\]", line)
            name = target.group(1) if target else line[:60]
            problems.append(f"document-manifest.md: stale hash for {name}")

    for rel in unpinned_legal_docs():
        problems.append(f"{rel}: a legal document the manifest does not pin")
    return problems


def write() -> None:
    rows = [(digest(rel), rel) for rel in listed_paths() if (REPO_ROOT / rel).exists()]
    SUMS.write_text("".join(f"{h}  {rel}\n" for h, rel in rows))

    by_path = dict((rel, h) for h, rel in rows)
    out = []
    for line in MANIFEST.read_text().splitlines(keepends=True):
        if line.startswith("|") and _HASH_CELL.search(line):
            target = re.search(r"\[`([^`]+)`\]", line)
            if target and target.group(1) in by_path:
                line = _HASH_CELL.sub(f"`{by_path[target.group(1)]}`", line, count=1)
        elif line.startswith("- Date: "):
            line = f"- Date: {date.today().isoformat()}\n"
        elif line.startswith("  `") and len(line.strip("` \n")) == 40:
            line = f"  `{_head_sha()}`\n"
        out.append(line)
    MANIFEST.write_text("".join(out))


def _head_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="recompute the pins instead of checking them")
    args = parser.parse_args(argv)

    if args.write:
        write()
        print(f"rewrote {SUMS.relative_to(REPO_ROOT)} and {MANIFEST.relative_to(REPO_ROOT)}")
        return 0

    problems = check()
    for problem in problems:
        print(problem)
    if problems:
        print(f"\nFAIL — {len(problems)} problem(s). `python scripts/legal_manifest.py --write` after reviewing.")
        return 1
    print(f"OK — {len(listed_paths())} pinned files match, and the manifest table agrees.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
