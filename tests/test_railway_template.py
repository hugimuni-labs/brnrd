"""deploy/railway must stay true to what the daemon reads and runs."""
import json
import os
import re
import subprocess

import pytest
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


def _released(tmp_path):
    """A repository with releases v0.6.9, v0.7.0, v0.7.1 and a newer pre-release; main past them."""
    import subprocess
    repo = tmp_path / "brnrd"
    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                       capture_output=True)
    repo.mkdir()
    git("init", "-q", "-b", "main")
    for tag in ("v0.6.9", "v0.7.0", "v0.7.1", "v0.8.0rc1", "main-only"):
        (repo / "version").write_text(tag)
        git("add", "-A")
        git("commit", "-qm", tag)
        if tag != "main-only":
            git("tag", "-a", "-m", tag, tag)
    return repo


@pytest.mark.parametrize(("version", "tag"), [("", "v0.7.1"), ("0.7.0", "v0.7.0"), ("v0.6.9", "v0.6.9")])
def test_the_image_installs_a_checkout_of_the_release_tag(tmp_path, version, tag):
    import subprocess
    repo, dest = _released(tmp_path), tmp_path / "checkout"
    done = subprocess.run(["sh", str(ROOT / "deploy/railway/checkout.sh"), str(dest), version],
                          env={**os.environ, "BRNRD_SOURCE_URL": str(repo)}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (dest / "version").read_text() == tag  # the newest release, never the pre-release or main
    assert (dest / ".git").is_dir()  # a git checkout, not a copied tree (root-owned in the image: pinned there)
    assert f"brnrd checkout: {tag} " in done.stdout


def test_the_image_installs_from_the_checkout_and_never_from_pypi():
    dockerfile = (ROOT / "deploy/railway/Dockerfile").read_text()
    assert 'sh /usr/local/bin/brnrd-checkout /opt/brnrd/checkout "$BRNRD_VERSION"' in dockerfile
    assert "pip install --no-cache-dir -e /opt/brnrd/checkout" in dockerfile
    assert '"brnrd${BRNRD_VERSION' not in dockerfile
