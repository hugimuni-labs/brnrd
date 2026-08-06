"""Tests for the course — the run's own route, read back off its card.

Unit half: :mod:`brr.course` parsing/rendering. Integration half: the course
riding the boundary channel through ``hooks.run_hook``, because the feature
*is* the read-back — a parser that works and a boundary that never carries
it would be the exact "read by no boundary" gap this closes
(design-the-blueprint.md's fourth flag).
"""

from __future__ import annotations

import json

from brr import course, hooks


# ── unit: parsing ────────────────────────────────────────────────────────────


CARD = """# my-run

## Now
Doing things.

## Plan
- [x] read the ticket
prose between rows is legal and ignored
- [ ] fix the parser
- [ ] gate + PR

## Notes
- [ ] a checkbox outside the section does not count
"""


def test_parse_reads_only_the_course_section():
    route = course.parse(CARD)
    assert route is not None
    assert route.total == 3
    assert route.done_count == 1
    assert [row.text for row in route.open_rows] == ["fix the parser", "gate + PR"]


def test_parse_accepts_both_headings_case_insensitively():
    for heading in ("## Plan", "## plan", "## Course", "## COURSE"):
        body = f"{heading}\n- [ ] one thing\n"
        route = course.parse(body)
        assert route is not None and route.total == 1, heading


def test_parse_returns_none_without_a_course():
    assert course.parse(None) is None
    assert course.parse("") is None
    assert course.parse("# card\n\n## Now\nworking\n") is None
    # A checkbox outside any course section is not a course.
    assert course.parse("## Notes\n- [ ] loose row\n") is None


def test_cursor_mark_overrides_first_unchecked():
    body = "## Plan\n- [ ] first open\n- [ ] the real current ←\n- [ ] later\n"
    route = course.parse(body)
    assert route is not None
    current = route.current
    assert current is not None
    assert current.text == "the real current"


def test_row_and_section_caps_keep_the_denominator_honest():
    rows = "\n".join(f"- [ ] row {i}" for i in range(course.MAX_ROWS + 5))
    route = course.parse("## Plan\n" + rows + "\n")
    assert route is not None
    assert len(route.rows) == course.MAX_ROWS
    assert route.overflow == 5
    assert route.total == course.MAX_ROWS + 5
    # The chip's denominator counts overflow — a clipped parse must not
    # render a smaller plan than the resident wrote.
    assert course.chip(route) == f"course 0/{course.MAX_ROWS + 5}"


def test_token_moves_only_when_the_section_changes():
    a = course.token(course.parse("## Plan\n- [ ] x\n- [ ] y\n"))
    same = course.token(course.parse("## Plan\n- [ ] x\n- [ ] y\n"))
    checked = course.token(course.parse("## Plan\n- [x] x\n- [ ] y\n"))
    assert a == same
    assert a != checked
    # Content outside the section does not move the token: the latch must
    # not fire because `## Now` was rewritten.
    other = course.token(
        course.parse("## Now\nnew prose\n\n## Plan\n- [ ] x\n- [ ] y\n")
    )
    assert a == other


def test_chip_is_silent_when_finished_or_absent():
    assert course.chip(None) is None
    assert course.chip(course.parse("## Plan\n- [x] done\n")) is None
    assert course.chip(course.parse(CARD)) == "course 1/3"


def test_stop_lines_name_open_rows_and_stay_silent_when_kept():
    lines = course.stop_lines(course.parse(CARD))
    assert lines and "2 of 3" in lines[0]
    assert any("fix the parser" in line for line in lines)
    assert course.stop_lines(course.parse("## Plan\n- [x] all done\n")) == []
    assert course.stop_lines(None) == []


# ── integration: the boundary carries the route ──────────────────────────────


