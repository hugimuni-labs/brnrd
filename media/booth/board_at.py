"""Serve the local board as it was at a moment: a copy of the home with the ledger and the tapes cut at --at.

The board has no time travel (it shows the newest letters of each thread), so a shot of a past letter is made by
copying the home with every fact and tape line after the moment dropped, and serving that copy on its own port
with the same board code the loom runs. Nothing is rendered by hand: what the capture sees is the real board on
the real ledger, as it read then.

    ~/.brnrd/current/bin/python board_at.py --at 2026-11-05T22:26:20Z --port 4790
    (Ctrl-C to stop; the copy stays under --work for the next run at the same moment)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HOME = Path(os.environ.get("BRNRD_HOME", "~/.local/state/brnrd/accounts/acc_bdda426da378d4f0c3cad2eb/loom-home")).expanduser()


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def cut_lines(src: Path, dst: Path, keep) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open() as fin, dst.open("w") as fout:
        for line in fin:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if keep(row):
                fout.write(line)


def cut_home(home: Path, work: Path, at: datetime) -> Path:
    """The home as it was at ``at``: ledger facts by their ISO ``at``, tapes by their epoch ``at``; the self,
    the loom's config and the blobs are APFS clones (``cp -c``), the repos a symlink (the board only reads them)."""
    out = work / at.strftime("%Y%m%dT%H%M%SZ")
    if (out / ".done").exists():
        return out
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for part in ("self", "loom", "blobs"):
        if (home / part).exists():
            subprocess.run(["cp", "-cR", str(home / part), str(out / part)], check=True)
    if (home / "repos").exists():
        (out / "repos").symlink_to(home / "repos")
    for name in ("loop.lock", "board"):  # the live loom's lock and the board's own notes are not the moment's
        target = out / "loom" / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    stamp = at.timestamp()
    for src in (home / "ledger").rglob("*.jsonl"):
        cut_lines(src, out / src.relative_to(home), lambda row: "at" not in row or moment(row["at"]) <= at)
    for src in (home / "rooms").glob("*/port/tools.jsonl"):
        cut_lines(src, out / src.relative_to(home), lambda row: float(row.get("at", 0)) <= stamp)
    (out / ".done").touch()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--at", required=True, help="the moment, ISO (2026-11-05T22:26:20Z)")
    ap.add_argument("--port", type=int, default=4790)
    ap.add_argument("--work", default=os.environ.get("TMPDIR", "/tmp") + "/board-at")
    ap.add_argument("--home", default=str(HOME))
    args = ap.parse_args()
    root = cut_home(Path(args.home), Path(args.work), moment(args.at))
    from brr.loom.runtime.board import Parts, serve
    from brr.loom.runtime.config import load_config
    from brr.loom.runtime.home import Home
    import threading
    home = Home(root)
    parts = Parts(home, lambda: load_config(root))
    threading.Thread(target=parts.run, name="board", daemon=True).start()
    serve(parts, args.port)
    print(f"board at {args.at} on http://127.0.0.1:{args.port}/ from {root}", flush=True)
    signal.sigwait({signal.SIGINT, signal.SIGTERM})
    parts.stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
