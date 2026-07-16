"""Tests for the daemon lifecycle packets after triage was removed.

These verify that the worker emits the run-progress packets in the
right order for happy-path, retry, and Docker-preserved-container
scenarios. Records are read directly from the per-conversation log.
"""

from __future__ import annotations

from pathlib import Path

from brr import conversations, daemon, envs, run_progress
from brr.runner import RunnerArtifactRecord, RunnerResult

from _helpers import (
    StubWorktreeEnv,
    make_event,
    succeed_invoke,
    write_repo_scaffold,
)


def _patch_runner(monkeypatch):
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda root, _overrides=None: daemon.runner.runner_profile("codex", root))
    monkeypatch.setattr(
        daemon.runner, "fallback_runner_profile",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, _root, **kw: f"RUN {eid}: {task} -> {rp}",
    )


def _update_records(brr_dir: Path, conv_key: str) -> list[dict]:
    return [r for r in conversations.read_records(brr_dir, conv_key)
            if r.get("kind") == "update"]


def _packet_types(brr_dir: Path, conv_key: str) -> list[str]:
    return [r.get("type") for r in _update_records(brr_dir, conv_key)]


def test_success_emits_full_progress_lifecycle(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(
        tmp_path, eid="evt-success", body="ship it",
        telegram_chat_id=10, telegram_topic_id=1,
    )
    _patch_runner(monkeypatch)
    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=succeed_invoke()),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    types = _packet_types(tmp_path / ".brr", task.conversation_key)
    assert "run_created" in types
    assert "env_prepared" in types
    assert "attempt_started" in types
    assert "run_started" in types
    assert "finalizing" in types
    assert "done" in types
    assert "triage_done" not in types
    assert types.index("env_prepared") < types.index("attempt_started")
    assert types.index("attempt_started") < types.index("finalizing")
    assert types.index("finalizing") < types.index("done")


def test_sync_packet_is_scoped_to_run(tmp_path, monkeypatch):
    """The sync card line belongs to a run, not to the whole thread.

    Run cards now ignore anonymous task-era records, so the daemon must
    emit sync outcomes only after it has a concrete run id to attach.
    """
    write_repo_scaffold(tmp_path)
    event = make_event(
        tmp_path, eid="evt-sync", body="ship it",
        telegram_chat_id=10, telegram_topic_id=1,
    )
    _patch_runner(monkeypatch)
    monkeypatch.setattr(
        daemon.sync,
        "refresh_before_run",
        lambda *_args, **_kwargs: daemon.sync.SyncResult(
            ff_branches={"main": "abc1234"},
        ),
    )
    monkeypatch.setattr(
        daemon.sync,
        "render_summary",
        lambda _result: "ff main -> abc1234",
    )
    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=succeed_invoke()),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    records = _update_records(tmp_path / ".brr", task.conversation_key)
    synced = next(r for r in records if r.get("type") == "synced")
    assert synced["run_id"] == task.id
    assert synced["event_id"] == "evt-sync"

    view = run_progress.project_run(
        tmp_path / ".brr", task.conversation_key, task.id,
    )
    assert view is not None
    assert view.sync_summary == "ff main -> abc1234"


def test_retry_emits_attempt_failed_and_retrying(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(
        tmp_path, eid="evt-retry", body="missing artifact",
        telegram_chat_id=20,
    )
    _patch_runner(monkeypatch)

    # Retry is triggered by a missing *required artifact* — empty stdout
    # alone stopped being a retry reason with the 2026-07-16 ceremony cut
    # (nobody re-runs a wake to extract a terminal sentence).
    def _retry_invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        if invocation.label.endswith("attempt-1"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="", stderr="", returncode=0, trace_dir=None,
                artifacts=[RunnerArtifactRecord(
                    path=Path("out.md"), label="out.md", exists=False,
                )],
            )
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=_retry_invoke),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 1,
    )

    assert task.status == "done"
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    types = [r.get("type") for r in records]
    assert types.count("attempt_started") == 2
    assert "attempt_failed" in types
    assert "retrying" in types
    failed = next(r for r in records if r.get("type") == "attempt_failed")
    assert failed.get("will_retry") is True


