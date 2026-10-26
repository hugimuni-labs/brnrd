"""The cloud gate lives in the home, and forge_token runs installed code (threads/forge-auth)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import brr
from brr.loom.runtime import entry, gates
from brr.loom.runtime.config import ConfigError, load_config, resolve_secret
from brr.loom.runtime.publish import remote_command


class Mint:
    """A brnrd.dev that mints one App token per POST and counts them."""

    def __init__(self, expires_in: float = 3600):
        self.minted, self.expires_in = [], expires_in
        mint = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                assert self.path == gates.MINT_PATH
                assert self.headers["Authorization"] == "Bearer acct"
                token = f"ghs_{len(mint.minted) + 1}"
                mint.minted.append(token)
                at = datetime.fromtimestamp(datetime.now().timestamp() + mint.expires_in, timezone.utc)
                body = json.dumps({"token": token, "expires_at": at.isoformat().replace("+00:00", "Z")})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def mint():
    found = Mint()
    yield found
    found.server.shutdown()


def connected(root: Path, url: str) -> Path:
    """What ``brnrd account connect`` leaves in the home: cloud.json and its token."""
    gate = root / "loom" / "gates"
    gate.mkdir(parents=True, exist_ok=True)
    (gate / "cloud.json").write_text(json.dumps({"brnrd_url": url}))
    (gate / "cloud.token").write_text("acct\n")
    (root / "loom" / "config.toml").touch()
    return gate


@pytest.fixture
def shim(monkeypatch):
    """The entry shim, running this checkout's brr from the suite's own interpreter."""
    entry.write_shim()
    entry.point(entry.prefix())
    monkeypatch.setenv("PYTHONPATH", str(Path(brr.__file__).parent.parent))


def spec(root: Path) -> str:
    return "command: " + entry.command("forge-token", "--home", str(root))


def test_publish_pushes_with_the_token_the_verb_mints_through_the_shim(tmp_path, mint, shim):
    root = tmp_path / "home"
    connected(root, mint.url)
    args, env, lane, _ = remote_command("https://github.com/o/r.git", ["push"], spec(root))
    assert lane == "token" and env["BRNRD_FORGE_TOKEN"] == "ghs_1"
    # A second push within the hour reuses the cached token: one mint, not two.
    remote_command("https://github.com/o/r.git", ["push"], spec(root))
    assert mint.minted == ["ghs_1"]
    cached = json.loads((root / "loom/gates/github.json").read_text())
    assert cached["token"] == "ghs_1" and (root / "loom/gates/github.json").stat().st_mode & 0o077 == 0


def test_a_token_near_its_end_is_minted_again(tmp_path):
    mint = Mint(expires_in=gates.MARGIN - 60)
    try:
        root = tmp_path / "home"
        connected(root, mint.url)
        state = gates.state_dir(root, load_config(root))
        assert gates.forge_token(state) == "ghs_1"
        assert gates.forge_token(state) == "ghs_2"
    finally:
        mint.server.shutdown()


def test_the_verb_fails_closed_without_a_gate(tmp_path, shim):
    root = tmp_path / "home"
    (root / "loom").mkdir(parents=True)
    (root / "loom" / "config.toml").touch()
    with pytest.raises(ConfigError):
        resolve_secret(spec(root))


def legacy_home(tmp_path: Path) -> tuple[Path, Path]:
    root, old = tmp_path / "loom-home", tmp_path / "home" / "account"
    (old / "gates").mkdir(parents=True)
    (old / "gates/cloud.json").write_text(json.dumps({"brnrd_url": "https://brnrd.test"}))
    (old / "gates/cloud.token").write_text("acct\n")
    (root / "loom" / "bin").mkdir(parents=True)
    (root / "loom/config.toml").write_text(
        'name = "brnrd"\n'
        f'forge_token = "command: {root}/loom/bin/forge-token"\n'
        f'relay_state = "{tmp_path}/home/account"\n'
        'guard = "off"\n')
    return root, old


def test_migrate_copies_the_gate_home_and_points_forge_token_at_the_verb(tmp_path):
    root, old = legacy_home(tmp_path)
    assert gates.legacy(root, load_config(root))
    seen = []
    data = gates.migrate(root, verify=seen.append)
    assert seen == [root / "loom"]
    assert data["gates_from"] == str(old / "gates") and sorted(data["copied"]) == ["cloud.json", "cloud.token"]
    # Copied, not moved: the old dir stays for one release.
    assert (old / "gates/cloud.token").is_file()
    config = load_config(root)
    assert config.relay_state is None and config.name == "brnrd" and config.guard == "off"
    assert config.forge_token == spec(root)
    assert gates.state_dir(root, config) == root / "loom"
    assert not gates.legacy(root, config)


def test_a_copy_the_cloud_refuses_leaves_the_config_as_it_was(tmp_path):
    root, _old = legacy_home(tmp_path)
    before = (root / "loom/config.toml").read_text()

    def refuse(_state):
        raise RuntimeError("401")

    with pytest.raises(RuntimeError):
        gates.migrate(root, verify=refuse)
    assert (root / "loom/config.toml").read_text() == before


def test_relay_state_inside_the_home_or_a_plain_forge_token_is_not_legacy(tmp_path):
    root = tmp_path / "home"
    connected(root, "https://brnrd.test")
    (root / "loom/config.toml").write_text(
        f'relay_state = "{root}/loom"\nforge_token = "command: gh auth token"\n')
    assert not gates.legacy(root, load_config(root))
