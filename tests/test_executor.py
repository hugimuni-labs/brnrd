"""Tests for executor module."""

from brr.executor import detect_executor


def test_detect_executor_returns_string_or_none():
    result = detect_executor()
    assert result is None or isinstance(result, str)
