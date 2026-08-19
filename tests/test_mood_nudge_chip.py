"""The blank-mood boundary chip (the mood seam's ergonomics ask, 2026-08-03).

`card stale`, one tier softer: a run old enough to plausibly want a mood
and still carrying none earns one soft `mood?` ask on the boundary bar —
ambient (never opens the gate by itself, mirroring `_gate_chip`) and
latched (fires once per run, never a repeat — #779's "a soft nag has no
counter" is the exact defect a per-boundary re-render would be).

Every test here drives `hooks.compute_neutral` with a real hook context
and a real `.mood` (or its absence) on disk, the same discipline
`tests/test_gate_chip.py` uses: the defect this guards against is the
chip never *reaching* a boundary, which a unit test of a formatter cannot
see.
"""

from __future__ import annotations

import json

from brr import hooks


def _ctx(tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir(exist_ok=True)
    portal = outbox / "portal-state.json"
    ctx = hooks.HookContext({
        "BRR_OUTBOX_DIR": str(outbox),
        "BRR_PORTAL_STATE": str(portal),
        "BRR_RUN_ID": "run-260803-0737-4l69",
    })
    return ctx, outbox, portal


def _portal(token: str, *, elapsed_seconds=901, **extra):
    payload = {
        "change_token": token,
        "run": {"id": "run-260803-0737-4l69"},
        "budget": {"elapsed_seconds": elapsed_seconds, "budget_seconds": 14400},
        # Production always writes both counts (portals.write_portal_state);
        # omitting pending_event_count renders the honest ✉? unknown chip
        # (#1000), which would keep the bar alive for the wrong reason here.
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"events": []},
        "outbound": {},
        # Something laden, so the bar renders at all — the chip is ambient
        # by design and must never open the gate on its own.
        "card": {"stale": True, "state": "stale", "age_seconds": 400},
        "resources": {},
        "produce": {"known": True, "counts": {}},
        "name": {"written": True},
    }
    payload.update(extra)
    return payload


def test_an_old_run_with_no_mood_gets_the_nudge(tmp_path):
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "mood?" in (out["inject"] or "")


def test_a_young_run_gets_the_hint_on_its_first_bar(tmp_path):
    # The 15m floor dropped 2026-08-19 (evt-…-mhrx: "at the very beginning,
    # we should also hint that it's yours to change") — change-gating
    # prices the early hint at one render, so age stopped being the guard.
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(
        json.dumps(_portal("t1", elapsed_seconds=120)), encoding="utf-8"
    )
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "mood?" in (out["inject"] or "")


def test_a_written_mood_forever_disqualifies_the_nudge(tmp_path):
    """Never renders once any `.mood` write has happened.

    Even though the run clears the elapsed floor and a `.mood` chip does
    not itself police staleness, a run wearing a face is never asked
    to put one on.
    """
    ctx, outbox, portal = _ctx(tmp_path)
    (outbox / hooks.MOOD_NAME).write_text("curious\n", encoding="utf-8")
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    inject = out["inject"] or ""
    assert "mood?" not in inject
    assert "curious" in inject


def test_the_nudge_never_opens_the_gate_by_itself(tmp_path):
    """Ambient, like the gate chip and the produce count.

    A quiet boundary — nothing else laden — must stay silent even though
    this run is old and moodless; if the nudge could keep the bar alive it
    would render on every tool call of every long moodless run, the *fires
    constantly for a non-reason* death this whole family is written
    against.
    """
    ctx, outbox, portal = _ctx(tmp_path)
    # Boundary 0 (w-54): burn the first bar while the run is still too
    # young for the nudge, so eligibility is the only new fact below.
    young = _portal("t1", elapsed_seconds=120,
                    card={"stale": False, "state": "ok"})
    portal.write_text(json.dumps(young), encoding="utf-8")
    hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    # Same token, now old and moodless: eligibility alone opens nothing.
    quiet = _portal("t1", card={"stale": False, "state": "ok"})
    portal.write_text(json.dumps(quiet), encoding="utf-8")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert out["inject"] is None


def test_the_nudge_fires_once_and_then_latches_silent(tmp_path):
    """The latch: shown once, never again — even while still eligible.

    Two boundaries, both old enough and both moodless, each with something
    else laden so the bar actually renders. The chip earns the first and
    only the first.
    """
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    first = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "mood?" in (first["inject"] or "")

    portal.write_text(json.dumps(_portal("t2")), encoding="utf-8")
    second = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "mood?" not in (second["inject"] or "")
    # The card-stale detail line still carries — the latch silences only
    # the nudge chip, not the rest of the bar.
    assert "card" in (second["inject"] or "")


def test_a_mood_write_that_later_clears_still_disqualifies_the_nudge(tmp_path):
    """The disqualification is "a write happened", not "the file has
    content right now" — a resident that writes then clears `.mood` has
    still worn a face this run and must never be asked again."""
    ctx, outbox, portal = _ctx(tmp_path)
    (outbox / hooks.MOOD_NAME).write_text("curious\n", encoding="utf-8")
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    first = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "mood?" not in (first["inject"] or "")

    (outbox / hooks.MOOD_NAME).write_text("", encoding="utf-8")
    portal.write_text(json.dumps(_portal("t2")), encoding="utf-8")
    second = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "mood?" not in (second["inject"] or "")


# test_a_quiet_eligible_boundary_does_not_burn_the_latch retired 2026-08-19:
# with the nudge floor at 0 the run's first rendered bar always carries
# `mood?`, so an "eligible but never rendered" boundary is unconstructible.
# The latch-on-render discipline itself is pinned by
# test_the_nudge_fires_once_and_then_latches_silent above.
