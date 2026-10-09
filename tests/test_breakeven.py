"""Request accounting and payback, driven by this machine's real captures."""
import json
from pathlib import Path
from statistics import median

import pytest

from brr import breakeven, claude_status, daemon, hooks, vibe_usage
from brr.run import Run

FIXTURES = Path(__file__).parent / "fixtures/breakeven"
RUNS = sorted(p.parent for p in FIXTURES.glob("*/*/provenance.json"))


@pytest.mark.parametrize("directory", RUNS, ids=lambda p: p.name)
def test_native_real_records(directory):
    provenance = json.loads((directory / "provenance.json").read_text())
    snapshot = breakeven.native(directory / "native.jsonl", provenance["shell"],
                                core=provenance["core"])
    assert snapshot["boot"] == provenance["measured"]["B"]
    assert median(r["weighted"] for r in snapshot["baseline"]) == provenance["measured"]["c0"]
    assert len(snapshot["baseline"]) == 3
    assert len({r["request"] for r in snapshot["baseline"]}) == 3
    assert all(r["weighted"] > 0 for r in snapshot["baseline"])


@pytest.mark.parametrize("directory", RUNS, ids=lambda p: p.name)
def test_original_hook_draws_cannot_prove_a_baseline(directory):
    spend_path = directory / "spend.json"
    spend = json.loads(spend_path.read_text()) if spend_path.exists() else {}
    assert breakeven.baseline(spend, directory / "boundaries.jsonl") is None
    assert breakeven.chip(breakeven.project({}, None, shell=directory.parent.name,
                                          core=None)) == "n* ?"


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


def test_exact_identity_no_global_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(breakeven.codex_status, "_latest_rollout_fallback",
                        lambda *a: pytest.fail("read sibling"))
    assert breakeven.collect({}, "codex", tmp_path, tmp_path) == {}
    assert breakeven.collect({"codex_thread_id": "../bad"}, "codex", tmp_path, tmp_path) == {}
    assert breakeven.collect({}, "vibe", tmp_path, tmp_path) == {}


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


def test_model_request_dedup_and_boot_exclusion(tmp_path):
    p = next(p for p in RUNS if p.parent.name == "codex")
    native = breakeven.native(p / "native.jsonl", "codex")
    cost = native["baseline"][0]
    boundary = tmp_path / "boundaries.jsonl"
    records = [{"phase": "post-tool", "cost": {**cost, "request": native["boot_request"]}},
               {"phase": "post-tool", "cost": cost},
               {"phase": "post-tool", "cost": cost},
               {"phase": "post-tool", "cost": native["baseline"][1], "subagent": {"id": "child"}},
               {"phase": "post-tool", "cost": native["baseline"][2]}]
    boundary.write_text("".join(json.dumps(r) + "\n" for r in records))
    assert breakeven.baseline({"boot": {"request": native["boot_request"]}}, boundary) == median([
        cost["weighted"], native["baseline"][2]["weighted"]])


@pytest.mark.parametrize("bad", [None, True, -1, float("nan"), float("inf"), "12", 10**500])
def test_bad_terms_are_unknown(bad):
    assert breakeven.chip(breakeven.calculate(bad, 100, 10)) == "n* ?"


def test_finite_infinite_and_full_bar():
    terms = breakeven.calculate(38607, 27000, 16973)
    assert breakeven.chip(terms) == "n* 4"
    assert breakeven.chip(breakeven.calculate(38607, 16973, 16973)) == "n* ∞"
    assert breakeven.chip(breakeven.calculate(38607, 1, 16973)) == "n* ∞"
    assert hooks._hold_chip({"quota": {"hold": {"ratio": 0.8}}}) == "hold 0.8·boot"
    rendered = {}
    hooks._render_bar(run={}, pending=0, pending_known=True, pending_files=0,
                      events=[], budget={}, outbound={}, produce={}, card={},
                      card_stale=False, run_name={}, mood=None, resources={"breakeven": terms,
                      "quota": {"hold": {"ratio": 0.8}}}, rendered_chips=rendered)
    assert rendered["hold"] == "hold 0.8·boot"
    assert rendered["breakeven"] == "n* 4"


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


@pytest.mark.parametrize("shell", ["claude", "codex", "vibe"])
def test_corrupt_native_rows_do_not_raise(tmp_path, shell):
    path = tmp_path / "corrupt.jsonl"
    path.write_text('bad json\n{"type":"assistant","message":[]}\n'
                    '{"type":"event_msg","payload":false}\n'
                    '{"type":"action_result","payload":{"result":[]}}\n')
    assert breakeven.native(path, shell) == {}


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
