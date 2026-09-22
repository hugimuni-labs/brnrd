"""The list of asks: LRU order, the stale horizon, done apart, the disk door."""

from __future__ import annotations

import datetime as dt
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
    rows = asks.list_asks(surface)
    assert [r["id"] for r in rows] == ["w-1"]
    assert rows[0]["touched_at"].startswith("2026-09-21T12:00")
    assert [r["id"] for r in asks.list_asks(surface, include_done=True)] == ["w-1", "w-2"]
    assert asks.list_asks(tmp_path / "nowhere") == []
