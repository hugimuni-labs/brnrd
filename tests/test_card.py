"""The run card's Now projection — the Python half of a two-language contract.

Every case in ``tests/fixtures/card_now_projection.json`` runs here and in
``src/frontend/src/lib/runNode.test.ts``. The TS implementation is hand-written
because there is no shared runtime; the table is what makes that checkable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import card

FIXTURE = Path(__file__).parent / "fixtures" / "card_now_projection.json"
_TABLE = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _expand(parts: list) -> str:
    """A part list to a string: strings literal, ``[unit, count]`` repeated."""
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        else:
            unit, count = part
            out.append(unit * count)
    return "".join(out)


@pytest.mark.parametrize(
    "case", _TABLE["cases"], ids=[c["name"] for c in _TABLE["cases"]]
)
def test_shared_projection_table(case: dict) -> None:
    body = _expand(case["body"])
    expected = _expand(case["expected"])
    assert card.now_projection(body, limit=case.get("limit")) == expected


def test_the_fixture_table_is_the_one_the_typescript_test_reads() -> None:
    """A pin on the path, not the content.

    The table only makes the hand-written mirror checkable while both sides
    actually read it. If this file moves, the Vitest import breaks loudly
    rather than the frontend quietly testing nothing.
    """
    assert FIXTURE.exists()
    ts = (
        Path(__file__).parents[1]
        / "src" / "frontend" / "src" / "lib" / "runNode.test.ts"
    ).read_text(encoding="utf-8")
    assert "card_now_projection.json" in ts


def test_the_cap_mirrors_the_schema_that_actually_rejects() -> None:
    """``brr`` must not import the API package, so the number is copied.

    #722's outage was a bound that existed and did not engage. A bound that
    engages against the *wrong* number is the same failure one step later, so
    the copy is pinned to its source.
    """
    from brnrd.schemas import LiveRunIn

    field = LiveRunIn.model_fields["card_text"]
    declared = [
        m.max_length for m in field.metadata if hasattr(m, "max_length")
    ]
    assert declared == [card.CARD_TEXT_MAX_CHARS]


# --- the defect itself, stated as the behaviour that changed -----------------


def test_an_h1_now_card_no_longer_publishes_whole() -> None:
    """#722: the positive control and the regression in one pair.

    The ``## Now`` card is the control — it projected correctly before the fix
    and must still. The ``# Now`` card is the defect: identical content, one
    character of heading depth apart, and it used to return the entire body.
    """
    arc = "\n\n## Arc\n" + "history. " * 600
    h2 = "## Now\nliving\n" + arc
    h1 = "# Now\nliving\n" + arc.replace("## Arc", "# Arc")

    assert card.now_projection(h2, limit=card.CARD_TEXT_MAX_CHARS) == "living"
    assert card.now_projection(h1, limit=card.CARD_TEXT_MAX_CHARS) == "living"
    # And the thing that made it an outage rather than an annoyance:
    assert len(h1) > card.CARD_TEXT_MAX_CHARS


def test_a_section_less_card_is_bounded_rather_than_failing_open() -> None:
    """Half 2. The legacy one-note card stays valid — but not unbounded.

    ``return body`` was correct for a 200-character note and catastrophic for a
    9,811-character one, and nothing in the old code distinguished them.
    """
    body = "x" * 9811
    projected = card.now_projection(body, limit=card.CARD_TEXT_MAX_CHARS)
    assert len(projected) == card.CARD_TEXT_MAX_CHARS
    assert projected.endswith("…")
    # Unbounded when no transport is asking — the wake injection keeps it all.
    assert card.now_projection(body) == body


def test_an_oversize_now_section_is_bounded_too() -> None:
    """The half the ticket scoped to the fallback only.

    Fixing the anchor moves an H1 card off the fail-open path, and bounding the
    fallback closes it for section-less cards — but a card with a perfectly
    well-formed ``## Now`` holding 5,000 characters 422s identically, and 4 of
    272 captured bodies are exactly that. The bound is on the return value.
    """
    body = "## Now\n" + "y" * 5000 + "\n\n## Arc\nhistory"
    projected = card.now_projection(body, limit=card.CARD_TEXT_MAX_CHARS)
    assert len(projected) == card.CARD_TEXT_MAX_CHARS
    assert projected.endswith("…")
    assert "history" not in projected


def test_generalising_the_anchor_did_not_open_a_no_terminate_path() -> None:
    """The trap in the obvious fix.

    Match ``# Now`` without generalising the terminator and the section runs to
    EOF, because nothing after it starts with ``## ``. That moves the whole-body
    publish from the no-match path to the no-terminate path and measures as a
    fix.
    """
    body = "# Now\nliving\n\n# Arc\nhistory\n\n# Open\nquestions"
    assert card.now_projection(body) == "living"


def test_a_deeper_heading_stays_inside_the_section() -> None:
    """The other half of that trap: break on *any* heading and `### Sub` splits.

    It survives today only by accident — `### Sub` does not start with `## `.
    Depth comparison makes the accident a rule.
    """
    body = "## Now\nliving\n\n### Sub\ndetail\n\n## Arc\nhistory"
    assert card.now_projection(body) == "living\n\n### Sub\ndetail"


def test_a_hash_comment_inside_a_fence_is_not_a_heading() -> None:
    """A truncation path created by the fix, closed in the same change.

    Under the old H2-only terminator a ``# comment`` in a shell fence was
    harmless. Generalising to any depth makes it a section boundary unless
    fences are tracked — the fix would have introduced the defect class it was
    written to remove.
    """
    body = "## Now\n```sh\n# rm -rf\n```\ndone\n\n## Arc\nhistory"
    assert card.now_projection(body) == "```sh\n# rm -rf\n```\ndone"


def test_trailing_text_after_the_name_still_anchors() -> None:
    """Driven from the corpus, not imagined.

    10 of 272 captured bodies head the section ``## Now — done`` or
    ``## Now doing``. Under the exact-match anchor every one published whole,
    and the ticket's proposed ``#{1,6}\\s*now`` would have kept doing so.
    """
    body = "## Now — done\nliving\n\n## Arc\nhistory"
    assert card.now_projection(body) == "living"


def test_nowhere_is_a_different_section() -> None:
    """The cost of the trailing-text allowance, bounded by a word boundary."""
    body = "## Nowhere\nliving"
    assert card.now_projection(body) == "## Nowhere\nliving"


def test_section_names_report_an_h1_cards_shape() -> None:
    """The wake's "also in that body: …" line, which was empty for H1 cards.

    Same defect as the projection, on the surface that tells the next wake what
    the run wrote: an H1-sectioned body reported no sections at all.
    """
    assert card.section_names("# Now\na\n\n# Arc\nb\n\n# Open\nc") == ["Arc", "Open"]
    assert card.section_names("## Now\na\n\n## Arc\nb") == ["Arc"]
    # The shallowest depth in use is the top level; deeper headings are inside.
    assert card.section_names("## Now\na\n\n## Arc\nb\n\n### Sub\nc") == ["Arc"]
    assert card.section_names("no sections here") == []
