"""Request accounting and payback, driven by this machine's real captures."""
import json
from pathlib import Path
from statistics import median

import pytest

from brr import breakeven, claude_status, daemon, vibe_usage
from brr.run import Run

FIXTURES = Path(__file__).parent / "fixtures/breakeven"
RUNS = sorted(p.parent for p in FIXTURES.glob("*/*/provenance.json"))


def install_native(directory, tmp_path, monkeypatch):
    info = json.loads((directory / "provenance.json").read_text())
    shell = info["shell"]
    outbox = tmp_path / "outbox"
    outbox.mkdir(parents=True)
    task = Run(id=directory.name, event_id="evt-1", body="", source="spawn")
    if shell == "claude":
        monkeypatch.setattr(daemon.allowance, "latest_claude_transcript",
                            lambda *a, **k: directory / "native.jsonl")
    elif shell == "codex":
        task.meta["codex_thread_id"] = "01a0fe50-8d27-7291-8ce5-95610a77c39b"
        monkeypatch.setattr(breakeven.codex_status, "_rollout_for_thread",
                            lambda *a: directory / "native.jsonl")
    else:
        side = json.loads((directory / "vibe-usage.json").read_text())
        vibe_usage.write_sidecar(outbox, side)
        home = tmp_path / "vibe"
        journal = home / "logs/session/unified" / side["session_id"] / "journal"
        journal.mkdir(parents=True)
        (journal / "0001.jsonl").write_bytes((directory / "native.jsonl").read_bytes())
        monkeypatch.setenv("VIBE_HOME", str(home))
    return info, task, outbox


@pytest.mark.parametrize("directory", RUNS, ids=lambda p: p.name)
def test_boot_stamp_and_baseline_survive_snapshot_overwrite(directory, tmp_path, monkeypatch):
    info, task, outbox = install_native(directory, tmp_path, monkeypatch)
    daemon._record_boot_cost(task, info["shell"], tmp_path, outbox)
    stamp = claude_status.load_snapshot(outbox)["boot"]
    assert stamp["weighted"] == info["measured"]["B"]
    assert stamp["accounting_version"] == 1
    assert median(r["weighted"] for r in stamp["baseline"]) == info["measured"]["c0"]
    claude_status.write_snapshot(outbox, {"source": "terminal"})
    assert claude_status.load_snapshot(outbox)["boot"] == stamp
    # The unchanged native file does not get parsed again at every heartbeat.
    monkeypatch.setattr(breakeven, "requests", lambda *a, **k: pytest.fail("reparsed"))
    daemon._record_boot_cost(task, info["shell"], tmp_path, outbox)


def test_incremental_reader_retries_torn_tail(tmp_path, monkeypatch):
    directory = next(p for p in RUNS if p.parent.name == "codex")
    info, task, outbox = install_native(directory, tmp_path, monkeypatch)
    native = tmp_path / "rollout.jsonl"
    lines = (directory / "native.jsonl").read_text().splitlines(True)
    native.write_text("".join(lines[:-1]) + lines[-1][:20])
    monkeypatch.setattr(breakeven.codex_status, "_rollout_for_thread", lambda *a: native)
    before = breakeven.collect(task.meta, "codex", tmp_path, outbox)
    native.write_text("".join(lines))
    after = breakeven.collect(task.meta, "codex", tmp_path, outbox)
    assert after["latest"]["request"] != before["latest"]["request"]
    assert after["boot"] == before["boot"]