def test_clean_silent_run_fails_once_without_retry(tmp_path, monkeypatch):
    """Ceremony cut 2026-07-16: a clean exit that communicated nothing —
    no stdout, no outbox reply, no commit — is NOT re-run to extract a
    terminal sentence. It takes the give-up path in one attempt: the run
    errors, the addressed event gets the daemon's terminal failure note,
    and no `retrying` packet is emitted."""
    write_repo_scaffold(tmp_path)
    event = make_event(
        tmp_path, eid="evt-silent", body="say nothing",
        telegram_chat_id=21,
    )
    _patch_runner(monkeypatch)

    attempts: list[str] = []

    def _silent_invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        attempts.append(invocation.label)
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=_silent_invoke),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 1,
    )

    assert task.status == "error"
    assert len(attempts) == 1
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    types = [r.get("type") for r in records]
    assert "retrying" not in types
    assert "failed" in types
    # The addressed event still gets a visible terminal note — silence is
    # surfaced, never re-manufactured.
    assert task.terminal_reply


def test_hard_failure_does_not_retry_and_bubbles_error_to_failed_packet(
    tmp_path, monkeypatch,
):
    """A timeout (or any non-zero exit) is unretryable: the daemon must
    surface the real error to the gate immediately rather than burn
    another expensive attempt on the same prompt."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-timeout", body="big task",
                        telegram_chat_id=40)
    _patch_runner(monkeypatch)

    attempts: list[str] = []

    def _timed_out(_ctx, runner_name, invocation, _cfg, *, trace=False):
        attempts.append(invocation.label)
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="",
            stderr="OpenAI Codex v0.128.0\nthinking…\nrunner timed out after 3600s",
            returncode=124, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=_timed_out),
    )

    # max_retries=3 — even with retries allowed, hard failure must skip
    # them and give up immediately.
    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 3,
    )

    assert task.status == "error"
    assert attempts == ["evt-timeout-attempt-1"]
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    types = [r.get("type") for r in records]
    assert "retrying" not in types
    failed = next(r for r in records if r.get("type") == "failed")
    assert failed.get("exit_code") == 124
    assert failed.get("timed_out") is True
    assert failed.get("attempts") == 1
    assert "timed out after 3600s" in failed.get("error", "")
    # finalizing fires before the canonical failed packet so projections
    # show the real error rather than "finalizing (failed)".
    assert types.index("finalizing") < types.index("failed")
    attempt_failed = next(r for r in records if r.get("type") == "attempt_failed")
    assert attempt_failed.get("will_retry") is False
    assert attempt_failed.get("exit_code") == 124


def test_quota_failure_is_classified_for_attempt_and_terminal_packets(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-quota", body="big task",
                        telegram_chat_id=41)
    _patch_runner(monkeypatch)

    def _quota_hit(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="",
            stderr="You've hit your session limit · resets 5am (Europe/Berlin)",
            returncode=1, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=_quota_hit),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 3,
    )

    assert task.status == "error"
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    attempt_failed = next(r for r in records if r.get("type") == "attempt_failed")
    assert attempt_failed.get("failure_kind") == "quota_exhausted"
    failed = next(r for r in records if r.get("type") == "failed")
    assert failed.get("failure_kind") == "quota_exhausted"
    assert "session limit" in failed.get("error", "")
    response = (tmp_path / ".brr" / "responses" / "evt-quota.md").read_text(
        encoding="utf-8"
    )
    assert "runner quota was exhausted" in response


def test_relay_candidate_rides_attempt_failure_and_terminal_response(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-relay", body="big task",
                        telegram_chat_id=43)
    _patch_runner(monkeypatch)
    relay_profile = daemon.runner_select.runner_from_profile(
        "brnrd-codex-relay",
        {
            "owner": "brnrd",
            "provider": "openai",
            "model": "gpt-5-codex-relay",
            "class": "relay",
            "cost_rank": 1,
        },
    )
    monkeypatch.setattr(
        daemon.runner_select,
        "available_runners",
        lambda _repo: [relay_profile],
    )

    def _quota_hit(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="",
            stderr="You've hit your session limit",
            returncode=1, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=_quota_hit),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    attempt_failed = next(r for r in records if r.get("type") == "attempt_failed")
    assert attempt_failed.get("needs_relay_consent") is True
    assert "brnrd-codex-relay" in attempt_failed.get("relay_candidate", "")
    relay_plan = attempt_failed.get("relay_plan")
    assert relay_plan["reason"] == "quota_exhausted"
    assert relay_plan["model"] == "gpt-5-codex-relay"
    assert relay_plan["provider"] == "openai"
    response = (tmp_path / ".brr" / "responses" / "evt-relay.md").read_text(
        encoding="utf-8"
    )
    assert "Relay fallback" in response
    assert "brnrd-codex-relay" in response
    assert "did not spend relay tokens automatically" in response


def test_operational_failure_falls_back_to_next_runner(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-fallback", body="big task",
                        telegram_chat_id=42)
    _patch_runner(monkeypatch)
    monkeypatch.setattr(
        daemon.runner, "fallback_runner_profile",
        lambda _repo, _current, kind, *, tried=(): (
            daemon.runner.runner_profile("claude", _repo)
            if kind == "quota_exhausted" else None
        ),
    )

    attempts: list[str] = []

    def _quota_then_success(_ctx, runner_name, invocation, _cfg, *, trace=False):
        attempts.append(runner_name)
        if runner_name == "codex":
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="",
                stderr="You've hit your session limit",
                returncode=1, trace_dir=None, artifacts=[],
            )
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=_quota_then_success),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    assert attempts == ["codex", "claude"]
    assert task.meta["runner_name"] == "claude"
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    attempt_failed = next(r for r in records if r.get("type") == "attempt_failed")
    assert attempt_failed.get("failure_kind") == "quota_exhausted"
    assert attempt_failed.get("will_fallback") is True
    assert attempt_failed.get("fallback_runner") == "claude"
    retrying = next(r for r in records if r.get("type") == "retrying")
    assert retrying.get("from_runner") == "codex"
    assert retrying.get("runner") == "claude"
    assert retrying.get("reason") == "fallback after quota_exhausted"


def test_failure_after_retries_emits_finalizing_then_failed(tmp_path, monkeypatch):
    """The failed packet must be the last word.

    Gates and the conversation projection replay updates in order and
    take the most recent packet as the terminal explanation. If
    ``finalizing(stage=failed)`` lands after ``failed``, its placeholder
    detail ("finalizing (failed)") clobbers the real failure reason.
    """
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-fail", body="never works",
                        telegram_chat_id=30)
    _patch_runner(monkeypatch)

    def _always_fail(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=_always_fail),
    )

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    types = _packet_types(tmp_path / ".brr", task.conversation_key)
    assert "attempt_failed" in types
    assert "failed" in types
    assert types.index("finalizing") < types.index("failed")


class _FakeDockerEnv:
    """In-memory Docker env stub for daemon packet assertions."""

    name = "docker"

    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.containers: list[str] = []

    def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                outbox_path=None):
        ctx = envs.RunContext(
            name=self.name,
            cwd=repo_root,
            repo_root=repo_root,
            runtime_dir=repo_root / ".brr",
            response_path_host=response_path,
            response_path_env=response_path,
            outbox_host=outbox_path,
            outbox_env=outbox_path,
            branch_name=None,
        )
        ctx.env_state.update({
            "run_id": task.id,
            "docker_image": "img:latest",
            "docker_containers": [],
        })
        task.meta["docker_image"] = "img:latest"
        return ctx

    def invoke(self, ctx, runner_name, invocation, cfg, *, trace=False):
        cid = f"brr-{ctx.env_state['run_id']}-{invocation.label}"
        ctx.env_state["docker_containers"].append(cid)
        ctx.env_state["docker_container"] = cid
        self.containers.append(cid)
        response = Path(invocation.response_path)
        if self.succeed:
            response.parent.mkdir(parents=True, exist_ok=True)
            response.write_text("docker ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout="docker ok\n" if self.succeed else "",
            stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    def finalize(self, ctx, task, runs_dir):
        preserved = ctx.env_state.get("docker_containers", [])
        if preserved and task.status != "done":
            task.meta["docker_containers"] = ", ".join(preserved)
            task.save(runs_dir)
        return task


def test_docker_env_emits_container_started(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-docker", body="run docker",
                        telegram_chat_id=40)
    _patch_runner(monkeypatch)

    fake_env = _FakeDockerEnv(succeed=True)
    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: fake_env)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    types = [r.get("type") for r in records]
    assert "container_started" in types
    container_event = next(r for r in records if r.get("type") == "container_started")
    assert container_event.get("container", "").startswith("brr-")


def test_docker_failed_emits_container_preserved(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-docker-fail", body="never finishes",
                        telegram_chat_id=50)
    _patch_runner(monkeypatch)

    fake_env = _FakeDockerEnv(succeed=False)
    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: fake_env)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    records = _update_records(tmp_path / ".brr", task.conversation_key)
    types = [r.get("type") for r in records]
    assert "failed" in types
    assert "container_preserved" in types
    preserved = next(r for r in records if r.get("type") == "container_preserved")
    assert preserved.get("containers"), preserved


# ── publish() arms ──────────────────────────────────────────────────
#
# ``daemon.publish`` is the single post-finalize publish step. It has
# five mutually exclusive arms; one test per arm so the decision table
# stays readable.


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _publish_task(*, meta: dict, conv_key: str = "telegram:99:") -> "daemon.Run":
    from brr.run import Run

    return Run(
        id="task-publish",
        event_id="evt-publish",
        body="publish me",
        status="done",
        source="github",
        conversation_key=conv_key,
        meta=meta,
    )


def test_publish_noop_when_no_publish_branch(tmp_path, monkeypatch):
    """``publish_branch`` empty → publish returns without touching git."""
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    monkeypatch.setattr(daemon.gitops, "shared_brr_dir", lambda _r: brr_dir)
    calls: list = []
    monkeypatch.setattr(
        daemon.subprocess, "run",
        lambda args, **_kw: calls.append(args) or _Result(returncode=0),
    )

    task = _publish_task(meta={})
    daemon.publish(tmp_path, task)

    assert calls == []
    assert _packet_types(brr_dir, "telegram:99:") == []


def test_publish_plain_push_to_existing_upstream(tmp_path, monkeypatch):
    """Branch with upstream + new commits → plain ``git push``,
    push_started/push_done packets land on the conversation log."""
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    monkeypatch.setattr(daemon.gitops, "shared_brr_dir", lambda _r: brr_dir)
    monkeypatch.setattr(daemon.gitops, "branch_upstream", lambda _r, b: f"origin/{b}")
    monkeypatch.setattr(daemon.gitops, "branch_remote", lambda _r, _b: "origin")

    calls: list = []

    def _fake_run(args, **_kw):
        calls.append(args)
        if "log" in args:
            return _Result(returncode=0, stdout="abc Fix bug\n")
        return _Result(returncode=0)

    monkeypatch.setattr(daemon.subprocess, "run", _fake_run)

    task = _publish_task(meta={"publish_branch": "main"})
    daemon.publish(tmp_path, task)

    assert ["git", "push", "origin", "main"] in calls
    types = _packet_types(brr_dir, "telegram:99:")
    assert "push_started" in types
    assert "push_done" in types


def test_publish_new_branch_pushes_with_upstream_flag(tmp_path, monkeypatch):
    """New ``brr/<run-id>`` with no upstream + new commits → push
    with ``-u`` so the local branch tracks origin afterwards."""
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    monkeypatch.setattr(daemon.gitops, "shared_brr_dir", lambda _r: brr_dir)
    monkeypatch.setattr(daemon.gitops, "branch_upstream", lambda _r, _b: None)
    monkeypatch.setattr(daemon.gitops, "branch_remote", lambda _r, _b: None)
    monkeypatch.setattr(daemon.gitops, "default_remote", lambda _r: "origin")
    monkeypatch.setattr(daemon.gitops, "default_branch", lambda _r: "main")
    monkeypatch.setattr(daemon.gitops, "rev_parse", lambda _r, _ref: None)

    calls: list = []

    def _fake_run(args, **_kw):
        calls.append(args)
        if "merge-base" in args:
            return _Result(returncode=0, stdout="baseoid\n")
        if "log" in args:
            return _Result(returncode=0, stdout="abc Fix bug\n")
        return _Result(returncode=0)

    monkeypatch.setattr(daemon.subprocess, "run", _fake_run)

    task = _publish_task(meta={"publish_branch": "brr/task-1"})
    daemon.publish(tmp_path, task)

    assert ["git", "push", "-u", "origin", "brr/task-1"] in calls
    started = next(
        r for r in _update_records(brr_dir, "telegram:99:")
        if r.get("type") == "push_started"
    )
    assert started.get("set_upstream") is True


def test_publish_refspec_when_agent_kept_run_branch(tmp_path, monkeypatch):
    """Agent stayed on ``brr/<run-id>`` but event named a different
    expected publish branch → push via refspec to the expected name
    without touching any local ref."""
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    monkeypatch.setattr(daemon.gitops, "shared_brr_dir", lambda _r: brr_dir)
    monkeypatch.setattr(daemon.gitops, "branch_upstream", lambda _r, _b: None)
    monkeypatch.setattr(daemon.gitops, "branch_remote", lambda _r, _b: None)
    monkeypatch.setattr(daemon.gitops, "default_remote", lambda _r: "origin")
    monkeypatch.setattr(
        daemon.gitops, "rev_parse",
        lambda _r, ref: "remoteoid" if ref == "origin/feature/x" else None,
    )
    monkeypatch.setattr(daemon.gitops, "is_ancestor", lambda *_a, **_k: True)

    calls: list = []

    def _fake_run(args, **_kw):
        calls.append(args)
        if "log" in args:
            return _Result(returncode=0, stdout="abc rebased\n")
        return _Result(returncode=0)

    monkeypatch.setattr(daemon.subprocess, "run", _fake_run)

    task = _publish_task(meta={
        "publish_branch": "brr/task-1",
        "target_branch": "feature/x",
    })
    daemon.publish(tmp_path, task)

    assert ["git", "push", "origin", "brr/task-1:feature/x"] in calls
    # Refspec push must not carry ``-u`` (the local name doesn't match
    # the remote target, so an upstream would be meaningless).
    assert not any(
        arg == "-u" for cmd in calls for arg in cmd if "push" in cmd
    )


def test_publish_force_with_lease_for_rewritten_target(tmp_path, monkeypatch):
    """Agent rewrote ``feature/x`` locally (rebase) and brr captured the
    remote OID at task start → push with ``--force-with-lease`` anchored
    to that OID. This is the PR-rebase arm."""
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    monkeypatch.setattr(daemon.gitops, "shared_brr_dir", lambda _r: brr_dir)
    monkeypatch.setattr(daemon.gitops, "branch_upstream", lambda _r, b: f"origin/{b}")
    monkeypatch.setattr(daemon.gitops, "branch_remote", lambda _r, _b: "origin")
    # ``is_ancestor`` False → local rewrote history relative to the
    # remote, so the lease arm fires.
    monkeypatch.setattr(daemon.gitops, "is_ancestor", lambda *_a, **_k: False)

    calls: list = []

    def _fake_run(args, **_kw):
        calls.append(args)
        if "log" in args:
            return _Result(returncode=0, stdout="abc rebased\n")
        return _Result(returncode=0)

    monkeypatch.setattr(daemon.subprocess, "run", _fake_run)

    expected_oid = "6c1ca158d19c6ba40c06e8a46f7c338ada056246"
    task = _publish_task(
        meta={
            "publish_branch": "feature/x",
            "target_branch": "feature/x",
            "expected_remote_oid": expected_oid,
        },
        conv_key="github:owner/repo#17",
    )
    daemon.publish(tmp_path, task)

    assert [
        "git", "push",
        f"--force-with-lease=refs/heads/feature/x:{expected_oid}",
        "origin", "feature/x:refs/heads/feature/x",
    ] in calls
    started = next(
        r for r in _update_records(brr_dir, "github:owner/repo#17")
        if r.get("type") == "push_started"
    )
    assert started.get("force_with_lease") is True


def test_publish_flips_publish_status_to_conflict_on_push_failure(
    tmp_path, monkeypatch,
):
    """A failed push must mark the task's publish status as ``conflict``
    and emit the conflict packet so gates show the delivery failure
    instead of (falsely) celebrating success."""
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    monkeypatch.setattr(daemon.gitops, "shared_brr_dir", lambda _r: brr_dir)
    monkeypatch.setattr(daemon.gitops, "branch_upstream", lambda _r, b: f"origin/{b}")
    monkeypatch.setattr(daemon.gitops, "branch_remote", lambda _r, _b: "origin")

    def _fake_run(args, **_kw):
        if "log" in args:
            return _Result(returncode=0, stdout="abc Fix bug\n")
        if "push" in args:
            return _Result(
                returncode=1, stderr="error: failed to push",
            )
        return _Result(returncode=0)

    monkeypatch.setattr(daemon.subprocess, "run", _fake_run)

    task = _publish_task(meta={"publish_branch": "main"})
    daemon.publish(tmp_path, task)

    assert task.meta.get("publish_status") == "conflict"
    types = _packet_types(brr_dir, "telegram:99:")
    assert "conflict" in types
