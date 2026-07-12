"""Tests for the seam bench (brnrd bench) — the lesser-light probe loop.

Only the deterministic core is CI-testable: sandbox prep, probe
evaluation over synthetic transcripts, report rendering, CLI wiring.
Actually spawning a runner spends real quota and needs CLI auth — that
path is exercised by the resident, not by pytest.
"""

import subprocess

import pytest

from brr import bench
from brr.cli import main


# ── Scenario registry ────────────────────────────────────────────────


def test_scenarios_are_well_formed():
    assert "simple-ask" in bench.SCENARIOS
    assert "followup-fold" in bench.SCENARIOS
    for name, scenario in bench.SCENARIOS.items():
        assert scenario.name == name
        assert scenario.lead.strip()
        assert scenario.timeout_seconds > 0
        for probe in scenario.probes:
            assert probe in bench.PROBES, f"unknown probe {probe} in {name}"


def test_followup_fold_scenario_probes_the_fold_seam():
    scenario = bench.SCENARIOS["followup-fold"]
    assert scenario.followups
    assert "fold" in scenario.probes
    assert "single_run" in scenario.probes


# ── Sandbox prep ─────────────────────────────────────────────────────


def test_prepare_sandbox_writes_repo_home_and_config(tmp_path):
    sandbox = bench.prepare_sandbox(tmp_path, shell="claude-haiku")
    assert (sandbox.repo / ".git").is_dir()
    assert (sandbox.repo / "AGENTS.md").exists()
    assert (sandbox.repo / "kb" / "index.md").exists()
    assert sandbox.home.is_dir()
    config = (sandbox.repo / ".brr" / "config").read_text(encoding="utf-8")
    assert "shell=claude-haiku" in config
    assert "runner.timeout_seconds=480" in config
    # scaffold is committed so runner writes show up as a clean diff
    out = subprocess.run(
        ["git", "log", "--oneline"], cwd=sandbox.repo,
        capture_output=True, text=True, check=True,
    )
    assert "sandbox scaffold" in out.stdout


def test_prepare_sandbox_scenario_config_overrides(tmp_path):
    sandbox = bench.prepare_sandbox(
        tmp_path, shell="codex-mini", config={"runner.timeout_seconds": 120}
    )
    config = (sandbox.repo / ".brr" / "config").read_text(encoding="utf-8")
    assert "shell=codex-mini" in config
    assert "runner.timeout_seconds=120" in config


# ── Probes ───────────────────────────────────────────────────────────


def _transcript(**kw) -> bench.Transcript:
    t = bench.Transcript(scenario="t", shell="claude-haiku")
    t.lead_event_id = kw.pop("lead", "evt-lead")
    for key, value in kw.items():
        setattr(t, key, value)
    return t


SCEN = bench.SCENARIOS["simple-ask"]


@pytest.mark.parametrize(
    "tail",
    [
        "…work done.\n\ndone — committed abc1234 on brr/x",
        "shipped.\n\ncontinuing — next: wire the parser",
        "**blocked** — needs the API token",
        "Two ways forward:\n1. keep the shim\n2. cut it now\nI recommend 2.",
    ],
)
def test_probe_next_move_accepts_contract_shapes(tail):
    t = _transcript(responses={"evt-lead": f"some reply body\n{tail}"})
    assert bench.probe_next_move(t, SCEN).passed


def test_probe_next_move_rejects_shapeless_closeout():
    t = _transcript(responses={"evt-lead": "I did some things and it went fine."})
    assert not bench.probe_next_move(t, SCEN).passed


def test_probe_response_fails_on_timeout_or_empty():
    assert not bench.probe_response(_transcript(), SCEN).passed
    t = _transcript(responses={"evt-lead": "hi"}, timed_out=True)
    assert not bench.probe_response(t, SCEN).passed
    assert bench.probe_response(_transcript(responses={"evt-lead": "hi"}), SCEN).passed


def test_probe_card_reads_card_composed_text():
    with_note = _transcript(
        records=[{"kind": "update", "type": "card_composed", "text": "orienting"}]
    )
    empty_note = _transcript(
        records=[{"kind": "update", "type": "card_composed", "text": "  "}]
    )
    assert bench.probe_card(with_note, SCEN).passed
    assert not bench.probe_card(empty_note, SCEN).passed


