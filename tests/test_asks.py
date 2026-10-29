"""The list of asks: LRU order, the stale horizon, done apart, the disk door."""

from __future__ import annotations

import datetime as dt

from brr import asks

NOW = dt.datetime(2026, 9, 22, tzinfo=dt.timezone.utc)


def _pairs(**items: str):
    return [(f"surface/warp/{k.replace('_', '-')}.md", v) for k, v in items.items()]


def test_lru_order_and_stale_sinks_below_live():
    payload = asks.build_asks(
        _pairs(
            w_1="# Old\n\ntouched: 2026-09-10\n",
            w_2="# New\n\ntouched: 2026-09-21\n",
            w_3="# Ancient\n\ntouched: 2026-01-01\n",
            w_4="# Unknown\n\ntype: action\n",
        ),
        now=NOW,
    )
    assert [r["id"] for r in payload["asks"]] == ["w-2", "w-1", "w-3", "w-4"]
    assert [r["stale"] for r in payload["asks"]] == [False, False, True, True]


def test_done_retired_and_goals_are_apart_and_done_keeps_its_row():
    payload = asks.build_asks(
        _pairs(w_1="# Shipped\n\ndone: 2026-09-01\n", w_2="# Gone\n\nretired: 2026-09-02\n",
               g_1="# Goal\n\ntype: goal\n", w_3="# Open\n\ntouched: 2026-09-20\n"),
        now=NOW,
    )
    assert [r["id"] for r in payload["asks"]] == ["w-3"]
    assert {r["id"] for r in payload["done"]} == {"w-1", "w-2"}
    assert [g["id"] for g in payload["goals"]] == ["g-1"]
    assert payload["done"][0]["stage"] == "accepted"


def test_says_attempts_and_run_id_touch():
    payload = asks.build_asks(
        _pairs(w_9="# T\n\nreturn: in chat\nsays: evt-1 evt-2\ntaken: run-260921-2330-3low\nafter: w-8\n"),
        now=NOW,
    )
    row = payload["asks"][0]
    assert [s["event"] for s in row["says"]] == ["evt-1", "evt-2"]
    assert row["attempts"] == ["run-260921-2330-3low"]
    assert row["touched_at"].startswith("2026-09-21T23:30")
    assert row["after"] == "w-8" and row["return"] == "in chat"


def test_run_node_asks_rows_join_the_says_and_bad_lines_are_skipped():
    files = _pairs(w_5="# T\n\ntouched: 2026-09-01\n") + [
        ("runs/x/run-260920-1000-abcd/asks.jsonl", '{"event":"evt-z","item":"w-5"}\nnot json\n'),
    ]
    row = asks.build_asks(files, now=NOW)["asks"][0]
    assert [s["event"] for s in row["says"]] == ["evt-z"]
    assert row["touched_at"].startswith("2026-09-20T10:00")


def test_non_item_files_are_skipped():
    payload = asks.build_asks(
        [("surface/warp/index.md", "# i"), ("surface/warp/sub/w-1.md", "# x"),
         ("surface/other.md", "# o"), ("surface/warp/w-1.readings.jsonl", "{}")],
        now=NOW,
    )
    assert payload["asks"] == [] and payload["done"] == []


def test_asks_from_files_orders_by_committed_at_the_publisher_stamps():
    """design-the-ask.md §Done, reopened, linked: "the hosted order needs the
    publisher to stamp each surface file's last commit time" — the reader's
    half of that contract. `w-1` has no `touched:`/says/attempts/done row at
    all, so without `committed_at` it would read as unknown and sink; the
    publisher's stamp alone should be enough to put it on top.
    """
    payload = asks.asks_from_files(
        [
            {"path": "surface/warp/w-1.md", "markdown": "# New\n", "committed_at": "2026-09-21T12:00:00Z"},
            {"path": "surface/warp/w-2.md", "markdown": "# Old\n\ntouched: 2026-09-01\n"},
        ],
        now=NOW,
    )
    assert [r["id"] for r in payload["asks"]] == ["w-1", "w-2"]
    assert payload["asks"][0]["touched_at"].startswith("2026-09-21T12:00")


def test_asks_from_files_ignores_committed_at_off_the_warp():
    """A stray ``committed_at`` on a non-warp/non-.md path must not raise or
    get folded in under a bogus item id."""
    payload = asks.asks_from_files(
        [{"path": "knowledge/repos/x/foo.md", "markdown": "# K", "committed_at": "2026-09-21T12:00:00Z"}],
        now=NOW,
    )
    assert payload["asks"] == [] and payload["done"] == []


def test_run_node_asks_row_with_part_surfaces_as_excerpt():
    """design-the-ask.md §Build cut, step 2 (rung 3): a `--part` bound
    alongside `--item` rides the same `.asks.jsonl` row as `part`; the
    reader surfaces it as that say's `excerpt`. A row with no `part` keeps
    `excerpt: None`, unchanged from before this existed."""
    files = _pairs(w_5="# T\n\ntouched: 2026-09-01\n") + [
        (
            "runs/x/run-260920-1000-abcd/asks.jsonl",
            '{"event":"evt-a","item":"w-5","part":"the fuel gauge bit"}\n'
            '{"event":"evt-b","item":"w-5"}\n',
        ),
    ]
    row = asks.build_asks(files, now=NOW)["asks"][0]
    says_by_event = {s["event"]: s for s in row["says"]}
    assert says_by_event["evt-a"]["excerpt"] == "the fuel gauge bit"
    assert says_by_event["evt-b"]["excerpt"] is None


