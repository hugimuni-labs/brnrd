"""The shell-agnostic quota sample store and the burn measured off it.

The burn scenarios here are ported from `test_codex_status.py`, which measured
the same thing by scanning Codex session rollouts. The discipline under test is
unchanged — same-window filtering, the minimum-span refusal, the non-monotonic
clamp, the `sustainable` rule — but the evidence now comes from one store fed by
the level reads brr already performs, for either Shell.
"""

from datetime import datetime, timezone


from brr import usage_samples


WEEK_RESETS = 1784490642.0
WEEK_MINUTES = 10080.0


# --- burn: the ported discipline --------------------------------------------


# --- the store: recording -----------------------------------------------------


# ── the gauge that never goes blind (move 5, #1971 "the blind weekend") ──


def _claude_levels(now, used, resets):
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "session_used_percentage": used / 2,
        "session_resets_at": now - (now % 18000) + 18000,
        "week_used_percentage": used,
        "week_resets_at": resets,
        "updated_at": stamp,
    }


def test_retention_is_a_week_with_twice_the_burn_horizon_at_full_resolution():
    assert usage_samples._RETENTION_HOURS == 7 * 24
    assert usage_samples._FULL_RESOLUTION_HOURS == 2 * usage_samples.BURN_HORIZON_HOURS


