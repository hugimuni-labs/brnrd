"""Tests for runner module."""

from brr.runner import detect_runner


def test_detect_runner_returns_string_or_none():
    result = detect_runner()
    assert result is None or isinstance(result, str)
