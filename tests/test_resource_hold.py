"""Unit tests for the pure ``resource_hold`` primitives.

Daemon-integration behaviour (arming on a real worker failure, deferring
sibling events, resuming a held run through a fresh dispatch) lives in
``tests/test_daemon_resource_hold.py`` — this file only exercises the
functions that have no filesystem or daemon state at all.
"""

from __future__ import annotations

from brr import resource_hold


class TestBuild:
    def test_defaults(self):
        meta = resource_hold.build(
            reason=resource_hold.REASON_QUOTA_EXHAUSTED,
            provider="codex",
        )
        assert meta["reason"] == resource_hold.REASON_QUOTA_EXHAUSTED
        assert meta["provider"] == "codex"
        assert meta["resume_condition"] == resource_hold.RESUME_OPERATOR
        assert meta["resume_kind"] == resource_hold.RESUME_UNSUPPORTED
        assert meta["reset_deadline"] is None
        assert meta["released"] is False
        assert meta["released_at"] is None
        assert meta["released_by"] is None
        assert meta["accumulated_event_ids"] == []
        assert meta["generation"] == 1
        assert meta["armed_at"]

    def test_native_resume_kind_requires_a_session_id(self):
        # Asking for native resume without a session id to resume *into*
        # is a contradiction — degrade to the honest "unsupported" rather
        # than publish a resume_kind nothing can act on.
        meta = resource_hold.build(
            reason=resource_hold.REASON_QUOTA_EXHAUSTED,
            provider="codex",
            resume_kind=resource_hold.RESUME_NATIVE,
            native_session_id=None,
        )
        assert meta["resume_kind"] == resource_hold.RESUME_UNSUPPORTED

        meta = resource_hold.build(
            reason=resource_hold.REASON_QUOTA_EXHAUSTED,
            provider="codex",
            resume_kind=resource_hold.RESUME_NATIVE,
            native_session_id="thread-123",
        )
        assert meta["resume_kind"] == resource_hold.RESUME_NATIVE

    def test_unrecognised_resume_condition_degrades_to_operator(self):
        meta = resource_hold.build(
            reason=resource_hold.REASON_QUOTA_EXHAUSTED,
            provider="codex",
            resume_condition="typo-condition",
            reset_deadline=12345.0,
        )
        assert meta["resume_condition"] == resource_hold.RESUME_OPERATOR
        # A condition that isn't "reset" must not carry a stray deadline
        # forward — nothing should ever read it as meaningful.
        assert meta["reset_deadline"] is None

    def test_operator_condition_drops_any_reset_deadline(self):
        meta = resource_hold.build(
            reason=resource_hold.REASON_RESIDENT_REQUESTED,
            provider="codex",
            resume_condition=resource_hold.RESUME_OPERATOR,
            reset_deadline=99999.0,
        )
        assert meta["reset_deadline"] is None

    def test_reset_condition_keeps_its_deadline(self):
        meta = resource_hold.build(
            reason=resource_hold.REASON_QUOTA_EXHAUSTED,
            provider="codex",
            resume_condition=resource_hold.RESUME_RESET,
            reset_deadline=42.0,
        )
        assert meta["resume_condition"] == resource_hold.RESUME_RESET
        assert meta["reset_deadline"] == 42.0


class TestIsActive:
    def test_none_is_not_active(self):
        assert resource_hold.is_active(None) is False

    def test_empty_dict_is_not_active(self):
        assert resource_hold.is_active({}) is False

    def test_fresh_build_is_active(self):
        meta = resource_hold.build(reason="x", provider="codex")
        assert resource_hold.is_active(meta) is True

    def test_released_is_not_active(self):
        meta = resource_hold.build(reason="x", provider="codex")
        released = resource_hold.mark_released(meta, by="operator")
        assert resource_hold.is_active(released) is False


class TestMarkReleased:
    def test_stamps_fields_and_does_not_mutate_input(self):
        meta = resource_hold.build(reason="x", provider="codex")
        released = resource_hold.mark_released(meta, by="operator", now=1000.0)
        assert released["released"] is True
        assert released["released_by"] == "operator"
        assert released["released_at"]
        # Original untouched — callers persist the return value, not the
        # argument, so a caller that forgets to reassign fails loudly
        # (still armed) rather than silently double-releasing.
        assert meta["released"] is False

    def test_idempotent_by_the_read_not_a_lock(self):
        meta = resource_hold.build(reason="x", provider="codex")
        once = resource_hold.mark_released(meta, by="operator")
        twice = resource_hold.mark_released(once, by="reset")
        # Second release is a no-op in effect: still released, and the
        # *first* releaser's identity is what a reader should trust more,
        # but this function itself just records whatever it's told — the
        # "once" guarantee lives in the caller checking is_active() first.
        assert twice["released"] is True


class TestAccumulateEvent:
    def test_appends_and_dedupes(self):
        meta = resource_hold.build(reason="x", provider="codex")
        meta = resource_hold.accumulate_event(meta, "evt-1")
        meta = resource_hold.accumulate_event(meta, "evt-2")
        meta = resource_hold.accumulate_event(meta, "evt-1")
        assert meta["accumulated_event_ids"] == ["evt-1", "evt-2"]

    def test_does_not_mutate_input(self):
        meta = resource_hold.build(reason="x", provider="codex")
        updated = resource_hold.accumulate_event(meta, "evt-1")
        assert meta["accumulated_event_ids"] == []
        assert updated["accumulated_event_ids"] == ["evt-1"]


class TestResetConditionMet:
    def test_operator_condition_never_releases_on_a_clock(self):
        meta = resource_hold.build(
            reason="x", provider="codex",
            resume_condition=resource_hold.RESUME_OPERATOR,
        )
        # Even a wildly-past "deadline" (which build() would have dropped
        # anyway) must not release an operator-only hold.
        meta["reset_deadline"] = 0.0
        assert resource_hold.reset_condition_met(meta, now=10_000.0) is False

    def test_reset_condition_with_no_deadline_never_releases(self):
        meta = resource_hold.build(
            reason="x", provider="codex",
            resume_condition=resource_hold.RESUME_RESET,
        )
        assert meta["reset_deadline"] is None
        assert resource_hold.reset_condition_met(meta, now=10_000.0) is False

    def test_reset_condition_releases_once_deadline_passes(self):
        meta = resource_hold.build(
            reason="x", provider="codex",
            resume_condition=resource_hold.RESUME_RESET,
            reset_deadline=1000.0,
        )
        assert resource_hold.reset_condition_met(meta, now=999.0) is False
        assert resource_hold.reset_condition_met(meta, now=1000.0) is True
        assert resource_hold.reset_condition_met(meta, now=1001.0) is True

    def test_already_released_hold_never_re_releases(self):
        meta = resource_hold.build(
            reason="x", provider="codex",
            resume_condition=resource_hold.RESUME_RESET,
            reset_deadline=1000.0,
        )
        meta = resource_hold.mark_released(meta, by="reset", now=1000.0)
        assert resource_hold.reset_condition_met(meta, now=2000.0) is False


class TestPortalProjection:
    def test_none_stays_none(self):
        assert resource_hold.portal_projection(None) is None

    def test_returns_a_copy(self):
        meta = resource_hold.build(reason="x", provider="codex")
        projected = resource_hold.portal_projection(meta)
        assert projected == meta
        projected["reason"] = "mutated"
        assert meta["reason"] == "x"
