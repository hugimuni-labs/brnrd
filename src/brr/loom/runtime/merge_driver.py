"""``loom-readme``: merge a README by a body, or stop.

Git runs ``python -m brr.loom.runtime merge-driver %O %A %B %P``. The body
is ``LOOM_MERGE_BODY`` (``fake`` or ``claude-haiku``). ``fake`` keeps ours
and appends theirs' lines that ours does not already have. The result is
written to ``%A`` only when it passes step 2's README parser. Anything else
exits 1, and git stops the rebase.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from brr.loom.runtime.selfrepo import ReadmeError, git, parse_readme


def install_merge_driver(repo: Path) -> None:
    """Point ``merge.loom-readme`` at this tree's interpreter.

    The command carries ``PYTHONPATH`` for the tree that contains this
    file, so a room merges with the code it was built from. An installed
    copy resolves the same way: the path is that copy's parent of ``brr``.
    """
    root = Path(__file__).resolve().parents[3]
    command = (
        f"PYTHONPATH={shlex.quote(os.fspath(root))} "
        f"{shlex.quote(sys.executable)} -m brr.loom.runtime merge-driver "
        "%O %A %B %P"
    )
    git(repo, "config", "merge.loom-readme.driver", command)


def fake_merge(ours: str, theirs: str) -> str:
    """Ours, then every line of theirs that is not already in ours."""
    ours_lines = ours.splitlines()
    seen = set(ours_lines)
    extra = [line for line in theirs.splitlines() if line not in seen]
    text = "\n".join(ours_lines + extra)
    return text + "\n"


def _strip_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
    body = "\n".join(lines).rstrip()
    return body + "\n" if body else ""


def haiku_merge(ours: str, theirs: str, path: str) -> str | None:
    prompt = (
        "Merge two versions of one file. Keep a valid README frontmatter "
        "(id, status, tense) and a single heading line that starts with '# '. "
        "Keep every unique body line from both sides. "
        "Output only the file, no explanation.\n\n"
        f"PATH {path}\n\nOURS\n{ours}\nTHEIRS\n{theirs}"
    )
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", "haiku",
             "--dangerously-skip-permissions", "--", prompt],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return _strip_fence(proc.stdout)


def acceptable(text: str, path: str) -> bool:
    """Step 2's parser, reused. ``README.md`` must also match its folder."""
    try:
        data = parse_readme(text)
    except ReadmeError:
        return False
    if data.get("id") != Path(path).parent.name:
        return False
    return True


def merge(base: str, ours: str, theirs: str, path: str) -> int:
    """Write the merge into ``ours`` (git's ``%A``). Exit 1 leaves git stopped."""
    del base  # the fake body is defined on ours and theirs; the base is context
    try:
        ours_text = Path(ours).read_text(encoding="utf-8")
        theirs_text = Path(theirs).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 1
    body = os.environ.get("LOOM_MERGE_BODY") or "fake"
    if body == "fake":
        result = fake_merge(ours_text, theirs_text)
    elif body == "claude-haiku":
        result = haiku_merge(ours_text, theirs_text, path)
        if result is None:
            return 1
    else:
        return 1
    if not acceptable(result, path):
        return 1
    try:
        Path(ours).write_text(result, encoding="utf-8")
    except OSError:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "merge-driver":
        args = args[1:]
    if len(args) != 4:
        print("usage: python -m brr.loom.runtime merge-driver %O %A %B %P", file=sys.stderr)
        return 1
    return merge(*args)
