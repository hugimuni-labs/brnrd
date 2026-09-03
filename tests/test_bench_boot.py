"""Tests for `brnrd bench boot` (w-56) — score the reaction, not the prose.

Three layers, matching the module they exercise:

- the scenario parser (`load_boot_scenario`) against real and malformed
  YAML — no daemon, no filesystem beyond the scenario file itself;
- the scorer (`score_boot`, and the artefact-loading helpers it reads
  through) against constructed `BootArtifacts` and, separately, against
  on-disk fixture run nodes — pure functions and plain file I/O, never a
  daemon or a subprocess;
- the CLI (`brnrd bench boot`) wired to a fake `dispatch_boot_run`, so the
  matrix/summary/output-file plumbing is exercised without dispatching a
  real run (that path spends real runner quota and needs CLI auth — see
  `brr.bench`'s own module docstring for the same posture on `bench run`).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from brr import bench, emotes
from brr.cli import main

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "bench" / "scenarios"


# ── Scenario parser ──────────────────────────────────────────────────


def test_load_boot_scenario_reads_the_shipped_scenarios():
    for name in ("make-brnrd-visible", "smoke"):
        scenario = bench.load_boot_scenario(SCENARIOS_DIR / f"{name}.yaml")
        assert scenario.name == name
        assert scenario.ask.strip()
        assert scenario.done_when is not None
        for steer in scenario.steers:
            assert steer.after_boundary >= 1
            assert steer.text.strip()
        if scenario.follow_up is not None:
            assert scenario.follow_up.after_boundary >= 1
            assert scenario.follow_up.text.strip()


def test_load_boot_scenario_smoke_is_small(tmp_path):
    """The smoke scenario's own contract: small boundary numbers, a
    `file_contains` done_when — the shape a 5-minute haiku run can meet."""
    scenario = bench.load_boot_scenario(SCENARIOS_DIR / "smoke.yaml")
    assert scenario.done_when.kind == "file_contains"
    all_boundaries = [s.after_boundary for s in scenario.steers]
    if scenario.follow_up:
        all_boundaries.append(scenario.follow_up.after_boundary)
    assert max(all_boundaries) <= 5


@pytest.mark.parametrize(
    "body,expected",
    [
        ("steers: []\n", "no non-empty 'ask'"),
        ("ask: do it\nsteers:\n  - text: only text\n", "after_boundary"),
        ("ask: do it\nfollow_up:\n  after_boundary: 2\n", "'text'"),
        ("ask: do it\n", "done_when needs one of"),
        ("ask: do it\ndone_when:\n  file_contains:\n    path: x\n", "'needle'"),
    ],
)
def test_load_boot_scenario_refuses_malformed_files(tmp_path, body, expected):
    path = tmp_path / "bad.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        bench.load_boot_scenario(path)


# ── Pure scorer ──────────────────────────────────────────────────────


def _handle() -> str:
    return next(iter(emotes.EMOTES))


def _scenario() -> bench.BootScenario:
    return bench.BootScenario(
        name="fixture",
        ask="do the thing",
        steers=(bench.BootDirective(after_boundary=2, text="steer one"),),
        follow_up=bench.BootDirective(after_boundary=4, text="narrow the scope"),
        done_when=bench.BootDoneWhen(kind="file_contains", path="notes.md", needle="done"),
    )


def _boundaries(n: int) -> list[dict]:
    return [
        {"phase": "post-tool", "at": f"2026-01-01T00:00:{i:02d}Z", "act": "mutate"}
        for i in range(1, n + 1)
    ]


_PLAN_A = "## Now\nworking\n\n## Plan\n- [ ] a\n"
_PLAN_B = "## Now\nworking\n\n## Plan\n- [x] a\n- [ ] b\n"
_PLAN_C = "## Now\ndone\n\n## Plan\n- [x] a\n- [x] b\n"


def _full_marks_artifacts() -> bench.BootArtifacts:
    return bench.BootArtifacts(
        run_id="run-fixture-1",
        final_response="did the thing, see notes.md",
        timed_out=False,
        mood_line=_handle(),
        boundaries=_boundaries(5),
        card_timeline=[
            ("2026-01-01T00:00:00Z", _PLAN_A),
            ("2026-01-01T00:00:03Z", _PLAN_B),
            ("2026-01-01T00:00:05Z", _PLAN_C),
        ],
        commits=[],
        bolt="accepted",
        done_when_result=(True, "notes.md contains 'done'"),
    )


def test_score_boot_full_marks_has_no_divergence():
    result = bench.score_boot(_full_marks_artifacts(), _scenario())
    assert result.first_divergence is None
    assert all(r.passed for r in result.rows)
    names = [r.name for r in result.rows]
    assert names == ["reply", "face", "steer_1", "plan_fold", "ask_complete", "bolt"]


def test_score_boot_missing_fold_diverges_at_plan_fold():
    artifacts = _full_marks_artifacts()
    # Nothing changes on the card between boundary 4's cutoff (t4) and the
    # end of the run: t5's text equals t3's — the fold never happened.
    artifacts.card_timeline = [
        ("2026-01-01T00:00:00Z", _PLAN_A),
        ("2026-01-01T00:00:03Z", _PLAN_B),
        ("2026-01-01T00:00:05Z", _PLAN_B),
    ]
    result = bench.score_boot(artifacts, _scenario())
    assert result.first_divergence == "plan_fold"
    by_name = {r.name: r.passed for r in result.rows}
    assert by_name["steer_1"] is True  # the earlier steer still folded
    assert by_name["plan_fold"] is False


def test_score_boot_missing_bolt_diverges_at_bolt():
    artifacts = _full_marks_artifacts()
    artifacts.bolt = None
    result = bench.score_boot(artifacts, _scenario())
    assert result.first_divergence == "bolt"
    assert all(r.passed for r in result.rows if r.name != "bolt")


def test_score_boot_no_reply_diverges_first():
    artifacts = _full_marks_artifacts()
    artifacts.final_response = ""
    result = bench.score_boot(artifacts, _scenario())
    assert result.first_divergence == "reply"


def test_score_boot_timed_out_fails_reply_even_with_text():
    artifacts = _full_marks_artifacts()
    artifacts.timed_out = True
    result = bench.score_boot(artifacts, _scenario())
    assert result.first_divergence == "reply"
    assert "timed out" in dict((r.name, r.detail) for r in result.rows)["reply"]


def test_score_boot_unresolved_face_fails():
    artifacts = _full_marks_artifacts()
    artifacts.mood_line = "not a real emote handle at all"
    result = bench.score_boot(artifacts, _scenario())
    assert result.first_divergence == "face"


def test_score_boot_steer_never_reached_fails_with_reason():
    artifacts = _full_marks_artifacts()
    artifacts.boundaries = _boundaries(1)  # only one post-tool boundary ever fired
    result = bench.score_boot(artifacts, _scenario())
    steer_row = next(r for r in result.rows if r.name == "steer_1")
    assert not steer_row.passed
    assert "never reached" in steer_row.detail


def test_score_boot_commit_after_boundary_counts_as_delta():
    artifacts = _full_marks_artifacts()
    artifacts.card_timeline = [("2026-01-01T00:00:00Z", _PLAN_A)] * 1
    # Deliberately a git-shaped timestamp (offset, not `Z`) landing in the
    # same wall-clock second as boundary 4's cutoff but a moment later —
    # exercises `_ts_gt`'s timezone-aware compare, not a lexical one (a
    # naive string compare gets exactly this case backwards).
    artifacts.commits = [("2026-01-01T00:00:04.500000+00:00", "feat: narrowed to the readme")]
    result = bench.score_boot(artifacts, _scenario())
    fold_row = next(r for r in result.rows if r.name == "plan_fold")
    assert fold_row.passed
    assert "commit" in fold_row.detail


def test_score_boot_scenario_with_no_directives_has_no_steer_rows():
    scenario = bench.BootScenario(
        name="bare", ask="just do it",
        done_when=bench.BootDoneWhen(kind="file_contains", path="x", needle="y"),
    )
    artifacts = _full_marks_artifacts()
    artifacts.done_when_result = (True, "x contains y")
    result = bench.score_boot(artifacts, scenario)
    names = [r.name for r in result.rows]
    assert names == ["reply", "face", "ask_complete", "bolt"]


def test_ts_gt_is_timezone_aware_not_lexical():
    """A `git log --date=iso-strict` timestamp carries a UTC offset (not
    `Z`), so a wall-clock string that reads *earlier* than a boundary's
    `Z` timestamp can still be chronologically *later* once the offset is
    applied. A naive string compare gets exactly this case backwards;
    `_ts_gt` must not.
    """
    cutoff = "2026-01-01T10:00:00Z"
    # 09:30 at UTC-1 is 10:30Z — chronologically after the 10:00Z cutoff,
    # despite the wall-clock digits ("09:30...") reading lexically smaller.
    truly_later = "2026-01-01T09:30:00-01:00"
    assert truly_later < cutoff  # the lexical order is backwards, by construction
    assert bench._ts_gt(truly_later, cutoff)  # the real, timezone-aware order is not


# ── evaluate_done_when ───────────────────────────────────────────────


def test_evaluate_done_when_file_contains(tmp_path):
    (tmp_path / "notes.md").write_text("we shipped the README\n", encoding="utf-8")
    dw = bench.BootDoneWhen(kind="file_contains", path="notes.md", needle="README")
    ok, detail = bench.evaluate_done_when(dw, repo=tmp_path)
    assert ok
    assert "README" in detail

    dw_miss = bench.BootDoneWhen(kind="file_contains", path="notes.md", needle="nope")
    ok, _detail = bench.evaluate_done_when(dw_miss, repo=tmp_path)
    assert not ok

    dw_absent = bench.BootDoneWhen(kind="file_contains", path="missing.md", needle="x")
    ok, detail = bench.evaluate_done_when(dw_absent, repo=tmp_path)
    assert not ok
    assert "does not exist" in detail


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_evaluate_done_when_commit_touches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "bench@brr")
    _git(repo, "config", "user.name", "bench")
    (repo / "README.md").write_text("# hi\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "touch readme")

    ok, detail = bench.evaluate_done_when(
        bench.BootDoneWhen(kind="commit_touches", path="README.md"), repo=repo,
    )
    assert ok
    assert "README.md" in detail

    ok, _detail = bench.evaluate_done_when(
        bench.BootDoneWhen(kind="commit_touches", path="nope.md"), repo=repo,
    )
    assert not ok


def test_evaluate_done_when_none_is_vacuous(tmp_path):
    ok, detail = bench.evaluate_done_when(None, repo=tmp_path)
    assert ok
    assert "vacuously" in detail


# ── Fixture run node (real files, no daemon) ─────────────────────────


def _write_run_node(base: Path, run_id: str, *, boundaries: list[dict], mood: str) -> Path:
    run_dir = base / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / bench.hooks.BOUNDARIES_NAME).write_text(
        "\n".join(json.dumps(b) for b in boundaries) + "\n", encoding="utf-8",
    )
    (run_dir / "mood").write_text(mood + "\n", encoding="utf-8")
    return run_dir


def test_read_boot_artifacts_pieces_against_a_fixture_run_node(tmp_path):
    brr_dir = tmp_path / ".brr"
    run_dir = _write_run_node(
        brr_dir, "run-fixture-2", boundaries=_boundaries(3), mood=_handle(),
    )
    conv_dir = brr_dir / "conversations"
    conv_dir.mkdir(parents=True)
    records = [
        {"kind": "update", "type": "card_composed", "ts": "2026-01-01T00:00:00Z", "text": _PLAN_A},
        {"kind": "update", "type": "card_composed", "ts": "2026-01-01T00:00:03Z", "text": _PLAN_C},
    ]
    (conv_dir / "log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8",
    )
    ledger = brr_dir / bench.run_ledger.LEDGER_NAME
    ledger.write_text(
        json.dumps({"run_id": "run-fixture-2", "bolt": "accepted"}) + "\n", encoding="utf-8",
    )

    assert bench._read_boundaries_raw(run_dir) == _boundaries(3)
    assert bench._post_tool_boundary_count(run_dir) == 3
    assert bench._read_mood_line(run_dir) == _handle()
    assert bench._bolt_for_run(brr_dir, "run-fixture-2") == "accepted"

    conv_records = bench._read_conversation_records(brr_dir)
    timeline = bench._card_timeline(conv_records)
    assert [text for _ts, text in timeline] == [_PLAN_A, _PLAN_C]


def test_read_boot_artifacts_missing_run_node_degrades_quietly(tmp_path):
    assert bench._read_boundaries_raw(None) == []
    assert bench._post_tool_boundary_count(None) == 0
    assert bench._read_mood_line(None) == ""


# ── Sandbox prep helpers ─────────────────────────────────────────────


def test_stage_prompts_override_copies_only_markdown(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "run.md").write_text("custom run.md\n", encoding="utf-8")
    (prompts_dir / "notes.txt").write_text("not staged\n", encoding="utf-8")

    staged = bench._stage_prompts_override(repo, prompts_dir)
    assert staged == ["run.md"]
    assert (repo / ".brr" / "prompts" / "run.md").read_text(encoding="utf-8") == "custom run.md\n"
    assert not (repo / ".brr" / "prompts" / "notes.txt").exists()


def test_write_boot_config_pins_shell_and_skips_network(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    bench._write_boot_config(repo, "claude-haiku")
    config = (repo / ".brr" / "config").read_text(encoding="utf-8")
    assert "shell=claude-haiku" in config
    assert "sync.fetch_before_run=false" in config


def test_boot_clone_id_is_filesystem_safe():
    clone_id = bench._boot_clone_id("claude/haiku weird", "out dir!!")
    assert clone_id
    assert all(c.isalnum() or c in "._-" for c in clone_id)


# ── CLI wiring, fake dispatcher ───────────────────────────────────────


def _fake_dispatch_factory(bolt="accepted"):
    def _fake_dispatch(scenario, *, runner, prompts_dir, repo_root, root, timeout_seconds):
        artifacts = _full_marks_artifacts()
        artifacts.bolt = bolt
        artifacts.done_when_result = (True, "fixture always satisfies done_when")
        info = bench.BootDispatchInfo(clone_id="fake-clone", repo=root)
        return artifacts, info

    return _fake_dispatch


def test_cli_bench_boot_writes_row_json_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "dispatch_boot_run", _fake_dispatch_factory())
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    out_dir = tmp_path / "out"

    rc = main([
        "bench", "boot",
        "--runner", "claude-haiku",
        "--prompts", str(prompts_dir),
        "--scenario", str(SCENARIOS_DIR / "smoke.yaml"),
        "--out", str(out_dir),
    ])
    assert rc == 0

    row_path = out_dir / "claude-haiku__prompts.json"
    assert row_path.exists()
    payload = json.loads(row_path.read_text(encoding="utf-8"))
    assert payload["runner"] == "claude-haiku"
    assert payload["first_divergence"] is None

    summary = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "claude-haiku" in summary
    assert "prompts" in summary


def test_cli_bench_boot_nonzero_exit_on_divergence(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "dispatch_boot_run", _fake_dispatch_factory(bolt=None))
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    out_dir = tmp_path / "out"

    rc = main([
        "bench", "boot",
        "--runner", "claude-haiku",
        "--prompts", str(prompts_dir),
        "--scenario", str(SCENARIOS_DIR / "smoke.yaml"),
        "--out", str(out_dir),
    ])
    assert rc == 1
    payload = json.loads((out_dir / "claude-haiku__prompts.json").read_text(encoding="utf-8"))
    assert payload["first_divergence"] == "bolt"


def test_cli_bench_boot_runs_full_matrix(tmp_path, monkeypatch):
    seen_pairs = []

    def _fake_dispatch(scenario, *, runner, prompts_dir, repo_root, root, timeout_seconds):
        seen_pairs.append((runner, prompts_dir.name))
        artifacts = _full_marks_artifacts()
        artifacts.done_when_result = (True, "fixture")
        return artifacts, bench.BootDispatchInfo(clone_id="fake", repo=root)

    monkeypatch.setattr(bench, "dispatch_boot_run", _fake_dispatch)
    prompts_a = tmp_path / "prompts-a"
    prompts_b = tmp_path / "prompts-b"
    prompts_a.mkdir()
    prompts_b.mkdir()
    out_dir = tmp_path / "out"

    rc = main([
        "bench", "boot",
        "--runner", "claude-haiku", "--runner", "claude",
        "--prompts", str(prompts_a), "--prompts", str(prompts_b),
        "--scenario", str(SCENARIOS_DIR / "smoke.yaml"),
        "--out", str(out_dir),
    ])
    assert rc == 0
    assert len(seen_pairs) == 4
    assert (out_dir / "claude-haiku__prompts-a.json").exists()
    assert (out_dir / "claude__prompts-b.json").exists()


def test_cli_bench_boot_rejects_missing_prompts_dir(tmp_path, capsys):
    rc = main([
        "bench", "boot",
        "--runner", "claude-haiku",
        "--prompts", str(tmp_path / "does-not-exist"),
        "--scenario", str(SCENARIOS_DIR / "smoke.yaml"),
        "--out", str(tmp_path / "out"),
    ])
    assert rc == 2
    assert "is not a directory" in capsys.readouterr().out


def test_cli_bench_boot_rejects_bad_scenario(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("steers: []\n", encoding="utf-8")
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    rc = main([
        "bench", "boot",
        "--runner", "claude-haiku",
        "--prompts", str(prompts_dir),
        "--scenario", str(bad),
        "--out", str(tmp_path / "out"),
    ])
    assert rc == 2
    assert "bad scenario" in capsys.readouterr().out
