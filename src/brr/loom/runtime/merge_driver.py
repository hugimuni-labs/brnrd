"""``loom-readme``: merge a README by a body, or stop.

Git runs ``brnrd runtime merge-driver %O %A %B %P``. The body
is ``LOOM_MERGE_BODY`` (``fake`` or ``claude-haiku``). Frontmatter merges per key against the base. ``fake`` keeps our prose
and appends their lines that ours does not already have. The result is
written to ``%A`` only when it passes step 2's README parser. Anything else
exits 1, and git stops the rebase.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from brr.loom.runtime import entry
from brr.loom.runtime.selfrepo import ReadmeError, git, parse_readme


def driver_command() -> str:
    """``merge.loom-readme``'s command: the shim, with this tree on ``PYTHONPATH``.

    The path makes a room merge with the code it was built from; an installed
    copy resolves the same way (that copy's parent of ``brr``). A path that
    has since gone is skipped by Python, and the shim's brnrd merges instead.
    """
    root = Path(__file__).resolve().parents[3]
    return f"PYTHONPATH={shlex.quote(os.fspath(root))} {entry.command('merge-driver')} %O %A %B %P"


def install_merge_driver(repo: Path) -> None:
    git(repo, "config", "merge.loom-readme.driver", driver_command())


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
            ["claude", "-p", "--model", "haiku", "--", prompt],
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


def _parts(text: str) -> tuple[dict, dict[str, str], str]:
    data = parse_readme(text)
    lines = text.splitlines(keepends=True)
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    raw = {line.partition(":")[0].strip(): line.rstrip("\r\n")
           for line in lines[1:end] if line.strip()}
    return data, raw, "".join(lines[end + 1:])


def merge(base: str, ours: str, theirs: str, path: str) -> int:
    """Merge frontmatter against the base, then let the body merge the prose.

    During send_to_self's rebase, %A is main and %B is the room's commit.
    Conflicting status decisions therefore take %A; other conflicts stop.
    """
    try:
        try:
            base_data = _parts(Path(base).read_text(encoding="utf-8"))[0]
        except ReadmeError:
            base_data = {}  # both sides added the file: there is no base to read
        our_data, our_raw, our_body = _parts(Path(ours).read_text(encoding="utf-8"))
        their_data, their_raw, their_body = _parts(Path(theirs).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ReadmeError):
        return 1
    header = ["---"]
    missing = object()
    for key in dict.fromkeys([*our_raw, *their_raw]):
        before = base_data.get(key, missing)
        left, right = our_data.get(key, missing), their_data.get(key, missing)
        if left != before and right != before and left != right and key != "status":
            return 1
        source = their_raw if left == before and right != before else our_raw
        if key in source:
            header.append(source[key])
    front = "\n".join([*header, "---"]) + "\n"
    body = os.environ.get("LOOM_MERGE_BODY") or "fake"
    if body == "fake":
        result = front + fake_merge(our_body, their_body)
    elif body == "claude-haiku":
        result = haiku_merge(front + our_body, front + their_body, path)
        if result is None or not acceptable(result, path):
            return 1
        # A body chooses prose, never a different thread state.
        result = front + _parts(result)[2]
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
    if len(args) != 4:
        print("usage: python -m brr.loom.runtime merge-driver %O %A %B %P", file=sys.stderr)
        return 1
    return merge(*args)
