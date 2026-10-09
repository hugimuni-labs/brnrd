"""The self's git calls share one discovery boundary."""

from pathlib import Path

import pytest

from brr.loom.runtime.labels import CLEAN, _strand_of
from brr.loom.runtime.loom import _env as body_env
from brr.loom.runtime.merge import _git, _stamp_message, _unmerged
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


@pytest.mark.parametrize("stages", [(1, 2), (1, 3), (1, 2, 3)])
def test_deleted_and_unmerged_files_are_named_from_the_index(tmp_path: Path, stages) -> None:
    git(tmp_path, "init", "-b", "main")
    blob = git(tmp_path, "hash-object", "-w", "--stdin", input="content\n").stdout.strip()
    entries = "".join(f"100644 {blob} {stage}\tmissing.txt\n" for stage in stages)
    git(tmp_path, "update-index", "--index-info", input=entries)
    assert not (tmp_path / "missing.txt").exists()
    assert _unmerged(tmp_path) == ("missing.txt",)
