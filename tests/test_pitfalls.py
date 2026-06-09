"""Tests for trigger-indexed failure-memory (`brr.pitfalls`)."""

from __future__ import annotations

from pathlib import Path

from brr import pitfalls


def _write(dom: Path, text: str) -> Path:
    dom.mkdir(parents=True, exist_ok=True)
    (dom / pitfalls.PITFALLS_FILE).write_text(text, encoding="utf-8")
    return dom


# ── parse ────────────────────────────────────────────────────────────


def test_parse_missing_file_is_empty(tmp_path: Path) -> None:
    assert pitfalls.parse_pitfalls(tmp_path / "dominion") == []


def test_parse_ignores_preamble_before_first_heading(tmp_path: Path) -> None:
    dom = _write(
        tmp_path / "dom",
        "# Pitfalls\n# a comment\ntrigger: not-a-real-trigger\n\n"
        "## Real one\ntrigger: docker\nbody line\n",
    )
    parsed = pitfalls.parse_pitfalls(dom)
    assert len(parsed) == 1
    assert parsed[0].title == "Real one"
    assert parsed[0].triggers == ["docker"]
    assert parsed[0].body == "body line"


def test_parse_splits_triggers_and_keeps_body(tmp_path: Path) -> None:
    dom = _write(
        tmp_path / "dom",
        "## Blind retry\n"
        "trigger: retry, 5xx ,  http client \n"
        "Line one.\nLine two.\n",
    )
    (p,) = pitfalls.parse_pitfalls(dom)
    assert p.triggers == ["retry", "5xx", "http client"]
    assert p.body == "Line one.\nLine two."


def test_parse_pitfall_without_trigger_is_inert(tmp_path: Path) -> None:
    dom = _write(tmp_path / "dom", "## No trigger\njust prose\n")
    (p,) = pitfalls.parse_pitfalls(dom)
    assert p.triggers == []
    assert p.matches("anything at all") is False


def test_parse_multiple_pitfalls(tmp_path: Path) -> None:
    dom = _write(
        tmp_path / "dom",
        "## First\ntrigger: alpha\nbody a\n\n## Second\ntrigger: beta\nbody b\n",
    )
    parsed = pitfalls.parse_pitfalls(dom)
    assert [p.title for p in parsed] == ["First", "Second"]


# ── match ──────────────────────────────────────────────────────────────


def test_match_is_case_insensitive_substring(tmp_path: Path) -> None:
    dom = _write(tmp_path / "dom", "## P\ntrigger: Docker\nb\n")
    parsed = pitfalls.parse_pitfalls(dom)
    assert pitfalls.match(parsed, "rebuild the DOCKER image") != []
    assert pitfalls.match(parsed, "unrelated task") == []


def test_match_any_trigger_fires(tmp_path: Path) -> None:
    dom = _write(tmp_path / "dom", "## P\ntrigger: alpha, beta\nb\n")
    parsed = pitfalls.parse_pitfalls(dom)
    assert pitfalls.match(parsed, "touching beta today") != []


def test_match_empty_task_text_returns_nothing(tmp_path: Path) -> None:
    dom = _write(tmp_path / "dom", "## P\ntrigger: alpha\nb\n")
    parsed = pitfalls.parse_pitfalls(dom)
    assert pitfalls.match(parsed, "") == []


def test_match_preserves_file_order(tmp_path: Path) -> None:
    dom = _write(
        tmp_path / "dom",
        "## First\ntrigger: x\n\n## Second\ntrigger: x\n",
    )
    parsed = pitfalls.parse_pitfalls(dom)
    assert [p.title for p in pitfalls.match(parsed, "x x")] == ["First", "Second"]


# ── format ─────────────────────────────────────────────────────────────


def test_format_empty_is_blank(tmp_path: Path) -> None:
    assert pitfalls.format_block([]) == ""


def test_format_renders_titles_and_bodies(tmp_path: Path) -> None:
    dom = _write(
        tmp_path / "dom",
        "## Blind retry\ntrigger: retry\nGate retries behind idempotency.\n",
    )
    block = pitfalls.format_block(pitfalls.parse_pitfalls(dom))
    assert "## Pitfalls that match this task" in block
    assert "### Blind retry" in block
    assert "Gate retries behind idempotency." in block
    assert "trigger:" not in block  # triggers are matching metadata, not shown
