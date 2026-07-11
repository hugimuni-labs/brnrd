"""Codex app-server quota probe (#315) — parse, cache, degrade, merge.

The probe itself spawns ``codex app-server`` and talks JSON-RPC; these tests
never spawn it (a unit suite must not depend on a logged-in Codex). They pin the
four things that can rot without anyone noticing: the response *shape* we parse,
the TTL cache, the degrade-to-stale-cache path (the whole point — an idle or
offline Codex must still report *a real number, honestly aged*, never a blank),
and the freshest-wins merge against the passive rollout read.
"""

from __future__ import annotations

import pytest

from brr import codex_usage


# A real ``account/rateLimits/read`` result, captured live from codex-cli
# 0.144.1 (2026-07-12) and trimmed. If OpenAI changes this shape, the parser
# must degrade to "no quota slot", never raise — see the malformed cases below.
LIVE_RESULT = {
    "rateLimits": {
        "limitId": "codex",
        "planType": "plus",
        "primary": {"usedPercent": 33, "windowDurationMins": 300, "resetsAt": 1783824529},
        "secondary": {"usedPercent": 6, "windowDurationMins": 10080, "resetsAt": 1784374398},
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
    },
    "rateLimitResetCredits": {"availableCount": 4, "credits": []},
}


def test_parse_rate_limits_matches_the_rollout_reads_shape():
    levels = codex_usage.parse_rate_limits(LIVE_RESULT)
    quota = levels["quota"]

    # Same normalized keys the rollout collector emits — every downstream reader
    # (dashboard windows, pacing floors, the Mode line) stays seam-agnostic.
    assert quota["primary_remaining_percent"] == 67.0
    assert quota["secondary_remaining_percent"] == 94.0
    assert quota["primary_resets_at"] == 1783824529
    assert quota["summary"].startswith("5h 67% left")
    assert "7d 94% left" in quota["summary"]
    assert levels["plan_type"] == "plus"
    assert levels["source"] == "codex app-server"


def test_parse_rate_limits_carries_reset_credits():
    # Only this seam knows about the free "Full reset" grants; a quota panel that
    # reads 4% left while four unredeemed resets sit on the account is half a truth.
    assert codex_usage.parse_rate_limits(LIVE_RESULT)["quota"]["reset_credits_available"] == 4


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"rateLimits": None},
        {"rateLimits": {"primary": "nonsense"}},
        {"rateLimits": {"primary": {"usedPercent": None}}},
    ],
)
def test_parse_rate_limits_degrades_on_unknown_shapes(result):
    # Protocol drift must cost a level slot, never a heartbeat.
    assert "quota" not in codex_usage.parse_rate_limits(result)


def test_load_or_refresh_uses_cache_within_ttl(tmp_path, monkeypatch):
    calls = []

    def fake_probe(**kwargs):
        calls.append(1)
        return codex_usage.parse_rate_limits(LIVE_RESULT)

    monkeypatch.setattr(codex_usage, "probe_rate_limits", fake_probe)
    first = codex_usage.load_or_refresh_snapshot(tmp_path)
    second = codex_usage.load_or_refresh_snapshot(tmp_path)
    assert first == second
    assert len(calls) == 1  # second read never spawned a probe
    assert (tmp_path / codex_usage.SNAPSHOT_NAME).exists()

    codex_usage.load_or_refresh_snapshot(tmp_path, max_age_seconds=0)
    assert len(calls) == 2  # expired TTL does


def test_failed_probe_degrades_to_the_stale_cache(tmp_path, monkeypatch):
    """The #315 contract: rough-but-real when the probe can't run, never blank."""
    monkeypatch.setattr(
        codex_usage, "probe_rate_limits",
        lambda **kw: codex_usage.parse_rate_limits(LIVE_RESULT),
    )
    cached = codex_usage.load_or_refresh_snapshot(tmp_path)

    monkeypatch.setattr(codex_usage, "probe_rate_limits", lambda **kw: None)
    degraded = codex_usage.load_or_refresh_snapshot(tmp_path, max_age_seconds=0)

    assert degraded == cached
    # And it keeps its *own* capture time, so the dashboard ages it truthfully
    # rather than restamping it as fresh.
    assert degraded["updated_at"] == cached["updated_at"]


def test_cold_cache_and_failed_probe_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_usage, "probe_rate_limits", lambda **kw: None)
    assert codex_usage.load_or_refresh_snapshot(tmp_path) is None


def _levels(source, updated_at, pct, extra=None):
    levels = {
        "source": source,
        "updated_at": updated_at,
        "quota": {"summary": f"5h {pct}% left", "primary_remaining_percent": pct},
    }
    levels.update(extra or {})
    return levels


def test_merge_prefers_the_fresher_quota_and_keeps_its_timestamp():
    probe = _levels("codex app-server", "2026-07-12T10:00:00Z", 67.0)
    rollout = _levels(
        "codex session rollout", "2026-07-12T08:00:00Z", 20.0,
        {"context_window": {"summary": "40% context left (est)"}},
    )

    merged = codex_usage.merge_levels(probe, rollout)
    # Idle Codex: the rollout froze two hours ago, the probe is live.
    assert merged["quota"]["primary_remaining_percent"] == 67.0
    assert merged["updated_at"] == "2026-07-12T10:00:00Z"
    # …but context/tokens are per-thread, and only the rollout can see them.
    assert merged["context_window"]["summary"] == "40% context left (est)"

    # Live Codex: a turn just wrote a rollout event newer than the cached probe.
    fresh_rollout = _levels("codex session rollout", "2026-07-12T10:05:00Z", 55.0)
    merged = codex_usage.merge_levels(probe, fresh_rollout)
    assert merged["quota"]["primary_remaining_percent"] == 55.0
    assert merged["updated_at"] == "2026-07-12T10:05:00Z"


def test_merge_survives_either_side_missing():
    probe = _levels("codex app-server", "2026-07-12T10:00:00Z", 67.0)
    assert codex_usage.merge_levels(probe, None)["quota"]["primary_remaining_percent"] == 67.0
    assert codex_usage.merge_levels(None, probe)["quota"]["primary_remaining_percent"] == 67.0
    assert codex_usage.merge_levels(None, None) is None


def test_probe_never_raises_when_codex_is_missing(_no_codex_app_server_probe):
    # No `codex` on PATH is the ordinary case on a Claude-only box. The autouse
    # conftest fixture stubs the probe out for every other test; this one asks
    # for the real thing back (that is what the fixture yields) so the
    # never-raises contract is actually exercised, not asserted against a stub.
    real_probe = _no_codex_app_server_probe
    assert real_probe(codex_bin="brr-no-such-binary-xyz") is None
