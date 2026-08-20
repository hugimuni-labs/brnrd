"""The ignition assignments (w-69, ``design-the-ignition-assignments.md``).

Every seam through its real caller: pricing through :func:`brr.assignments.
price` (the same reducer contract the scheduler's pacing trusts), derivation
through ``prompts.build_boot_score`` (the call every daemon wake makes), and
the boundary ledger through ``hooks.run_hook`` — fresh subprocess-shaped
invocations against a real portal file, hook state carried on disk between
boundaries, exactly as production runs it.
"""

from __future__ import annotations

import json
from pathlib import Path

from brr import assignments as am
from brr import hooks, prompts
from brr.bootscore import format_kernel, to_dict


# ── Pricing (fork 2, amended and signed: windows priced from live quota) ──


def test_pricing_tiers_track_the_pacing_floors():
    rich = am.price(80.0)
    mid = am.price(36.0)
    low = am.price(15.0)
    critical = am.price(5.0)
    # Scarce quota buys a terse ignition: later (larger windows), smaller
    # (slower cadence) escalations. Rich quota an attentive one.
    assert rich.multiplier < mid.multiplier < low.multiplier < critical.multiplier
    assert rich.cadence < mid.cadence < low.cadence < critical.cadence
    assert (rich.label, mid.label, low.label, critical.label) == (
        "rich", "mid", "low", "critical"
    )


def test_unmeasured_quota_prices_neutral():
    p = am.price(None)
    assert p.multiplier == 1.0
    assert p.label == "unmeasured"


def test_windows_scale_with_the_price():
    rich = am.derive(has_event_body=True, pending_count=1, pricing=am.price(80.0))
    scarce = am.derive(has_event_body=True, pending_count=1, pricing=am.price(5.0))
    rich_pending = next(a for a in rich if a.kind == am.KIND_PENDING)
    scarce_pending = next(a for a in scarce if a.kind == am.KIND_PENDING)
    assert scarce_pending.window > rich_pending.window
    assert scarce_pending.cadence > rich_pending.cadence


# ── Derivation ─────────────────────────────────────────────────────────────


def test_the_waking_event_is_assignment_one():
    # The signed rider (evt-…-pbrc): the run's own reason arrives as
    # assignment #1, not special-cased prose above the list.
    rows = am.derive(has_event_body=True, pending_count=3, pricing=am.price(None))
    assert rows[0].kind == am.KIND_EVENT
    # And it is unmetered: its normal discharge (the final stdout reply)
    # post-dates every boundary; the Stop capsule governs that seam.
    assert rows[0].window is None


def test_a_strand_never_sees_the_residents_queue():
    # The 2026-07-13 incident, re-pinned against the new list: a strand
    # inherited the parent's pending count at position 1 of the old `next:`
    # list and answered the user's messages in the resident's thread.
    rows = am.derive(
        is_strand=True, has_event_body=True, pending_count=12,
        environment="host", pricing=am.price(None),
    )
    assert all(a.kind != am.KIND_PENDING for a in rows)
    # Nor the resident-only card/claims rows.
    assert all(a.kind not in (am.KIND_CARD, am.KIND_CLAIMS) for a in rows)
    # But the host branch rule still reaches it.
    assert any(a.kind == am.KIND_BRANCH for a in rows)


def test_escalation_detail_is_capped_by_construction():
    for kind in (am.KIND_PENDING, am.KIND_ORIENT, am.KIND_CARD, am.KIND_CLAIMS):
        assert len(am.detail_lines_for(kind)) <= am.ESCALATION_CAP


