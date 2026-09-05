from __future__ import annotations

from brr import hold_verb, resource_hold


class TestParseHold:
    def test_bare_marker_defaults(self):
        spec, error = hold_verb.parse_hold({"hold": "true"})
        assert error is None
        assert spec == {
            "reason": resource_hold.REASON_RESIDENT_REQUESTED,
            "provider": None,
            "resume_condition": resource_hold.RESUME_OPERATOR,
            "reset_deadline_hint": None,
        }

    def test_empty_string_marker_is_also_accepted(self):
        spec, error = hold_verb.parse_hold({"hold": ""})
        assert error is None
        assert spec["resume_condition"] == resource_hold.RESUME_OPERATOR

    def test_reason_and_provider(self):
        spec, error = hold_verb.parse_hold({
            "hold": "true", "reason": "near weekly quota", "provider": "codex",
        })
        assert error is None
        assert spec["reason"] == "near weekly quota"
        assert spec["provider"] == "codex"

    def test_resume_reset_alias(self):
        spec, error = hold_verb.parse_hold({"hold": "true", "resume": "reset"})
        assert error is None
        assert spec["resume_condition"] == resource_hold.RESUME_RESET

    def test_resume_quota_reset_alias(self):
        spec, error = hold_verb.parse_hold({"hold": "true", "resume": "quota_reset"})
        assert error is None
        assert spec["resume_condition"] == resource_hold.RESUME_RESET

    def test_resume_manual_alias(self):
        spec, error = hold_verb.parse_hold({"hold": "true", "resume": "manual"})
        assert error is None
        assert spec["resume_condition"] == resource_hold.RESUME_OPERATOR

    def test_unrecognised_resume_is_refused(self):
        spec, error = hold_verb.parse_hold({"hold": "true", "resume": "sometime"})
        assert spec is None
        assert "sometime" in error

    def test_explicit_reset_timestamp_parses(self):
        spec, error = hold_verb.parse_hold({
            "hold": "true", "resume": "reset", "reset": "2026-09-12T00:00:00Z",
        })
        assert error is None
        assert spec["reset_deadline_hint"] is not None

    def test_malformed_reset_timestamp_is_refused(self):
        spec, error = hold_verb.parse_hold({
            "hold": "true", "resume": "reset", "reset": "not-a-date",
        })
        assert spec is None
        assert "not-a-date" in error

    def test_non_marker_value_is_refused(self):
        spec, error = hold_verb.parse_hold({"hold": "why"})
        assert spec is None
        assert "hold:" in error

    def test_missing_key_defaults_same_as_bare_marker(self):
        # The daemon's own call site gates on `if "hold" in fm:` before ever
        # calling this — mirroring `parse_await`'s identical stance, this
        # function only validates the *value*, never key presence.
        spec, error = hold_verb.parse_hold({})
        assert error is None
        assert spec["resume_condition"] == resource_hold.RESUME_OPERATOR
