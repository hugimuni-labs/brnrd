"""deploy/railway must stay true to what the daemon reads and runs."""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "deploy" / "railway"


def _load(name):
    return json.loads((D / name).read_text())


def test_railway_json_points_at_the_daemon_image():
    cfg = _load("railway.json")
    assert cfg["build"]["builder"] == "DOCKERFILE"
    assert (ROOT / cfg["build"]["dockerfilePath"]).is_file()
    assert cfg["deploy"]["restartPolicyType"] in {"ALWAYS", "ON_FAILURE", "NEVER"}


def test_no_public_port_because_the_daemon_serves_none():
    assert _load("template.json")["service"]["public_networking"] is False
    assert "127.0.0.1" in (ROOT / "src/brr/loom/server.py").read_text()


def test_volume_matches_the_dockerfile_state_paths():
    tpl = _load("template.json")["service"]["volume_mount_path"]
    dockerfile = (D / "Dockerfile").read_text()
    for var in ("XDG_STATE_HOME", "HOME", "BRNRD_REPO_DIR"):
        assert re.search(rf"{var}={re.escape(tpl)}/", dockerfile), var
    assert "XDG_STATE_HOME" in (ROOT / "src/brr/account.py").read_text()


def test_declared_variables_are_read_by_the_daemon_or_start_script():
    src = "".join(p.read_text() for p in (ROOT / "src/brr").rglob("*.py"))
    start = (D / "start.sh").read_text()
    for key in _load("template.json")["variables"]:
        if key == "CLAUDE_CODE_OAUTH_TOKEN":  # read by the claude CLI itself
            continue
        assert key in src or key in start, key


def test_start_script_runs_the_real_verbs():
    start = (D / "start.sh").read_text()
    assert "exec brnrd up --foreground" in start
    assert "account connect --no-service" in start
    out = subprocess.run(["bash", "-n", str(D / "start.sh")], capture_output=True)
    assert out.returncode == 0, out.stderr