def test_kernel_renders_rows_with_discharge_and_window(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# rules\ncontract\n", encoding="utf-8")
    score = prompts.build_boot_score(
        tmp_path, is_daemon=True, environment="host",
        event_ids=("evt-1",), pending_count=2, has_event_body=True,
        quota_binding_pct=80.0,
    )
    kernel = format_kernel(score)
    assert "assignments:" in kernel
    assert "1. answer the waking event" in kernel
    assert "⇢" in kernel               # every row names its discharge
    assert "↗" in kernel               # metered rows name their window
    assert "## Plan" in kernel         # the scaffold offer (fork 1, signed)
    # The retired surfaces stay retired.
    assert "next:" not in kernel
    assert "\norient:" not in kernel
    # The orientation files render under their own row.
    assert str((tmp_path / "AGENTS.md").resolve()) in kernel


def test_assignments_ride_the_persisted_score(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
    score = prompts.build_boot_score(
        tmp_path, is_daemon=True, has_event_body=True, quota_binding_pct=None,
    )
    persisted = to_dict(score)
    rows = am.rows_from_score(persisted)
    assert rows and rows[0]["id"] == "a-event"
    # And an older score without the field degrades to no ledger.
    assert am.rows_from_score({"orientation_set": []}) == []


# ── The boundary ledger, through the real hook caller ─────────────────────


def _env(tmp_path):
    return {
        "BRR_RUN_ID": "run-260820-0500-test",
        "BRR_EVENT_ID": "evt-1",
        "BRR_RUNNER": "claude",
        "BRR_OUTBOX_DIR": str(tmp_path),
        "BRR_PORTAL_STATE": str(tmp_path / "portal-state.json"),
        "BRR_BOOT_SCORE": str(tmp_path / "boot-score.json"),
    }


def _portal(tmp_path, token, **extra):
    payload = {
        "change_token": token,
        "run": {"id": "run-260820-0500-test"},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"events": []},
        "outbound": {"replies_current": 0, "replies_other": 0,
                     "outbound_messages": 0},
        "budget": {"elapsed_seconds": 60, "budget_seconds": 7200},
        "card": {"active": False},
        "name": {"written": False},
        "produce": {"known": True, "counts": {}},
        "resources": {},
    }
    payload.update(extra)
    (tmp_path / "portal-state.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _score(tmp_path, rows):
    (tmp_path / "boot-score.json").write_text(
        json.dumps({"assignments": rows, "orientation_set": []}),
        encoding="utf-8",
    )


def _claims_row(window=1, cadence=2, detail=("one line each",)):
    return {
        "id": "a-claims", "kind": "claims",
        "title": "claim .name · .mood · .topics",
        "discharge": "one write each",
        "window": window, "cadence": cadence, "detail": list(detail),
        "anchor": ".name",
    }


def _inject(out):
    return (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""


def test_seed_header_names_the_ignition(tmp_path):
    _score(tmp_path, [_claims_row()])
    _portal(tmp_path, "t0")
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    text = _inject(out)
    assert "[brnrd ignition] 1 assignment(s)" in text
    assert "portal seed" not in text


def test_seed_without_a_ledger_keeps_the_portal_seed_header(tmp_path):
    _portal(tmp_path, "t0")
    env = {k: v for k, v in _env(tmp_path).items() if k != "BRR_BOOT_SCORE"}
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", env)
    assert "[brnrd portal seed]" in _inject(out)


def test_overdue_row_escalates_one_line_per_cadence_then_holds(tmp_path):
    _score(tmp_path, [_claims_row(
        window=1, cadence=2,
        detail=["line one", "line two", "line three"],
    )])
    env = _env(tmp_path)

    # Boundary 1: inside the window — the chip stands, no overdue rows.
    _portal(tmp_path, "t1")
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    text = _inject(first)
    assert "assign 0/1" in text
    assert "assign overdue" not in text

    # Boundary 2: past the window — level 1, the first line unlocks, and
    # the escalation edge opens the boundary by itself (unchanged token).
    _portal(tmp_path, "t1")
    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    text = _inject(second)
    assert "assign overdue" in text
    assert "line one" in text and "line two" not in text

    # Boundary 3: same level — no edge, nothing renders at all.
    _portal(tmp_path, "t1")
    third, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "assign overdue" not in _inject(third)

    # Boundary 4: cadence 2 boundaries past overdue — level 2 unlocks.
    _portal(tmp_path, "t1")
    fourth, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    text = _inject(fourth)
    assert "line two" in text

    # Boundaries 5-8: level 3 unlocks at its cadence, then HOLDS — no
    # growth past the cap however many boundaries pass.
    for token in ("t1", "t1", "t1", "t1"):
        _portal(tmp_path, token)
        out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    state = json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )
    row_state = state[am.STATE_KEY]["rows"]["a-claims"]
    assert row_state["level"] == 3
    assert row_state["retired"] is None


def test_the_claims_discharge_retires_the_row(tmp_path):
    _score(tmp_path, [_claims_row(window=9)])
    env = _env(tmp_path)
    _portal(tmp_path, "t1")
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "assign 0/1" in _inject(first)

    # The three writes land: name (portal facet), mood + topics (controls).
    (tmp_path / hooks.MOOD_NAME).write_text("focused\n", encoding="utf-8")
    (tmp_path / ".topics").write_text("the-clockwork\n", encoding="utf-8")
    _portal(tmp_path, "t2", name={"written": True})
    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    # All rows retired: the chip leaves and never returns.
    assert "assign" not in _inject(second)
    state = json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )
    assert (
        state[am.STATE_KEY]["rows"]["a-claims"]["retired"]
        == am.RETIRED_DISCHARGED
    )


def test_the_card_handoff_adopts_and_defers(tmp_path):
    # Two rows: one whose anchor survives into the adopted ## Plan, one the
    # resident's plan leaves out. Adoption and deferral are both silent
    # retirements — the resident's own act (design §3).
    _score(tmp_path, [
        _claims_row(window=9),
        {
            "id": "a-card", "kind": "card",
            "title": "write .card with a ## Plan",
            "discharge": "the write", "window": 9, "cadence": 4,
            "detail": [], "anchor": ".card",
        },
    ])
    env = _env(tmp_path)
    _portal(tmp_path, "t1")
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    (tmp_path / hooks.CARD_NAME).write_text(
        "## Now\nworking\n\n## Plan\n- [ ] claim .name and .mood\n",
        encoding="utf-8",
    )
    _portal(tmp_path, "t2", card={"active": True})
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    state = json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )
    rows = state[am.STATE_KEY]["rows"]
    # The card row's own discharge is the plan-carrying write itself.
    assert rows["a-card"]["retired"] == am.RETIRED_DISCHARGED
    # The claims row's anchor (".name") appears in a plan row → adopted:
    # the course engine owns it from here.
    assert rows["a-claims"]["retired"] == am.RETIRED_ADOPTED


