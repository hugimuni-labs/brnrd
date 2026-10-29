"""Tests for the constitution template machinery (L1 blocks + L2 bridges)."""


from brr import constitution as C


# ── Versioned blocks (L1) ────────────────────────────────────────────

_DOC = """# Project

per-repo prose that must never count as drift

<!-- brnrd:block id=stewardship v=2 hash=PENDING -->
## Stewardship
be a good steward
<!-- /brnrd:block -->

middle per-repo material

<!-- brnrd:block id=guardrails v=1 hash=PENDING -->
## Guardrails
do not leak secrets
<!-- /brnrd:block -->
"""


def test_stamp_is_idempotent():
    once = C.stamp(_DOC)
    assert C.stamp(once) == once


def test_template_carries_no_retired_architecture():
    """L0: the template must not ship brr-specific or stale-arch claims."""
    text = C.TEMPLATE_PATH.read_text(encoding="utf-8").lower()
    assert ".brnrd-kb/" not in text
    assert "gurio/brr" not in text
    assert "src/brr/docs" not in text


# ── Shell bridges (L2) ───────────────────────────────────────────────


