"""Tests for the deterministic kb consistency preflight."""

from pathlib import Path

from brr import kb_preflight


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_scan_returns_empty_when_kb_missing(tmp_path):
    assert kb_preflight.scan(tmp_path) == []


def test_scan_returns_empty_for_consistent_kb(tmp_path):
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Subject](decision-foo.md) — desc\n"
    ))
    _write(
        tmp_path / "kb" / "decision-foo.md",
        "# Foo\n\nStatus: accepted on 2026-04-01\n\nBody.\n",
    )
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    assert kb_preflight.scan(tmp_path) == []


def test_scan_accepts_relative_repo_root(tmp_path, monkeypatch):
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Subject](decision-foo.md) — desc\n"
    ))
    _write(
        tmp_path / "kb" / "decision-foo.md",
        "# Foo\n\nStatus: accepted on 2026-04-01\n\nBody.\n",
    )
    _write(tmp_path / "kb" / "log.md", "# Log\n")
    monkeypatch.chdir(tmp_path)

    assert kb_preflight.scan(Path(".")) == []


def test_scan_flags_pages_missing_from_index(tmp_path):
    _write(tmp_path / "kb" / "index.md", "# Index\n\n(no entries)\n")
    _write(tmp_path / "kb" / "decision-foo.md", "# Foo\n")
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert any(
        f.type == "missing-from-index" and f.target == "kb/decision-foo.md"
        for f in findings
    )


def test_scan_exempts_index_and_log_from_index_check(tmp_path):
    _write(tmp_path / "kb" / "index.md", "# Index\n")
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert all(
        f.target not in ("kb/index.md", "kb/log.md")
        for f in findings
    )


def test_scan_flags_stale_index_entries(tmp_path):
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Gone](decision-gone.md) — was here once\n"
    ))
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    stale = [f for f in findings if f.type == "stale-index-entry"]
    assert any("decision-gone.md" in f.target for f in stale), findings


def test_scan_flags_broken_links_in_other_pages(tmp_path):
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Foo](decision-foo.md) — desc\n"
    ))
    _write(tmp_path / "kb" / "decision-foo.md", (
        "See [missing](missing-page.md).\n"
    ))
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    broken = [f for f in findings if f.type == "broken-link"]
    assert any("decision-foo.md" in f.target for f in broken), findings
    assert any("missing-page.md" in f.target for f in broken), findings


def test_scan_skips_broken_links_inside_log(tmp_path):
    _write(tmp_path / "kb" / "index.md", "# Index\n")
    _write(tmp_path / "kb" / "log.md", (
        "# Log\n\n"
        "Mentions a now-slashed `kb/old-review.md` for historical narrative.\n"
        "Including a real markdown link [old](old-review.md) in prose.\n"
    ))

    findings = kb_preflight.scan(tmp_path)

    assert all(f.type != "broken-link" for f in findings), findings


def test_scan_ignores_external_urls(tmp_path):
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Page](decision-foo.md) — has external links\n"
    ))
    _write(tmp_path / "kb" / "decision-foo.md", (
        "Status: accepted on 2026-04-01\n\n"
        "See [docs](https://example.com/foo) and "
        "[mailto](mailto:a@b.c) — these aren't kb pages.\n"
    ))
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert findings == []


def test_scan_ignores_anchor_only_fragments(tmp_path):
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Foo](decision-foo.md) — desc\n"
    ))
    _write(tmp_path / "kb" / "decision-foo.md", (
        "Status: accepted on 2026-04-01\n\n"
        "Jump to [section](#some-anchor) within this page.\n"
    ))
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert findings == []


def test_scan_flags_oversized_pages_as_warning(tmp_path):
    """Pages past the readability threshold get an advisory so the
    maintenance pass can consider splitting or compressing them.
    ``log.md`` is exempt because it grows monotonically by design.
    """
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Big](subject-big.md) — desc\n"
    ))
    # Generate a page comfortably past the 32K threshold.
    big_body = "x " * 20_000
    _write(
        tmp_path / "kb" / "subject-big.md",
        f"# Big subject\n\nStatus: accepted on 2026-04-01\n\n{big_body}\n",
    )
    _write(tmp_path / "kb" / "log.md", "# Log\n" + ("y " * 20_000))

    findings = kb_preflight.scan(tmp_path)

    oversized = [f for f in findings if f.type == "oversized-page"]
    assert len(oversized) == 1
    assert oversized[0].severity == "warning"
    assert oversized[0].target == "kb/subject-big.md"
    # log.md is exempt regardless of size.
    assert all(f.target != "kb/log.md" for f in oversized)


