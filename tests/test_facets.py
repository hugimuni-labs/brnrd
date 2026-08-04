"""The boundary facet schema — the single definition every renderer projects."""

from brr import facets, presence


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


def test_build_attaches_pacing_status_to_quota_facet_when_present():
    res = facets.build(
        quota_summary="weekly 14% left",
        pacing_status={"binding_remaining_pct": 14.2, "floor": "low"},
    )
    assert res["quota"]["pacing"] == {"binding_remaining_pct": 14.2, "floor": "low"}


def test_build_omits_pacing_key_when_quota_pacing_unknown():
    res = facets.build(quota_summary="weekly 90% left")
    assert "pacing" not in res["quota"]
    # Explicit None (no signal this heartbeat) is the same as omitting it.
    res_none = facets.build(quota_summary="weekly 90% left", pacing_status=None)
    assert "pacing" not in res_none["quota"]


# ── coexisting_runs (live presence-registry read) ────────────────────────────


def test_build_coexisting_none_stays_unimplemented():
    """Omitting ``coexisting`` reproduces every prior wake's behaviour."""
    res = facets.build(quota_summary="42%")
    assert res["coexisting_runs"]["status"] == "unimplemented"


def test_build_coexisting_empty_list_is_affirmative_absent():
    """A call site with a wired collector that finds nobody else is 'absent',
    not 'unimplemented' — the collector ran, there's genuinely nothing there."""
    res = facets.build(quota_summary="42%", coexisting=[])
    assert res["coexisting_runs"]["status"] == "absent"
    assert res["coexisting_runs"]["note"] == "no sibling runs active right now"


def test_build_coexisting_known_with_sibling_summary():
    res = facets.build(
        quota_summary="42%",
        coexisting=[
            {"run_id": "run-a", "name": "frontend repair", "label": "fix the frontend build", "kind": "daemon"},
            {"run_id": "run-b", "stream": "telegram:1:", "kind": "daemon"},
        ],
    )
    facet = res["coexisting_runs"]
    assert facet["status"] == "known"
    assert facet["required"] is False
    assert "2 sibling runs" in facet["summary"]
    assert "frontend repair" in facet["summary"]
    assert len(facet["siblings"]) == 2


# ── #585: presence label must never carry another run's task prose ──────────
#
# The mechanism: a sibling's presence entry (name/label/stream/run_id) is
# read by `presence.list_active`, handed to `facets.build` as `coexisting`,
# and its rendered `summary` is injected into *this* run's own hook context
# at every tool-call boundary (`hooks.py` -> `facets.render_line`). Two
# guards: (1) daemon.py's presence `register()` call no longer falls back to
# `task.body` for `label` (see `daemon._presence_label_for_event`, tested in
# test_daemon.py); (2) this facet must independently refuse to emit long
# free text regardless of which field a sibling's entry carries it in — the
# tests below pin guard (2).
#
# N = 32 (`facets._SIBLING_HANDLE_MAX_CHARS`): long enough that a real
# handle (a run id like "run-260723-1241-xv6d", a short resident `.name`, a
# conversation key) renders in full; short enough that no truncated prefix
# of a task spec reads as a sentence-length directive a sibling could
# mistake for its own instruction.


def test_sibling_handle_hard_caps_long_free_text_regardless_of_source():
    long_body = "Fix GitHub issue **#565**: stopped runs are credited " + (
        "even though the branch never merged, which double-counts them " * 4
    )
    assert len(long_body) > facets._SIBLING_HANDLE_MAX_CHARS
    for field in ("name", "label", "stream", "run_id"):
        handle = facets._sibling_handle({field: long_body})
        assert len(handle) <= facets._SIBLING_HANDLE_MAX_CHARS + 1  # +1 for "…"
        assert long_body[:facets._SIBLING_HANDLE_MAX_CHARS + 1] not in handle


def test_sibling_handle_prefers_name_then_label_then_stream_then_run_id():
    assert facets._sibling_handle(
        {"name": "n", "label": "l", "stream": "s", "run_id": "r"}
    ) == "n"
    assert facets._sibling_handle(
        {"label": "l", "stream": "s", "run_id": "r"}
    ) == "l"
    assert facets._sibling_handle({"stream": "s", "run_id": "r"}) == "s"
    assert facets._sibling_handle({"run_id": "r"}) == "r"
    assert facets._sibling_handle({}) == "?"


def test_sibling_handle_collapses_whitespace_before_capping():
    # A multi-line body must not wrap into something that reads like a
    # continued sentence once truncated.
    handle = facets._sibling_handle({"label": "line one\nline two\nline three"})
    assert "\n" not in handle


