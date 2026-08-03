"""The gate receipt gets a chip (#1048).

`.gate-receipt.json` decides whether a run may merge — `workflow.md`
self-merge condition 1 is, operationally, a question about this file — and
it was the only control file in the outbox with no boundary signal.

Every test here drives `hooks.compute_neutral` with a real hook context and
a real receipt on disk, rather than calling `_gate_chip` directly: the
defect this guards against is the verdict never *reaching* a boundary, and
a unit test of the formatter cannot see that.
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


def _portal(token: str, **extra):
    payload = {
        "change_token": token,
        "run": {"id": "run-260803-0737-4l69"},
        "budget": {"elapsed_seconds": 30, "budget_seconds": 14400},
        "attention": {"pending_outbox_file_count": 0},
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


def _receipt(outbox, **fields):
    payload = {
        "verdict": "GREEN",
        "head": "e59f52715d71ef1aa3a0de86f9c50ee19b10aa33",
        "run_id": "run-260803-0737-4l69",
    }
    payload.update(fields)
    (outbox / hooks.GATE_RECEIPT_NAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_the_verdict_and_its_head_reach_a_mid_run_boundary(tmp_path):
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    _receipt(outbox)
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "gate GREEN@e59f527" in (out["inject"] or "")


def test_a_red_verdict_says_red(tmp_path):
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    _receipt(outbox, verdict="RED")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "gate RED@e59f527" in (out["inject"] or "")


def test_no_receipt_renders_no_chip_at_all(tmp_path):
    """A run that has not gated is not a run that failed.

    Silence is the honest answer; a chip claiming any verdict here would be
    a pessimistic lie where the truth is "no measurement exists".
    """
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "gate " not in (out["inject"] or "")


def test_a_malformed_receipt_renders_no_chip(tmp_path):
    """Absent, unreadable and malformed all mean *no trustworthy record*."""
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    (outbox / hooks.GATE_RECEIPT_NAME).write_text("{not json", encoding="utf-8")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "gate " not in (out["inject"] or "")


def test_a_receipt_with_no_verdict_renders_no_chip(tmp_path):
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    _receipt(outbox, verdict="")
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "gate " not in (out["inject"] or "")


def test_a_tree_that_moved_under_the_gate_says_so(tmp_path):
    """#917's exact fact, in words rather than as an unresolvable mark."""
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    _receipt(outbox, tree_moved_during_gate=True)
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "gate GREEN@e59f527 (moved)" in (out["inject"] or "")


def test_the_chip_never_opens_the_gate_by_itself(tmp_path):
    """Ambient, like the produce count.

    A green receipt sitting in the outbox is not news at every boundary. If
    this chip could keep the bar alive it would render on every tool call of
    a run that gated once, which is the *fires constantly for a non-reason*
    death — and it would do it to the one signal that must stay readable.
    """
    ctx, outbox, portal = _ctx(tmp_path)
    quiet = _portal("t1", card={"stale": False, "state": "ok"})
    portal.write_text(json.dumps(quiet), encoding="utf-8")
    _receipt(outbox)
    out = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert out["inject"] is None


def test_the_chip_follows_a_re_gate_rather_than_caching(tmp_path):
    """A resident gates more than once in a long run.

    A cached verdict is exactly the stale claim this chip exists to make
    visible, so the receipt is re-read at every boundary.
    """
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    _receipt(outbox, verdict="RED", head="aaaaaaa1111111111111111111111111111111")
    first = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "gate RED@aaaaaaa" in (first["inject"] or "")

    portal.write_text(json.dumps(_portal("t2")), encoding="utf-8")
    _receipt(outbox, verdict="GREEN", head="bbbbbbb2222222222222222222222222222222")
    second = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "gate GREEN@bbbbbbb" in (second["inject"] or "")
    assert "RED" not in (second["inject"] or "")