def test_scan_flags_missing_status_marker_on_lifecycle_pages(tmp_path):
    """plan-/design-/decision-/deck- pages without a Status line get
    flagged. A subject hub without one stays quiet — subjects are not
    lifecycle pages."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Plan](plan-foo.md) — desc\n"
        "- [Subject](subject-bar.md) — desc\n"
    ))
    _write(
        tmp_path / "kb" / "plan-foo.md",
        "# Plan foo\n\nBody without a status marker.\n",
    )
    _write(
        tmp_path / "kb" / "subject-bar.md",
        "# Subject bar\n\nNo status, but subjects don't need one.\n",
    )
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    missing = [f for f in findings if f.type == "missing-status-marker"]
    assert len(missing) == 1
    assert missing[0].target == "kb/plan-foo.md"
    assert missing[0].severity == "warning"


def test_scan_accepts_emphasized_status_marker(tmp_path):
    """``**Status:** active`` should count the same as ``Status: active``.
    Page authors often emphasize the marker; the scanner shouldn't
    nag them about formatting."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Plan](plan-foo.md) — desc\n"
    ))
    _write(
        tmp_path / "kb" / "plan-foo.md",
        "# Plan\n\n**Status:** active\n\nBody.\n",
    )
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert all(f.type != "missing-status-marker" for f in findings)


def test_scan_flags_revision_history_heavy_pages(tmp_path):
    """A page whose body reads like a running diff of its own past
    wording gets a warning so the maintenance pass can compress it
    to a single lineage breadcrumb."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Design](design-x.md) — desc\n"
    ))
    body = (
        "# Design x\n\n"
        "Status: accepted on 2026-05-12\n\n"
        "## Revision history\n\n"
        "- **2026-05-12 amendment.** Removed the old shape.\n"
        "- **2026-05-11 implementation note.** Shipped revision A.\n"
        "- **2026-05-10 revision.** Earlier draft superseded.\n"
        "\n"
        "Previously we did Y; originally Z; the old code did W.\n"
    )
    _write(tmp_path / "kb" / "design-x.md", body)
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    heavy = [f for f in findings if f.type == "revision-history-heavy"]
    assert len(heavy) == 1
    assert heavy[0].target == "kb/design-x.md"
    assert heavy[0].severity == "warning"


def test_scan_does_not_flag_clean_lineage_breadcrumb(tmp_path):
    """A page with a single dated lineage bullet at the bottom is
    fine — that's the *recommended* shape, not the bloat we're
    flagging. Below the threshold."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Design](design-x.md) — desc\n"
    ))
    body = (
        "# Design x\n\n"
        "Status: accepted on 2026-05-12\n\n"
        "Current shape paragraph one.\n\nCurrent shape paragraph two.\n\n"
        "## Lineage\n\n"
        "- **2026-05-12** rewrote the resolver for state-first.\n"
    )
    _write(tmp_path / "kb" / "design-x.md", body)
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert all(f.type != "revision-history-heavy" for f in findings)


def test_scan_flags_recent_log_entry_over_budget(tmp_path):
    """When the newest log entry exceeds the prompt byte budget it
    silently pushes older entries out of the conversation context
    block. The maintenance pass should compress it."""
    _write(tmp_path / "kb" / "index.md", "# Index\n")
    bulk = "x " * 3000  # ~6000 bytes, comfortably over the 4KB budget.
    _write(
        tmp_path / "kb" / "log.md",
        f"# Log\n\n## [2026-05-13] implement | Big entry\n\n{bulk}\n",
    )

    findings = kb_preflight.scan(tmp_path)

    over = [f for f in findings if f.type == "recent-log-budget-exceeded"]
    assert len(over) == 1
    assert over[0].severity == "info"
    assert "kb/log.md" in over[0].target


