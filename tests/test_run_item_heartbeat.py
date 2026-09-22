"""``.item`` folded in through the real ``daemon._frame_heartbeat`` path —
the same chokepoint ``.topic`` settles at, once per heartbeat (design-the-
ask.md §Build cut step 4, his 2026-09-22 steer).
"""

from __future__ import annotations

import time
from pathlib import Path

from brr import account, daemon, heddles, items, protocol
from brr.run import Run


def test_frame_heartbeat_folds_item_control_into_attempts_and_stage(tmp_path):
    heddles.reset_cache()
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = account.resolve_context(
        repo, {"home.path": str(tmp_path / "home"), "repo.label": "hugimuni-labs/brnrd"},
    )
    warp = account.work_surface_path(ctx) / "warp"
    warp.mkdir(parents=True)
    (warp / "w-1.md").write_text(items.new_item_text("Ship it", item_type="action"), encoding="utf-8")

    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "telegram", "x", status="processing",
                                telegram_user_id="42", telegram_chat_id="42")
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    (outbox / ".item").write_text("w-1\n", encoding="utf-8")

    task = Run(id="run-seat", event_id=own.stem, body="x", source="telegram",
               meta={"repo_label": "hugimuni-labs/brnrd"})
    task.conversation_key = "telegram:42:"

    daemon._frame_heartbeat(
        task, outbox_dir=outbox, card_state={}, output_stats={"current": 0},
        brr_dir=brr_dir, account_context=ctx, repo_label="hugimuni-labs/brnrd",
        work_dir=repo, repo_root=repo,
    )

    text = (warp / "w-1.md").read_text(encoding="utf-8")
    assert "attempts: run-seat" in text
    assert "stage: making" in text
    assert task.meta.get("run_item") == "w-1"

    # a second heartbeat with the same control is a no-op — no duplicate
    # attempts row, no repeated write.
    before = text
    daemon._frame_heartbeat(
        task, outbox_dir=outbox, card_state={}, output_stats={"current": 0},
        brr_dir=brr_dir, account_context=ctx, repo_label="hugimuni-labs/brnrd",
        work_dir=repo, repo_root=repo,
    )
    assert (warp / "w-1.md").read_text(encoding="utf-8") == before


def test_frame_heartbeat_with_no_item_control_leaves_the_warp_untouched(tmp_path):
    heddles.reset_cache()
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = account.resolve_context(
        repo, {"home.path": str(tmp_path / "home"), "repo.label": "hugimuni-labs/brnrd"},
    )
    warp = account.work_surface_path(ctx) / "warp"
    warp.mkdir(parents=True)
    (warp / "w-1.md").write_text(items.new_item_text("Ship it", item_type="action"), encoding="utf-8")

    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "telegram", "x", status="processing",
                                telegram_user_id="42", telegram_chat_id="42")
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    # no .item written

    task = Run(id="run-seat", event_id=own.stem, body="x", source="telegram",
               meta={"repo_label": "hugimuni-labs/brnrd"})
    task.conversation_key = "telegram:42:"

    daemon._frame_heartbeat(
        task, outbox_dir=outbox, card_state={}, output_stats={"current": 0},
        brr_dir=brr_dir, account_context=ctx, repo_label="hugimuni-labs/brnrd",
        work_dir=repo, repo_root=repo,
    )
    text = (warp / "w-1.md").read_text(encoding="utf-8")
    assert "attempts:" not in text and "stage:" not in text
