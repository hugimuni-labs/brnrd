"""A body running the suite can never touch its real home (test-isolation).

Sasha's resident ran the root suite from its room and lost its loom's relay
token: ``tests/loom/conftest.py`` stripped the body's environment, the root
``tests/conftest.py`` did not. This runs a probe through a pytest launched
the way a body launches it, with every home-resolving input pointed at a
fake real home, and proves that home keeps its gate files.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES = ("loom/gates/cloud.json", "loom/gates/cloud.token",
         "account/gates/cloud.json", "account/gates/cloud.token")


def _real_home(user: Path) -> Path:
    """A loom home at ``~/brnrd``, connected, with one room."""
    home = user / "brnrd"
    (home / "self" / "core" / "loom").mkdir(parents=True)
    (home / "self" / "core" / "loom" / "wake").write_text("", encoding="utf-8")
    (home / "loom" / "config.toml").parent.mkdir(parents=True)
    (home / "loom" / "config.toml").write_text("", encoding="utf-8")
    (home / "loom" / "install-id").write_text("real\n", encoding="utf-8")
    for rel in GATES:
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"token": "real", "brnrd_url": "https://brnrd.invalid", '
                        '"account_id": "acc_real"}\n', encoding="utf-8")
    (home / "rooms" / "s-real" / "port" / "bin").mkdir(parents=True)
    return home


def test_a_root_level_test_cannot_reach_the_hosting_body_s_home(tmp_path):
    user = tmp_path / "user"
    home = _real_home(user)
    room = home / "rooms" / "s-real"
    # PYTEST_SHARD: CI's shard would split the probe's three tests too.
    env = {
        **{k: v for k, v in os.environ.items() if k != "PYTEST_SHARD"},
        "HOME": str(user),
        "XDG_STATE_HOME": str(user / ".local" / "state"),
        "BRNRD_HOME": str(home),
        "BRNRD_ROOM": str(room),
        "BRNRD_THREAD": "real",
        "BRR_HOME": str(home),
        "LOOM_BUDGET_BYTES": "32000",
        "PATH": f"{room / 'port' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "PROBE_REAL_HOME": str(home),
        "PYTHONPATH": str(ROOT / "src"),
    }
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         str(ROOT / "tests" / "hosted" / "probe_real_home.py")],
        cwd=ROOT, env=env, capture_output=True, text=True)
    for rel in GATES:
        assert (home / rel).is_file(), f"{rel} was deleted"
    assert out.returncode == 0, out.stdout + out.stderr
    assert "3 passed" in out.stdout, out.stdout