def test_sign_row_rides_the_regular_row_dict():
    payload = asks.build_asks(_pairs(w_1="# T\n\nsign: mira\n"), now=NOW)
    assert payload["asks"][0]["sign"] == "mira"
    no_sign = asks.build_asks(_pairs(w_2="# T\n\ntype: action\n"), now=NOW)
    assert no_sign["asks"][0]["sign"] is None


def test_public_row_carries_sign():
    payload = asks.build_asks(_pairs(w_1="# T\n\nsign: mira\n"), now=NOW)
    assert asks.public_row(payload["asks"][0])["sign"] == "mira"


# ── the inbound directive: "accept w-N" / "reroute w-N: <why>" ────────────


def _item(root, item_id, text):
    (root / f"{item_id}.md").write_text(text, encoding="utf-8")


def test_apply_inbound_directive_through_the_real_event_creation_path(tmp_path):
    """"Through the real event-creation path" — the task's own words: mint
    the event with `protocol.create_event` (the function every inbound
    gate calls) rather than a hand-built id/body, then apply the directive
    against the id and body it actually produced."""
    from brr import protocol

    inbox_dir = tmp_path / "inbox"
    root = tmp_path / "warp"
    root.mkdir()
    _item(root, "w-1", "# T\n\ntype: action\n")
    event_path = protocol.create_event(inbox_dir, source="cloud", body="accept w-1\n")
    result = asks.apply_inbound_directive(root, event_path.stem, "accept w-1\n")
    assert result["item"] == "w-1"
    assert f"says: {event_path.stem}" in (root / "w-1.md").read_text()


# ── derived, not written: mark_in_hand / mark_delivered ────────────────


def test_stage_rank_orders_the_lifecycle_and_ranks_terminals_equal():
    assert asks.STAGE_RANK[None] == 0
    assert asks.STAGE_RANK["heard"] < asks.STAGE_RANK["making"]
    assert asks.STAGE_RANK["making"] < asks.STAGE_RANK["delivered"]
    assert (
        asks.STAGE_RANK["accepted"] == asks.STAGE_RANK["reshaped"]
        == asks.STAGE_RANK["sprouted"]
    )


_GITHUB_BASES = {
    "gurio/brr": {"forge": "https://github.com/gurio/brr", "forge_kind": "github", "kb": "https://github.com/gurio/brr-kb/blob/main/knowledge/"},
}


def test_resolve_receipts_reads_both_return_and_receipt_rows_deduped():
    row = {"return": "#123 in git", "receipt": "#123 design-the-ask.md"}
    receipts = asks.resolve_receipts(row, _GITHUB_BASES)
    assert [r["ref"] for r in receipts] == ["#123", "design-the-ask.md"]


def test_resolve_receipts_pr_number_resolves_against_the_one_connected_repo():
    receipts = asks.resolve_receipts({"receipt": "#123"}, _GITHUB_BASES)
    assert receipts == [{"ref": "#123", "url": "https://github.com/gurio/brr/pull/123"}]


def test_resolve_receipts_md_name_resolves_against_the_kb_base():
    receipts = asks.resolve_receipts({"receipt": "design-the-ask.md"}, _GITHUB_BASES)
    assert receipts == [
        {"ref": "design-the-ask.md", "url": "https://github.com/gurio/brr-kb/blob/main/knowledge/design-the-ask.md"},
    ]


def test_resolve_receipts_warp_item_ref_links_the_item_page():
    """`/warp/[id]` exists since #2127, so a `w-N` receipt links to it."""
    receipts = asks.resolve_receipts({"receipt": "w-7"}, _GITHUB_BASES)
    assert receipts == [{"ref": "w-7", "url": "/warp/w-7"}]


def test_resolve_receipts_prose_in_return_is_not_a_receipt():
    """`return:` usually names the return's *kind* (design-the-ask.md's "in
    git"/"in the world"/… table), not a reference — a prose word there must
    not masquerade as a receipt."""
    receipts = asks.resolve_receipts({"return": "in the world", "receipt": "#5"}, _GITHUB_BASES)
    assert [r["ref"] for r in receipts] == ["#5"]


def test_resolve_receipts_unknown_forge_kind_never_guesses_github():
    """#852's lesson: an unresolved forge kind must not default to GitHub."""
    bases = {"g/r": {"forge": "https://internal.example/g/r", "forge_kind": None, "kb": None}}
    receipts = asks.resolve_receipts({"receipt": "#5"}, bases)
    assert receipts == [{"ref": "#5", "url": None}]


def test_resolve_receipts_multi_repo_account_does_not_guess_which_repo():
    """A bare `#123` carries no repo of its own — ambiguous across more than
    one connected repo, so it resolves to no url rather than a plausible
    wrong one (the same ambiguity the-panel-third-pass.md's `runRepoLabel`
    already declined to guess across)."""
    bases = {
        "a/x": {"forge": "https://github.com/a/x", "forge_kind": "github"},
        "b/y": {"forge": "https://github.com/b/y", "forge_kind": "github"},
    }
    receipts = asks.resolve_receipts({"receipt": "#9"}, bases)
    assert receipts == [{"ref": "#9", "url": None}]


def test_resolve_receipts_empty_row_and_no_bases():
    assert asks.resolve_receipts({}, {}) == []
    assert asks.resolve_receipts({"return": "in chat"}, {}) == []
