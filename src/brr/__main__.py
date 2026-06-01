"""Entry point for `python -m brr`.

This script delegates to the CLI defined in `brr.cli`.  It allows you to
invoke brr directly via `python -m brr`.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())