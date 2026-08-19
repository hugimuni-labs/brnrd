"""The topic-discoverability boundary chip (the-run-that-claims-its-thread,
steer 2026-08-12).

A run that has claimed no topic (no `.topics`, no item taken) earns a
`topic?` marker on the boundary bar — ambient (never opens the gate by
itself, mirroring `_gate_chip`/`mood?`) and capped at a *few* renders per
run (2), not exactly one: the maintainer's own furniture rule ("we
shouldn't repeat the same un-interactive data into each boundary") rules
out re-rendering every tick, but one shot can miss a boundary that renders
nothing else laden.

Every test here drives `hooks.compute_neutral` with a real hook context
and a real `.topics` (or its absence) on disk, the same discipline
`tests/test_mood_nudge_chip.py` uses.
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
        "BRR_RUN_ID": "run-260812-0001-topx",
    })
    return ctx, outbox, portal


def _portal(token: str, *, item_count=0, **extra):
    counts = {}
    if item_count:
        counts["item"] = item_count
    payload = {
        "change_token": token,
        "run": {"id": "run-260812-0001-topx"},
        "budget": {"elapsed_seconds": 60, "budget_seconds": 14400},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"events": []},
        "outbound": {},
        # Something laden, so the bar renders at all — the chip is ambient
        # by design and must never open the gate on its own.
        "card": {"stale": True, "state": "stale", "age_seconds": 400},
        "resources": {},
        "produce": {"known": True, "counts": counts},
        "name": {"written": True},
    }
    payload.update(extra)
    return payload


def test_a_topicless_run_gets_the_chip(tmp_path):
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "topic?" in (out["inject"] or "")


def test_a_written_topics_claim_disqualifies_the_chip(tmp_path):
    ctx, outbox, portal = _ctx(tmp_path)
    (outbox / hooks.TOPICS_NAME).write_text("the-loom\n", encoding="utf-8")
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "topic?" not in (out["inject"] or "")


def test_an_item_taken_disqualifies_the_chip_with_no_topics_file(tmp_path):
    """The items store's own signal (an `item` relic in this run's produce
    counts) satisfies the check exactly like a `.topics` claim would."""
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1", item_count=1)), encoding="utf-8")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "topic?" not in (out["inject"] or "")


def test_the_chip_never_opens_the_gate_by_itself(tmp_path):
    """Ambient, like the mood nudge and the gate chip: a quiet boundary —
    nothing else laden — must stay silent even though this run is
    topicless."""
    ctx, outbox, portal = _ctx(tmp_path)
    # Boundary 0 (w-54): burn the first bar with a claim in place, so the
    # chip's own eligibility is the only new fact below.
    (outbox / hooks.TOPICS_NAME).write_text("the-loom\n", encoding="utf-8")
    quiet = _portal("t1", card={"stale": False, "state": "ok"})
    portal.write_text(json.dumps(quiet), encoding="utf-8")
    hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    # The claim disappears (fresh-read, no latch), token unchanged: a
    # topicless run alone opens nothing.
    (outbox / hooks.TOPICS_NAME).unlink()
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert out["inject"] is None


def test_the_chip_renders_once_under_change_gating(tmp_path):
    """The furniture rule, w-54 edition: a topicless run stays topicless
    across N laden boundaries, and the chip renders exactly once — the
    old cap of two guarded against a render landing on a boundary nobody
    saw, and commit-on-render (the change-gate advances only when the bar
    actually injected) closes that hole structurally. The cap remains as
    an upper bound the gate now undershoots."""
    ctx, outbox, portal = _ctx(tmp_path)
    rendered = 0
    for i in range(6):
        portal.write_text(json.dumps(_portal(f"t{i}")), encoding="utf-8")
        out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
        if "topic?" in (out["inject"] or ""):
            rendered += 1
    assert rendered == 1


def test_the_chip_stops_immediately_once_a_claim_lands(tmp_path):
    """A claim written mid-run silences the chip on the very next boundary
    — no "ever written" latch like `.mood`'s; the claim is the fact, read
    fresh every time."""
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    first = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "topic?" in (first["inject"] or "")

    (outbox / hooks.TOPICS_NAME).write_text("the-loom\n", encoding="utf-8")
    portal.write_text(json.dumps(_portal("t2")), encoding="utf-8")
    second = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "topic?" not in (second["inject"] or "")
    # The card-stale detail line still carries — silencing the chip doesn't
    # silence the rest of the bar.
    assert "card" in (second["inject"] or "")


def test_a_quiet_eligible_boundary_does_not_burn_the_cap(tmp_path):
    """Latch on the render, not on the decision (#728's rule, same as the
    mood nudge): a boundary where the chip was eligible but nothing else
    was laden must not spend one of the two renders."""
    ctx, outbox, portal = _ctx(tmp_path)
    # Boundary 0 (w-54): burn the first bar with a claim in place.
    (outbox / hooks.TOPICS_NAME).write_text("the-loom\n", encoding="utf-8")
    quiet = _portal("t1", card={"stale": False, "state": "ok"})
    portal.write_text(json.dumps(quiet), encoding="utf-8")
    hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    # Eligible (claim gone) but the gate never opens: no render spent.
    (outbox / hooks.TOPICS_NAME).unlink()
    quiet_out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert quiet_out["inject"] is None

    laden = _portal("t2")
    portal.write_text(json.dumps(laden), encoding="utf-8")
    laden_out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "topic?" in (laden_out["inject"] or "")


def test_mood_and_topic_chips_coexist_independently(tmp_path):
    """A run can wear a face and still have claimed no thread — the two
    ambient nudges are unrelated and both may render on the same bar."""
    ctx, outbox, portal = _ctx(tmp_path)
    (outbox / hooks.MOOD_NAME).write_text("curious\n", encoding="utf-8")
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    inject = out["inject"] or ""
    assert "curious" in inject
    assert "topic?" in inject
