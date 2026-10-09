"""Puts this directory on the path so the suite can share ``_step``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
