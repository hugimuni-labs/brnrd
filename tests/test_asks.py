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


def test_parse_accept_reroute_grammar():
    assert asks.parse_accept_reroute("accept w-1") == ("accept", "w-1", None)
    assert asks.parse_accept_reroute("Reroute w-3: too broad, split it") == (
        "reroute", "w-3", "too broad, split it",
    )
    assert asks.parse_accept_reroute("ACCEPT mira") == ("accept", "mira", None)
    assert asks.parse_accept_reroute("I accept your offer") is None
    assert asks.parse_accept_reroute("just a normal message") is None
    assert asks.parse_accept_reroute("") is None
    # a leading blank line doesn't hide the directive on the next one
    assert asks.parse_accept_reroute("\n\naccept w-2") == ("accept", "w-2", None)


def _item(root, item_id, text):
    (root / f"{item_id}.md").write_text(text, encoding="utf-8")


def test_resolve_sign_case_insensitive_and_unmatched(tmp_path):
    root = tmp_path / "warp"
    root.mkdir()
    _item(root, "w-1", "# T\n\nsign: Mira\n")
    assert asks.resolve_sign(root, "mira") == "w-1"
    assert asks.resolve_sign(root, "MIRA") == "w-1"
    assert asks.resolve_sign(root, "nope") is None
    assert asks.resolve_sign(None, "mira") is None
    assert asks.resolve_sign(root, "") is None


def test_apply_inbound_directive_accept_writes_stage_done_and_says(tmp_path):
    root = tmp_path / "warp"
    root.mkdir()
    _item(root, "w-1", "# Ship the thing\n\ntype: action\n")
    result = asks.apply_inbound_directive(root, "evt-1", "accept w-1", date="2026-09-22")
    assert result == {"verb": "accept", "target": "w-1", "item": "w-1", "why": None}
    text = (root / "w-1.md").read_text()
    assert "done: 2026-09-22" in text
    assert "stage: accepted" in text
    assert "says: evt-1" in text
    rows = [
        json.loads(line) for line in (root / ".asks.jsonl").read_text().splitlines()
    ]
    assert rows == [{"event": "evt-1", "item": "w-1", "verb": "accept"}]


def test_apply_inbound_directive_reroute_writes_stage_and_why(tmp_path):
    root = tmp_path / "warp"
    root.mkdir()
    _item(root, "w-2", "# Ship the thing\n\ntype: action\n")
    result = asks.apply_inbound_directive(
        root, "evt-2", "reroute w-2: too broad, split it", date="2026-09-22",
    )
    assert result == {
        "verb": "reroute", "target": "w-2", "item": "w-2", "why": "too broad, split it",
    }
    text = (root / "w-2.md").read_text()
    assert "stage: reshaped" in text
    assert "reroute: too broad, split it" in text
    assert "says: evt-2" in text
    assert "done:" not in text


def test_apply_inbound_directive_resolves_a_callsign(tmp_path):
    root = tmp_path / "warp"
    root.mkdir()
    _item(root, "w-3", "# T\n\ntype: action\nsign: mira\n")
    result = asks.apply_inbound_directive(root, "evt-3", "accept mira")
    assert result["item"] == "w-3"
    assert "stage: accepted" in (root / "w-3.md").read_text()


def test_apply_inbound_directive_unknown_target_is_a_no_op_with_error(tmp_path):
    root = tmp_path / "warp"
    root.mkdir()
    _item(root, "w-1", "# T\n\ntype: action\n")
    result = asks.apply_inbound_directive(root, "evt-9", "accept w-999")
    assert result["item"] is None and result["error"]
    # nothing mutated, nothing recorded
    assert "says:" not in (root / "w-1.md").read_text()
    assert not (root / ".asks.jsonl").exists()

    result2 = asks.apply_inbound_directive(root, "evt-10", "accept zzzznope")
    assert result2["item"] is None and result2["error"]


def test_apply_inbound_directive_ordinary_message_is_none(tmp_path):
    root = tmp_path / "warp"
    root.mkdir()
    _item(root, "w-1", "# T\n\ntype: action\n")
    assert asks.apply_inbound_directive(root, "evt-1", "just checking in") is None


