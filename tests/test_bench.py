"""Tests for the seam bench (brnrd bench) — the lesser-light probe loop.

Only the deterministic core is CI-testable: sandbox prep, probe
evaluation over synthetic transcripts, report rendering, CLI wiring.
Actually spawning a runner spends real quota and needs CLI auth — that
path is exercised by the resident, not by pytest.
"""

import os
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


# ── Drift scenario: the long-run arm ─────────────────────────────────


def test_drift_scenario_puts_its_obligations_late():
    """The design claim, pinned. A drift probe whose follow-up fires at
    first-signal is just `followup-fold` with extra steps — it samples the
    one moment nothing has drifted yet, which is the exact flaw that made
    the turn-1 floor probe report 6/6 and mean nothing."""
    drift = bench.SCENARIOS["drift"]
    assert drift.followups, "drift needs a fold-in to probe"
    for fu in drift.followups:
        assert fu.after.startswith("+"), "drift's follow-up must fire on a delay"
        assert bench._followup_delay(fu) >= 120, "too early to have drifted"
    for probe in ("mount", "classification", "commit", "card", "fold", "next_move"):
        assert probe in drift.probes


def test_drift_substrate_is_actually_broken(tmp_path):
    """The anti-costume test. If someone ever tidies these bugs away, the
    scenario keeps running, keeps passing, and measures nothing — a red
    suite is the load the whole probe rests on, so it gets asserted, not
    assumed."""
    drift = bench.SCENARIOS["drift"]
    sandbox = bench.prepare_sandbox(
        tmp_path, shell="claude-haiku", scaffold=drift.scaffold,
    )
    proc = subprocess.run(
        ["python", "-m", "pytest", "-q", "tests/test_taskq.py"],
        cwd=sandbox.repo, capture_output=True, text=True,
    )
    assert proc.returncode != 0, "drift substrate must start red"
    assert "3 failed" in proc.stdout, proc.stdout[-500:]


def test_prepare_sandbox_writes_scenario_scaffold(tmp_path):
    sandbox = bench.prepare_sandbox(
        tmp_path, shell="claude-haiku",
        scaffold={"pkg/mod.py": "x = 1\n", "tests/test_mod.py": "def test_x(): pass\n"},
    )
    assert (sandbox.repo / "pkg" / "mod.py").read_text() == "x = 1\n"
    assert (sandbox.repo / "tests" / "test_mod.py").exists()
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=sandbox.repo, capture_output=True, text=True,
    ).stdout
    assert "pkg/mod.py" in tracked, "scaffold must land in the scaffold commit"


# ── Arm attestation ──────────────────────────────────────────────────


def _t(**kw):
    t = bench.Transcript(scenario="drift", shell="claude-haiku")
    for key, value in kw.items():
        setattr(t, key, value)
    return t


_PROSE_WAKE = "\n".join(bench._PROSE_CONTRACT_MARKERS)


def test_probe_mount_attests_each_arm_from_the_wake():
    drift = bench.SCENARIOS["drift"]
    mounted = _t(config={"boot.mount": "true"}, prompt_texts=["kernel only"])
    assert bench.probe_mount(mounted, drift).passed

    prose = _t(config={"boot.mount": "false"}, prompt_texts=[_PROSE_WAKE])
    assert bench.probe_mount(prose, drift).passed


def test_probe_mount_voids_an_arm_whose_config_lied():
    """The failure that would eat the whole experiment: a config asking for
    a mounted boot, a wake that got the prose one anyway, and two arms
    reported as different when they were identical."""
    drift = bench.SCENARIOS["drift"]
    lying = _t(config={"boot.mount": "true"}, prompt_texts=[_PROSE_WAKE])
    result = bench.probe_mount(lying, drift)
    assert not result.passed
    assert "ARM VOID" in result.detail


def test_probe_mount_refuses_to_guess_without_a_wake():
    drift = bench.SCENARIOS["drift"]
    result = bench.probe_mount(_t(config={"boot.mount": "true"}), drift)
    assert not result.passed
    assert "unverifiable" in result.detail


# ── Late obligations ─────────────────────────────────────────────────


def test_probe_classification_reads_the_ledger_not_the_reply():
    drift = bench.SCENARIOS["drift"]
    null_row = _t(ledger_rows=[{"task_classification": None}])
    assert not bench.probe_classification(null_row, drift).passed

    written = _t(ledger_rows=[{"task_classification": "bugfix"}])
    result = bench.probe_classification(written, drift)
    assert result.passed and "bugfix" in result.detail

    assert not bench.probe_classification(_t(), drift).passed


