"""Tests for the constitution template machinery (L1 blocks + L2 bridges)."""

import pytest

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


def test_parse_blocks_reads_id_version_and_body():
    blocks = C.parse_blocks(_DOC)
    assert [b.id for b in blocks] == ["stewardship", "guardrails"]
    assert blocks[0].version == 2
    assert "be a good steward" in blocks[0].body


def test_compute_hash_is_edge_whitespace_insensitive():
    assert C.compute_hash("body") == C.compute_hash("\n  body \n")
    assert C.compute_hash("body") != C.compute_hash("body!")
    assert len(C.compute_hash("body")) == C.HASH_LEN


def test_stamp_then_verify_is_clean():
    stamped = C.stamp(_DOC)
    assert "hash=PENDING" not in stamped
    res = C.verify(stamped)
    assert res.ok, (res.mismatches, res.pending, res.error)


def test_stamp_is_idempotent():
    once = C.stamp(_DOC)
    assert C.stamp(once) == once


def test_verify_flags_pending_and_tampered():
    stamped = C.stamp(_DOC)
    # PENDING placeholder is unverified, not a match.
    res_pending = C.verify(_DOC)
    assert not res_pending.ok
    assert set(res_pending.pending) == {"stewardship", "guardrails"}
    # Tamper a block body after stamping ⇒ hash mismatch.
    tampered = stamped.replace("be a good steward", "be a bad steward")
    res = C.verify(tampered)
    assert not res.ok
    assert [m.id for m in res.mismatches] == ["stewardship"]


def test_parse_rejects_structural_faults():
    with pytest.raises(C.ConstitutionError):
        C.parse_blocks("<!-- brnrd:block id=x v=1 hash=abc -->\nunclosed\n")
    with pytest.raises(C.ConstitutionError):
        C.parse_blocks("<!-- /brnrd:block -->")
    dup = (
        "<!-- brnrd:block id=x v=1 hash=abc -->\na\n<!-- /brnrd:block -->\n"
        "<!-- brnrd:block id=x v=1 hash=abc -->\nb\n<!-- /brnrd:block -->\n"
    )
    with pytest.raises(C.ConstitutionError):
        C.parse_blocks(dup)


def test_verify_reports_structural_fault_as_error():
    res = C.verify("<!-- brnrd:block id=x v=1 hash=abc -->\nunclosed\n")
    assert not res.ok and res.error


def test_block_drift_compares_by_identity_not_whole_file():
    template = C.stamp(_DOC)
    # Adopter kept the blocks verbatim but heavily tailored per-repo prose.
    installed = template.replace(
        "per-repo prose that must never count as drift",
        "COMPLETELY DIFFERENT project description with many words",
    ).replace("middle per-repo material", "other tailoring entirely")
    assert C.block_drift(installed, template) == []
    # Now the template bumps the stewardship block; the adopter lags.
    newer = C.stamp(
        template.replace(
            "<!-- brnrd:block id=stewardship v=2 hash=", "PLACEHOLDER"
        ).replace("be a good steward", "steward with judgement")
        .replace("PLACEHOLDER", "<!-- brnrd:block id=stewardship v=3 hash=")
    )
    drift = C.block_drift(installed, newer)
    assert [d.id for d in drift] == ["stewardship"]
    assert drift[0].installed_version == 2 and drift[0].template_version == 3


def test_block_drift_empty_when_no_blocks_present():
    assert C.block_drift("plain\n", "also plain\n") == []


def test_packaged_template_verifies():
    """The shipped template must always be stamped (no PENDING, no mismatch)."""
    res = C.verify_template()
    assert res.ok, (res.mismatches, res.pending, res.error)
    ids = {b.id for b in C.parse_blocks(C.TEMPLATE_PATH.read_text(encoding="utf-8"))}
    assert {"stewardship", "knowledge", "guardrails"} <= ids


def test_template_carries_no_retired_architecture():
    """L0: the template must not ship brr-specific or stale-arch claims."""
    text = C.TEMPLATE_PATH.read_text(encoding="utf-8").lower()
    assert ".brnrd-kb/" not in text
    assert "gurio/brr" not in text
    assert "src/brr/docs" not in text


# ── Shell bridges (L2) ───────────────────────────────────────────────


def test_bridge_filename_and_content_per_shell():
    assert C.bridge_filename("claude") == "CLAUDE.md"
    assert C.bridge_filename("gemini") == "GEMINI.md"
    assert C.bridge_filename("codex") is None
    assert C.bridge_filename("cursor") is None
    assert "@AGENTS.md" in C.bridge_content("claude")
    assert C.bridge_content("codex") is None


def test_write_bridges_writes_only_shells_that_need_one(tmp_path):
    (tmp_path / "AGENTS.md").write_text("contract", encoding="utf-8")
    written = C.write_bridges(tmp_path, ["claude", "gemini", "codex", "cursor"])
    assert set(written) == {"claude", "gemini"}
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / "GEMINI.md").exists()
    assert "@AGENTS.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_write_bridges_is_idempotent_and_nondestructive(tmp_path):
    (tmp_path / "AGENTS.md").write_text("contract", encoding="utf-8")
    C.write_bridges(tmp_path, ["claude"])
    # Second call: bridge already points at the contract ⇒ nothing rewritten.
    assert C.write_bridges(tmp_path, ["claude"]) == []


def test_reachability_true_when_bridge_points_at_contract(tmp_path):
    (tmp_path / "AGENTS.md").write_text("contract", encoding="utf-8")
    C.write_bridges(tmp_path, ["claude"])
    r = C.verify_reachability(tmp_path, "claude")
    assert r.reachable and "CLAUDE.md" in r.detail


def test_reachability_native_shell_needs_only_contract(tmp_path):
    (tmp_path / "AGENTS.md").write_text("contract", encoding="utf-8")
    assert C.verify_reachability(tmp_path, "codex").reachable
    assert C.verify_reachability(tmp_path, "cursor").reachable


def test_reachability_false_when_contract_absent(tmp_path):
    C.write_bridges(tmp_path, ["claude"])  # bridge written, but no AGENTS.md
    r = C.verify_reachability(tmp_path, "claude")
    assert not r.reachable and "AGENTS.md" in r.detail


def test_reachability_false_when_bridge_missing_or_not_pointing(tmp_path):
    (tmp_path / "AGENTS.md").write_text("contract", encoding="utf-8")
    r = C.verify_reachability(tmp_path, "claude")
    assert not r.reachable and "bridge missing" in r.detail
    # A bridge file that does not import the contract is unreachable.
    (tmp_path / "CLAUDE.md").write_text("unrelated notes\n", encoding="utf-8")
    r2 = C.verify_reachability(tmp_path, "claude")
    assert not r2.reachable and "does not point" in r2.detail


def test_reachability_accepts_symlink_bridge(tmp_path):
    (tmp_path / "AGENTS.md").write_text("contract", encoding="utf-8")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")
    assert C.verify_reachability(tmp_path, "claude").reachable
