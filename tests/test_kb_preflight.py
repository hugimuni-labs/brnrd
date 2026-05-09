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
    _write(tmp_path / "kb" / "decision-foo.md", "# Foo\n")
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    assert kb_preflight.scan(tmp_path) == []


def test_scan_accepts_relative_repo_root(tmp_path, monkeypatch):
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Subject](decision-foo.md) — desc\n"
    ))
    _write(tmp_path / "kb" / "decision-foo.md", "# Foo\n")
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
        "Jump to [section](#some-anchor) within this page.\n"
    ))
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    findings = kb_preflight.scan(tmp_path)

    assert findings == []


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


def test_scan_finding_order_is_stable(tmp_path):
    _write(tmp_path / "kb" / "index.md", (
        "# Index\n\n"
        "- [Foo](decision-foo.md) — desc\n"
        "- [Stale](decision-stale.md) — desc\n"
    ))
    _write(tmp_path / "kb" / "decision-foo.md", (
        "[broken](missing-1.md), [broken-too](missing-2.md)\n"
    ))
    _write(tmp_path / "kb" / "decision-orphan.md", "# Orphan\n")
    _write(tmp_path / "kb" / "log.md", "# Log\n")

    types_in_order = [f.type for f in kb_preflight.scan(tmp_path)]

    assert types_in_order == sorted(types_in_order, key=lambda t: (
        ["missing-from-index", "stale-index-entry", "broken-link"].index(t)
    )), types_in_order