def _portal(tmp_path, *, token="t1", pending=0, events=None):
    payload = {
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {
            "pending_event_count": pending,
            "pending_outbox_file_count": 0,
        },
        "inbound": {
            "current_event": "evt-1",
            "current_event_replyable": True,
            "events": events or [],
        },
        "outbound": {
            "replies_current": 0, "replies_other": 0, "outbound_messages": 0,
        },
        "budget": {"elapsed_seconds": 10, "budget_seconds": 3600},
        "card": {"present": True, "stale": False},
        "change_token": token,
    }
    (tmp_path / "portal-state.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _env(tmp_path):
    return {
        "BRR_RUN_ID": "run-1",
        "BRR_EVENT_ID": "evt-1",
        "BRR_RUNNER": "claude",
        "BRR_OUTBOX_DIR": str(tmp_path),
        "BRR_PORTAL_STATE": str(tmp_path / "portal-state.json"),
    }


def _write_card(tmp_path, body):
    (tmp_path / ".card").write_text(body, encoding="utf-8")


def _inject(out):
    if not out:
        return ""
    ctx = out.get("hookSpecificOutput") or {}
    return ctx.get("additionalContext") or ""


def test_course_edge_opens_the_boundary_and_renders_chip_and_line(tmp_path):
    """Writing a route reaches the very next boundary — the card is a
    control file the portal token never sees, so without the edge opening
    the gate the one confirmation boundary would render nothing (#1008's
    gate-opener rule, applied to the course)."""
    _portal(tmp_path, token="t1")
    _write_card(tmp_path, CARD)
    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert code == 0
    text = _inject(out)
    assert "course 1/3" in text
    assert "course → fix the parser" in text
    assert "(+1 more open)" in text


def test_standing_course_does_not_re_render_its_line(tmp_path):
    """The current-row line is latched on the course's own delta: a route
    that merely *stands* must not repeat it (the fires-constantly death).
    The chip may keep riding boundaries that render for other reasons."""
    _portal(tmp_path, token="t1")
    _write_card(tmp_path, CARD)
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert "course →" in _inject(out)
    # Same card, same token, nothing else to say: the boundary is quiet and
    # carries no second course line.
    out2, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert "course →" not in _inject(out2)


def test_checking_a_row_moves_the_chip_on_the_next_boundary(tmp_path):
    """The discharge is the off switch: checking a row on the card is the
    act, and the next boundary confirms it moved."""
    _portal(tmp_path, token="t1")
    _write_card(tmp_path, CARD)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    _write_card(tmp_path, CARD.replace("- [ ] fix the parser", "- [x] fix the parser"))
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    text = _inject(out)
    assert "course 2/3" in text
    assert "course → gate + PR" in text


def test_fresh_event_re_renders_the_current_row(tmp_path):
    """The derailment moment: a *new* event letter carries the current route
    row with it, so "continue or turn" is decidable in the loud zone."""
    _portal(tmp_path, token="t1")
    _write_card(tmp_path, CARD)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    # A fresh steer lands; the route line rides its boundary.
    _portal(
        tmp_path, token="t2", pending=1,
        events=[{"id": "evt-9", "source": "telegram", "summary": "new topic"}],
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    text = _inject(out)
    assert "evt-9" in text
    assert "course → fix the parser" in text
    # The same event still pending on the next boundary is a reminder, not a
    # derailment — the route line does not ride `seen ×N` repeats.
    out2, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert "course →" not in _inject(out2)


def test_stop_reads_back_open_rows_even_if_said_mid_run(tmp_path):
    """Unlatched at Stop: the closeout is the moment the surface exists for.
    An open row is said there even when a mid-run boundary already carried
    it — the difference between an obligation and an ambient line."""
    _portal(tmp_path, token="t1")
    _write_card(tmp_path, CARD)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    text = _inject(out)
    assert "course open: 2 of 3" in text
    assert "fix the parser" in text
    assert "gate + PR" in text


def test_stop_is_silent_on_a_finished_route(tmp_path):
    _portal(tmp_path, token="t1")
    _write_card(
        tmp_path,
        "## Plan\n- [x] read the ticket\n- [x] fix the parser\n- [x] gate + PR\n",
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    assert "course open" not in _inject(out)
