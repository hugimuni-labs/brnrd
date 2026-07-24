"""Tests for the daemon worker after the triage stage was removed."""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from brr import daemon, envs, presence, protocol, release_availability
from brr import schedule as schedule_mod
from brr.run import Run
from brr.runner import RunnerResult

from _helpers import (
    StubWorktreeEnv,
    commit_files,
    init_git_repo,
    make_event,
    succeed_invoke,
    write_repo_scaffold,
)


def _stub_env_isolated(monkeypatch, tmp_path):
    """Replace env backends with stand-ins that don't touch git/docker."""
    worktree_path = tmp_path / ".brr" / "worktrees" / "stub"
    worktree_path.mkdir(parents=True, exist_ok=True)
    finalized: list[str] = []

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                    outbox_path=None):
            return envs.RunContext(
                name=self.name,
                cwd=worktree_path,
                repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                outbox_host=outbox_path,
                outbox_env=outbox_path,
                branch_name=f"brr/{task.id}",
                env_state={"worktree_path": str(worktree_path)},
            )

        def invoke(self, ctx, runner_name, invocation, cfg=None, *, trace=False):
            raise NotImplementedError("override in test")

        def finalize(self, ctx, task, runs_dir):
            finalized.append(task.id)
            return task

    monkeypatch.setattr(envs, "get_env", lambda _name: StubEnv())
    return worktree_path, finalized


def test_merge_level_snapshots_forwards_enriched_quota_subfields():
    """Regression guard for #214/B2: `_merge_level_snapshots` forwards the
    whole `quota` value it's handed — it must not strip the new numeric
    `buckets` / `*_remaining_percent` sub-fields the collectors now attach,
    since `binding_quota_remaining_pct` reads them downstream of the merge.
    """
    usage_levels = {
        "source": "claude /usage PTY",
        "quota": {
            "summary": "week 15% left",
            "buckets": {"week": {"remaining_percentage": 15.0}},
        },
    }
    result_levels = {"source": "claude result json", "spend": {"summary": "$1.20"}}

    merged = daemon._merge_level_snapshots(usage_levels, result_levels)

    assert merged["quota"] == usage_levels["quota"]
    assert merged["quota"]["buckets"]["week"]["remaining_percentage"] == 15.0
    assert merged["spend"] == result_levels["spend"]


def _live_shape_561_levels():
    """The exact live shape from issue #561's dispatching run: session/week
    healthy, a near-exhausted per-model week bucket for a Core (Fable) the
    run never selects.
    """
    return {
        "quota": {
            "summary": "session 96% left; week 44% left; Fable week 4% left",
            "buckets": {
                "session": {"remaining_percentage": 96.0},
                "week": {"remaining_percentage": 44.0},
                "week_models": {"Fable": {"remaining_percentage": 4.0}},
            },
        }
    }


def test_quota_pacing_status_not_critical_for_live_shape_561():
    """#561: a run dispatched to a Core other than the thin week_models
    bucket's must not read `floor: critical` off that unrelated bucket —
    the account-wide session/week buckets (96%/44%) are what's live for it.
    """
    levels = _live_shape_561_levels()

    status = daemon._quota_pacing_status({}, levels, model="opus")

    assert status["binding_remaining_pct"] == 44.0
    assert status["floor"] is None
    assert status["excluded_thin"] == ["Fable"]


def test_quota_pacing_status_binds_when_model_matches_thin_bucket():
    """The same snapshot, for a run actually dispatched to the thin Core,
    still reads critical — exclusion is scoped to *other* Cores, not
    blanket immunity for week_models.
    """
    levels = _live_shape_561_levels()

    status = daemon._quota_pacing_status({}, levels, model="fable")

    assert status["binding_remaining_pct"] == 4.0
    assert status["floor"] == "critical"
    assert "excluded_thin" not in status


def test_quota_pacing_status_no_model_excludes_all_week_models():
    """A scheduling tick with no committed runner: per-model buckets never
    bind, but a thin one still surfaces informationally."""
    levels = _live_shape_561_levels()

    status = daemon._quota_pacing_status({}, levels)

    assert status["binding_remaining_pct"] == 44.0
    assert status["floor"] is None
    assert status["excluded_thin"] == ["Fable"]


def test_run_worker_constructs_task_without_triage(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-1")
    worktree_path, _finalized = _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"PROMPT {eid} {kw.get('run_id')} -> {rp}",
    )

    invocations: list[str] = []

    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        invocations.append(invocation.kind)
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("plain answer\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="plain answer\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert task.body == "raw event body"
    assert task.env == "worktree"
    assert task.meta["pid"] == os.getpid()
    # Happy path: the daemon-run invocation is the only runner call —
    # no separate triage stage, no retry. The labelled-kind check
    # captures both halves of that intent in one assertion.
    assert invocations == ["daemon-run"]
    persisted = Run.from_file(tmp_path / ".brr" / "runs" / task.id / "run.md")
    assert persisted is not None
    assert persisted.status == "done"
    assert persisted.meta["pid"] == os.getpid()
    response = (tmp_path / ".brr" / "responses" / "evt-1.md").read_text(encoding="utf-8")
    assert response == "plain answer\n"


def test_run_worker_installs_project_repo_run_id_hook(tmp_path, monkeypatch):
    """#575: a resident's own hand ``git commit`` inside a host run needs
    the same ``Brnrd-Run-Id`` stamping #565 gave the account-knowledge
    checkout — installed against ``repo_root`` (the checkout every worktree
    shares ``.git/hooks`` with), once per run, regardless of env backend."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-1")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        envs.get_env("worktree").__class__, "invoke", fake_invoke, raising=False,
    )

    hook_calls: list[Path] = []
    monkeypatch.setattr(
        daemon.gitops, "ensure_run_id_hook", lambda root: hook_calls.append(root),
    )

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert hook_calls == [tmp_path]


def test_run_worker_refuses_untrusted_when_solitary_unavailable(tmp_path, monkeypatch):
    """#517: an untrusted event with no isolated env to hold it is refused
    before any runner is prepared — fail closed, visibly."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-untrusted", source="github",
                       trust_tier="untrusted")

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")

    invoked: list[str] = []

    def fail_prepare(*_a, **_k):
        invoked.append("prepare")
        raise AssertionError("a refused run must never prepare an environment")

    monkeypatch.setattr(envs.WorktreeEnv, "prepare", fail_prepare, raising=False)
    monkeypatch.setattr(envs.SolitaryEnv, "prepare", fail_prepare, raising=False)

    # No docker.image in cfg → solitary can't back the run → refuse.
    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert invoked == []
    assert task.status == "done"
    assert task.meta["trust_tier"] == "untrusted"
    assert task.meta.get("trust_refused")
    assert task.meta.get("publish_status") == "refused"
    # The refusal is recorded on the event's response so the operator sees it.
    response = (tmp_path / ".brr" / "responses" / "evt-untrusted.md").read_text(encoding="utf-8")
    assert "untrusted" in response.lower()


def test_run_worker_applies_dashboard_wake_request_one_shot(tmp_path, monkeypatch):
    """#328 tap-to-request: a mirrored wake request overrides the runner for
    exactly one wake, is spent into the consumed ledger, and stamps the
    prompt's Runner line so the resident knows the body was asked for."""
    from brr import wake_request as wake_request_mod

    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-wake")
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"
    wake_request_mod.store_pending(
        brr_dir, {"request_id": "wake_9", "profile": "codex-mini"},
    )

    seen_overrides: list[dict | None] = []

    def fake_resolve(_root, overrides=None):
        seen_overrides.append(overrides)
        name = overrides["runner"] if overrides and overrides.get("runner") else "codex"
        return daemon.runner.runner_profile(name, _root)

    prompt_kwargs: dict = {}

    def fake_prompt(task, eid, rp, root, **kw):
        prompt_kwargs.update(kw)
        return f"PROMPT {eid}"

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", fake_resolve)
    monkeypatch.setattr(
        daemon.runner, "profile_metadata", lambda name, root=None: {"shell": "codex"},
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", fake_prompt)
    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        envs.get_env("worktree").__class__, "invoke", fake_invoke, raising=False,
    )

    task = daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    assert task.status == "done"
    assert seen_overrides and seen_overrides[0] == {"runner": "codex-mini"}
    # Spent: pending mirror gone, id parked for the publish-tick ack.
    assert wake_request_mod.pending(brr_dir) is None
    assert wake_request_mod.consumed_ids(brr_dir) == ["wake_9"]
    # The wake knows it was asked for.
    assert prompt_kwargs["runner_medium"] == (
        "codex-mini (requested from the dashboard spool rack)"
    )
    # #564: consumption leaves a trace — who spent it, from what source.
    receipt = wake_request_mod.last_receipt(brr_dir)
    assert receipt["at"]
    assert {k: v for k, v in receipt.items() if k != "at"} == {
        "request_id": "wake_9",
        "source": "telegram",
        "event_id": "evt-wake",
        "profile": "codex-mini",
    }


def test_run_worker_event_pin_outranks_wake_request(tmp_path, monkeypatch):
    """An event-level shell/core pin (respawn, quality escalation) is a
    deliberate per-run choice: the tap must neither override it nor be
    silently swallowed — it stays pending for the next unpinned wake."""
    from brr import wake_request as wake_request_mod

    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-pinned")
    event["core"] = "claude-opus-4-8"
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"
    wake_request_mod.store_pending(
        brr_dir, {"request_id": "wake_10", "profile": "codex-mini"},
    )

    seen_overrides: list[dict | None] = []

    def fake_resolve(_root, overrides=None):
        seen_overrides.append(overrides)
        return daemon.runner.runner_profile("claude-opus", _root)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", fake_resolve)
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **kw: "PROMPT",
    )
    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        envs.get_env("worktree").__class__, "invoke", fake_invoke, raising=False,
    )

    daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    assert seen_overrides and seen_overrides[0] == {"core": "claude-opus-4-8"}
    assert wake_request_mod.pending(brr_dir) == {
        "request_id": "wake_10",
        "profile": "codex-mini",
    }
    assert wake_request_mod.consumed_ids(brr_dir) == []


def test_run_worker_drops_wake_request_for_unknown_profile(tmp_path, monkeypatch):
    """#564: a tap naming a profile this daemon doesn't know (stale rack,
    another daemon's catalog) is dropped WITHOUT being spent — a drop never
    delivered what was asked for, so consuming it too would burn the tap for
    nothing. It stays pending for a daemon that knows the profile, or for
    the server to cancel."""
    from brr import wake_request as wake_request_mod

    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-unknown")
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"
    wake_request_mod.store_pending(
        brr_dir, {"request_id": "wake_11", "profile": "gemini-ultra-99"},
    )

    seen_overrides: list[dict | None] = []

    def fake_resolve(_root, overrides=None):
        seen_overrides.append(overrides)
        return daemon.runner.runner_profile("codex", _root)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", fake_resolve)
    monkeypatch.setattr(
        daemon.runner, "profile_metadata", lambda name, root=None: None,
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **kw: "PROMPT",
    )
    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        envs.get_env("worktree").__class__, "invoke", fake_invoke, raising=False,
    )

    daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    # No override applied, and the drop does NOT spend the tap.
    assert seen_overrides and seen_overrides[0] is None
    assert wake_request_mod.pending(brr_dir) == {
        "request_id": "wake_11",
        "profile": "gemini-ultra-99",
    }
    assert wake_request_mod.consumed_ids(brr_dir) == []
    assert wake_request_mod.last_receipt(brr_dir) is None


def test_run_worker_schedule_source_does_not_consume_wake_request(tmp_path, monkeypatch):
    """#564: a `source: schedule` wake (director tick, `every:`/`at:`
    firing — nobody watching) must not consume a dashboard tap parked for
    the interactive wake the maintainer was about to cause. The request
    stays pending, untouched, for that wake."""
    from brr import wake_request as wake_request_mod

    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-schedule", source="schedule")
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"
    wake_request_mod.store_pending(
        brr_dir, {"request_id": "wake_12", "profile": "codex-mini"},
    )

    seen_overrides: list[dict | None] = []

    def fake_resolve(_root, overrides=None):
        seen_overrides.append(overrides)
        return daemon.runner.runner_profile("codex", _root)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", fake_resolve)
    monkeypatch.setattr(
        daemon.runner, "profile_metadata", lambda name, root=None: {"shell": "codex"},
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **kw: "PROMPT",
    )

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        envs.get_env("worktree").__class__, "invoke", fake_invoke, raising=False,
    )

    daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    # Untouched: no override, nothing consumed, no receipt.
    assert seen_overrides and seen_overrides[0] is None
    assert wake_request_mod.pending(brr_dir) == {
        "request_id": "wake_12",
        "profile": "codex-mini",
    }
    assert wake_request_mod.consumed_ids(brr_dir) == []
    assert wake_request_mod.last_receipt(brr_dir) is None


def test_run_worker_wake_request_claimed_when_parked_shortly_before_event(
    tmp_path, monkeypatch,
):
    """#577: the mirror file can land *after* dispatch already read
    ``pending()`` once — the worker-level second read still claims it when
    the tap's server-stamped ``parked_at`` predates this event by only a
    couple of seconds, well inside the default claim window."""
    from datetime import datetime, timedelta, timezone

    from brr import wake_request as wake_request_mod

    write_repo_scaffold(tmp_path)
    now = datetime.now(timezone.utc)
    event = make_event(
        tmp_path, eid="evt-wake-close",
        created=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"
    parked = now - timedelta(seconds=2)
    wake_request_mod.store_pending(
        brr_dir,
        {
            "request_id": "wake_close", "profile": "codex-mini",
            "requested_at": parked.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    seen_overrides: list[dict | None] = []

    def fake_resolve(_root, overrides=None):
        seen_overrides.append(overrides)
        name = overrides["runner"] if overrides and overrides.get("runner") else "codex"
        return daemon.runner.runner_profile(name, _root)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", fake_resolve)
    monkeypatch.setattr(
        daemon.runner, "profile_metadata", lambda name, root=None: {"shell": "codex"},
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **kw: "PROMPT",
    )

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        envs.get_env("worktree").__class__, "invoke", fake_invoke, raising=False,
    )

    task = daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    assert seen_overrides and seen_overrides[0] == {"runner": "codex-mini"}
    assert wake_request_mod.pending(brr_dir) is None
    assert wake_request_mod.consumed_ids(brr_dir) == ["wake_close"]
    assert task.meta["wake_request"] == {
        "requested_profile": "codex-mini",
        "applied": True,
        "reason": None,
        "resolved_profile": "codex-mini",
    }


def test_run_worker_wake_request_lapses_when_parked_outside_claim_window(
    tmp_path, monkeypatch,
):
    """#577: a tap parked well before this event's window is not this
    event's tap — the wake dispatches on the default profile (visibly, via
    task.meta["wake_request"]) and the tap lapses rather than staying armed
    for whatever unrelated wake reads next."""
    from datetime import datetime, timedelta, timezone

    from brr import wake_request as wake_request_mod

    write_repo_scaffold(tmp_path)
    now = datetime.now(timezone.utc)
    event = make_event(
        tmp_path, eid="evt-wake-stale",
        created=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"
    parked = now - timedelta(minutes=10)
    wake_request_mod.store_pending(
        brr_dir,
        {
            "request_id": "wake_stale_evt", "profile": "codex-mini",
            "requested_at": parked.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    seen_overrides: list[dict | None] = []

    def fake_resolve(_root, overrides=None):
        seen_overrides.append(overrides)
        return daemon.runner.runner_profile("codex", _root)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", fake_resolve)
    monkeypatch.setattr(
        daemon.runner, "profile_metadata", lambda name, root=None: {"shell": "codex"},
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **kw: "PROMPT",
    )

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        envs.get_env("worktree").__class__, "invoke", fake_invoke, raising=False,
    )

    task = daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    # Not applied: the daemon dispatched on the default resolve, untouched.
    assert seen_overrides and seen_overrides[0] is None
    # Lapsed, not left pending — the next unrelated wake can't inherit it.
    assert wake_request_mod.pending(brr_dir) is None
    assert wake_request_mod.consumed_ids(brr_dir) == ["wake_stale_evt"]
    receipt = wake_request_mod.last_receipt(brr_dir)
    assert receipt["outcome"] == "lapsed"
    assert receipt["profile"] is None
    # #577: visible on the run — "you asked for X, you got Y, because Z".
    report = task.meta["wake_request"]
    assert report["requested_profile"] == "codex-mini"
    assert report["resolved_profile"] == "codex"
    assert report["applied"] is False
    assert "claim window" in report["reason"]


def test_run_worker_finalize_appends_run_ledger_row(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-ledger")
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(
        envs,
        "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=succeed_invoke("ledger done\n")),
    )
    snapshots = iter([
        {
            "quota": {
                "primary_used_percent": 10.0,
                "secondary_used_percent": 20.0,
            },
        },
        {
            "quota": {
                "primary_used_percent": 12.0,
                "secondary_used_percent": 25.0,
            },
        },
    ])
    monkeypatch.setattr(
        daemon.run_ledger,
        "load_quota_levels",
        lambda *args, **kwargs: next(snapshots),
    )

    task = daemon._run_worker_and_finalize(
        event,
        tmp_path,
        tmp_path / ".brr" / "responses",
        {"run_ledger.subscription_price.codex": 20},
        0,
    )

    ledger = tmp_path / ".brr" / "run-ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["run_id"] == task.id
    assert rows[0]["event_id"] == "evt-ledger"
    assert rows[0]["weekly_pct_delta"] == 5.0
    assert rows[0]["five_hour_pct_delta"] == 2.0
    assert rows[0]["usd_subscription_attributed"] == 1.0
    assert rows[0]["estimate_vs_actual"] == "actual"
    assert task.terminal_reply == "ledger done"
    assert not (tmp_path / ".brr" / "outbox" / "evt-ledger").exists()


def test_capture_knowledge_no_longer_archives_replies(tmp_path, monkeypatch):
    """Terminal traffic belongs to home/runs, never the knowledge repo."""
    task = Run(
        id="run-reply-race",
        event_id="evt-reply-race",
        body="answer",
        status="done",
        meta={"repo_label": "Gurio/brr"},
    )
    responses = tmp_path / ".brr" / "responses"
    outbox = tmp_path / ".brr" / "outbox" / task.event_id
    outbox.mkdir(parents=True)
    monkeypatch.setattr(
        daemon.knowledge,
        "archive_reply",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("legacy archive used")),
    )
    monkeypatch.setattr(daemon.knowledge, "capture", lambda *_a, **_k: True)

    terminal_reply = "---\ngate: forge\n---\n\n" + "x" * 130 + "\nsecond line"
    daemon._capture_knowledge(
        tmp_path,
        {},
        task,
        event={"id": task.event_id, "source": "telegram"},
        responses_dir=responses,
        outbox_dir=outbox,
        terminal_reply=terminal_reply,
    )

    assert daemon.relics.read_reported(outbox) == []
    assert "reply_archive" not in task.meta


def test_capture_knowledge_auto_reports_changed_kb_pages_once(tmp_path, monkeypatch):
    task = Run(id="run-kb-relic", event_id="evt-kb-relic", body="answer")
    outbox = tmp_path / ".brr" / "outbox" / task.event_id
    outbox.mkdir(parents=True)
    daemon.relics.append(outbox, "kb_page", path="kb/already.md")

    def fake_capture(*_args, captured_pages, **_kwargs):
        captured_pages.extend(["already.md", "new.md"])
        return True

    monkeypatch.setattr(daemon.knowledge, "capture", fake_capture)
    monkeypatch.setattr(
        daemon.knowledge, "kb_page_url",
        lambda _root, page, _cfg: f"https://example.test/{page}",
    )

    daemon._capture_knowledge(tmp_path, {}, task, outbox_dir=outbox)

    assert daemon.relics.read_reported(outbox) == [
        {"kind": "kb", "path": "kb/already.md"},
        {"kind": "kb", "path": "new.md", "url": "https://example.test/new.md"},
    ]


def test_capture_knowledge_derives_relics_from_commit_window_and_dedupes(
    tmp_path, monkeypatch,
):
    """#538: pages committed mid-run surface via the run-start OID window,
    unioned with the dirty-diff manifest and deduped against both it and
    resident self-reports — no page appears twice."""
    task = Run(
        id="run-kb-window",
        event_id="evt-kb-window",
        body="answer",
        meta={"kb_start_oid": "a" * 40},
    )
    outbox = tmp_path / ".brr" / "outbox" / task.event_id
    outbox.mkdir(parents=True)
    daemon.relics.append(outbox, "kb", path="kb/self-reported.md")

    def fake_capture(*_args, captured_pages, **_kwargs):
        captured_pages.append("dirty.md")
        return True

    window_calls: list[tuple[str | None, str | None]] = []

    def fake_window(_root, start_oid, *, cfg=None, run_id=None):
        window_calls.append((start_oid, run_id))
        return ["dirty.md", "windowed.md", "self-reported.md"]

    monkeypatch.setattr(daemon.knowledge, "capture", fake_capture)
    monkeypatch.setattr(
        daemon.knowledge, "committed_pages_in_window", fake_window,
    )
    monkeypatch.setattr(
        daemon.knowledge, "kb_page_url", lambda _root, _page, _cfg: None,
    )

    daemon._capture_knowledge(tmp_path, {}, task, outbox_dir=outbox)

    # The window is now filtered by *this run's* identity (#565), not just
    # a bare time range — the caller must pass its own run id through.
    assert window_calls == [("a" * 40, "run-kb-window")]
    assert daemon.relics.read_reported(outbox) == [
        {"kind": "kb", "path": "kb/self-reported.md"},
        {"kind": "kb", "path": "dirty.md"},
        {"kind": "kb", "path": "windowed.md"},
    ]


def test_capture_knowledge_stopped_run_suppresses_shared_window_sweep(
    tmp_path, monkeypatch,
):
    """#575 — the other half of #565: a stopped host run must not sweep the
    shared account-knowledge checkout. That sweep can both commit a live
    sibling's dirty edits under the stopped run's identity and credit a
    sibling's already-committed pages to this run's dashboard node. The
    owning run's own capture net (which runs this exact path when *it*
    finishes) still gets credit — nothing here is a permanent loss, only
    deferred to whoever is actually still working."""
    task = Run(
        id="run-stopped-sweep",
        event_id="evt-stopped-sweep",
        body="answer",
        status="stopped",
        meta={"kb_start_oid": "a" * 40},
    )
    outbox = tmp_path / ".brr" / "outbox" / task.event_id
    outbox.mkdir(parents=True)

    monkeypatch.setattr(
        daemon.knowledge, "capture",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("stopped run must not sweep the shared checkout"),
        ),
    )
    monkeypatch.setattr(
        daemon.knowledge, "committed_pages_in_window",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("stopped run must not read the shared commit window"),
        ),
    )

    daemon._capture_knowledge(tmp_path, {}, task, outbox_dir=outbox)

    assert daemon.relics.read_reported(outbox) == []


def test_run_worker_crash_retires_event_instead_of_infinite_retry_loop(
    tmp_path, monkeypatch,
):
    """A crash inside ``_run_worker`` must not orphan the event as "processing".

    Found live 2026-07-06: an uncaught exception left ``task`` unset, so
    nothing ever advanced the event's status past "processing" —
    ``list_dispatchable`` treats "processing" as still-eligible (that's how
    a daemon restart resumes a run in flight), so the very next main-loop
    tick re-dispatched the *same* event, crashed again, and repeated with
    no backoff: a live incident produced 26+ runs in ~50 minutes, one fresh
    run-id per attempt, before manual intervention. The event must come out
    of "processing" limbo (here: "error") so it stops being immediately
    re-dispatchable, regardless of what actually crashed.
    """
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-crash")
    protocol.set_status(event, "processing")  # matches real dispatch (daemon.py:4437)
    monkeypatch.setattr(
        daemon,
        "_run_worker",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        daemon._run_worker_and_finalize(
            event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
        )

    assert event["status"] == "error"
    reread = protocol._read_event(tmp_path / ".brr" / "inbox" / "evt-crash.md")
    assert reread["status"] == "error"
    assert reread["status"] not in ("pending", "processing")


def test_run_worker_does_not_infer_native_hooks_from_runner_name(
    tmp_path, monkeypatch
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-no-hooks")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(
        daemon.runner,
        "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner_select.implicit_runner("claude"),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner,
        "profile_hooks_flavour",
        lambda _runner_name, _repo_root=None: None,
    )
    monkeypatch.setattr(
        daemon.hooks_mod,
        "hook_capability",
        lambda *_args, **_kwargs: pytest.fail(
            "hook capability should only be checked for declared hooks"
        ),
    )
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *args, **kwargs: "PROMPT"
    )
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"


def test_run_worker_installs_native_hooks_only_when_profile_declares_them(
    tmp_path, monkeypatch
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-declared-hooks")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(
        daemon.runner,
        "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner_select.runner_from_profile(
            "custom", {"shell": "custom", "hooks": "claude"},
        ),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner,
        "profile_hooks_flavour",
        lambda _runner_name, _repo_root=None: "claude",
    )
    checked: list[str] = []
    installed: list[str] = []

    def fake_capability(flavour, _cwd):
        checked.append(flavour)
        return True

    def fake_install(flavour, cwd):
        installed.append(flavour)
        return cwd / ".claude" / "settings.local.json"

    monkeypatch.setattr(daemon.hooks_mod, "hook_capability", fake_capability)
    monkeypatch.setattr(daemon.hooks_mod, "install_hook_config", fake_install)
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *args, **kwargs: "PROMPT"
    )
    base_env = envs.get_env("worktree")
    seen_env: dict[str, str] = {}

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        seen_env.update(invocation.env)
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    assert checked == ["claude"]
    assert installed == ["claude"]
    assert seen_env["BRR_RUNNER"] == "claude"