def test_build_coexisting_never_leaks_a_sibling_task_body_substring(tmp_path):
    """The mechanical regression test #585 specifies: two *live* runs with
    distinct task bodies, presence registered for both (via the real
    ``presence`` module — the shape a hypothetical future producer bug
    would put a verbatim task body in as ``label``, exactly what guard (2)
    exists to survive even though guard (1), in ``daemon.py``, no longer
    produces it). Compute the ``coexisting_runs`` facet for one run and
    assert no substring longer than N chars of the *other* run's task body
    appears verbatim in the facet summary or in the woven
    ``- resources: …`` line. No issue-number heuristics, no semantics —
    purely mechanical."""
    n = facets._SIBLING_HANDLE_MAX_CHARS
    brr_dir = tmp_path / ".brr"
    body_a = (
        "# Task — issue #565: stopped runs are credited even though the "
        "branch never merged, which double-counts them in the ledger view."
    )
    body_b = (
        "# Task — issue #564: the dominion inject drops a third of the "
        "resident's playbook silently on every third heartbeat tick."
    )
    presence.register(brr_dir, kind="daemon", run_id="run-a", label=body_a)
    presence.register(brr_dir, kind="daemon", run_id="run-b", label=body_b)
    active = presence.list_active(brr_dir)
    assert len(active) == 2

    def _facet_and_line(self_run_id: str) -> tuple[dict, str]:
        siblings = [e for e in active if e["run_id"] != self_run_id]
        res = facets.build(quota_summary="42%", coexisting=siblings)
        return res["coexisting_runs"], facets.render_line(res)

    def _assert_no_leak(other_body: str, facet: dict, line: str) -> None:
        for start in range(0, len(other_body) - n):
            window = other_body[start : start + n + 1]
            assert window not in facet["summary"]
            assert window not in line

    # From run-a's perspective, run-b is the only sibling: its body must
    # not leak more than N verbatim chars.
    facet_a, line_a = _facet_and_line("run-a")
    _assert_no_leak(body_b, facet_a, line_a)
    # Symmetric: from run-b's perspective, run-a's body must not leak.
    facet_b, line_b = _facet_and_line("run-b")
    _assert_no_leak(body_a, facet_b, line_b)


def test_build_coexisting_incident_shape_issue_number_prose_does_not_leak():
    """The real #574 incident shape: a spawn worker's task body opens with
    `Fix GitHub issue **#565**: …`. A sibling's facet must not carry that
    prose — a bare, short `#565` alone would be fine (it's not a directive
    by itself), but the surrounding sentence must not survive."""
    task_body = (
        "Fix GitHub issue **#565**: stopped runs are credited even though "
        "the branch never merged, which double-counts them in the ledger."
    )
    res = facets.build(
        quota_summary="42%",
        coexisting=[{"run_id": "run-nig2", "label": task_body, "kind": "daemon"}],
    )
    facet = res["coexisting_runs"]
    line = facets.render_line(res)
    assert "stopped runs are credited even though" not in facet["summary"]
    assert "stopped runs are credited even though" not in line


