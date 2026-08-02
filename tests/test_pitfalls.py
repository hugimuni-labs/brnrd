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


def test_parse_empty_file_is_empty(tmp_path: Path) -> None:
    assert pitfalls.parse_pitfalls(_write(tmp_path / "dom", "")) == []


def test_parse_preamble_only_is_empty(tmp_path: Path) -> None:
    dom = _write(
        tmp_path / "dom",
        "# Pitfalls\n# explanatory comment\ntrigger: not-a-real-trigger\n",
    )
    assert pitfalls.parse_pitfalls(dom) == []


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


def test_parse_accumulates_multiple_trigger_lines(tmp_path: Path) -> None:
    dom = _write(
        tmp_path / "dom",
        "## Two trigger lines\n"
        "trigger: quota, rate limit\n"
        "Some prose between them.\n"
        "trigger: branch, rebase\n",
    )
    (p,) = pitfalls.parse_pitfalls(dom)
    assert p.triggers == ["quota", "rate limit", "branch", "rebase"]
    assert p.body == "Some prose between them."


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


def test_parse_existing_heading_shape_is_backwards_compatible(tmp_path: Path) -> None:
    dom = _write(
        tmp_path / "dom",
        "# Pitfalls\n\n"
        "## First\ntrigger: alpha, beta\nbody a\n\n"
        "## Second\ntrigger: gamma\nbody b\n",
    )
    assert pitfalls.parse_pitfalls(dom) == [
        pitfalls.Pitfall(title="First", triggers=["alpha", "beta"], body="body a"),
        pitfalls.Pitfall(title="Second", triggers=["gamma"], body="body b"),
    ]


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
    lines = block.splitlines()
    assert lines[0] == "# Pitfalls that match this task"
    assert "## Blind retry" in lines
    assert "Gate retries behind idempotency." in block
    assert "trigger:" not in block  # triggers are matching metadata, not shown


def test_format_entry_heading_round_trips_as_distinct_pitfall(tmp_path: Path) -> None:
    rendered = pitfalls.format_block(
        [
            pitfalls.Pitfall(
                title="Quota flicker",
                triggers=["quota"],
                body="Check the provider status before changing configuration.",
            )
        ]
    )
    dom = _write(
        tmp_path / "dom",
        "## Existing host\ntrigger: existing\nHost body.\n\n" + rendered,
    )

    parsed = pitfalls.parse_pitfalls(dom)

    assert [p.title for p in parsed] == ["Existing host", "Quota flicker"]
    assert parsed[0].triggers == ["existing"]
    assert parsed[1].body == (
        "Check the provider status before changing configuration."
    )


# ── inert entries (#985) ─────────────────────────────────────────────
#
# The store's one silent failure mode: an entry with no `trigger:` line
# parses, reads as filed, and matches nothing forever. These pin the
# *detector*; `tests/test_prompts.py` pins that what it detects actually
# reaches the block a wake renders.


def test_inert_reports_an_entry_with_no_trigger_line(tmp_path: Path) -> None:
    dom = _write(
        tmp_path / "dom",
        "## Probe config\nSee the probe before trusting the screenshot.\n",
    )

    assert [p.title for p in pitfalls.inert(pitfalls.parse_pitfalls(dom))] == [
        "Probe config"
    ]


def test_inert_is_silent_when_every_entry_carries_a_trigger(
    tmp_path: Path,
) -> None:
    """The bar most likely to regress: a clean file costs nothing.

    A notice that fires for a non-reason stops being read, and takes the
    files where it *is* a reason down with it.
    """
    dom = _write(
        tmp_path / "dom",
        "## First\ntrigger: docker\nbody a\n\n"
        "## Second\ntrigger: quota, budget\nbody b\n",
    )

    assert pitfalls.inert(pitfalls.parse_pitfalls(dom)) == []


def test_inert_catches_a_trigger_line_with_nothing_in_it(
    tmp_path: Path,
) -> None:
    """`trigger:` followed by only separators is the same defect wearing a
    trigger line — `parse_pitfalls` drops the empty fields, so the entry
    reaches `matches()` with an empty trigger list exactly as if the line
    had never been written."""
    dom = _write(tmp_path / "dom", "## Hollow\ntrigger: , ,\nbody\n")

    (entry,) = pitfalls.parse_pitfalls(dom)
    assert entry.triggers == []
    assert [p.title for p in pitfalls.inert([entry])] == ["Hollow"]


def test_inert_keeps_file_order_and_reports_only_the_inert_ones(
    tmp_path: Path,
) -> None:
    dom = _write(
        tmp_path / "dom",
        "## Alpha\nno trigger here\n\n"
        "## Beta\ntrigger: beta\nbody\n\n"
        "## Gamma\nnor here\n",
    )

    assert [p.title for p in pitfalls.inert(pitfalls.parse_pitfalls(dom))] == [
        "Alpha", "Gamma",
    ]


def test_an_inert_entry_never_matches_anything(tmp_path: Path) -> None:
    """The consequence the notice exists to report, asserted rather than
    assumed: this is *why* an inert entry is worth a line."""
    dom = _write(tmp_path / "dom", "## Probe config\nprobe config screenshot\n")

    parsed = pitfalls.parse_pitfalls(dom)

    assert pitfalls.inert(parsed) == parsed
    assert pitfalls.match(parsed, "probe config screenshot probe") == []
