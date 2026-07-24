"""Codex session-rollout quota collector — the head-less subscription-quota
source for the Codex Shell (§8). Verified live 2026-06-28: ``token_count``
events in a rollout JSONL carry ``rate_limits`` (5h + weekly)."""

import pytest
import json

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


def test_load_levels_reads_newest_rollout(tmp_path, monkeypatch):
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "06" / "28"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout-2026-06-28T00-00-00-abc.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps({"timestamp": "t", "type": "event_msg", "payload": p})
            for p in (
                {"type": "user_message", "message": "hi"},
                _PAYLOAD,
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    levels = codex_status.load_levels()
    assert levels is not None
    assert "5h 20% left" in levels["quota"]["summary"]


def test_load_levels_no_sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent"))
    assert codex_status.load_levels() is None


def test_load_levels_updated_at_is_the_events_own_timestamp_not_scrape_time(
    tmp_path, monkeypatch
):
    """Live-caught 2026-07-09: a screenshot showed the 5h window rendered
    'critical, resets in now' while the weekly window read a healthy 81% —
    traced to ``updated_at`` being stamped with wall-clock "now" on every
    daemon poll tick, even when the rollout file hadn't been written to in
    hours (no codex run active). ``activity_dashboard.py::_quota_views``'s
    staleness check trusts this field, so an hours-stale snapshot always
    looked freshly-scraped — the same "lying usage panel" class already
    fixed for Claude (2026-07-07), reproduced here because this collector
    was assumed to have no idle-gap. ``updated_at`` must reflect the
    rollout event's own real time, not whenever brr happened to re-read it."""
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "08"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout-2026-07-08T00-00-00-abc.jsonl"
    old_timestamp = "2026-07-08T20:18:25.753Z"
    rollout.write_text(
        json.dumps({"timestamp": old_timestamp, "type": "event_msg", "payload": _PAYLOAD}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    levels = codex_status.load_levels()
    assert levels is not None
    # Reformatted to the shared updated_at shape, not left as wall-clock now.
    assert levels["updated_at"] == "2026-07-08T20:18:25Z"


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


def test_facets_levels_collector_bool_back_compat():
    """``levels_collector=True`` still means all level slots are wired."""
    res = facets.build(levels={}, levels_collector=True)
    assert res["spend"]["status"] == "absent"
    assert res["context_window"]["status"] == "absent"


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


def _write_rollout(path, *, used_percent: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "type": "token_count",
        "rate_limits": {
            "primary": {"used_percent": used_percent, "window_minutes": 300},
            "secondary": None,
        },
    }
    path.write_text(
        json.dumps({"timestamp": "t", "type": "event_msg", "payload": payload}),
        encoding="utf-8",
    )


def test_load_levels_exact_thread_id_ignores_newer_unrelated_rollout(
    tmp_path, monkeypatch,
):
    """A concurrent sibling Codex run's rollout can be newer-mtime than this
    run's own — the whole point of #195. Exact ``thread_id`` correlation must
    win over "whatever was touched most recently", not just over "no id"."""
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "22"
    mine_id = "a0d0f1e9-8aeb-4f27-8e3c-f72822288984"
    sibling_id = "2914d67e-aa77-477a-ad34-2f024f7458e8"
    mine = sessions / f"rollout-2026-07-22T00-00-00-{mine_id}.jsonl"
    _write_rollout(mine, used_percent=20.0)
    # A sibling's rollout, written *after* mine — newest-mtime would pick
    # this one and report the wrong run's quota.
    sibling = sessions / f"rollout-2026-07-22T00-05-00-{sibling_id}.jsonl"
    _write_rollout(sibling, used_percent=99.0)
    assert sibling.stat().st_mtime >= mine.stat().st_mtime

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    levels = codex_status.load_levels(thread_id=mine_id)
    assert levels is not None
    assert "80% left" in levels["quota"]["summary"]  # 100 - 20 used


def test_load_levels_no_thread_id_falls_back_to_newest_mtime(tmp_path, monkeypatch):
    """No id available at all (pre-``--json`` caller, or the Shell isn't
    codex) — the explicit compatibility fallback, unchanged from before
    #195."""
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "22"
    older = sessions / "rollout-2026-07-22T00-00-00-aaa.jsonl"
    _write_rollout(older, used_percent=10.0)
    newer = sessions / "rollout-2026-07-22T00-05-00-bbb.jsonl"
    _write_rollout(newer, used_percent=90.0)

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    levels = codex_status.load_levels()
    assert levels is not None
    assert "10% left" in levels["quota"]["summary"]  # 100 - 90 used (newest)


def test_load_levels_malformed_thread_id_is_honest_absence_not_fallback(
    tmp_path, monkeypatch,
):
    """A supplied-but-invalid id is not the same fact as no id being known.
    Falling back could read a sibling's rollout, so drift stays absent."""
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "22"
    rollout = sessions / "rollout-2026-07-22T00-00-00-only.jsonl"
    _write_rollout(rollout, used_percent=5.0)

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    for unsafe in ("../../etc/passwd", "../escape", "a/b", "", None, 42):
        if unsafe is None:
            continue  # None deliberately selects the compatibility fallback.
        assert codex_status.load_levels(thread_id=unsafe) is None


def test_load_levels_thread_id_given_but_absent_returns_none_not_fallback(
    tmp_path, monkeypatch,
):
    """A *proven* thread id that matches nothing is honest absence, not a
    silent fallback to some other rollout — falling back here would risk
    exactly the sibling cross-read #195 exists to prevent."""
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "22"
    unrelated = sessions / "rollout-2026-07-22T00-00-00-someone-else.jsonl"
    _write_rollout(unrelated, used_percent=50.0)

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    assert codex_status.load_levels(
        thread_id="a0d0f1e9-8aeb-4f27-8e3c-f72822288984"
    ) is None


def test_rollout_for_thread_exact_suffix_match(tmp_path):
    root = tmp_path / "sessions"
    thread_id = "a0d0f1e9-8aeb-4f27-8e3c-f72822288984"
    target = root / "2026" / "07" / "22" / f"rollout-2026-07-22T00-00-00-{thread_id}.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    decoy = root / "2026" / "07" / "22" / "rollout-2026-07-22T01-00-00-2914d67e-aa77-477a-ad34-2f024f7458e8.jsonl"
    decoy.write_text("{}", encoding="utf-8")

    assert codex_status._rollout_for_thread(root, thread_id) == target


def test_safe_thread_id_rejects_traversal_and_separators():
    assert codex_status._safe_thread_id("a0d0f1e9-8aeb-4f27-8e3c-f72822288984") == (
        "a0d0f1e9-8aeb-4f27-8e3c-f72822288984"
    )
    for bad in ("../etc", "a/b", "a\\b", "", None, 123, "x" * 200, "abc-123"):
        assert codex_status._safe_thread_id(bad) is None


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


def test_exhausted_credits_bind_pacing_at_zero():
    """The verdict has to reach the seam that acts on it.

    ``runner_quota.binding_quota_remaining_pct`` is what pacing, the
    schedule cadence, and the wake's posture line read. A ``quota`` slot
    that no consumer can turn into a number is a meter shipped dark.
    """
    from brr import runner_quota

    levels = codex_status.parse_token_count({"rate_limits": _EXHAUSTED_CREDITS})
    assert runner_quota.binding_quota_remaining_pct(levels) == 0.0


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