def test_probe_fold_requires_routed_replies_for_every_followup():
    t = _transcript(
        followup_event_ids=["evt-f1"],
        responses={"evt-lead": "done", "evt-f1": "folded — noted"},
    )
    assert bench.probe_fold(t, SCEN).passed
    t2 = _transcript(followup_event_ids=["evt-f1"], responses={"evt-lead": "done"})
    assert not bench.probe_fold(t2, SCEN).passed


def test_probe_fold_counts_partials_and_interim_records():
    # A folded reply lands as write_partial when no gate drains it…
    t = _transcript(
        followup_event_ids=["evt-f1"], partials={"evt-f1": ["folded — noted"]}
    )
    assert bench.probe_fold(t, SCEN).passed
    # …or shows as an interim_response record targeting the event.
    t2 = _transcript(
        followup_event_ids=["evt-f1"],
        records=[{"kind": "interim_response", "target_event": "evt-f1"}],
    )
    assert bench.probe_fold(t2, SCEN).passed


def test_probe_single_run_flags_respawned_followup():
    assert bench.probe_single_run(_transcript(run_dirs=["run-a"]), SCEN).passed
    assert not bench.probe_single_run(
        _transcript(run_dirs=["run-a", "run-b"]), SCEN
    ).passed


def test_probe_interim_accepts_both_record_shapes():
    artifact = _transcript(
        records=[{"kind": "artifact", "artifact_kind": "interim_response", "body": "hi"}]
    )
    lifecycle = _transcript(
        records=[{"kind": "update", "type": "interim_response", "event_id": "evt-lead"}]
    )
    assert bench.probe_interim(artifact, SCEN).passed
    assert bench.probe_interim(lifecycle, SCEN).passed
    assert not bench.probe_interim(_transcript(), SCEN).passed


def test_first_signal_detection():
    assert bench._is_signal_record(
        {"kind": "update", "type": "card_composed", "text": "working"}
    )
    assert bench._is_signal_record({"kind": "interim_response", "body": "hi"})
    assert not bench._is_signal_record(
        {"kind": "update", "type": "spawned", "run_id": "x"}
    )
    assert not bench._is_signal_record({"kind": "event", "body": "user says"})


# ── Report rendering ─────────────────────────────────────────────────


def test_render_report_carries_probe_table_and_final_reply():
    t = _transcript(
        responses={"evt-lead": "all good\n\ndone — receipt"},
        run_dirs=["run-a"],
        started_at=100.0,
        first_signal_at=112.0,
        finished_at=190.0,
    )
    results = bench.evaluate(t, SCEN)
    report = bench.render_report(t, SCEN, results)
    assert "| response | ✓ |" in report
    assert "| next_move | ✓ |" in report
    assert "first signal: 12s" in report
    assert "done — receipt" in report


def test_render_transcript_weaves_records():
    t = _transcript(
        records=[
            {"ts": "2026-07-03T10:00:00Z", "kind": "event", "body": "user asks"},
            {"ts": "2026-07-03T10:00:30Z", "kind": "update",
             "type": "card_composed", "text": "orienting"},
        ]
    )
    woven = bench.render_transcript(t)
    assert "event" in woven and "user asks" in woven
    assert "update/card_composed" in woven and "orienting" in woven


# ── Follow-up trigger grammar ────────────────────────────────────────


def test_followup_delay_grammar():
    assert bench._followup_delay(bench.FollowUp(body="x", after="first-signal")) is None
    assert bench._followup_delay(bench.FollowUp(body="x", after="+45")) == 45.0
    assert bench._followup_delay(bench.FollowUp(body="x", after="+bad")) == 30.0


# ── CLI wiring ───────────────────────────────────────────────────────


def test_cli_bench_scenarios_lists_registry(capsys):
    assert main(["bench", "scenarios"]) == 0
    out = capsys.readouterr().out
    assert "simple-ask" in out
    assert "followup-fold" in out


def test_cli_bench_run_rejects_unknown_scenario(capsys):
    assert main(["bench", "run", "--scenario", "nope"]) == 2
    assert "unknown scenario" in capsys.readouterr().out
