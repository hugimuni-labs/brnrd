"""The boundary facet schema — the single definition every renderer projects."""

from brr import facets


def test_schema_is_wall_derived_and_ordered():
    keys = [f.key for f in facets.FACETS]
    assert keys == [
        "quota", "spend", "context_window", "coexisting_runs", "remote_scm"
    ]
    # Level facets are the walls; state facets are actionable posture.
    by_key = facets.FACETS_BY_KEY
    assert by_key["quota"].kind == facets.LEVEL
    assert by_key["spend"].kind == facets.LEVEL
    assert by_key["context_window"].kind == facets.LEVEL
    assert by_key["coexisting_runs"].kind == facets.STATE
    assert by_key["remote_scm"].kind == facets.STATE
    # coexisting_runs is the only optional facet (single-flight someday-nicety).
    assert by_key["coexisting_runs"].required is False
    assert all(s.required for s in facets.FACETS if s.key != "coexisting_runs")


def test_build_three_state_honesty_without_collector():
    res = facets.build(quota_summary="weekly 42% - resets 3d", branch="brr/x")
    assert res["quota"]["status"] == "known"
    # No level collector wired → spend/context_window read unimplemented.
    assert res["spend"]["status"] == "unimplemented"
    assert res["context_window"]["status"] == "unimplemented"
    assert res["coexisting_runs"]["status"] == "unimplemented"
    # No PR recorded → affirmative-absent, not unimplemented.
    assert res["remote_scm"]["status"] == "absent"


def test_build_level_collector_flips_empty_to_absent_and_known():
    empty = facets.build(levels_collector=True)
    assert empty["spend"]["status"] == "absent"
    assert empty["context_window"]["status"] == "absent"
    assert empty["quota"]["status"] == "absent"

    full = facets.build(
        levels_collector=True,
        levels={
            "quota": {"summary": "5h 58% left"},
            "spend": {"summary": "$0.42 this session"},
            "context_window": {"summary": "62% context left"},
        },
        branch="brr/x",
        pr_number="207",
    )
    assert full["quota"]["status"] == "known"
    assert full["spend"]["summary"] == "$0.42 this session"
    assert full["context_window"]["status"] == "known"
    assert full["remote_scm"]["status"] == "known"
    assert full["remote_scm"]["pr_number"] == "207"


def test_render_line_carries_every_schema_facet_in_order():
    res = facets.build(quota_summary="42%", branch="brr/x")
    line = facets.render_line(res)
    assert line.startswith("- resources: ")
    for spec in facets.FACETS:
        assert f"{spec.label}=" in line
    # Schema order is preserved in the woven line.
    assert line.index("quota=") < line.index("spend=") < line.index("remote-scm=")


def test_facet_value_renders_states():
    assert facets.facet_value({"status": "known", "summary": "42%"}) == "42%"
    assert (
        facets.facet_value({"status": "known", "pr_state": "open",
                            "pr_number": "9"}) == "PR #9"
    )
    assert facets.facet_value(
        {"status": "absent", "note": "no PR yet"}
    ) == "absent (no PR yet)"
    assert facets.facet_value({"status": "unimplemented"}) == "unimplemented"


def test_describe_facets_schema_only_and_with_live_status():
    rows = facets.describe_facets()
    assert [r["key"] for r in rows] == [f.key for f in facets.FACETS]
    assert all(r["fills"] for r in rows)
    assert all(r["status"] is None for r in rows)

    res = facets.build(quota_summary="42%", branch="brr/x")
    live = facets.describe_facets(res)
    quota = next(r for r in live if r["key"] == "quota")
    assert quota["status"] == "known"
    assert quota["value"] == "42%"
