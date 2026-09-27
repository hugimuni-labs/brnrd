"""body-version: stamp — resident acknowledges State/Next update; daemon detects
drift.

Covers:
- ``asks.stamp_body_version`` writes ``body-version: <hash>`` to frontmatter
- ``asks.body_version_stale`` returns ``True`` when body changes since stamp
- ``run_item.settle`` emits a notice on first-stale transition, not every tick
- ``brnrd item stamp`` CLI command writes the stamp and prints the digest
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import asks, items, run_item
from brr.run import Run


# ── fixtures ─────────────────────────────────────────────────────────────────


def _warp(tmp_path: Path) -> Path:
    root = tmp_path / "surface" / "warp"
    root.mkdir(parents=True)
    return root


def _item_file(root: Path, item_id: str, body: str = "") -> Path:
    """Write a minimal warp item with an optional prose body."""
    headline = "Test item"
    text = items.new_item_text(headline, item_type="action")
    if body:
        text = text.rstrip() + f"\n\n{body}\n"
    path = root / f"{item_id}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _task(meta: dict | None = None) -> Run:
    from brr import protocol
    task = Run(
        id="run-test-0001",
        event_id="evt-0001",
        body="hello",
        source="telegram",
        env="host",
        conversation_key="cloud:telegram:1:",
    )
    if meta:
        task.meta.update(meta)
    return task


# ── stamp_body_version ────────────────────────────────────────────────────────


def test_stamp_writes_body_version_to_frontmatter(tmp_path):
    root = _warp(tmp_path)
    _item_file(root, "w-1", body="## Answer\n\n**State:** doing it **Next:** continue")
    digest = asks.stamp_body_version(root, "w-1")
    assert digest is not None
    assert len(digest) == asks._BODY_HASH_LEN
    text = (root / "w-1.md").read_text(encoding="utf-8")
    assert f"body-version: {digest}" in text


def test_stamp_returns_none_on_missing_item(tmp_path):
    root = _warp(tmp_path)
    # No w-99.md
    assert asks.stamp_body_version(root, "w-99") is None


def test_stamp_is_idempotent(tmp_path):
    root = _warp(tmp_path)
    _item_file(root, "w-1", body="## Answer\n\nsome content")
    digest1 = asks.stamp_body_version(root, "w-1")
    digest2 = asks.stamp_body_version(root, "w-1")
    assert digest1 == digest2
    # Only one body-version: line
    text = (root / "w-1.md").read_text(encoding="utf-8")
    assert text.count("body-version:") == 1


# ── body_version_stale ────────────────────────────────────────────────────────


def test_not_stale_when_no_stamp_set(tmp_path):
    root = _warp(tmp_path)
    _item_file(root, "w-1", body="## Answer\n\ncontent")
    # No stamp written → not stale (opted out)
    assert asks.body_version_stale(root, "w-1") is False


def test_not_stale_right_after_stamp(tmp_path):
    root = _warp(tmp_path)
    _item_file(root, "w-1", body="## Answer\n\ncontent")
    asks.stamp_body_version(root, "w-1")
    assert asks.body_version_stale(root, "w-1") is False


def test_stale_when_body_changes_after_stamp(tmp_path):
    root = _warp(tmp_path)
    path = _item_file(root, "w-1", body="## Answer\n\noriginal content")
    asks.stamp_body_version(root, "w-1")
    # Now change the body without re-stamping
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("original content", "updated content"), encoding="utf-8")
    assert asks.body_version_stale(root, "w-1") is True


def test_not_stale_after_re_stamp_following_body_change(tmp_path):
    root = _warp(tmp_path)
    path = _item_file(root, "w-1", body="## Answer\n\noriginal")
    asks.stamp_body_version(root, "w-1")
    # Change body
    path.write_text(
        path.read_text(encoding="utf-8").replace("original", "updated"),
        encoding="utf-8",
    )
    # Stale
    assert asks.body_version_stale(root, "w-1") is True
    # Re-stamp
    asks.stamp_body_version(root, "w-1")
    assert asks.body_version_stale(root, "w-1") is False


# ── run_item.settle staleness notice ─────────────────────────────────────────


def test_settle_emits_advisory_on_first_stale(tmp_path):
    root = _warp(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".item").write_text("w-1\n", encoding="utf-8")
    path = _item_file(root, "w-1", body="## Answer\n\noriginal")
    # Stamp first
    asks.stamp_body_version(root, "w-1")
    # Change body without re-stamping
    path.write_text(
        path.read_text(encoding="utf-8").replace("original", "updated"),
        encoding="utf-8",
    )
    task = _task()
    notices: list[tuple[str, str]] = []
    run_item.settle(task, outbox_dir=outbox, warp_root=root,
                    notice=lambda k, t: notices.append((k, t)))
    assert any(k == "advisory" and "w-1" in t for k, t in notices), \
        f"Expected advisory notice for w-1, got: {notices}"


def test_settle_does_not_repeat_advisory_on_subsequent_ticks(tmp_path):
    root = _warp(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".item").write_text("w-1\n", encoding="utf-8")
    path = _item_file(root, "w-1", body="## Answer\n\noriginal")
    asks.stamp_body_version(root, "w-1")
    path.write_text(
        path.read_text(encoding="utf-8").replace("original", "updated"),
        encoding="utf-8",
    )
    task = _task()
    notices: list[tuple[str, str]] = []
    say = lambda k, t: notices.append((k, t))
    # First tick: notice fires
    run_item.settle(task, outbox_dir=outbox, warp_root=root, notice=say)
    first_count = len(notices)
    # Second tick: no new notice (already stale, no transition)
    run_item.settle(task, outbox_dir=outbox, warp_root=root, notice=say)
    assert len(notices) == first_count, "Advisory should not repeat on subsequent ticks"


def test_settle_no_notice_when_no_stamp(tmp_path):
    """No stamp on the item → staleness not checked → no notice."""
    root = _warp(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".item").write_text("w-1\n", encoding="utf-8")
    _item_file(root, "w-1", body="## Answer\n\ncontent")
    # No stamp
    task = _task()
    notices: list[tuple[str, str]] = []
    run_item.settle(task, outbox_dir=outbox, warp_root=root,
                    notice=lambda k, t: notices.append((k, t)))
    advisory = [(k, t) for k, t in notices if k == "advisory"]
    assert not advisory, f"Unexpected advisory notices: {advisory}"


# ── CLI: brnrd item stamp ─────────────────────────────────────────────────────


def test_cli_item_stamp_writes_hash(tmp_path, monkeypatch):
    """``brnrd item stamp w-1`` hashes the body and prints the digest."""
    from brr import cli as cli_mod

    root = _warp(tmp_path)
    _item_file(root, "w-1", body="## Answer\n\n**State:** X **Next:** Y")
    monkeypatch.setenv("BRR_SHARED_DIR", str(tmp_path / ".brr"))
    monkeypatch.setattr(
        cli_mod, "_item_context",
        lambda: (root, None),
    )

    class FakeArgs:
        id = "w-1"

    captured = []
    monkeypatch.setattr("builtins.print", lambda *a, **kw: captured.append(" ".join(str(x) for x in a)))
    result = cli_mod.cmd_item_stamp(FakeArgs())
    assert result == 0
    assert any("w-1" in line and "body-version:" in line for line in captured)
    # The stamp was written
    assert asks.body_version_stale(root, "w-1") is False
