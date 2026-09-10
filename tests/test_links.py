"""The priced decision chain (`.links.jsonl`, `brnrd do --link`).

The measurement that produced the verb: a run held its seat across a
71-minute gate — ten `brnrd await` calls, ~250k weighted tokens, the single
largest expenditure of that run — and chose it ten separate times without
ever attaching a number to it. Nothing was hidden; the bar printed the spend
the whole time. What was missing was *attribution*.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import links


def test_a_link_with_no_item_renders_the_empty_set(tmp_path):
    """`∅` is the product, not a placeholder.

    The flag is optional on purpose: requiring an item would make residents
    invent one, and an invented item measures worse than a missing one — the
    same failure as a forecast made defensive by a penalty.
    """
    assert links.chip(tmp_path) == "link ∅"

    links.open_link(tmp_path, intent="hold the seat", why="", spend=1_000)

    chip = links.chip(tmp_path, spend_now=251_000)
    assert chip.startswith("link hold the seat")
    assert "250k/∅" in chip, chip


def test_opening_a_link_closes_the_open_one(tmp_path):
    """A chain does not nest. Calls between a close and the next open are `∅`."""
    links.open_link(tmp_path, intent="first", why="a", spend=0)
    links.open_link(tmp_path, intent="second", why="b", spend=100)

    rows = links.read(tmp_path)
    assert [r["intent"] for r in rows] == ["first", "second"]
    assert rows[0]["closed_at"] is not None
    assert rows[0]["spend_close"] == 100, "the closing spend is the next link's opening"
    assert rows[1]["closed_at"] is None
    assert links.open_row(tmp_path)["intent"] == "second"


def test_the_row_stores_endpoints_never_a_delta(tmp_path):
    """A delta written at record time cannot be recomputed when the weighting moves.

    This repo has paid for that twice; the row keeps `spend_open` and
    `spend_close` and lets every reader do its own arithmetic.
    """
    links.open_link(tmp_path, intent="x", why="y", spend=1_000)
    links.close_link(tmp_path, spend=4_000)

    row = json.loads((tmp_path / links.CONTROL_NAME).read_text().strip())
    assert row["spend_open"] == 1_000
    assert row["spend_close"] == 4_000
    assert "delta" not in row and "spent" not in row
    assert links.spent(row) == 3_000


def test_unmeasurable_spend_is_none_never_zero(tmp_path):
    """The empty-column rule: a field's name is not a measurement.

    A link whose endpoints could not be read has *no* measurement. Rendering
    that as `0` claims the decision was free, which is the one thing it
    certainly was not.
    """
    links.open_link(tmp_path, intent="x", why="y", spend=None)
    row = links.open_row(tmp_path)

    assert links.spent(row) is None
    assert links.spent(row, spend_now=500) is None, (
        "one missing endpoint is still no measurement"
    )
    assert "/" not in links.chip(tmp_path), links.chip(tmp_path)


def test_a_boundary_is_reported_and_never_enforced(tmp_path):
    """The sanction is that it is written down.

    A boundary that halted a run would be set high by everyone who wanted to
    keep working, and a boundary nobody sets honestly measures nothing.
    """
    links.open_link(
        tmp_path, intent="build", why="his ask", boundary=100_000, spend=0,
    )
    row = links.open_row(tmp_path)

    assert links.over_boundary(row, spend_now=99_000) is False
    assert links.over_boundary(row, spend_now=101_000) is True
    assert "⚠" in links.chip(tmp_path, spend_now=101_000)
    # and nothing raised, nothing refused, on either side of the line
    assert links.open_link(tmp_path, intent="next", why="still going", spend=101_000)


def test_no_boundary_declared_is_not_over_it(tmp_path):
    """`∅` must never read as a breach — an unpriced link is unmeasured, not late."""
    links.open_link(tmp_path, intent="x", why="y", spend=0)
    assert links.over_boundary(links.open_row(tmp_path), spend_now=10**9) is False


def test_unbound_streak_counts_back_to_the_last_item(tmp_path):
    """The drift signal, measured rather than inferred.

    Three links matching no open item does not mean the plan stalled; it
    means the plan is describing a run that stopped happening.
    """
    links.open_link(tmp_path, intent="a", why="w", item="w-1", spend=0)
    assert links.unbound_streak(tmp_path) == 0

    for name in ("b", "c", "d"):
        links.open_link(tmp_path, intent=name, why="w", spend=0)
    assert links.unbound_streak(tmp_path) == 3

    links.open_link(tmp_path, intent="e", why="w", item="w-2", spend=0)
    assert links.unbound_streak(tmp_path) == 0, "an item resets the streak"


def test_closing_nothing_is_not_an_error(tmp_path):
    assert links.close_link(tmp_path) is None
    assert links.read(tmp_path) == []


def test_a_malformed_line_costs_its_own_row_only(tmp_path):
    """Same tolerance as every sibling control file: one bad row is not the file."""
    links.open_link(tmp_path, intent="good", why="w", spend=0)
    with (tmp_path / links.CONTROL_NAME).open("a", encoding="utf-8") as handle:
        handle.write("{not json\n\n")

    rows = links.read(tmp_path)
    assert [r["intent"] for r in rows] == ["good"]


def test_the_control_file_is_dot_prefixed(tmp_path):
    """`daemon._drain_outbox` skips dot-files outright.

    A bare `links.jsonl` in a live outbox would be swept up as an
    undelivered chat message and fail to parse as frontmatter — the exact
    reasoning `do.ASKS_CONTROL_NAME` records for its own name.
    """
    assert links.CONTROL_NAME.startswith(".")


# ── the CLI verb ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv, needle",
    [
        (["--link", "x"], "--link requires --why"),
        (["--link", "x", "--why", "y", "--boundary", "lots"], "not a token count"),
        (["--why", "orphan"], "--why given with no --link"),
        (["--boundary", "1k"], "--boundary given with no --link"),
        (["--link-item", "w-1"], "--link-item given with no --link"),
        (["--link", "x", "--why", "y", "--link-close"], "already closes the open one"),
    ],
)
def test_every_refusal_is_total(tmp_path, capsys, argv, needle):
    """Nothing written on any refusal.

    A half-written chain is worse than no chain: the gap reads as an unpriced
    decision where it was in fact a rejected one.
    """
    from brr.cli import main

    assert main(["do", "--outbox", str(tmp_path), *argv]) == 1
    assert needle in capsys.readouterr().err
    assert not (tmp_path / links.CONTROL_NAME).exists()


def test_open_and_close_through_the_cli(tmp_path, capsys):
    from brr.cli import main

    assert main([
        "do", "--outbox", str(tmp_path),
        "--link", "build the priced decision chain",
        "--why", "his ask, personally",
        "--boundary", "400k",
    ]) == 0
    out = capsys.readouterr().out
    assert "link opened: build the priced decision chain" in out
    assert "400k" in out and "∅" in out, "an unbound link says so at the seam too"

    row = links.open_row(tmp_path)
    assert row["boundary"] == 400_000
    assert row["why"] == "his ask, personally"
    assert row["item"] is None

    assert main(["do", "--outbox", str(tmp_path), "--link-close"]) == 0
    assert "link closed" in capsys.readouterr().out
    assert links.open_row(tmp_path) is None

    assert main(["do", "--outbox", str(tmp_path), "--link-close"]) == 0
    assert "nothing open" in capsys.readouterr().out
