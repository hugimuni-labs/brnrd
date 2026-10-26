"""Entry point for `python -m brr`.

This script delegates to the CLI defined in `brr.cli`.  It allows you to
invoke brr directly via `python -m brr`.
"""

import sys

if __name__ == "__main__":
    # ``brnrd runtime <verb>``: what the loom's launchers run through
    # ~/.brnrd/bin/brnrd, dispatched before the daemon CLI is imported.
    if sys.argv[1:2] == ["runtime"]:
        from .loom.runtime.__main__ import main as runtime

        sys.exit(runtime(sys.argv[2:]))
    from .cli import main

    sys.exit(main())