def test_run_worker_threads_runner_quota_into_prompt(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-quota")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner_quota,
        "describe_runner_quota",
        lambda runner_name, _cfg, _brr_dir: (
            "weekly 0% - resets 2026-06-17T01:29Z"
            if runner_name == "codex"
            else None
        ),
    )
    # Pin the config-derived fallback path hermetically: without this the
    # codex level collector reads the *host's* live session rollout and
    # overrides the stubbed summary (level quota wins by design; see
    # test_run_worker_threads_level_quota_into_prompt for that path).
    monkeypatch.setattr(daemon, "_collect_levels", lambda *a, **kw: (None, False))
    captured: dict[str, object] = {}

    def _prompt(_task, _eid, _rp, _root, **kw):
        captured.update(kw)
        return "PROMPT"

    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", _prompt)
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    assert captured["runner_medium"] == "codex"
    assert captured["runner_quota"] == "weekly 0% - resets 2026-06-17T01:29Z"


def test_run_worker_marks_error_on_env_setup_failure(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-2")

    class ExplodingEnv:
        name = "worktree"

        def prepare(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def invoke(self, *_args, **_kwargs):  # pragma: no cover - never reached
            raise AssertionError("invoke should not run")

        def finalize(self, *_args, **_kwargs):  # pragma: no cover - never reached
            return None

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: ExplodingEnv())

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "error"
    assert event["status"] == "done"
    response = protocol.read_response(tmp_path / ".brr" / "responses", "evt-2")
    assert response is not None
    assert "environment setup failed: boom" in response
    persisted = Run.from_file(tmp_path / ".brr" / "runs" / task.id / "run.md")
    assert persisted is not None
    assert persisted.status == "error"


def test_presence_label_for_event_never_falls_back_to_task_body():
    """#585: a presence `label` is dashboard chrome, not a content channel.

    The pre-fix shape derived the label as
    ``event.get("summary") or task.body``, so a spawn worker's presence
    label was its own full task spec whenever `summary` was unpopulated —
    which it always was (audited: no writer sets it on this creation-time
    `event` dict). `_presence_label_for_event` must never read `body`
    (or any task-prose field) at all — an absent `summary` degrades to an
    empty label, not to the event's body."""
    huge_task_prose = (
        "# Task — issue #565: stopped runs are credited even though the "
        "branch never merged, which double-counts them in the ledger. " * 5
    )
    event = {"id": "evt-1", "body": huge_task_prose}
    assert daemon._presence_label_for_event(event) == ""


def test_presence_label_for_event_uses_summary_when_a_caller_sets_it():
    """A caller that *does* populate a short, deliberate `summary` still
    gets it through, truncated to 120 chars — the field stays usable for
    a future handle-shaped writer, just never task prose by default."""
    event = {"id": "evt-1", "body": "irrelevant", "summary": "Add labels to live runs"}
    assert daemon._presence_label_for_event(event) == "Add labels to live runs"

    event_long = {"id": "evt-2", "summary": "x" * 200}
    assert len(daemon._presence_label_for_event(event_long)) == 120


def test_presence_registered_during_run_and_cleared_after(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(
        tmp_path, eid="evt-p1", summary="Add labels to live runs",
    )
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **k: "PROMPT",
    )
    # _run_worker_and_finalize calls publish at the end; stub it so the test
    # exercises the presence finally without real git pushes.
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)

    brr_dir = tmp_path / ".brr"
    seen: dict[str, object] = {}
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        # Mid-run: this thought is recorded as present on its stream, so a
        # concurrent session would see it and could avoid colliding.
        active = presence.list_active(brr_dir)
        seen["during"] = [(e["kind"], e["run_id"], e["label"]) for e in active]
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker_and_finalize(
        event, tmp_path, brr_dir / "responses", {}, 0,
    )

    assert seen["during"] == [("daemon", task.id, "Add labels to live runs")]
    # The thought is no longer awake → its presence entry is gone.
    assert presence.list_active(brr_dir) == []


def test_run_worker_does_not_retry_on_empty_stdout(tmp_path, monkeypatch):
    """Ceremony cut 2026-07-16: empty stdout alone no longer triggers a
    full re-run — a clean silent run with no other success signal takes
    the give-up path in one attempt and surfaces a terminal failure note."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-3")
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    attempts: list[str] = []

    class RetryEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                    outbox_path=None):
            return envs.RunContext(
                name=self.name, cwd=tmp_path, repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                outbox_host=outbox_path,
                outbox_env=outbox_path,
                branch_name=f"brr/{task.id}",
                env_state={"worktree_path": str(tmp_path)},
            )

        def invoke(self, ctx, runner_name, invocation, cfg, *, trace=False):
            attempts.append(invocation.label)
            stdout = "" if invocation.label.endswith("attempt-1") else "fixed reply\n"
            if stdout:
                Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
                Path(invocation.response_path).write_text(stdout, encoding="utf-8")
            return RunnerResult(
                invocation=invocation,
                runner_name=runner_name,
                command=["mock"],
                stdout=stdout,
                stderr="",
                returncode=0,
                trace_dir=None,
                artifacts=[],
            )

        def finalize(self, _ctx, task, _tasks_dir):
            return task

    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: RetryEnv())

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 1)

    assert task.status == "error"
    assert attempts == ["evt-3-attempt-1"]
    # The addressed event still gets a visible terminal note.
    assert task.terminal_reply


def test_run_worker_accepts_current_outbox_reply_without_stdout(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-outbox-only")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, ctx, runner_name, invocation, cfg=None, *, trace=False):
        assert ctx.outbox_host is not None
        ctx.outbox_host.mkdir(parents=True, exist_ok=True)
        (ctx.outbox_host / "reply.md").write_text(
            "handled through outbox\n", encoding="utf-8",
        )
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 1,
    )

    assert task.status == "done"
    assert event["status"] == "done"
    responses = tmp_path / ".brr" / "responses"
    assert protocol.read_response(responses, "evt-outbox-only") is None
    assert [
        protocol.read_partial(p)
        for p in protocol.list_partials(responses, "evt-outbox-only")
    ] == ["handled through outbox"]


def test_drain_outbox_queues_respawn_request(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(
        inbox,
        "telegram",
        "original task",
        status="processing",
        conversation_key="telegram:42:",
        chat_id="42",
        origin_message_key="telegram:42::99",
    )
    event_id = path.stem
    (outbox / "respawn.md").write_text(
        "---\n"
        "respawn: true\n"
        "shell: codex-mini\n"
        "repo: Gurio/other\n"
        "reason: needs a stronger core\n"
        "defer_until: +30m\n"
        "---\n"
        "carry this exact task forward\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-dispatch",
        event_id=event_id,
        body="original task",
        source="telegram",
        conversation_key="telegram:42:",
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        stats=stats,
    )

    assert promoted == 1
    assert stats == {"respawn": 1}
    spawned = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("respawned_from_event") == event_id
    ][0]
    assert spawned["source"] == "telegram"
    assert spawned["conversation_key"] == "telegram:42:"
    assert spawned["chat_id"] == 42
    assert spawned["shell"] == "codex-mini"
    assert spawned["repo"] == "Gurio/other"
    assert spawned["repo_label"] == "Gurio/other"
    assert spawned["respawn_reason"] == "needs a stronger core"
    assert spawned["body"] == "carry this exact task forward"
    assert "origin_message_key" not in spawned
    assert protocol.event_is_deferred(spawned)


def test_pending_events_for_agent_excludes_own_respawn(tmp_path):
    """A respawn this run just queued must not show up as attention-owed.

    Found live (2026-07-06): a run that queued a codex-shell respawn for a
    bounded worker task kept re-triggering the Stop-hook fold-in-or-explain
    gate every phase after, because the queued event was indistinguishable
    from an unaddressed user message in ``_pending_events_for_agent`` —
    ``pending_event_count`` could never reach zero from inside the very run
    that created it, since dispatching it as a new run requires this run to
    end first. Respawn-origin events are a system-to-system handoff, not a
    follow-up any resident-wake can fold in, so they're excluded here.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    current = protocol.create_event(
        inbox, "telegram", "current task", status="processing",
    )
    current_id = current.stem
    real_followup = protocol.create_event(
        inbox, "telegram", "a genuine user follow-up",
    )
    protocol.create_event(
        inbox, "telegram", "queued worker task",
        respawned_by_run="run-current", respawned_from_event=current_id,
        shell="codex",
    )

    events = daemon._pending_events_for_agent(inbox, current_id)

    assert [ev["id"] for ev in events] == [real_followup.stem]


def test_run_worker_does_not_dedupe_its_own_respawn(tmp_path, monkeypatch):
    """A respawn event must never be flagged as a duplicate of its parent.

    Found live (2026-07-06): ``_queue_respawn_request`` carries the
    parent's ``telegram_chat_id``/``telegram_topic_id``/
    ``telegram_message_id`` forward so the respawn's eventual reply lands
    in the same chat thread — but those are exactly the fields
    ``origin_message_key_for_event`` hashes into the exact-duplicate key.
    The respawn event recomputed to the *same* key as the message that
    triggered the run which queued it, so the moment it started, the
    "arrived via two channels" check in ``_run_worker`` matched it
    against its own parent and silently squashed it with "I already
    received this source message on another configured channel" instead
    of actually running.
    """
    write_repo_scaffold(tmp_path)
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"

    telegram_ids = dict(
        telegram_chat_id=155783668,
        telegram_topic_id="",
        telegram_message_id=42,
    )
    parent_event = make_event(
        tmp_path, eid="evt-parent", conversation_key="telegram:155783668:",
        **telegram_ids,
    )
    # Seed the conversation log as if the parent event already ran —
    # this is what records the origin_message_key a later duplicate
    # check matches against.
    from brr import conversations
    conversations.append_event(brr_dir, "telegram:155783668:", parent_event)

    respawn_event = make_event(
        tmp_path, eid="evt-respawn", conversation_key="telegram:155783668:",
        respawned_by_run="run-parent", respawned_from_event="evt-parent",
        **telegram_ids,
    )

    worktree_path, _finalized = _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"PROMPT {eid}",
    )

    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("real respawn answer\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="real respawn answer\n", stderr="", returncode=0,
            trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(respawn_event, tmp_path, responses, {}, 0)

    assert task.status == "done"
    assert "deduplicated_by_event_id" not in task.meta
    response = (responses / "evt-respawn.md").read_text(encoding="utf-8")
    assert response == "real respawn answer\n"


def test_drain_outbox_queues_worker_respawn_request(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(
        inbox,
        "telegram",
        "original task",
        status="processing",
        conversation_key="telegram:42:",
        chat_id="42",
    )
    event_id = path.stem
    (outbox / "respawn.md").write_text(
        "---\n"
        "respawn: true\n"
        "worker: true\n"
        "shell: codex-mini\n"
        "---\n"
        "bounded task for a worker wake\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-dispatch",
        event_id=event_id,
        body="original task",
        source="telegram",
        conversation_key="telegram:42:",
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        stats=stats,
    )

    assert promoted == 1
    spawned = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("respawned_from_event") == event_id
    ][0]
    assert spawned["worker"] is True


def test_drain_outbox_bare_respawn_omits_worker_key(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(
        inbox,
        "telegram",
        "original task",
        status="processing",
        conversation_key="telegram:42:",
        chat_id="42",
    )
    event_id = path.stem
    (outbox / "respawn.md").write_text(
        "---\n"
        "respawn: true\n"
        "shell: codex-mini\n"
        "---\n"
        "carry this exact task forward\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-dispatch",
        event_id=event_id,
        body="original task",
        source="telegram",
        conversation_key="telegram:42:",
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        stats=stats,
    )

    assert promoted == 1
    spawned = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("respawned_from_event") == event_id
    ][0]
    assert "worker" not in spawned


def test_drain_outbox_quality_respawn_resolves_local_escalation(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(
        inbox,
        "telegram",
        "original task",
        status="processing",
        conversation_key="telegram:42:",
        chat_id="42",
    )
    event_id = path.stem
    monkeypatch.setattr(
        daemon.runner,
        "quality_escalation_runner",
        lambda _repo, current, *, target_class=None, tried=(): (
            "claude-opus"
            if current == "codex-mini" and target_class == "strong"
            else None
        ),
    )
    (outbox / "respawn.md").write_text(
        "---\n"
        "respawn: true\n"
        "quality: escalate\n"
        "reason: needs a stronger core\n"
        "---\n"
        "carry this exact task forward\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-dispatch",
        event_id=event_id,
        body="original task",
        source="telegram",
        conversation_key="telegram:42:",
        meta={"runner_name": "codex-mini"},
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        repo_root=tmp_path,
        stats=stats,
    )

    assert promoted == 1
    assert stats == {"respawn": 1}
    spawned = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("respawned_from_event") == event_id
    ][0]
    assert spawned["shell"] == "claude-opus"
    assert spawned["respawn_quality"] == "strong"
    assert spawned["respawn_reason"] == "needs a stronger core"


def test_drain_outbox_queues_spawn_request(tmp_path):
    """``spawn:`` frontmatter queues a cap-1 concurrent worker-stack child.

    kb/design-director-loop.md §"Concurrent sub-spawns", slice 1: unlike
    ``respawn:`` (queued for after this run ends), a spawn is meant for the
    daemon's second dispatch slot — this test only covers the *queueing*
    shape (worker forced, parent linkage, exclusion-reuse); the main-loop
    concurrent-dispatch wiring itself has no automated end-to-end test
    (consistent with the rest of ``start()``'s dispatch loop, which isn't
    unit-tested at that level either).
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(
        inbox,
        "telegram",
        "original task",
        status="processing",
        conversation_key="telegram:42:",
    )
    event_id = path.stem
    (outbox / "spawn.md").write_text(
        "---\n"
        "spawn: true\n"
        "shell: codex-mini\n"
        "reason: cheaper core has quota headroom\n"
        "---\n"
        "bounded task for a concurrent worker child\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-parent",
        event_id=event_id,
        body="original task",
        source="telegram",
        conversation_key="telegram:42:",
        meta={"repo_label": "Gurio/brr"},
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        stats=stats,
    )

    assert promoted == 1
    assert stats == {"spawn": 1}
    spawned = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("spawn_parent_run_id") == "run-parent"
    ][0]
    assert spawned["worker"] is True
    assert spawned["spawn_immediate"] is True
    # Forced regardless of the repo's own `environment=` config — a
    # spawn shares the daemon process with its still-running parent, so
    # it is the one dispatch path that needs its own isolated cwd even
    # when the repo otherwise runs `environment=host` (see the
    # 2026-07-07 run-260707-1321-auhp collision in
    # kb/design-director-loop.md).
    assert spawned["environment"] == "worktree"
    assert spawned["shell"] == "codex-mini"
    assert spawned["repo_label"] == "Gurio/brr"
    # Reuses the respawn-origin exclusion so the parent's own attention
    # gate doesn't nag it about a dispatch it just made on purpose.
    assert spawned["respawned_from_event"] == event_id
    assert spawned["respawned_by_run"] == "run-parent"
    # A `reset_on: spawn` schedule entry (e.g. the director tick) reads
    # this signal back on its next tick to push its own cooldown out,
    # rather than firing redundantly right after this concurrent dispatch.
    assert schedule_mod.load_signals(brr_dir).get("spawn") is not None


def test_drain_outbox_spawn_env_optdown_and_host_refusal(tmp_path):
    """`environment:` in spawn frontmatter may opt down, never up (#515).

    `solitary`/`docker` are WorktreeEnv subclasses — the child keeps its
    own worktree, so the 2026-07-07 cwd-collision guard holds while
    isolation only increases. `host` (or anything unknown) is refused
    with a notice rather than silently rewritten to the worktree floor.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "task", status="processing")
    event_id = path.stem
    task = Run(
        id="run-parent", event_id=event_id, body="task", source="telegram",
    )

    (outbox / "spawn-solitary.md").write_text(
        "---\nspawn: true\nshell: claude\nenvironment: solitary\n---\n"
        "isolated probe child\n",
        encoding="utf-8",
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, event_id),
        task, responses, event_id, outbox, inbox,
    )
    assert promoted == 1
    spawned = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("spawn_parent_run_id") == "run-parent"
    ]
    assert len(spawned) == 1
    assert spawned[0]["environment"] == "solitary"
    assert spawned[0]["worker"] is True

    # `host` — an opt *up* — is refused, leaves a notice, queues nothing.
    (outbox / "spawn-host.md").write_text(
        "---\nspawn: true\nshell: claude\nenvironment: host\n---\n"
        "child asking to share the parent's cwd\n",
        encoding="utf-8",
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, event_id),
        task, responses, event_id, outbox, inbox,
    )
    assert promoted == 0
    assert len([
        ev for ev in protocol.list_pending(inbox)
        if ev.get("spawn_parent_run_id") == "run-parent"
    ]) == 1  # still only the solitary one
    notices = daemon._read_outbox_notices(outbox)
    assert any("not spawnable" in str(n.get("text", "")) for n in notices)


def test_drain_outbox_spawn_refuses_nested_from_worker_run(tmp_path):
    """A worker-stack run must not itself spawn a further child (no nesting)."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original task", status="processing")
    event_id = path.stem
    (outbox / "spawn.md").write_text(
        "---\nspawn: true\nshell: codex-mini\n---\nnested child\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-worker-child", event_id=event_id, body="original task",
        source="telegram", meta={"worker": True},
    )

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, event_id),
        task, responses, event_id, outbox, inbox,
    )

    assert promoted == 0
    assert [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("spawn_parent_run_id")
    ] == []


def test_notify_spawn_parent_lands_pending_event_for_still_running_parent(
    tmp_path,
):
    """Completion notify is a normal pending event the parent can fold in.

    Distinct from the spawn-dispatch event itself: this one is *not*
    tagged respawned_from_event/respawned_by_run, so _pending_events_for_agent
    surfaces it as real attention-owed follow-up.
    """
    inbox = tmp_path / ".brr" / "inbox"
    response_path = tmp_path / "response.md"
    response_path.write_text("child's answer\n", encoding="utf-8")
    task = Run(
        id="run-child",
        event_id="evt-child",
        body="",
        source="telegram",
        status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "response_path": str(response_path),
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    pending = protocol.list_pending(inbox)
    assert len(pending) == 1
    note = pending[0]
    assert note["conversation_key"] == "telegram:42:"
    assert note["spawned_by_run"] == "run-child"
    assert "respawned_from_event" not in note
    assert "child's answer" in note["body"]
    # Not excluded from the parent's own attention gate.
    assert daemon._pending_events_for_agent(inbox, "some-other-event")


def test_notify_spawn_parent_noop_without_parent_linkage(tmp_path):
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(id="run-solo", event_id="evt-solo", body="", source="telegram")

    daemon._notify_spawn_parent(inbox, task)

    assert protocol.list_pending(inbox) == []


def test_notify_spawn_parent_of_crash_lands_pending_event(tmp_path):
    """A spawn that crashes before returning a Run must still notify its
    parent — not just a clean finish.

    Bug found live 2026-07-07: the main loop's reap step only called
    ``_notify_spawn_parent`` in the success branch of
    ``current_spawn.result()``; a worker future that raised (a runner
    launch failure, an unhandled exception) left the parent with no signal
    the spawn ever existed, contradicting the "completion always lands
    back" design. This exercises the crash-path notifier built straight
    from the raw inbox event dict (a crashed worker never produces the
    richer ``Run`` object the clean-finish path reads from).
    """
    inbox = tmp_path / ".brr" / "inbox"
    event = {
        "id": "evt-child",
        "spawn_parent_run_id": "run-parent",
        "spawn_parent_conversation_key": "telegram:42:",
    }

    daemon._notify_spawn_parent_of_crash(inbox, event, RuntimeError("boom"))

    pending = protocol.list_pending(inbox)
    assert len(pending) == 1
    note = pending[0]
    assert note["conversation_key"] == "telegram:42:"
    assert note["spawn_parent_run_id"] == "run-parent"
    assert note["spawn_failed"] is True
    assert "evt-child" in note["body"]
    assert "boom" in note["body"]
    # Not excluded from the parent's own attention gate, same as a clean finish.
    assert daemon._pending_events_for_agent(inbox, "some-other-event")


def test_notify_spawn_parent_of_crash_noop_without_parent_linkage(tmp_path):
    inbox = tmp_path / ".brr" / "inbox"
    event = {"id": "evt-solo"}

    daemon._notify_spawn_parent_of_crash(inbox, event, RuntimeError("boom"))

    assert protocol.list_pending(inbox) == []


# ── #574: spawn contract check (spec vs what the child actually published) ──


def test_extract_spawn_contract_finds_branch_and_report():
    spec = (
        "# Task: issue #574\n"
        "**Branch: `brr/spawn-contract-check`**\n"
        "**Report: `/tmp/brr-spawn-contract-check-report.md`**\n"
    )
    branch, report = daemon._extract_spawn_contract(spec)
    assert branch == "brr/spawn-contract-check"
    assert report == "/tmp/brr-spawn-contract-check-report.md"


def test_extract_spawn_contract_ignores_source_paths_masquerading_as_branch():
    """A spec's own code anchors (``src/brr/daemon.py``) must never be read
    as the branch commitment — this repo's own spec bodies routinely cite
    both in the same message, anchors first."""
    spec = (
        "Anchors: `src/brr/daemon.py::_queue_spawn_request` (~4906).\n"
        "**Branch: `brr/real-slug`**\n"
    )
    branch, _report = daemon._extract_spawn_contract(spec)
    assert branch == "brr/real-slug"


def test_extract_spawn_contract_ignores_dot_brr_runtime_paths():
    """`.brr/worktrees/<run-id>` is named in the working rules of every
    host-environment spawn spec brnrd writes — and it is a `brr/` token
    reached via a `.`, not a `/`. The first cut of this check extracted
    `brr/worktrees` from it and would have flagged a compliant worker.
    Ordering saved the two live dispatches on 2026-07-23; ordering is not
    a guard."""
    spec = (
        "Work ONLY under `/home/gurio/src/misc/brr/.brr/worktrees/<run-id>`;\n"
        "re-read `.brr/outbox/evt-x/portal-state.json` at plan boundaries.\n"
        "**Branch: `brr/real-slug`**\n"
    )
    branch, _report = daemon._extract_spawn_contract(spec)
    assert branch == "brr/real-slug"


def test_extract_spawn_contract_no_tokens_returns_none_none():
    branch, report = daemon._extract_spawn_contract("just do the thing, no branch here")
    assert branch is None
    assert report is None


def test_spawn_contract_check_no_branch_in_spec_is_no_contract():
    assert daemon._spawn_contract_check("do the thing", "brr/whatever") is None


def test_spawn_contract_check_match_is_no_mismatch(tmp_path):
    # The report-path token convention is literally `/tmp/brr-*.md` (every
    # dispatch spec uses it) — tmp_path itself lives under a nested
    # `/tmp/pytest-.../` prefix, so the fixture path is built directly
    # under `/tmp/` here to actually exercise the regex, not just the
    # branch-only fallback.
    report = Path(f"/tmp/brr-contract-check-match-{tmp_path.name}-report.md")
    report.write_text("done\n", encoding="utf-8")
    try:
        spec = f"Branch: `brr/thing`\nReport: `{report}`\n"
        result = daemon._spawn_contract_check(spec, "brr/thing")
        assert result["mismatch"] is False
        assert result["spec_branch"] == "brr/thing"
        assert result["published_branch"] == "brr/thing"
        assert result["report_found"] is True
    finally:
        report.unlink(missing_ok=True)


def test_spawn_contract_check_branch_mismatch():
    spec = "Branch: `brr/wake-request-source-gate`\n"
    result = daemon._spawn_contract_check(spec, "brr/stopped-run-kb-credit")
    assert result["mismatch"] is True
    assert result["spec_branch"] == "brr/wake-request-source-gate"
    assert result["published_branch"] == "brr/stopped-run-kb-credit"


def test_spawn_contract_check_no_branch_published_is_mismatch():
    """A spec that names a branch but the child never published anything at
    all is exactly the silent-substitution shape #574 exists to catch."""
    spec = "Branch: `brr/thing`\n"
    result = daemon._spawn_contract_check(spec, None)
    assert result["mismatch"] is True
    assert result["published_branch"] is None


def test_spawn_contract_check_missing_report_is_mismatch(tmp_path):
    missing = Path(f"/tmp/brr-never-written-{tmp_path.name}-report.md")
    assert not missing.exists()
    spec = f"Branch: `brr/thing`\nReport: `{missing}`\n"
    result = daemon._spawn_contract_check(spec, "brr/thing")
    assert result["mismatch"] is True
    assert result["report_found"] is False


def test_spawn_contract_check_no_report_named_only_branch_checked():
    """No ``/tmp/brr-*.md`` token in the spec at all ⇒ nothing to check
    there; only the branch commitment governs the verdict."""
    spec = "Branch: `brr/thing`\n"
    result = daemon._spawn_contract_check(spec, "brr/thing")
    assert result["mismatch"] is False
    assert result["spec_report"] is None
    assert result["report_found"] is None


def _spawn_child_run(*, body, publish_branch=None, status="done"):
    meta = {
        "spawn_parent_run_id": "run-parent",
        "spawn_parent_conversation_key": "telegram:42:",
    }
    if publish_branch is not None:
        meta["publish_branch"] = publish_branch
    return Run(
        id="run-child", event_id="evt-child", body=body,
        source="telegram", status=status, meta=meta,
    )


