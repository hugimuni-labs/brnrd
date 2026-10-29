"""Run only by test_hosting_isolation.py, inside a body-shaped environment.

Not named ``test_*``: the suite never collects it on its own. Each test here
is a call a root-level test could make that resolves "the home"; the
environment the outer test built points every one of them at a fake real
home, and none may reach it.
"""

import os
import subprocess
import sys
from pathlib import Path

REAL = Path(os.environ["PROBE_REAL_HOME"]).resolve()


def test_disconnect_leaves_the_real_gate(tmp_path):
    """Resolve the home the ways a runtime verb does, then disconnect it."""
    from brr.gates import cloud
    from brr.loom.runtime import home

    try:
        resolved = home.resolve_home()[0]
    except SystemExit:
        resolved = tmp_path / "no-home"
    room = os.environ.get("BRNRD_ROOM")
    roomed = Path(room).parent.parent if room else tmp_path / "no-room"
    for root in (resolved, roomed):
        (root / "loom").mkdir(parents=True, exist_ok=True)
        cloud.disconnect(root / "loom")
    brr_dir = tmp_path / "repo" / ".brr"
    brr_dir.mkdir(parents=True)
    cloud.disconnect(brr_dir)


def test_no_resolver_names_the_real_home():
    from brr.loom.runtime import home, service

    try:
        resolved = home.resolve_home()[0]
    except SystemExit:
        resolved = None
    try:
        served = service._service_home(None)[0]
    except SystemExit:
        served = None
    assert REAL not in {resolved, served, *home.found_homes()}


def test_runtime_cli_sees_no_room():
    out = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "where"],
                         capture_output=True, text=True)
    assert str(REAL) not in out.stdout + out.stderr
