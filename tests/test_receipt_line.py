"""scripts/receipt_line.py — the README receipt line is rewritten in place, and only when a number moved."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "receipt_line", Path(__file__).resolve().parents[1] / "scripts" / "receipt_line.py",
)
receipt_line = importlib.util.module_from_spec(_SPEC)
sys.modules["receipt_line"] = receipt_line
_SPEC.loader.exec_module(receipt_line)

_LINE = (
    "<strong>2,442 commits on main · 1,640 by the resident · 1,109 merged PRs · "
    "since March 2026.</strong>"
)


def _readme(tmp_path: Path, line: str = _LINE) -> Path:
    p = tmp_path / "README.md"
    p.write_text(f"<h1>brnrd</h1>\n<p align=\"center\">\n  {line}<br>\n  The git log is the demo.\n</p>\n", encoding="utf-8")
    return p


def test_rewrite_replaces_only_the_line_and_stamps_the_date(tmp_path):
    readme = _readme(tmp_path)
    numbers = {"commits": 1941, "resident": 1360, "prs": 1115}
    assert receipt_line.rewrite(readme, numbers, today=date(2026, 9, 6)) is True
    text = readme.read_text(encoding="utf-8")
    assert (
        "<strong>1,941 commits on main · 1,360 by the resident · 1,115 merged PRs · "
        "since March 2026 · as of 2026-09-06.</strong>"
    ) in text
    assert "The git log is the demo." in text
    assert "2,442" not in text


def test_rewrite_is_a_no_op_when_nothing_moved(tmp_path):
    numbers = {"commits": 1941, "resident": 1360, "prs": 1115}
    readme = _readme(tmp_path, receipt_line.render(numbers, today=date(2026, 9, 6)))
    before = readme.read_text(encoding="utf-8")
    assert receipt_line.rewrite(readme, numbers, today=date(2026, 9, 6)) is False
    assert readme.read_text(encoding="utf-8") == before


def test_rewrite_refuses_a_readme_whose_wording_drifted(tmp_path):
    readme = _readme(tmp_path, "<strong>some other sentence</strong>")
    try:
        receipt_line.rewrite(readme, {"commits": 1, "resident": 1, "prs": 1})
    except SystemExit as exc:
        assert "drifted" in str(exc)
    else:
        raise AssertionError("a README without the line must fail loudly, not rewrite nothing")


def test_the_real_readme_carries_the_line():
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert receipt_line._LINE_RE.search(text), "README wording and _LINE_RE drifted apart"