def test_scan_does_not_flag_recent_log_within_budget(tmp_path):
    """A small newest entry stays quiet so the maintenance pass
    doesn't churn over routine activity."""
    _write(tmp_path / "kb" / "index.md", "# Index\n")
    _write(
        tmp_path / "kb" / "log.md",
        "# Log\n\n## [2026-05-13] implement | Small\n\nDid a thing.\n",
    )

    findings = kb_preflight.scan(tmp_path)

    assert all(f.type != "recent-log-budget-exceeded" for f in findings)


def test_format_findings_renders_severity_prefix_for_advisories():
    """Errors keep the existing bullet format; advisories get a
    bracketed severity prefix so a human reader can triage at a
    glance and the LLM prompt sees the distinction clearly."""
    findings = [
        kb_preflight.Finding(
            type="broken-link",
            target="kb/x.md → y.md",
            description="missing",
        ),
        kb_preflight.Finding(
            type="oversized-page",
            target="kb/big.md",
            description="too big",
            severity="warning",
        ),
        kb_preflight.Finding(
            type="recent-log-budget-exceeded",
            target="kb/log.md",
            description="big entry",
            severity="info",
        ),
    ]

    block = kb_preflight.format_findings(findings)

    assert "**broken-link**" in block
    assert "[warning]" not in block.split("**broken-link**", 1)[0]
    assert "**oversized-page** [warning]" in block
    assert "**recent-log-budget-exceeded** [info]" in block


def test_format_findings_returns_empty_string_when_clean(tmp_path):
    assert kb_preflight.format_findings([]) == ""


def test_format_findings_renders_findings_block():
    findings = [
        kb_preflight.Finding(
            type="missing-from-index",
            target="kb/decision-foo.md",
            description="needs an index entry",
        ),
        kb_preflight.Finding(
            type="broken-link",
            target="kb/decision-foo.md → bar.md",
            description="bar.md not found",
        ),
    ]

    block = kb_preflight.format_findings(findings)

    assert "Findings (deterministic preflight)" in block
    assert "missing-from-index" in block
    assert "kb/decision-foo.md" in block
    assert "broken-link" in block


def test_scan_flags_hub_coverage_when_section_lacks_subject_page(tmp_path):
    """An index section with at least two design/plan/decision/deck
    pages but no subject hub gets a soft nudge to synthesise."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "## Environments\n\n"
        "- [Design](design-env-interface.md) — desc\n"
        "- [Plan](plan-concurrent-worktrees.md) — desc\n"
    ))
    _write(
        tmp_path / "kb" / "design-env-interface.md",
        "# Env interface\n\nStatus: active\n\nBody.\n",
    )
    _write(
        tmp_path / "kb" / "plan-concurrent-worktrees.md",
        "# Worktrees\n\nStatus: shipped on 2026-04-01\n\nBody.\n",
    )
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    coverage = [f for f in findings if f.type == "hub-coverage"]
    assert len(coverage) == 1
    assert coverage[0].severity == "info"
    assert "Environments" in coverage[0].target


def test_scan_suppresses_hub_coverage_when_subject_present(tmp_path):
    """A section that already has a ``subject-*.md`` link is covered
    even if it also lists several artifacts."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "## Tasks & branching\n\n"
        "- [Hub](subject-runs-branching.md) — synthesis\n"
        "- [Design](design-daemon-landing-branch.md) — desc\n"
        "- [Plan](plan-branch-modes.md) — desc\n"
        "- [Decision](decision-remove-triage.md) — desc\n"
    ))
    for name in (
        "subject-runs-branching.md",
        "design-daemon-landing-branch.md",
        "plan-branch-modes.md",
        "decision-remove-triage.md",
    ):
        _write(
            tmp_path / "kb" / name,
            f"# {name}\n\nStatus: accepted on 2026-04-01\n\nBody.\n",
        )
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert all(f.type != "hub-coverage" for f in findings), findings