def test_notify_spawn_parent_contract_match_is_ordinary_event(tmp_path):
    inbox = tmp_path / ".brr" / "inbox"
    task = _spawn_child_run(
        body="Branch: `brr/thing`\n", publish_branch="brr/thing",
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert "status=done" in note["body"]
    assert "contract-mismatch" not in note["body"]


def test_notify_spawn_parent_scanned_branch_mismatch_annotates_not_indicts(tmp_path):
    """#640: a *scanned* (undeclared) contract — no ``branch:``/``report:``
    frontmatter, just the first ``brr/<slug>`` matched in spec prose — is a
    fuzzy read. A mismatch there must annotate the completion note, not
    stamp the worker's real status as ``contract-mismatch`` nor set the
    event-layer flag; only a *declared* contract may indict (guarded by
    ``test_notify_spawn_parent_declared_branch_mismatch_still_indicts``
    below, #640-negative)."""
    inbox = tmp_path / ".brr" / "inbox"
    response_path = tmp_path / "response.md"
    response_path.write_text("worker's own account of the work\n", encoding="utf-8")
    task = _spawn_child_run(
        body="Branch: `brr/wake-request-source-gate`\n",
        publish_branch="brr/stopped-run-kb-credit",
    )
    task.meta["response_path"] = str(response_path)

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert "status=done" in note["body"]
    assert "contract-mismatch" not in note["body"]
    assert "advisory" in note["body"]
    assert "brr/wake-request-source-gate" in note["body"]
    assert "brr/stopped-run-kb-credit" in note["body"]


def test_notify_spawn_parent_scanned_missing_report_annotates_not_indicts(tmp_path):
    """Same #640 rule for the report-path half of a scanned contract: a
    missing report scanned from prose annotates, it doesn't indict."""
    inbox = tmp_path / ".brr" / "inbox"
    missing = Path(f"/tmp/brr-never-written-{tmp_path.name}-report.md")
    assert not missing.exists()
    task = _spawn_child_run(
        body=f"Branch: `brr/thing`\nReport: `{missing}`\n",
        publish_branch="brr/thing",
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert "status=done" in note["body"]
    assert "was never written" in note["body"]


def test_notify_spawn_parent_no_branch_in_spec_is_unchanged(tmp_path):
    """No ``brr/<slug>`` anywhere in the spec ⇒ no contract to check ⇒ the
    completion event reads exactly as it did before #574."""
    inbox = tmp_path / ".brr" / "inbox"
    task = _spawn_child_run(body="just go do the thing", publish_branch="brr/thing")

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert "status=done" in note["body"]


def test_notify_spawn_parent_contract_check_failure_fails_open(tmp_path, monkeypatch):
    """A bug in the contract check itself must never surface as a worker
    failure — it degrades to the ordinary completion event, logged."""
    inbox = tmp_path / ".brr" / "inbox"
    task = _spawn_child_run(body="Branch: `brr/thing`\n", publish_branch="brr/thing")

    def boom(*_a, **_k):
        raise ValueError("unparseable")

    monkeypatch.setattr(daemon, "_spawn_contract_check", boom)

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert "status=done" in note["body"]


def test_notify_spawn_parent_pqav_regression(tmp_path):
    """Reconstructs the live 2026-07-22 case named in #574: a spec for #564
    on ``brr/wake-request-source-gate``, a child that delivered #565 on
    ``brr/stopped-run-kb-credit`` and reported clean.

    Under #640 this reconstructs as a *scanned* contract — 2026-07-22 predates
    the declared ``branch:``/``report:`` frontmatter, so nothing here was
    ever declared. The scan still catches the divergence (the whole reason
    the check exists), but per #640 a scanned read may only annotate, not
    indict: the mismatch flag stays unset and the run's own completion
    status is untouched. A dispatcher that wants this caught as a hard
    verdict now has to *declare* the contract."""
    inbox = tmp_path / ".brr" / "inbox"
    spec = (
        "# Task: issue #564 — wake request source gate\n"
        "**Branch: `brr/wake-request-source-gate`**\n"
        "**Report: `/tmp/brr-wake-request-report.md`**\n"
    )
    task = _spawn_child_run(body=spec, publish_branch="brr/stopped-run-kb-credit")

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert "advisory" in note["body"]
    assert "brr/wake-request-source-gate" in note["body"]
    assert "brr/stopped-run-kb-credit" in note["body"]
    # The run's own completion status is untouched.
    assert task.status == "done"


# ── #640: declared contract beats scanned prose ─────────────────────


def test_queue_spawn_request_declares_contract_from_frontmatter(tmp_path):
    """``branch:``/``report:`` in the spawn frontmatter carry onto the
    child's own meta as ``spawn_contract_branch``/``spawn_contract_report``
    — the wiring ``_notify_spawn_parent`` later prefers over the prose
    scan (#640)."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original task", status="processing")
    event_id = path.stem
    task = Run(id="run-parent", event_id=event_id, body="original", source="telegram")

    accepted = daemon._queue_spawn_request(
        daemon._WorkerEmit(brr_dir, "", event_id),
        task,
        inbox,
        event_id,
        {
            "spawn": True,
            "branch": "brr/declared-slug",
            "report": "/tmp/brr-declared-slug-report.md",
        },
        "bounded side task",
        outbox,
    )

    assert accepted is True
    spawned = [p for p in inbox.glob("*.md") if p.stem != event_id]
    child = protocol._read_event(spawned[0])
    assert child["spawn_contract_branch"] == "brr/declared-slug"
    assert child["spawn_contract_report"] == "/tmp/brr-declared-slug-report.md"


def test_notify_spawn_parent_declared_contract_beats_sibling_prose(tmp_path):
    """#640a: a spec whose prose responsibly names a *sibling* worker's
    branch ahead of its own (the worktree-discipline "don't collide with
    the run on X" note) must not have that sibling mention read as the
    contract when a ``branch:`` was declared. Declared + published match
    ⇒ no mismatch, status untouched — even though the first ``brr/<slug>``
    in the prose is the sibling's, not this worker's own."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child",
        body=(
            "A sibling worker is live on `brr/sibling-branch` — do not "
            "collide with it.\n\n"
            "## Deliverable\nBranch: `brr/real-slug`\n"
        ),
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "publish_branch": "brr/real-slug",
            "spawn_contract_branch": "brr/real-slug",
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert "status=done" in note["body"]
    assert "contract-mismatch" not in note["body"]


def test_notify_spawn_parent_declared_branch_mismatch_still_indicts(tmp_path):
    """#640-negative (guard the guard): a *declared* branch that genuinely
    differs from published must still stamp contract-mismatch — the
    declared/scanned distinction is not a blanket amnesty."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child",
        body="Branch: `brr/declared-slug`\n",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "publish_branch": "brr/actually-published",
            "spawn_contract_branch": "brr/declared-slug",
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note["spawn_contract_mismatch"] is True
    assert note["spawn_contract_spec_branch"] == "brr/declared-slug"
    assert note["spawn_contract_published_branch"] == "brr/actually-published"
    assert "status=contract-mismatch" in note["body"]


# ── #633: contract-mismatch requires evidence the worker ran ────────


def test_notify_spawn_parent_no_evidence_is_runner_failed_not_mismatch(tmp_path):
    """#633a: a completion with no transcript/commits/worktree changes and a
    runner error must read as ``runner-failed``, with the provider's own
    message promoted to the first line and the spec/published comparison
    dropped entirely — even though the spec declared a branch commitment
    the child never had a chance to meet. The event-layer mismatch flag
    must not be set either."""
    inbox = tmp_path / ".brr" / "inbox"
    response_path = tmp_path / "response.md"
    response_path.write_text(
        "I couldn't complete this run.\n\n"
        "brr is surfacing this because runner failed after 1 attempt(s): "
        "ERROR: You've hit your usage limit. Try again at Jul 28th, 2026.",
        encoding="utf-8",
    )
    task = Run(
        id="run-child", event_id="evt-child",
        body="Branch: `brr/control-verbs-owner-only`\n",
        source="telegram", status="error",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_branch": "brr/control-verbs-owner-only",
            "response_path": str(response_path),
            # No has_new_commit, no trace_dirs — nothing the worker did.
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert "status=runner-failed" in note["body"]
    assert "contract mismatch" not in note["body"]
    assert "spec branch" not in note["body"]
    assert "published branch" not in note["body"]
    # The provider's own message is the first thing in the note, not
    # buried after a status/contract preamble.
    assert note["body"].startswith("I couldn't complete this run.")
    assert note["body"].index("usage limit") < note["body"].index(
        "status=runner-failed",
    )


def test_notify_spawn_parent_worker_ran_declared_mismatch_still_indicts(tmp_path):
    """#633-negative: a worker that *ran* (real commit on the branch) and
    published the wrong declared branch must still be stamped
    contract-mismatch — the #633 evidence gate must not swallow a genuine
    violation just because the run's own terminal status was ``error``."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child",
        body="Branch: `brr/declared-slug`\n",
        source="telegram", status="error",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "publish_branch": "brr/wrong-branch",
            "spawn_contract_branch": "brr/declared-slug",
            "has_new_commit": True,
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note["spawn_contract_mismatch"] is True
    assert note["spawn_contract_spec_branch"] == "brr/declared-slug"
    assert note["spawn_contract_published_branch"] == "brr/wrong-branch"
    assert "status=contract-mismatch" in note["body"]


# ── #648: spawn_completed carries child produce handles ─────────────────────


def test_notify_spawn_parent_clean_reap_carries_produce_handles(tmp_path):
    """A clean spawn reap emits branch, PR number, and report path/found as
    structured frontmatter keys on the spawn_completed event.

    Load-bearing: before #648 the healthy path carried none of these.
    Must be red against unmodified source.
    """
    inbox = tmp_path / ".brr" / "inbox"
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-child"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / ".pr").write_text("647\n", encoding="utf-8")
    report = tmp_path / "brr-reap-handles-report.md"
    report.write_text("report content\n", encoding="utf-8")
    task = Run(
        id="run-child", event_id="evt-child",
        body=f"Branch: `brr/orientation-ledger`\nReport: `{report}`\n",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "publish_branch": "brr/orientation-ledger",
            "spawn_contract_branch": "brr/orientation-ledger",
            "spawn_contract_report": str(report),
            "outbox_path": str(outbox_dir),
        },
    )

    # Production order, not a convenient one: the child's `finally` captures
    # the PR handle and then rmtree's the outbox, and only afterwards does the
    # parent's loop reap the future and notify. A fixture that skips the
    # teardown tests a lifecycle the runtime cannot produce.
    daemon._capture_pr_handle(task, outbox_dir)
    daemon._remove_outbox(outbox_dir)
    assert not outbox_dir.exists()

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note.get("spawn_published_branch") == "brr/orientation-ledger"
    # parse_frontmatter coerces bare numerics to int; 647 written → 647 read.
    assert str(note.get("spawn_pr_number")) == "647"
    assert note.get("spawn_report_path") == str(report)
    assert note.get("spawn_report_found") is True


def test_notify_spawn_parent_pr_survives_outbox_teardown(tmp_path):
    """The `.pr` control is gone by the time the parent reaps — the handle
    must have been captured before teardown, or it is lost by construction.

    Regression pin for the defect this fix closes (#648, caught in review):
    `_notify_spawn_parent` runs after the child's future resolves, and that
    future's `finally` has already `shutil.rmtree`'d the outbox the `.pr`
    control lives in. Reading `.pr` at notify time therefore *always* reads a
    path that no longer exists, so the one field the ticket was filed to save
    was the one that could never arrive.

    Asserted the way the failure actually shows up: the outbox is destroyed
    first, and the handle must still be on the event. Deleting
    `_capture_pr_handle` turns this red; no fixture tweak can make it pass
    while the read stays on the deleted path.
    """
    inbox = tmp_path / ".brr" / "inbox"
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-child"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / ".pr").write_text("651\n", encoding="utf-8")
    task = Run(
        id="run-child", event_id="evt-child",
        body="",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "publish_branch": "brr/catalog-carries-the-price",
            "outbox_path": str(outbox_dir),
        },
    )

    daemon._capture_pr_handle(task, outbox_dir)
    daemon._remove_outbox(outbox_dir)
    assert not (outbox_dir / ".pr").exists()

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert str(note.get("spawn_pr_number")) == "651"


def test_capture_pr_handle_absent_control_sets_no_key(tmp_path):
    """No `.pr` ⇒ no `pr_number` on meta, so 'no PR' stays distinguishable
    from 'PR unknown' (#648 rule 1). The fixture cannot become legal: it
    asserts the key is *absent*, not that it holds a placeholder."""
    outbox_dir = tmp_path / "outbox"
    outbox_dir.mkdir()
    task = Run(id="r", event_id="e", body="", source="telegram", status="done", meta={})

    daemon._capture_pr_handle(task, outbox_dir)

    assert "pr_number" not in task.meta

    # A missing outbox directory entirely must also be survivable: teardown
    # ordering is not something a produce convenience may assume.
    daemon._capture_pr_handle(task, tmp_path / "never-existed")
    assert "pr_number" not in task.meta


def test_notify_spawn_parent_no_pr_file_omits_spawn_pr_number(tmp_path):
    """No .pr file written ⇒ spawn_pr_number absent from the event — never
    zero, None, or an empty string. 'Absent stays absent' (#648 rule 1).

    spawn_published_branch is still emitted when a branch was published.
    """
    inbox = tmp_path / ".brr" / "inbox"
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-child"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    # No .pr file written.
    task = Run(
        id="run-child", event_id="evt-child",
        body="",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "publish_branch": "brr/some-branch",
            "outbox_path": str(outbox_dir),
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_pr_number" not in note
    # Branch WAS published, so that key must still appear.
    assert note.get("spawn_published_branch") == "brr/some-branch"


def test_notify_spawn_parent_mismatch_reap_preserves_contract_keys(tmp_path):
    """A mismatch reap carries the new produce handles AND every
    spawn_contract_* key exactly as before — the two namespaces are additive.

    Regression pin on the existing mismatch behaviour: nothing in the
    produce-handle logic may alter, drop, or rename any spawn_contract_* key.
    """
    inbox = tmp_path / ".brr" / "inbox"
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-child"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    task = Run(
        id="run-child", event_id="evt-child",
        body="",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "publish_branch": "brr/actually-published",
            "spawn_contract_branch": "brr/declared-slug",
            "outbox_path": str(outbox_dir),
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    # Existing mismatch keys byte-unchanged.
    assert note.get("spawn_contract_mismatch") is True
    assert note.get("spawn_contract_spec_branch") == "brr/declared-slug"
    assert note.get("spawn_contract_published_branch") == "brr/actually-published"
    assert "status=contract-mismatch" in note["body"]
    # New produce handle also present under the distinct namespace.
    assert note.get("spawn_published_branch") == "brr/actually-published"


def test_notify_spawn_parent_report_found_false_carried(tmp_path):
    """spawn_report_found=False rides through when the worker skipped its
    declared report. Must be red against unmodified source.
    """
    inbox = tmp_path / ".brr" / "inbox"
    missing = tmp_path / "brr-never-written-report.md"
    assert not missing.exists()
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-child"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    task = Run(
        id="run-child", event_id="evt-child",
        body=f"Branch: `brr/thing`\nReport: `{missing}`\n",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "publish_branch": "brr/thing",
            "spawn_contract_branch": "brr/thing",
            "spawn_contract_report": str(missing),
            "outbox_path": str(outbox_dir),
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note.get("spawn_report_path") == str(missing)
    assert note.get("spawn_report_found") is False


def test_notify_spawn_parent_cancelled_before_start_emits_no_produce_keys(tmp_path):
    """A spawn cancelled before it started emits spawn_stopped=True but carries
    no produce handles — no branch, no PR, no report path.

    The child never ran; by rule 1 absent stays absent. Pin on the
    stopped-before-start create_event call (separate from _notify_spawn_parent)
    so neither path accidentally acquires produce keys.
    """
    inbox = tmp_path / ".brr" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    # Create the queued spawn event the stop will cancel.
    path = protocol.create_event(
        inbox, "spawn", "do the thing",
        spawn_immediate=True, spawn_parent_run_id="run-parent",
    )
    spawn_eid = path.stem
    # Register as not-yet-dispatched (run_id=None).
    control = {
        "event_id": spawn_eid,
        "parent_run_id": "run-parent",
        "run_id": None,
        "stopped": False,
    }

    daemon._apply_run_stop(
        control, inbox, stopped_by="run-parent", conversation_key="telegram:42:",
    )

    completed = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("source") == "spawn_completed"
    ]
    assert len(completed) == 1
    note = completed[0]
    assert note.get("spawn_stopped") is True
    assert "spawn_published_branch" not in note
    assert "spawn_pr_number" not in note
    assert "spawn_report_path" not in note


def test_clean_finish_spawn_notifies_parent_end_to_end(tmp_path, monkeypatch):
    """A spawn that runs to a clean, zero-commit finish must still land a
    completion notification in the parent's thread — issue #268's still-
    open finding, quoted from its 2026-07-07 follow-up comment: "a spawn
    that exits cleanly with zero commits produces no completion/crash
    notification back to the parent, despite #266's crash-notify path."

    Every existing test touching this exercises only one half of the
    seam: ``test_notify_spawn_parent_lands_pending_event_for_still_running_
    parent`` unit-tests ``_notify_spawn_parent`` against a hand-built
    ``Run`` whose ``meta`` already contains ``spawn_parent_run_id`` —
    never touching real event dispatch. ``test_concurrent_spawn_pool_
    respects_configured_width`` and its siblings drive the real
    ``start()`` loop's dispatch/reap wiring, but monkeypatch
    ``_notify_spawn_parent`` away entirely and hand the fake worker a
    ``Run`` with bare ``meta={"worker": True}`` — never exercising the
    real ``spawn_parent_run_id``/``spawn_parent_conversation_key``
    propagation ``Run.from_event`` performs from the actual dispatched
    event. ``test_drain_outbox_queues_spawn_request``'s own docstring
    names the gap directly: "the main-loop concurrent-dispatch wiring
    itself has no automated end-to-end test."

    This pins that missing seam: a real spawn event created via
    ``_drain_outbox``/``_queue_spawn_request`` (so it carries the same
    parent-linkage meta production dispatch writes), read back through
    the real ``start()`` loop's dispatch scan, turned into a ``Run`` via
    the real ``Run.from_event`` (only the runner subprocess itself is
    faked — no branch, no commit, no response file: the exact "clean,
    zero-commit finish" shape #268 names), reaped by the real main loop,
    and handed to the real (unmocked) ``_notify_spawn_parent``. If parent
    linkage ever failed to survive that round trip, this test would catch
    it; as of this run it passes against the current code, meaning the
    success-path notify wiring is already structurally sound for this
    shape — the director's own read of the issue.
    """
    write_repo_scaffold(tmp_path)
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    parent_outbox = brr_dir / "outbox" / "evt-parent"
    parent_outbox.mkdir(parents=True)

    parent_path = protocol.create_event(
        inbox, "telegram", "parent task", status="processing",
        conversation_key="telegram:99:",
    )
    parent_event_id = parent_path.stem
    (parent_outbox / "spawn.md").write_text(
        "---\nspawn: true\nshell: codex-mini\n---\nbounded concurrent task\n",
        encoding="utf-8",
    )
    parent_task = Run(
        id="run-parent-e2e", event_id=parent_event_id, body="parent task",
        source="telegram", conversation_key="telegram:99:",
        meta={"repo_label": "Gurio/brr"},
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:99:", parent_event_id),
        parent_task, responses, parent_event_id, parent_outbox, inbox,
    )
    assert promoted == 1

    cfg: dict = {}

    def fake_run_worker(event, *_args, **_kwargs):
        # Real meta propagation via the real Run.from_event — this is the
        # exact mechanism that must carry spawn_parent_run_id /
        # spawn_parent_conversation_key from the dispatched event through
        # to the Run the reap block hands to _notify_spawn_parent. Status
        # "done", no branch/commit/response-file meta at all: the "clean,
        # zero-commit finish" shape #268 names.
        task = Run.from_event(event, cfg)
        task.status = "done"
        return task

    ticks = {"n": 0}

    def fake_fire_due_schedules(*_a, **_k):
        ticks["n"] += 1
        notes = [
            e for e in protocol.list_pending(inbox) if e.get("spawned_by_run")
        ]
        if notes or ticks["n"] > 200:
            raise StopIteration

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: cfg)
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_fire_due_schedules", fake_fire_due_schedules)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    # Deliberately NOT monkeypatching _notify_spawn_parent — that is the
    # function under test.

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert ticks["n"] <= 200, "spawn never reaped/notified within the tick budget"
    notes = [e for e in protocol.list_pending(inbox) if e.get("spawned_by_run")]
    assert len(notes) == 1
    note = notes[0]
    assert note["conversation_key"] == "telegram:99:"
    assert note["spawn_parent_run_id"] == "run-parent-e2e"
    assert note.get("spawn_failed") is not True
    assert "status=done" in note["body"]


def test_crashed_spawn_notifies_parent_end_to_end(tmp_path, monkeypatch):
    """Symmetric to ``test_clean_finish_spawn_notifies_parent_end_to_end``,
    for the crash half of the same reap block: a spawn whose worker raises
    before producing a ``Run`` must still land a (failure) notification in
    the parent's thread, via the real ``_queue_spawn_request`` → dispatch →
    reap → ``_notify_spawn_parent_of_crash`` path — not a hand-built event
    dict calling the notifier directly (``test_notify_spawn_parent_of_
    crash_lands_pending_event`` already covers that half in isolation).
    """
    write_repo_scaffold(tmp_path)
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    parent_outbox = brr_dir / "outbox" / "evt-parent"
    parent_outbox.mkdir(parents=True)

    parent_path = protocol.create_event(
        inbox, "telegram", "parent task", status="processing",
        conversation_key="telegram:77:",
    )
    parent_event_id = parent_path.stem
    (parent_outbox / "spawn.md").write_text(
        "---\nspawn: true\nshell: codex-mini\n---\nbounded concurrent task\n",
        encoding="utf-8",
    )
    parent_task = Run(
        id="run-parent-crash-e2e", event_id=parent_event_id, body="parent task",
        source="telegram", conversation_key="telegram:77:",
        meta={"repo_label": "Gurio/brr"},
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:77:", parent_event_id),
        parent_task, responses, parent_event_id, parent_outbox, inbox,
    )
    assert promoted == 1

    cfg: dict = {}

    def fake_run_worker(_event, *_args, **_kwargs):
        raise RuntimeError("boom: runner launch failed")

    ticks = {"n": 0}

    def fake_fire_due_schedules(*_a, **_k):
        ticks["n"] += 1
        notes = [
            e for e in protocol.list_pending(inbox) if e.get("spawn_failed")
        ]
        if notes or ticks["n"] > 200:
            raise StopIteration

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: cfg)
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_fire_due_schedules", fake_fire_due_schedules)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    # Deliberately NOT monkeypatching _notify_spawn_parent_of_crash.

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert ticks["n"] <= 200, "crashed spawn never reaped/notified within the tick budget"
    notes = [e for e in protocol.list_pending(inbox) if e.get("spawn_failed")]
    assert len(notes) == 1
    note = notes[0]
    assert note["conversation_key"] == "telegram:77:"
    assert note["spawn_parent_run_id"] == "run-parent-crash-e2e"
    assert "boom" in note["body"]


def _stuck_spawn_dispatch(tmp_path, conv_key="telegram:88:", parent_run="run-parent-orphan"):
    """Stage a real spawn dispatch frozen at the moment a daemon died.

    Follows the #304 e2e discipline: the spawn event is produced by the
    real ``_drain_outbox`` → ``_queue_spawn_request`` path (so it carries
    exactly the parent-linkage meta production writes), then advanced to
    ``processing`` with the very ``protocol.set_status`` write the spawn
    dispatch slot performs — the durable state a daemon death between
    dispatch and reap leaves behind. Returns the spawn event dict.
    """
    write_repo_scaffold(tmp_path)
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    parent_outbox = brr_dir / "outbox" / "evt-parent"
    parent_outbox.mkdir(parents=True)

    parent_path = protocol.create_event(
        inbox, "telegram", "parent task", status="processing",
        conversation_key=conv_key,
    )
    parent_event_id = parent_path.stem
    (parent_outbox / "spawn.md").write_text(
        "---\nspawn: true\nshell: codex-mini\n---\nbounded concurrent task\n",
        encoding="utf-8",
    )
    parent_task = Run(
        id=parent_run, event_id=parent_event_id, body="parent task",
        source="telegram", conversation_key=conv_key,
        meta={"repo_label": "Gurio/brr"},
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, conv_key, parent_event_id),
        parent_task, responses, parent_event_id, parent_outbox, inbox,
    )
    assert promoted == 1
    spawn_events = [
        e for e in protocol.list_pending(inbox) if e.get("spawn_immediate")
    ]
    assert len(spawn_events) == 1
    spawn_event = spawn_events[0]
    # The exact write the concurrent-spawn dispatch slot performs before
    # submitting the worker — the last durable trace a daemon death leaves.
    protocol.set_status(spawn_event, "processing")
    return spawn_event


def _boot_daemon_once(tmp_path, monkeypatch):
    """Drive a fresh ``daemon.start`` through boot, exiting on the first tick.

    The reconciliation sweep under test runs *before* the main loop, so one
    tick is enough; ``_run_worker`` is rigged to fail loudly if the loop
    ever reaches a dispatch, pinning that the sweep (not a re-dispatched
    worker) produced whatever the assertions observe.
    """
    cfg: dict = {}

    def fail_run_worker(*_a, **_k):
        raise AssertionError("boot tick must not dispatch a worker")

    def stop_immediately(*_a, **_k):
        raise StopIteration

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: cfg)
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fail_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_fire_due_schedules", stop_immediately)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    with pytest.raises(StopIteration):
        daemon.start(tmp_path)


