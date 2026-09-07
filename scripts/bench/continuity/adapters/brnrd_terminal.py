#!/usr/bin/env python3
"""Contender 3: brnrd, npm package, terminal path (`brnrd init`-free: bare `brnrd run`).

No daemon, no account pairing — the docs' own "quick sanity check" path
(`brnrd run "<instruction>"`), run three times as genuinely fresh processes.
`BRNRD_HOME` is pinned to a scratch dir so this never touches the real
account's dominion; `brnrd_bin` should point at a `node_modules/.bin/brnrd`
installed via `npm install brnrd` (not `-g`) into its own scratch prefix.

Usage: brnrd_terminal.py <workdir> <transcript-dir> <brnrd-bin> <brnrd-home>
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prompts import SESSION_1, SESSION_2, SESSION_3  # noqa: E402

_CLEAN_ENV_BASE = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def run_session(workdir: Path, prompt: str, label: str, transcript_dir: Path,
                 brnrd_bin: Path, brnrd_home: Path) -> str:
    env = dict(_CLEAN_ENV_BASE)
    env["BRNRD_HOME"] = str(brnrd_home)
    cmd = [str(brnrd_bin), "run", prompt]
    t0 = time.time()
    result = subprocess.run(
        cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=600,
    )
    dt = time.time() - t0
    transcript_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_dir / f"{label}.txt"
    out_path.write_text(
        f"$ {' '.join(cmd)}\ncwd: {workdir}\nBRNRD_HOME: {brnrd_home}\n"
        f"elapsed: {dt:.1f}s\nreturncode: {result.returncode}\n"
        f"\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
    )
    print(f"[{label}] {dt:.1f}s rc={result.returncode} -> {out_path}")
    return result.stdout


def main() -> None:
    workdir = Path(sys.argv[1]).resolve()
    transcript_dir = Path(sys.argv[2]).resolve()
    brnrd_bin = Path(sys.argv[3]).resolve()
    brnrd_home = Path(sys.argv[4]).resolve()

    print("=== session 1 (fresh process; first run also provisions brnrd's venv) ===")
    run_session(workdir, SESSION_1, "session-1", transcript_dir, brnrd_bin, brnrd_home)

    print("=== session 2 (fresh process, simulated restart) ===")
    run_session(workdir, SESSION_2, "session-2", transcript_dir, brnrd_bin, brnrd_home)

    print("=== session 3 (fresh process, 'next day') ===")
    run_session(workdir, SESSION_3, "session-3", transcript_dir, brnrd_bin, brnrd_home)


if __name__ == "__main__":
    main()
