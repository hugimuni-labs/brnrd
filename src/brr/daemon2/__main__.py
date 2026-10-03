"""Run one replacement-daemon dispatch without switching the installed daemon."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runtime import Daemon2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--runner")
    parser.add_argument("--runner-cmd", type=Path)
    args = parser.parse_args(argv)
    config = {"runner_cmd": [str(args.runner_cmd)]} if args.runner_cmd else None
    result = Daemon2(args.repo, args.home, runtime_dir=args.runtime_dir,
                     runner_name=args.runner, runner_config=config).once()
    print(json.dumps(result.__dict__ if result else None, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