def _age_path(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_daemon_start_reports_available_update(tmp_path, monkeypatch, capsys):
    (tmp_path / "AGENTS.md").write_text("test\n", encoding="utf-8")

    def refresh(_repo_root, *, on_complete=None):
        assert on_complete is not None
        on_complete(release_availability.Availability("0.1.0", "0.2.0"))
        return False

    monkeypatch.setattr(release_availability, "refresh_if_stale_async", refresh)
    _boot_daemon_once(tmp_path, monkeypatch)

    assert "[brnrd] update available: 0.1.0 → 0.2.0" in capsys.readouterr().out


def test_orphaned_spawn_reconciled_to_parent_on_restart(tmp_path, monkeypatch):
    """#311 option (2), end to end: a spawn event left ``processing`` by a
    daemon death, provably no longer running (no presence, no live pid, no
    write inside the safety horizon), is resolved at the next boot and the
    crash notification lands in the parent's conversation — from the
    parent-linkage meta the real ``_queue_spawn_request`` wrote, via the
    real, unmocked ``_notify_spawn_parent_of_crash``.
    """
    spawn_event = _stuck_spawn_dispatch(tmp_path)
    _age_path(spawn_event["_path"], 25 * 3600)

    _boot_daemon_once(tmp_path, monkeypatch)

    inbox = tmp_path / ".brr" / "inbox"
    notes = [e for e in protocol.list_pending(inbox) if e.get("spawn_failed")]
    assert len(notes) == 1
    note = notes[0]
    assert note["conversation_key"] == "telegram:88:"
    assert note["spawn_parent_run_id"] == "run-parent-orphan"
    assert "restarted" in note["body"]
    # The stuck event itself is resolved — the idempotence guard, and what
    # keeps the crash-recovery re-dispatch path off work the parent has
    # just been told died.
    refreshed = protocol.parse_frontmatter(
        Path(spawn_event["_path"]).read_text(encoding="utf-8"))
    assert refreshed.get("status") == "error"
    assert "spawn reconciliation" in str(refreshed.get("reconcile_reason"))


def test_orphaned_spawn_reconciliation_is_idempotent_across_restarts(
    tmp_path, monkeypatch,
):
    """A second restart must not double-notify: the first sweep resolved the
    event's status, and a resolved event never matches the sweep again —
    the status transition *is* the guard, no extra bookkeeping."""
    spawn_event = _stuck_spawn_dispatch(tmp_path, conv_key="telegram:89:")
    _age_path(spawn_event["_path"], 25 * 3600)

    _boot_daemon_once(tmp_path, monkeypatch)
    _boot_daemon_once(tmp_path, monkeypatch)

    inbox = tmp_path / ".brr" / "inbox"
    notes = [e for e in protocol.list_pending(inbox) if e.get("spawn_failed")]
    assert len(notes) == 1


def test_spawn_reconciliation_leaves_live_worker_untouched(
    tmp_path, monkeypatch,
):
    """Conservative liveness: a spawn whose run still has a live presence
    entry is not swept, even when every durable file is ancient — a daemon
    restart can leave an orphaned runner still writing, and presence is the
    live authority the janitors already trust."""
    spawn_event = _stuck_spawn_dispatch(tmp_path, conv_key="telegram:90:")
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    live_run = Run(
        id="run-spawn-still-live", event_id=spawn_event["id"],
        body="bounded concurrent task", source="spawn",
    )
    live_run.save(runs_dir)
    presence.register(
        brr_dir, kind="daemon", run_id="run-spawn-still-live",
        pid=os.getpid(),
    )
    # Age every durable trace past the safety horizon so presence is the
    # only thing keeping this dispatch alive — the sharpest reading of the
    # liveness check.
    _age_path(spawn_event["_path"], 25 * 3600)
    _age_path(runs_dir / "run-spawn-still-live" / "run.md", 25 * 3600)

    _boot_daemon_once(tmp_path, monkeypatch)

    inbox = brr_dir / "inbox"
    assert not [e for e in protocol.list_pending(inbox) if e.get("spawn_failed")]
    refreshed = protocol.parse_frontmatter(
        Path(spawn_event["_path"]).read_text(encoding="utf-8"))
    assert refreshed.get("status") == "processing"


def test_spawn_reconciliation_waits_out_fresh_dispatches(tmp_path):
    """No liveness signal at all still doesn't mean dead: a freshly-stuck
    event (inside the safety horizon, no closed ledger row) is left for a
    later boot — "stale for a generous threshold" over "not in my table"."""
    spawn_event = _stuck_spawn_dispatch(tmp_path, conv_key="telegram:91:")
    ctx = daemon.account.resolve_context(tmp_path, {})

    assert daemon._reconcile_orphaned_spawn_dispatches(ctx, tmp_path, {}) == 0
    refreshed = protocol.parse_frontmatter(
        Path(spawn_event["_path"]).read_text(encoding="utf-8"))
    assert refreshed.get("status") == "processing"


def test_spawn_reconciliation_accepts_closed_ledger_as_proof(tmp_path):
    """A closed ledger row for the event's run proves the worker ended even
    inside the staleness horizon (daemon died between the worker's ledger
    append and the reap-notify), so the parent hears promptly instead of a
    day later."""
    spawn_event = _stuck_spawn_dispatch(tmp_path, conv_key="telegram:92:")
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    Run(
        id="run-spawn-ledger-closed", event_id=spawn_event["id"],
        body="bounded concurrent task", source="spawn",
    ).save(runs_dir)
    ledger_path = daemon.run_ledger.ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({"run_id": "run-spawn-ledger-closed"}) + "\n",
        encoding="utf-8",
    )
    ctx = daemon.account.resolve_context(tmp_path, {})

    assert daemon._reconcile_orphaned_spawn_dispatches(ctx, tmp_path, {}) == 1
    inbox = brr_dir / "inbox"
    notes = [e for e in protocol.list_pending(inbox) if e.get("spawn_failed")]
    assert len(notes) == 1
    assert notes[0]["conversation_key"] == "telegram:92:"


# ── #316: boot-time interrupted-run marker ───────────────────────────


def _dead_pid() -> int:
    """A pid that provably belonged to a real, now-dead process."""
    proc = subprocess.Popen(["sleep", "0"])
    proc.wait()
    return proc.pid


def _frozen_run(tmp_path, conv_key="telegram:95:", *, pid="dead"):
    """Stage a real addressed run frozen at the moment a daemon died.

    #304 e2e discipline: the event is written by the real
    ``protocol.create_event``, advanced to ``processing`` with the very
    ``protocol.set_status`` write the dispatch loop performs, and the run
    manifest is built through the real ``Run.from_event`` seam — including
    the exact ``task.meta["pid"]`` write whose stated purpose is this
    future boot's proof of death. The conversation log then receives the
    same lifecycle packets a live worker would have emitted before the
    crash, so the "frozen card" the sweep must update is the projection a
    real card renders from. Returns ``(event, task)``.
    """
    if not (tmp_path / "AGENTS.md").exists():
        write_repo_scaffold(tmp_path)
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    runs_dir = brr_dir / "runs"
    event_path = protocol.create_event(
        inbox, "telegram", "long research task",
        conversation_key=conv_key, trust_tier="owner",
    )
    event = next(
        e for e in protocol.list_pending(inbox)
        if Path(e["_path"]) == event_path
    )
    protocol.set_status(event, "processing")
    task = Run.from_event(event)
    if pid == "dead":
        task.meta["pid"] = _dead_pid()
    elif pid is not None:
        task.meta["pid"] = pid
    task.save(runs_dir)
    emit = daemon._WorkerEmit(brr_dir, conv_key, str(event["id"]))
    emit(
        "run_created", run_id=task.id, event_id=event["id"],
        env="worktree", repo_label="Gurio/brr",
    )
    emit("attempt_started", run_id=task.id, event_id=event["id"], attempt=1)
    emit(
        "run_started", run_id=task.id, event_id=event["id"],
        runner="claude", branch=f"brr/{task.id}",
    )
    return event, task


def _boot_daemon_recording_dispatches(tmp_path, monkeypatch):
    """Drive ``daemon.start`` through boot and its dispatch loop, recording
    which events the loop dispatches.

    Unlike ``_boot_daemon_once``, the loop is allowed to dispatch: the
    #316 marker must leave the crash-recovery re-dispatch of the frozen
    run's event undisturbed, so the retry itself is part of what these
    tests pin. The fake worker raises, which drives the real
    crashed-before-a-Run backstop (event retired to ``error``), keeping a
    double boot from re-dispatching endlessly.
    """
    dispatched: list[str] = []
    ticks = {"n": 0}

    def fake_run_worker(event, *_a, **_k):
        dispatched.append(str(event.get("id") or ""))
        raise RuntimeError("worker stub: dispatch recorded")

    def fake_fire_due_schedules(*_a, **_k):
        ticks["n"] += 1
        if dispatched or ticks["n"] > 200:
            raise StopIteration

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_fire_due_schedules", fake_fire_due_schedules)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    with pytest.raises(StopIteration):
        daemon.start(tmp_path)
    return dispatched


def _host_interrupted_records(brr_dir, conv_key):
    from brr import conversations

    return [
        r for r in conversations.read_records(brr_dir, conv_key)
        if r.get("type") == "failed"
        and r.get("failure_kind") == "host_interrupted"
    ]


def test_interrupted_run_marked_and_card_updated_on_boot(tmp_path, monkeypatch):
    """#316 direction (1), end to end: a run left in flight by a daemon
    death (manifest still ``pending``/``running``, dispatcher pid provably
    dead, no presence) is marked ``host_interrupted`` at the next boot,
    its frozen card re-renders as "interrupted … retrying", and the
    event's own crash-recovery retry still fires — the marker changes the
    card story, never the retry mechanism."""
    from brr import run_progress

    event, task = _frozen_run(tmp_path)

    dispatched = _boot_daemon_recording_dispatches(tmp_path, monkeypatch)

    brr_dir = tmp_path / ".brr"
    refreshed = Run.from_file(brr_dir / "runs" / task.id / "run.md")
    assert refreshed is not None
    assert refreshed.status == "error"
    assert refreshed.meta.get("failure_kind") == "host_interrupted"
    assert "dispatching daemon" in str(refreshed.meta.get("interrupt_reason"))
    # The terminal packet the dead daemon never sent reached the card's
    # conversation log exactly once …
    records = _host_interrupted_records(brr_dir, "telegram:95:")
    assert len(records) == 1
    assert records[0].get("run_id") == task.id
    # … and the rendered card now tells the truthful story.
    view = run_progress.project_run(brr_dir, "telegram:95:", task.id)
    assert view is not None
    assert view.state == "failed"
    assert view.failure_kind == "host_interrupted"
    card = run_progress.render_text(view)
    assert "interrupted" in card
    assert "retrying" in card
    # The existing retry mechanism dispatched the same event untouched.
    assert dispatched == [str(event["id"])]


def test_interrupted_marker_leaves_live_run_untouched(tmp_path):
    """Conservative liveness: a live presence entry — or a still-alive
    recorded dispatcher pid (a dev-reload re-exec keeps the same pid) —
    means the run is owned by someone; the marker must not touch it even
    when every durable file is ancient."""
    event, task = _frozen_run(tmp_path, conv_key="telegram:96:")
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    presence.register(
        brr_dir, kind="daemon", run_id=task.id, pid=os.getpid(),
    )
    _age_path(runs_dir / task.id / "run.md", 25 * 3600)
    ctx = daemon.account.resolve_context(tmp_path, {})

    assert daemon._mark_interrupted_runs(ctx, tmp_path, {}) == 0

    # Same verdict when only the recorded pid is alive (no presence).
    event2, task2 = _frozen_run(tmp_path, conv_key="telegram:96:",
                                pid=os.getpid())
    _age_path(runs_dir / task2.id / "run.md", 25 * 3600)
    assert daemon._mark_interrupted_runs(ctx, tmp_path, {}) == 0

    for staged in (task, task2):
        refreshed = Run.from_file(runs_dir / staged.id / "run.md")
        assert refreshed.status == "pending"
        assert "failure_kind" not in refreshed.meta
    assert not _host_interrupted_records(brr_dir, "telegram:96:")
    # Event state untouched either way — the retry path is not ours.
    for ev in (event, event2):
        fm = protocol.parse_frontmatter(
            Path(ev["_path"]).read_text(encoding="utf-8"))
        assert fm.get("status") == "processing"


def test_interrupted_marker_skips_terminal_runs(tmp_path):
    """A run that already reached a terminal status tells its own story;
    the marker must not rewrite history however dead its pid is."""
    _event, task = _frozen_run(tmp_path, conv_key="telegram:97:")
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    task.update_status("done", runs_dir)
    ctx = daemon.account.resolve_context(tmp_path, {})

    assert daemon._mark_interrupted_runs(ctx, tmp_path, {}) == 0

    refreshed = Run.from_file(runs_dir / task.id / "run.md")
    assert refreshed.status == "done"
    assert "failure_kind" not in refreshed.meta
    assert not _host_interrupted_records(brr_dir, "telegram:97:")


def test_interrupted_marker_idempotent_across_double_boot(tmp_path, monkeypatch):
    """A second boot must not double-mark or double-emit: the manifest's
    ``error`` transition is the guard, no extra bookkeeping."""
    _event, task = _frozen_run(tmp_path, conv_key="telegram:98:")

    first = _boot_daemon_recording_dispatches(tmp_path, monkeypatch)
    second = _boot_daemon_recording_dispatches(tmp_path, monkeypatch)

    brr_dir = tmp_path / ".brr"
    records = _host_interrupted_records(brr_dir, "telegram:98:")
    assert len(records) == 1
    assert records[0].get("run_id") == task.id
    # First boot retried the event; the fake worker's crash retired it
    # (the real crashed-before-a-Run backstop), so the second boot had
    # nothing to dispatch — and nothing to re-mark.
    assert len(first) == 1
    assert second == []


def test_interrupted_marker_waits_out_fresh_pidless_manifests(tmp_path):
    """No recorded pid means no affirmative proof: a fresh manifest is
    left for a later boot, and only the janitors' conservative staleness
    horizon (the fallback, never the preferred evidence) marks it."""
    from brr import run_progress

    _event, task = _frozen_run(tmp_path, conv_key="telegram:99:", pid=None)
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    ctx = daemon.account.resolve_context(tmp_path, {})

    assert daemon._mark_interrupted_runs(ctx, tmp_path, {}) == 0
    refreshed = Run.from_file(runs_dir / task.id / "run.md")
    assert refreshed.status == "pending"

    _age_path(runs_dir / task.id / "run.md", 25 * 3600)
    assert daemon._mark_interrupted_runs(ctx, tmp_path, {}) == 1
    refreshed = Run.from_file(runs_dir / task.id / "run.md")
    assert refreshed.status == "error"
    assert refreshed.meta.get("failure_kind") == "host_interrupted"
    assert "safety horizon" in str(refreshed.meta.get("interrupt_reason"))
    view = run_progress.project_run(brr_dir, "telegram:99:", task.id)
    assert view.failure_kind == "host_interrupted"


def test_interrupted_marker_retry_tail_follows_event_state(tmp_path):
    """The card's "retrying" tail is read off the event's actual
    dispatchability, not asserted: a still-``processing`` event earns the
    tail; an already-retired event gets plain "interrupted"."""
    from brr import run_progress

    event, task = _frozen_run(tmp_path, conv_key="telegram:100:")
    ctx = daemon.account.resolve_context(tmp_path, {})
    protocol.set_status(event, "error")

    assert daemon._mark_interrupted_runs(ctx, tmp_path, {}) == 1

    brr_dir = tmp_path / ".brr"
    view = run_progress.project_run(brr_dir, "telegram:100:", task.id)
    card = run_progress.render_text(view)
    assert "interrupted" in card
    assert "retrying" not in card
    # The sweep never touches event state — retired stays retired.
    fm = protocol.parse_frontmatter(
        Path(event["_path"]).read_text(encoding="utf-8"))
    assert fm.get("status") == "error"


def _account_context_for_policy(tmp_path):
    home = tmp_path / "account-home"
    return daemon.account.AccountContext(
        account_id="default",
        dominion_repo=home,
        dispatch_inbox=home / "dispatch" / "inbox",
        responses_dir=home / "dispatch" / "responses",
        runs_dir=home / "runs",
        repos={},
        default_repo=daemon.account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )


def test_drain_outbox_parks_runner_policy_proposal(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    ctx = _account_context_for_policy(tmp_path)
    path = protocol.create_event(
        inbox,
        "telegram",
        "propose a runner policy",
        status="processing",
        conversation_key="telegram:42:",
    )
    event_id = path.stem
    (outbox / "policy.md").write_text(
        "---\n"
        "runner_policy: propose\n"
        "scope: repo\n"
        "---\n"
        "Prefer codex-mini for quick mechanical tasks.\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-policy",
        event_id=event_id,
        body="propose a runner policy",
        source="telegram",
        conversation_key="telegram:42:",
        meta={"repo_label": "Gurio/brr"},
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        account_context=ctx,
        stats=stats,
    )

    assert promoted == 1
    assert stats == {"current": 1, "runner_policy": 1}
    assert not daemon.account.runner_policy_path(ctx, "Gurio/brr").exists()
    proposals = list(daemon.account.runner_policy_proposals_path(ctx).glob("*.md"))
    assert len(proposals) == 1
    proposal_text = proposals[0].read_text(encoding="utf-8")
    assert "status: pending" in proposal_text
    assert "repo_label: Gurio/brr" in proposal_text
    assert protocol.frontmatter_body(proposal_text).strip() == (
        "Prefer codex-mini for quick mechanical tasks."
    )
    partial = protocol.list_partials(responses, event_id)[0].read_text(encoding="utf-8")
    assert "approve runner-policy" in partial
    assert proposals[0].stem in partial


def _write_policy_proposal(ctx, proposal_id, *, conversation_key="telegram:42:"):
    proposal = daemon.account.runner_policy_proposals_path(ctx) / f"{proposal_id}.md"
    proposal.parent.mkdir(parents=True)
    proposal.write_text(
        "---\n"
        f"id: {proposal_id}\n"
        "status: pending\n"
        "scope: repo\n"
        "repo_label: Gurio/brr\n"
        "policy_path: runner-policy/Gurio__brr/policy.md\n"
        f"conversation_key: {conversation_key}\n"
        "created: 2026-06-30T00:00:00Z\n"
        "---\n"
        "Prefer codex-mini for quick mechanical tasks.\n",
        encoding="utf-8",
    )
    return proposal


def _policy_control_target(
    tmp_path,
    body,
    *,
    conversation_key="telegram:42:",
    trust_tier="owner",
    source="telegram",
):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    protocol.create_event(
        inbox,
        source,
        body,
        conversation_key=conversation_key,
        trust_tier=trust_tier,
    )
    event = protocol.list_pending(inbox)[0]
    return daemon._DispatchTarget(
        event=event,
        repo_root=tmp_path,
        inbox_dir=inbox,
        responses_dir=responses,
        repo_label="Gurio/brr",
    )


def test_runner_policy_approval_applies_pending_proposal(tmp_path):
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "rpol-test-approve"
    proposal = _write_policy_proposal(ctx, proposal_id)
    target = _policy_control_target(
        tmp_path,
        f"approve runner-policy {proposal_id}",
    )

    handled = daemon._handle_runner_policy_control_event(target, ctx)

    assert handled is True
    assert daemon.account.runner_policy_path(ctx, "Gurio/brr").read_text(
        encoding="utf-8",
    ) == "Prefer codex-mini for quick mechanical tasks.\n"
    updated = proposal.read_text(encoding="utf-8")
    assert "status: applied" in updated
    assert "applied_path: runner-policy/Gurio__brr/policy.md" in updated
    assert protocol.list_pending(target.inbox_dir) == []
    response = protocol.response_path(
        target.responses_dir, target.event["id"],
    ).read_text(encoding="utf-8")
    assert "Applied runner-policy proposal" in response


def test_runner_policy_rejection_closes_without_applying(tmp_path):
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "rpol-test-reject"
    proposal = _write_policy_proposal(ctx, proposal_id)
    target = _policy_control_target(
        tmp_path,
        f"reject runner-policy {proposal_id}",
    )

    handled = daemon._handle_runner_policy_control_event(target, ctx)

    assert handled is True
    assert not daemon.account.runner_policy_path(ctx, "Gurio/brr").exists()
    assert "status: rejected" in proposal.read_text(encoding="utf-8")
    response = protocol.response_path(
        target.responses_dir, target.event["id"],
    ).read_text(encoding="utf-8")
    assert "Rejected runner-policy proposal" in response


def test_runner_policy_approval_requires_same_conversation(tmp_path):
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "rpol-test-cross-thread"
    proposal = _write_policy_proposal(
        ctx,
        proposal_id,
        conversation_key="telegram:42:",
    )
    target = _policy_control_target(
        tmp_path,
        f"approve runner-policy {proposal_id}",
        conversation_key="telegram:99:",
    )

    handled = daemon._handle_runner_policy_control_event(target, ctx)

    assert handled is True
    assert not daemon.account.runner_policy_path(ctx, "Gurio/brr").exists()
    assert "status: pending" in proposal.read_text(encoding="utf-8")
    response = protocol.response_path(
        target.responses_dir, target.event["id"],
    ).read_text(encoding="utf-8")
    assert "different conversation" in response


# ── Loom envelope Phase 2 — config-change proposals ────────────────────


def test_drain_outbox_parks_config_change_proposal(tmp_path, monkeypatch):
    from brr.gates import cloud as cloud_mod

    monkeypatch.setattr(
        cloud_mod,
        "propose_config_change",
        lambda brr_dir, **kw: {
            "request_id": "cfgreq_x",
            "status": "pending",
            "approve_url": "https://brnrd.example/config-approve/cfgreq_x",
        },
    )
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    ctx = _account_context_for_policy(tmp_path)
    path = protocol.create_event(
        inbox,
        "telegram",
        "please raise the spawn pool",
        status="processing",
        conversation_key="telegram:42:",
    )
    event_id = path.stem
    (outbox / "config.md").write_text(
        "---\n"
        "config_change: spawn.max_concurrent\n"
        "value: 8\n"
        "---\n"
        "Need headroom for a four-way fan-out.\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-cfg",
        event_id=event_id,
        body="please raise the spawn pool",
        source="telegram",
        conversation_key="telegram:42:",
        meta={"repo_label": "Gurio/brr"},
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        repo_root=tmp_path,
        account_context=ctx,
        stats=stats,
    )

    assert promoted == 1
    assert stats == {"current": 1, "config_change": 1}
    proposals = list(daemon.account.config_change_proposals_path(ctx).glob("*.md"))
    assert len(proposals) == 1
    text = proposals[0].read_text(encoding="utf-8")
    assert "status: pending" in text
    assert "config_key: spawn.max_concurrent" in text
    assert "requested_value: 8" in text
    assert protocol.frontmatter_body(text).strip() == "Need headroom for a four-way fan-out."
    partial = protocol.list_partials(responses, event_id)[0].read_text(encoding="utf-8")
    assert "https://brnrd.example/config-approve/cfgreq_x" in partial
    assert proposals[0].stem in partial


def test_drain_outbox_rejects_config_change_off_allowlist(tmp_path, monkeypatch):
    from brr.gates import cloud as cloud_mod

    minted_calls: list[str] = []
    monkeypatch.setattr(
        cloud_mod,
        "propose_config_change",
        lambda brr_dir, **kw: minted_calls.append(kw["config_key"]),
    )
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    ctx = _account_context_for_policy(tmp_path)
    path = protocol.create_event(
        inbox, "telegram", "turn off pacing floors", conversation_key="telegram:42:",
    )
    event_id = path.stem
    (outbox / "config.md").write_text(
        "---\nconfig_change: pacing.quota_low_floor_pct\nvalue: 0\n---\nplease\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-cfg-2",
        event_id=event_id,
        body="turn off pacing floors",
        source="telegram",
        conversation_key="telegram:42:",
        meta={"repo_label": "Gurio/brr"},
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        repo_root=tmp_path,
        account_context=ctx,
        stats=stats,
    )

    assert promoted == 1
    assert not daemon.account.config_change_proposals_path(ctx).exists()
    assert minted_calls == []
    partial = protocol.list_partials(responses, event_id)[0].read_text(encoding="utf-8")
    assert "isn't on the agent-proposable config allowlist" in partial


def test_drain_outbox_parks_dominion_budget_config_change(tmp_path, monkeypatch):
    """Wake-context budget knobs are proposable (2026-07-11 audit)."""
    from brr.gates import cloud as cloud_mod

    monkeypatch.setattr(
        cloud_mod,
        "propose_config_change",
        lambda brr_dir, **kw: {
            "request_id": "cfgreq_y",
            "status": "pending",
            "approve_url": "https://brnrd.example/config-approve/cfgreq_y",
        },
    )
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    ctx = _account_context_for_policy(tmp_path)
    path = protocol.create_event(
        inbox, "telegram", "trim the ledger inject", conversation_key="telegram:42:",
    )
    event_id = path.stem
    (outbox / "config.md").write_text(
        "---\nconfig_change: dominion.ledger_inject_budget_bytes\nvalue: 4096\n---\n"
        "Ledger tail rides every wake at its full cap; halve it.\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-cfg-3",
        event_id=event_id,
        body="trim the ledger inject",
        source="telegram",
        conversation_key="telegram:42:",
        meta={"repo_label": "Gurio/brr"},
    )
    stats: dict[str, int] = {}

    daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        repo_root=tmp_path,
        account_context=ctx,
        stats=stats,
    )

    proposals = list(daemon.account.config_change_proposals_path(ctx).glob("*.md"))
    assert len(proposals) == 1
    text = proposals[0].read_text(encoding="utf-8")
    assert "config_key: dominion.ledger_inject_budget_bytes" in text
    assert "requested_value: 4096" in text


