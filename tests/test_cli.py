"""Tests for CLI dispatch."""

import subprocess
import sys

import pytest

from brr.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "brr" in capsys.readouterr().out


def test_status_outside_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["status"])
    assert "Not a Git repository" in capsys.readouterr().out


def test_run_dispatches(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    monkeypatch.chdir(repo)
    main(["run", "hello world"])
    out = capsys.readouterr().out
    assert "hello world" in out
