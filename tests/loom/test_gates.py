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


def test_a_body_starts_with_a_token_good_for_most_of_an_hour(tmp_path, mint, shim):
    """The GH_TOKEN a body exports for shells that bypass its gh shim is resolved
    once; a cached token with 20 min left would die mid-body, so it is minted again."""
    import time
    from brr.loom.runtime.loom import _forge_env
    root = tmp_path / "home"
    gate = connected(root, mint.url)
    (root / "loom/config.toml").write_text(f'forge_token = "{spec(root)}"\n')
    room = root / "rooms" / "s-1"
    room.mkdir(parents=True)
    (gate / gates.TOKEN_FILE).write_text(json.dumps({"token": "ghs_old", "expires": time.time() + 1200}))
    env = dict(__import__("os").environ)
    _forge_env(room, env)
    assert env["GH_TOKEN"] == "ghs_1"
    # The shim and publish keep the cache's own margin: no second mint.
    assert resolve_secret(spec(root)) == "ghs_1" and mint.minted == ["ghs_1"]


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


def test_migrate_checks_the_copy_by_minting_through_it(tmp_path, mint, shim):
    root, old = legacy_home(tmp_path)
    (old / "gates/cloud.json").write_text(json.dumps({"brnrd_url": mint.url}))
    gates.migrate(root)
    assert mint.minted == ["ghs_1"]
    assert load_config(root).forge_token == spec(root)
    assert resolve_secret(spec(root)) == "ghs_1"


def test_a_copy_that_cannot_mint_leaves_the_config_as_it_was(tmp_path):
    root, old = legacy_home(tmp_path)
    before = (root / "loom/config.toml").read_text()

    class Gone(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Gone)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        (old / "gates/cloud.json").write_text(json.dumps({"brnrd_url": f"http://127.0.0.1:{server.server_port}"}))
        with pytest.raises(Exception):
            gates.migrate(root)
    finally:
        server.shutdown()
    assert (root / "loom/config.toml").read_text() == before


def test_relay_state_inside_the_home_or_a_plain_forge_token_is_not_legacy(tmp_path):
    root = tmp_path / "home"
    connected(root, "https://brnrd.test")
    (root / "loom/config.toml").write_text(
        f'relay_state = "{root}/loom"\nforge_token = "command: gh auth token"\n')
    assert not gates.legacy(root, load_config(root))


def signed_in_gh(tmp_path: Path, monkeypatch, token: str = "gho_person") -> None:
    """A gh on PATH that answers ``gh auth token`` like a signed-in one."""
    bin_dir = tmp_path / "gh-bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\n[ "$1 $2" = "auth token" ] && echo {token} && exit 0\nexit 1\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")


def fresh(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    (root / "loom").mkdir(parents=True)
    (root / "loom/config.toml").write_text('name = "brnrd"\n# forge_token = ...\n')
    return root


def test_no_cloud_sets_the_persons_gh_and_publish_pushes_with_it(tmp_path, monkeypatch):
    root = fresh(tmp_path)
    signed_in_gh(tmp_path, monkeypatch)
    assert gates.converge(root) == {"forge_token": gates.GH, "forge_token_was": None}
    config = load_config(root)
    assert config.forge_token == gates.GH and config.name == "brnrd"
    _args, env, lane, _ = remote_command("https://github.com/o/r.git", ["push"], config.forge_token)
    assert lane == "token" and env["BRNRD_FORGE_TOKEN"] == "gho_person"
    assert gates.converge(root) == {}


def test_gh_not_signed_in_leaves_forge_token_unset(tmp_path):
    root = fresh(tmp_path)
    assert gates.converge(root) == {}
    assert load_config(root).forge_token is None


def test_a_gate_moves_the_persons_gh_to_the_app_and_publish_mints(tmp_path, monkeypatch, mint, shim):
    root = fresh(tmp_path)
    signed_in_gh(tmp_path, monkeypatch)
    gates.converge(root)
    connected(root, mint.url)
    assert gates.converge(root) == {"forge_token": spec(root), "forge_token_was": gates.GH}
    _args, env, _lane, _ = remote_command("https://github.com/o/r.git", ["push"],
                                          load_config(root).forge_token)
    assert env["BRNRD_FORGE_TOKEN"] == "ghs_1"


def test_the_app_never_falls_back_to_the_persons_gh_and_a_persons_choice_stays(tmp_path, monkeypatch):
    signed_in_gh(tmp_path, monkeypatch)
    root = fresh(tmp_path)
    # The verb with its gate gone stays the verb: it fails loudly as the App (fork A).
    (root / "loom/config.toml").write_text(f'forge_token = {json.dumps(spec(root))}\n')
    assert gates.converge(root) == {}
    # A person switches identity by changing the getter; converge leaves it.
    for chosen in ("env: MY_TOKEN", "file: /tmp/t", "command: op read op://x/gh"):
        (root / "loom/config.toml").write_text(f'forge_token = {json.dumps(chosen)}\n')
        assert gates.converge(root) == {}
        assert load_config(root).forge_token == chosen
    connected(root, "http://127.0.0.1:1")
    assert gates.converge(root) == {}
    assert load_config(root).forge_token == chosen


def test_install_writes_and_names_the_identity_and_a_dry_run_writes_nothing(tmp_path, monkeypatch):
    from brr.loom.runtime import service
    signed_in_gh(tmp_path, monkeypatch)
    root = fresh(tmp_path)
    before = (root / "loom/config.toml").read_text()
    assert service._settle_forge(root, write=False) == f"{gates.GH} (your own gh login, set now)"
    assert (root / "loom/config.toml").read_text() == before
    assert service._settle_forge(root) == f"{gates.GH} (your own gh login, set now)"
    assert service._settle_forge(root) == f"{gates.GH} (your own gh login)"


def test_loom_start_converges_and_records_it(tmp_path, monkeypatch):
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import read_facts
    from brr.loom.runtime.loom import _converge_forge_token
    signed_in_gh(tmp_path, monkeypatch)
    root = fresh(tmp_path)
    _converge_forge_token(Home(root))
    assert load_config(root).forge_token == gates.GH
    moved = [f for f in read_facts(Home(root)) if f.kind == "home.migrated"]
    assert [f.data["want"] for f in moved] == ["forge_token"]
