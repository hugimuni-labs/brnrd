#!/usr/bin/env python3
"""Contender 1: plain `claude` CLI, auto-memory on (the platform default).

Each session is a genuinely fresh process (`claude -p`, no --continue/--resume)
in the same project directory, so any recall in session 2/3 must come from
whatever the CLI persisted on disk between sessions (CLAUDE.md / auto-memory),
not from conversation state we kept alive ourselves.

Usage: claude_plain.py <workdir> <transcript-dir>
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prompts import SESSION_1, SESSION_2, SESSION_3  # noqa: E402

# Same GIT_DIR/GIT_WORK_TREE trap as make_scratch_repo.py: scrub before
# spawning claude, since claude's own tool calls (incl. git) must land in
# the scratch workdir, never in whatever worktree this script itself runs from.
_CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def run_session(workdir: Path, prompt: str, label: str, transcript_dir: Path) -> str:
    cmd = [
        "claude", "-p", prompt,
        "--dangerously-skip-permissions",  # scratch dir, no real secrets/network — see report caveats
        "--output-format", "text",
    ]
    t0 = time.time()
    result = subprocess.run(
        cmd, cwd=workdir, env=_CLEAN_ENV,
        capture_output=True, text=True, timeout=600,
    )
    dt = time.time() - t0
    transcript_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_dir / f"{label}.txt"
    out_path.write_text(
        f"$ {' '.join(cmd)}\ncwd: {workdir}\nelapsed: {dt:.1f}s\nreturncode: {result.returncode}\n"
        f"\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
    )
    print(f"[{label}] {dt:.1f}s rc={result.returncode} -> {out_path}")
    return result.stdout


def main() -> None:
    workdir = Path(sys.argv[1]).resolve()
    transcript_dir = Path(sys.argv[2]).resolve()

    print("=== session 1 (fresh process) ===")
    run_session(workdir, SESSION_1, "session-1", transcript_dir)

    print("=== session 2 (fresh process, simulated restart) ===")
    run_session(workdir, SESSION_2, "session-2", transcript_dir)

    print("=== session 3 (fresh process, 'next day') ===")
    run_session(workdir, SESSION_3, "session-3", transcript_dir)

    # Evidence of what, if anything, the CLI persisted between sessions.
    print("=== on-disk memory artifacts after the chain ===")
    for candidate in [
        workdir / "CLAUDE.md",
        Path.home() / ".claude" / "projects",
    ]:
        print(f"  {candidate}: {'exists' if candidate.exists() else 'absent'}")


if __name__ == "__main__":
    main()