def test_build_coexisting_resident_authored_name_still_renders():
    """Requirement (4): a resident-authored `.name` is the one legitimate
    label and must keep rendering through the facet boundary."""
    res = facets.build(
        quota_summary="42%",
        coexisting=[
            {"run_id": "run-a", "name": "dashboard name", "kind": "daemon"},
        ],
    )
    assert "dashboard name" in res["coexisting_runs"]["summary"]


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
        facets.facet_value({"status": "known", "pr_state": "recorded",
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


# ── runner governance block (step 3, design-runner-cores.md) ─────────────────


def test_build_runner_block_absent_without_runner_name():
    res = facets.build(quota_summary="42%")
    runner = res["runner"]
    assert runner["status"] == "absent"
    assert runner["summary"] is None


def test_build_runner_block_known_with_runner_name():
    res = facets.build(
        quota_summary="42%",
        runner_name="claude",
        runner_meta={
            "model": "claude-sonnet-4-6",
            "class": "balanced",
            "provider": "anthropic",
            "hooks": "claude",
            "cost_rank": 30,
            "capability_score": 0.73,
            "capability_source": "benchmark-cache",
            "capability_freshness": "2026-06-29",
        },
    )
    runner = res["runner"]
    assert runner["status"] == "known"
    assert runner["name"] == "claude"
    assert runner["model"] == "claude-sonnet-4-6"
    assert runner["model_requested"] == "claude-sonnet-4-6"
    assert runner["model_observed"] is None
    assert runner["attestation"] == "pending"
    assert runner["class"] == "balanced"
    assert runner["provider"] == "anthropic"
    assert runner["hooks"] == "claude"
    assert runner["cost_rank"] == 30
    assert runner["capability_score"] == 0.73
    assert runner["capability_source"] == "benchmark-cache"
    assert runner["capability_freshness"] == "2026-06-29"
    assert runner["summary"] == "claude"


def test_runner_block_exposes_observed_core_mismatch():
    runner = facets.build(
        runner_name="claude-fable",
        runner_meta={"model": "claude-fable-5"},
        levels={"model_ids": ["claude-opus-4-8"]},
    )["runner"]

    assert runner["model_requested"] == "claude-fable-5"
    assert runner["model_observed"] == "claude-opus-4-8"
    assert runner["attestation"] == "mismatch"
    assert runner["core_mismatch"] is True


def test_build_runner_block_can_expose_quality_escalation_target():
    res = facets.build(
        runner_name="codex-mini",
        runner_meta={"class": "economy"},
        quality_escalation={
            "status": "known",
            "name": "claude-opus",
            "model": "claude-opus-4-8",
            "class": "strong",
        },
    )

    runner = res["runner"]
    assert runner["quality_escalation"]["name"] == "claude-opus"
    assert runner["quality_escalation"]["class"] == "strong"


def test_build_runner_block_known_with_name_only():
    """runner_meta is optional — name alone is enough for a known block."""
    res = facets.build(runner_name="codex")
    runner = res["runner"]
    assert runner["status"] == "known"
    assert runner["name"] == "codex"
    assert runner["model"] is None
    assert runner["class"] is None


def test_build_runner_block_exposes_catalog():
    res = facets.build(
        runner_name="codex-mini",
        runner_meta={"class": "economy"},
        runner_catalog=[
            {
                "name": "codex-mini",
                "shell": "codex",
                "model": "gpt-5.4-mini",
                "class": "economy",
                "selected": True,
                "availability": "available",
            },
            {
                "name": "claude-opus",
                "shell": "claude",
                "model": "claude-opus-4-8",
                "class": "strong",
                "selected": False,
                "availability": "available",
            },
        ],
    )

    catalog = res["runner"]["catalog"]
    assert catalog[0]["name"] == "codex-mini"
    assert catalog[0]["selected"] is True
    assert catalog[1]["name"] == "claude-opus"


def test_build_runner_block_can_expose_relay_consent():
    """relay_consent carries spending plan details when relay fallback is offered."""
    res = facets.build(
        runner_name="codex-mini",
        runner_meta={"class": "economy", "provider": "openai"},
        relay_consent={
            "status": "pending",
            "reason": "local_quota_exhausted",
            "model": "gpt-5-codex-mini",
            "provider": "openai",
            "provider_cost_usd": "0.10",
            "relay_service_fee_usd": "0.01",
            "total_estimated_cost_usd": "0.11",
            "per_run_cap_usd": "1.00",
            "relay_balance_usd": "5.00",
            "consent_state": "pending",
        },
    )

    runner = res["runner"]
    assert "relay_consent" in runner
    relay = runner["relay_consent"]
    assert relay["status"] == "pending"
    assert relay["reason"] == "local_quota_exhausted"
    assert relay["model"] == "gpt-5-codex-mini"
    assert relay["consent_state"] == "pending"


def test_build_runner_block_exposes_wake_request_miss():
    """#577: a tap that existed and did not apply is visible on
    resources.runner.wake_request — "you asked for X, you got Y, why"."""
    res = facets.build(
        runner_name="claude-opus",
        runner_meta={"class": "strong"},
        wake_request={
            "requested_profile": "claude-fable",
            "resolved_profile": "claude-opus",
            "applied": False,
            "reason": "tap parked outside the claim window for this wake",
        },
    )
    wake_request = res["runner"]["wake_request"]
    assert wake_request["requested_profile"] == "claude-fable"
    assert wake_request["resolved_profile"] == "claude-opus"
    assert wake_request["applied"] is False
    assert "claim window" in wake_request["reason"]


def test_build_runner_block_omits_wake_request_key_when_absent():
    """No tap in play this wake ⇒ the key is absent, not null — a run that
    never touched the dashboard tap must not look like a miss."""
    res = facets.build(runner_name="codex")
    assert "wake_request" not in res["runner"]


def test_render_line_does_not_include_runner_block():
    """render_line iterates FACETS (the level/state walls); runner is governance,
    not a wall, so it should NOT appear in the hook injection line."""
    res = facets.build(
        quota_summary="42%",
        runner_name="claude",
        runner_meta={"class": "balanced"},
    )
    line = facets.render_line(res)
    # The hook line should carry the schema facets, not the runner block
    assert "runner=" not in line
    assert "quota=" in line
