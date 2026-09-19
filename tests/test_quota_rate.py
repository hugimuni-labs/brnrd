"""The token<->quota exchange rate, and the pool priced off it.

Every rate assertion here is computed by hand from the fixture rather than
recorded from a run of the code — a golden number captured from the
implementation proves only that it did not change.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from brr import quota_rate

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
NOW_EPOCH = NOW.timestamp()


def stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(*, hours_ago=1.0, shell="claude", weekly=None, five_hour=None,
        fresh=0, output=0, cache_read=0, cache_creation=0, run_id=None,
        duration_hours=0.5, core="opus"):
    """One ledger row. Token columns are ``None`` when every class is zero, so a
    caller can build a row that proves *no* token reading (the real shape of a
    run whose Shell returned no usage envelope)."""
    empty = not any((fresh, output, cache_read, cache_creation))
    return {
        "run_id": run_id or f"run-{hours_ago}-{shell}",
        "runner_shell": shell,
        "runner_core": core,
        "started_at": stamp(hours_ago + duration_hours),
        "ended_at": stamp(hours_ago),
        "weekly_pct_delta": weekly,
        "five_hour_pct_delta": five_hour,
        "tokens_input": None if empty else fresh,
        "tokens_output": None if empty else output,
        "tokens_cache_read": None if empty else cache_read,
        "tokens_cache_creation": None if empty else cache_creation,
    }


def spread(n: int, *, per_row_pct: float, fresh: int, shell="claude",
           first_hours_ago=200.0, step=20.0, field="weekly"):
    """*n* identical rows spaced *step* hours apart — enough samples, enough span."""
    return [
        row(hours_ago=first_hours_ago - i * step, shell=shell, fresh=fresh,
            run_id=f"run-{shell}-{i}", **{field: per_row_pct})
        for i in range(n)
    ]


class TestShellFamily:
    @pytest.mark.parametrize("name,expected", [
        ("claude", "claude"), ("claude-opus", "claude"), ("CLAUDE-Fable", "claude"),
        ("codex", "codex"), ("codex-gpt-5.6-sol", "codex"),
        ("", None), (None, None), ("gemini", None), ("gpt-5.6-sol", None),
    ])
    def test_families(self, name, expected):
        assert quota_rate.shell_family(name) == expected


class TestWeightedTokens:
    def test_applies_the_published_price_ratios(self):
        total = quota_rate.weighted_tokens_of(
            row(fresh=1000, output=100, cache_read=10_000, cache_creation=400)
        )
        # 1000*1 + 100*5 + 10000*0.1 + 400*1.25 = 1000 + 500 + 1000 + 500
        assert total == 3000.0

    def test_a_row_with_no_token_columns_is_none_not_zero(self):
        assert quota_rate.weighted_tokens_of(row()) is None

    def test_partial_columns_count_what_is_present(self):
        assert quota_rate.weighted_tokens_of(
            {"tokens_output": 10, "tokens_input": None,
             "tokens_cache_read": None, "tokens_cache_creation": None}
        ) == 50.0


class TestReadRows:
    def test_skips_unparseable_lines_and_non_objects(self, tmp_path):
        path = tmp_path / "run-ledger.jsonl"
        path.write_text(
            json.dumps({"run_id": "a"}) + "\n"
            + "{not json\n" + "[1,2]\n" + "\n"
            + json.dumps({"run_id": "b"}) + "\n",
            encoding="utf-8",
        )
        assert [r["run_id"] for r in quota_rate.read_rows(path)] == ["a", "b"]

    def test_a_missing_ledger_reads_empty_never_raises(self, tmp_path):
        assert quota_rate.read_rows(tmp_path / "nope.jsonl") == []
        assert quota_rate.read_rows(None) == []

    def test_a_tail_read_drops_the_partial_first_line(self, tmp_path):
        path = tmp_path / "run-ledger.jsonl"
        lines = [json.dumps({"run_id": f"r{i}", "pad": "x" * 200}) for i in range(50)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rows = quota_rate.read_rows(path, tail_bytes=1000)
        assert rows, "the tail should still yield whole rows"
        assert all("run_id" in r for r in rows)
        assert len(rows) < 50


class TestRate:
    def test_pooled_rate_is_sum_delta_over_sum_weighted(self):
        rows = spread(6, per_row_pct=1.0, fresh=2_000_000)
        reading = quota_rate.rate(None, "claude", 10080.0, now=NOW_EPOCH, rows=rows)
        assert reading["status"] == "measured"
        # 6 rows x 1% over 6 x 2M weighted tokens = 0.5 %/Mtok
        assert reading["pct_per_mtok"] == 0.5
        assert reading["samples"] == 6
        assert reading["weighted_tokens_observed"] == 12_000_000

    def test_pooled_is_not_the_mean_of_row_ratios(self):
        """The quantization defence: one tiny row that recorded a whole percent
        must not drag the estimate, which a mean of ratios would let it do."""
        rows = spread(5, per_row_pct=1.0, fresh=2_000_000)
        rows.append(row(hours_ago=5, fresh=10_000, weekly=1.0, run_id="tiny"))
        reading = quota_rate.rate(None, "claude", 10080.0, now=NOW_EPOCH, rows=rows)
        # pooled: 6% / 10.01M = 0.5995 %/Mtok. mean-of-ratios would be 17.1.
        assert reading["pct_per_mtok"] == pytest.approx(0.5995, abs=0.001)
        assert reading["row_rate_p90"] == pytest.approx(100.0, abs=0.001)

    def test_thin_evidence_is_unmeasured_and_says_how_thin(self):
        rows = spread(quota_rate.MIN_SAMPLES - 1, per_row_pct=1.0, fresh=2_000_000)
        reading = quota_rate.rate(None, "claude", 10080.0, now=NOW_EPOCH, rows=rows)
        assert reading["status"] == "unmeasured"
        assert reading["samples"] == quota_rate.MIN_SAMPLES - 1
        assert "joinable run" in reading["reason"]
        assert str(quota_rate.MIN_SAMPLES) in reading["reason"]
        assert "pct_per_mtok" not in reading

    def test_enough_rows_packed_into_too_short_a_span_is_unmeasured(self):
        rows = spread(8, per_row_pct=1.0, fresh=2_000_000, first_hours_ago=10, step=0.5)
        reading = quota_rate.rate(None, "claude", 10080.0, now=NOW_EPOCH, rows=rows)
        assert reading["status"] == "unmeasured"
        assert "apart" in reading["reason"]

    def test_rows_outside_the_horizon_are_not_evidence(self):
        rows = spread(8, per_row_pct=1.0, fresh=2_000_000, first_hours_ago=900, step=20)
        reading = quota_rate.rate(
            None, "claude", 10080.0, now=NOW_EPOCH, rows=rows,
            horizon_hours=quota_rate.DEFAULT_HORIZON_HOURS,
        )
        assert reading["status"] == "unmeasured"
        assert reading["samples"] == 0

    def test_a_zero_delta_row_is_not_a_sample(self):
        rows = spread(6, per_row_pct=1.0, fresh=2_000_000)
        rows += [row(hours_ago=3, fresh=5_000_000, weekly=0.0, run_id="quantized-away")]
        reading = quota_rate.rate(None, "claude", 10080.0, now=NOW_EPOCH, rows=rows)
        assert reading["samples"] == 6
        assert reading["pct_per_mtok"] == 0.5

    def test_a_row_with_no_usage_envelope_is_not_a_sample(self):
        rows = spread(6, per_row_pct=1.0, fresh=2_000_000)
        rows += [row(hours_ago=3, weekly=9.0, run_id="no-tokens")]
        assert quota_rate.rate(
            None, "claude", 10080.0, now=NOW_EPOCH, rows=rows
        )["samples"] == 6

    def test_the_other_shell_is_a_different_pool(self):
        rows = spread(6, per_row_pct=1.0, fresh=2_000_000, shell="claude")
        rows += spread(6, per_row_pct=10.0, fresh=100_000, shell="codex")
        claude = quota_rate.rate(None, "claude-opus", 10080.0, now=NOW_EPOCH, rows=rows)
        codex = quota_rate.rate(None, "codex-gpt-5.6-sol", 10080.0, now=NOW_EPOCH, rows=rows)
        assert claude["pct_per_mtok"] == 0.5
        assert codex["pct_per_mtok"] == 100.0

    def test_the_two_windows_are_priced_separately(self):
        rows = [
            row(hours_ago=200 - i * 20, fresh=2_000_000, weekly=1.0, five_hour=6.0,
                run_id=f"r{i}")
            for i in range(6)
        ]
        week = quota_rate.rate(None, "claude", 10080.0, now=NOW_EPOCH, rows=rows)
        five = quota_rate.rate(None, "claude", 300.0, now=NOW_EPOCH, rows=rows)
        assert week["pct_per_mtok"] == 0.5
        assert five["pct_per_mtok"] == 3.0

    def test_an_unknown_window_duration_is_refused_not_approximated(self):
        rows = spread(6, per_row_pct=1.0, fresh=2_000_000)
        reading = quota_rate.rate(None, "claude", 1440.0, now=NOW_EPOCH, rows=rows)
        assert reading["status"] == "unmeasured"
        assert "no ledger column measures" in reading["reason"]

    def test_an_unknown_shell_is_refused(self):
        reading = quota_rate.rate(None, "gemini", 10080.0, now=NOW_EPOCH, rows=[])
        assert reading["status"] == "unmeasured"
        assert "no quota window is keyed" in reading["reason"]

    def test_solitary_diagnostic_excludes_overlapped_runs(self):
        """Six well-spaced rows are all solitary; adding an overlapping sibling
        to each removes the diagnostic rather than reporting a biased one."""
        rows = spread(6, per_row_pct=1.0, fresh=2_000_000)
        alone = quota_rate.rate(None, "claude", 10080.0, now=NOW_EPOCH, rows=rows)
        assert alone[quota_rate.SOLITARY_KEY] == 0.5
        crowded = list(rows) + [
            row(hours_ago=200 - i * 20, fresh=1_000_000, weekly=1.0,
                run_id=f"sibling-{i}", duration_hours=1.0)
            for i in range(6)
        ]
        reading = quota_rate.rate(None, "claude", 10080.0, now=NOW_EPOCH, rows=crowded)
        assert reading[quota_rate.SOLITARY_KEY] is None
        assert reading["status"] == "measured"


class TestTokensForPercent:
    def test_converts_through_the_measured_rate(self):
        reading = quota_rate.rate(
            None, "claude", 10080.0, now=NOW_EPOCH,
            rows=spread(6, per_row_pct=1.0, fresh=2_000_000),
        )
        # 0.5 %/Mtok -> 50% buys 100M weighted tokens
        assert quota_rate.tokens_for_percent(reading, 50.0) == 100_000_000

    def test_an_unmeasured_rate_converts_to_nothing(self):
        thin = quota_rate.rate(None, "claude", 10080.0, now=NOW_EPOCH, rows=[])
        assert quota_rate.tokens_for_percent(thin, 50.0) is None
        assert quota_rate.tokens_for_percent(None, 50.0) is None


class TestCommitments:
    def test_undrawn_allowance_is_what_a_live_run_still_holds(self):
        result = quota_rate.commitments([
            {"shell": "claude", "allowance_tokens": 10_000_000,
             "allowance_spent": 4_000_000, "run_id": "a"},
        ], "claude")
        assert result["tokens"] == 6_000_000
        assert result["runs"] == 1
        assert result["detail"][0]["spend_read"] is True

    def test_an_unread_spend_commits_the_whole_ceiling(self):
        result = quota_rate.commitments([
            {"shell": "claude", "allowance_tokens": 10_000_000,
             "allowance_spent": None, "run_id": "a"},
        ], "claude")
        assert result["tokens"] == 10_000_000
        assert result["detail"][0]["spend_read"] is False

    def test_an_overrun_run_commits_nothing_further(self):
        result = quota_rate.commitments([
            {"shell": "claude", "allowance_tokens": 1_000_000,
             "allowance_spent": 9_000_000, "run_id": "a"},
        ], "claude")
        assert result["tokens"] == 0

    def test_a_run_with_no_declared_allowance_is_counted_but_not_summed(self):
        result = quota_rate.commitments([
            {"shell": "claude", "allowance_tokens": None, "run_id": "seat"},
            {"shell": "claude", "allowance_tokens": 3_000_000,
             "allowance_spent": 0, "run_id": "strand"},
        ], "claude")
        assert result["tokens"] == 3_000_000
        assert result["runs"] == 1
        assert result["unbounded_runs"] == 1

    def test_the_other_shells_commitments_are_not_this_pool(self):
        runs = [
            {"shell": "codex", "allowance_tokens": 8_000_000,
             "allowance_spent": 0, "run_id": "c"},
            {"shell": "claude-opus", "allowance_tokens": 2_000_000,
             "allowance_spent": 0, "run_id": "a"},
            {"shell": None, "allowance_tokens": 5_000_000,
             "allowance_spent": 0, "run_id": "unattested"},
        ]
        assert quota_rate.commitments(runs, "claude")["tokens"] == 2_000_000
        assert quota_rate.commitments(runs, "codex")["tokens"] == 8_000_000


class TestPool:
    @staticmethod
    def rows():
        return spread(6, per_row_pct=1.0, fresh=2_000_000)

    def test_prices_the_window_and_subtracts_work_in_flight(self):
        result = quota_rate.pool(
            None, "claude",
            window={"remaining_pct": 50.0, "window_minutes": 10080.0},
            live_runs=[{"shell": "claude", "allowance_tokens": 30_000_000,
                        "allowance_spent": 0, "run_id": "a"}],
            now=NOW_EPOCH, rows=self.rows(),
        )
        assert result["status"] == "measured"
        assert result["pool_tokens"] == 100_000_000
        assert result["committed_tokens"] == 30_000_000
        assert result["free_tokens"] == 70_000_000

    def test_free_tokens_goes_negative_when_the_fleet_has_outrun_the_window(self):
        result = quota_rate.pool(
            None, "claude",
            window={"remaining_pct": 10.0, "window_minutes": 10080.0},
            live_runs=[{"shell": "claude", "allowance_tokens": 30_000_000,
                        "allowance_spent": 0, "run_id": "a"}],
            now=NOW_EPOCH, rows=self.rows(),
        )
        assert result["pool_tokens"] == 20_000_000
        assert result["free_tokens"] == -10_000_000

    def test_a_window_without_a_duration_cannot_be_priced(self):
        result = quota_rate.pool(
            None, "claude", window={"remaining_pct": 50.0},
            now=NOW_EPOCH, rows=self.rows(),
        )
        assert result["status"] == "unmeasured"
        assert "binding quota window" in result["reason"]

    def test_no_window_at_all_still_reports_the_commitments_it_knows(self):
        result = quota_rate.pool(
            None, "claude", window=None,
            live_runs=[{"shell": "claude", "allowance_tokens": 4_000_000,
                        "allowance_spent": 1_000_000, "run_id": "a"}],
            now=NOW_EPOCH, rows=self.rows(),
        )
        assert result["status"] == "unmeasured"
        assert result["committed_tokens"] == 3_000_000

    def test_a_thin_rate_makes_the_pool_unmeasured_and_forwards_the_reason(self):
        result = quota_rate.pool(
            None, "claude",
            window={"remaining_pct": 50.0, "window_minutes": 10080.0},
            now=NOW_EPOCH, rows=[],
        )
        assert result["status"] == "unmeasured"
        assert "joinable run" in result["reason"]
        assert "pool_tokens" not in result


# --- the daemon seam: what the dispatch path and the boundary facet read ----

from brr import daemon  # noqa: E402  (module-level import after the pure tests)


@pytest.fixture
def clean_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


class TestLiveRunCommitments:
    def test_a_registered_spawn_carries_its_proposed_shell(self, clean_controls):
        daemon._register_run_control(
            "evt-1", "run-parent", allowance_tokens=5_000_000, shell="claude-opus",
        )
        rows = daemon._live_run_commitments("claude")
        assert [r["allowance_tokens"] for r in rows] == [5_000_000]
        assert rows[0]["shell"] == "claude-opus"

    def test_a_spawn_with_no_named_shell_belongs_to_no_pool_yet(self, clean_controls):
        daemon._register_run_control("evt-1", "run-parent", allowance_tokens=5_000_000)
        assert daemon._live_run_commitments("claude") == []
        assert daemon._live_run_commitments("codex") == []
        assert len(daemon._live_run_commitments()) == 1

    def test_every_parent_s_children_count_not_just_one_s(self, clean_controls):
        daemon._register_run_control(
            "evt-mine", "run-a", allowance_tokens=1_000_000, shell="claude")
        daemon._register_run_control(
            "evt-theirs", "run-b", allowance_tokens=2_000_000, shell="claude")
        assert quota_rate.commitments(
            daemon._live_run_commitments("claude"), "claude"
        )["tokens"] == 3_000_000

    def test_a_stopped_strand_releases_its_commitment(self, clean_controls):
        daemon._register_run_control(
            "evt-1", "run-parent", allowance_tokens=5_000_000, shell="claude")
        with daemon._run_controls_lock:
            daemon._run_controls["evt-1"]["stopped"] = True
        assert daemon._live_run_commitments("claude") == []


class TestSpawnAllowanceWarning:
    @staticmethod
    def measured_pool(free_tokens):
        return {
            "status": "measured", "shell": "claude", "remaining_percent": 40.0,
            "window_minutes": 10080.0, "pool_tokens": 80_000_000,
            "committed_tokens": 80_000_000 - free_tokens, "committed_runs": 2,
            "free_tokens": free_tokens,
            "rate": {"status": "measured", "pct_per_mtok": 0.5,
                     "samples": 17, "span_hours": 302.9},
        }

    def test_an_ask_inside_the_free_pool_says_nothing(self):
        assert daemon._spawn_allowance_warning(
            self.measured_pool(30_000_000), 20_000_000, "claude") is None

    def test_an_ask_past_the_free_pool_names_every_operand(self):
        text = daemon._spawn_allowance_warning(
            self.measured_pool(5_000_000), 20_000_000, "claude-opus")
        assert text is not None
        assert "allowance 20m" in text
        assert "5m" in text           # the free pool
        assert "80m" in text          # the whole priced window
        assert "0.5 %/Mtok" in text
        assert "n=17 runs" in text
        assert "2 live run(s)" in text
        assert "dispatched anyway" in text
        assert "week 40.0% left" in text

    def test_an_unmeasured_pool_warns_about_nothing(self):
        unmeasured = {"status": "unmeasured", "reason": "2 joinable run(s)"}
        assert daemon._spawn_allowance_warning(unmeasured, 20_000_000, "claude") is None
        assert daemon._spawn_allowance_warning(None, 20_000_000, "claude") is None

    def test_a_spawn_with_no_allowance_figure_warns_about_nothing(self):
        assert daemon._spawn_allowance_warning(
            self.measured_pool(1), None, "claude") is None

    def test_a_negative_free_pool_still_renders(self):
        text = daemon._spawn_allowance_warning(
            self.measured_pool(-10_000_000), 20_000_000, "claude")
        assert "-10m" in text


class TestPricedSpawnPool:
    def test_no_brr_dir_reads_none_not_an_empty_pool(self):
        assert daemon._priced_spawn_pool(None, "claude", {}) is None

    def test_a_fully_identified_window_prices_off_the_repo_ledger(
        self, tmp_path, clean_controls,
    ):
        ledger = tmp_path / "run-ledger.jsonl"
        ledger.write_text(
            "\n".join(json.dumps(r) for r in spread(
                6, per_row_pct=1.0, fresh=2_000_000)) + "\n",
            encoding="utf-8",
        )
        daemon._register_run_control(
            "evt-1", "run-parent", allowance_tokens=4_000_000, shell="claude")
        # The shape `runner_quota.binding_quota_window` actually reads for
        # Claude: used-percent and reset instant on the top level, duration
        # implied by the bucket name. Only the week bucket is present, so the
        # week is what binds — on a live seat the session bucket usually is,
        # and it is priced ~6x dearer per token.
        levels = {"quota": {}, "week_used_percentage": 50.0,
                  "week_resets_at": NOW_EPOCH + 3600.0}
        result = daemon._priced_spawn_pool(
            tmp_path, "claude", levels, now=NOW_EPOCH,
        )
        assert result["status"] == "measured", result.get("reason")
        assert result["window_minutes"] == 10080.0
        assert result["pool_tokens"] == 100_000_000
        assert result["committed_tokens"] == 4_000_000
        assert result["free_tokens"] == 96_000_000

    def test_a_window_with_no_clock_reads_unmeasured(self, tmp_path, clean_controls):
        (tmp_path / "run-ledger.jsonl").write_text("", encoding="utf-8")
        result = daemon._priced_spawn_pool(tmp_path, "claude", {"quota": {}})
        assert result["status"] == "unmeasured"
