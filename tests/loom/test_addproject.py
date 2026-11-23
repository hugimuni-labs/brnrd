"""``brnrd add`` and ``brnrd add .`` land the project on the home setup made, with no pairing."""

import subprocess

import pytest

from brr import cli
from brr.loom.runtime import library
from brr.loom.runtime.home import Home
from brr.loom.runtime.selfrepo import init_self


def _checkout(path, origin=None):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    init_self(root, person="ada")
    monkeypatch.setenv("BRNRD_HOME", str(root))
    return root


@pytest.mark.parametrize("argv", [["add"], ["add", "."]])
def test_bare_add_and_add_dot_enter_the_checkout_on_main(home, tmp_path, monkeypatch, capsys, argv):
    project = _checkout(tmp_path / "acme", "git@github.com:ada/acme-app.git")
    (project / "src").mkdir()
    monkeypatch.chdir(project / "src")  # from inside the checkout, not only its root
    cli.main(argv)
    found = {e.name: e for e in library.entries(Home(home))[0]}
    assert found["acme-app"].source == "git@github.com:ada/acme-app.git"
    assert found["acme-app"].whose == "ada"
    assert "Added acme-app" in capsys.readouterr().out
    # Twice is once: nothing new lands.
    tip = subprocess.run(["git", "-C", str(home / "self"), "rev-parse", "main"],
                         capture_output=True, text=True, check=True).stdout
    cli.main(argv)
    assert "already in brnrd's library" in capsys.readouterr().out
    assert subprocess.run(["git", "-C", str(home / "self"), "rev-parse", "main"],
                          capture_output=True, text=True, check=True).stdout == tip


def test_a_folder_without_origin_is_known_by_path(home, tmp_path, monkeypatch):
    project = _checkout(tmp_path / "notes")
    monkeypatch.chdir(project)
    cli.main(["add"])
    (entry,) = library.entries(Home(home))[0]
    assert (entry.name, entry.kind, entry.path) == ("notes", "known", str(project.resolve()))


def test_a_taken_name_gets_a_number(home, tmp_path, monkeypatch):
    for where, origin in (("a/site", "https://github.com/ada/site.git"),
                          ("b/site", "https://github.com/bob/site.git")):
        monkeypatch.chdir(_checkout(tmp_path / where, origin))
        cli.main(["add", "."])
    assert sorted(e.name for e in library.entries(Home(home))[0]) == ["site", "site-2"]


def test_add_before_setup_says_run_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_HOME", str(tmp_path / "nohome"))
    monkeypatch.chdir(_checkout(tmp_path / "p"))
    with pytest.raises(SystemExit, match="run brnrd setup first"):
        cli.main(["add"])
