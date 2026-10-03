"""Run one replacement-daemon dispatch without switching the installed daemon."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runtime import Daemon2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--runner")
    parser.add_argument("--runner-cmd", type=Path)
    parser.add_argument("--role", choices=("any", "resident", "strand"),
                        default="any")
    args = parser.parse_args(argv)
    config = {"runner_cmd": [str(args.runner_cmd)]} if args.runner_cmd else None
    daemon = Daemon2(args.repo, args.home, runtime_dir=args.runtime_dir,
                     runner_name=args.runner, runner_config=config)
    if args.once:
        result = daemon.once(role=args.role)
        print(json.dumps(result.__dict__ if result else None, default=str))
    else:
        import signal as _signal

        def _handler(sig: int, frame: object) -> None:  # noqa: ARG001
            daemon.stop()

        _signal.signal(_signal.SIGTERM, _handler)
        _signal.signal(_signal.SIGINT, _handler)
        results = daemon.serve(role=args.role)
        print(json.dumps([r.__dict__ for r in results], default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