def test_cohort_exact_core_limit_and_medians(tmp_path, monkeypatch):
    directories = [p for p in RUNS if p.parent.name == "codex"]
    for directory in directories:
        info, task, outbox = install_native(directory, tmp_path / directory.name, monkeypatch)
        daemon._record_boot_cost(task, "codex", tmp_path, outbox)
        dest = tmp_path / "runs" / directory.name
        dest.mkdir(parents=True)
        (dest / "state.md").write_text(
            f"---\nrun_id: {directory.name}\nrunner_shell: codex\nrunner_core: {info['core']}\nstarted_at: {info['state']['started_at']}\n---\n")
        (dest / "spend.json").write_bytes((outbox / claude_status.SNAPSHOT_NAME).read_bytes())
    terms = breakeven.cohort(tmp_path / "runs", "codex", "gpt-6.1-sol")
    assert terms["B"] == 38607
    assert terms["c0"] == 16973
    assert terms["boot_samples"] == terms["baseline_samples"] == 3
    assert breakeven.cohort(tmp_path / "runs", "codex", "different")["B"] is None
    assert breakeven.cohort(tmp_path / "runs", "codex", "gpt-6.1-sol", limit=1)["boot_samples"] == 1


def test_resume_does_not_create_fresh_sample_or_change_hold_meter(tmp_path, monkeypatch):
    directory = next(p for p in RUNS if p.parent.name == "claude")
    info, task, outbox = install_native(directory, tmp_path, monkeypatch)
    task.meta["resume_native_session_id"] = "existing-session"
    old = daemon.allowance.claude_first_turn_boot_tokens(directory / "native.jsonl")
    daemon._record_boot_cost(task, "claude", tmp_path, outbox)
    stamp = claude_status.load_snapshot(outbox)["boot"]
    assert stamp["resumed"] is True
    assert stamp["accounting_version"] is None
    assert stamp["hold_weighted"] == old


def test_native_cache_drops_a_predecessors_coordinate(tmp_path, monkeypatch):
    directories = [p for p in RUNS if p.parent.name == "codex"]
    info, task, outbox = install_native(directories[0], tmp_path, monkeypatch)
    first = breakeven.collect(task.meta, "codex", tmp_path, outbox)
    monkeypatch.setattr(breakeven.codex_status, "_rollout_for_thread",
                        lambda *a: directories[1] / "native.jsonl")
    second = breakeven.collect(task.meta, "codex", tmp_path, outbox)
    assert second["boot"] != first["boot"]
    assert second["boot_request"] != first["boot_request"]


def test_archive_recovery_reads_exact_native_prefix(tmp_path, monkeypatch):
    directory = next(p for p in RUNS if p.parent.name == "codex")
    info, task, outbox = install_native(directory, tmp_path, monkeypatch)
    run_dir = tmp_path / "local" / task.id
    run_dir.mkdir(parents=True)
    (run_dir / "run.md").write_text(task.to_frontmatter())
    recovered = breakeven.historical_boot(run_dir, "codex")
    assert recovered["weighted"] == info["measured"]["B"]
    assert recovered["core"] == info["core"]
    task.meta["resume_native_session_id"] = task.meta["codex_thread_id"]
    (run_dir / "run.md").write_text(task.to_frontmatter())
    assert breakeven.historical_boot(run_dir, "codex") == {}


def test_placeholder_stamp_upgrades_when_billed_usage_arrives(tmp_path, monkeypatch):
    directory = next(p for p in RUNS if p.parent.name == "claude")
    info, task, outbox = install_native(directory, tmp_path, monkeypatch)
    lines = (directory / "native.jsonl").read_text().splitlines(True)
    transcript = tmp_path / "growing.jsonl"
    transcript.write_text(lines[0])  # the actual zero placeholder in this capture
    monkeypatch.setattr(daemon.allowance, "latest_claude_transcript", lambda *a, **k: transcript)
    daemon._record_boot_cost(task, "claude", tmp_path, outbox)
    first = claude_status.load_snapshot(outbox)["boot"]
    assert first["weighted"] == 0
    assert first["accounting_version"] is None
    transcript.write_text("".join(lines))
    daemon._record_boot_cost(task, "claude", tmp_path, outbox)
    final = claude_status.load_snapshot(outbox)["boot"]
    assert final["weighted"] == info["measured"]["B"]
    assert final["accounting_version"] == 1
    assert final["hold_weighted"] == 0
