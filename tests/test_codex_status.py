"""Codex session-rollout quota collector — the head-less subscription-quota
source for the Codex Shell (§8). Verified live 2026-06-28: ``token_count``
events in a rollout JSONL carry ``rate_limits`` (5h + weekly)."""

import pytest

from brr import codex_status, facets


# A real-shape token_count payload (trimmed from a live rollout, 2026-06-28).
_PAYLOAD = {
    "type": "token_count",
    "info": {
        "total_token_usage": {"total_tokens": 6965046},
        "last_token_usage": {"input_tokens": 198358, "total_tokens": 199850},
        "model_context_window": 258400,
    },
    "rate_limits": {
        "limit_id": "codex",
        "primary": {"used_percent": 80.0, "window_minutes": 300, "resets_at": 1782572885},
        "secondary": {"used_percent": 27.0, "window_minutes": 10080, "resets_at": 1783095178},
        "plan_type": "plus",
    },
}


def test_supported_is_per_vessel():
    assert codex_status.supported("codex") is True
    assert codex_status.supported("codex-mini") is True
    assert codex_status.supported("claude") is False
    assert codex_status.supported(None) is False


def test_parse_token_count_quota_and_context():
    levels = codex_status.parse_token_count(_PAYLOAD)
    # used_percent → headroom = 100 - used; window_minutes → human label.
    assert "5h 20% left" in levels["quota"]["summary"]
    assert "7d 73% left" in levels["quota"]["summary"]
    assert "resets" in levels["quota"]["summary"]
    assert levels["plan_type"] == "plus"
    # Both windows' used_percent survive numerically now, not just the 5h
    # one — the weekly (secondary) window was previously discarded past the
    # rendered summary string (kb/design-director-loop.md §B1).
    assert levels["quota"]["primary_used_percent"] == 80.0
    assert levels["quota"]["secondary_used_percent"] == 27.0
    assert levels["quota"]["primary_remaining_percent"] == 20.0
    assert levels["quota"]["secondary_remaining_percent"] == 73.0
    # Raw reset epochs pass through alongside the formatted summary text —
    # the dashboard's window-track visual needs a machine-parseable instant.
    assert levels["quota"]["primary_resets_at"] == 1782572885.0
    assert levels["quota"]["secondary_resets_at"] == 1783095178.0
    # context headroom estimated from last input_tokens / window.
    assert "context left (est)" in levels["context_window"]["summary"]
    assert 20 < levels["context_window"]["remaining_percentage"] < 30
    assert levels["tokens"]["input_tokens"] == 198358
    assert levels["tokens"]["output_tokens"] == 1492
    assert 76 < levels["tokens"]["context_window_used_percent"] < 77
    assert levels["source"] == "codex session rollout"


def test_parse_token_count_unrecognized_shape_is_empty():
    levels = codex_status.parse_token_count({"nope": True})
    assert "quota" not in levels and "context_window" not in levels


def test_parse_token_count_missing_secondary_stays_none():
    payload = {
        "rate_limits": {
            "primary": {"used_percent": 40.0, "window_minutes": 300},
        },
    }
    levels = codex_status.parse_token_count(payload)
    assert levels["quota"]["primary_used_percent"] == 40.0
    assert levels["quota"]["primary_remaining_percent"] == 60.0
    assert levels["quota"]["secondary_used_percent"] is None
    assert levels["quota"]["secondary_remaining_percent"] is None
    # No resets_at on either window in this payload — both stay None, not a
    # fabricated guess.
    assert levels["quota"]["primary_resets_at"] is None
    assert levels["quota"]["secondary_resets_at"] is None


def test_fmt_event_timestamp_falls_back_to_none_for_garbage():
    assert codex_status._fmt_event_timestamp("not-a-timestamp") is None
    assert codex_status._fmt_event_timestamp(None) is None
    assert codex_status._fmt_event_timestamp("2026-07-08T20:18:25.753Z") == "2026-07-08T20:18:25Z"


