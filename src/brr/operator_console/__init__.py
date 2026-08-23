"""Local operator console for brnrd.

This is intentionally a developer/operator surface, not the resident's coding
Shell and not a second daemon. The snapshot/model layer has no optional
dependencies; Textual is imported only when the interactive frontend starts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .model import collect_snapshot, resolve_repo_root
from .tui import run_tui


def _once(repo_root: Path, selected_run_id: str | None) -> int:
    """Small non-interactive probe, useful when the TUI extra is absent."""
    snapshot = collect_snapshot(repo_root, selected_run_id=selected_run_id)
    selected = snapshot.selected
    payload = {
        "repo": str(snapshot.repo_root),
        "brr_dir": str(snapshot.brr_dir),
        "daemon_pid": snapshot.daemon_pid,
        "selected_run_id": snapshot.selected_run_id,
        "runs": [
            {
                "run_id": run.run_id,
                "kind": run.kind,
                "name": run.name,
                "label": run.label,
                "repo_label": run.repo_label,
                "runner": {
                    "name": run.runner_name,
                    "shell": run.runner_shell,
                    "core": run.runner_core,
                    "class": run.runner_class,
                },
                "event_id": run.event_id,
                "stream": run.stream,
                "boundaries": len(run.boundaries),
                "boot_native": isinstance(run.boot.get("session_start"), dict),
                "pending": (
                    len(run.inbox_state)
                    if isinstance(run.inbox_state, list)
                    else None
                ),
            }
            for run in snapshot.runs
        ],
        "selected": (
            {
                "run_id": selected.run_id,
                "prompt_bytes": len(selected.prompt.encode("utf-8")),
                "boot": selected.boot,
                "boundaries": len(selected.boundaries),
                "portal_state": selected.portal_state,
                "inbox": selected.inbox_state,
                "card": selected.card,
            }
            if selected
            else None
        ),
        "console_key": snapshot.console_key,
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brnrd-console",
        description="Local operator console over a running brnrd daemon",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="repo to inspect (default: git repository containing cwd)",
    )
    parser.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="initial run id to select",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="print one JSON snapshot and exit; does not require Textual",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo_root = resolve_repo_root(args.repo)
        if args.once:
            return _once(repo_root, args.run_id)
        run_tui(repo_root, selected_run_id=args.run_id)
        return 0
    except (RuntimeError, OSError) as exc:
        raise SystemExit(f"[brnrd-console] {exc}") from None


__all__ = [
    "collect_snapshot",
    "main",
    "resolve_repo_root",
]
