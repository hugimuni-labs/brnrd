"""The list of asks: LRU order, the stale horizon, done apart, the disk door."""

from __future__ import annotations

import datetime as dt
import json
import subprocess

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


def test_list_asks_reads_disk_with_git_touch_times(tmp_path):
    surface = tmp_path / "surface"
    (surface / "warp").mkdir(parents=True)
    (surface / "warp" / "w-1.md").write_text("# On disk\n\ntype: action\n")
    (surface / "warp" / "w-2.md").write_text("# Finished\n\ndone: 2026-09-01\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_COMMITTER_DATE": "2026-09-21T12:00:00Z",
           "GIT_AUTHOR_DATE": "2026-09-21T12:00:00Z", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True, env=env)
    payload = asks.list_asks(surface)
    assert [r["id"] for r in payload["asks"]] == ["w-1"]
    assert payload["asks"][0]["touched_at"].startswith("2026-09-21T12:00")
    assert [r["id"] for r in payload["done"]] == ["w-2"]
    empty = asks.list_asks(tmp_path / "nowhere")
    assert empty == {"asks": [], "done": [], "goals": [], "stale_after_days": asks.DEFAULT_STALE_AFTER_DAYS}


def test_list_asks_folds_in_runs_dir_and_outbox_bindings(tmp_path):
    surface = tmp_path / "surface"
    (surface / "warp").mkdir(parents=True)
    (surface / "warp" / "w-1.md").write_text("# On disk\n\ntype: action\n")
    run = tmp_path / "runs" / "repo" / "run-1"
    run.mkdir(parents=True)
    (run / "asks.jsonl").write_text(json.dumps({"event": "evt-1", "item": "w-1"}) + "\n")
    outbox = tmp_path / "outbox" / "evt-2"
    outbox.mkdir(parents=True)
    (outbox / ".asks.jsonl").write_text(json.dumps({"event": "evt-2", "item": "w-1"}) + "\n")
    payload = asks.list_asks(
        surface, runs_dir=tmp_path / "runs", outbox_root=tmp_path / "outbox",
    )
    row = payload["asks"][0]
    assert row["id"] == "w-1"
    assert {s["event"] for s in row["says"]} == {"evt-1", "evt-2"}
    assert row["touched_at"] is not None  # no git repo here — the binding files' own mtime carries it


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


def test_resolve_receipts_warp_item_ref_has_no_url_yet():
    """No dashboard page resolves one warp item to a URL today — same
    discipline `_enrich_ask_says` already applies to a say's own `url`."""
    receipts = asks.resolve_receipts({"receipt": "w-7"}, _GITHUB_BASES)
    assert receipts == [{"ref": "w-7", "url": None}]


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