def test_probe_commit_ignores_the_scaffold_commit():
    drift = bench.SCENARIOS["drift"]
    scaffold_only = _t(commit_subjects=["bench: sandbox scaffold"])
    assert not bench.probe_commit(scaffold_only, drift).passed

    did_work = _t(commit_subjects=["fix: taskq heap order", "bench: sandbox scaffold"])
    assert bench.probe_commit(did_work, drift).passed


# ── CLI arms ─────────────────────────────────────────────────────────


def test_cli_config_flag_carries_the_arm(monkeypatch):
    seen = {}

    def fake_run(scenario, *, shell, root):
        seen["config"] = dict(scenario.config)
        return _t(), []

    monkeypatch.setattr(bench, "run_scenario", fake_run)
    main([
        "bench", "run", "--scenario", "drift",
        "--config", "boot.mount=true", "--config", "runner.timeout_seconds=900",
    ])
    assert seen["config"]["boot.mount"] == "true"
    assert seen["config"]["runner.timeout_seconds"] == "900"


def test_cli_config_flag_rejects_a_bare_key(capsys):
    assert main(["bench", "run", "--scenario", "drift", "--config", "nope"]) == 2
    assert "expected KEY=VALUE" in capsys.readouterr().out


def test_harvest_sees_a_commit_made_on_a_run_branch(tmp_path):
    """The probe's own costume, caught live and pinned here.

    A run works in a worktree on `brr/run-…`; the sandbox's default checkout
    never moves. `git log` (the checked-out branch) therefore reports NOTHING
    for a run that branched and committed exactly as the contract asks — and
    the first drift arm was read that way: a reply truthfully reporting
    `committed 3b61492` was scored a hallucinated receipt, because the probe
    was looking at the wrong ref. The fix is `--all`; this is the test that
    fails without it.
    """
    sandbox = bench.prepare_sandbox(tmp_path, shell="claude-haiku")
    env = {
        "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@brr",
        "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@brr",
    }
    run = subprocess.run
    run(["git", "switch", "-q", "-c", "brr/run-x"], cwd=sandbox.repo, check=True)
    (sandbox.repo / "notes.md").write_text("fixed\n", encoding="utf-8")
    run(["git", "commit", "-qam", "fix: the actual work"],
        cwd=sandbox.repo, check=True, env={**dict(os.environ), **env})
    run(["git", "switch", "-q", "main"], cwd=sandbox.repo, check=True)

    t = bench.harvest(sandbox, bench.Transcript(scenario="drift", shell="claude-haiku"))
    assert "fix: the actual work" in t.commit_subjects
    assert bench.probe_commit(t, bench.SCENARIOS["drift"]).passed


def test_probe_branch_catches_work_committed_onto_main(tmp_path):
    """The obligation the first probe set could not see.

    `probe_commit` asks *whether* a commit exists. Both drift arms committed,
    so it scored ✓✓ and reported a null — while one arm had `cd`'d out of its
    worktree and put the work straight onto the default branch. Whether and
    where are different obligations, and only one of them has a blast radius.
    """
    sandbox = bench.prepare_sandbox(tmp_path, shell="claude-haiku")
    env = {**dict(os.environ),
           "GIT_AUTHOR_NAME": "b", "GIT_AUTHOR_EMAIL": "b@b",
           "GIT_COMMITTER_NAME": "b", "GIT_COMMITTER_EMAIL": "b@b"}

    # A run that branched: main still points at the scaffold.
    subprocess.run(["git", "switch", "-q", "-c", "brr/run-y"], cwd=sandbox.repo, check=True)
    (sandbox.repo / "notes.md").write_text("on a branch\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "fix: on a run branch"],
                   cwd=sandbox.repo, check=True, env=env)
    subprocess.run(["git", "switch", "-q", "main"], cwd=sandbox.repo, check=True)
    t = bench.harvest(sandbox, bench.Transcript(scenario="drift", shell="claude-haiku"))
    assert bench.probe_branch(t, bench.SCENARIOS["drift"]).passed
    assert bench.probe_commit(t, bench.SCENARIOS["drift"]).passed  # both hold

    # A run that committed onto main: `commit` still passes, `branch` does not.
    (sandbox.repo / "notes.md").write_text("straight to main\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "fix: straight onto main"],
                   cwd=sandbox.repo, check=True, env=env)
    t2 = bench.harvest(sandbox, bench.Transcript(scenario="drift", shell="claude-haiku"))
    assert bench.probe_commit(t2, bench.SCENARIOS["drift"]).passed, "commit is blind to this"
    result = bench.probe_branch(t2, bench.SCENARIOS["drift"])
    assert not result.passed
    assert "MOVED" in result.detail