def test_drain_outbox_rejects_non_integer_config_change_value(tmp_path, monkeypatch):
    """Allowlisted keys are int-valued; a bad value must never park.

    An approved proposal writes straight into ``.brr/config`` and prompt
    assembly does ``int(cfg.get(...))`` at wake build — a non-integer
    would crash every subsequent wake. Validate at proposal time.
    """
    from brr.gates import cloud as cloud_mod

    minted_calls: list[str] = []
    monkeypatch.setattr(
        cloud_mod,
        "propose_config_change",
        lambda brr_dir, **kw: minted_calls.append(kw["config_key"]),
    )
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    ctx = _account_context_for_policy(tmp_path)
    path = protocol.create_event(
        inbox, "telegram", "tune budget", conversation_key="telegram:42:",
    )
    event_id = path.stem
    (outbox / "config.md").write_text(
        "---\nconfig_change: dominion.ledger_inject_budget_bytes\nvalue: lots\n---\nplease\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-cfg-4",
        event_id=event_id,
        body="tune budget",
        source="telegram",
        conversation_key="telegram:42:",
        meta={"repo_label": "Gurio/brr"},
    )
    stats: dict[str, int] = {}

    daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        repo_root=tmp_path,
        account_context=ctx,
        stats=stats,
    )

    assert not daemon.account.config_change_proposals_path(ctx).exists()
    assert minted_calls == []
    partial = protocol.list_partials(responses, event_id)[0].read_text(encoding="utf-8")
    assert "needs a positive integer value" in partial


def _write_config_change_proposal(
    ctx,
    proposal_id,
    *,
    conversation_key="telegram:42:",
    key="spawn.max_concurrent",
    current="4",
    requested="8",
):
    proposal = daemon.account.config_change_proposals_path(ctx) / f"{proposal_id}.md"
    proposal.parent.mkdir(parents=True)
    proposal.write_text(
        "---\n"
        f"id: {proposal_id}\n"
        "status: pending\n"
        f"config_key: {key}\n"
        f"current_value: {current}\n"
        f"requested_value: {requested}\n"
        "repo_label: Gurio/brr\n"
        f"conversation_key: {conversation_key}\n"
        "created: 2026-07-08T00:00:00Z\n"
        "---\n"
        "Need headroom.\n",
        encoding="utf-8",
    )
    return proposal


def test_config_change_approval_applies_to_brr_config(tmp_path):
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "cfgchg-test-approve"
    proposal = _write_config_change_proposal(ctx, proposal_id)
    target = _policy_control_target(tmp_path, f"approve config-change {proposal_id}")

    handled = daemon._handle_config_change_control_event(target, ctx)

    assert handled is True
    cfg = daemon.conf.load_config(target.repo_root)
    assert cfg["spawn.max_concurrent"] == 8
    updated = proposal.read_text(encoding="utf-8")
    assert "status: applied" in updated
    assert protocol.list_pending(target.inbox_dir) == []
    response = protocol.response_path(
        target.responses_dir, target.event["id"],
    ).read_text(encoding="utf-8")
    assert "Applied config-change proposal" in response


def test_config_change_rejection_leaves_config_untouched(tmp_path):
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "cfgchg-test-reject"
    proposal = _write_config_change_proposal(ctx, proposal_id)
    target = _policy_control_target(tmp_path, f"reject config-change {proposal_id}")

    handled = daemon._handle_config_change_control_event(target, ctx)

    assert handled is True
    assert daemon.conf.load_config(target.repo_root) == {}
    assert "status: rejected" in proposal.read_text(encoding="utf-8")
    response = protocol.response_path(
        target.responses_dir, target.event["id"],
    ).read_text(encoding="utf-8")
    assert "Rejected config-change proposal" in response


def test_handle_daemon_control_events_routes_config_change(tmp_path):
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "cfgchg-test-route"
    _write_config_change_proposal(ctx, proposal_id)
    target = _policy_control_target(tmp_path, f"approve config-change {proposal_id}")

    remaining = daemon._handle_daemon_control_events([target], ctx)

    assert remaining == []


# ── Owner-tier gate tests (S2) ─────────────────────────────────────────────
#
# Each test below was driven red before keeping: the test was written first,
# run against unpatched main (without the tier gate), confirmed to fail, then
# the patch was applied and it turned green.  Red output is quoted in
# /tmp/brr-control-verbs-owner-only-report.md.


def _response_body(target) -> str:
    return protocol.response_path(
        target.responses_dir, target.event["id"]
    ).read_text(encoding="utf-8").strip()


def test_config_change_gate_blocks_collaborator(tmp_path):
    """collaborator tier: event consumed, config unchanged, proposal still pending."""
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "cfgchg-gate-collab"
    proposal = _write_config_change_proposal(ctx, proposal_id)
    target = _policy_control_target(
        tmp_path,
        f"approve config-change {proposal_id}",
        trust_tier="collaborator",
        source="github",
    )

    handled = daemon._handle_config_change_control_event(target, ctx)

    assert handled is True, "event must be consumed (not dispatched as a thought)"
    assert daemon.conf.load_config(target.repo_root) == {}, "config file must not change"
    assert "status: pending" in proposal.read_text(encoding="utf-8"), "proposal stays pending"
    assert _response_body(target) == daemon._CONTROL_VERB_REFUSE_MSG


def test_config_change_gate_blocks_untrusted(tmp_path):
    """Untrusted event (no stamp, github source): same outcome as collaborator.

    This is the case that proves the gate is not collaborator-specific — an
    unattributed inbox-drop event whose body happens to match the regex must
    not write config.
    """
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "cfgchg-gate-untrusted"
    proposal = _write_config_change_proposal(ctx, proposal_id)
    # No trust_tier in the event — resolve_tier sees source=github (not in
    # _OWNER_SOURCES) and no stamp → UNTRUSTED (fail-closed).
    target = _policy_control_target(
        tmp_path,
        f"approve config-change {proposal_id}",
        trust_tier="",        # blank — stripped to "", not a valid tier
        source="github",
    )

    handled = daemon._handle_config_change_control_event(target, ctx)

    assert handled is True
    assert daemon.conf.load_config(target.repo_root) == {}
    assert "status: pending" in proposal.read_text(encoding="utf-8")
    assert _response_body(target) == daemon._CONTROL_VERB_REFUSE_MSG


def test_runner_policy_gate_blocks_collaborator(tmp_path):
    """collaborator tier on runner-policy: event consumed, policy unchanged."""
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "rpol-gate-collab"
    proposal = _write_policy_proposal(ctx, proposal_id)
    target = _policy_control_target(
        tmp_path,
        f"approve runner-policy {proposal_id}",
        trust_tier="collaborator",
        source="github",
    )

    handled = daemon._handle_runner_policy_control_event(target, ctx)

    assert handled is True
    assert not daemon.account.runner_policy_path(ctx, "Gurio/brr").exists()
    assert "status: pending" in proposal.read_text(encoding="utf-8")
    assert _response_body(target) == daemon._CONTROL_VERB_REFUSE_MSG


def test_runner_policy_gate_blocks_untrusted(tmp_path):
    """Untrusted event (no stamp, github source): runner-policy unchanged."""
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "rpol-gate-untrusted"
    proposal = _write_policy_proposal(ctx, proposal_id)
    target = _policy_control_target(
        tmp_path,
        f"approve runner-policy {proposal_id}",
        trust_tier="",
        source="github",
    )

    handled = daemon._handle_runner_policy_control_event(target, ctx)

    assert handled is True
    assert not daemon.account.runner_policy_path(ctx, "Gurio/brr").exists()
    assert "status: pending" in proposal.read_text(encoding="utf-8")
    assert _response_body(target) == daemon._CONTROL_VERB_REFUSE_MSG


def test_owner_config_change_still_applies(tmp_path):
    """owner tier: current behaviour is preserved — config still written."""
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "cfgchg-gate-owner-ok"
    _write_config_change_proposal(ctx, proposal_id)
    # trust_tier="owner" is the _policy_control_target default; explicit here
    # for documentation clarity.
    target = _policy_control_target(
        tmp_path,
        f"approve config-change {proposal_id}",
        trust_tier="owner",
    )

    handled = daemon._handle_config_change_control_event(target, ctx)

    assert handled is True
    assert daemon.conf.load_config(target.repo_root)["spawn.max_concurrent"] == 8


def test_control_verb_existence_oracle_closed(tmp_path):
    """Non-owner naming a real vs nonexistent proposal gets byte-identical responses.

    Without this invariant, an attacker can enumerate proposal ids by watching
    whether they get a "not found" or a "not authorised" response.
    """
    ctx = _account_context_for_policy(tmp_path)
    real_id = "cfgchg-oracle-real"
    _write_config_change_proposal(ctx, real_id)
    fake_id = "cfgchg-oracle-nonexistent"

    # Both targets land in the same tmp_path so responses are readable after.
    # The inbox accumulates two events; list_pending returns them in order.
    # Build real first, run it, then build fake.
    target_real = _policy_control_target(
        tmp_path,
        f"approve config-change {real_id}",
        trust_tier="collaborator",
        source="github",
    )
    handled_real = daemon._handle_config_change_control_event(target_real, ctx)
    body_real = _response_body(target_real)

    target_fake = _policy_control_target(
        tmp_path,
        f"approve config-change {fake_id}",
        trust_tier="collaborator",
        source="github",
    )
    handled_fake = daemon._handle_config_change_control_event(target_fake, ctx)
    body_fake = _response_body(target_fake)

    assert handled_real is True
    assert handled_fake is True
    assert body_real == body_fake, (
        "response bodies must be identical — existence oracle must be closed"
    )


def test_config_change_cross_conversation_blocked_for_owner(tmp_path):
    """owner event from a different conversation: config unchanged.

    The cross-conversation guard applies after the tier check — an owner in
    the wrong thread cannot approve someone else's proposal.
    """
    ctx = _account_context_for_policy(tmp_path)
    proposal_id = "cfgchg-cross-conv"
    proposal = _write_config_change_proposal(
        ctx,
        proposal_id,
        conversation_key="telegram:42:",
    )
    target = _policy_control_target(
        tmp_path,
        f"approve config-change {proposal_id}",
        conversation_key="telegram:99:",
        trust_tier="owner",
    )

    handled = daemon._handle_config_change_control_event(target, ctx)

    assert handled is True
    assert daemon.conf.load_config(target.repo_root) == {}
    assert "status: pending" in proposal.read_text(encoding="utf-8")
    response = _response_body(target)
    assert "different conversation" in response


def test_run_worker_writes_terminal_failure_response_on_runner_error(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-run-fail")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="",
            stderr="connection dropped",
            returncode=1,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker_and_finalize(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    assert event["status"] == "done"
    response = protocol.read_response(tmp_path / ".brr" / "responses", "evt-run-fail")
    assert response is not None
    assert "runner failed after 1 attempt(s): connection dropped" in response
    assert task.terminal_reply == response


def test_interrupted_terminal_failure_omits_stderr_detail(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-interrupted")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="", stderr="turn interrupted\nprivate runner detail",
            returncode=1, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker_and_finalize(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    response = protocol.read_response(
        tmp_path / ".brr" / "responses", "evt-interrupted",
    )
    assert response is not None
    assert "runner was interrupted (external kill or shell interrupt)" in response
    assert "private runner detail" not in response
    assert task.terminal_reply == response


def test_run_worker_writes_terminal_failure_response_after_empty_stdout(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-empty-final")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    assert event["status"] == "done"
    response = protocol.read_response(tmp_path / ".brr" / "responses", "evt-empty-final")
    assert response is not None
    assert "runner produced no reply after 1 attempt(s)" in response


def test_write_terminal_failure_response_notices_schedule_crash(tmp_path):
    """A crashed ``schedule``-source run (director tick) must not vanish.

    ``_event_requires_thread_delivery`` correctly treats "schedule" as
    internal for the *success* path — a tick that re-derived nothing new
    is supposed to stay quiet (the notify-bar logic). But that same
    internal-source check used to gate the *failure* path too, so a
    crashed tick (found live 2026-07-07, run-260707-1154-kem3: killed
    mid-run, returncode 143, empty stdout/stderr) left no response file
    and nothing for the gate to deliver — silence-because-crashed and
    silence-because-nothing-changed were indistinguishable from the one
    surface (chat) the maintainer watches. This asserts the crash path now
    writes and delivers a note even though the event source is internal.
    """
    write_repo_scaffold(tmp_path)
    responses_dir = tmp_path / ".brr" / "responses"
    event = make_event(
        tmp_path, eid="evt-tick-crash", source="schedule", body="director tick",
    )
    task = Run(
        id="run-tick-crash",
        event_id="evt-tick-crash",
        body="director tick",
        source="schedule",
        conversation_key="schedule:director-tick",
    )
    response_path = tmp_path / ".brr" / "responses" / "evt-tick-crash.md"

    wrote = daemon._write_terminal_failure_response(
        daemon._WorkerEmit(tmp_path / ".brr", "schedule:director-tick", "evt-tick-crash"),
        task,
        event,
        responses_dir,
        response_path,
        "runner killed after 1 attempt(s) with exit code 143",
    )

    assert wrote is True
    assert event["status"] == "done"
    response = protocol.read_response(responses_dir, "evt-tick-crash")
    assert response is not None
    assert "runner killed after 1 attempt(s) with exit code 143" in response


def test_run_worker_calls_sync_before_resolving_branch_plan(
    tmp_path, monkeypatch,
):
    """Pre-task fetch+ff fires before the daemon picks a seed ref."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-sync-order")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )

    call_order: list[str] = []
    captured_targets: list[list[str]] = []

    def fake_refresh(_repo, *, target_branches, cfg=None):
        call_order.append("sync")
        captured_targets.append(list(target_branches))
        return daemon.sync.SyncResult(fetched=True)

    real_resolve = daemon.branching.resolve_publish_plan

    def wrapped_resolve(repo_root, ev, cfg):
        call_order.append("resolve")
        return real_resolve(repo_root, ev, cfg)

    monkeypatch.setattr(daemon.sync, "refresh_before_run", fake_refresh)
    monkeypatch.setattr(daemon.branching, "resolve_publish_plan", wrapped_resolve)

    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert call_order[:2] == ["sync", "resolve"]
    # When the event carries no structured branch field, we still
    # ask sync to consider the host's default branch (or whatever
    # gitops returns there) — empty is acceptable for a repo without
    # a default branch but the call must happen.
    assert captured_targets, "sync.refresh_before_run was not called"


def test_run_worker_proceeds_when_sync_fails(tmp_path, monkeypatch):
    """A sync error never blocks task execution."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-sync-fail")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(
        daemon.sync, "refresh_before_run",
        lambda _repo, *, target_branches, cfg=None: daemon.sync.SyncResult(
            error="git fetch origin: simulated network failure",
        ),
    )

    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"


def test_branches_to_refresh_includes_default_and_structured(monkeypatch, tmp_path):
    """The helper merges the local default branch with structured event keys."""
    write_repo_scaffold(tmp_path)
    monkeypatch.setattr(daemon.gitops, "default_branch", lambda _root: "main")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(daemon.gitops, "valid_branch_name", lambda _root, _b: True)

    targets = daemon._branches_to_refresh(
        tmp_path,
        {
            "branch_target": "feature-x",
            "target_branch": "release",
            "branch": "auto",
        },
    )

    assert targets[0] == "main"
    assert "feature-x" in targets
    assert "release" in targets
    # ``branch=auto`` is a no-op sentinel and must not appear.
    assert "auto" not in targets


def test_start_preserves_error_event_status(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = {"id": "evt-err", "status": "pending", "_path": tmp_path / ".brr" / "inbox" / "evt-err.md"}
    event["_path"].write_text(
        "---\nid: evt-err\nstatus: pending\n---\nhelp\n", encoding="utf-8",
    )
    statuses: list[str] = []
    pending_calls: list[int] = []

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    # Compress the polling sleep so the loop reaches its second
    # iteration (where StopIteration is raised) without the test
    # waiting on the production interval.
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.01)

    def fake_list_pending(_inbox):
        pending_calls.append(1)
        # Call 1 is the boot interrupted-run marker (#316) and call 2 the
        # boot spawn-reconciliation sweep (#311) — both inspect and skip
        # this non-spawn event; call 3 is the loop's first dispatch scan.
        # The fourth call breaks the loop in the main thread. The finally
        # block waits for the in-flight worker to finish before tearing
        # the pool down, so statuses observed by the worker thread are
        # present when pytest.raises captures the exit.
        if len(pending_calls) <= 3:
            return [event]
        raise StopIteration

    monkeypatch.setattr(daemon.protocol, "list_pending", fake_list_pending)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _ev, status: statuses.append(status))
    monkeypatch.setattr(
        daemon,
        "_run_worker",
        lambda *_a, **_k: Run(id="task-err", event_id="evt-err", body="help", status="error"),
    )
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert statuses == ["processing", "error"]


def _seed_trace_dir(brr_dir: Path, rel: str) -> Path:
    path = brr_dir / rel
    path.mkdir(parents=True, exist_ok=True)
    (path / "stdout.txt").write_text("ok\n", encoding="utf-8")
    return path


def test_cleanup_traces_on_success_removes_dirs_and_meta(tmp_path):
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    runs_dir.mkdir(parents=True)
    trace_a = _seed_trace_dir(brr_dir, "traces/daemon-run/evt-1-attempt-1")
    trace_b = _seed_trace_dir(brr_dir, "traces/daemon-run/evt-1-attempt-2")
    task = Run(id="task-clean", event_id="evt-1", body="x", status="done")
    task.meta["trace_dirs"] = (
        "traces/daemon-run/evt-1-attempt-1, traces/daemon-run/evt-1-attempt-2"
    )
    task.save(runs_dir)

    daemon._cleanup_traces_on_success(brr_dir, runs_dir, task)

    assert not trace_a.exists()
    assert not trace_b.exists()
    assert "trace_dirs" not in task.meta
    reloaded = Run.from_file(runs_dir / task.id / "run.md")
    assert reloaded is not None
    assert "trace_dirs" not in reloaded.meta


def test_cleanup_traces_on_success_keeps_on_failure(tmp_path):
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    runs_dir.mkdir(parents=True)
    trace = _seed_trace_dir(brr_dir, "traces/daemon-run/evt-2-attempt-1")
    for status in ("error", "conflict"):
        task = Run(id=f"task-{status}", event_id="evt-2", body="x", status=status)
        task.meta["trace_dirs"] = "traces/daemon-run/evt-2-attempt-1"
        task.save(runs_dir)

        daemon._cleanup_traces_on_success(brr_dir, runs_dir, task)

        assert trace.exists(), f"trace removed on status={status}"
        assert task.meta.get("trace_dirs"), f"meta cleared on status={status}"


def test_start_allows_same_pid_during_reexec(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    calls: list[str] = []

    monkeypatch.setenv("BRR_REEXEC", "1")
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: daemon.os.getpid())
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: calls.append("write-pid"))
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: calls.append("clear-pid"))
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    def stop_on_scan(_inbox):
        calls.append("scan")
        raise StopIteration

    monkeypatch.setattr(daemon.protocol, "list_pending", stop_on_scan)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    # Three scans: the boot interrupted-run marker (#316) and the boot
    # spawn-reconciliation sweep (#311) each scan first and their
    # must-not-block-boot guards swallow the fixture's StopIteration;
    # the main loop's own first scan then raises it for real.
    assert calls == ["write-pid", "scan", "scan", "scan", "clear-pid"]


def test_start_rejects_existing_pid_without_reexec(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    monkeypatch.delenv("BRR_REEXEC", raising=False)
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: daemon.os.getpid())

    with pytest.raises(SystemExit) as exc:
        daemon.start(tmp_path)

    assert "daemon already running" in str(exc.value)


def test_start_rejects_different_pid_during_reexec(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    monkeypatch.setenv("BRR_REEXEC", "1")
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: daemon.os.getpid() + 1)

    with pytest.raises(SystemExit) as exc:
        daemon.start(tmp_path)

    assert "daemon already running" in str(exc.value)


def test_dev_reload_mode_from_config_reexecs_at_idle_boundary(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    order: list[str] = []

    class FakeWatcher:
        last_changed = ["package/daemon.py"]

        def changed(self):
            order.append("watch")
            return True

    def _stop_after_reexec():
        order.append("reexec")
        raise StopIteration

    monkeypatch.setattr(
        daemon.reload_mod.DevReloadWatcher,
        "for_repo",
        classmethod(lambda cls, _repo_root: order.append("watcher") or FakeWatcher()),
    )
    monkeypatch.setattr(daemon.reload_mod, "reexec", _stop_after_reexec)
    monkeypatch.setattr(
        daemon.reload_mod,
        "format_dev_reload_breadcrumb",
        lambda paths: order.append(f"breadcrumb:{paths}") or "dev-reload: re-exec",
    )
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: order.append("write-pid"))
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: order.append("clear-pid"))
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {"dev_reload": True})
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox: (_ for _ in ()).throw(AssertionError("should reexec first")),
    )
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert order == [
        "write-pid", "watcher", "watch",
        "breadcrumb:['package/daemon.py']", "reexec", "clear-pid",
    ]


def test_dev_reload_reexecs_only_after_task_push(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-reload",
        "status": "pending",
        "_path": tmp_path / ".brr" / "inbox" / "evt-reload.md",
    }
    event["_path"].write_text(
        "---\nid: evt-reload\nstatus: pending\n---\nhelp\n",
        encoding="utf-8",
    )
    order: list[str] = []
    order_lock = threading.Lock()

    def record(label: str) -> None:
        # Worker thread and main thread both append; the lock keeps
        # the timeline observable without rare interleaving artefacts.
        with order_lock:
            order.append(label)

    class FakeWatcher:
        def __init__(self):
            self.calls = 0
            self.last_changed: list = []

        def changed(self):
            self.calls += 1
            record(f"watch:{self.calls}")
            return self.calls == 2

    watcher = FakeWatcher()

    def _stop_after_reexec():
        raise StopIteration

    monkeypatch.setattr(
        daemon.reload_mod.DevReloadWatcher,
        "for_repo",
        classmethod(lambda cls, _repo_root: watcher),
    )
    monkeypatch.setattr(daemon.reload_mod, "reexec", _stop_after_reexec)
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: record("write-pid"))
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: record("clear-pid"))
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    # Short scan interval so the loop's second iteration (where the
    # watcher reports a change and the now-empty pool triggers
    # reexec) lands quickly after the worker thread finishes.
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.05)
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox: [event],
    )
    monkeypatch.setattr(
        daemon.protocol,
        "set_status",
        lambda _event, status: record(f"status:{status}"),
    )

    def fake_run_worker(*_args, **_kwargs):
        record("worker")
        return Run(
            id="task-reload",
            event_id="evt-reload",
            body="help",
            status="done",
        )

    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(
        daemon,
        "publish",
        lambda *_args, **_kwargs: record("push"),
    )
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path, dev_reload=True)

    # What this test is named for, and the only thing that must hold: the
    # reexec never happens until the finished task has been published. The
    # daemon latches "changed" and waits for the pool to drain, so a push in
    # flight defers the reexec rather than losing it.
    #
    # The *interleaving* is not deterministic, and asserting one made this test
    # flaky under load. The main thread polls the watcher every _SCAN_INTERVAL
    # (0.05s) while the worker thread runs on its own schedule; when the worker
    # needs more than one tick to reach `push`, extra `watch:N` ticks appear —
    # correct behaviour that a hard-coded list reads as a regression. Assert
    # the causal contract; let the scheduler be the scheduler.
    causal = [step for step in order if not step.startswith("watch:")]
    assert causal == [
        "write-pid",
        "status:processing",
        "worker",
        "status:done",
        "push",
        "clear-pid",
    ]
    # The watcher is polled at least until it reports a change (call 2), and
    # the reexec — the StopIteration above, immediately before clear-pid — is
    # strictly after the push.
    assert order.index("push") < order.index("clear-pid")
    assert order.count("watch:1") == 1 and "watch:2" in order
    assert watcher.calls >= 2


def test_max_concurrent_spawns_config_parsing():
    """``spawn.max_concurrent`` generalizes the old spawn cap-of-1 to a
    small configurable pool (kb/design-multi-workstream-concurrency.md
    'Ranked moves' #1; maintainer call 2026-07-08: 'set the concurrency to
    4 or something already'). Default 4; clamped to at least 1 so a
    misconfigured 0/negative value can't silently wedge every `spawn:`
    request back into the sequential queue; a non-numeric value falls back
    to the default rather than crashing the daemon loop.
    """
    assert daemon._max_concurrent_spawns({}) == 4
    assert daemon._max_concurrent_spawns({"spawn.max_concurrent": 2}) == 2
    assert daemon._max_concurrent_spawns({"spawn.max_concurrent": 0}) == 1
    assert daemon._max_concurrent_spawns({"spawn.max_concurrent": -3}) == 1
    assert daemon._max_concurrent_spawns({"spawn.max_concurrent": "bogus"}) == 4
    assert daemon._max_concurrent_spawns({"spawn.max_concurrent": True}) == 4


def test_concurrent_spawn_pool_respects_configured_width(tmp_path, monkeypatch):
    """Multiple `spawn:` events dispatch up to `spawn.max_concurrent` at
    once — the old shape allowed exactly one concurrent spawn no matter how
    many `spawn:` requests were pending; this exercises the generalized
    pool (kb/design-multi-workstream-concurrency.md 'slice 1') with three
    candidates against a configured width of 2, asserting the third waits
    for a slot rather than either queuing sequentially (old behavior) or
    all three running at once (an unbounded pool).
    """
    write_repo_scaffold(tmp_path)

    lock = threading.Lock()
    running_ids: set[str] = set()
    started_two = threading.Event()
    release = threading.Event()

    def fake_run_worker(event, *_args, **_kwargs):
        eid = event["id"]
        with lock:
            running_ids.add(eid)
            if len(running_ids) >= 2:
                started_two.set()
        release.wait(timeout=5)
        with lock:
            running_ids.discard(eid)
        return Run(
            id=f"task-{eid}", event_id=eid, body="spawned",
            status="done", meta={"worker": True},
        )

    checked = threading.Event()

    def fake_fire_due_schedules(*_a, **_k):
        # Called every main-loop tick regardless of busy/idle state — the
        # one hook available to observe pool state and stop the loop
        # without racing the worker threads over StopIteration.
        if started_two.is_set() and not checked.is_set():
            checked.set()
            time.sleep(0.05)
            with lock:
                snapshot = set(running_ids)
            assert len(snapshot) == 2, (
                f"expected exactly 2 concurrent at pool width 2, got {snapshot}"
            )
            release.set()
            # Let the freed slots pick up the third candidate and finish
            # before stopping the loop.
            time.sleep(0.3)
            raise StopIteration

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(
        daemon.conf, "load_config", lambda _root: {"spawn.max_concurrent": 2},
    )
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_notify_spawn_parent", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_fire_due_schedules", fake_fire_due_schedules)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    for i in range(3):
        protocol.create_event(
            tmp_path / ".brr" / "inbox", "spawn", f"spawned work {i}",
            spawn_immediate=True, worker=True, environment="worktree",
        )

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert checked.is_set()