def test_scan_does_not_flag_hub_coverage_for_research_only_sections(tmp_path):
    """A section that only lists research / notes pages stays quiet;
    research-shaped material doesn't aggregate into a hub by itself."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "## Research\n\n"
        "- [Research one](research-one.md) — desc\n"
        "- [Research two](research-two.md) — desc\n"
        "- [Notes](notes-foo.md) — desc\n"
    ))
    _write(tmp_path / "kb" / "research-one.md", "# 1\n\nBody.\n")
    _write(tmp_path / "kb" / "research-two.md", "# 2\n\nBody.\n")
    _write(tmp_path / "kb" / "notes-foo.md", "# notes\n\nBody.\n")
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert all(f.type != "hub-coverage" for f in findings), findings


def test_scan_strips_decoration_from_section_titles(tmp_path):
    """Index headings often carry a parenthesised italic status — the
    hub-coverage advisory should report the clean title."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "## Fleet & overlays *(paused — env axis is the only active strand)*\n\n"
        "- [Deck](deck-x.md) — desc\n"
        "- [Plan](plan-x.md) — desc\n"
    ))
    _write(
        tmp_path / "kb" / "deck-x.md",
        "# Deck\n\nStatus: paused\n\nBody.\n",
    )
    _write(
        tmp_path / "kb" / "plan-x.md",
        "# Plan\n\nStatus: blocked\n\nBody.\n",
    )
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    coverage = [f for f in findings if f.type == "hub-coverage"]
    assert len(coverage) == 1
    assert "Fleet & overlays" in coverage[0].target
    assert "paused" not in coverage[0].target


def test_scan_flags_proposal_scaffolding_on_shipped_pages(tmp_path):
    """A shipped or accepted page that still carries multiple
    proposal-shape sections gets a nudge to compress to current
    state."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Design](design-x.md) — desc\n"
    ))
    _write(tmp_path / "kb" / "design-x.md", (
        "# Design x\n\n"
        "Status: shipped on 2026-05-10\n\n"
        "## Goals\n\nWhat we wanted.\n\n"
        "## Non-goals\n\nWhat we ruled out.\n\n"
        "## Alternatives considered\n\nOther shapes we looked at.\n\n"
        "## Current shape\n\nWhat we built.\n"
    ))
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    scaffold = [f for f in findings if f.type == "proposal-scaffolding"]
    assert len(scaffold) == 1
    assert scaffold[0].severity == "info"
    assert scaffold[0].target == "kb/design-x.md"


def test_scan_does_not_flag_proposal_scaffolding_when_in_flight(tmp_path):
    """A page still ``Status: active`` or ``in flight`` is allowed to
    carry proposal scaffolding — that's exactly what proposals look
    like before they ship."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Design](design-x.md) — desc\n"
    ))
    _write(tmp_path / "kb" / "design-x.md", (
        "# Design x\n\n"
        "Status: active\n\n"
        "## Goals\n\nWhat we want.\n\n"
        "## Alternatives considered\n\nOther shapes.\n\n"
        "## Open questions\n\nStill thinking.\n"
    ))
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert all(f.type != "proposal-scaffolding" for f in findings), findings


def test_scan_does_not_flag_single_goals_block_on_shipped_page(tmp_path):
    """One ``## Goals`` block on a shipped design is usually a fine
    paragraph of context. The advisory fires only on the *retained
    proposal shape* — two or more scaffolding sections together."""
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n- [Design](design-x.md) — desc\n"
    ))
    _write(tmp_path / "kb" / "design-x.md", (
        "# Design x\n\n"
        "Status: shipped on 2026-05-10\n\n"
        "## Goals\n\nOne paragraph of context.\n\n"
        "## Current shape\n\nWhat we built.\n"
    ))
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert all(f.type != "proposal-scaffolding" for f in findings), findings


def test_scan_finding_order_is_stable(tmp_path):
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Foo](decision-foo.md) — desc\n"
        "- [Stale](decision-stale.md) — desc\n"
    ))
    _write(tmp_path / "kb" / "decision-foo.md", (
        "Status: accepted on 2026-04-01\n\n"
        "[broken](missing-1.md), [broken-too](missing-2.md)\n"
    ))
    _write(
        tmp_path / "kb" / "decision-orphan.md",
        "# Orphan\n\nStatus: accepted on 2026-04-01\n",
    )
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)
    # Errors come before warnings/info regardless of type ordering;
    # within a severity, (type, target) sort lexicographically.
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=lambda s: (
        ["error", "warning", "info"].index(s)
    )), severities