def test_apply_inbound_directive_idempotent_on_redelivery(tmp_path):
    root = tmp_path / "warp"
    root.mkdir()
    _item(root, "w-1", "# T\n\ntype: action\n")
    first = asks.apply_inbound_directive(root, "evt-1", "accept w-1", date="2026-09-22")
    assert first.get("idempotent") is not True
    second = asks.apply_inbound_directive(root, "evt-1", "accept w-1", date="2026-09-22")
    assert second["idempotent"] is True
    text = (root / "w-1.md").read_text()
    assert text.count("evt-1") == 1  # says: row stamped once, not twice
    rows = (root / ".asks.jsonl").read_text().splitlines()
    assert len(rows) == 1  # the jsonl row was written once, not twice


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


def test_list_asks_folds_in_the_account_level_directive_row(tmp_path):
    """The inbound `accept`/`reroute` hook's own row (`asks.apply_inbound_directive`,
    written straight to `warp/.asks.jsonl` since no run owns the event yet)
    reaches the LRU the same way a run's own `.asks.jsonl` binding does."""
    surface = tmp_path / "surface"
    (surface / "warp").mkdir(parents=True)
    (surface / "warp" / "w-1.md").write_text("# On disk\n\ntype: action\n")
    (surface / "warp" / ".asks.jsonl").write_text(
        json.dumps({"event": "evt-9", "item": "w-1", "verb": "accept"}) + "\n"
    )
    payload = asks.list_asks(surface)
    row = payload["asks"][0]
    assert {s["event"] for s in row["says"]} == {"evt-9"}
    assert row["touched_at"] is not None  # the binding file's own mtime carries it


# ── derived, not written: mark_in_hand / mark_delivered ────────────────


def _make_item(tmp_path, item_id: str, text: str):
    from brr import items as items_mod

    root = tmp_path / "surface" / "warp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{item_id}.md"
    path.write_text(text, encoding="utf-8")
    return root, path


def test_mark_in_hand_stamps_attempts_and_advances_to_making(tmp_path):
    root, path = _make_item(tmp_path, "w-1", "# T\n\ntype: action\n")
    assert asks.mark_in_hand(root, "w-1", run_id="run-a")
    text = path.read_text(encoding="utf-8")
    assert "attempts: run-a" in text and "stage: making" in text
    # idempotent: a second stamp with the same run id is a no-op
    assert not asks.mark_in_hand(root, "w-1", run_id="run-a")
    # a second run's attempt appends, stage stays making
    assert asks.mark_in_hand(root, "w-1", run_id="run-b")
    assert "attempts: run-a run-b" in path.read_text(encoding="utf-8")


def test_mark_in_hand_never_moves_stage_backward(tmp_path):
    root, path = _make_item(tmp_path, "w-1", "# T\n\ntype: action\nstage: delivered\n")
    assert asks.mark_in_hand(root, "w-1", run_id="run-a")  # attempts: still stamps
    text = path.read_text(encoding="utf-8")
    assert "stage: delivered" in text and "stage: making" not in text


def test_mark_in_hand_unresolvable_or_blank_run_id_is_false(tmp_path):
    root, _path = _make_item(tmp_path, "w-1", "# T\n\ntype: action\n")
    assert not asks.mark_in_hand(root, "w-999", run_id="run-a")
    assert not asks.mark_in_hand(root, "w-1", run_id="")
    assert not asks.mark_in_hand(None, "w-1", run_id="run-a")


def test_mark_delivered_sets_stage_and_first_return(tmp_path):
    root, path = _make_item(tmp_path, "w-1", "# T\n\ntype: action\n")
    assert asks.mark_delivered(root, "w-1", receipt="owner/repo#42")
    text = path.read_text(encoding="utf-8")
    assert "stage: delivered" in text and "return: owner/repo#42" in text


def test_mark_delivered_never_overwrites_an_existing_return(tmp_path):
    root, path = _make_item(tmp_path, "w-1", "# T\n\ntype: action\nreturn: in chat\n")
    assert asks.mark_delivered(root, "w-1", receipt="owner/repo#42")
    text = path.read_text(encoding="utf-8")
    assert "stage: delivered" in text
    assert "return: in chat" in text and "owner/repo#42" not in text


def test_mark_delivered_blank_receipt_or_unresolvable_is_false(tmp_path):
    root, _path = _make_item(tmp_path, "w-1", "# T\n\ntype: action\n")
    assert not asks.mark_delivered(root, "w-1", receipt="")
    assert not asks.mark_delivered(root, "w-999", receipt="owner/repo#1")


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