def test_facets_codex_collector_marks_spend_unimplemented():
    """Per-slot honesty: Codex collects quota + context, but has no $-spend
    gauge, so ``spend`` must read ``unimplemented`` (not ``absent``)."""
    levels = codex_status.parse_token_count(_PAYLOAD)
    res = facets.build(levels=levels, levels_collector=codex_status.COLLECTED_SLOTS)
    assert res["quota"]["status"] == "known"
    assert res["context_window"]["status"] == "known"
    assert res["spend"]["status"] == "unimplemented"


def test_parse_token_count_carries_each_windows_duration():
    """The rollout seam must hand downstream readers the same structural
    duration the app-server seam does (2026-07-13): a window's slot is not its
    identity — a weekly window can arrive as `primary` — so `window_minutes`
    has to survive the parse, not just get rendered into the summary text."""
    quota = codex_status.parse_token_count(_PAYLOAD)["quota"]
    assert quota["primary_window_minutes"] == 300.0
    assert quota["secondary_window_minutes"] == 10080.0

    weekly_in_primary = codex_status.parse_token_count(
        {
            "rate_limits": {
                "primary": {"used_percent": 41.0, "window_minutes": 10080},
                "secondary": None,
            }
        }
    )["quota"]
    assert weekly_in_primary["primary_window_minutes"] == 10080.0
    assert weekly_in_primary["primary_remaining_percent"] == 59.0
    assert weekly_in_primary["secondary_window_minutes"] is None


# ── Issue #195: exact thread-id correlation ──────────────────────────────


# ── credits-based plans (2026-07-24) ──────────────────────────────────
#
# The payload below is verbatim from this account's rollout at the moment
# two dispatched Codex workers died on their first token: no windows at
# all, the whole quota fact in ``credits``. The window reader produced no
# summary, the ``quota`` slot came back absent, and absent renders
# everywhere as "no reading yet" rather than "this Shell cannot run".

_EXHAUSTED_CREDITS = {
    "limit_id": "premium",
    "limit_name": None,
    "primary": None,
    "secondary": None,
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
    "individual_limit": None,
    "plan_type": None,
    "rate_limit_reached_type": None,
}


def test_exhausted_credits_produce_a_quota_slot_not_silence():
    """A zero-balance plan must be *readable*, not absent.

    This is the regression that cost two worker dispatches: with no
    ``primary``/``secondary`` windows the collector emitted no ``quota``
    key at all, so every downstream surface reported the Shell as
    unmeasured while it was in fact unusable.
    """
    levels = codex_status.parse_token_count({"rate_limits": _EXHAUSTED_CREDITS})
    quota = levels.get("quota")
    assert quota is not None, "an exhausted Shell must not read as unmeasured"
    assert quota["credits_exhausted"] is True
    assert "exhausted" in quota["summary"]
    assert quota["credits_balance"] == "0"


def test_exhausted_credits_without_a_proven_plan_reads_unmeasured_not_zero():
    """Superseded by #2084 (2026-09-22) — was
    ``test_exhausted_credits_bind_pacing_at_zero``, asserting ``== 0.0``.

    ``_EXHAUSTED_CREDITS`` carries ``"plan_type": None`` — the exact same
    shape (only a ``credits`` block, no window, no provable plan) that three
    subscription-account strands died on in September, while their real
    session/week buckets read healthy moments before and after. Nothing in
    *this one reading* tells the two incidents apart: the April/July "two
    workers died on their first token" case and the September false wall are
    byte-identical once parsed — the discriminator #2084 asks for (bucket
    history, or the parent seat's own concurrent window reading) is cross-
    reading state this module does not keep yet (`runner_quota`'s own
    Doubts). Given that, binding on an unrecognised plan is the worse
    failure to ship twice: a truly exhausted pay-per-use account with no
    reported ``plan_type`` now reads "unmeasured" and pays one wasted
    dispatch attempt (caught immediately by the ordinary
    ``quota_exhausted`` failure path on the real API call) instead of
    incorrectly killing a healthy subscription account outright.
    """
    from brr import runner_quota

    levels = codex_status.parse_token_count({"rate_limits": _EXHAUSTED_CREDITS})
    assert runner_quota.binding_quota_remaining_pct(levels) is None


