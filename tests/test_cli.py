"""Tests for CLI dispatch."""

import pytest

from brr.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


@pytest.mark.parametrize("command", ["status", "inspect", "docs", "streams", "stream", "eject"])
def test_removed_diagnostic_commands_are_not_public(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


def test_run_requires_instruction():
    with pytest.raises(SystemExit):
        main(["run"])


def test_run_self_review_flag_passes_to_runner(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr("brr.cli._repo_root", lambda: tmp_path)
    monkeypatch.setattr("brr.daemon.read_pid", lambda _brr: None)
    monkeypatch.setattr(
        "brr.runner.run_task",
        lambda instruction, *, self_review=False: calls.append(
            (instruction, self_review),
        )
        or "ok",
    )

    main(["run", "--self-review", "review the env"])

    assert calls == [("review the env", True)]


def test_up_dev_reload_flag_passes_to_daemon(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr("brr.cli._repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "brr.daemon.start",
        lambda repo_root, *, dev_reload=None: calls.append(
            (repo_root, dev_reload),
        ),
    )

    main(["up", "--dev-reload"])

    assert calls == [(tmp_path, True)]


def test_bind_dispatches_to_gate_bind(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def bind(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["bind", "telegram"])

    assert calls == [tmp_path / ".brr"]


def test_setup_dispatches_to_gate_setup(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def setup(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["setup", "telegram"])

    assert calls == [tmp_path / ".brr"]


def test_setup_falls_back_to_auth_then_bind(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def auth(brr_dir):
            calls.append(("auth", brr_dir))

        @staticmethod
        def bind(brr_dir):
            calls.append(("bind", brr_dir))

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["setup", "telegram"])

    assert calls == [
        ("auth", tmp_path / ".brr"),
        ("bind", tmp_path / ".brr"),
    ]