def test_concurrent_spawn_does_not_duplicate_dispatch_of_same_event(
    tmp_path, monkeypatch,
):
    """A single `spawn:` event must be dispatched exactly once, even when
    the pool has more than one open slot and several ticks pass before it
    completes.

    Root-caused live 2026-07-08 (run-260708-2010-5sor): one `spawn:` outbox
    dispatch produced 4 concurrent duplicate children, all working the
    identical event, bounded only by `spawn.max_concurrent`. Cause:
    `list_dispatchable`/`list_pending` deliberately keep returning
    "processing"-status events (so a still-running resident event stays
    visible for follow-up-folding) — but the spawn pool's fill loop had no
    check against events already claimed in `active_spawns`, unlike the
    resident dispatch path, which is implicitly guarded by `current is
    None` in memory. With pool width > 1, the same single candidate refilled
    every open slot, tick after tick, until the pool hit its configured cap.
    This pins the fix: with width 4 and only one pending spawn candidate
    that takes several ticks to finish, exactly one child ever gets
    submitted.
    """
    write_repo_scaffold(tmp_path)

    dispatch_count = 0
    dispatch_lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()

    def fake_run_worker(event, *_args, **_kwargs):
        nonlocal dispatch_count
        with dispatch_lock:
            dispatch_count += 1
        started.set()
        release.wait(timeout=5)
        return Run(
            id=f"task-{event['id']}", event_id=event["id"], body="spawned",
            status="done", meta={"worker": True},
        )

    ticks_since_start = 0

    def fake_fire_due_schedules(*_a, **_k):
        nonlocal ticks_since_start
        if started.is_set():
            ticks_since_start += 1
            # Let several ticks elapse with the event still "processing"
            # before releasing the worker and stopping the loop — this is
            # exactly the window the bug needed to over-dispatch.
            if ticks_since_start >= 5:
                release.set()
                time.sleep(0.05)
                raise StopIteration

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(
        daemon.conf, "load_config", lambda _root: {"spawn.max_concurrent": 4},
    )
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_notify_spawn_parent", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_fire_due_schedules", fake_fire_due_schedules)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    protocol.create_event(
        tmp_path / ".brr" / "inbox", "spawn", "spawned work",
        spawn_immediate=True, worker=True, environment="worktree",
    )

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert dispatch_count == 1, (
        f"expected the single spawn event dispatched exactly once, got "
        f"{dispatch_count}"
    )


def test_dev_reload_does_not_stall_concurrent_spawn_dispatch(tmp_path, monkeypatch):
    """A `spawn:` child dispatches alongside a still-running resident
    thought even after the dev-reload watcher has flagged a package
    change — kb/plan-spawn-gap-closure.md "Gap 2", resolved 2026-07-08.
    Only the resident slot (and re-exec itself) still wait on
    ``reload_requested``; the concurrent-spawn slot no longer does, since
    a spawn is a separate subprocess that never touches this process's
    in-memory staleness the way a fresh resident dispatch or re-exec does.
    """
    write_repo_scaffold(tmp_path)
    make_event(tmp_path, eid="evt-resident", body="edit brr itself")

    order: list[str] = []
    order_lock = threading.Lock()

    def record(label: str) -> None:
        with order_lock:
            order.append(label)

    resident_started = threading.Event()
    release_resident = threading.Event()

    class FakeWatcher:
        def __init__(self):
            self.calls = 0
            self.last_changed: list = []

        def changed(self):
            self.calls += 1
            record(f"watch:{self.calls}")
            # Flips true only once resident dispatch is confirmed
            # underway, so reload_requested becomes true while `current`
            # is still busy — the exact shape Gap 2 was about.
            return resident_started.is_set()

    watcher = FakeWatcher()

    def _stop_after_reexec():
        record("reexec")
        raise StopIteration

    def fake_run_worker(event, *_args, **_kwargs):
        eid = event.get("id")
        if eid == "evt-resident":
            record("resident-start")
            resident_started.set()
            release_resident.wait(timeout=5)
            record("resident-done")
            return Run(
                id="task-resident", event_id=eid, body="edit brr itself",
                status="done",
            )
        record("spawn-run")
        return Run(
            id="task-spawn", event_id=eid, body="spawned work",
            status="done", meta={"worker": True},
        )

    monkeypatch.setattr(
        daemon.reload_mod.DevReloadWatcher,
        "for_repo",
        classmethod(lambda cls, _repo_root: watcher),
    )
    monkeypatch.setattr(daemon.reload_mod, "reexec", _stop_after_reexec)
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: record("write-pid"))
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: record("clear-pid"))
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: record("push"))
    monkeypatch.setattr(
        daemon, "_notify_spawn_parent", lambda *_a, **_k: record("notify"),
    )
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    def _inject_spawn_once_resident_running() -> None:
        resident_started.wait(timeout=5)
        protocol.create_event(
            tmp_path / ".brr" / "inbox", "spawn", "spawned work",
            spawn_immediate=True, worker=True, environment="worktree",
        )
        # Give the loop a couple of ticks to observe reload_requested
        # flip true and still dispatch the spawn before unblocking the
        # resident thought.
        time.sleep(0.15)
        release_resident.set()

    injector = threading.Thread(target=_inject_spawn_once_resident_running)
    injector.start()

    with pytest.raises(StopIteration):
        daemon.start(tmp_path, dev_reload=True)
    injector.join(timeout=5)

    assert "resident-start" in order
    assert "spawn-run" in order
    # The spawn dispatched (and ran) *before* the resident thought wound
    # down, and reexec waited for both — proof reload_requested still
    # holds the resident slot but no longer holds the concurrent-spawn
    # slot.
    assert order.index("spawn-run") < order.index("resident-done")
    assert order.index("resident-done") < order.index("reexec")


def test_publish_runs_with_task_meta_for_pr_rebase(tmp_path, monkeypatch):
    """The publish kernel reads ``publish_branch`` + ``expected_remote_oid``
    directly from ``task.meta`` (no extra threading from the worker)."""
    task = Run(
        id="task-lease",
        event_id="evt-lease",
        body="rebase",
        status="done",
        source="github",
        conversation_key="github:owner/repo#17",
        meta={
            "publish_branch": "brr/deliver-pr-rebase",
            "target_branch": "brr/deliver-pr-rebase",
            "expected_remote_oid": "6c1ca158d19c6ba40c06e8a46f7c338ada056246",
        },
    )
    monkeypatch.setattr(daemon, "_run_worker", lambda *_a, **_k: task)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda *_a, **_k: None)
    captured: dict = {}

    def fake_publish(repo, t):
        captured["repo"] = repo
        captured["publish_branch"] = t.meta.get("publish_branch")
        captured["expected_remote_oid"] = t.meta.get("expected_remote_oid")

    monkeypatch.setattr(daemon, "publish", fake_publish)

    event = {"id": "evt-lease", "source": "github", "body": "rebase"}
    daemon._run_worker_and_finalize(event, tmp_path, tmp_path / ".brr", {}, 0)

    assert captured["publish_branch"] == "brr/deliver-pr-rebase"
    assert (
        captured["expected_remote_oid"]
        == "6c1ca158d19c6ba40c06e8a46f7c338ada056246"
    )


