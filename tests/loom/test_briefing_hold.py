"""A withheld act is a ledger note. The sentence names flags the send parser accepts."""

import os
import subprocess
import sys
from pathlib import Path

HOLD = 'brnrd runtime send --re <id> --to thread:<thread> --note "<why>"'
SRC = Path(__file__).resolve().parents[2] / "src"


def test_a_withheld_act_is_a_ledger_note_and_not_a_self_commit():
    from brr.loom.runtime import briefing

    assert Path(briefing.__file__).resolve() == (SRC / "brr/loom/runtime/briefing.py").resolve()
    bullet = next(line for line in briefing.BRIEFING.splitlines() if HOLD in line)
    assert bullet.startswith("- When a grant, a source or a fact is missing")
    assert "Never a self commit." in bullet
    assert "send-self" not in bullet
    # One bullet, no new section. The briefing already names `## Held` in an older line.
    assert not bullet.startswith("##")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # python -m outside pytest follows the editable install. Pin the worktree.
    proc = subprocess.run(
        [sys.executable, "-m", "brr.loom.runtime", "send", "--help"],
        capture_output=True, text=True, env=env, cwd=SRC.parents[0],
    )
    assert proc.returncode == 0, proc.stderr
    for flag in ("--re", "--to", "--note"):
        assert flag in proc.stdout
    # --note is the whole message. A body that adds a body is refused.
    assert "body" in proc.stdout
