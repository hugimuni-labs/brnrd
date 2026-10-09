"""The self's git calls share one discovery boundary."""

from pathlib import Path

from brr.loom.runtime.labels import CLEAN, _strand_of
from brr.loom.runtime.loom import _env as body_env
from brr.loom.runtime.merge import _git, _stamp_message
from brr.loom.runtime.merge_driver import install_merge_driver
from brr.loom.runtime.selfrepo import _PINNED_GIT, git, init_self


def test_self_operations_ignore_all_inherited_discovery_pins(tmp_path: Path, monkeypatch) -> None:
    for key in _PINNED_GIT:
        monkeypatch.setenv(key, str(tmp_path / "foreign-repo"))
    home = tmp_path / "home"
    init_self(home)
    repo = home / "self"
    git(repo, "checkout", "-b", "strand/own")
    assert _git(repo, "rev-parse", "--show-toplevel").stdout.strip() == str(repo)
    assert _strand_of(repo) == "own"
    install_merge_driver(repo)
    assert "merge-driver" in git(repo, "config", "merge.loom-readme.driver").stdout
    assert not set(_PINNED_GIT) & body_env(repo).keys()
    text = _stamp_message("change\n", strand="own", label=CLEAN, widening=None)
    assert "Loom-Label: taint=0; audience=self" in text
