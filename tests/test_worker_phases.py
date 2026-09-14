"""Each phase of the throw, driven on its own from a real fixture.

``test_worker_composition.py`` proves the composed throw reproduces ``main``;
this module proves the seams: every phase returns its declared shape, every
boundary kind is reachable, the shapes are frozen, and ``daemon._run_worker``
is a call into ``worker.run`` (so every patch of it still lands).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from brr import daemon, worker
from brr.run import Run
from brr.runner import RunnerArtifactRecord, RunnerResult
from brr.worker import (
    Attempt,
    Boundary,
    Dispatched,
    Finalized,
    Lane,
    Prepared,
    Streamed,
)

from _helpers import StubWorktreeEnv, make_event, succeed_invoke, write_repo_scaffold


@pytest.fixture(autouse=True)
def _close_path_and_clean_controls(monkeypatch):
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _wire(monkeypatch, invoke=None, *, fallback=None):
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda root, _overrides=None: daemon.runner.runner_profile("codex", root),
    )
    monkeypatch.setattr(
        daemon.runner, "fallback_runner_profile", fallback or (lambda *_a, **_k: None),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda task, eid, rp, _root, **kw: "PROMPT",
    )
    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _n: StubWorktreeEnv(invoke_fn=invoke or succeed_invoke()),
    )


def _prepared(tmp_path, monkeypatch, invoke=None, *, max_retries=0, **event_kw) -> Prepared:
    write_repo_scaffold(tmp_path)
    _wire(monkeypatch, invoke, fallback=event_kw.pop("fallback", None))
    event = make_event(tmp_path, eid=event_kw.pop("eid", "evt-phase"), **event_kw)
    prepared = worker.prepare(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, max_retries,
    )
    assert isinstance(prepared, Prepared)
    return prepared


def _to_boundary(p: Prepared, attempt: Attempt) -> Boundary:
    dispatched = worker.dispatch(p, attempt)
    assert isinstance(dispatched, Dispatched)
    streamed = worker.stream(p, dispatched)
    assert isinstance(streamed, Streamed)
    reached = worker.boundary(p, streamed)
    assert isinstance(reached, Boundary)
    return reached


def _result(invocation, runner_name, *, code=0, stdout="", stderr="", **kw):
    return RunnerResult(
        invocation=invocation, runner_name=runner_name, command=["mock"],
        stdout=stdout, stderr=stderr, returncode=code, trace_dir=None,
        artifacts=kw.pop("artifacts", []), **kw,
    )


# ── prepare ──────────────────────────────────────────────────────────


def test_prepare_returns_prepared_with_the_first_lane(tmp_path, monkeypatch):
    p = _prepared(tmp_path, monkeypatch)

    assert isinstance(p.lane, Lane)
    assert p.lane.name == "codex"
    assert isinstance(p.task, Run) and p.task.status == "running"
    assert p.outbox_dir == tmp_path / ".brr" / "outbox" / "evt-phase"
    assert (p.outbox_dir / "portal-state.json").exists()  # the `preparing` write


def test_prepare_ends_the_throw_on_a_refused_source(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    _wire(monkeypatch)
    event = make_event(tmp_path, eid="evt-untrusted", source="github", trust_tier="untrusted")

    ended = worker.prepare(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert isinstance(ended, Finalized)
    assert ended.stage == "refused"
    assert ended.task.status == "done"
    assert ended.task.meta["transitions"][-1]["why"] == "trust_refused"


# ── dispatch · stream · boundary · finalize, on the happy path ───────


def test_each_phase_returns_its_shape_through_to_done(tmp_path, monkeypatch):
    p = _prepared(tmp_path, monkeypatch)
    first = Attempt(n=1, lane=p.lane)

    dispatched = worker.dispatch(p, first)
    assert isinstance(dispatched, Dispatched)
    assert dispatched.attempt.n == 1
    assert dispatched.attempt.prompt_mode == "normal"
    assert isinstance(dispatched.prompt, str) and dispatched.prompt

    streamed = worker.stream(p, dispatched)
    assert isinstance(streamed, Streamed)
    assert isinstance(streamed.result, RunnerResult)
    assert streamed.dispatched is dispatched

    reached = worker.boundary(p, streamed)
    assert isinstance(reached, Boundary)
    assert reached.kind == "completed"
    assert reached.next_attempt is None
    assert reached.terminal_reply == "all done"

    ended = worker.finalize(p, reached)
    assert isinstance(ended, Finalized)
    assert ended.stage == "done"
    assert ended.task.status == "done"
    assert ended.task.meta["transitions"][-1]["why"] == "runner_completed"


# ── every other boundary kind ────────────────────────────────────────


def test_boundary_retry_carries_the_next_attempt(tmp_path, monkeypatch):
    def invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return _result(invocation, runner_name, artifacts=[
            RunnerArtifactRecord(path=Path("out.md"), label="out.md", exists=False),
        ])

    p = _prepared(tmp_path, monkeypatch, invoke, max_retries=1)
    reached = _to_boundary(p, Attempt(n=1, lane=p.lane))

    assert reached.kind == "retry"
    assert reached.next_attempt is not None
    assert reached.next_attempt.n == 2
    assert reached.next_attempt.retries_used == 1
    assert reached.next_attempt.prompt_mode == "artifact_retry"
    with pytest.raises(ValueError):
        worker.finalize(p, reached)


def test_boundary_fallback_swaps_the_lane(tmp_path, monkeypatch):
    def invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return _result(invocation, runner_name, code=1, stderr="You've hit your session limit")

    p = _prepared(
        tmp_path, monkeypatch, invoke,
        fallback=lambda _repo, _cur, kind, *, tried=(), **_kw: (
            daemon.runner.runner_profile("claude", _repo) if kind == "quota_exhausted" else None
        ),
    )
    reached = _to_boundary(p, Attempt(n=1, lane=p.lane))

    assert reached.kind == "fallback"
    assert reached.attempt.last_failure["failure_kind"] == "quota_exhausted"
    nxt = reached.next_attempt
    assert nxt is not None and nxt.n == 2
    assert nxt.lane.name == "claude" and nxt.lane is not p.lane
    assert nxt.prompt_mode == "fallback" and nxt.fallback_notice
    assert nxt.last_failure is None
    assert nxt.attempted_runners == ["codex"]


def test_boundary_exhausted_then_finalize_failed(tmp_path, monkeypatch):
    def invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return _result(invocation, runner_name, code=124, stderr="runner timed out after 3600s")

    p = _prepared(tmp_path, monkeypatch, invoke, max_retries=3)
    reached = _to_boundary(p, Attempt(n=1, lane=p.lane))

    assert reached.kind == "exhausted"
    assert reached.attempt.last_failure["timed_out"] is True
    ended = worker.finalize(p, reached)
    assert ended.stage == "failed" and ended.task.status == "error"


def test_boundary_hold_then_finalize_held(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)

    def invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return _result(
            invocation, runner_name, code=1,
            stderr="codex task_complete error (usage limit exceeded): out of quota",
            codex_task_error={"kind": "usage_limit_exceeded", "message": "out of quota"},
        )

    p = _prepared(tmp_path, monkeypatch, invoke, max_retries=3)
    reached = _to_boundary(p, Attempt(n=1, lane=p.lane))

    assert reached.kind == "hold"
    assert reached.hold_spec is not None
    ended = worker.finalize(p, reached)
    assert ended.stage == "held"
    assert ended.task.meta["resource_hold"]["released"] is False


def test_dispatch_refuses_a_stopped_run_as_a_boundary(tmp_path, monkeypatch):
    p = _prepared(tmp_path, monkeypatch)
    with daemon._run_controls_lock:
        daemon._run_controls.setdefault(p.eid, {})["stopped"] = True

    reached = worker.dispatch(p, Attempt(n=1, lane=p.lane))

    assert isinstance(reached, Boundary)
    assert reached.kind == "stopped"
    ended = worker.finalize(p, reached)
    assert isinstance(ended, Finalized) and ended.stage == "stopped"


# ── the seams themselves ─────────────────────────────────────────────


def test_shapes_are_frozen(tmp_path, monkeypatch):
    p = _prepared(tmp_path, monkeypatch)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.task = None  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.lane.name = "claude"  # type: ignore[misc]


def test_daemon_run_worker_is_a_call_into_worker_run(monkeypatch):
    seen = []

    def fake_run(*args, **kwargs):
        seen.append((args, kwargs))
        return "the run"

    monkeypatch.setattr(worker, "run", fake_run)

    out = daemon._run_worker(
        {"id": "evt"}, Path("/repo"), Path("/responses"), {"k": 1}, 2,
        account_context=None, inbox_dir=Path("/inbox"),
    )

    assert out == "the run"
    assert seen == [(
        ({"id": "evt"}, Path("/repo"), Path("/responses"), {"k": 1}, 2),
        {"account_context": None, "inbox_dir": Path("/inbox")},
    )]
