"""The wire's boundary-detail disclosure bound.

Measured 2026-08-28: the room rendered the body of a chat message off a
boundary line, because a reply written as `cat > f <<'EOF' …` puts its whole
text into argv, and the publisher forwarded 500 characters of it. That 500
was `hooks._DETAIL_BASH_MAX` — a *retention* cap sized for the local,
gitignored `boundaries.jsonl` — doing disclosure work it was never chosen for.

These pin the seam, not a renderer: there were four renderers of `edge.detail`
at the time (the ASCII room, `ResidentField`, the `/new` HUD, `liveRuns`' own
summary), and bounding one left three.
"""

from brr.gates.cloud_publisher import _WIRE_DETAIL_MAX, _wire_detail


def test_a_heredoc_keeps_its_shape_and_loses_its_body():
    detail = (
        "cat > /tmp/reply5.md <<'MDEOF' **#1671 merged — thanks. One consequence "
        "handled before it bit: #1672 was stacked on that branch.**"
    )
    out = _wire_detail(detail)
    assert out == "cat > /tmp/reply5.md <<'MDEOF' …"
    assert "#1671 merged" not in out
    assert "stacked on that branch" not in out


def test_every_heredoc_spelling_is_caught_by_the_operator_not_a_list():
    # The operator is the structural fact; the delimiter is arbitrary text.
    for opening in ("<<'EOF'", '<<"EOF"', "<<EOF", "<<-EOF", "<<MDEOF"):
        out = _wire_detail(f"cat > f {opening} secret prose that must not travel")
        assert "secret prose" not in out, opening
        assert opening in out, opening


def test_a_payload_with_no_heredoc_is_still_bounded():
    # The bound is the guarantee; the heredoc rule is only legibility. Anything
    # that rode argv — `-m`, `-c`, a quoted echo — is cut by length regardless.
    out = _wire_detail("git commit -m " + "x" * 500)
    assert out is not None
    assert len(out) == _WIRE_DETAIL_MAX


def test_a_short_command_travels_whole():
    assert _wire_detail("pytest tests/test_x.py") == "pytest tests/test_x.py"


def test_nothing_attested_stays_nothing():
    # Absent is different from empty, and both are different from "".
    assert _wire_detail(None) is None
    assert _wire_detail("") is None
    assert _wire_detail(123) is None


def test_the_wire_bound_is_tighter_than_the_local_retention_cap():
    # If these ever converge again, the disclosure rule has silently become
    # whatever transport happened to need — which is how this defect happened.
    from brr.hooks import _DETAIL_BASH_MAX

    assert _WIRE_DETAIL_MAX < _DETAIL_BASH_MAX
