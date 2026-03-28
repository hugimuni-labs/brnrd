"""Tests for runners module."""

from brr.runners import get_default_runner, Runner, CodexRunner


def test_get_default_runner_returns_runner():
    runner = get_default_runner()
    assert isinstance(runner, Runner)


def test_codex_runner_name():
    r = CodexRunner()
    assert r.name == "codex"
