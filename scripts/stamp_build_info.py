"""The one writer of ``build_info.txt`` — every deploy surface calls this.

``src/brnrd/version_info.py`` reads a three-line stamp (identity, UTC build
time, identity *source*) and reports ``commit`` only when the third line says
``git``. There used to be two inline writers — a PaaS build hook and the
backend ``Dockerfile`` — and when the three-line honesty fix landed
(2026-07-30) only one copy was updated: every container image wrote a
two-line stamp and could never report its commit, discovered live on the
first Scaleway shadow deploy (``/v1/stats/version`` → ``commit: null``).
A fact stored twice is repaired once; now it is stored once.

Identity resolution, in order:

1. ``BRNRD_BUILD_COMMIT`` env — CI passes the exact sha as a build arg;
   the most reliable source and the only one a docker build has.
2. ``git rev-parse HEAD`` — a build tree that is a real clone.
3. Nothing — both lines empty; an absent answer is honest, a fabricated
   one is not.

The third line survives the removal of the PaaS tree-id rung (2026-07-31,
with ``.upsun/``) because it still separates *stamped with a real sha* from
*stamped with nothing*, and because images built before that removal are
still readable: an absent source reads as unknown, never as a guess.
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import subprocess


def resolve_identity() -> tuple[str, str]:
    """``(value, source)`` — source is ``git`` or ``""``."""
    commit = os.environ.get("BRNRD_BUILD_COMMIT", "").strip()
    if commit:
        return commit, "git"
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
    except OSError:  # no git binary in the build image
        sha = ""
    if sha:
        return sha, "git"
    return "", ""


def stamp(dest: pathlib.Path | None = None) -> pathlib.Path:
    if dest is None:
        import brnrd

        dest = pathlib.Path(brnrd.__file__).parent / "build_info.txt"
    value, source = resolve_identity()
    built_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    dest.write_text(f"{value}\n{built_at}\n{source}\n", encoding="utf-8")
    print("build_info:", dest, value or "<unknown>", built_at, source or "<unknown>")
    return dest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dest",
        type=pathlib.Path,
        default=None,
        help="target file; defaults to build_info.txt inside the installed brnrd package",
    )
    stamp(parser.parse_args().dest)