def test_exhausted_credits_are_not_filed_under_a_window_slot():
    """Zero headroom is a *credits* fact, not a 5h/weekly one.

    The module docstring's 2026-07-13 lesson — the slot a number arrives
    in is not its identity — applies to the fix as much as to the bug.
    """
    levels = codex_status.parse_token_count({"rate_limits": _EXHAUSTED_CREDITS})
    quota = levels["quota"]
    for key in (
        "primary_used_percent",
        "secondary_used_percent",
        "primary_remaining_percent",
        "secondary_remaining_percent",
        "primary_window_minutes",
        "secondary_window_minutes",
    ):
        assert key not in quota, f"{key} would mislabel a credits fact as a window"


def test_measured_windows_are_never_overridden_by_credits():
    """Credits ride along; a measured window still binds."""
    from brr import runner_quota

    levels = codex_status.parse_token_count({
        "rate_limits": {
            "primary": {"used_percent": 20.0, "window_minutes": 300},
            "secondary": {"used_percent": 8.0, "window_minutes": 10080},
            "credits": {"has_credits": True, "unlimited": False, "balance": "42"},
        }
    })
    quota = levels["quota"]
    assert quota["primary_remaining_percent"] == 80.0
    assert "credits 42" in quota["summary"]
    assert quota["credits_exhausted"] is False
    assert runner_quota.binding_quota_remaining_pct(levels) == 80.0


def test_subscription_window_is_not_overridden_by_absent_credit_wallet():
    """Plus reports a real window and ``has_credits: false`` together.

    The latter means there is no separate credit-wallet fallback; it must not
    turn the usable subscription window into a binding zero.
    """
    from brr import runner_quota

    levels = codex_status.parse_token_count({
        "rate_limits": {
            "primary": {"used_percent": 16.0, "window_minutes": 10080},
            "secondary": None,
            "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
            "plan_type": "plus",
        }
    })
    quota = levels["quota"]
    assert quota["summary"].startswith("7d 84% left")
    assert "credits exhausted" not in quota["summary"]
    assert quota.get("credits_exhausted") is None
    assert runner_quota.binding_quota_remaining_pct(levels) == 84.0


def test_a_positive_balance_never_becomes_a_percentage():
    """A balance has no denominator. Null beats a borrowed ratio."""
    from brr import runner_quota

    levels = codex_status.parse_token_count({
        "rate_limits": {
            "primary": None,
            "secondary": None,
            "credits": {"has_credits": True, "unlimited": False, "balance": "1200"},
        }
    })
    assert levels["quota"]["summary"] == "credits 1200"
    assert runner_quota.binding_quota_remaining_pct(levels) is None


def test_unlimited_credits_report_no_constraint():
    from brr import runner_quota

    levels = codex_status.parse_token_count({
        "rate_limits": {"primary": None, "secondary": None,
                        "credits": {"unlimited": True, "has_credits": False}},
    })
    assert levels["quota"]["summary"] == "credits unlimited"
    assert "credits_exhausted" not in levels["quota"]
    assert runner_quota.binding_quota_remaining_pct(levels) is None


@pytest.mark.parametrize(
    "credits",
    [None, {}, "garbage", 7, {"has_credits": None}, {"balance": "0"}],
)
def test_an_unprovable_credits_block_stays_absent(credits):
    """A guard may only assert what it can be proven wrong about.

    ``has_credits`` missing or ``None`` is not evidence of exhaustion —
    it is evidence of a shape this reader does not understand. Silence is
    the correct output; a false 'exhausted' would pause every Codex
    dispatch on a schema change.
    """
    levels = codex_status.parse_token_count(
        {"rate_limits": {"primary": None, "secondary": None, "credits": credits}}
    )
    assert "quota" not in levels