def test_a_plan_that_omits_a_row_defers_it(tmp_path):
    _score(tmp_path, [_claims_row(window=1, cadence=2)])
    env = _env(tmp_path)
    _portal(tmp_path, "t1")
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    (tmp_path / hooks.CARD_NAME).write_text(
        "## Now\nworking\n\n## Plan\n- [ ] ship the fix\n",
        encoding="utf-8",
    )
    _portal(tmp_path, "t2", card={"active": True})
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    state = json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )
    assert (
        state[am.STATE_KEY]["rows"]["a-claims"]["retired"]
        == am.RETIRED_DEFERRED
    )
    # Deferred means deferred: many boundaries later, still no escalation.
    for token in ("t3", "t4", "t5"):
        _portal(tmp_path, token, card={"active": True})
        out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
        assert "assign overdue" not in _inject(out)


def test_stop_reads_back_the_never_discharged_rows(tmp_path):
    _score(tmp_path, [
        _claims_row(window=9),
        {
            "id": "a-event", "kind": "event",
            "title": "answer the waking event",
            "discharge": "the reply", "window": None, "cadence": 4,
            "detail": [], "anchor": "waking event",
        },
    ])
    env = _env(tmp_path)
    _portal(tmp_path, "t1")
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    _portal(tmp_path, "t2")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    text = _inject(out)
    assert "never discharged or deferred" in text
    assert "claim .name" in text
    # The waking-event row is the Stop delivery clause's seam, not this
    # readback's — naming it twice would be two surfaces disagreeing.
    assert "answer the waking event" not in text


def test_stop_does_not_tick_the_overdue_clock(tmp_path):
    _score(tmp_path, [_claims_row(window=2, cadence=2)])
    env = _env(tmp_path)
    _portal(tmp_path, "t1")
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    before = json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )[am.STATE_KEY]["ordinal"]
    # Stop can fire more than once; a re-fire is not a boundary the run lived.
    for token in ("t2", "t3"):
        _portal(tmp_path, token)
        hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    after = json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )[am.STATE_KEY]["ordinal"]
    assert after == before
