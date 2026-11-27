"""The runtime installs reviewed files and presents only their summary at wake."""

import json

import pytest

from brr.loom.runtime import kits
from brr.loom.runtime.__main__ import main as cli
from brr.loom.runtime.home import Home
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import git

from test_project_repos import _home, _log, _room, _seat, _started, _tick

SKILL = "skills/prospecting/SKILL.md"
BODY = "Read this only when the skill is needed."


@pytest.fixture
def reviewed_source(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q")
    git(upstream, "config", "user.name", "Test")
    git(upstream, "config", "user.email", "kit@example.org")
    (upstream / SKILL).parent.mkdir(parents=True)
    (upstream / SKILL).write_text(BODY)
    (upstream / "LICENSE").write_text("Licence notice")
    (upstream / "not-selected.sh").write_text("exit 99")
    (upstream / "skills/prospecting/link.md").symlink_to("SKILL.md")
    git(upstream, "add", ".")
    git(upstream, "commit", "-qm", "Initial content")
    commit = git(upstream, "rev-parse", "HEAD").stdout.strip()

    def fetch(repo, url, pin):
        assert url == "https://github.com/example/growth.git"
        assert pin == commit
        git(repo, "init", "--bare", "-q")
        git(repo, "fetch", str(upstream), pin)

    monkeypatch.setattr(kits, "_fetch", fetch)
    return {
        "version": 1,
        "id": "growth",
        "description": "Find real projects",
        "source": {"url": "https://github.com/example/growth.git", "commit": commit, "license": "MIT"},
        "skills": [{"id": "prospecting", "files": [SKILL]}],
        "hands": [],
        "mcp_servers": [{"id": "example", "command": "/unavailable/server"}],
        "requirements": {},
        "script_count": 0,
        "review": {"by": "Ada", "at": "2026-11-27T19:33:00Z", "notes": "Read the entry"},
    }


def test_import_lists_selected_files_and_wake_shows_only_summary(tmp_path, reviewed_source, capsys):
    root = _home(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(reviewed_source))
    command = ["kit", "import", str(manifest), "--home", str(root)]
    assert cli(command) == 0
    target = root / "kits" / "growth" / reviewed_source["source"]["commit"]
    assert sorted(str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()) == [
        "LICENSE", "manifest.json", SKILL,
    ]
    assert (target / SKILL).read_text() == BODY
    assert json.loads((target / "manifest.json").read_text()) == reviewed_source
    assert cli(command) == 0
    capsys.readouterr()
    assert cli(["kit", "list", "--home", str(root)]) == 0
    assert str(target / SKILL) in capsys.readouterr().out

    _seat(root, "work", [])
    home = Home(root)
    _tick(home, {}, Router(home))
    assert _started(root), _log(root)
    wake = (_room(root) / "port/wake.md").read_text()
    assert "kit growth @" in wake
    assert "Find real projects" in wake
    assert BODY not in wake
    assert not (root / "self/kits").exists()


@pytest.mark.parametrize("file", ["../outside.md", "skills/prospecting/link.md"])
def test_import_rejects_unsafe_files_without_exposing_a_kit(tmp_path, reviewed_source, file):
    root = tmp_path / "home"
    reviewed_source["skills"][0]["files"].append(file)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(reviewed_source))
    assert cli(["kit", "import", str(manifest), "--home", str(root)]) == 1
    assert cli(["kit", "list", "--home", str(root)]) == 0
    assert not list(root.glob("kits/*/*/manifest.json"))