def test_worker_finalize_tolerates_gate_cleanup_after_response(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-cleaned", body="answer first")

    def fake_run_worker(ev, *_args, **_kwargs):
        daemon._set_event_status_if_present(ev, "done")
        ev["_path"].unlink()
        return Run(
            id="task-cleaned",
            event_id=ev["id"],
            body=ev["body"],
            source=ev["source"],
            status="done",
        )

    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_args, **_kwargs: None)

    task = daemon._run_worker_and_finalize(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"


# ── Forge URL inference ──────────────────────────────────────────────
#
# The URL-template logic itself is covered exhaustively in
# tests/test_forges.py. ``daemon._forge_view_url`` is a thin wrapper
# that reads the remote URL via ``gitops``, reads forge overrides from
# ``.brr/config``, and swallows any failure into ``None``. The tests
# below only cover those wrapper-specific responsibilities.


def test_forge_view_url_feeds_remote_and_config_overrides_to_forges(monkeypatch, tmp_path):
    """The wrapper reads the remote URL via gitops and the
    ``forge.kind`` / ``forge.url_base`` overrides via the config
    loader, then delegates to ``forges.view_branch_url``. This guards
    the *plumbing* — that the wrapper still wires the right inputs
    together — without re-testing URL templating."""
    monkeypatch.setattr(
        daemon.gitops, "remote_url",
        lambda _repo, _remote: "git@git.internal.example.com:team/repo.git",
    )
    monkeypatch.setattr(
        daemon.conf, "load_config",
        lambda _repo: {
            "forge.kind": "gitlab",
            "forge.url_base": "https://gitlab.example.com",
        },
    )
    captured: dict = {}

    def fake_view_branch_url(url, branch, **kwargs):
        captured["args"] = (url, branch)
        captured["kwargs"] = kwargs
        return "https://gitlab.example.com/team/repo/-/tree/feature/foo"

    monkeypatch.setattr(daemon.forges, "view_branch_url", fake_view_branch_url)

    url = daemon._forge_view_url(tmp_path, "origin", "feature/foo")

    assert url == "https://gitlab.example.com/team/repo/-/tree/feature/foo"
    assert captured["args"] == (
        "git@git.internal.example.com:team/repo.git", "feature/foo",
    )
    assert captured["kwargs"] == {
        "override_kind": "gitlab",
        "override_url_base": "https://gitlab.example.com",
    }


def test_forge_view_url_returns_none_when_remote_missing(monkeypatch, tmp_path):
    """No remote URL means nothing to template against — the wrapper
    short-circuits to ``None`` rather than calling ``forges`` with
    ``None``."""
    monkeypatch.setattr(daemon.gitops, "remote_url", lambda _repo, _remote: None)
    called = False

    def _should_not_call(*_a, **_kw):
        nonlocal called
        called = True
        return "should not happen"

    monkeypatch.setattr(daemon.forges, "view_branch_url", _should_not_call)

    assert daemon._forge_view_url(tmp_path, "origin", "main") is None
    assert called is False


def test_forge_view_url_swallows_exceptions(monkeypatch, tmp_path):
    """The push has already succeeded by the time we reach
    ``_forge_view_url``; a missing link is never worth failing the
    task over, so any exception in the resolve chain returns
    ``None``."""
    def _boom(*_a, **_kw):
        raise RuntimeError("git binary exploded")

    monkeypatch.setattr(daemon.gitops, "remote_url", _boom)

    assert daemon._forge_view_url(tmp_path, "origin", "main") is None


# ── §8 re-alignment: success-signal axis on _result_satisfied_delivery ──


def _result(ok=True, has_response=False, missing=()):
    """Tiny stand-in for runner.RunnerResult covering the fields read by
    ``_result_satisfied_delivery``."""
    class _R:
        pass
    r = _R()
    r.ok = ok
    r.has_response = has_response
    r.missing_artifacts = list(missing)
    return r


def test_result_satisfied_delivery_picks_current_reply_signal():
    """A stdout reply on the current thread is one satisfying signal; it
    wins over commit/outbound and identifies as ``current_reply``."""
    event = {"source": "telegram"}
    stats = {"current": 1, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(
        _result(has_response=True), stats, event,
    )
    assert ok is True
    assert signal == "current_reply"


def test_result_satisfied_delivery_picks_outbox_current_reply_signal():
    """An outbox-only current-thread interim counts as success even
    without stdout. Preserves the existing shipped behavior, now with
    the named signal so the card can reflect it."""
    event = {"source": "telegram"}
    stats = {"current": 1, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "current_reply"


def test_result_satisfied_delivery_recognises_other_thread_reply():
    """A folded-in reply to a sibling event (no current-thread reply)
    is a successful delivery — §6 says events go to threads, not stdout.
    Previously this read as a silent drop."""
    event = {"source": "telegram"}
    stats = {"current": 0, "other": 1, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "other_reply"


def test_result_satisfied_delivery_recognises_outbound_gate_send():
    """A `gate:` out-of-bound message is a delivery event — a co-maintainer
    that pinged a forge or chat from a scheduled wake didn't fail just
    because the current thread had no reply."""
    event = {"source": "schedule"}
    stats = {"current": 0, "other": 0, "outbound": 1}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "outbound"


def test_result_satisfied_delivery_recognises_respawn_signal():
    """A parked respawn is an explicit success signal: the current run handed the
    work to a new Shell/Core instead of silently producing no output."""
    event = {"source": "telegram"}
    stats = {"current": 0, "other": 0, "outbound": 0, "respawn": 1}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "respawn"


def test_result_satisfied_delivery_recognises_commit_signal():
    """A run that committed new work on the worktree branch is a
    successful run, even without any reply event — §6's commit signal."""
    event = {"source": "telegram"}
    stats = {"current": 0, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(
        _result(), stats, event, has_new_commit=True,
    )
    assert ok is True
    assert signal == "commit"


def test_result_satisfied_delivery_internal_event_passes_without_reply():
    """Internal-source events (schedule fires) have no user thread to
    close, so a clean exit with no signal still resolves as ``internal``
    success. Preserves the shipped behavior with the named signal."""
    event = {"source": "schedule"}
    stats = {"current": 0, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "internal"


def test_result_satisfied_delivery_user_event_without_signal_fails():
    """The §6 invariant: silence on a user-addressed event is failure.
    No reply, no commit, no internal-event exemption → satisfied=False
    so the failure-path writes a terminal note instead of swallowing
    the request."""
    event = {"source": "telegram"}
    stats = {"current": 0, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is False
    assert signal == ""


def test_result_satisfied_delivery_runner_error_fails():
    """A runner.ok=False result is operational failure regardless of
    any output stats — those didn't come from the failed attempt."""
    event = {"source": "telegram"}
    stats = {"current": 5, "other": 5, "outbound": 5}
    ok, signal = daemon._result_satisfied_delivery(
        _result(ok=False), stats, event, has_new_commit=True,
    )
    assert ok is False
    assert signal == ""


def test_result_satisfied_delivery_missing_artifact_fails():
    """A missing required artifact means the runner didn't validate —
    treat as failure even if other output paths fired."""
    event = {"source": "telegram"}
    stats = {"current": 1, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(
        _result(missing=["foo"]), stats, event,
    )
    assert ok is False
    assert signal == ""


def test_post_delivery_attend_skips_when_gate_not_configured(tmp_path, monkeypatch):
    """The daemon dwell is a real gate behavior, not a unit-test tax.

    A direct worker test has no configured Telegram gate, so even with the
    default-positive seconds knob the helper returns before sleeping.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    event = {"id": "evt-a", "source": "telegram", "status": "done"}
    task = Run(
        id="run-a",
        event_id="evt-a",
        body="answer",
        source="telegram",
        conversation_key="telegram:42:",
    )
    monkeypatch.setattr(
        daemon.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("slept")),
    )

    result = daemon._post_delivery_attend(
        daemon._WorkerEmit(brr_dir, "telegram:42:", "evt-a"),
        task,
        event,
        inbox,
        {"delivery.post_delivery_attend_seconds": 30},
        signal="current_reply",
        attempt=1,
    )

    assert result == "skipped"


def test_post_delivery_attend_emits_phase_and_yields_on_pending_event(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    protocol.create_event(
        inbox,
        "telegram",
        "one more thing",
        conversation_key="telegram:42:",
    )
    event = {"id": "evt-a", "source": "telegram", "status": "done"}
    task = Run(
        id="run-a",
        event_id="evt-a",
        body="answer",
        source="telegram",
        conversation_key="telegram:42:",
    )
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, _gate: True)
    monkeypatch.setattr(
        daemon.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("slept")),
    )

    result = daemon._post_delivery_attend(
        daemon._WorkerEmit(brr_dir, "telegram:42:", "evt-a"),
        task,
        event,
        inbox,
        {"delivery.post_delivery_attend_seconds": 30},
        signal="current_reply",
        attempt=1,
    )

    assert result == "pending"
    records = [
        r for r in daemon.conversations.read_records(brr_dir, "telegram:42:")
        if r.get("kind") == "update"
    ]
    assert [r.get("type") for r in records] == ["attending"]
    assert records[0]["reason"] == "watching for follow-up after delivery"


def test_post_delivery_attend_enqueues_follow_up_that_lands_during_dwell(
    tmp_path, monkeypatch,
):
    """#351 interim guarantee: a follow-up arriving during the attendance
    dwell must reach the normal dispatch path — never be polled-and-dropped.

    Pins the failure mode, not the implementation: after the dwell yields on
    the pending follow-up, the event must still be *dispatchable* (it becomes
    the next enqueued run, not a silent drop) and the inbox wake must be
    re-armed so the single-flight loop rescans promptly instead of the
    follow-up being eaten. Models the live loss the maintainer hit: a
    same-thread message sent during the post-run window vanished.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    follow_up_path = protocol.create_event(
        inbox,
        "telegram",
        "oh wait, also Y",
        conversation_key="telegram:42:",
    )
    follow_up = protocol._read_event(follow_up_path)
    event = {"id": "evt-a", "source": "telegram", "status": "done"}
    task = Run(
        id="run-a",
        event_id="evt-a",
        body="answer",
        source="telegram",
        conversation_key="telegram:42:",
    )
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, _gate: True)
    monkeypatch.setattr(
        daemon.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("slept")),
    )
    # The main loop clears the wake at the top of every iteration; while the
    # attending run is still the in-flight `current` it cannot dispatch the
    # follow-up (single-flight). Simulate that consumed signal so the test
    # exercises the seam where the follow-up would otherwise be missed.
    protocol.inbox_wake().clear()

    result = daemon._post_delivery_attend(
        daemon._WorkerEmit(brr_dir, "telegram:42:", "evt-a"),
        task,
        event,
        inbox,
        {"delivery.post_delivery_attend_seconds": 30},
        signal="current_reply",
        attempt=1,
    )

    assert result == "pending"
    # The follow-up was not consumed by attendance: it is still on the normal
    # dispatch path and will become an enqueued run.
    dispatchable_ids = {
        ev.get("id") for ev in protocol.list_dispatchable(inbox)
    }
    assert follow_up["id"] in dispatchable_ids
    # And the loop is woken to pick it up on the next tick rather than waiting
    # out the poll — detecting it during attendance *is* its enqueue.
    assert protocol.inbox_wake().is_set()


def test_run_worker_writes_prompt_to_run_dir(tmp_path, monkeypatch):
    """The daemon persists the assembled prompt in .brr/runs/<run-id>/prompt.md.

    On successful runs the trace directories are cleaned up, but the run dir
    is not, so prompt.md survives — giving a faithful "what did this wake
    see?" answer.
    """
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-prompt")
    worktree_path, _finalized = _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")

    captured_prompts: list[str] = []

    def fake_build_prompt(task, eid, rp, root, **kw):
        p = f"PROMPT run={kw.get('run_id')} evt={eid}"
        captured_prompts.append(p)
        return p

    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", fake_build_prompt)

    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="done\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    prompt_path = tmp_path / ".brr" / "runs" / task.id / "prompt.md"
    assert prompt_path.exists(), f"prompt.md not found at {prompt_path}"
    content = prompt_path.read_text(encoding="utf-8")
    # The first attempt's prompt (not a retry prompt) is what's persisted.
    assert "evt=evt-prompt" in content


# ── _scm_facet (portal-state SCM posture) ────────────────────────────


def test_scm_facet_unknown_without_workdir():
    # No readable worktree → known=False so the back channel stays silent
    # rather than claim a clean tree it never inspected.
    facet = daemon._scm_facet(None, "brr/run-x")
    assert facet == {
        "known": False, "branch": "brr/run-x",
        "unpushed_commits": 0, "modified_files": 0,
    }


def test_scm_facet_reports_dirty_unpushed_tree(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "x\n"})  # no remote → 1 unpushed
    (repo / "b.txt").write_text("dirty\n", encoding="utf-8")  # 1 untracked
    facet = daemon._scm_facet(repo, "brr/run-x")
    assert facet["known"] is True
    assert facet["branch"] == "brr/run-x"
    assert facet["unpushed_commits"] == 1
    assert facet["modified_files"] == 1


def test_write_live_portal_state_wires_produce_inputs(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    task = Run(
        id="run-1", event_id="evt-1", body="", source="telegram",
        meta={"branch_name": "brr/work", "seed_ref": "main"},
    )
    seen = {}

    def fake_live_summary(repo_root, **kwargs):
        seen.update({"repo_root": repo_root, **kwargs})
        return {"known": True, "counts": {"issue": 1},
                "latest_commit": None, "branch": "brr/work", "pr": None}

    monkeypatch.setattr(daemon.relics, "live_summary", fake_live_summary)
    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        work_dir=work_dir,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["produce"]["counts"] == {"issue": 1}
    # A worktree run's own isolated branch (branch_name set) needs no
    # identity filter — no sibling can land a commit there (#575).
    assert seen == {
        "repo_root": work_dir,
        "branch": "brr/work",
        "seed_ref": "main",
        "outbox_dir": outbox_dir,
        "commit_run_id": None,
    }


def test_write_live_portal_state_filters_host_run_by_identity(tmp_path, monkeypatch):
    """#575: a *host* run (no ``branch_name``) measures the shared checkout
    via ``collection_scope``'s fallback, so the live facet must pass this
    run's own id through — otherwise a concurrent sibling's mid-run commits
    would flash as this run's produce before closeout ever applies the
    filter."""
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    task = Run(id="run-host-1", event_id="evt-1", body="", source="telegram")
    seen = {}

    def fake_live_summary(repo_root, **kwargs):
        seen.update({"repo_root": repo_root, **kwargs})
        return {"known": False}

    monkeypatch.setattr(daemon.relics, "live_summary", fake_live_summary)
    monkeypatch.setattr(daemon.relics, "collection_scope", lambda _meta, _wd: ("main", "abc123"))
    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        work_dir=work_dir,
    )

    assert seen["commit_run_id"] == "run-host-1"


# ── _resources_facet (portal-state work-status posture) ──────────────


def test_resources_facet_quota_known_when_summary_present():
    facet = daemon._resources_facet("weekly 42% - resets 3d")
    assert facet["quota"]["status"] == "known"
    assert facet["quota"]["summary"] == "weekly 42% - resets 3d"
    # The level facets with no collector wired for this medium advertise
    # themselves as unimplemented and whether they are required, so a future
    # wake sees the slot and its weight.
    assert facet["spend"]["status"] == "unimplemented"
    assert facet["spend"]["required"] is True
    assert facet["context_window"]["status"] == "unimplemented"
    assert facet["context_window"]["required"] is True
    assert facet["coexisting_runs"]["status"] == "unimplemented"
    assert facet["coexisting_runs"]["required"] is False


def test_resources_facet_coexisting_known_when_siblings_passed():
    """Explicit passthrough: ``_resources_facet`` forwards ``coexisting`` to
    ``facets.build`` unchanged (the wiring under test is the call site in
    ``_write_live_portal_state`` below, not this thin wrapper)."""
    facet = daemon._resources_facet(
        "weekly 42%",
        coexisting=[{"run_id": "run-b", "label": "other work"}],
    )
    assert facet["coexisting_runs"]["status"] == "known"
    assert "other work" in facet["coexisting_runs"]["summary"]


# ── _write_live_portal_state (coexisting_runs ← presence registry) ───────────


def test_write_live_portal_state_coexisting_runs_reflects_presence(tmp_path):
    """``brr_dir`` wires a *live*, heartbeat-refreshed sibling-run read —
    the same presence query already used for the wake-time-only
    ``present_snapshot`` (``_run_worker``'s "Other thoughts awake right
    now"), extended to the portal-state facet a running resident's hooks
    surface after every tool call."""
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    task = Run(id="run-self", event_id="evt-1", body="", source="telegram")

    def _read_facet() -> dict:
        payload = json.loads(
            (outbox_dir / "portal-state.json").read_text(encoding="utf-8")
        )
        return payload["resources"]["coexisting_runs"]

    # No brr_dir given → unchanged legacy behaviour.
    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    assert _read_facet()["status"] == "unimplemented"
    assert _read_facet()["spawn_pool"] == {
        "max_concurrent": 4, "active": None, "available": None,
    }

    # brr_dir given, nobody else present → affirmative-absent.
    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        brr_dir=brr_dir,
    )
    assert _read_facet()["status"] == "absent"
    assert _read_facet()["spawn_pool"] == {
        "max_concurrent": 4, "active": 0, "available": 4,
    }

    # A sibling registers itself (a concurrent spawn, an ad-hoc session) →
    # known, self excluded by run_id.
    presence.register(
        brr_dir, kind="daemon", stream="other", run_id="run-sibling",
        label="fix the frontend build", is_subspawn=True,
    )
    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        brr_dir=brr_dir,
    )
    facet = _read_facet()
    assert facet["status"] == "known"
    assert "fix the frontend build" in facet["summary"]
    assert facet["spawn_pool"] == {
        "max_concurrent": 4, "active": 1, "available": 3,
    }


def test_resources_facet_level_collector_flips_empty_to_absent():
    # With a level collector wired (for example Claude result JSON), an empty spend /
    # context-window slot is affirmative-'absent', not unbuilt 'unimplemented'.
    facet = daemon._resources_facet(None, levels_collector=True)
    assert facet["spend"]["status"] == "absent"
    assert facet["context_window"]["status"] == "absent"
    # A populated level snapshot reads 'known' and carries its summary.
    facet = daemon._resources_facet(
        None,
        levels_collector=True,
        levels={
            "spend": {"summary": "$0.42 this session"},
            "context_window": {"summary": "62% context left"},
            "quota": {"summary": "5h 58% left"},
        },
    )
    assert facet["spend"]["status"] == "known"
    assert facet["spend"]["summary"] == "$0.42 this session"
    assert facet["context_window"]["status"] == "known"
    # A level-source quota wins over the local snapshot path.
    assert facet["quota"]["status"] == "known"
    assert facet["quota"]["summary"] == "5h 58% left"


def test_resources_facet_quota_absent_without_summary():
    # Quota's collector exists but proved nothing for this medium: that is an
    # affirmative-empty 'absent', not an unbuilt 'unimplemented'.
    facet = daemon._resources_facet(None)
    assert facet["quota"]["status"] == "absent"
    assert facet["quota"]["summary"] is None
    assert facet["quota"]["note"]
    facet_blank = daemon._resources_facet("   ")
    assert facet_blank["quota"]["status"] == "absent"


def test_resources_facet_remote_scm_pr_not_created_is_absent():
    facet = daemon._resources_facet(None, branch="brr/feature")
    assert facet["remote_scm"]["status"] == "absent"
    assert facet["remote_scm"]["pr_state"] == "none"
    assert facet["remote_scm"]["branch"] == "brr/feature"
    assert facet["remote_scm"]["pr_number"] is None
    assert "no PR" in facet["remote_scm"]["note"]


def test_resources_facet_remote_scm_known_when_pr_recorded():
    facet = daemon._resources_facet(None, branch="brr/feature", pr_number="207")
    assert facet["remote_scm"]["status"] == "known"
    assert facet["remote_scm"]["pr_state"] == "recorded"
    assert facet["remote_scm"]["pr_number"] == "207"
    assert facet["remote_scm"]["note"] is None


def test_read_pr_control_accepts_bare_number_hash_and_url(tmp_path):
    """The `.pr` control file (2026-07-07 fix for 'remote_scm=absent even
    after the resident created a PR itself mid-run'): the resident can write
    whatever `gh pr create` handed it, not a specific format."""
    for text in (
        "274", "#274", "https://github.com/Gurio/brr/pull/274\n",
        "https://gitlab.com/Gurio/brr/-/merge_requests/274",
    ):
        pr_path = tmp_path / ".pr"
        pr_path.write_text(text, encoding="utf-8")
        assert daemon._read_pr_control(pr_path) == "274"


@pytest.mark.parametrize(
    "text", ["ea35206", "prefix 274", "not-a-url/pull/274", "https://x/pulls/274"],
)
def test_read_pr_control_rejects_sha_and_malformed_content(tmp_path, text):
    pr_path = tmp_path / ".pr"
    pr_path.write_text(text, encoding="utf-8")
    assert daemon._read_pr_control(pr_path) is None


def test_read_pr_control_missing_or_empty_file_is_none(tmp_path):
    assert daemon._read_pr_control(tmp_path / ".pr") is None
    empty = tmp_path / ".pr"
    empty.write_text("   ", encoding="utf-8")
    assert daemon._read_pr_control(empty) is None


def test_resources_facet_threads_runner_catalog():
    facet = daemon._resources_facet(
        None,
        runner_name="codex-mini",
        runner_catalog=[
            {
                "name": "codex-mini",
                "shell": "codex",
                "model": "gpt-5.4-mini",
                "selected": True,
                "availability": "available",
            }
        ],
    )

    catalog = facet["runner"]["catalog"]
    assert catalog[0]["name"] == "codex-mini"
    assert catalog[0]["selected"] is True


def test_repo_label_prefers_event_repo():
    label = daemon._repo_label(
        Path("/tmp/local-brr"),
        {"github_repo": "Gurio/brr"},
        {},
    )

    assert label == "Gurio/brr"


def test_repo_label_falls_back_to_remote(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon.gitops, "default_remote", lambda _root: "origin")
    monkeypatch.setattr(
        daemon.gitops,
        "remote_url",
        lambda _root, _remote: "git@github.com:Gurio/brr.git",
    )

    assert daemon._repo_label(tmp_path, {}, {}) == "Gurio/brr"


def test_repo_label_uses_config_before_directory_name(tmp_path):
    assert daemon._repo_label(tmp_path, {}, {"repo.label": "local/demo"}) == "local/demo"


def test_account_dispatch_inbox_routes_message_event_to_registered_repo(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    write_repo_scaffold(repo_a)
    write_repo_scaffold(repo_b)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
        "account.repo.Gurio/b": str(repo_b),
    }
    ctx = daemon.account.resolve_context(repo_a, cfg)
    protocol.create_event(
        ctx.dispatch_inbox,
        "telegram",
        "route this to repo b",
        repo="Gurio/b",
    )

    targets = daemon._dispatchable_targets(ctx, repo_a, cfg)

    assert len(targets) == 1
    assert targets[0].repo_root == repo_b
    assert targets[0].repo_label == "Gurio/b"
    assert targets[0].inbox_dir == ctx.dispatch_inbox
    assert targets[0].responses_dir == ctx.responses_dir


def test_account_dispatch_inbox_routes_home_label_to_account_home(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
    }
    ctx = daemon.account.resolve_context(repo, cfg)
    protocol.create_event(
        ctx.dispatch_inbox,
        "cli",
        "work across projects",
        repo_label="home",
        trust_tier="owner",
    )

    targets = daemon._dispatchable_targets(ctx, repo, cfg)

    assert len(targets) == 1
    assert targets[0].repo_root == ctx.dominion_repo
    assert targets[0].repo_label == "home"
    assert targets[0].responses_dir == ctx.responses_dir


def test_home_run_uses_host_tree_and_home_run_node(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
    }
    ctx = daemon.account.resolve_context(repo, cfg)
    event = make_event(
        repo,
        eid="evt-home",
        source="cli",
        repo_label="home",
        environment="worktree",
    )
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        daemon.runner,
        "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(
        daemon.sync,
        "refresh_before_run",
        lambda *_args, **_kwargs: pytest.fail("home run attempted repo sync"),
    )
    base_env = envs.get_env("host")

    def fake_invoke(_self, ctx, runner_name, invocation, cfg=None, *, trace=False):
        seen["cwd"] = ctx.cwd
        Path(invocation.response_path).write_text("home done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="home done\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event,
        ctx.dominion_repo,
        ctx.responses_dir,
        cfg,
        0,
        account_context=ctx,
        inbox_dir=ctx.dispatch_inbox,
    )

    assert task.status == "done"
    assert task.env == "host"
    assert task.meta["root_kind"] == "home"
    assert task.meta["forge_lane"] is False
    assert task.meta["branch_source"] == "home:host"
    assert "publish_branch" not in task.meta
    assert seen["cwd"] == ctx.dominion_repo
    state = daemon._persist_run_state_doc(
        ctx, task, repo_label="home", stage="finished", cfg=cfg,
    )
    assert state == ctx.runs_dir / "home" / task.id / "state.md"


def test_home_run_has_no_publish_lane_and_refuses_spawn(tmp_path):
    task = Run(
        id="run-home",
        event_id="evt-home",
        body="account work",
        status="done",
        meta={
            "repo_label": "home",
            "root_kind": "home",
            "publish_branch": "main",
        },
    )
    daemon.publish(tmp_path, task)

    outbox = tmp_path / "outbox"
    accepted = daemon._queue_spawn_request(
        daemon._WorkerEmit(tmp_path, "", "evt-home"),
        task,
        tmp_path / "inbox",
        "evt-home",
        {"spawn": True},
        "parallel work",
        outbox,
    )

    assert accepted is False
    notices = daemon._read_outbox_notices(outbox)
    assert notices and "shared host tree" in notices[0]["text"]


# ── default-branch publisher ────────────────────────────────────────
#
# Runs merge reviewed work into the default branch of the shared
# checkout; publish() only carries per-run branches, so nothing pushed
# it (found live 2026-07-22: origin/main 11 commits behind). The gate
# is branch state, not run env — the first (host-only) cut stranded
# solitary-env continuation merges the very next morning (#327/#61).
# Every "GitHub" here is a real local ``git init --bare`` repo.


def _host_publish_repo(tmp_path):
    """Real repo + bare origin, main pushed and in sync. Returns both."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "main", str(origin)],
        check=True,
    )
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"README.md": "seed\n"})
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"], cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return repo, origin


def _head_oid(repo, ref="main"):
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=repo,
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _host_task(run_id="run-hostpub", env="host", **meta):
    return Run(
        id=run_id, event_id="evt-hostpub", body="merge work",
        status="done", env=env, meta=meta,
    )


def test_host_publish_fast_forwards_default_branch(tmp_path, capsys):
    repo, origin = _host_publish_repo(tmp_path)
    head = commit_files(
        repo, {"work.txt": "merged by host run\n"}, message="host merge",
    )
    # False-positive guard: remote genuinely lacks the commit beforehand.
    assert _head_oid(origin) != head

    daemon.publish_default_branch(repo, _host_task())

    assert _head_oid(origin) == head
    assert "pushing main" in capsys.readouterr().out


def test_host_publish_skips_diverged_remote_with_marker(tmp_path, capsys):
    repo, origin = _host_publish_repo(tmp_path)
    # A second machine pushes to origin/main...
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(other)], check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Other"], cwd=other, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "other@example.com"],
        cwd=other, check=True,
    )
    remote_head = commit_files(
        other, {"remote.txt": "remote-side\n"}, message="remote work",
    )
    subprocess.run(
        ["git", "push", "-q", "origin", "main"], cwd=other, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # ...while the host run merged different local work, and the daemon
    # knows about the divergence (tracking ref is current).
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=repo, check=True)
    commit_files(repo, {"local.txt": "local-side\n"}, message="local work")

    daemon.publish_default_branch(repo, _host_task())

    # No push, no force: origin still at the remote-side head.
    assert _head_oid(origin) == remote_head
    out = capsys.readouterr().out
    assert "[brnrd]" in out and "diverged" in out
    assert "pushing main" not in out


def test_publish_fires_for_solitary_env_when_default_branch_moved(
    tmp_path, capsys,
):
    """The live 2026-07-22 morning shape: a solitary-env continuation run
    merges a reviewed PR into the shared checkout's main — the publisher
    keys on branch state, not run env, so the merge still leaves the
    machine."""
    repo, origin = _host_publish_repo(tmp_path)
    head = commit_files(
        repo, {"work.txt": "merged by solitary continuation\n"},
        message="reviewed merge",
    )
    assert _head_oid(origin) != head

    daemon.publish_default_branch(repo, _host_task(env="solitary"))

    assert _head_oid(origin) == head
    assert "pushing main" in capsys.readouterr().out


def test_publish_noops_for_worktree_env_that_left_main_alone(
    tmp_path, capsys,
):
    """A worktree run that only worked its own branch: main in sync,
    publisher stays silent — env-agnostic must not mean chatty."""
    repo, origin = _host_publish_repo(tmp_path)
    before = _head_oid(origin)

    daemon.publish_default_branch(
        repo, _host_task(env="worktree", publish_branch="brr/topic"),
    )

    assert _head_oid(origin) == before
    assert capsys.readouterr().out == ""


def test_host_publish_never_fires_for_home_root(tmp_path, capsys):
    repo, origin = _host_publish_repo(tmp_path)
    before = _head_oid(origin)
    commit_files(repo, {"work.txt": "home capture net owns this\n"})

    daemon.publish_default_branch(repo, _host_task(root_kind="home"))

    assert _head_oid(origin) == before
    assert capsys.readouterr().out == ""


def test_host_publish_noop_when_nothing_to_push(tmp_path, capsys):
    repo, origin = _host_publish_repo(tmp_path)
    before = _head_oid(origin)

    daemon.publish_default_branch(repo, _host_task())

    assert _head_oid(origin) == before
    assert capsys.readouterr().out == ""


def test_host_env_run_finalize_publishes_default_branch(tmp_path, monkeypatch):
    """e2e: a host-env run through ``_run_worker_and_finalize`` lands its
    default-branch merge on the (bare, local) remote."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "main", str(origin)],
        check=True,
    )
    repo = tmp_path / "repo"
    init_git_repo(repo)
    write_repo_scaffold(repo)
    commit_files(repo, {"README.md": "seed\n", "AGENTS.md": "# Project\n"})
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "main"], cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    event = make_event(repo, eid="evt-hostpub-e2e", environment="host")

    monkeypatch.setattr(
        daemon.runner,
        "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    merged: dict[str, str] = {}
    base_env = envs.get_env("host")

    def fake_invoke(_self, ctx, runner_name, invocation, cfg=None, *, trace=False):
        # The "agent" merges reviewed work into the default branch of the
        # shared checkout — the observed host-run shape.
        (repo / "merged.txt").write_text("reviewed work\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "merged.txt"], cwd=repo, check=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "-m", "merge reviewed work"],
            cwd=repo, check=True,
        )
        merged["head"] = _head_oid(repo)
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("merged\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="merged\n", stderr="", returncode=0,
            trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker_and_finalize(
        event, repo, repo / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    assert task.env == "host"
    assert merged["head"]
    assert _head_oid(origin) == merged["head"]


def test_account_dispatch_keeps_forge_events_on_repo_local_route(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    write_repo_scaffold(repo_a)
    write_repo_scaffold(repo_b)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
        "account.repo.Gurio/b": str(repo_b),
    }
    ctx = daemon.account.resolve_context(repo_a, cfg)
    repo_b_inbox = repo_b / ".brr" / "inbox"
    protocol.create_event(repo_b_inbox, "github", "fix this issue")

    targets = daemon._dispatchable_targets(ctx, repo_a, cfg)

    assert len(targets) == 1
    assert targets[0].repo_root == repo_b
    assert targets[0].repo_label == "Gurio/b"
    assert targets[0].inbox_dir == repo_b_inbox
    assert targets[0].responses_dir == repo_b / ".brr" / "responses"
    assert targets[0].event["repo_label"] == "Gurio/b"


def test_cloud_dispatch_uses_explicit_then_thread_sticky_then_default(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    write_repo_scaffold(repo_a)
    write_repo_scaffold(repo_b)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
        "account.repo.Gurio/b": str(repo_b),
    }
    ctx = daemon.account.resolve_context(repo_a, cfg)
    thread = "cloud:telegram:42:"

    explicit = {
        "source": "cloud",
        "repo": "Gurio/b",
        "cloud_platform": "telegram",
        "cloud_chat_id": 42,
    }
    assert daemon._repo_for_event(
        ctx,
        explicit,
        fallback_repo_root=repo_a,
        fallback_label="Gurio/a",
    ) == (repo_b, "Gurio/b")

    daemon.conversations.append_run(
        repo_b / ".brr",
        thread,
        run_id="run-b",
        event_id="evt-b",
        env="worktree",
        status="finished",
        repo_label="Gurio/b",
    )
    follow_up = {
        "source": "cloud",
        "cloud_platform": "telegram",
        "cloud_chat_id": 42,
    }
    assert daemon._repo_for_event(
        ctx,
        follow_up,
        fallback_repo_root=repo_a,
        fallback_label="Gurio/a",
    ) == (repo_b, "Gurio/b")

    fresh_thread = {
        "source": "cloud",
        "cloud_platform": "telegram",
        "cloud_chat_id": 99,
    }
    assert daemon._repo_for_event(
        ctx,
        fresh_thread,
        fallback_repo_root=repo_a,
        fallback_label="Gurio/a",
    ) == (repo_a, "Gurio/a")


def test_account_starts_one_cloud_gate_on_default_repo_runtime(
    tmp_path, monkeypatch,
):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    write_repo_scaffold(repo_a)
    write_repo_scaffold(repo_b)
    ctx = daemon.account.resolve_context(
        repo_a,
        {
            "repo.label": "Gurio/a",
            "home.path": str(tmp_path / "account-home"),
            "account.repo.Gurio/b": str(repo_b),
        },
    )
    calls = []

    def capture(*args):
        calls.append(args)
        return []

    monkeypatch.setattr(daemon, "_start_gates", capture)

    daemon._start_account_gates(ctx, repo_a)

    cloud_calls = [
        call for call in calls
        if len(call) >= 5 and call[4] == frozenset({"cloud"})
    ]
    assert len(cloud_calls) == 1
    assert cloud_calls[0][0] == repo_a / ".brr"
    assert cloud_calls[0][1] == ctx.dispatch_inbox
    assert cloud_calls[0][2] == ctx.responses_dir


def test_dashboard_dispatch_header_stamps_event_before_repo_routing(tmp_path):
    from brr import wake_request as wake_request_mod

    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    write_repo_scaffold(repo_a)
    write_repo_scaffold(repo_b)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
        "account.repo.Gurio/b": str(repo_b),
    }
    ctx = daemon.account.resolve_context(repo_a, cfg)
    event = protocol.create_event(ctx.dispatch_inbox, "telegram", "dispatch this")
    target = daemon._dispatchable_targets(ctx, repo_a, cfg)[0]
    wake_request_mod.store_pending(
        repo_a / ".brr",
        {
            "request_id": "wake_dispatch",
            "profile": "codex-mini",
            "repo_label": "Gurio/b",
            "environment": "solitary",
        },
    )

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied.repo_root == repo_b
    assert applied.repo_label == "Gurio/b"
    assert applied.event["runner"] == "codex-mini"
    assert applied.event["repo_label"] == "Gurio/b"
    assert applied.event["environment"] == "solitary"
    assert applied.event["dashboard_wake_request_id"] == "wake_dispatch"
    assert wake_request_mod.pending(repo_a / ".brr") is None
    assert wake_request_mod.consumed_ids(repo_a / ".brr") == ["wake_dispatch"]
    # #564: consumption leaves a trace — who spent it, from what source.
    receipt = wake_request_mod.last_receipt(repo_a / ".brr")
    assert receipt["at"]
    assert {k: v for k, v in receipt.items() if k != "at"} == {
        "request_id": "wake_dispatch",
        "source": "telegram",
        "event_id": target.event["id"],
        "profile": "codex-mini",
    }


def test_dashboard_wake_request_schedule_source_does_not_consume(tmp_path):
    """#564: a `source: schedule` event at dispatch time (the director tick
    itself, before any per-run worker fallback runs) must not bind or spend
    a parked dashboard tap — that tap is a promise to the next *interactive*
    wake, not to whichever schedule firing happens to dispatch first."""
    from brr import wake_request as wake_request_mod

    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    write_repo_scaffold(repo_a)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
    }
    ctx = daemon.account.resolve_context(repo_a, cfg)
    protocol.create_event(ctx.dispatch_inbox, "schedule", "director tick")
    target = daemon._dispatchable_targets(ctx, repo_a, cfg)[0]
    wake_request_mod.store_pending(
        repo_a / ".brr",
        {"request_id": "wake_sched", "profile": "codex-mini"},
    )

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied is target
    assert "runner" not in applied.event
    assert "dashboard_wake_request_id" not in applied.event
    assert wake_request_mod.pending(repo_a / ".brr") == {
        "request_id": "wake_sched",
        "profile": "codex-mini",
    }
    assert wake_request_mod.consumed_ids(repo_a / ".brr") == []
    assert wake_request_mod.last_receipt(repo_a / ".brr") is None


def test_apply_dashboard_wake_request_claims_when_parked_shortly_before_event(
    tmp_path,
):
    """#577: parked_at is the server's own tap timestamp, not this mirror
    file's write time — a tap parked a couple of seconds before the event
    it rode in on is claimable even though the mirror itself only appears
    on disk later."""
    from datetime import datetime, timedelta, timezone

    from brr import wake_request as wake_request_mod

    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    write_repo_scaffold(repo_a)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
    }
    ctx = daemon.account.resolve_context(repo_a, cfg)
    protocol.create_event(ctx.dispatch_inbox, "telegram", "dispatch this")
    target = daemon._dispatchable_targets(ctx, repo_a, cfg)[0]
    parked = datetime.now(timezone.utc) - timedelta(seconds=2)
    wake_request_mod.store_pending(
        repo_a / ".brr",
        {
            "request_id": "wake_close_evt", "profile": "codex-mini",
            "requested_at": parked.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied.event["runner"] == "codex-mini"
    assert applied.event["dashboard_wake_request_id"] == "wake_close_evt"
    assert wake_request_mod.pending(repo_a / ".brr") is None
    assert wake_request_mod.consumed_ids(repo_a / ".brr") == ["wake_close_evt"]


def test_apply_dashboard_wake_request_lapses_stale_park_outside_window(tmp_path):
    """#577: a tap parked well before this event's claim window belongs to
    a wake that already came and went — dispatch leaves it unapplied and
    the tap lapses instead of lingering to ambush the next unrelated wake."""
    from datetime import datetime, timedelta, timezone

    from brr import wake_request as wake_request_mod

    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    write_repo_scaffold(repo_a)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
    }
    ctx = daemon.account.resolve_context(repo_a, cfg)
    protocol.create_event(ctx.dispatch_inbox, "telegram", "dispatch this")
    target = daemon._dispatchable_targets(ctx, repo_a, cfg)[0]
    parked = datetime.now(timezone.utc) - timedelta(minutes=10)
    wake_request_mod.store_pending(
        repo_a / ".brr",
        {
            "request_id": "wake_stale_dispatch", "profile": "codex-mini",
            "requested_at": parked.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied is target
    assert "runner" not in applied.event
    assert "dashboard_wake_request_id" not in applied.event
    assert wake_request_mod.pending(repo_a / ".brr") is None
    assert wake_request_mod.consumed_ids(repo_a / ".brr") == ["wake_stale_dispatch"]
    receipt = wake_request_mod.last_receipt(repo_a / ".brr")
    assert receipt["outcome"] == "lapsed"
    assert receipt["profile"] is None


def test_account_run_state_doc_persists_run_snapshot(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {
            "repo.label": "Gurio/brr",
            "home.path": str(tmp_path / "account-home"),
        },
    )
    task = Run(
        id="run-state",
        event_id="evt-state",
        body="please make the state visible",
        source="telegram",
        status="running",
        meta={"runner_name": "codex", "reply_archive": "archived"},
    )

    path = daemon._persist_run_state_doc(
        ctx,
        task,
        repo_label="Gurio/brr",
        stage="created",
    )

    assert path == ctx.runs_dir / "Gurio__brr" / "run-state" / "state.md"
    text = path.read_text(encoding="utf-8")
    assert "run_id: run-state" in text
    assert "repo_label: Gurio/brr" in text
    assert "runner_name: codex" in text
    assert "reply_archive: archived" in text
    # The body no longer restates frontmatter facts as bullets — the
    # non-repetitive-node cut, 2026-07-19.
    assert "- runner:" not in text
    # The local store path is recorded as a dev breadcrumb; with no forge
    # remote on the dominion there is no web URL to surface yet.
    assert task.meta["run_state_path"] == str(path)
    assert "run_state_url" not in task.meta


def test_dispatch_edge_is_recorded_on_both_run_nodes(tmp_path):
    """A spawned child stamps its parent; the parent's own rewrite keeps it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )

    def persist(run_id, stage, meta=None):
        task = Run(
            id=run_id, event_id=f"evt-{run_id}", body="work",
            source="telegram", status="running", meta=dict(meta or {}),
        )
        return daemon._persist_run_state_doc(
            ctx, task, repo_label="Gurio/brr", stage=stage,
        )

    parent = persist("run-parent", "running")
    child = persist(
        "run-child", "done", {"spawn_parent_run_id": "run-parent"},
    )

    assert "parent_run_id: run-parent" in child.read_text(encoding="utf-8")
    assert "child_run_ids: run-child" in parent.read_text(encoding="utf-8")

    # A second child appends rather than replacing, and re-persisting the
    # same child stays idempotent.
    persist("run-child-2", "done", {"spawn_parent_run_id": "run-parent"})
    persist("run-child", "done", {"spawn_parent_run_id": "run-parent"})
    assert (
        "child_run_ids: run-child, run-child-2"
        in parent.read_text(encoding="utf-8")
    )

    # The parent's own closeout rewrite must not drop the accreted half.
    persist("run-parent", "done")
    assert (
        "child_run_ids: run-child, run-child-2"
        in parent.read_text(encoding="utf-8")
    )


def test_dispatch_edge_skips_a_parent_that_left_no_run_node(tmp_path):
    """No document, no fabricated edge — and the child still persists."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(
        id="run-orphan", event_id="evt-orphan", body="work", source="spawn",
        status="done", meta={"spawn_parent_run_id": "run-never-written"},
    )

    path = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="done",
    )

    assert "parent_run_id: run-never-written" in path.read_text(encoding="utf-8")
    assert not (ctx.runs_dir / "Gurio__brr" / "run-never-written").exists()


def test_run_body_captures_the_resident_card_without_daemon_prose(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(id="run-body", event_id="evt-body", body="build it", source="telegram")
    card = tmp_path / ".card"
    body = "## Now\n\nTesting.\n\n## Arc\n\nThe resident wrote this.\n"
    card.write_text(body, encoding="utf-8")

    path = daemon._persist_run_body(
        ctx, task, repo_label="Gurio/brr", card_path=card,
    )

    assert path == ctx.runs_dir / "Gurio__brr" / "run-body" / "body.md"
    assert path.read_text(encoding="utf-8") == body
    assert task.meta["run_body_path"] == str(path)


def test_card_now_projection_keeps_the_full_body_off_the_live_card():
    body = "## Now\n\nDriving tests.\n\n## Arc\n\nA long permanent story."

    assert daemon._card_now_projection(body) == "Driving tests."
    assert daemon._card_now_projection("Plain legacy note") == "Plain legacy note"


def test_boot_janitor_reaps_only_provably_dead_running_state_docs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    now = 1_800_000_000.0

    def state(run_id: str, *, pid: int | None = None) -> Path:
        meta = {"repo_label": "Gurio/brr"}
        if pid is not None:
            meta["pid"] = pid
        task = Run(
            id=run_id, event_id=f"evt-{run_id}", body="work",
            source="telegram", status="running", meta=meta,
        )
        path = daemon._persist_run_state_doc(ctx, task, repo_label="Gurio/brr", stage="running")
        assert path is not None
        os.utime(path, (now, now))
        return path

    closed = state("run-closed")
    ancient = state("run-ancient")
    fresh = state("run-fresh")
    live = state("run-live")
    pid_live = state("run-pid", pid=os.getpid())
    os.utime(ancient, (now - 2 * 86400, now - 2 * 86400))
    os.utime(live, (now - 2 * 86400, now - 2 * 86400))
    os.utime(pid_live, (now - 2 * 86400, now - 2 * 86400))

    ledger = daemon.run_ledger.ledger_path(repo)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"run_id": "run-closed"}) + "\n", encoding="utf-8")
    presence.register(
        daemon.gitops.shared_brr_dir(repo), kind="daemon", run_id="run-live",
        pid=os.getpid(), now=now,
    )

    reaped = daemon._reap_zombie_run_state_docs(ctx, now=now)

    assert reaped == [ancient, closed]
    for path in reaped:
        text = path.read_text(encoding="utf-8")
        fields = protocol.parse_frontmatter(text)
        assert fields["status"] == "error"
        assert fields["stage"] == "reaped"
        assert fields["reap_reason"].startswith("boot janitor:")
    for path in (fresh, live, pid_live):
        assert protocol.parse_frontmatter(path.read_text(encoding="utf-8"))["status"] == "running"


