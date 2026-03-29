"""Tests for CLI dispatch."""

import subprocess

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


def test_run_requires_instruction(capsys):
    with pytest.raises(SystemExit):
        main(["run"])
