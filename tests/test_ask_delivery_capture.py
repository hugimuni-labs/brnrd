"""design-the-ask.md's steer (2026-09-22), (c): "at run end, if the run's
produce names the item … the daemon sets `stage: delivered` and adds the
PR to `return:`". ``daemon._ask_delivery_capture``/``_ask_commit_ref``
scan the same commit/merge relic records THE WELD's own capture half
already collects (``daemon._weld_capture``, sibling call, zero extra
I/O) — these tests drive the two functions directly with the same
relic-record shapes ``test_weld.py`` already exercises ``weld.capture_refs``
against.
"""

from __future__ import annotations

from pathlib import Path

from brr import daemon, items


def _warp(tmp_path: Path) -> Path:
    root = tmp_path / "surface" / "warp"
    root.mkdir(parents=True)
    return root


def _item(root: Path, item_id: str, text: str = "# T\n\ntype: action\n") -> Path:
    path = root / f"{item_id}.md"
    path.write_text(text, encoding="utf-8")
    return path


# ── _ask_commit_ref ──────────────────────────────────────────────────────


def test_ask_commit_ref_merge_with_pr_and_repo():
    record = {"kind": "merge", "pr": 42, "repo": "owner/repo", "subject": "Merge pull request #42"}
    assert daemon._ask_commit_ref(record, None) == "owner/repo#42"


def test_ask_commit_ref_merge_with_pr_falls_back_to_origin():
    record = {"kind": "merge", "pr": 7, "subject": "Merge pull request #7"}
    assert daemon._ask_commit_ref(record, "owner/repo") == "owner/repo#7"


def test_ask_commit_ref_plain_commit_uses_its_url():
    record = {"kind": "commit", "sha": "abc123", "subject": "w-1: fix", "url": "https://x/commit/abc123"}
    assert daemon._ask_commit_ref(record, None) == "https://x/commit/abc123"


def test_ask_commit_ref_nothing_attested_is_none():
    assert daemon._ask_commit_ref({"kind": "commit", "sha": "abc"}, None) is None
    assert daemon._ask_commit_ref({"kind": "merge", "pr": 1}, None) is None  # no repo anywhere


# ── _ask_delivery_capture ────────────────────────────────────────────────


def test_delivery_capture_from_a_commit_message(tmp_path):
    root = _warp(tmp_path)
    _item(root, "w-1")
    records = [
        {"kind": "commit", "sha": "abc123", "subject": "w-1: fix the fuel gauge",
         "url": "https://example/commit/abc123"},
    ]
    daemon._ask_delivery_capture(root, records=records, origin_repo=None, run_id="run-1")
    text = (root / "w-1.md").read_text(encoding="utf-8")
    assert "stage: delivered" in text
    assert "return: https://example/commit/abc123" in text


def test_delivery_capture_from_a_merged_pr_subject(tmp_path):
    root = _warp(tmp_path)
    _item(root, "w-2")
    # `w-2` must sit at a real token boundary — "w-2-branch" would be one
    # longer token to `scan_item_addresses` and never match at all (the
    # same boundary rule `items.py`'s own scanner tests pin).
    records = [
        {"kind": "merge", "pr": 99, "subject": "Merge pull request #99 (w-2)",
         "sha": "def456"},
    ]
    daemon._ask_delivery_capture(root, records=records, origin_repo="owner/repo", run_id="run-1")
    text = (root / "w-2.md").read_text(encoding="utf-8")
    assert "stage: delivered" in text
    assert "return: owner/repo#99" in text


def test_delivery_capture_ignores_a_subject_with_no_w_n_mention(tmp_path):
    root = _warp(tmp_path)
    path = _item(root, "w-1")
    before = path.read_text(encoding="utf-8")
    records = [{"kind": "commit", "sha": "abc", "subject": "fix the fuel gauge", "url": "https://x/abc"}]
    daemon._ask_delivery_capture(root, records=records, origin_repo=None, run_id="run-1")
    assert path.read_text(encoding="utf-8") == before


def test_delivery_capture_skips_an_unresolvable_item(tmp_path):
    root = _warp(tmp_path)
    records = [{"kind": "commit", "sha": "def", "subject": "w-999 does not exist", "url": "https://x/def"}]
    daemon._ask_delivery_capture(root, records=records, origin_repo=None, run_id="run-1")
    assert not (root / "w-999.md").exists()


def test_delivery_capture_a_done_item_still_gains_delivered_and_return(tmp_path):
    """`done:` (a run's own receipt) and `stage: delivered` are not in
    tension — a done item with no ask `stage:` row yet still deserves the
    backfill a later commit's own mention provides."""
    root = _warp(tmp_path)
    path = _item(root, "w-1")
    items.mark_done(path, date="2026-09-22")
    records = [{"kind": "commit", "sha": "abc", "subject": "w-1 already done", "url": "https://x/abc"}]
    daemon._ask_delivery_capture(root, records=records, origin_repo=None, run_id="run-1")
    text = path.read_text(encoding="utf-8")
    assert "stage: delivered" in text and "return: https://x/abc" in text


def test_delivery_capture_skips_a_retired_item(tmp_path):
    """Retirement is abandonment — confirming delivery on withdrawn work
    would contradict the retirement, not confirm it."""
    root = _warp(tmp_path)
    path = _item(root, "w-1")
    items.mark_retired(path, date="2026-09-22", why="superseded")
    before = path.read_text(encoding="utf-8")
    records = [{"kind": "commit", "sha": "abc", "subject": "w-1: closing", "url": "https://x/abc"}]
    daemon._ask_delivery_capture(root, records=records, origin_repo=None, run_id="run-1")
    assert path.read_text(encoding="utf-8") == before


def test_delivery_capture_never_overwrites_an_existing_return(tmp_path):
    root = _warp(tmp_path)
    _item(root, "w-1", "# T\n\ntype: action\nreturn: in chat\n")
    records = [{"kind": "commit", "sha": "abc", "subject": "w-1: done", "url": "https://x/abc"}]
    daemon._ask_delivery_capture(root, records=records, origin_repo=None, run_id="run-1")
    text = (root / "w-1.md").read_text(encoding="utf-8")
    assert "stage: delivered" in text
    assert "return: in chat" in text and "https://x/abc" not in text


def test_delivery_capture_no_warp_root_is_a_silent_no_op():
    daemon._ask_delivery_capture(None, records=[{"kind": "commit", "subject": "w-1"}],
                                 origin_repo=None, run_id="run-1")  # must not raise


def test_delivery_capture_never_raises_on_a_malformed_record(tmp_path, capsys):
    root = _warp(tmp_path)
    _item(root, "w-1")
    records = ["not-a-dict", {"kind": "commit"}, {"kind": "commit", "subject": None}]
    daemon._ask_delivery_capture(root, records=records, origin_repo=None, run_id="run-1")
    # no crash; nothing to deliver from any of these malformed/empty rows
    assert "stage:" not in (root / "w-1.md").read_text(encoding="utf-8")
