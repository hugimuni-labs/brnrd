"""The legal pack's integrity claim has to be checkable, or it is decoration.

``docs/legal/export/SHA256SUMS`` tells counsel *these are the exact bytes you
reviewed*. Between 2026-07-27 and 2026-07-30 six of its twenty entries pinned
bytes that no longer existed — including the DPA and the Article 30 record —
and nothing anywhere noticed, because nothing recomputed them. This module is
that something.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "legal_manifest.py"


def _module():
    spec = importlib.util.spec_from_file_location("legal_manifest_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_pack_pins_the_bytes_that_are_actually_there():
    problems = _module().check()
    assert not problems, (
        "the legal export pack no longer pins the current files:\n  "
        + "\n  ".join(problems)
        + "\n\nReview the changes, then run: python scripts/legal_manifest.py --write"
    )


def test_every_legal_document_is_pinned():
    """A new page under docs/legal/ must not be able to join unpinned."""
    assert _module().unpinned_legal_docs() == []


def test_drift_in_one_byte_is_caught(tmp_path, monkeypatch):
    """The check must fail on a real change, not merely on a missing file."""
    module = _module()
    target = REPO_ROOT / "docs" / "legal" / "dpa.md"
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n<!-- drift -->\n")
        problems = module.check()
        assert any("dpa.md" in problem for problem in problems), problems
    finally:
        target.write_bytes(original)
    assert module.check() == []