def test_boot_janitor_reaps_the_run_manifest_store_too(tmp_path):
    """The activity publisher reads manifests, not state docs.

    Until 2026-07-19 the janitor only walked ``state.md``, so a run the daemon
    was killed out from under stayed ``running`` in ``.brr/runs/<id>/run.md``
    forever — and ``cloud.py::_run_activity_records`` publishes exactly the
    pending/running manifests, which is how /activity came to report 279 live
    runs against two real ones. Same proof rules as its twin: presence wins,
    a closed ledger row proves the end, age is the crash backstop.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    now = 1_800_000_000.0
    runs_dir = daemon.gitops.shared_brr_dir(repo) / "runs"

    def manifest(run_id: str, status: str = "running") -> Path:
        task = Run(
            id=run_id, event_id=f"evt-{run_id}", body="work",
            source="telegram", status=status,
        )
        path = task.save(runs_dir)
        os.utime(path, (now, now))
        return path

    closed = manifest("run-closed")
    ancient = manifest("run-ancient")
    pending_ancient = manifest("run-pending", status="pending")
    fresh = manifest("run-fresh")
    live = manifest("run-live")
    done = manifest("run-done", status="done")
    for path in (ancient, pending_ancient, live, done):
        os.utime(path, (now - 2 * 86400, now - 2 * 86400))

    ledger = daemon.run_ledger.ledger_path(repo)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"run_id": "run-closed"}) + "\n", encoding="utf-8")
    presence.register(
        daemon.gitops.shared_brr_dir(repo), kind="daemon", run_id="run-live",
        pid=os.getpid(), now=now,
    )

    reaped = daemon._reap_zombie_run_manifests(ctx, now=now)

    assert sorted(reaped) == sorted([ancient, closed, pending_ancient])
    for path in reaped:
        fields = protocol.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert fields["status"] == "error"
        assert fields["reap_reason"].startswith("boot janitor:")
        assert fields["reaped_at"]
    # A live run, a young one, and an already-terminal one are all untouched —
    # the reaper must never overwrite a real status with a guess.
    assert protocol.parse_frontmatter(fresh.read_text(encoding="utf-8"))["status"] == "running"
    assert protocol.parse_frontmatter(live.read_text(encoding="utf-8"))["status"] == "running"
    assert protocol.parse_frontmatter(done.read_text(encoding="utf-8"))["status"] == "done"


def test_capture_dominion_commits_account_home(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    cfg = {
        "repo.label": "Gurio/brr",
        "home.path": str(tmp_path / "account-home"),
    }
    ctx = daemon.account.resolve_context(repo, cfg)
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=ctx.dominion_repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=ctx.dominion_repo,
        check=True,
    )
    repo_dom = daemon.account.repo_dominion_path(ctx, "Gurio/brr")
    daemon.dominion.seed_account_dominion(repo_dom)
    (repo_dom / "notes.md").write_text("remember this\n", encoding="utf-8")
    task = Run(
        id="run-capture",
        event_id="evt-capture",
        body="capture memory",
        source="telegram",
        status="done",
        meta={"repo_label": "Gurio/brr"},
    )

    daemon._capture_dominion(repo, cfg, task, account_context=ctx)

    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=ctx.dominion_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log == "brnrd-home: capture account memory after run run-capture"


def test_finalize_captures_after_finished_run_state(monkeypatch, tmp_path):
    event = {"id": "evt-final", "source": "telegram", "status": "done"}
    task = Run(
        id="run-final",
        event_id="evt-final",
        body="finish state",
        source="telegram",
        status="done",
        meta={"repo_label": "Gurio/brr"},
    )
    calls: list[tuple[str, str]] = []

    def fake_run_worker(*args, **kwargs):
        return task

    def fake_persist(
        _ctx, persisted_task, *, repo_label, stage, cfg=None,
        work_dir=None, outbox_dir=None,
    ):
        calls.append(("persist", stage))
        persisted_task.meta["run_state_stage"] = stage
        return tmp_path / "state.md"

    def fake_capture(_repo, _cfg, captured_task, *, account_context=None):
        calls.append(("capture", captured_task.meta.get("run_state_stage", "")))

    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda _repo, _task: None)
    monkeypatch.setattr(daemon, "_persist_run_state_doc", fake_persist)
    monkeypatch.setattr(daemon, "_capture_dominion", fake_capture)
    monkeypatch.setattr(daemon, "_retire_internal_event", lambda _event, _responses, **_kw: False)

    daemon._run_worker_and_finalize(
        event,
        tmp_path,
        tmp_path / ".brr" / "responses",
        {},
        0,
        account_context=None,
    )

    assert calls == [("persist", "finished"), ("capture", "finished")]


def test_collect_levels_for_claude_merges_usage_and_result(monkeypatch, tmp_path):
    monkeypatch.setattr(
        daemon.claude_usage,
        "load_or_refresh_snapshot",
        lambda outbox, cwd=None: {
            "source": "claude /usage PTY",
            "quota": {"summary": "session 100% left; week 55% left"},
        },
    )
    monkeypatch.setattr(
        daemon.claude_status,
        "load_snapshot",
        lambda outbox: {
            "source": "claude result JSON",
            "spend": {"summary": "$0.0100 this session"},
            "context_window": {"summary": "95% context left (est)"},
        },
    )

    levels, slots = daemon._collect_levels("claude", tmp_path, tmp_path)

    assert slots == {"quota", "spend", "context_window"}
    assert levels["quota"]["summary"] == "session 100% left; week 55% left"
    assert levels["spend"]["summary"] == "$0.0100 this session"
    assert levels["context_window"]["summary"] == "95% context left (est)"
    assert levels["source"] == "claude /usage PTY + claude result JSON"


def test_run_worker_weaves_same_thread_siblings_into_prompt(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    conv = "telegram:chat:42"
    now = time.time()
    lead_path = tmp_path / ".brr" / "inbox" / "evt-lead.md"
    follow_path = tmp_path / ".brr" / "inbox" / "evt-follow.md"
    lead_path.write_text(
        f"---\nid: evt-lead\nstatus: pending\nsource: telegram\n"
        f"trust_tier: owner\nconversation_key: {conv}\n---\ndoes the voice hold?\n",
        encoding="utf-8",
    )
    follow_path.write_text(
        f"---\nid: evt-follow\nstatus: pending\nsource: telegram\n"
        f"trust_tier: owner\nconversation_key: {conv}\n---\naddress this as changes right away\n",
        encoding="utf-8",
    )
    os.utime(lead_path, (now - 1.0, now - 1.0))
    os.utime(follow_path, (now - 0.5, now - 0.5))
    lead = {
        "id": "evt-lead",
        "status": "pending",
        "body": "does the voice hold?",
        "source": "telegram",
        "trust_tier": "owner",
        "conversation_key": conv,
        "_path": lead_path,
    }
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    captured: dict[str, object] = {}

    def _prompt(task_body, _eid, _rp, _root, **kw):
        captured["task_body"] = task_body
        captured.update(kw)
        return "PROMPT"

    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", _prompt)
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        lead, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    assert "does the voice hold?" in str(captured["task_body"])
    assert "address this as changes right away" in str(captured["task_body"])
    pending = captured.get("pending_events") or []
    assert all(ev.get("id") != "evt-follow" for ev in pending)


def test_run_worker_threads_level_quota_into_prompt(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-level-quota")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", lambda _root, _overrides=None: daemon.runner.runner_profile("claude", _root))
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner_quota,
        "describe_runner_quota",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        daemon,
        "_collect_levels",
        lambda *_a, **_k: (
            {"quota": {"summary": "session 88% left; week 40% left"}},
            frozenset({"quota"}),
        ),
    )
    captured: dict[str, object] = {}

    def _prompt(_task, _eid, _rp, _root, **kw):
        captured.update(kw)
        return "PROMPT"

    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", _prompt)
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    assert captured["runner_quota"] == "session 88% left; week 40% left"


def test_drain_outbox_spawns_without_an_explicit_shell_or_core(tmp_path):
    """``shell:``/``core:`` are optional — the child dispatches on the account
    default. They used to be *required*, and a spawn without them was dropped
    with the only trace a print to the daemon's uncaptured stdout: the prompt
    contract said optional, the code said mandatory, and the resident waited
    for a worker that never existed."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(
        inbox, "telegram", "original task", status="processing",
        conversation_key="telegram:42:",
    )
    event_id = path.stem
    (outbox / "spawn.md").write_text(
        "---\nspawn: true\n---\nbounded side task\n",
        encoding="utf-8",
    )
    task = Run(id="run-parent", event_id=event_id, body="original", source="telegram")
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task, responses, event_id, outbox, inbox, stats=stats,
    )

    assert promoted == 1
    assert stats == {"spawn": 1}
    spawned = [p for p in inbox.glob("*.md") if p.stem != event_id]
    assert len(spawned) == 1
    child = protocol._read_event(spawned[0])
    assert child["body"].strip() == "bounded side task"
    assert child["worker"] is True
    assert child["spawn_immediate"] is True
    # No shell/core keys — dispatch resolves the account default.
    assert "shell" not in child
    assert "core" not in child


def test_refused_spawn_leaves_a_notice_the_running_resident_can_read(tmp_path):
    """A refused directive must never look like a working one. The file is
    deleted either way, so the refusal has to land where the resident reads:
    the portal, not the daemon's stdout."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original", status="processing")
    event_id = path.stem
    (outbox / "spawn.md").write_text(
        "---\nspawn: true\n---\nnested work\n", encoding="utf-8",
    )
    # A worker-stack run: nesting is refused by design.
    task = Run(
        id="run-worker", event_id=event_id, body="original", source="telegram",
        meta={"worker": True},
    )

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, event_id),
        task, responses, event_id, outbox, inbox,
    )

    assert promoted == 0
    notices = daemon._read_outbox_notices(outbox)
    assert len(notices) == 1
    assert "no nested spawns" in notices[0]["text"]


def test_reply_to_a_stale_event_leaves_a_notice(tmp_path):
    """The other silent drop: a reply addressed to an event that isn't pending
    is deleted undelivered."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original", status="processing")
    event_id = path.stem
    (outbox / "reply.md").write_text(
        "---\nevent: evt-does-not-exist\n---\nanswer\n", encoding="utf-8",
    )
    task = Run(id="run-parent", event_id=event_id, body="original", source="telegram")

    daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, event_id),
        task, responses, event_id, outbox, inbox,
    )

    notices = daemon._read_outbox_notices(outbox)
    assert len(notices) == 1
    assert "evt-does-not-exist" in notices[0]["text"]
    assert "NOT delivered" in notices[0]["text"]


def test_worker_boot_prompt_excludes_foreign_pending_events(
    tmp_path, monkeypatch,
):
    """A worker's boot prompt gets the same pending-event isolation as its
    live inbox.json.

    Found live (2026-07-18, first wyrd fleet): the live inbox correctly
    showed a worker zero foreign events, but the boot-prompt snapshot was
    built without ``worker=`` — so the worker's prompt listed two of the
    maintainer's pending telegram messages under "Inbox — other pending
    events" while inbox.json stayed empty. Isolation must hold on both
    surfaces; the prompt is the one the worker actually reads at wake.
    """
    write_repo_scaffold(tmp_path)
    event = make_event(
        tmp_path, eid="evt-worker-child", source="spawn",
        body="bounded worker task",
    )
    event["spawn_immediate"] = True
    event["worker"] = True
    event["environment"] = "worktree"
    # A foreign user message pending in the shared inbox at worker boot.
    protocol.create_event(
        tmp_path / ".brr" / "inbox", "telegram", "user says something private",
    )
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"

    prompt_kwargs: dict = {}

    def fake_prompt(task, eid, rp, root, **kw):
        prompt_kwargs.update(kw)
        return f"PROMPT {eid}"

    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", fake_prompt)
    # Pin runner resolution: every sibling test does this, and without it the
    # test silently depends on a claude/codex CLI being on PATH (absent on CI).
    monkeypatch.setattr(
        daemon.runner,
        "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(
        envs.get_env("worktree").__class__, "invoke", fake_invoke, raising=False,
    )

    task = daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    assert task.status == "done"
    assert prompt_kwargs.get("pending_events") == []


def test_dispatch_edge_backfill_replays_the_ledger_onto_existing_nodes(tmp_path):
    """The edge was recorded in the ledger long before it reached the node."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    for run_id in ("run-old-parent", "run-old-child"):
        daemon._persist_run_state_doc(
            ctx,
            Run(id=run_id, event_id=f"evt-{run_id}", body="work", source="telegram"),
            repo_label="Gurio/brr",
            stage="done",
        )
    ledger = daemon.run_ledger.ledger_path(repo)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "run_id": "run-old-child",
                    "parent_run_id": "run-old-parent",
                    "repo_label": "Gurio/brr",
                },
                # A ledger row whose node was never written links nothing.
                {
                    "run_id": "run-absent",
                    "parent_run_id": "run-old-parent",
                    "repo_label": "Gurio/brr",
                },
                {"run_id": "run-old-parent"},
                "not-json-below",
            )
        )
        + "\nnot json at all\n",
        encoding="utf-8",
    )

    assert daemon._backfill_dispatch_edges(ctx) == 1
    # Replaying it is a no-op, not a duplicated edge.
    assert daemon._backfill_dispatch_edges(ctx) == 0

    node = ctx.runs_dir / "Gurio__brr"
    child = (node / "run-old-child" / "state.md").read_text(encoding="utf-8")
    parent = (node / "run-old-parent" / "state.md").read_text(encoding="utf-8")
    assert "parent_run_id: run-old-parent" in child
    assert "child_run_ids: run-old-child\n" in parent
    assert "run-absent" not in parent


def test_dispatch_edges_survive_a_fleet_closing_at_once(tmp_path):
    """A fleet's children stamp one parent concurrently; no edge is lost."""
    import concurrent.futures

    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    daemon._persist_run_state_doc(
        ctx,
        Run(id="run-fleet", event_id="evt-fleet", body="work", source="telegram"),
        repo_label="Gurio/brr",
        stage="running",
    )
    children = [f"run-child-{index}" for index in range(12)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(
            lambda child: daemon._record_dispatch_edge(
                ctx,
                repo_label="Gurio/brr",
                parent_run_id="run-fleet",
                child_run_id=child,
            ),
            children,
        ))

    text = (ctx.runs_dir / "Gurio__brr" / "run-fleet" / "state.md").read_text(
        encoding="utf-8",
    )
    recorded = daemon.protocol.parse_frontmatter(text)["child_run_ids"]
    assert sorted(item.strip() for item in recorded.split(",")) == sorted(children)


def test_running_stage_reports_execution_not_the_pending_lifecycle(tmp_path):
    """A mid-flight node says "running", not the lifecycle's "pending"."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(
        id="run-mid", event_id="evt-mid", body="work", source="telegram",
        status="pending",
    )

    created = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="created",
    ).read_text(encoding="utf-8")
    assert "status: pending" in created

    running = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="running",
    ).read_text(encoding="utf-8")
    assert "status: running" in running

    # A terminal status is never overwritten by the stage.
    task.status = "done"
    finished = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="running",
    ).read_text(encoding="utf-8")
    assert "status: done" in finished


def test_boot_janitor_reaps_runs_frozen_at_pending_too(tmp_path):
    """The 280-node class: died off the closeout path, still claiming pending."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(
        id="run-frozen", event_id="evt-frozen", body="work", source="telegram",
        status="pending", meta={"repo_label": "Gurio/brr"},
    )
    path = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="created",
    )
    ledger = daemon.run_ledger.ledger_path(repo)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"run_id": "run-frozen"}) + "\n", encoding="utf-8")

    reaped = daemon._reap_zombie_run_state_docs(ctx)

    assert path in reaped
    text = path.read_text(encoding="utf-8")
    assert "status: error" in text
    assert "stage: reaped" in text
    assert "closed ledger row" in text


def test_run_state_doc_carries_produce_and_preserves_it(tmp_path, monkeypatch):
    """The node states its own produce (maintainer, 2026-07-19).

    Until now relics were collected only by ``run_ledger.append_closed_run``
    and rendered only from the ledger API's seven-day window, so a run's own
    permanent document could never say what the run made — and a live run had
    no manifest anywhere. Produce belongs on the frame, and a rewrite that
    cannot re-derive it must preserve rather than erase it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(
        id="run-produce",
        event_id="evt-produce",
        body="make something",
        source="telegram",
        status="running",
        meta={"branch_name": "brr/thing", "seed_ref": "main"},
    )

    monkeypatch.setattr(
        daemon.relics,
        "collect",
        lambda *_args, **_kwargs: [
            {"kind": "commit", "sha": "abc1234def", "subject": "do the thing",
             "url": "https://forge/commit/abc1234"},
            {"kind": "pr", "number": 487, "url": "https://forge/pr/487"},
        ],
    )

    path = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="running",
        work_dir=repo, outbox_dir=None,
    )
    text = path.read_text(encoding="utf-8")
    assert "## Produce" in text
    assert "[abc1234 do the thing](https://forge/commit/abc1234)" in text
    assert "[PR #487](https://forge/pr/487)" in text
    # The fingerprint is stored so the heartbeat can rewrite the node when
    # produce moves, and only then.
    assert task.meta["run_state_produce_fingerprint"]

    # A rewrite from a call site with no work dir in scope must not silently
    # delete an already-proven manifest.
    path = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="finished",
    )
    text = path.read_text(encoding="utf-8")
    assert "[PR #487](https://forge/pr/487)" in text
    assert "stage: finished" in text


def test_run_state_produce_change_detection(tmp_path, monkeypatch):
    """The node is rewritten when produce moves, never on a timer."""
    task = Run(
        id="run-fp", event_id="evt-fp", body="x", status="running",
        meta={"branch_name": "brr/thing"},
    )
    records = [{"kind": "commit", "sha": "aaa", "subject": "one"}]
    monkeypatch.setattr(daemon.relics, "collect", lambda *_a, **_k: records)

    # No fingerprint recorded yet: the first observation is a change.
    assert daemon._run_state_produce_changed(
        task, work_dir=tmp_path, outbox_dir=None) is True

    task.meta["run_state_produce_fingerprint"] = daemon.relics.fingerprint(records)
    assert daemon._run_state_produce_changed(
        task, work_dir=tmp_path, outbox_dir=None) is False

    records.append({"kind": "pr", "number": 9})
    assert daemon._run_state_produce_changed(
        task, work_dir=tmp_path, outbox_dir=None) is True

    # The probe is read-only: it must never convince the next write that it
    # already published something it did not.
    assert task.meta["run_state_produce_fingerprint"] != daemon.relics.fingerprint(records)


def test_refresh_codex_thread_id_reads_live_jsonl_fail_closed(tmp_path):
    task = Run(id="run-codex", event_id="evt-codex", body="x")
    events = tmp_path / ".codex-events.jsonl"
    events.write_text(
        '{"type":"thread.started","thread_id":'
        '"a0d0f1e9-8aeb-4f27-8e3c-f72822288984"}\n',
        encoding="utf-8",
    )

    assert daemon._refresh_codex_thread_id(task, events) == (
        "a0d0f1e9-8aeb-4f27-8e3c-f72822288984"
    )
    assert task.meta["codex_thread_id"] == (
        "a0d0f1e9-8aeb-4f27-8e3c-f72822288984"
    )

    events.write_text(
        '{"type":"thread.started","thread_id":"not-a-uuid"}\n',
        encoding="utf-8",
    )
    task.meta.pop("codex_thread_id")
    assert daemon._refresh_codex_thread_id(task, events) is None
    assert "codex_thread_id" not in task.meta


def test_account_run_state_doc_carries_mood_frontmatter(tmp_path):
    """#566 slice 0: the resident-authored `.mood` first line rides the
    run-state frame at every persist — running stage tracks the live face,
    finished stage keeps the last one worn. Absent file → absent key."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    task = Run(
        id="run-mood", event_id="evt-mood", body="face check",
        source="telegram", status="running", meta={},
    )

    path = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="running", outbox_dir=outbox,
    )
    assert "mood:" not in path.read_text(encoding="utf-8")

    (outbox / ".mood").write_text("fo.cus\nnarration the frame never carries\n")
    daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="finished", outbox_dir=outbox,
    )
    text = path.read_text(encoding="utf-8")
    assert "mood: fo.cus" in text
    assert "narration" not in text


# ── #632 review additions: the layers the PR's own tests never reached ──
#
# The shipped tests all drive ``_render_runner_catalog`` with hand-built rows
# that already carry ``quota_level``. That covers the renderer and nothing
# else: the percent *direction*, the one-read-per-pool invariant, and the two
# daemon call sites were all uncovered, so the wiring could be deleted with
# the suite green. Added in review (run-260724-0546-jnhk), each driven red
# against the shipped source first.


def test_shell_level_label_percent_is_remaining_not_used(tmp_path):
    """``percent`` is REMAINING quota, so 89 renders "89%" — not "11%".

    The whole feature inverts if this ever flips, and inverting it is silent:
    every healthy pool would read exhausted and every dead one healthy. Pinned
    against the live shape ``_quota_snapshot`` actually returns.
    """
    from brr.gates import cloud

    label = cloud._shell_level_label([
        {"label": "5h window", "percent": 89.0, "reset": "11:20am"},
        {"label": "weekly", "percent": 88.0, "reset": "Jul 31"},
    ])
    # Most-constraining window wins, and it is read as remaining.
    assert label == "88%"


def test_shell_level_label_absent_reading_is_none_not_healthy(tmp_path):
    """No window carries a percent ⇒ ``None``, never a fabricated level.

    This is #632's whole point restated at the producer: an absent reading
    that renders as a value is worse than one that renders as nothing.
    Asserts identity with ``None`` so no placeholder string can satisfy it.
    """
    from brr.gates import cloud

    assert cloud._shell_level_label([]) is None
    assert cloud._shell_level_label([{"label": "weekly", "percent": None}]) is None


def test_shell_level_label_zero_remaining_is_exhausted(tmp_path):
    from brr.gates import cloud

    assert cloud._shell_level_label(
        [{"label": "weekly", "percent": 0.0, "reset": "Jul 28"}]
    ) == "exhausted, resets Jul 28"


def test_quota_shell_labels_reads_each_pool_once(tmp_path, monkeypatch):
    """One snapshot read per catalog build, however many profiles share a pool.

    The shipped suite claimed to pin this by setting the same ``quota_level``
    on three fixture rows — which pins the renderer's propagation and would
    pass identically if the snapshot were re-read per profile. This counts the
    reads instead.
    """
    from brr.gates import cloud

    calls = []

    def _fake_snapshot(brr_dir):
        calls.append(brr_dir)
        return [
            {"shell": "codex", "windows": [{"percent": 0.0, "reset": "Jul 28"}]},
            {"shell": "claude", "windows": [{"percent": 88.0}]},
        ]

    monkeypatch.setattr(cloud, "_quota_snapshot", _fake_snapshot)
    labels = cloud.quota_shell_labels(tmp_path)

    assert len(calls) == 1
    assert labels == {"codex": "exhausted, resets Jul 28", "claude": "88%"}


def test_enrich_catalog_quota_stamps_every_row_and_skips_unknown_pools(
    tmp_path, monkeypatch,
):
    """The daemon-side wiring, which had no test at all.

    Without this, either ``_enrich_catalog_quota`` call site in ``_run_worker``
    could be deleted and the suite would stay green — the exact "the number
    exists, the chooser cannot see it" failure #632 was filed about.
    """
    from brr.gates import cloud

    monkeypatch.setattr(
        cloud, "_quota_snapshot",
        lambda brr_dir: [{"shell": "codex", "windows": [{"percent": 0.0}]}],
    )
    catalog = [
        {"name": "codex", "shell": "codex", "quota_source": "codex-local"},
        {"name": "codex-full", "shell": "codex", "quota_source": "codex-local"},
        {"name": "claude", "shell": "claude", "quota_source": "claude-local"},
    ]

    daemon._enrich_catalog_quota(catalog, tmp_path)

    assert catalog[0]["quota_level"] == "exhausted"
    assert catalog[1]["quota_level"] == "exhausted"
    # claude had no reading in this snapshot — unknown stays unknown, and the
    # key must be absent rather than empty so the renderer emits nothing.
    assert "quota_level" not in catalog[2]
