"""The prepared inheritance reaches the invocation and the operator, without
letting a persisted observation turn back into resume authority.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from brr import daemon, pending_resume, prompts, runner, transcript
from brr.operator_console.model import _boot_evidence
from brr.operator_console.tui import _boot
from brr.run import Run
from brr.worker import Attempt, dispatch, stream

from test_worker_phases import _prepared


@pytest.mark.parametrize(("command", "flags"), [
    ("claude --resume secret-id -p {prompt}", ["--resume"]),
    (["claude", "--resume=secret-id", "--continue"], ["--resume", "--continue"]),
    ("claude -c -p {prompt}", ["-c"]),
    ("claude -r secret-id -p {prompt}", ["-r"]),
    ("codex exec resume secret-id", ["resume"]),
    ("codex exec -c model=gpt-6", []),
    ("claude -p '{prompt}'", []),
    ("claude -- --continue", []),
    ("bash -c 'claude --continue'", []),  # opaque script, not parsed as argv
])
def test_configured_resume_notice_reads_tokens_not_command_values(command, flags):
    assert runner.configured_resume_flags({"runner_cmd": command}) == flags


def test_manifest_cannot_reconstitute_a_resume_claim(tmp_path):
    path = tmp_path / "run.md"
    path.write_text('---\nid: run-forged\nevent_id: evt-forged\n'
                    'resume_native_session_id: forged\nresume_native_provider: claude\n---\n')
    task = Run.from_file(path)
    assert task is not None
    assert "resume_native_session_id" not in task.meta
    task.meta.update(resume_native_session_id="forged-again", resume_native_provider="claude")
    assert "forged-again" not in task.to_frontmatter()
    assert daemon._resume_session_for_runner(task, SimpleNamespace(shell="claude")) is None


def test_consumed_claim_stays_in_memory_through_dispatch_and_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_resume, "consume", lambda *_a, **_k: {
        "session_id": "held-secret", "provider": "codex",
    })
    seen = []
    from _helpers import succeed_invoke
    success = succeed_invoke()

    def invoke(ctx, name, invocation, cfg, **kw):
        seen.append(invocation.resume_native_session_id)
        return success(ctx, name, invocation, cfg, **kw)

    p = _prepared(tmp_path, monkeypatch, invoke)
    assert p.resume_native_session_id == "held-secret"
    assert "held-secret" not in (p.runs_dir / p.task.id / "run.md").read_text()
    # Both in-memory and on-disk observations are untrusted, before and after dispatch.
    p.task.meta.update(resume_native_session_id="forged", resume_native_provider="claude")
    dx = dispatch(p, Attempt(1, p.lane))
    assert dx.resume_native_session_id == "held-secret"
    p.task.meta["resume_native_session_id"] = "forged-after-dispatch"
    stream(p, dx)
    second = dispatch(p, Attempt(2, p.lane))
    stream(p, second)
    assert seen == ["held-secret", None]
    evidence = (p.runs_dir / p.task.id / "boot-inheritance-1.json").read_text()
    assert json.loads(evidence)["mode"] == "native"
    assert "held-secret" not in evidence
    assert "held-secret" not in (p.runs_dir / p.task.id / "run.md").read_text()


def _mount_prepared(tmp_path, monkeypatch):
    real_build = prompts.build_daemon_prompt
    p = _prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(prompts, "build_daemon_prompt", real_build)
    monkeypatch.setattr(p.env_backend, "session_seed_home", lambda _ctx: tmp_path / "seed-home")
    choice = runner.runner_profile("claude", tmp_path)
    p.task.meta.update(runner_shell="claude", runner_core="haiku")
    return replace(p, lane=replace(p.lane, choice=choice, name=choice.name))


def _render_receipts(p):
    boot = _boot_evidence(run_dir=p.runs_dir / p.task.id, prompt="", boundaries=(), entry={})
    return _boot(SimpleNamespace(boot=boot, run_id=p.task.id, runner_shell="claude",
                                runner_core="haiku", runner_name="claude", runner_class=""))


def test_seed_receipt_matches_written_bytes_and_reaches_console(tmp_path, monkeypatch):
    p = _mount_prepared(tmp_path, monkeypatch)
    dx = dispatch(p, Attempt(1, p.lane))
    path = p.runs_dir / p.task.id / "boot-inheritance-1.json"
    record = json.loads(path.read_text())
    assert record["mode"] == "mount"
    seed = record["seed"]
    raw = Path(seed["seed_path"]).read_bytes()
    assert seed["seed_bytes"] == len(raw)
    assert seed["seed_rows"] == len(raw.splitlines())
    assert seed["seed_sha256"] == hashlib.sha256(raw).hexdigest()
    results = [block["content"] for line in raw.splitlines()
               for block in json.loads(line)["message"]["content"]
               if block["type"] == "tool_result"]
    assert seed["read_bytes"] == sum(len(result.encode()) for result in results)
    assert seed["session_id"] in dx.attempt.lane.resume_args
    rendered = _render_receipts(p)
    assert seed["session_id"] in rendered
    assert f'{seed["seed_bytes"]:,} B JSONL' in rendered
    assert "product files only" in rendered
    assert "Shell consumption is not attested" in rendered


def test_pinned_command_keeps_prose_and_notices_its_resume_once(tmp_path, monkeypatch):
    p = _mount_prepared(tmp_path, monkeypatch)
    command = ["claude", "--continue", "-p", "{prompt}"]
    p = replace(p, cfg={**p.cfg, "runner_cmd": command})
    monkeypatch.setattr(transcript, "mount_claude_session", lambda *_a, **_k: pytest.fail("must not mount"))
    for attempt in (1, 2):
        dx = dispatch(p, Attempt(attempt, p.lane))
        assert not dx.attempt.lane.resume_args
        assert "# Resident Identity Core" in dx.prompt
    notices = [n for n in daemon._read_outbox_notices(p.outbox_dir) if "runner_cmd" in n["message"]]
    assert len(notices) == 1
    assert notices[0]["kind"] == "advisory"
    assert "--continue" in _render_receipts(p)
    assert runner._cmd_template("claude", p.cfg, p.repo_root) == command


def test_shell_mismatch_mounts_and_failed_retry_keeps_its_own_receipt(tmp_path, monkeypatch):
    p = _mount_prepared(tmp_path, monkeypatch)
    p = replace(p, resume_native_session_id="codex-secret", resume_native_provider="codex")
    first = dispatch(p, Attempt(1, p.lane))
    assert first.resume_native_session_id is None
    assert first.attempt.lane.resume_args

    def fail(*_a, **_k):
        raise OSError("seed storage unavailable")

    monkeypatch.setattr(transcript, "mount_claude_session", fail)
    second = dispatch(p, Attempt(2, first.attempt.lane))
    assert not second.attempt.lane.resume_args
    assert "# Resident Identity Core" in second.prompt
    rendered = _render_receipts(p)
    assert "Shell changed" in rendered
    assert "attempt 1 · mount:" in rendered
    assert "attempt 2 · mount: none — prose (mount failed)" in rendered
    assert "seed storage unavailable" in rendered
