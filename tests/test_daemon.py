"""Tests for the daemon worker after the triage stage was removed."""

import json
import os
import subprocess
import threading
import time
import types
from pathlib import Path

import pytest

from brr import daemon, envs, presence, promises, protocol, release_availability
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


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


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


def test_quota_pacing_status_carries_default_thresholds():
    status = daemon._quota_pacing_status({}, _live_shape_561_levels(), model="opus")

    assert status["low_floor_pct"] == 20.0
    assert status["critical_floor_pct"] == 8.0
    assert status["stretch_factor"] == 3.0


def test_quota_pacing_status_carries_configured_threshold_used_for_floor():
    cfg = {"pacing.quota_low_floor_pct": 35}
    levels = {
        "quota": {
            "buckets": {"week": {"remaining_percentage": 24.0}},
        }
    }

    status = daemon._quota_pacing_status(cfg, levels)

    assert status["low_floor_pct"] == 35.0
    assert status["floor"] == "low"


def test_quota_pacing_status_stays_absent_without_measurement():
    assert daemon._quota_pacing_status({}, None) is None


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


def _stub_wake_runner(monkeypatch, seen_overrides, resolved="codex"):
    """Common runner/prompt/env stubs for the wake-tap worker tests."""
    def fake_resolve(_root, overrides=None):
        seen_overrides.append(overrides)
        name = overrides["runner"] if overrides and overrides.get("runner") else resolved
        return daemon.runner.runner_profile(name, _root)

    monkeypatch.setattr(daemon.runner, "resolve_runner_profile", fake_resolve)
    monkeypatch.setattr(
        daemon.runner, "profile_metadata", lambda name, root=None: {"shell": "codex"},
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")

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


def test_run_worker_reads_the_dispatch_time_claim_verdict(tmp_path, monkeypatch):
    """#733: the worker consumes the verdict `_apply_dashboard_wake_request`
    stamped on the event — it no longer decides anything about a tap.

    The runner override, the "you were asked for" line on the prompt, and
    the run's `wake_request` facet all come off those flat event keys.
    """
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-wake")
    event["runner"] = "codex-mini"
    event["dashboard_wake_request_id"] = "wake_9"
    event["dashboard_wake_request_profile"] = "codex-mini"
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"

    seen_overrides: list[dict | None] = []
    prompt_kwargs: dict = {}

    def fake_prompt(task, eid, rp, root, **kw):
        prompt_kwargs.update(kw)
        return f"PROMPT {eid}"

    _stub_wake_runner(monkeypatch, seen_overrides)
    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", fake_prompt)

    task = daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    assert task.status == "done"
    assert seen_overrides and seen_overrides[0] == {"runner": "codex-mini"}
    # The wake knows it was asked for.
    assert prompt_kwargs["runner_medium"] == (
        "codex-mini (requested from the dashboard dispatch header)"
    )
    assert task.meta["wake_request"] == {
        "requested_profile": "codex-mini",
        "applied": True,
        "reason": None,
        "resolved_profile": "codex-mini",
    }


def test_run_worker_surfaces_a_refused_tap_on_the_run(tmp_path, monkeypatch):
    """#733's other half: a tap that existed and did not apply must not look
    like no tap. "You asked for X, you got Y, because Z" reaches the run via
    `resources.runner.wake_request` (facets.py) — the surface a human reads
    when asking where the dashboard pick went."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-wake-refused")
    event["dashboard_wake_request_id"] = "wake_lapsed"
    event["dashboard_wake_request_profile"] = "codex-mini"
    event["dashboard_wake_request_reason"] = "the tap expired before a wake claimed it"
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"

    seen_overrides: list[dict | None] = []
    _stub_wake_runner(monkeypatch, seen_overrides)
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **kw: "PROMPT",
    )

    task = daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    # Refused ⇒ no override; the wake ran on the default resolve.
    assert seen_overrides and seen_overrides[0] is None
    assert task.meta["wake_request"] == {
        "requested_profile": "codex-mini",
        "applied": False,
        "reason": "the tap expired before a wake claimed it",
        "resolved_profile": "codex",
    }


def test_run_worker_never_claims_a_tap_itself(tmp_path, monkeypatch):
    """The duplicated guard ladder is gone (#733).

    A mirrored tap with no verdict stamped on the event is *not this
    worker's business*: a concurrent `spawn:` child and a crash re-dispatch
    both reach `_run_worker` without passing dispatch, and neither is "the
    next wake the account owner is about to cause". The worker must leave
    the mirror alone and never reach the network — running the ladder here
    a second time is what made #733's expiry invisible.
    """
    from brr import wake_request as wake_request_mod
    from brr.gates import cloud

    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-spawn-child")
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"
    wake_request_mod.store_pending(brr_dir, {"request_id": "wake_untouched"})

    def _never(*args, **kwargs):
        raise AssertionError("the worker must not claim a tap")

    monkeypatch.setattr(cloud, "claim_wake_request", _never)
    monkeypatch.setattr(cloud, "_request", _never)

    seen_overrides: list[dict | None] = []
    _stub_wake_runner(monkeypatch, seen_overrides)
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **kw: "PROMPT",
    )

    task = daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    assert seen_overrides and seen_overrides[0] is None
    # Still armed for the wake it was actually parked for.
    assert wake_request_mod.pending_id(brr_dir) == "wake_untouched"
    assert wake_request_mod.last_receipt(brr_dir) is None
    # No tap was in play *for this wake* — absent, not a miss.
    assert "wake_request" not in task.meta


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
    assert rows[0]["bolt_declaration"] is None
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


def _persist_run_manifest(runs_dir, run_id, status, meta=None):
    Run(
        id=run_id, event_id=f"evt-{run_id}", body="work",
        status=status, meta=dict(meta or {}),
    ).save(runs_dir)


def test_outermost_live_ancestor_run_id_walks_past_a_finalized_middle_run(
    tmp_path,
):
    """#1276: a chain more than one level deep must not stop at the nearest
    ancestor — only an ancestor still open when this run finalizes is a
    safe credit target, and the topmost one is the last of them to close."""
    runs_dir = tmp_path / ".brr" / "runs"
    _persist_run_manifest(runs_dir, "run-grandparent", "running")
    _persist_run_manifest(
        runs_dir, "run-parent", "done",
        {"spawn_parent_run_id": "run-grandparent"},
    )
    child = Run(
        id="run-child", event_id="evt-child", body="work",
        meta={"spawn_parent_run_id": "run-parent"},
    )

    assert (
        daemon._outermost_live_ancestor_run_id(child, runs_dir)
        == "run-grandparent"
    )


def test_outermost_live_ancestor_run_id_none_when_every_ancestor_finalized(
    tmp_path,
):
    runs_dir = tmp_path / ".brr" / "runs"
    _persist_run_manifest(runs_dir, "run-parent", "done")
    child = Run(
        id="run-child", event_id="evt-child", body="work",
        meta={"spawn_parent_run_id": "run-parent"},
    )

    assert daemon._outermost_live_ancestor_run_id(child, runs_dir) is None
    # No parent at all is the same answer, not an error.
    orphan = Run(id="run-orphan", event_id="evt-orphan", body="work")
    assert daemon._outermost_live_ancestor_run_id(orphan, runs_dir) is None
    # An unresolvable parent id (a hand-edited or corrupted chain) degrades
    # the same way — never an exception.
    dangling = Run(
        id="run-dangling", event_id="evt-dangling", body="work",
        meta={"spawn_parent_run_id": "run-does-not-exist"},
    )
    assert daemon._outermost_live_ancestor_run_id(dangling, runs_dir) is None


def test_capture_knowledge_defers_dirty_sweep_credit_to_a_live_ancestor(
    tmp_path, monkeypatch,
):
    """#1276: a strand's own finalization sweep is what actually commits a
    still-running parent's uncommitted kb pages (the dirty scan is
    repo-scoped, not run-scoped). Before the fix, the sweep stamped and
    credited those pages to the strand; the parent's own later window read
    then found nothing, filtered by its own identity trailer. The sweep
    must run under the live ancestor's id instead, and this run must not
    self-credit what it swept under that borrowed identity."""
    runs_dir = tmp_path / ".brr" / "runs"
    _persist_run_manifest(runs_dir, "run-parent", "running")
    child = Run(
        id="run-child", event_id="evt-child", body="child work",
        meta={"spawn_parent_run_id": "run-parent", "kb_start_oid": "a" * 40},
    )
    outbox = tmp_path / ".brr" / "outbox" / child.event_id
    outbox.mkdir(parents=True)

    capture_calls: list[str | None] = []

    def fake_capture(*_args, captured_pages, run_id=None, **_kwargs):
        capture_calls.append(run_id)
        # A dirty scan that is repo-scoped, not run-scoped: this is the
        # parent's uncommitted page, swept because it happened to be dirty.
        captured_pages.append("parent-authored.md")
        return True

    monkeypatch.setattr(daemon.knowledge, "capture", fake_capture)
    monkeypatch.setattr(
        daemon.knowledge, "committed_pages_in_window",
        lambda *_a, **_k: [],
    )

    daemon._capture_knowledge(tmp_path, {}, child, outbox_dir=outbox)

    # The sweep committed under the *parent's* identity, not the strand's.
    assert capture_calls == ["run-parent"]
    # And the strand claims none of what it swept — nothing to defer twice.
    assert daemon.relics.read_reported(outbox) == []


def test_capture_knowledge_still_credits_a_run_with_no_live_ancestor(
    tmp_path, monkeypatch,
):
    """The common case (no parent, or a parent that already finalized) is
    unchanged by #1276's fix: this run sweeps and credits under its own
    identity exactly as before."""
    task = Run(
        id="run-solo", event_id="evt-solo", body="work",
        meta={"kb_start_oid": "a" * 40},
    )
    outbox = tmp_path / ".brr" / "outbox" / task.event_id
    outbox.mkdir(parents=True)

    capture_calls: list[str | None] = []

    def fake_capture(*_args, captured_pages, run_id=None, **_kwargs):
        capture_calls.append(run_id)
        captured_pages.append("own.md")
        return True

    monkeypatch.setattr(daemon.knowledge, "capture", fake_capture)
    monkeypatch.setattr(
        daemon.knowledge, "committed_pages_in_window",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        daemon.knowledge, "kb_page_url", lambda *_a, **_k: None,
    )

    daemon._capture_knowledge(tmp_path, {}, task, outbox_dir=outbox)

    assert capture_calls == ["run-solo"]
    assert daemon.relics.read_reported(outbox) == [
        {"kind": "kb", "path": "own.md"},
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
    of "processing" limbo (here: "done", with the crash recorded in
    `run_outcome:` instead — design-the-post.md §THE FIELD TWO MACHINES
    WRITE) so it stops being immediately
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

    # design-the-post.md §THE FIELD TWO MACHINES WRITE: the crash outcome
    # lands in `run_outcome:`, not `status:` — the letter settles at "done".
    assert event["status"] == "done"
    assert event.get("run_outcome") == "error"
    reread = protocol._read_event(tmp_path / ".brr" / "inbox" / "evt-crash.md")
    assert reread["status"] == "done"
    assert reread.get("run_outcome") == "error"
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
    # design-the-post.md §THE FIELD TWO MACHINES WRITE: the run's outcome
    # goes in its own key now — the letter's own status stays "done".
    assert event.get("run_outcome") == "error"
    response = protocol.read_response(tmp_path / ".brr" / "responses", "evt-2")
    assert response is not None
    assert "environment setup failed: boom" in response
    persisted = Run.from_file(tmp_path / ".brr" / "runs" / task.id / "run.md")
    assert persisted is not None
    assert persisted.status == "error"


def test_presence_label_for_event_never_falls_back_to_task_body():
    """#585: a presence `label` is dashboard chrome, not a content channel.

    The pre-fix shape derived the label as
    ``event.get("summary") or task.body``, so a spawn strand's presence
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


def test_presence_label_for_event_prefers_declared_title_over_summary(tmp_path):
    """#880 §1b: a spawn dispatcher's declared ``title:`` frontmatter — a
    one-line, dispatcher-authored label, not the free-text ``task.body``
    #585 guarded against — wins over ``summary`` and populates the
    presence row from the first heartbeat, before the child has had a
    chance to introduce itself via ``.name``."""
    event = {
        "id": "evt-1", "body": "irrelevant",
        "title": "fix the frontend build",
        "summary": "some other auto-derived text",
    }
    assert daemon._presence_label_for_event(event) == "fix the frontend build"

    # No title ⇒ falls back to summary exactly as before.
    event_no_title = {"id": "evt-2", "summary": "Add labels to live runs"}
    assert daemon._presence_label_for_event(event_no_title) == "Add labels to live runs"


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


def test_poison_outbox_file_does_not_wedge_the_flush_tick(tmp_path, monkeypatch):
    """#1379: one bad staging file must cost itself, not the whole tick.

    Driven through the *real* caller the defect lives in —
    ``_invoke_with_heartbeat``'s boundary-flush poll firing the actual
    ``_emit_flush`` closure ``_run_worker`` built, which calls the real
    ``_drain_outbox`` — not a hand-built ``_drain_outbox(...)`` call. The
    stub runner stages a poison file (crafted to raise inside
    ``_resolve_event_target``, one of the calls the drain's own docstring
    used to claim were guarded and weren't) ahead of a healthy plain-reply
    file, writes the ``.flush`` signal ``_invoke_with_heartbeat`` polls
    for, then sleeps past one ``_FLUSH_POLL_INTERVAL`` tick — long enough
    for the real flush-triggered drain to run while the "runner" is still
    "alive" — and snapshots the outbox *before* returning, isolating this
    from the unrelated post-return recovery drain a few lines below it in
    ``_run_worker``.

    Pre-fix, the poison file's exception would propagate out of
    ``_drain_outbox`` into ``_invoke_with_heartbeat``'s blanket
    ``except Exception: pass``, abandoning the whole tick: the healthy
    file never promoted, ``_write_live_portal_state`` never reached (its
    call sits after the drain in both ``_emit_heartbeat`` and
    ``_emit_flush``), and the file retried identically forever since the
    drain always sorts oldest-mtime-first.
    """
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-1379")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    base_env = envs.get_env("worktree")

    # A targeted poison: only the file addressed to this exact sentinel
    # event id blows up inside `_resolve_event_target` (one of the calls
    # `_drain_outbox`'s own docstring claimed, falsely, were guarded) —
    # the healthy file (no `event:` field, defaults to the current event)
    # is unaffected and drains through the real function.
    real_resolve = daemon._resolve_event_target

    def _boom_resolve(address_sources, raw_target):
        if raw_target == "evt-1379-poison-target":
            raise RuntimeError("synthetic poison for #1379's drain guard test")
        return real_resolve(address_sources, raw_target)

    monkeypatch.setattr(daemon, "_resolve_event_target", _boom_resolve)

    snapshot: dict = {}

    def fake_invoke(_self, ctx, runner_name, invocation, cfg=None, *, trace=False):
        outbox_dir = Path(ctx.outbox_host)
        outbox_dir.mkdir(parents=True, exist_ok=True)
        # Oldest first — the exact shape that wedges the pre-fix drain
        # (both it and the pending-count lister sort oldest-mtime-first).
        poison = outbox_dir / "poison.md"
        poison.write_text(
            "---\nevent: evt-1379-poison-target\n---\nnever delivered\n",
            encoding="utf-8",
        )
        old = time.time() - 5
        os.utime(poison, (old, old))
        healthy = outbox_dir / "healthy.md"
        healthy.write_text("plain reply, no frontmatter\n", encoding="utf-8")
        newer = time.time() - 4
        os.utime(healthy, (newer, newer))
        (outbox_dir / ".flush").write_text("tok-1379\n", encoding="utf-8")
        # > 1 `_FLUSH_POLL_INTERVAL` (1.0s) tick, so the real poll loop in
        # `_invoke_with_heartbeat` observes the flush signal and fires the
        # real `_emit_flush` while this "runner" is still "alive".
        time.sleep(1.3)
        poisoned_dir = outbox_dir / ".poisoned"
        processed_dir = outbox_dir / ".processed"
        snapshot["poisoned"] = (
            sorted(p.name for p in poisoned_dir.glob("*"))
            if poisoned_dir.is_dir() else []
        )
        snapshot["processed"] = (
            sorted(p.name for p in processed_dir.glob("*"))
            if processed_dir.is_dir() else []
        )
        state_path = outbox_dir / "portal-state.json"
        snapshot["portal_state"] = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists() else None
        )
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
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 1,
    )

    # The run itself completed — no uncaught exception blew through
    # `_run_worker` (the pre-fix failure mode the post-return recovery
    # drain would have hit a second time, this time with nothing to catch
    # it).
    assert task.status == "done"

    # Snapshotted *during* the flush tick, before the post-return recovery
    # drain runs — this is specifically the `_emit_flush` -> `_drain_outbox`
    # path #1379 is about.
    assert snapshot["poisoned"] == ["poison.md"], (
        "the poison file must be quarantined off the drain's own glob, "
        "not retried forever nor left drifting in the live outbox dir"
    )
    assert snapshot["processed"] == ["healthy.md"], (
        "the healthy file must still drain in the same tick — one file's "
        "failure costs that file, not its neighbours"
    )
    portal_state = snapshot["portal_state"]
    assert portal_state is not None, (
        "_write_live_portal_state must still run this tick — pre-fix, the "
        "poison file's exception abandoned the whole tick before this call"
    )
    assert portal_state["outbound"]["replies_current"] == 1
    notice_texts = [n.get("text", "") for n in portal_state.get("notices", [])]
    assert any(
        "RuntimeError" in text and "poison.md" in text
        for text in notice_texts
    ), f"no notice named the poison file's exception + filename: {notice_texts}"


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
    bounded strand task kept re-triggering the Stop-hook fold-in-or-explain
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


def test_pending_event_record_carries_local_attachment_path(tmp_path):
    """A folded-in event's downloaded attachment gets an openable path.

    #1156: the bytes for a *pending, not-yet-woken* event are already on
    disk (``protocol.attachments_dir_for_event``) — only the waking event
    used to have its attachments resolved to paths. A resident that folds
    in a different pending event via ``event: <id>`` must not be left with
    only the bare ``attachments:`` filename string.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    current = protocol.create_event(inbox, "telegram", "current task")
    current_id = current.stem
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"fake-jpeg-bytes")
    protocol.create_event(
        inbox, "telegram", "look at this", attachment_files=[src],
    )

    events = daemon._pending_events_for_agent(inbox, current_id)

    assert len(events) == 1
    paths = events[0]["attachment_paths"]
    assert len(paths) == 1
    assert Path(paths[0]).read_bytes() == b"fake-jpeg-bytes"
    assert "attachment_unfetched" not in events[0]


def test_pending_event_record_flags_unfetched_attachment(tmp_path):
    """An announced attachment with no local file renders as unfetched, not absent.

    #1156: ``event_attachment_paths`` silently drops a name with no file on
    disk — correct for a caller that only wants openable paths, wrong for a
    caller that needs to tell "no attachment" apart from "announced, never
    became bytes". A hand-edited/retention-swept event exercises the same
    path a real never-downloaded attachment (#1154) would.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    current = protocol.create_event(inbox, "telegram", "current task")
    current_id = current.stem
    ghost = protocol.create_event(inbox, "telegram", "a photo that never arrived")
    ghost_ev = next(
        ev for ev in protocol.list_pending(inbox) if ev["id"] == ghost.stem
    )
    protocol.update_event_meta(ghost_ev, attachments="ghost.png")

    events = daemon._pending_events_for_agent(inbox, current_id)

    assert len(events) == 1
    assert events[0]["id"] == ghost.stem
    assert events[0]["attachment_unfetched"] == ["ghost.png"]
    assert "attachment_paths" not in events[0]


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


def test_drain_outbox_queues_strand_respawn_request(tmp_path):
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
    assert spawned["strand"] is True


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
    assert "strand" not in spawned


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
    assert spawned["strand"] is True
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
    assert spawned[0]["strand"] is True

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
    """A strand-stack run must not itself spawn a further child (no nesting)."""
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
        source="telegram", meta={"strand": True},
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


def test_notify_spawn_parent_routes_to_adopting_resident(tmp_path):
    inbox = tmp_path / ".brr" / "inbox"
    daemon._register_run_control(
        "evt-child",
        "run-parent",
        parent_conversation_key="telegram:42:",
        repo_label="Gurio/brr",
    )
    daemon._bind_run_control("evt-child", "run-child")
    task = Run(
        id="run-successor",
        event_id="evt-successor",
        body="",
        source="telegram",
        conversation_key="telegram:42:",
        meta={"repo_label": "Gurio/brr"},
    )
    assert daemon._find_run_control("run-parent") is None, (
        "fixture must leave the original parent dead so the notify route below "
        "proves adoption, not the original dispatch edge"
    )
    adopted = daemon._authorize_child_control(
        task, daemon._find_run_control("evt-child")
    )
    assert adopted["adopted"] is True

    child = Run(
        id="run-child",
        event_id="evt-child",
        body="",
        source="telegram",
        status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
        },
    )

    daemon._notify_spawn_parent(inbox, child)

    [note] = protocol.list_pending(inbox)
    assert note["spawn_parent_run_id"] == "run-successor"
    assert note["conversation_key"] == "telegram:42:"


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


def test_spawn_contract_check_declared_report_alone_is_a_contract(tmp_path):
    """A declared ``report:`` with no ``branch:`` is still a contract.

    The gate used to be ``if declared_branch:`` alone, so this shape fell
    through to the prose scan: the declared report was discarded, and with
    no ``brr/`` token in the prose the whole contract evaporated
    (``return None``) — a dispatcher's stated commitment, dropped in
    silence. Either clause alone is a contract; the branch clause simply
    has nothing to say."""
    missing = Path(f"/tmp/brr-report-only-{tmp_path.name}.md")
    assert not missing.exists()
    result = daemon._spawn_contract_check(
        "no branch named anywhere in this prose",
        "brr/whatever-the-child-picked",
        declared_report=str(missing),
    )
    assert result is not None
    assert result["source"] == "declared"
    assert result["spec_branch"] is None
    assert result["branch_ok"] is True
    assert result["report_ok"] is False
    assert result["mismatch"] is True


def test_spawn_contract_check_declared_report_alone_ignores_prose_branch(tmp_path):
    """...and the scan must not run alongside it.

    Falling through to the scan did not merely drop the declared report —
    an incidental ``brr/<slug>`` anywhere in the prose (a sibling's branch,
    #640's own live false positive) became an enforced branch commitment
    nobody made. A declaration on either clause makes the whole contract
    declared."""
    written = tmp_path / "report.md"
    written.write_text("done", encoding="utf-8")
    result = daemon._spawn_contract_check(
        "A sibling worker is live on `brr/sibling-branch` — don't collide.",
        "brr/what-this-child-published",
        declared_report=str(written),
    )
    assert result["spec_branch"] is None
    assert result["mismatch"] is False


def test_report_only_mismatch_prints_no_branch_line(tmp_path):
    """#1097's rule, applied to the clause that does not exist.

    A report-only contract has no branch commitment, so the completion note
    must carry neither a branch accusation nor a ``✓ as declared`` absolution
    — both would be a verdict on a commitment nobody made."""
    inbox = tmp_path / ".brr" / "inbox"
    missing = Path(f"/tmp/brr-report-only-note-{tmp_path.name}.md")
    assert not missing.exists()
    task = Run(
        id="run-child", event_id="evt-child",
        body="research the thing; no branch expected",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_report": str(missing),
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note["spawn_contract_mismatch"] is True
    assert "spawn_contract_spec_branch" not in note
    assert "spawn_contract_published_branch" not in note
    assert "status=contract-mismatch" in note["body"]
    assert "contract mismatch — report vs published:" in note["body"]
    assert "branch:" not in note["body"]
    assert str(missing) in note["body"]


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
    assert note.get("spawn_status") == "done"
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

    # #1186 seeds this path for real (dispatch-time skeleton write) — route
    # it under tmp_path rather than a literal /tmp/... constant so this
    # assertion-only test never touches the real shared /tmp.
    report_path = str(tmp_path / "brr-declared-slug-report.md")
    accepted = daemon._queue_spawn_request(
        daemon._WorkerEmit(brr_dir, "", event_id),
        task,
        inbox,
        event_id,
        {
            "spawn": True,
            "branch": "brr/declared-slug",
            "report": report_path,
        },
        "bounded side task",
        outbox,
    )

    assert accepted is True
    spawned = [p for p in inbox.glob("*.md") if p.stem != event_id]
    child = protocol._read_event(spawned[0])
    assert child["spawn_contract_branch"] == "brr/declared-slug"
    assert child["spawn_contract_report"] == report_path


def test_queue_spawn_request_renders_declared_contract_into_child_body(tmp_path):
    """The declared contract must reach the child that is held to it.

    Live 2026-08-05, run-260805-1527-y100: specced ``branch:
    brr/both-doors-named`` + ``report: /tmp/brr-report-both-doors.md``,
    finalized ``contract-mismatch`` on both clauses — and its own report
    said, correctly, that no ``report:`` path was declared anywhere in the
    event's frontmatter *or body*. It wasn't: the frontmatter is consumed
    for routing and the body is the whole of what a child reads. #1097's
    own test already named this gap from the parent's side ("no prompt
    surface says so"); this is the child's side of it.

    Both values must appear verbatim — the branch as a name to rename to,
    the report as a path to write."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original task", status="processing")
    event_id = path.stem
    task = Run(id="run-parent", event_id=event_id, body="original", source="telegram")

    # #1186 seeds this path for real (dispatch-time skeleton write) — route
    # it under tmp_path rather than a literal /tmp/... constant so this
    # assertion-only test never touches the real shared /tmp.
    report_path = str(tmp_path / "brr-declared-slug-report.md")
    accepted = daemon._queue_spawn_request(
        daemon._WorkerEmit(brr_dir, "", event_id),
        task,
        inbox,
        event_id,
        {
            "spawn": True,
            "branch": "brr/declared-slug",
            "report": report_path,
        },
        "bounded side task",
        outbox,
    )

    assert accepted is True
    spawned = [p for p in inbox.glob("*.md") if p.stem != event_id]
    child_body = protocol._read_event(spawned[0])["body"]
    assert "bounded side task" in child_body
    assert "brr/declared-slug" in child_body
    assert report_path in child_body


def test_queue_spawn_request_undeclared_contract_leaves_the_body_alone(tmp_path):
    """Guard the guard: no ``branch:``/``report:`` ⇒ no rendered block.

    A spawn with no declared contract is checked by the prose scan, and an
    appended block would be one more thing for that scan to read. Byte-for-
    byte the spec as written is the contract for those, exactly as before."""
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
        {"spawn": True},
        "bounded side task",
        outbox,
    )

    assert accepted is True
    spawned = [p for p in inbox.glob("*.md") if p.stem != event_id]
    assert protocol._read_event(spawned[0])["body"].strip() == "bounded side task"


def test_queue_spawn_request_refuses_a_prose_report_at_dispatch_time(tmp_path):
    """#1136: a `report:` that was never a path is refused the moment it's
    typed, the same way a malformed `event:` id is refused — instead of
    riding a child's meta all the way to a completion check that can only
    discover the problem later (the live 2026-08-05 case:
    `report: the PR body is the report`, pinned separately by
    `test_a_prose_report_declaration_says_whose_it_was`). No child is
    queued at all: the whole directive is malformed input, same severity
    as no body / bad environment / nested spawn elsewhere in this
    function."""
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
            "report": "the PR body is the report",
        },
        "bounded side task",
        outbox,
    )

    assert accepted is False
    spawned = [p for p in inbox.glob("*.md") if p.stem != event_id]
    assert spawned == []
    notices = daemon._read_outbox_notices(outbox)
    assert len(notices) == 1
    assert "the PR body is the report" in notices[0]["text"]
    assert "start with" in notices[0]["text"]


def test_queue_spawn_request_accepts_an_absolute_report_path(tmp_path):
    """Guard the guard: an ordinary absolute path is unaffected — the
    shape check refuses only the unambiguous non-path case."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original task", status="processing")
    event_id = path.stem
    task = Run(id="run-parent", event_id=event_id, body="original", source="telegram")

    # #1186 seeds this path for real — keep it under tmp_path, not a
    # literal /tmp/... constant, so this test never touches real /tmp.
    report_path = str(tmp_path / "brr-report-with a space.md")
    accepted = daemon._queue_spawn_request(
        daemon._WorkerEmit(brr_dir, "", event_id),
        task,
        inbox,
        event_id,
        {"spawn": True, "report": report_path},
        "bounded side task",
        outbox,
    )

    assert accepted is True
    assert daemon._read_outbox_notices(outbox) == []


def test_queue_spawn_request_carries_title_into_child_meta(tmp_path):
    """#880 §1b: ``title:`` in the spawn frontmatter carries onto the
    child's own meta as ``title`` — the field `_presence_label_for_event`
    reads to populate that child's presence ``label``, so a parent
    supervising several siblings can tell their rows apart before any of
    them has written its own ``.name``. Absent ``title:`` ⇒ no key at all
    (matches ``branch:``/``report:``'s optional shape), never an empty
    string riding along as chrome."""
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
        {"spawn": True, "title": "fix the frontend build"},
        "bounded side task",
        outbox,
    )

    assert accepted is True
    spawned = [p for p in inbox.glob("*.md") if p.stem != event_id]
    child = protocol._read_event(spawned[0])
    assert child["title"] == "fix the frontend build"

    # A second child with no `title:` gets no key at all.
    accepted2 = daemon._queue_spawn_request(
        daemon._WorkerEmit(brr_dir, "", event_id),
        task, inbox, event_id, {"spawn": True}, "another bounded task", outbox,
    )
    assert accepted2 is True
    spawned2 = [p for p in inbox.glob("*.md") if p.stem not in (event_id, spawned[0].stem)]
    child2 = protocol._read_event(spawned2[0])
    assert "title" not in child2


# ── #1186: the declared `report:` file is pre-seeded at dispatch time ──


def test_queue_spawn_request_seeds_declared_report_before_child_queued(
    tmp_path, monkeypatch,
):
    """#1186: a strand that spends its whole context window on the actual
    work has none left for the two voluntary closing acts — one of them is
    writing the `report:` file it was contracted to produce, from a blank
    page, at the point it has the least budget left to do so. The fix:
    `_queue_spawn_request` writes a skeleton at the declared path itself,
    *before* the child's own event is even queued — checked here by
    wrapping `protocol.create_event` and asserting the file already exists
    at the moment the child event is minted, not merely sometime before
    this call returns."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original task", status="processing")
    event_id = path.stem
    task = Run(id="run-parent", event_id=event_id, body="original", source="telegram")

    report_path = tmp_path / "reports" / "brr-seed-report.md"
    assert not report_path.exists()

    real_create_event = protocol.create_event
    seen = {}

    def _wrapped_create_event(*args, **kwargs):
        seen["report_existed_at_queue_time"] = report_path.exists()
        return real_create_event(*args, **kwargs)

    monkeypatch.setattr(daemon.protocol, "create_event", _wrapped_create_event)

    accepted = daemon._queue_spawn_request(
        daemon._WorkerEmit(brr_dir, "", event_id),
        task,
        inbox,
        event_id,
        {
            "spawn": True,
            "branch": "brr/declared-slug",
            "report": str(report_path),
            "title": "fix the frontend build",
        },
        "bounded side task",
        outbox,
    )

    assert accepted is True
    assert seen["report_existed_at_queue_time"] is True
    content = report_path.read_text(encoding="utf-8")
    assert "run-parent" in content
    assert event_id in content
    assert "brr/declared-slug" in content
    assert "fix the frontend build" in content
    assert daemon._read_outbox_notices(outbox) == []


def test_queue_spawn_request_no_report_seeds_nothing(tmp_path, monkeypatch):
    """Guard the guard: no `report:` declared ⇒ the seeding path never
    runs at all, not merely "runs and no-ops". A `branch:`-only spawn (or
    a bare one) must not touch the filesystem on this behalf."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original task", status="processing")
    event_id = path.stem
    task = Run(id="run-parent", event_id=event_id, body="original", source="telegram")

    def _boom(*args, **kwargs):
        raise AssertionError("_seed_declared_report must not be called with no report:")

    monkeypatch.setattr(daemon, "_seed_declared_report", _boom)

    accepted = daemon._queue_spawn_request(
        daemon._WorkerEmit(brr_dir, "", event_id),
        task,
        inbox,
        event_id,
        {"spawn": True, "branch": "brr/declared-slug"},
        "bounded side task",
        outbox,
    )

    assert accepted is True


def test_queue_spawn_request_report_collision_does_not_clobber(tmp_path):
    """#1186's collision guard: a path that already holds *different*
    content (not this same dispatch's own skeleton, re-seeded) is left
    alone — a naming collision on a dispatcher-chosen path is a dispatcher
    mistake, not a reason to refuse the spawn or destroy what's there. The
    daemon notes it via an advisory notice instead."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original task", status="processing")
    event_id = path.stem
    task = Run(id="run-parent", event_id=event_id, body="original", source="telegram")

    report_path = tmp_path / "reports" / "brr-collision-report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("someone else's unrelated file\n", encoding="utf-8")

    accepted = daemon._queue_spawn_request(
        daemon._WorkerEmit(brr_dir, "", event_id),
        task,
        inbox,
        event_id,
        {
            "spawn": True,
            "branch": "brr/declared-slug",
            "report": str(report_path),
        },
        "bounded side task",
        outbox,
    )

    assert accepted is True
    assert report_path.read_text(encoding="utf-8") == "someone else's unrelated file\n"
    notices = daemon._read_outbox_notices(outbox)
    assert len(notices) == 1
    assert notices[0]["kind"] == "advisory"
    assert str(report_path) in notices[0]["text"]


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


def test_notify_spawn_parent_clean_bolt_discharges_unpublished_branch(tmp_path):
    """#677: the live 2026-08-15 case — a declared `branch:` never
    published because the run's own accepted, unannotated `cut:` bolt
    attested nothing needed publishing (a deliberate no-code-change
    verdict). That is a substitution the parent should know about, not
    the ticket-swap violation `contract-mismatch` names: status stays
    whatever the run finished as, and the event-layer flag must not be
    set."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child",
        body="investigate; change nothing unless reproduced",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_branch": "brr/the-render-that-moved-underneath",
            "bolt": {"accepted_at": "2026-08-15T21:01:33Z", "annotated": 0},
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert "status=done" in note["body"]
    assert "contract-mismatch" not in note["body"]
    assert "advisory" in note["body"]
    assert "brr/the-render-that-moved-underneath" in note["body"]
    assert "2026-08-15T21:01:33Z" in note["body"]


def test_notify_spawn_parent_bounced_bolt_does_not_discharge(tmp_path):
    """#677-negative (guard the guard): a bolt force-accepted at the
    cap-3 bounce fallback carries `annotated > 0` — the daemon's own
    dissent, not a clean attestation — and must not discharge the branch
    clause the way an accepted, unannotated bolt does. This is half the
    guard: without the `annotated == 0` check, a run could dodge the
    contract check by cutting a bolt the daemon itself disputed. It
    falls to the same "nothing published, nothing attests why" bucket a
    run with no bolt at all gets — not silently waved through as
    `absolved`, and not the ticket-swap `contract-mismatch` label
    either, since "no bolt" and "a bolt the daemon didn't accept
    cleanly" are the same evidentiary state: neither one testifies that
    nothing needed publishing."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child",
        body="do the thing",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_branch": "brr/declared-slug",
            "bolt": {"accepted_at": "2026-08-15T21:01:33Z", "annotated": 2},
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert note.get("spawn_status") == "nothing-published"
    assert "status=nothing-published" in note["body"]
    assert "advisory" not in note["body"]
    assert "contract-mismatch" not in note["body"]


def test_notify_spawn_parent_unpublished_with_no_bolt_gets_its_own_label(tmp_path):
    """#677 residual defect #1: nothing published, and nothing attests
    why — a real deviation, but not a branch-*name* disagreement. Gets
    its own status label (not `contract-mismatch`, not the ticket-swap
    accusation) and wording that says what actually happened."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child",
        body="do the thing",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_branch": "brr/declared-slug",
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_contract_mismatch" not in note
    assert note.get("spawn_status") == "nothing-published"
    assert "status=nothing-published" in note["body"]
    assert "contract-mismatch" not in note["body"]
    assert "published nothing on its contract branch" in note["body"]
    assert "brr/declared-slug" in note["body"]


def test_notify_spawn_parent_clean_bolt_discharges_branch_even_when_report_fails(tmp_path):
    """#677 open decision: when both clauses fail, does a clean bolt
    still discharge the branch half? Yes — the bolt attests the branch/
    produce plan, never a specific declared `report:` path, which is an
    independent, checkable filesystem fact. So the overall status still
    indicts (the report really is missing), but the branch line reads as
    the discharge sentence, not a `spec branch:`/`published branch:`
    accusation pair."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child",
        body="investigate and write it up",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_branch": "brr/the-render-that-moved-underneath",
            "spawn_contract_report": str(tmp_path / "never-written.md"),
            "bolt": {"accepted_at": "2026-08-15T21:01:33Z", "annotated": 0},
        },
    )

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note["spawn_contract_mismatch"] is True
    assert "status=contract-mismatch" in note["body"]
    assert "never published, but bolt accepted" in note["body"]
    assert "spec branch:" not in note["body"]
    assert "published branch:" not in note["body"]
    assert "MISSING" in note["body"]


def test_a_kept_branch_is_not_printed_as_an_accusation(tmp_path):
    """#1097: the perfect worker indicted for its parent's typo.

    ``report:`` is a filesystem path — the ``spawn:`` row now says so, after
    this docstring's own observation that no prompt surface did went on to
    cost a live run — making a parent naming a path the child never writes an
    ordinary, entirely parent-side error. The completion note used to print
    the branch lines on *any* mismatch — so a child that published exactly the
    branch it was declared, and only missed a report it was never told to
    create, was reported under two branch lines that read as the accusation.

    Both clauses failing still prints both; what stops is a satisfied clause
    standing beside a violated one with nothing to tell them apart.
    """
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child", body="do the thing",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_branch": "brr/exactly-as-told",
            "spawn_contract_report": str(tmp_path / "never-written.md"),
            "publish_branch": "brr/exactly-as-told",
            "has_new_commit": True,
        },
    )

    daemon._notify_spawn_parent(inbox, task)
    body = protocol.list_pending(inbox)[0]["body"]

    header = body.split("contract mismatch — ")[1].split(" vs published")[0]
    assert header == "report", header
    assert "never-written.md" in body and "MISSING" in body
    # The branch is not on trial: no spec-vs-published pair for a branch
    # that matched, because that pair *is* the indictment's grammar.
    assert "published branch:" not in body, body
    assert "brr/exactly-as-told" in body  # still stated, as kept


def test_a_prose_report_declaration_says_whose_it_was(tmp_path):
    """The live 2026-08-05 case, end to end: a declaration that was never a path.

    ``report: the PR body is the report`` cannot be ``stat``-ed, so the check
    reports ``MISSING`` for a strand whose commit was pushed and whose PR was
    open — and the note names the *strand's* run id, so the reader arrives
    primed to distrust the child. The declaration is the dispatcher's, carried
    onto the child's meta at spawn.

    Driven through ``_notify_spawn_parent`` rather than the note builder: what
    is being pinned is what the reader of the completion event actually sees.
    """
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child", body="do the thing",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_branch": "brr/exactly-as-told",
            "spawn_contract_report": "the PR body is the report",
            "publish_branch": "brr/exactly-as-told",
            "has_new_commit": True,
        },
    )

    daemon._notify_spawn_parent(inbox, task)
    body = protocol.list_pending(inbox)[0]["body"]

    assert "the PR body is the report" in body and "MISSING" in body
    # Who declared it — a fact, stated unconditionally.
    assert "dispatcher's declaration" in body, body
    # The ambiguity, named rather than resolved: a path may legally contain a
    # space, so the note may not assert that this one is not a path.
    assert "meant as prose" in body, body
    assert "not a path" not in body, body


def test_missing_report_with_a_shipped_pr_names_the_pr(tmp_path):
    """#1136: the live 2026-08-05 case's other half — the durable artefact
    sits in `.pr` while the check says only "MISSING". A strand that spent
    its whole context window may legitimately have a PR open and no report
    file left to write. The parent should read "PR #N shipped, no report
    file" — a true sentence it can act on — not a bare accusation that
    reads as if nothing happened.

    Production order, not a convenient one: `.pr` is captured onto
    ``task.meta`` and the outbox torn down *before* the parent's reap loop
    calls ``_notify_spawn_parent`` (mirrors
    ``test_notify_spawn_parent_clean_reap_carries_produce_handles``).
    """
    inbox = tmp_path / ".brr" / "inbox"
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-child"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / ".pr").write_text("1134\n", encoding="utf-8")
    task = Run(
        id="run-child", event_id="evt-child", body="do the thing",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_report": str(tmp_path / "never-written.md"),
            "publish_branch": "brr/exactly-as-told",
            "has_new_commit": True,
            "outbox_path": str(outbox_dir),
        },
    )

    daemon._capture_pr_handle(task, outbox_dir)
    daemon._remove_outbox(outbox_dir)
    assert not outbox_dir.exists()

    daemon._notify_spawn_parent(inbox, task)
    note = protocol.list_pending(inbox)[0]
    body = note["body"]

    assert "MISSING" in body
    assert "PR #1134" in body and "no report file" in body
    assert str(note.get("spawn_pr_number")) == "1134"


def test_missing_report_without_a_pr_is_unchanged(tmp_path):
    """The negative twin: no `.pr` ⇒ no PR-aware sentence, and the existing
    MISSING/prose-ambiguity wording is untouched — 'absent stays absent'
    (#648 rule 1) applies to this new fact exactly like every other one."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child", body="do the thing",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_report": str(tmp_path / "never-written.md"),
            "publish_branch": "brr/exactly-as-told",
            "has_new_commit": True,
        },
    )

    daemon._notify_spawn_parent(inbox, task)
    body = protocol.list_pending(inbox)[0]["body"]

    assert "MISSING" in body
    assert "PR #" not in body
    assert "no report file" not in body


def test_a_missing_but_path_shaped_report_makes_no_prose_guess(tmp_path):
    """A strand that simply never wrote the file gets no prose accusation."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child", body="do the thing",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_branch": "brr/exactly-as-told",
            "spawn_contract_report": str(tmp_path / "never-written.md"),
            "publish_branch": "brr/exactly-as-told",
            "has_new_commit": True,
        },
    )

    daemon._notify_spawn_parent(inbox, task)
    body = protocol.list_pending(inbox)[0]["body"]

    assert "MISSING" in body
    assert "dispatcher's declaration" in body
    assert "meant as prose" not in body, body


def test_both_clauses_failing_still_names_both(tmp_path):
    """The twin. Narrowing a message must not start hiding real failures —
    that moves the lie one door down instead of ending it."""
    inbox = tmp_path / ".brr" / "inbox"
    task = Run(
        id="run-child", event_id="evt-child", body="do the thing",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "spawn_contract_branch": "brr/declared",
            "spawn_contract_report": str(tmp_path / "never-written.md"),
            "publish_branch": "brr/something-else",
            "has_new_commit": True,
        },
    )

    daemon._notify_spawn_parent(inbox, task)
    body = protocol.list_pending(inbox)[0]["body"]

    assert "branch and report" in body
    assert "published branch:  brr/something-else" in body
    assert "MISSING" in body


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
    assert note.get("spawn_status") == "runner-failed"
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


def test_notify_spawn_parent_omits_status_before_terminal_state(tmp_path):
    """An undetermined lifecycle state stays absent, never ``pending``."""
    inbox = tmp_path / ".brr" / "inbox"
    task = _spawn_child_run(body="", status="pending")

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_status" not in note


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


def test_notify_spawn_parent_carries_publish_count_and_reply_bytes(
    tmp_path, monkeypatch,
):
    """A pushed child carries both produce facts through the real publish and
    notification paths; substantial produce needs no extra prose annotation.
    """
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    inbox = brr_dir / "inbox"
    response = tmp_path / "response.md"
    reply = "é" * (daemon._SPAWN_NOTIFY_RESPONSE_MAX_CHARS + 10)
    response.write_text(reply, encoding="utf-8")
    task = Run(
        id="run-child", event_id="evt-child", body="",
        source="telegram", status="done",
        meta={
            "spawn_parent_run_id": "run-parent",
            "spawn_parent_conversation_key": "telegram:42:",
            "publish_branch": "brr/delivered",
            "response_path": str(response),
        },
    )
    monkeypatch.setattr(daemon, "_refuse_publish", lambda *_a: None)
    monkeypatch.setattr(daemon.gitops, "shared_brr_dir", lambda _r: brr_dir)
    monkeypatch.setattr(
        daemon.gitops, "branch_upstream",
        lambda _r, branch: f"origin/{branch}",
    )
    monkeypatch.setattr(daemon.gitops, "branch_remote", lambda *_a: "origin")
    monkeypatch.setattr(
        daemon, "_commits_between", lambda *_a: ["first", "second"],
    )
    monkeypatch.setattr(
        daemon.subprocess, "run",
        lambda args, **_kw: daemon.subprocess.CompletedProcess(args, 0, "", ""),
    )

    # Production order: publish stamps the Run, then the parent-reap path
    # asks the notifier to build the event. No event dict is assembled here.
    daemon.publish(tmp_path, task)
    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note.get("spawn_commits") == 2
    assert note.get("spawn_reply_bytes") == len(reply.encode("utf-8"))
    assert "…(truncated)" in note["body"]
    assert "\nproduce:" not in note["body"]


def test_notify_spawn_parent_present_empty_reply_is_a_real_zero(tmp_path):
    """An existing zero-byte response is known produce, not an absent fact."""
    inbox = tmp_path / ".brr" / "inbox"
    response = tmp_path / "response.md"
    response.write_bytes(b"")
    task = _spawn_child_run(body="")
    task.meta["response_path"] = str(response)

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note.get("spawn_reply_bytes") == 0
    assert "spawn_commits" not in note
    assert "produce:" in note["body"]
    assert "reply 0 B" in note["body"]
    assert "empty" not in note["body"].lower()


def test_notify_spawn_parent_unknown_reply_stays_absent(tmp_path):
    """No response_path is unknown, so it must never be rendered as zero."""
    inbox = tmp_path / ".brr" / "inbox"
    task = _spawn_child_run(body="")
    assert "response_path" not in task.meta

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_reply_bytes" not in note
    assert "spawn_commits" not in note
    assert "reply 0 B" not in note["body"]
    assert "\nproduce:" in note["body"]
    assert "reply unknown" in note["body"]


def test_notify_spawn_parent_measured_zero_commits_is_not_unknown(tmp_path):
    """``publish_status == "nothing"`` is the env layer's *measured* zero.

    ``publish()`` returns before stamping a count when there is no branch to
    push, so an absent ``publish_commits`` covers two different states — and
    only one of them is unknown. ``nothing`` means ``has_commits_beyond(seed)``
    was checked and came back empty; rendering that as ``commits unknown``
    would report a fact we hold as one we don't.
    """
    inbox = tmp_path / ".brr" / "inbox"
    response = tmp_path / "response.md"
    response.write_text("done — nothing to act on.", encoding="utf-8")
    task = _spawn_child_run(body="")
    task.meta["publish_status"] = "nothing"
    task.meta["response_path"] = str(response)

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note.get("spawn_commits") == 0
    # 27, not 25: the em dash is three bytes. The handle counts bytes, and a
    # test that counted characters would have passed against a `len()` bug.
    assert "produce: 0 commits · no branch · reply 27 B" in note["body"]
    assert "commits unknown" not in note["body"]


def test_notify_spawn_parent_detached_commits_stay_unknown(tmp_path):
    """The other no-branch arm. ``detached`` never counted anything, so the
    count must stay absent rather than borrowing the measured-zero shape."""
    inbox = tmp_path / ".brr" / "inbox"
    task = _spawn_child_run(body="")
    task.meta["publish_status"] = "detached"

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert "spawn_commits" not in note
    assert "commits unknown" in note["body"]


def test_notify_spawn_parent_undecodable_reply_still_has_a_size(tmp_path):
    """A response that is not UTF-8 is unreadable, not unmeasurable.

    The prose stays empty — undecodable bytes are not a reply anyone can
    read — but the byte count is on disk and reporting it as unknown would be
    the same defect this handle exists to close.
    """
    inbox = tmp_path / ".brr" / "inbox"
    response = tmp_path / "response.md"
    response.write_bytes(b"\xff\xfe\x00garbage")
    task = _spawn_child_run(body="")
    task.meta["response_path"] = str(response)

    daemon._notify_spawn_parent(inbox, task)

    note = protocol.list_pending(inbox)[0]
    assert note.get("spawn_reply_bytes") == 10  # 3 BOM-ish bytes + "garbage"
    assert "reply 10 B" in note["body"]
    assert "reply unknown" not in note["body"]


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
    ``Run`` with bare ``meta={"strand": True}`` — never exercising the
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
    # Both push lanes, not just one. `write_repo_scaffold` deliberately does
    # not `git init`, and since #746 `publish_default_branch` refuses to ship
    # from a tree git will not confirm — recording that on this run's
    # `stray_host_write`, which rewrites the very note this test reads.
    monkeypatch.setattr(daemon, "publish_default_branch", lambda *_a, **_k: None)
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


def test_dispatch_records_user_run_signal_for_non_schedule_event(tmp_path, monkeypatch):
    """#904 option 1: the daemon records a `user-run` signal when it
    dispatches a resident lead run for any event that isn't itself a
    schedule firing — the "the user is already talking to the resident"
    case an opted-in `every:` entry's `reset_on: user-run` treats as a
    cooldown push (see ``schedule.apply_reset_signals``). Drives the real
    dispatch loop (``daemon.start``), not a hand-called helper, so this is
    the actual trigger point rather than a claim about it.
    """
    write_repo_scaffold(tmp_path)
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    protocol.create_event(
        inbox, "telegram", "hello", conversation_key="telegram:1:",
    )

    cfg: dict = {}

    def fake_run_worker(event, *_args, **_kwargs):
        task = Run.from_event(event, cfg)
        task.status = "done"
        return task

    ticks = {"n": 0}

    def fake_fire_due_schedules(*_a, **_k):
        ticks["n"] += 1
        if schedule_mod.load_signals(brr_dir).get("user-run") or ticks["n"] > 200:
            raise StopIteration

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: cfg)
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "publish_default_branch", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_fire_due_schedules", fake_fire_due_schedules)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert ticks["n"] <= 200, "resident dispatch never recorded user-run within the tick budget"
    assert "user-run" in schedule_mod.load_signals(brr_dir)


def test_dispatch_does_not_record_user_run_signal_for_schedule_event(
    tmp_path, monkeypatch,
):
    """The converse: a schedule-sourced event's own dispatch must not
    record `user-run` — that would let a schedule entry's firing reset its
    own (or another entry's) `reset_on: user-run` cooldown, which is not
    what the signal means."""
    write_repo_scaffold(tmp_path)
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    protocol.create_event(
        inbox, "schedule", "director tick body", schedule_id="director-tick",
    )

    cfg: dict = {}

    def fake_run_worker(event, *_args, **_kwargs):
        task = Run.from_event(event, cfg)
        task.status = "done"
        return task

    ticks = {"n": 0}

    def fake_fire_due_schedules(*_a, **_k):
        ticks["n"] += 1
        if ticks["n"] > 30:
            raise StopIteration

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: cfg)
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "publish_default_branch", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_fire_due_schedules", fake_fire_due_schedules)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert "user-run" not in schedule_mod.load_signals(brr_dir)


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
    # design-the-post.md §THE FIELD TWO MACHINES WRITE: the outcome lands
    # in `run_outcome:` now; the letter itself settles at "done".
    assert refreshed.get("status") == "done"
    assert refreshed.get("run_outcome") == "error"
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
    # design-the-post.md §THE FIELD TWO MACHINES WRITE: the orphaned
    # dispatch's own letter settles at "done" with its outcome in
    # `run_outcome:`, not the raw "error" written to `status:`.
    resolved = protocol.parse_frontmatter(
        Path(spawn_event["_path"]).read_text(encoding="utf-8"))
    assert resolved.get("status") == "done"
    assert resolved.get("run_outcome") == "error"


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
    # design-the-post.md §THE FIELD TWO MACHINES WRITE: the run's outcome
    # goes in its own key now — the letter's own status stays "done".
    assert event.get("run_outcome") == "error"
    response = protocol.read_response(tmp_path / ".brr" / "responses", "evt-run-fail")
    assert response is not None
    assert "runner failed after 1 attempt(s): connection dropped" in response
    assert task.terminal_reply == response


def test_finalize_stopped_run_settles_letter_done_with_run_outcome(
    tmp_path, monkeypatch,
):
    """A dispatched-then-stopped run: `_finalize_stopped_run` must settle
    the letter's `status:` at "done" and carry `run_outcome: stopped" — not
    the run's raw word (design-the-post.md §THE FIELD TWO MACHINES WRITE).

    Before this cut the function wrote the letter-flavored "cancelled" to
    `status`, but the general finalize guard in `_run_worker_and_finalize`
    (`event.get("status") != "done"`) then clobbered it right back to the
    raw "stopped" — so "cancelled" never actually survived to disk for a
    run that had been dispatched. Fixed as a side effect of the split:
    `_set_event_run_outcome` settles `status` at "done" here, so that later
    guard now no-ops instead of overwriting.
    """
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    runs_dir.mkdir(parents=True)
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    event_path = inbox / "evt-stop.md"
    event_path.write_text(
        "---\nid: evt-stop\nstatus: processing\n---\nhelp\n", encoding="utf-8",
    )
    event = protocol._read_event(event_path)
    assert event is not None
    task = Run(id="task-stop", event_id="evt-stop", body="help", status="running")
    task.save(runs_dir)
    branch_plan = types.SimpleNamespace(target_branch="brr/task-stop")

    class FakeEnvBackend:
        def finalize(self, _ctx, task, _runs_dir):
            return task

    monkeypatch.setattr(daemon, "_capture_worktree", lambda *_a, **_k: None)
    emit = daemon._WorkerEmit(brr_dir, "", "evt-stop")

    result = daemon._finalize_stopped_run(
        emit, task, event, "evt-stop", runs_dir,
        FakeEnvBackend(), object(), branch_plan, {},
        {"stopped_by": "run-parent"}, 1, [],
    )

    assert result.status == "stopped"
    assert event["status"] == "done"
    assert event.get("run_outcome") == "stopped"
    # The general finalize guard reads status off the event directly, so
    # confirm it will see "done" and no-op rather than re-write it.
    assert event.get("status") == "done"


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


class TestNotifyGateFallback:
    """The 'no run left unheard' fallback (#743 family): a schedule-woken
    (or any gate-less) run's terminal reply reaches a configured chat gate
    via ``notify.gate`` instead of being staged undeliverable, when one
    can be resolved. Drives the real ``_run_worker`` decision site
    (``daemon.py`` around ``_terminal_reply_lands``/``_terminal_route``),
    with only ``_gate_can_deliver`` faked to control which gates read as
    configured on this fake account."""

    def _run(
        self, tmp_path, monkeypatch, *, cfg_extra=None, configured_gates=(),
        eid="evt-tick", body="director tick note\n", duplicate=False,
        event_conversation_key=None, seed_conversations=None,
    ):
        # A real git repo, not just ``write_repo_scaffold``'s directory
        # shape: an account-attached run stays on the ``worktree`` env
        # (unlike a ``repo_label="home"`` run, which is forced to
        # ``host``), and ``WorktreeEnv.prepare`` really does ``git
        # worktree add`` against *repo_root*.
        init_git_repo(tmp_path)
        commit_files(tmp_path, {"AGENTS.md": "# Project\n"})
        (tmp_path / ".brr" / "inbox").mkdir(parents=True)
        (tmp_path / ".brr" / "responses").mkdir(parents=True)
        cfg = {
            "repo.label": "Gurio/notify",
            "home.path": str(tmp_path / "account-home"),
            **(cfg_extra or {}),
        }
        ctx = daemon.account.resolve_context(tmp_path, cfg)
        event_kwargs = {}
        if event_conversation_key is not None:
            event_kwargs["conversation_key"] = event_conversation_key
        event = make_event(
            tmp_path, eid=eid, source="schedule", body="tick", **event_kwargs,
        )
        # Seed prior conversation activity so the recent-activity tiebreak has
        # something to read: (key, seconds_ago) — smaller seconds_ago is more
        # recent. Explicit mtimes, not write order, because two fast writes
        # can land in the same filesystem-mtime tick.
        brr_dir = tmp_path / ".brr"
        for key, seconds_ago in seed_conversations or []:
            daemon.conversations.append_event(
                brr_dir, key, {"id": f"evt-seed-{key}", "source": key.split(":", 1)[0], "body": "hi"},
            )
            log_path = daemon.conversations.event_log_path(brr_dir, key, f"evt-seed-{key}")
            stamp = time.time() - seconds_ago
            os.utime(log_path, (stamp, stamp))
        monkeypatch.setattr(
            daemon.runner, "resolve_runner_profile",
            lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
        )
        monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
        monkeypatch.setattr(
            daemon.prompts, "build_daemon_prompt",
            lambda task, eid, rp, root, **kw: "PROMPT",
        )
        monkeypatch.setattr(
            daemon, "_gate_can_deliver",
            lambda _brr, gate: gate in configured_gates,
        )
        if duplicate:
            monkeypatch.setattr(
                daemon, "_terminal_stream_duplicates_delivered",
                lambda _task, _resp_path: True,
            )
        base_env = envs.get_env("worktree")

        def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
            Path(invocation.response_path).write_text(body, encoding="utf-8")
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout=body, stderr="", returncode=0, trace_dir=None, artifacts=[],
            )

        monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

        inbox_dir = tmp_path / ".brr" / "inbox"
        responses_dir = tmp_path / ".brr" / "responses"
        task = daemon._run_worker(
            event, tmp_path, responses_dir, cfg, 0,
            account_context=ctx, inbox_dir=inbox_dir,
        )
        return task, ctx, event, inbox_dir, responses_dir

    def _message_rows(self, ctx, task):
        messages_dir = daemon.message_store.run_messages_dir(
            ctx, "Gurio/notify", task.id,
        )
        return daemon.message_store.list_messages(messages_dir)

    def test_single_configured_gate_is_inferred_and_delivered(
        self, tmp_path, monkeypatch,
    ):
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch, configured_gates=("telegram",),
        )

        assert task.status == "done"
        assert task.meta["terminal_route"] == "gate-fallback"
        assert event.get("terminal_suppressed") is True

        [fallback] = protocol.list_done(inbox_dir, "telegram")
        fallback_body = protocol.read_response(responses_dir, fallback["id"])
        assert fallback_body.strip() == "director tick note"

        [row] = self._message_rows(ctx, task)
        # #1205: queuing to the fallback gate is not a platform receipt —
        # this row must read `carried`, never `delivered`, until the gate's
        # own delivery loop (not exercised by this unit test) actually
        # posts it and reconciles a real receipt back.
        assert row["status"] == daemon.message_store.CARRIED
        assert not row.get("platform_gate")
        assert "telegram" in row["reason"]

    def test_cloud_now_capable_sole_gate_delivers_to_the_account_drawer(
        self, tmp_path, monkeypatch,
    ):
        # #1205: cloud declares ``CAN_SEND_UNADDRESSED = True`` now that the
        # server's fresh-send primitive (``POST /v1/daemons/messages``)
        # backs it — a schedule wake's terminal reply resolves through the
        # fallback exactly like any other capable gate. The load-bearing
        # assertion is *where* the synthesized event lands: cloud's own
        # delivery loop drains ``account_context.dispatch_inbox``
        # exclusively (``_start_account_gates`` excludes it from every
        # per-repo gate set), never a repo's local ``inbox_dir`` — the
        # "wrong drawer" half of #1205's mechanism 1, live again the moment
        # cloud became capable of an unaddressed send at all.
        # ``create_event`` never errors on a wrong directory, so a
        # regression here is silent except for this assertion.
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch, configured_gates=("cloud",),
        )

        assert task.meta["terminal_route"] == "gate-fallback"
        # Nothing synthesized into the repo-local inbox beyond the schedule
        # event itself — the fix routes the *write*, it does not widen what
        # this inbox holds.
        assert list(inbox_dir.glob("*.md")) == [event["_path"]]
        [fallback] = protocol.list_done(ctx.dispatch_inbox, "cloud")
        fallback_body = protocol.read_response(ctx.responses_dir, fallback["id"])
        assert fallback_body.strip() == "director tick note"

        [row] = self._message_rows(ctx, task)
        # Queuing to the fallback gate is not a platform receipt (#1205) —
        # this row reads `carried`, never `delivered`, until cloud's own
        # delivery loop (not exercised by this unit test) actually posts it.
        assert row["status"] == daemon.message_store.CARRIED
        assert "cloud" in row["reason"]

    def test_two_capable_candidates_with_no_history_stay_ambiguous(
        self, tmp_path, monkeypatch,
    ):
        # cloud and telegram are both configured and both capable now.
        # Pre-#1205, cloud's incapability made telegram the unambiguous
        # survivor; with two genuine candidates and no conversation history
        # to break the tie, ``_resolve_notify_gate`` resolves to nothing
        # rather than guessing (pinned at the unit level in
        # ``test_outbox.py``; this exercises the same decision through the
        # real ``_run_worker`` path).
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch, configured_gates=("cloud", "telegram"),
        )

        assert task.meta["terminal_route"] == "undeliverable"
        assert protocol.list_done(inbox_dir, "telegram") == []
        assert protocol.list_done(ctx.dispatch_inbox, "cloud") == []

        [row] = self._message_rows(ctx, task)
        assert row["status"] == daemon.message_store.UNDELIVERABLE

    def test_two_capable_candidates_prefer_recent_activity(
        self, tmp_path, monkeypatch,
    ):
        # Seeding one candidate's conversation history breaks the tie the
        # previous test leaves unresolved — same recent-activity tiebreak as
        # ``test_resolve_notify_gate_ambiguous_prefers_most_recently_active_thread``
        # in ``test_outbox.py``, exercised here with cloud as a genuine
        # (now-capable) second candidate.
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch, configured_gates=("cloud", "telegram"),
            seed_conversations=[("telegram:1:0", 30)],
        )

        assert task.meta["terminal_route"] == "gate-fallback"
        assert protocol.list_done(ctx.dispatch_inbox, "cloud") == []
        [fallback] = protocol.list_done(inbox_dir, "telegram")
        fallback_body = protocol.read_response(responses_dir, fallback["id"])
        assert fallback_body.strip() == "director tick note"

        [row] = self._message_rows(ctx, task)
        assert row["status"] == daemon.message_store.CARRIED
        assert "telegram" in row["reason"]

    def test_zero_configured_gates_stays_undeliverable(self, tmp_path, monkeypatch):
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch, configured_gates=(),
        )

        assert task.meta["terminal_route"] == "undeliverable"
        # Nothing synthesized beyond the schedule event's own retirement.
        assert list(inbox_dir.glob("*.md")) == [event["_path"]]

        [row] = self._message_rows(ctx, task)
        assert row["status"] == daemon.message_store.UNDELIVERABLE

    def test_several_configured_gates_with_no_explicit_key_stays_undeliverable(
        self, tmp_path, monkeypatch,
    ):
        # Ambiguous — brr does not guess which correspondent was meant.
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch, configured_gates=("telegram", "slack"),
        )

        assert task.meta["terminal_route"] == "undeliverable"
        assert list(inbox_dir.glob("*.md")) == [event["_path"]]

        [row] = self._message_rows(ctx, task)
        assert row["status"] == daemon.message_store.UNDELIVERABLE

    def test_ambiguous_gates_resolve_via_the_runs_own_conversation(
        self, tmp_path, monkeypatch,
    ):
        # Two candidates would otherwise be ambiguous — but this run's own
        # conversation_key names a thread telegram owns, so it is not
        # actually ambiguous: the run carries the answer.
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch, configured_gates=("telegram", "slack"),
            event_conversation_key="telegram:555:0",
        )

        assert task.meta["terminal_route"] == "gate-fallback"
        [fallback] = protocol.list_done(inbox_dir, "telegram")
        fallback_body = protocol.read_response(responses_dir, fallback["id"])
        assert fallback_body.strip() == "director tick note"

        [row] = self._message_rows(ctx, task)
        assert row["status"] == daemon.message_store.CARRIED
        assert "telegram" in row["reason"]

    def test_ambiguous_gates_resolve_via_the_repos_most_recent_thread(
        self, tmp_path, monkeypatch,
    ):
        # The run's own conversation (schedule:default) owns neither
        # candidate — but the repo's most recently active thread is a slack
        # one, so that is who hears it rather than nobody.
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch, configured_gates=("telegram", "slack"),
            seed_conversations=[
                ("telegram:1:0", 600),  # older
                ("slack:general:0", 30),  # newer — this one wins
            ],
        )

        assert task.meta["terminal_route"] == "gate-fallback"
        [fallback] = protocol.list_done(inbox_dir, "slack")
        fallback_body = protocol.read_response(responses_dir, fallback["id"])
        assert fallback_body.strip() == "director tick note"

        [row] = self._message_rows(ctx, task)
        assert row["status"] == daemon.message_store.CARRIED
        assert "slack" in row["reason"]

    def test_explicit_notify_gate_wins_over_single_gate_inference(
        self, tmp_path, monkeypatch,
    ):
        # Two candidates would otherwise be ambiguous (previous test) — the
        # explicit key resolves it outright, to the *named* gate, not
        # necessarily the one inference would have picked.
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch,
            cfg_extra={"notify.gate": "slack"},
            configured_gates=("telegram", "slack"),
        )

        assert task.meta["terminal_route"] == "gate-fallback"
        assert protocol.list_done(inbox_dir, "telegram") == []
        [fallback] = protocol.list_done(inbox_dir, "slack")
        fallback_body = protocol.read_response(responses_dir, fallback["id"])
        assert fallback_body.strip() == "director tick note"

        [row] = self._message_rows(ctx, task)
        assert row["status"] == daemon.message_store.CARRIED
        assert "slack" in row["reason"]

    def test_duplicate_terminal_never_fires_the_fallback(self, tmp_path, monkeypatch):
        # Single-delivery invariant: a terminal stream that exactly
        # duplicates a reply this run already delivered mid-run must stay
        # suppressed as a duplicate, never re-routed through notify.gate —
        # two channels would double-post the same text. Note this
        # combination (an *unowned*-source run whose terminal stream is
        # also a *duplicate*) can't be produced by the mid-run outbox drain
        # today — the digest that arms the dedupe is only recorded for a
        # reply the drain itself judged deliverable
        # (``_drain_outbox``: ``if not ppath: continue`` skips the
        # bookkeeping before it, and a schedule-sourced reply is never
        # deliverable in the first place). So this pins the *ordering* the
        # decision site guarantees directly — duplicate is checked before
        # notify.gate is even resolved — by forcing the duplicate verdict
        # the dedupe would produce if it ever could reach this shape,
        # rather than the (currently unreachable) natural path to it.
        task, ctx, event, inbox_dir, responses_dir = self._run(
            tmp_path, monkeypatch, configured_gates=("telegram",),
            duplicate=True,
        )

        assert task.meta["terminal_route"] == "duplicate"
        # notify.gate was never even consulted: no fallback event landed.
        assert protocol.list_done(inbox_dir, "telegram") == []


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


def test_start_settles_event_done_and_records_run_outcome_on_error(
    tmp_path, monkeypatch,
):
    """Was `test_start_preserves_error_event_status`, pinning the pre-split
    defect this cut fixes: a run ending `error` used to overwrite the
    letter's own `status:` with the run's outcome word
    (design-the-post.md §THE FIELD TWO MACHINES WRITE). Now the letter
    settles at `done` and the outcome lands in its own `run_outcome:` key."""
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

    assert statuses == ["processing", "done"]
    assert event.get("run_outcome") == "error"


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


# ── #1195 rec 3: a cpu-informed default, opt-in behind `spawn.max_concurrent_auto` ──
#
# The flat default above (4) is pinned by `test_max_concurrent_spawns_config_parsing`
# as machine-independent — asserted with no `os.cpu_count()` dependency at all. #1195
# rec 3 asks whether the *default* should instead scale with the box (measured: 5
# concurrent strands each running a full gate drove a 16-core box to a 15-minute load
# average of 14.97). Swapping the unset-default itself would break that pinned test on
# any host whose core count isn't 16 — most CI runners included — so the computed
# default is wired in as an *opt-in* (`spawn.max_concurrent_auto`), never as a silent
# change to what `{}` resolves to. See the #1195 rec 3 report section for why this
# reconciliation was made instead of wiring the formula in as the unconditional
# default, and the formula itself flagged there as a recommendation, not a decision.


def test_cpu_scaled_max_concurrent_spawns_follows_the_recommended_formula(monkeypatch):
    """`max(2, cpu_count() // 4)` — the formula #1195 rec 3 names as a starting
    point. Pinned here as what the mechanism computes today; the report leaves
    the formula itself as a recommendation for the maintainer to confirm."""
    monkeypatch.setattr(daemon.os, "cpu_count", lambda: 16)
    assert daemon._cpu_scaled_max_concurrent_spawns() == 4
    monkeypatch.setattr(daemon.os, "cpu_count", lambda: 8)
    assert daemon._cpu_scaled_max_concurrent_spawns() == 2
    monkeypatch.setattr(daemon.os, "cpu_count", lambda: 4)
    assert daemon._cpu_scaled_max_concurrent_spawns() == 2  # floor, not 1
    monkeypatch.setattr(daemon.os, "cpu_count", lambda: 1)
    assert daemon._cpu_scaled_max_concurrent_spawns() == 2  # floor, not 0


def test_cpu_scaled_max_concurrent_spawns_falls_back_when_cpu_count_is_unknown(
    monkeypatch,
):
    """`os.cpu_count()` is documented to return `None` when the count can't be
    determined (containers with a restricted `/proc`, some sandboxes) — the
    computed path degrades to the flat constant rather than crashing on
    `None // 4`."""
    monkeypatch.setattr(daemon.os, "cpu_count", lambda: None)
    assert daemon._cpu_scaled_max_concurrent_spawns() == daemon._MAX_CONCURRENT_SPAWNS_DEFAULT


def test_max_concurrent_spawns_auto_opt_in_uses_the_cpu_scaled_default(monkeypatch):
    """Unset + the opt-in flag ⇒ the computed default, not the flat constant —
    the mechanism rec 3 asks for, reachable without disturbing what `{}}` alone
    (no opt-in) resolves to."""
    monkeypatch.setattr(daemon.os, "cpu_count", lambda: 8)
    assert daemon._max_concurrent_spawns({"spawn.max_concurrent_auto": True}) == 2
    # The flag alone changes nothing when absent or false — same flat default
    # `test_max_concurrent_spawns_config_parsing` already pins for `{}`.
    assert daemon._max_concurrent_spawns({"spawn.max_concurrent_auto": False}) == 4


def test_max_concurrent_spawns_explicit_value_always_wins_over_auto(monkeypatch):
    """"Never overriding an explicit config value" — rec 3's own wording — holds
    even with the opt-in flag set."""
    monkeypatch.setattr(daemon.os, "cpu_count", lambda: 8)
    assert (
        daemon._max_concurrent_spawns(
            {"spawn.max_concurrent": 6, "spawn.max_concurrent_auto": True}
        )
        == 6
    )


def test_max_concurrent_spawns_invalid_explicit_value_with_auto_falls_back_to_computed(
    monkeypatch,
):
    """A non-numeric or boolean explicit value is not a deliberate choice —
    with the opt-in flag set, it degrades to the *computed* default, the same
    way it degrades to the flat one without the flag."""
    monkeypatch.setattr(daemon.os, "cpu_count", lambda: 8)
    assert (
        daemon._max_concurrent_spawns(
            {"spawn.max_concurrent": "bogus", "spawn.max_concurrent_auto": True}
        )
        == 2
    )


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
            status="done", meta={"strand": True},
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
            spawn_immediate=True, strand=True, environment="worktree",
        )

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert checked.is_set()


def test_concurrent_spawn_pool_releases_slot_when_reap_notify_crashes(tmp_path, monkeypatch):
    """#880 §1 guard: reaping a finished spawn calls ``_notify_spawn_parent``
    outside any try/except of its own — a real crash there (a bad inbox
    write, a permissions error) must still release the accepted-spawn slot,
    or that slot is gone for the rest of the daemon process's life, no
    restart required to lose it, just one unlucky notify. The exception
    still propagates (unchanged behavior, out of this task's scope to
    harden) — only the accounting must not leak."""
    write_repo_scaffold(tmp_path)
    monkeypatch.setattr(daemon, "_spawn_pool_accepted", set())

    def fake_run_worker(event, *_args, **_kwargs):
        eid = event["id"]
        return Run(
            id=f"task-{eid}", event_id=eid, body="spawned",
            status="done", meta={"strand": True},
        )

    def crashing_notify(*_a, **_k):
        raise RuntimeError("notify blew up")

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(
        daemon.conf, "load_config", lambda _root: {"spawn.max_concurrent": 1},
    )
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.02)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_notify_spawn_parent", crashing_notify)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    protocol.create_event(
        tmp_path / ".brr" / "inbox", "spawn", "spawned work",
        spawn_immediate=True, strand=True, environment="worktree",
    )

    with pytest.raises(RuntimeError, match="notify blew up"):
        daemon.start(tmp_path)

    assert daemon._spawn_pool_accepted_count() == 0


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
            status="done", meta={"strand": True},
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
        spawn_immediate=True, strand=True, environment="worktree",
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
            status="done", meta={"strand": True},
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
            spawn_immediate=True, strand=True, environment="worktree",
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


def test_dev_reload_does_not_hold_the_resident_seat_for_active_spawns(
    tmp_path, monkeypatch,
):
    """A reload waits for spawned workers without making a user wait too.

    A parent can edit Python, finish, and leave concurrent ``spawn:``
    children running.  The next loop sees ``current is None`` and a pending
    reload.  Before this regression, it called ``pool.shutdown(wait=True)``
    immediately, so a fresh user event could not use the deliberately
    reserved resident executor thread until every worker ended.  That turns
    background worker duration into correspondent latency.
    """
    write_repo_scaffold(tmp_path)
    inbox = tmp_path / ".brr" / "inbox"
    release_spawns = threading.Event()
    spawns_ready = threading.Event()
    resident_started = threading.Event()
    spawn_count = 0
    spawn_lock = threading.Lock()
    order: list[str] = []

    class ReloadRequested:
        last_changed = ["src/brr/daemon.py"]

        def changed(self):
            return spawns_ready.is_set()

    def fake_run_worker(event, *_args, **_kwargs):
        nonlocal spawn_count
        if event.get("spawn_immediate"):
            with spawn_lock:
                spawn_count += 1
                if spawn_count == 2:
                    protocol.create_event(inbox, "telegram", "new user message")
                    spawns_ready.set()
            release_spawns.wait(timeout=2)
            order.append(f"spawn-{event['id']}-done")
            return Run(
                id=f"run-{event['id']}", event_id=event["id"], body="worker",
                status="done", meta={"strand": True},
            )
        resident_started.set()
        order.append("resident-started")
        release_spawns.set()
        return Run(
            id="run-resident", event_id=event["id"], body="new user message",
            status="done",
        )

    def stop_after_reload():
        order.append("reexec")
        raise StopIteration

    # The unfixed shutdown path otherwise waits for intentionally blocked
    # workers.  The timer makes it fail deterministically rather than hang;
    # the healthy path starts the resident before this escape hatch matters.
    timer = threading.Timer(0.3, release_spawns.set)
    timer.start()
    monkeypatch.setattr(
        daemon.reload_mod.DevReloadWatcher,
        "for_repo",
        classmethod(lambda cls, _repo_root: ReloadRequested()),
    )
    monkeypatch.setattr(daemon.reload_mod, "reexec", stop_after_reload)
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(
        daemon.conf, "load_config", lambda _root: {"spawn.max_concurrent": 7},
    )
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.01)
    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daemon, "_notify_spawn_parent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daemon, "_fire_due_schedules", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    for i in range(2):
        protocol.create_event(
            inbox, "spawn", f"worker {i}", spawn_immediate=True,
            strand=True, environment="worktree",
        )

    try:
        with pytest.raises(StopIteration):
            daemon.start(tmp_path, dev_reload=True)
    finally:
        timer.cancel()
        release_spawns.set()

    assert resident_started.is_set(), (
        "a pending reload must not hold the free resident seat behind workers"
    )
    assert order.index("resident-started") < order.index("reexec")


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


def test_commits_between_raises_on_timeout_not_empty(tmp_path):
    """#1308: a probe that cannot measure must say so, not answer zero.

    ``_commits_between`` shells out to ``git log``; a ``TimeoutExpired``
    used to propagate uncaught (returning nothing at all, effectively an
    unhandled crash from the caller's perspective) and a non-zero
    returncode used to read as "no commits". Neither is the same fact as
    "measured the range and found it empty" — both must now raise
    ``_CommitProbeUnresolvable`` so ``publish()`` can tell the difference.
    """
    import subprocess as subprocess_mod

    def fake_run(*_a, **_k):
        raise subprocess_mod.TimeoutExpired(cmd=["git", "log"], timeout=10)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(daemon.subprocess, "run", fake_run)
        with pytest.raises(daemon._CommitProbeUnresolvable):
            daemon._commits_between(tmp_path, "main", "brr/topic")


def test_commits_between_raises_on_nonzero_returncode(tmp_path):
    """A resolvable-looking call that still exits non-zero (lock
    contention, an unresolvable ref) is a failed measurement, not a zero
    one — the old code returned ``[]`` for both "genuinely no commits"
    and "git could not tell me", indistinguishably (#1308)."""
    init_git_repo(tmp_path)
    commit_files(tmp_path, {"seed.txt": "seed\n"})
    with pytest.raises(daemon._CommitProbeUnresolvable):
        # "definitely-not-a-ref" resolves nowhere, so git log exits non-zero
        # rather than answering "zero commits".
        daemon._commits_between(tmp_path, "definitely-not-a-ref", "HEAD")


def test_publish_pushes_first_publish_branch_when_commit_probe_times_out(
    tmp_path, monkeypatch, capsys,
):
    """The exact #1308 shape: a strand's first push of a brand-new branch,
    where ``_commits_since_seed``'s internal ``git`` probe hangs.

    Before the fix, ``TimeoutExpired`` raised inside ``_commits_since_seed``
    propagated all the way up through ``publish()``'s own
    ``except (subprocess.TimeoutExpired, FileNotFoundError): pass`` — a
    handler written for the final ``git push`` subprocess call, not this
    internal probe — and the branch was silently never pushed. The fix
    must push anyway rather than trust an unmeasurable "zero".
    """
    repo, origin = _host_publish_repo(tmp_path)
    subprocess.run(
        ["git", "checkout", "-q", "-b", "brr/new-work"], cwd=repo, check=True,
    )
    commit_files(repo, {"work.txt": "brand new branch\n"}, message="new work")

    real_run = subprocess.run

    def flaky_run(cmd, *args, **kwargs):
        if cmd[:2] in (["git", "merge-base"], ["git", "log"]):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(daemon.subprocess, "run", flaky_run)

    task = Run(
        id="run-1308",
        event_id="evt-1308",
        body="first publish",
        status="done",
        meta={"publish_branch": "brr/new-work"},
    )

    daemon.publish(repo, task)

    # The branch reached origin despite the probe never being able to
    # measure a commit count — a push a correspondent can inspect, not a
    # silently discarded one.
    remote_branches = subprocess.run(
        ["git", "branch", "--list", "brr/new-work"], cwd=origin,
        capture_output=True, text=True, check=True,
    ).stdout
    assert "brr/new-work" in remote_branches
    out = capsys.readouterr().out
    assert "could not measure" in out
    assert "pushing brr/new-work" in out


def test_refuse_publish_does_not_blame_746_for_a_missing_repo(tmp_path, capsys):
    """#1408: ``gitops.toplevel`` returns ``None`` for two different causes —
    a repointed ``core.worktree`` (#746) *and* "this is not a git repository
    at all" — and ``_refuse_publish`` used to print the confident #746
    diagnosis for both. ``tmp_path`` here is never ``git init``ed (the exact
    shape every other test in this module hits when a repo isn't set up),
    so this is the ordinary case, not the exotic one — and the message must
    not assert a cause the code never established.
    """
    task = Run(id="run-1408a", event_id="evt-1408a", body="x", status="done")

    mismatch = daemon._refuse_publish(task, tmp_path, "publish")

    assert mismatch is not None
    assert mismatch["cause"] == "not-a-repo"
    out = capsys.readouterr().out
    assert "REFUSING publish" in out
    assert "#746" not in out
    assert "core.worktree" not in out
    detail = json.loads(task.meta["stray_host_write_detail"])
    assert detail["cause"] == "not-a-repo"


def test_refuse_publish_names_746_for_a_genuine_core_worktree_repoint(
    tmp_path, capsys,
):
    """The other side of #1408: a *real* ``core.worktree`` repoint — the
    shared git dir's config pointed at some other tree, exactly #746's
    incident — must still land the #746 diagnosis, because this time the
    code actually established it: ``gitops.toplevel`` resolves a real path
    that disagrees with ``repo_root``, not ``None``.
    """
    repo = tmp_path / "repo"
    other = tmp_path / "elsewhere"
    init_git_repo(repo)
    other.mkdir()
    subprocess.run(
        ["git", "config", "core.worktree", str(other)], cwd=repo, check=True,
    )
    task = Run(id="run-1408b", event_id="evt-1408b", body="x", status="done")

    mismatch = daemon._refuse_publish(task, repo, "publish")

    assert mismatch is not None
    assert mismatch["cause"] == "core-worktree-mismatch"
    out = capsys.readouterr().out
    assert "REFUSING publish" in out
    assert "#746" in out
    assert "core.worktree" in out
    detail = json.loads(task.meta["stray_host_write_detail"])
    assert detail["cause"] == "core-worktree-mismatch"


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


def test_status_done_race_still_dispatches_follow_up_promptly(tmp_path, monkeypatch):
    """#post-delivery-attend-removal: does killing the daemon-owned attend
    dwell (and its ``inbox_wake()`` re-arm) reopen a race where a follow-up
    landing right as a run wraps up sits until some later poll tick instead
    of dispatching promptly?

    Forces the worst case deterministically instead of hoping for lucky
    thread scheduling: the primary worker is paused inside
    ``_emit_preserved_containers`` — the last real hook that still runs
    before the (now-deleted) attend call site, so it fires at the same
    point ``update_status("done")`` already ran. The follow-up event is
    created while the primary is still paused there, and release is
    withheld until at least one full main-loop iteration has cleared
    ``protocol.inbox_wake()`` while ``current`` was still busy — exactly the
    seam the removed re-arm existed to close (see the deleted comment this
    test used to pin, kept here in spirit: "by the time attendance ends the
    wake can already be consumed"). If nothing re-signals the loop on
    release, the follow-up can only be noticed on the next poll tick.
    """
    write_repo_scaffold(tmp_path)
    inbox = tmp_path / ".brr" / "inbox"
    make_event(tmp_path, eid="evt-primary", source="telegram")

    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(
        daemon.sync, "refresh_before_run",
        lambda _repo, *, target_branches, cfg=None: daemon.sync.SyncResult(),
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

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    # Generous relative to the synchronization timeouts below (not tuned to
    # a bare-metal quiet box) so "waited out a poll tick" (~1x this value)
    # and "prompt" (scheduling overhead only) stay cleanly separated even
    # on a loaded host running many tests concurrently.
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 2.0)

    # `_run_worker_and_finalize` does real capture/ledger/publish/dominion
    # work *after* `_run_worker` returns and *before* its own return (see
    # its `finally` block) — that tail is real production surface this test
    # deliberately exercises (the fix under test lives at the very end of
    # it), but none of these individual steps are what's under test here,
    # and against a scaffold with no real git repo they are slow, flaky, or
    # both. Neutered to fast no-ops so the only variable left is the wake
    # race itself.
    for name in (
        "_stray_host_write", "_capture_knowledge", "_weld_capture",
        "publish", "publish_default_branch", "_persist_run_body",
        "_persist_run_state_doc", "_persist_boundaries_summary",
        "_capture_control_files", "_capture_dominion",
        "_attribute_schedule_entries",
    ):
        monkeypatch.setattr(daemon, name, lambda *_a, **_k: None)
    monkeypatch.setattr(daemon.run_ledger, "append_closed_run", lambda *_a, **_k: None)

    reached_tail = threading.Event()
    release_worker = threading.Event()

    def paused_emit_preserved_containers(_emit, _task):
        reached_tail.set()
        release_worker.wait(timeout=30)

    monkeypatch.setattr(
        daemon, "_emit_preserved_containers", paused_emit_preserved_containers,
    )

    scan_calls = {"n": 0}
    stop_flag = {"go": False}
    real_dispatchable_targets = daemon._dispatchable_targets

    def counting_dispatchable_targets(account_context, repo_root, cfg):
        scan_calls["n"] += 1
        if stop_flag["go"]:
            raise StopIteration
        return real_dispatchable_targets(account_context, repo_root, cfg)

    monkeypatch.setattr(daemon, "_dispatchable_targets", counting_dispatchable_targets)

    dispatch_times: dict[str, float] = {}
    real_set_status = daemon.protocol.set_status

    def tracking_set_status(event, status):
        if status == "processing":
            dispatch_times.setdefault(event.get("id"), time.monotonic())
        return real_set_status(event, status)

    monkeypatch.setattr(daemon.protocol, "set_status", tracking_set_status)

    result: dict[str, object] = {}

    def controller():
        if not reached_tail.wait(timeout=30):
            result["error"] = "primary worker never reached the finalize tail"
            stop_flag["go"] = True
            return
        pre_count = scan_calls["n"]
        follow_up_path = protocol.create_event(
            inbox, "telegram", "oh wait, also this", trust_tier="owner",
        )
        follow_up_id = protocol._read_event(follow_up_path)["id"]
        # Force at least one full main-loop iteration to clear the wake
        # while `current` (the primary) is still busy — the exact seam the
        # removed re-arm existed to close.
        deadline = time.monotonic() + 30
        while scan_calls["n"] < pre_count + 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        result["forced_extra_iteration"] = scan_calls["n"] >= pre_count + 2
        release_at = time.monotonic()
        release_worker.set()
        deadline2 = time.monotonic() + 30
        while follow_up_id not in dispatch_times and time.monotonic() < deadline2:
            time.sleep(0.005)
        result["dispatched"] = follow_up_id in dispatch_times
        if result["dispatched"]:
            result["delay"] = dispatch_times[follow_up_id] - release_at
        stop_flag["go"] = True

    thread = threading.Thread(target=controller)
    thread.start()

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)
    thread.join(timeout=30)

    assert "error" not in result, result.get("error")
    assert result.get("forced_extra_iteration"), (
        "test setup failed to force the race window: the loop never "
        "re-cleared the wake while the primary was still busy"
    )
    assert result.get("dispatched"), "follow-up was never dispatched at all"
    # "Promptly" = well inside one poll tick, not "eventually, after
    # waiting out a whole extra _SCAN_INTERVAL because the wake signal that
    # arrived during the busy window was consumed for nothing."
    assert result["delay"] < daemon._SCAN_INTERVAL / 2, (
        f"follow-up dispatch took {result['delay']:.3f}s after release "
        f"(scan interval {daemon._SCAN_INTERVAL}s) — it waited out a poll "
        "tick instead of being picked up promptly"
    )


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


# ── delivery: — "would my reply reach a human if I ended right now?" ──


@pytest.fixture(autouse=True)
def _clear_notify_gate_cache():
    """Drop the per-run notify-gate memo around every test in this module.

    :func:`daemon._cached_notify_gate` is a process-global keyed by run id,
    and the fixtures below all use ``run-1`` — without this, the first test
    to resolve a gate would answer for every later one. Production drops an
    entry in ``_run_worker_and_finalize``'s ``finally``; a test never
    reaches that, so it clears here.
    """
    daemon._notify_gate_cache.clear()
    yield
    daemon._notify_gate_cache.clear()


def test_notify_gate_is_resolved_once_per_run_not_once_per_heartbeat(
    tmp_path, monkeypatch,
):
    """The heartbeat may not pay the conversation-store scan every tick.

    ``_live_delivery_projection`` runs on every heartbeat. For the run type
    it exists to serve — a ``schedule`` fire, whose conversation key no chat
    gate owns — ``_resolve_notify_gate`` falls through to
    ``_notify_gate_by_recent_activity``, which stats every file under every
    conversation key. That scan's own docstring calls itself rare and
    per-run.

    Drive red: delete the memo in ``_cached_notify_gate`` (call
    ``_resolve_notify_gate`` directly) and this fails with calls == 3.
    """
    monkeypatch.setattr(
        daemon, "_gate_can_deliver",
        lambda _brr, gate: gate in ("telegram", "slack"),
    )
    calls = []
    real_scan = daemon._notify_gate_by_recent_activity

    def counting_scan(brr_dir, candidates):
        calls.append(tuple(candidates))
        return real_scan(brr_dir, candidates)

    monkeypatch.setattr(daemon, "_notify_gate_by_recent_activity", counting_scan)
    task = Run(id="run-heartbeat", event_id="evt-1", body="", source="schedule")
    task.conversation_key = "schedule:nightly"
    for _ in range(3):
        daemon._live_delivery_projection(
            task, {}, tmp_path, already_delivered=False,
        )
    # Sanity: the expensive path must actually be reached, or this test
    # passes over a branch it never entered.
    assert calls, (
        "fixture must reach the recent-activity scan — two candidate gates "
        "and a conversation key no candidate owns"
    )
    assert len(calls) == 1, f"scan ran {len(calls)}x across 3 heartbeats"


def test_forget_notify_gate_drops_the_entry_so_the_daemon_does_not_accumulate(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram",
    )
    task = Run(id="run-forget", event_id="evt-1", body="", source="schedule")
    daemon._live_delivery_projection(task, {}, tmp_path, already_delivered=False)
    assert "run-forget" in daemon._notify_gate_cache, (
        "the projection must populate the cache, or the next assertion is vacuous"
    )
    daemon._forget_notify_gate("run-forget")
    assert "run-forget" not in daemon._notify_gate_cache


def test_the_finalizer_actually_drops_the_cached_notify_gate():
    """`_forget_notify_gate` must be *called* from the run finalizer.

    Written after the direct-call test above stayed green when the call
    site was deleted — it guarded the function, not its use, which is a
    check with no teeth. Driving the real `_run_worker_and_finalize` to
    prove this would mean standing up a runner; an AST assertion on the
    call site is the cheap guard that can still fail, and this repo
    already uses that idiom (`tests/test_spawn_row_contract.py`).

    Drive red: delete the `_forget_notify_gate(...)` call in
    `_run_worker_and_finalize`'s `finally:` block.
    """
    import ast

    tree = ast.parse(Path(daemon.__file__).read_text(encoding="utf-8"))
    finalizer = next(
        (
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_run_worker_and_finalize"
        ),
        None,
    )
    # Sanity: a rename must break this loudly rather than pass over nothing.
    assert finalizer is not None, (
        "_run_worker_and_finalize not found in daemon.py — this guard has "
        "been silently disarmed by a rename"
    )
    called = {
        node.func.id
        for node in ast.walk(finalizer)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_forget_notify_gate" in called, (
        "_run_worker_and_finalize must drop the run's cached notify gate; "
        "without it a long-lived daemon holds one entry per run it ever ran"
    )


def test_an_uncacheable_run_still_resolves(tmp_path, monkeypatch):
    """A task with no id pays the full resolution rather than caching under "".

    Correctness is unchanged either way; this pins that the id-less path
    answers at all, so a future refactor cannot make "no id" mean "no gate".
    """
    monkeypatch.setattr(
        daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram",
    )
    task = Run(id="", event_id="evt-1", body="", source="schedule")
    projection = daemon._live_delivery_projection(
        task, {}, tmp_path, already_delivered=False,
    )
    assert projection["gate"] == "telegram"
    assert daemon._notify_gate_cache == {}



def test_delivery_projection_unknown_without_a_source(tmp_path):
    task = Run(id="run-1", event_id="evt-1", body="", source="")
    assert daemon._live_delivery_projection(task, {}, tmp_path, already_delivered=False) == {
        "known": False,
    }


def test_delivery_projection_gate_owned_source_lands_sole(tmp_path):
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    projection = daemon._live_delivery_projection(
        task, {}, tmp_path, already_delivered=False,
    )
    assert projection["known"] is True
    assert projection["would_land"] is True
    assert projection["route"] == "gate-sole"
    assert projection["gate"] == "telegram"
    assert projection["reason"] is None


def test_delivery_projection_gate_owned_source_already_delivered_is_extra(tmp_path):
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    projection = daemon._live_delivery_projection(
        task, {}, tmp_path, already_delivered=True,
    )
    assert projection["route"] == "gate-extra"
    assert projection["already_delivered"] is True


def test_delivery_projection_dispatch_edge_for_a_spawned_strand(tmp_path):
    task = Run(id="run-1", event_id="evt-1", body="", source="spawn")
    task.meta["spawn_parent_run_id"] = "run-parent"
    projection = daemon._live_delivery_projection(
        task, {}, tmp_path, already_delivered=False,
    )
    assert projection["would_land"] is True
    assert projection["route"] == "dispatch-edge"
    assert projection["gate"] is None


def test_delivery_projection_unowned_source_no_candidates_names_the_source(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, _gate: False)
    task = Run(id="run-1", event_id="evt-1", body="", source="schedule")
    projection = daemon._live_delivery_projection(
        task, {}, tmp_path, already_delivered=False,
    )
    assert projection["would_land"] is False
    assert projection["route"] == "undeliverable"
    assert projection["gate"] is None
    assert projection["reason"] == "no gate owns schedule events"


def test_delivery_projection_unowned_source_resolves_via_notify_gate(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram",
    )
    task = Run(id="run-1", event_id="evt-1", body="", source="schedule")
    projection = daemon._live_delivery_projection(
        task, {}, tmp_path, already_delivered=False,
    )
    assert projection["would_land"] is True
    assert projection["route"] == "gate-fallback"
    assert projection["gate"] == "telegram"
    assert projection["reason"] is None


def test_delivery_projection_ambiguous_candidates_names_the_count(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        daemon, "_gate_can_deliver",
        lambda _brr, gate: gate in ("telegram", "slack"),
    )
    task = Run(id="run-1", event_id="evt-1", body="", source="schedule")
    projection = daemon._live_delivery_projection(
        task, {}, tmp_path, already_delivered=False,
    )
    assert projection["would_land"] is False
    assert projection["reason"] == "notify.gate unresolved: 2 candidate gate(s)"


def test_delivery_projection_explicit_notify_gate_not_deliverable(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, _gate: False)
    task = Run(id="run-1", event_id="evt-1", body="", source="schedule")
    projection = daemon._live_delivery_projection(
        task, {"notify.gate": "slack"}, tmp_path, already_delivered=False,
    )
    assert projection["would_land"] is False
    assert projection["reason"] == "explicit notify.gate='slack' not deliverable here"


def test_delivery_projection_without_brr_dir_stays_conservative(monkeypatch):
    # An ad-hoc caller that omits brr_dir gets no filesystem-backed
    # notify.gate resolution — same "no crash, no guess" contract as the
    # schedule facet's brr_dir-less path above.
    task = Run(id="run-1", event_id="evt-1", body="", source="schedule")
    projection = daemon._live_delivery_projection(
        task, {}, None, already_delivered=False,
    )
    assert projection["would_land"] is False
    assert projection["route"] == "undeliverable"
    assert projection["reason"] == "no gate owns schedule events"


def test_write_live_portal_state_wires_the_delivery_facet(tmp_path):
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        brr_dir=brr_dir,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["delivery"]["known"] is True
    assert payload["delivery"]["route"] == "gate-sole"
    assert payload["delivery"]["gate"] == "telegram"


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


def test_write_live_portal_state_projects_armed_letters(tmp_path):
    """#904: the armed dated-letters snapshot ``schedule.save_armed_letters``
    writes (once per scheduling tick, see test_schedule_daemon.py) is
    projected into the per-run portal-state payload verbatim — hooks.py
    renders it straight off ``payload["schedule"]["armed"]``."""
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    rows = [{
        "id": "ship-the-thing", "when": "2026-08-01T09:00:00Z", "at": 1234.0,
        "heading": "Ship the thing",
        "premise": "the release branch is still green",
    }]
    schedule_mod.save_armed_letters(brr_dir, rows)

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        brr_dir=brr_dir,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schedule"]["armed"] == rows


def test_write_live_portal_state_armed_letters_empty_without_brr_dir(tmp_path):
    """Ad-hoc callers that omit ``brr_dir`` (no daemon-owned schedule
    snapshot to read) get an empty projection, not a crash."""
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schedule"]["armed"] == []


# ── await: — the hold path (#959, collapsed by #1187) ─────────────────


def _armed(**over):
    """A ``task.meta['await']`` record in the collapsed shape."""
    base = {
        "file": None,
        "timeout_seconds": 600.0,
        "armed_at": time.time(),
        "generation": "gen-1",
        "resolved": False,
        "capped": False,
    }
    base.update(over)
    return base


def test_portal_state_await_absent_when_never_armed(tmp_path):
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["await"] == {"armed": False}


def test_portal_state_await_file_trigger_fires(tmp_path):
    """The one surviving condition: a path the daemon cannot otherwise see."""
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    target = tmp_path / "gate.log"
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["await"] = _armed(file=str(target))

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["await"]["armed"] is True
    assert payload["await"]["resolved"] is False

    target.write_text("green\n", encoding="utf-8")
    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["await"] == {
        "armed": True,
        "file": str(target),
        "armed_at": payload["await"]["armed_at"],
        "generation": "gen-1",
        "timeout_seconds": 600.0,
        "deadline": payload["await"]["deadline"],
        "capped": False,
        "resolved": True,
        "outcome": "condition",
        "which": f"file:{target}",
    }
    # Sticky: task.meta itself carries the resolved outcome so a later tick
    # (or a retry attempt reusing this task) doesn't need to re-derive it.
    assert task.meta["await"]["resolved"] is True
    assert task.meta["await"]["outcome"] == "condition"


def test_portal_state_await_resolves_on_any_pending_event(tmp_path):
    """``event`` is the semantics, not a condition anyone had to name: a
    plain ``brnrd await`` resolves on whatever the daemon has."""
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    protocol.create_event(inbox_dir, "telegram", "a follow-up question")
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["await"] = _armed()

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["await"]["resolved"] is True
    assert payload["await"]["outcome"] == "event"
    assert payload["await"]["which"] is None


def test_portal_state_await_excludes_a_completion_pending_before_arming(tmp_path):
    """#1327: a ``spawn_completed`` event this run already rendered — and
    stamped ``observed_by`` (#1146) — never leaves ``status: pending`` until
    run end; the stamp is not removal. Arming a wait *after* that must not
    resolve on it instantly: the snapshot taken at arm time excludes exactly
    this id, so the wait fires only on what's new since arming.

    Before the fix this returns ``resolved: True, outcome: "event"`` — the
    verb becomes a no-op for the rest of the parent's life.
    """
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-parent", event_id="evt-1", body="", source="telegram")
    completed = protocol.create_event(
        inbox_dir, "spawn_completed", "child finished",
        spawn_parent_run_id=task.id,
    )
    completed_id = completed.stem

    # One prior tick renders + stamps the completion `observed_by` this
    # parent — the ordinary "surfaced to the run" path, before any wait is
    # ever armed.
    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )

    # Arm as `_drain_outbox` would: snapshotting this moment's pending set
    # into `armed_pending_ids`. The completion above is already in it.
    task.meta["await"] = _armed(
        armed_at=time.time(), armed_pending_ids=[completed_id],
    )

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["await"]["resolved"] is False


def test_portal_state_await_carries_a_generation_stamp(tmp_path):
    """``brnrd await`` re-arms on every call and ``armed_at`` renders to whole
    seconds — without an exact generation the CLI cannot tell a fresh arming
    from the previous call's sticky-resolved outcome.

    Neuter check (do this by hand, don't ship it): drop ``generation`` from
    the projection in ``_resolve_await_state`` and rerun — this goes red, and
    ``cli.cmd_await``'s stale-answer guard silently degrades to comparing
    ``None`` with ``None`` (i.e. every generation looks stale, and the
    command reports ``pending`` forever).
    """
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["await"] = _armed(generation="gen-abc")

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    assert json.loads(path.read_text(encoding="utf-8"))["await"]["generation"] == "gen-abc"

    # And on the sticky-resolved projection too, which is the one the CLI
    # actually has to disambiguate.
    task.meta["await"]["resolved"] = True
    task.meta["await"]["outcome"] = "timeout"
    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    assert json.loads(path.read_text(encoding="utf-8"))["await"]["generation"] == "gen-abc"


def test_portal_state_await_times_out(tmp_path):
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["await"] = _armed(timeout_seconds=1.0, armed_at=time.time() - 5)

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["await"]["resolved"] is True
    assert payload["await"]["outcome"] == "timeout"
    assert payload["await"]["which"] is None


def test_portal_state_await_extends_keepalive_to_the_deadline(tmp_path):
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    outbox_dir.mkdir(parents=True)
    keepalive_path = outbox_dir / ".keepalive"
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    armed_at = time.time()
    task.meta["await"] = _armed(timeout_seconds=900.0, armed_at=armed_at)

    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        keepalive_path=keepalive_path,
    )

    until = daemon.portals.keepalive_until(keepalive_path)
    assert until is not None
    assert until == pytest.approx(armed_at + 900.0, abs=2)


def test_portal_state_await_never_shortens_an_existing_keepalive(tmp_path):
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    outbox_dir.mkdir(parents=True)
    keepalive_path = outbox_dir / ".keepalive"
    far_future = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 4000)
    )
    keepalive_path.write_text(far_future + "\n", encoding="utf-8")
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["await"] = _armed(timeout_seconds=60.0)

    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        keepalive_path=keepalive_path,
    )

    assert keepalive_path.read_text(encoding="utf-8").strip() == far_future


def test_portal_state_await_capped_at_hard_budget_ceiling_with_advisory(tmp_path):
    """#959: 'a wait that outlives the budget must extend the slot or refuse
    to start' — this run extends, capped at the run's own hard ceiling, and
    says so as an advisory notice rather than silently truncating."""
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    outbox_dir.mkdir(parents=True)
    keepalive_path = outbox_dir / ".keepalive"
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    # 2h — far past the tiny hard cap below.
    task.meta["await"] = _armed(timeout_seconds=7200.0)
    start_monotonic = time.monotonic() - 10  # 10s already elapsed this attempt

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        keepalive_path=keepalive_path,
        hard_cap_seconds=60.0,
        start_monotonic=start_monotonic,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["await"]["capped"] is True
    assert payload["await"]["resolved"] is False  # not timed out — just capped

    until = daemon.portals.keepalive_until(keepalive_path)
    # Capped to roughly (start_monotonic + hard_cap) translated to wall clock
    # — well short of the requested 2h out.
    assert until is not None
    assert until < time.time() + 100

    notices = daemon._read_outbox_notices(outbox_dir)
    assert len(notices) == 1
    assert notices[0]["kind"] == "advisory"
    assert "capped" in notices[0]["text"]

    # A second tick must not re-notice — the cap is a one-time fact.
    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        keepalive_path=keepalive_path,
        hard_cap_seconds=60.0,
        start_monotonic=start_monotonic,
    )
    assert len(daemon._read_outbox_notices(outbox_dir)) == 1


def test_portal_state_await_resolved_state_is_sticky_across_ticks(tmp_path):
    """Once resolved, later ticks reflect the frozen outcome rather than
    re-evaluating — the world moves on, and the first resolution is the one
    that counted."""
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["await"] = _armed(timeout_seconds=1.0, armed_at=time.time() - 5)

    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    assert task.meta["await"]["outcome"] == "timeout"

    # Mutate as if evaluate() would now see something different — sticky
    # state must not flip.
    task.meta["await"]["file"] = str(tmp_path)
    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["await"]["outcome"] == "timeout"


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


def test_write_live_portal_state_coexisting_runs_reflects_presence(tmp_path, monkeypatch):
    """``brr_dir`` wires a *live*, heartbeat-refreshed sibling-run read —
    the same presence query already used for the wake-time-only
    ``present_snapshot`` (``_run_worker``'s "Other thoughts awake right
    now"), extended to the portal-state facet a running resident's hooks
    surface after every tool call.

    ``spawn_pool`` used to ride the same ``coexisting_snapshot`` read this
    facet is built from; #880 §1 moved its source to the accepted-spawn
    registry (``_spawn_pool_accepted_count``) precisely because presence
    undercounts a child that's been accepted onto the pool but hasn't
    registered presence yet. So a sibling showing up in presence (this
    test's third act) no longer moves ``spawn_pool`` at all — that's the
    point of the decoupling, covered on its own registry-driven terms by
    ``test_write_live_portal_state_spawn_pool_counts_accepted_not_registered``
    below. Isolated from other tests' registry state via a fresh empty set."""
    monkeypatch.setattr(daemon, "_spawn_pool_accepted", set())
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

    # No brr_dir given → the sibling-list facet stays "unimplemented" (no
    # presence collector wired at this call site), but spawn_pool is
    # independent of that wiring — it's main-loop bookkeeping, not a
    # presence read, so it reports the registry's real (empty) count.
    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    assert _read_facet()["status"] == "unimplemented"
    assert _read_facet()["spawn_pool"] == {
        "max_concurrent": 4, "active": 0, "available": 4,
    }

    # brr_dir given, nobody else present → affirmative-absent; spawn_pool
    # unchanged (still nothing accepted).
    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        brr_dir=brr_dir,
    )
    assert _read_facet()["status"] == "absent"
    assert _read_facet()["spawn_pool"] == {
        "max_concurrent": 4, "active": 0, "available": 4,
    }

    # A sibling registers itself (a concurrent spawn, an ad-hoc session) →
    # the sibling-list facet goes known, self excluded by run_id — but
    # spawn_pool does NOT move: presence is identity, not capacity, and no
    # spawn was ever accepted onto *this* pool in this test.
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
        "max_concurrent": 4, "active": 0, "available": 4,
    }


def test_write_live_portal_state_spawn_pool_counts_accepted_not_registered(tmp_path, monkeypatch):
    """#880 §1, the regression: a child ``_loop`` has accepted onto the pool
    (``_spawn_pool_accept``, the same call the submission site makes the
    instant ``pool.submit`` returns) counts as active *before* it has ever
    registered presence — the exact dispatch→start window the ticket
    reports as ~2-3 minutes wide on a saturated pool. Fails on pre-#880
    code, which derived ``spawn_pool`` from
    ``presence.list_active(...).is_subspawn`` and therefore read a
    queued-but-not-started child as zero."""
    monkeypatch.setattr(daemon, "_spawn_pool_accepted", set())
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    task = Run(id="run-self", event_id="evt-1", body="", source="telegram")

    # Two children accepted onto the pool — neither has registered presence
    # (no presence.register call for either), which is exactly the window
    # between _loop's pool.submit and the child's own _run_worker starting.
    daemon._spawn_pool_accept("evt-child-1")
    daemon._spawn_pool_accept("evt-child-2")

    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        brr_dir=brr_dir,
    )
    payload = json.loads(
        (outbox_dir / "portal-state.json").read_text(encoding="utf-8")
    )
    spawn_pool = payload["resources"]["coexisting_runs"]["spawn_pool"]
    assert spawn_pool == {"max_concurrent": 4, "active": 2, "available": 2}

    # Release one (the reap path) — the count drops immediately, still with
    # no presence entry ever having existed for either child.
    daemon._spawn_pool_release("evt-child-1")
    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        brr_dir=brr_dir,
    )
    payload = json.loads(
        (outbox_dir / "portal-state.json").read_text(encoding="utf-8")
    )
    spawn_pool = payload["resources"]["coexisting_runs"]["spawn_pool"]
    assert spawn_pool == {"max_concurrent": 4, "active": 1, "available": 3}


def test_write_live_portal_state_spawn_pool_active_unknown_when_registry_unreadable(tmp_path, monkeypatch):
    """An accounting-source failure must render ``active: null`` /
    ``available: null`` — never a silent zero. Zero and unknown are both
    live possibilities from a resident's seat (a genuinely idle pool vs. a
    pool this call site can't assert about), and collapsing "unknown" into
    "0 active / full headroom" is the same optimistic-direction lie #880
    reports for the presence-derived read, just moved to a new source."""
    def _boom():
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(daemon, "_spawn_pool_accepted_count", _boom)
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    task = Run(id="run-self", event_id="evt-1", body="", source="telegram")

    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
        brr_dir=brr_dir,
    )
    payload = json.loads(
        (outbox_dir / "portal-state.json").read_text(encoding="utf-8")
    )
    spawn_pool = payload["resources"]["coexisting_runs"]["spawn_pool"]
    assert spawn_pool == {"max_concurrent": 4, "active": None, "available": None}


def test_write_live_portal_state_projects_owned_children_from_run_controls(tmp_path):
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    task = Run(id="run-parent", event_id="evt-1", body="", source="telegram")

    daemon._register_run_control(
        "evt-child-a",
        "run-parent",
        parent_conversation_key="telegram:42:",
        repo_label="Gurio/brr",
    )
    daemon._bind_run_control("evt-child-a", "run-child-a")
    daemon._register_run_control(
        "evt-child-b",
        "run-parent",
        parent_conversation_key="telegram:42:",
        repo_label="Gurio/brr",
    )
    assert daemon._owned_child_controls("run-parent"), (
        "fixture must produce owned child controls or the portal projection "
        "below would pass vacuously over an empty set"
    )

    daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running", brr_dir=brr_dir,
    )

    payload = json.loads(
        (outbox_dir / "portal-state.json").read_text(encoding="utf-8")
    )
    owned = payload["resources"]["coexisting_runs"]["owned_children"]
    assert owned == [
        {"event_id": "evt-child-b", "parent_run_id": "run-parent", "run_id": ""},
        {"event_id": "evt-child-a", "parent_run_id": "run-parent", "run_id": "run-child-a"},
    ]


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


def test_host_publish_authenticates_like_a_run_would(tmp_path, monkeypatch):
    """Same gap as sync's fetch, on the push side (2026-08-06).

    ``publish_default_branch``'s own docstring claims "the daemon's managed
    credential setup applies" to this push — but the call it actually made
    passed no env at all, so it silently rode the bare daemon environment
    instead. Pin the fix: the env `gitops.push_branch` receives here is
    whatever `runner.clean_runner_environ` builds, not None.
    """
    repo, origin = _host_publish_repo(tmp_path)
    commit_files(repo, {"work.txt": "merged by host run\n"}, message="host merge")

    sentinel_env = {"MARK_OF_THE_FIX": "1"}
    monkeypatch.setattr(
        daemon.runner, "clean_runner_environ", lambda: dict(sentinel_env)
    )

    real_push_branch = daemon.gitops.push_branch
    seen_envs = []

    def spy(*args, **kwargs):
        seen_envs.append(kwargs.get("env"))
        return real_push_branch(*args, **kwargs)

    monkeypatch.setattr(daemon.gitops, "push_branch", spy)

    daemon.publish_default_branch(repo, _host_task())

    assert seen_envs == [sentinel_env]


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


# ── #733 the one claim point: `_apply_dashboard_wake_request` ────────────
#
# The server owns every rung; these pin what dispatch does with the answer.


def _wake_ctx(tmp_path, *, source="telegram", extra_repo=None):
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    write_repo_scaffold(repo_a)
    cfg = {
        "repo.label": "Gurio/a",
        "home.path": str(tmp_path / "account-home"),
    }
    if extra_repo is not None:
        write_repo_scaffold(extra_repo)
        cfg["account.repo.Gurio/b"] = str(extra_repo)
    ctx = daemon.account.resolve_context(repo_a, cfg)
    protocol.create_event(ctx.dispatch_inbox, source, "dispatch this")
    target = daemon._dispatchable_targets(ctx, repo_a, cfg)[0]
    return repo_a, ctx, target


def _stub_claim(monkeypatch, verdict, calls=None):
    from brr.gates import cloud

    def _fake(brr_dir, **kwargs):
        if calls is not None:
            calls.append(kwargs)
        return verdict

    monkeypatch.setattr(cloud, "claim_wake_request", _fake)


def test_dashboard_dispatch_header_stamps_event_before_repo_routing(
    tmp_path, monkeypatch,
):
    """An applied claim routes the wake to the repo the tap named — the one
    thing only this site can do, and the reason it owns the claim rather
    than `_run_worker` (whose repo root is already chosen)."""
    from brr import wake_request as wake_request_mod

    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    repo_a, ctx, target = _wake_ctx(tmp_path, extra_repo=repo_b)
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_dispatch"})
    calls: list[dict] = []
    _stub_claim(monkeypatch, {
        "apply": True, "reason": None, "request_id": "wake_dispatch",
        "status": "consumed", "profile": "codex-mini",
        "repo_label": "Gurio/b", "environment": "solitary",
    }, calls)

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    # The claim carried the whole question: which tap, which wake, what woke
    # it, and when that wake's event came into being.
    assert calls == [{
        "request_id": "wake_dispatch",
        "event_id": target.event["id"],
        "source": "telegram",
        "event_created": target.event["created"],
    }]
    assert applied.repo_root == repo_b
    assert applied.repo_label == "Gurio/b"
    assert applied.event["runner"] == "codex-mini"
    assert applied.event["repo_label"] == "Gurio/b"
    assert applied.event["environment"] == "solitary"
    assert applied.event["dashboard_wake_request_id"] == "wake_dispatch"
    assert applied.event["dashboard_wake_request_profile"] == "codex-mini"
    assert "dashboard_wake_request_reason" not in applied.event
    # The server said the row is retired; don't keep offering it.
    assert wake_request_mod.pending_id(repo_a / ".brr") is None
    receipt = wake_request_mod.last_receipt(repo_a / ".brr")
    assert receipt["at"]
    assert {k: v for k, v in receipt.items() if k != "at"} == {
        "request_id": "wake_dispatch",
        "source": "telegram",
        "event_id": target.event["id"],
        "profile": "codex-mini",
    }


def test_no_mirrored_tap_makes_no_http_call_at_all(tmp_path, monkeypatch):
    """#733's cost control: the presence bit gates the network entirely.

    This is the overwhelmingly common dispatch and every local-only account,
    so it must not merely return None — it must never touch the transport.
    """
    from brr.gates import cloud

    repo_a, ctx, target = _wake_ctx(tmp_path)

    def _never(*args, **kwargs):
        raise AssertionError("dispatch called out with no tap parked")

    monkeypatch.setattr(cloud, "claim_wake_request", _never)
    monkeypatch.setattr(cloud, "_request", _never)
    monkeypatch.setattr(cloud, "_load_state", _never)

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied is target
    assert "runner" not in applied.event
    assert "dashboard_wake_request_id" not in applied.event


def test_unreachable_server_leaves_dispatch_untouched(tmp_path, monkeypatch):
    """Fail-open, and byte-identical to the empty-mirror path: the honest
    cost of moving the decision to its owner is that dispatch now depends on
    brnrd.dev being reachable — it costs the tap, never the wake.

    No receipt either: nothing was decided, and claiming otherwise would be
    the invisible-outcome bug in a new place.
    """
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_offline"})
    _stub_claim(monkeypatch, None)

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied is target
    assert "runner" not in applied.event
    assert "dashboard_wake_request_id" not in applied.event
    # Still armed for a wake that can reach the server.
    assert wake_request_mod.pending_id(repo_a / ".brr") == "wake_offline"
    assert wake_request_mod.last_receipt(repo_a / ".brr") is None


def test_a_refused_tap_is_not_recorded_as_consumed(tmp_path, monkeypatch):
    """The refusal reaches the machine, in both places a human looks: the
    receipt file and the event the run's facet is built from."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_lapsed"})
    _stub_claim(monkeypatch, {
        "apply": False, "reason": "the tap expired before a wake claimed it",
        "request_id": "wake_lapsed", "status": "expired", "profile": "codex-mini",
    })

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied is target
    assert "runner" not in applied.event
    assert applied.event["dashboard_wake_request_id"] == "wake_lapsed"
    assert applied.event["dashboard_wake_request_profile"] == "codex-mini"
    assert applied.event["dashboard_wake_request_reason"] == (
        "the tap expired before a wake claimed it"
    )
    receipt = wake_request_mod.last_receipt(repo_a / ".brr")
    assert receipt["outcome"] == "refused"
    assert receipt["profile"] is None
    assert receipt["reason"] == "the tap expired before a wake claimed it"
    # Terminal server-side ⇒ stop offering it locally too.
    assert wake_request_mod.pending_id(repo_a / ".brr") is None


def test_a_refusal_that_keeps_the_tap_pending_leaves_the_mirror_armed(
    tmp_path, monkeypatch,
):
    """A `schedule`-source wake, an unpublished profile, a tap parked after
    the event: all refusals the server answers with the row *still pending*.
    The mirror must stay armed for the wake the tap was actually meant for."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path, source="schedule")
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_sched"})
    _stub_claim(monkeypatch, {
        "apply": False,
        "reason": "a schedule-source wake never spends a dashboard tap",
        "request_id": "wake_sched", "status": "pending", "profile": "codex-mini",
    })

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied is target
    assert "runner" not in applied.event
    assert wake_request_mod.pending_id(repo_a / ".brr") == "wake_sched"


def test_event_pin_outranks_the_tap_and_never_claims_it(tmp_path, monkeypatch):
    """An event-level pin (respawn shell:/core:, quality: escalate) is a
    deliberate per-run choice. It short-circuits *before* the claim, so the
    tap stays pending for the next unpinned wake instead of being spent on
    one that was never going to honour it — and costs no HTTP call."""
    from brr import wake_request as wake_request_mod
    from brr.gates import cloud

    repo_a, ctx, target = _wake_ctx(tmp_path)
    protocol.update_event_meta(target.event, core="claude-opus-4-8")
    target.event["core"] = "claude-opus-4-8"
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_pinned"})

    def _never(*args, **kwargs):
        raise AssertionError("a pinned wake must not claim a tap")

    monkeypatch.setattr(cloud, "claim_wake_request", _never)

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied is target
    assert "runner" not in applied.event
    assert wake_request_mod.pending_id(repo_a / ".brr") == "wake_pinned"
    assert wake_request_mod.last_receipt(repo_a / ".brr") is None


def test_applied_tap_naming_an_unregistered_repo_refuses_visibly(
    tmp_path, monkeypatch,
):
    """The rack drifted from the daemon's registered repos. The row is
    already spent server-side and the wake can't go where it was asked to —
    running the requested profile against the *wrong* repo would answer a
    question nobody asked, so refuse and let the receipt say why."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_lost"})
    _stub_claim(monkeypatch, {
        "apply": True, "reason": None, "request_id": "wake_lost",
        "status": "consumed", "profile": "codex-mini", "repo_label": "Gurio/gone",
    })

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied is target
    assert applied.repo_root == target.repo_root
    assert "runner" not in applied.event
    receipt = wake_request_mod.last_receipt(repo_a / ".brr")
    assert receipt["outcome"] == "refused"
    assert "Gurio/gone" in receipt["reason"]


def test_refusal_reason_cannot_break_the_event_frontmatter(tmp_path, monkeypatch):
    """Event frontmatter is flat and line-oriented. The reason is
    server-authored and short in practice; this is the guard that keeps "in
    practice" from being the load-bearing part."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_evil"})
    _stub_claim(monkeypatch, {
        "apply": False, "reason": "nope\nrunner: claude-fable\nstatus: done",
        "request_id": "wake_evil", "status": "pending", "profile": "codex-mini",
    })

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert applied.event["dashboard_wake_request_reason"] == (
        "nope runner: claude-fable status: done"
    )
    reread = daemon._dispatchable_targets(ctx, repo_a, {
        "repo.label": "Gurio/a", "home.path": str(tmp_path / "account-home"),
    })[0].event
    assert "runner" not in reread
    assert reread["status"] != "done"


# ── #932 conversation-sticky: the tap that holds ─────────────────────────
#
# The maintainer priced this fork: the one-shot is a cost-protection
# measure, plain sticky inherits the forget-to-downgrade problem, so the
# expiry is load-bearing. Sticky within the conversation, auto-reverting,
# never carried to schedule ticks.


def _give_identity(event, *, user_id="777", chat_id="555"):
    """Stamp a Telegram identity so the event yields both key kinds."""
    updates = {"telegram_user_id": user_id, "telegram_chat_id": chat_id}
    protocol.update_event_meta(event, **updates)
    event.update(updates)


def _second_target(ctx, repo_root, tmp_path, *, exclude_id, source="telegram",
                   body="a photo, 39 seconds later"):
    protocol.create_event(ctx.dispatch_inbox, source, body)
    cfg = {"repo.label": "Gurio/a", "home.path": str(tmp_path / "account-home")}
    targets = daemon._dispatchable_targets(ctx, repo_root, cfg)
    return next(t for t in targets if t.event["id"] != exclude_id)


def _never_claims(monkeypatch):
    from brr.gates import cloud

    def _never(*args, **kwargs):
        raise AssertionError("sticky inheritance must not touch the network")

    monkeypatch.setattr(cloud, "claim_wake_request", _never)
    monkeypatch.setattr(cloud, "_request", _never)


def test_applied_tap_binds_the_conversation_sticky_record(
    tmp_path, monkeypatch,
):
    """The claim that applies a tap now also writes the sticky record, bound
    to the claiming lead event's correspondent (preferred) and conversation
    keys — the newest tap owns the conversation."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    _give_identity(target.event)
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_hold"})
    _stub_claim(monkeypatch, {
        "apply": True, "reason": None, "request_id": "wake_hold",
        "status": "consumed", "profile": "claude-fable",
    })

    daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    record = wake_request_mod.sticky_record(repo_a / ".brr")
    assert record["request_id"] == "wake_hold"
    assert record["profile"] == "claude-fable"
    assert record["correspondent_key"] == "telegram:user-id:777"
    assert record["conversation_key"] == "telegram:555:"
    assert record["claimed_at"]


def test_burst_event_within_ttl_inherits_the_tap_profile(tmp_path, monkeypatch):
    """#932's live incident: the photo that lands 39 seconds after a fable
    tap and dispatches with no tap parked must inherit fable, not fall
    through to the config default — with the sticky stamps the run context
    bundle renders, and without an HTTP call."""
    from datetime import datetime, timedelta

    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    _give_identity(target.event)
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_hold"})
    _stub_claim(monkeypatch, {
        "apply": True, "reason": None, "request_id": "wake_hold",
        "status": "consumed", "profile": "claude-fable",
    })
    daemon._apply_dashboard_wake_request(target, ctx, repo_a)
    assert wake_request_mod.pending_id(repo_a / ".brr") is None

    burst = _second_target(ctx, repo_a, tmp_path, exclude_id=target.event["id"])
    _give_identity(burst.event)
    _never_claims(monkeypatch)

    applied = daemon._apply_dashboard_wake_request(burst, ctx, repo_a)

    assert applied.event["runner"] == "claude-fable"
    assert applied.event["dashboard_wake_sticky_profile"] == "claude-fable"
    # The stamps carry the record's own clock: expiry is claimed_at + TTL.
    record = wake_request_mod.sticky_record(repo_a / ".brr")
    claimed = datetime.fromisoformat(record["claimed_at"])
    assert applied.event["dashboard_wake_sticky_claimed_at"] == (
        claimed.isoformat(timespec="seconds")
    )
    assert applied.event["dashboard_wake_sticky_expires_at"] == (
        (claimed + timedelta(hours=2)).isoformat(timespec="seconds")
    )
    # Inherited, not claimed fresh — no request id, and the record survives
    # for the rest of the conversation instead of being retired.
    assert "dashboard_wake_request_id" not in applied.event
    assert wake_request_mod.sticky_record(repo_a / ".brr") is not None


def test_sticky_matches_the_correspondent_across_gates(tmp_path, monkeypatch):
    """The record binds the *human*, not the raw thread string: a tap
    claimed on the local Telegram gate covers the same person arriving via
    the brnrd relay (`cloud:telegram:…`), whose conversation key differs.
    #930 is what raw thread keys did to menus."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    wake_request_mod.store_sticky(
        repo_a / ".brr",
        request_id="wake_hold",
        profile="claude-fable",
        correspondent_key="telegram:user-id:777",
        conversation_key="telegram:555:",
    )
    relayed = _second_target(
        ctx, repo_a, tmp_path, exclude_id=target.event["id"], source="cloud",
    )
    updates = {
        "cloud_platform": "telegram", "cloud_user_id": "777",
        "cloud_chat_id": "900",
    }
    protocol.update_event_meta(relayed.event, **updates)
    relayed.event.update(updates)
    _never_claims(monkeypatch)

    applied = daemon._apply_dashboard_wake_request(relayed, ctx, repo_a)

    assert applied.event["runner"] == "claude-fable"


def test_sticky_does_not_leak_to_another_human_in_the_same_thread(
    tmp_path, monkeypatch,
):
    """A thread key is an *address*, not an identity — two people in one
    group chat share it. So a correspondent mismatch must end the match, not
    fall through to the thread: otherwise anyone in the chat inherits
    whoever last tapped a strong Core, which is the exact spend the TTL
    exists to bound. The conversation key stays the fallback for events
    carrying no human identity at all."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    wake_request_mod.store_sticky(
        repo_a / ".brr",
        request_id="wake_hold",
        profile="claude-fable",
        correspondent_key="telegram:user-id:777",
        conversation_key="cloud:telegram:900:",
    )
    # Same chat, different person.
    other = _second_target(
        ctx, repo_a, tmp_path, exclude_id=target.event["id"], source="cloud",
    )
    updates = {
        "cloud_platform": "telegram", "cloud_user_id": "888",
        "cloud_chat_id": "900",
    }
    protocol.update_event_meta(other.event, **updates)
    other.event.update(updates)
    _never_claims(monkeypatch)

    applied = daemon._apply_dashboard_wake_request(other, ctx, repo_a)

    assert "dashboard_wake_sticky_profile" not in applied.event
    assert applied.event.get("runner") != "claude-fable"
    # The record survives — it is still 777's, and 777's next message in the
    # same thread must still inherit it.
    assert wake_request_mod.sticky_record(repo_a / ".brr") is not None


def test_schedule_and_self_woken_events_never_consult_the_sticky_record(
    tmp_path, monkeypatch,
):
    """The maintainer's price for sticky existing at all: never carried to
    schedule ticks. The guard is on the source, not on the accident that a
    schedule tick usually has no correspondent — so even a schedule event
    that somehow carries the identity stays on the config default. Same for
    a respawn-origin event: the daemon woke itself; nobody tapped."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path, source="schedule")
    _give_identity(target.event)
    wake_request_mod.store_sticky(
        repo_a / ".brr",
        request_id="wake_hold",
        profile="claude-fable",
        correspondent_key="telegram:user-id:777",
        conversation_key="telegram:555:",
    )
    _never_claims(monkeypatch)

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert "runner" not in applied.event
    assert "dashboard_wake_sticky_profile" not in applied.event
    # The record is not spent on the refusal — the conversation keeps it.
    assert wake_request_mod.sticky_record(repo_a / ".brr") is not None

    respawn = _second_target(ctx, repo_a, tmp_path, exclude_id=target.event["id"])
    _give_identity(respawn.event)
    updates = {"respawned_by_run": "run-123"}
    protocol.update_event_meta(respawn.event, **updates)
    respawn.event.update(updates)

    applied = daemon._apply_dashboard_wake_request(respawn, ctx, repo_a)

    assert "runner" not in applied.event


def test_expired_sticky_record_falls_through_to_config(tmp_path, monkeypatch):
    """Expiry is the load-bearing half of the design: past the TTL the
    record is dropped and resolution falls through to `.brr/config` — the
    cheap default returns on its own, nobody has to remember to downgrade."""
    from datetime import datetime, timedelta, timezone

    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    _give_identity(target.event)
    stale = datetime.now(timezone.utc) - timedelta(hours=3)
    wake_request_mod.store_sticky(
        repo_a / ".brr",
        request_id="wake_hold",
        profile="claude-fable",
        correspondent_key="telegram:user-id:777",
        conversation_key="telegram:555:",
        claimed_at=stale.isoformat(timespec="seconds"),
    )
    _never_claims(monkeypatch)

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert "runner" not in applied.event
    assert "dashboard_wake_sticky_profile" not in applied.event
    # Dropped, so the next dispatch doesn't re-read a dead promise.
    assert wake_request_mod.sticky_record(repo_a / ".brr") is None


def test_sticky_ttl_is_config_overridable(tmp_path, monkeypatch):
    """`wake_request.sticky_ttl_seconds` in `.brr/config` overrides the 2 h
    default — as a string, because that is what the flat config file
    yields."""
    from datetime import datetime, timedelta, timezone

    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    _give_identity(target.event)
    ten_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    wake_request_mod.store_sticky(
        repo_a / ".brr",
        request_id="wake_hold",
        profile="claude-fable",
        correspondent_key="telegram:user-id:777",
        conversation_key="telegram:555:",
        claimed_at=ten_minutes_ago.isoformat(timespec="seconds"),
    )
    _never_claims(monkeypatch)

    applied = daemon._apply_dashboard_wake_request(
        target, ctx, repo_a, {"wake_request.sticky_ttl_seconds": "300"},
    )

    assert "runner" not in applied.event
    assert wake_request_mod.sticky_record(repo_a / ".brr") is None


def test_a_new_tap_replaces_the_sticky_record(tmp_path, monkeypatch):
    """One requester parks at most one tap at a time; the newest applied tap
    owns the conversation — profile, binding, and clock."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    _give_identity(target.event, user_id="888", chat_id="666")
    wake_request_mod.store_sticky(
        repo_a / ".brr",
        request_id="wake_old",
        profile="codex-mini",
        correspondent_key="telegram:user-id:777",
        conversation_key="telegram:555:",
    )
    wake_request_mod.store_pending(repo_a / ".brr", {"request_id": "wake_new"})
    _stub_claim(monkeypatch, {
        "apply": True, "reason": None, "request_id": "wake_new",
        "status": "consumed", "profile": "claude-fable",
    })

    daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    record = wake_request_mod.sticky_record(repo_a / ".brr")
    assert record["request_id"] == "wake_new"
    assert record["profile"] == "claude-fable"
    assert record["correspondent_key"] == "telegram:user-id:888"
    assert record["conversation_key"] == "telegram:666:"


def test_event_pin_outranks_the_sticky_record(tmp_path, monkeypatch):
    """An event-level pin (respawn shell:/core:, quality: escalate) is a
    deliberate per-run choice — it short-circuits before the record is even
    read, and leaves it live for the next unpinned wake in the thread."""
    from brr import wake_request as wake_request_mod

    repo_a, ctx, target = _wake_ctx(tmp_path)
    _give_identity(target.event)
    protocol.update_event_meta(target.event, core="claude-opus-4-8")
    target.event["core"] = "claude-opus-4-8"
    wake_request_mod.store_sticky(
        repo_a / ".brr",
        request_id="wake_hold",
        profile="claude-fable",
        correspondent_key="telegram:user-id:777",
        conversation_key="telegram:555:",
    )
    _never_claims(monkeypatch)

    applied = daemon._apply_dashboard_wake_request(target, ctx, repo_a)

    assert "runner" not in applied.event
    assert "dashboard_wake_sticky_profile" not in applied.event
    assert wake_request_mod.sticky_record(repo_a / ".brr") is not None


def test_run_worker_notes_a_sticky_inherited_profile(tmp_path, monkeypatch):
    """The run context bundle's "Requested Runner" line says when a profile
    came from the sticky record — so a wake can tell tap-fresh from
    tap-inherited, and see when the inheritance lapses."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-sticky-wake")
    event["runner"] = "claude-fable"
    event["dashboard_wake_sticky_profile"] = "claude-fable"
    event["dashboard_wake_sticky_claimed_at"] = "2026-08-01T11:27:00+00:00"
    event["dashboard_wake_sticky_expires_at"] = "2026-08-01T13:27:00+00:00"
    _stub_env_isolated(monkeypatch, tmp_path)
    brr_dir = tmp_path / ".brr"

    seen_overrides: list[dict | None] = []
    prompt_kwargs: dict = {}

    def fake_prompt(task, eid, rp, root, **kw):
        prompt_kwargs.update(kw)
        return f"PROMPT {eid}"

    _stub_wake_runner(monkeypatch, seen_overrides)
    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", fake_prompt)

    task = daemon._run_worker(event, tmp_path, brr_dir / "responses", {}, 0)

    assert task.status == "done"
    assert seen_overrides and seen_overrides[0] == {"runner": "claude-fable"}
    assert prompt_kwargs["runner_medium"] == (
        "claude-fable (conversation-sticky from tap at 11:27Z, "
        "expires 13:27Z)"
    )
    # Inherited is not a fresh claim: no tap was in play *for this wake*.
    assert "wake_request" not in task.meta


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
        meta={
            "runner_name": "codex",
            "reply_archive": "archived",
            "terminal_route": "gate-sole",
            "started_at": "2026-07-29T15:00:00Z",
            "ended_at": "2026-07-29T15:03:00Z",
        },
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
    assert "started_at: 2026-07-29T15:00:00Z" in text
    assert "ended_at: 2026-07-29T15:03:00Z" in text
    assert "runner_name: codex" in text
    assert "reply_archive: archived" in text
    # #743: which channel carried the terminal stream reaches the run node,
    # so "the fallback net was this run's only voice" is a readable fact
    # rather than something reconstructed from message-store frontmatter.
    assert "terminal_route: gate-sole" in text
    # The body no longer restates frontmatter facts as bullets — the
    # non-repetitive-node cut, 2026-07-19.
    assert "- runner:" not in text
    # The local store path is recorded as a dev breadcrumb; with no forge
    # remote on the dominion there is no web URL to surface yet.
    assert task.meta["run_state_path"] == str(path)
    assert "run_state_url" not in task.meta


def test_account_run_state_doc_does_not_invent_clock_readings(tmp_path):
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
        id="run-not-started",
        event_id="evt-not-started",
        body="duplicate delivery",
        source="telegram",
        status="done",
    )

    path = daemon._persist_run_state_doc(
        ctx,
        task,
        repo_label="Gurio/brr",
        stage="deduplicated",
    )

    fields = protocol.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert "started_at" not in fields
    assert "ended_at" not in fields


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


def test_persist_run_topics_writes_the_canonical_row(tmp_path):
    """Rendered, not raw-copied: a bare `.topics` row (no `topics:` prefix)
    still lands as the one canonical `topics: <slugs>` shape on the node."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(id="run-topics", event_id="evt-topics", body="work", source="telegram")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".topics").write_text("the-loom the-post\n", encoding="utf-8")

    path = daemon._persist_run_topics(
        ctx, task, repo_label="Gurio/brr", outbox_dir=outbox,
    )

    assert path == ctx.runs_dir / "Gurio__brr" / "run-topics" / "topics.md"
    assert path.read_text(encoding="utf-8") == "topics: the-loom the-post\n"
    assert task.meta["run_topics_path"] == str(path)


def test_persist_run_topics_no_claim_writes_no_file(tmp_path):
    """Absence is honest — no `.topics` claim means no fabricated file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(id="run-no-topics", event_id="evt-x", body="work", source="telegram")
    outbox = tmp_path / "outbox"
    outbox.mkdir()

    path = daemon._persist_run_topics(
        ctx, task, repo_label="Gurio/brr", outbox_dir=outbox,
    )

    assert path is None
    assert not (
        ctx.runs_dir / "Gurio__brr" / "run-no-topics" / "topics.md"
    ).exists()
    assert "run_topics_path" not in task.meta


def test_persist_boundaries_summary_writes_beside_body_and_state(tmp_path):
    """The node gets the derived summary, not the raw transcript.

    The source transcript lives in the daemon's own scratch dir
    (``<brr_dir>/runs/<run-id>/boundaries.jsonl``, where the hook backchannel
    writes it — see ``hooks._transcript_env`` in test_hooks.py for the same
    layout), never the account dominion; only ``hooks.derive_boundaries_summary``'s
    compact projection lands on the node.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(id="run-guard", event_id="evt-guard", body="work", source="telegram")
    brr_dir = tmp_path / "brr-scratch"
    run_dir = brr_dir / "runs" / "run-guard"
    run_dir.mkdir(parents=True)
    lines = [
        {
            "at": "2026-08-02T07:30:12Z", "phase": "session-start",
            "inject": "hi", "block": False, "block_reason": None,
        },
        {
            "at": "2026-08-02T07:31:00Z", "phase": "stop",
            "inject": "closeout", "block": True, "block_reason": "not done",
        },
        {
            "at": "2026-08-02T07:31:05Z", "phase": "stop",
            "inject": "closeout again", "block": False, "block_reason": None,
        },
    ]
    (run_dir / "boundaries.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8",
    )

    path = daemon._persist_boundaries_summary(
        ctx, task, repo_label="Gurio/brr", brr_dir=brr_dir,
    )

    assert path == ctx.runs_dir / "Gurio__brr" / "run-guard" / "boundaries.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["stops"] == 2
    assert summary["guard_fire_count"] == 1
    assert summary["final_stop_block"] is False
    assert task.meta["run_boundaries_summary_path"] == str(path)


def test_persist_boundaries_summary_omits_the_file_when_transcript_absent(tmp_path):
    """No transcript in the daemon's scratch dir ⇒ no `boundaries.json` on the node.

    Never a guessed, zero-valued summary — same pessimism as its source
    (`hooks.derive_boundaries_summary`).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(id="run-no-transcript", event_id="evt-x", body="work", source="telegram")
    brr_dir = tmp_path / "brr-scratch"
    (brr_dir / "runs" / "run-no-transcript").mkdir(parents=True)

    path = daemon._persist_boundaries_summary(
        ctx, task, repo_label="Gurio/brr", brr_dir=brr_dir,
    )

    assert path is None
    assert not (
        ctx.runs_dir / "Gurio__brr" / "run-no-transcript" / "boundaries.json"
    ).exists()
    assert "run_boundaries_summary_path" not in task.meta


def test_capture_control_files_partition_is_total():
    """Every control-file constant the scanned modules declare is decided.

    ``_discover_control_file_names`` reads the constants back via
    introspection rather than trusting a hand-copied list — this is the
    guard that catches the *next* control file somebody adds without
    deciding its fate. See the test below for proof the mechanism itself
    fires, not just that today's fixed set happens to be clean.
    """
    discovered = daemon._discover_control_file_names()
    classified = set(daemon.PRESERVED) | set(daemon.NOT_PRESERVED)
    missing = set(discovered) - classified
    assert not missing, (
        f"undecided control file(s): {sorted(discovered[n] for n in missing)} "
        "— add each to daemon.PRESERVED or daemon.NOT_PRESERVED with a reason"
    )
    # A partition, not just a total covering: no name is both kept and
    # dropped depending on which dict a reader happens to check first.
    assert not (set(daemon.PRESERVED) & set(daemon.NOT_PRESERVED))


def test_capture_control_files_partition_catches_an_undecided_constant():
    """The totality guard actually fires on a new constant, not just today's.

    A fake module carrying one unclassified ``*_NAME`` constant, scanned
    the same way :func:`daemon._discover_control_file_names` scans the real
    modules, must come back undecided. Neutering ``PRESERVED``/
    ``NOT_PRESERVED`` themselves (deleting an entry) would also redden
    ``test_capture_control_files_partition_is_total`` above — this test
    instead proves the *discovery* half by construction, independent of
    whichever real constants happen to exist right now.
    """
    fake_module = types.SimpleNamespace(
        __name__="fake_control_module",
        TOTALLY_NEW_CONTROL_NAME=".totally-new-control-file",
    )
    discovered = {
        value: f"{fake_module.__name__}.{attr}"
        for attr, value in vars(fake_module).items()
        if daemon._CONTROL_NAME_RE.match(attr)
        and isinstance(value, str) and value
    }
    assert discovered == {
        ".totally-new-control-file": "fake_control_module.TOTALLY_NEW_CONTROL_NAME"
    }
    classified = set(daemon.PRESERVED) | set(daemon.NOT_PRESERVED)
    assert set(discovered) - classified == {".totally-new-control-file"}


def test_capture_control_files_copies_preserved_names_dot_stripped(tmp_path):
    """The general case of ``_capture_pr_handle``: outbox -> node, dot stripped.

    Also proves the pessimistic-side-down half by construction: a file in
    ``NOT_PRESERVED`` (``inbox.json``, carrying what would be another
    correspondent's pending event in the real shape) sits right next to the
    preserved files in the source outbox and must not appear on the node.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(id="run-ctrl", event_id="evt-ctrl", body="work", source="telegram")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".relics.jsonl").write_text(
        '{"kind": "file", "path": "a.py"}\n', encoding="utf-8",
    )
    (outbox / ".mood").write_text("focused\nnarration line\n", encoding="utf-8")
    (outbox / ".claude-result-levels.json").write_text(
        '{"spend": {"total_cost_usd": 1.2}}', encoding="utf-8",
    )
    # Present in the outbox but NOT_PRESERVED — must survive uncopied.
    (outbox / "inbox.json").write_text(
        '{"events": [{"body": "another correspondent said this"}]}',
        encoding="utf-8",
    )
    (outbox / ".card").write_text("## Now\n\nlive\n", encoding="utf-8")

    written = daemon._capture_control_files(
        ctx, task, repo_label="Gurio/brr", outbox_dir=outbox,
    )

    run_dir = ctx.runs_dir / "Gurio__brr" / "run-ctrl"
    assert (
        run_dir / "relics.jsonl"
    ).read_text(encoding="utf-8") == '{"kind": "file", "path": "a.py"}\n'
    assert (run_dir / "mood").read_text(encoding="utf-8") == "focused\nnarration line\n"
    assert (
        run_dir / "spend.json"
    ).read_text(encoding="utf-8") == '{"spend": {"total_cost_usd": 1.2}}'
    assert not (run_dir / "inbox.json").exists()
    assert not (run_dir / "card").exists()
    assert not (run_dir / ".card").exists()
    assert {p.name for p in written} == {"relics.jsonl", "mood", "spend.json"}


def test_capture_control_files_is_silent_when_nothing_present(tmp_path):
    """No control files in the outbox ⇒ nothing written, no node directory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(id="run-empty", event_id="evt-empty", body="work", source="telegram")
    outbox = tmp_path / "outbox-empty"
    outbox.mkdir()

    written = daemon._capture_control_files(
        ctx, task, repo_label="Gurio/brr", outbox_dir=outbox,
    )

    assert written == []
    assert not (ctx.runs_dir / "Gurio__brr" / "run-empty").exists()


def test_capture_control_files_noop_without_outbox_or_disabled_account(tmp_path):
    """Absence discipline: no outbox, or no enabled account, writes nothing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(id="run-x", event_id="evt-x", body="work", source="telegram")

    assert daemon._capture_control_files(
        ctx, task, repo_label="Gurio/brr", outbox_dir=None,
    ) == []
    assert daemon._capture_control_files(
        None, task, repo_label="Gurio/brr", outbox_dir=tmp_path,
    ) == []


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


def test_primary_dominion_candidate_matches_forwarded_account_context(tmp_path):
    """#1409: forwarding ``account_context`` must reproduce the old hand-built candidate.

    ``_primary_dominion_candidate`` used to hand-build an extra
    ``ResidentDominion`` (``repo_label = account.repo_label(repo_root, cfg)``)
    and ``insert(0, …)`` it ahead of whatever ``resident_dominion_candidates``
    derived on its own. That is exactly the label ``resident_dominion_
    candidates`` derives internally when given ``account_context=`` but no
    ``repo_label=`` override, so the hand-built insert is provably a
    duplicate once the context is forwarded — this asserts the *path* and
    *capture_root* the caller now gets are the same values the old manual
    insert would have produced.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    cfg = {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")}
    ctx = daemon.account.resolve_context(repo, cfg)
    repo_dom = daemon.account.repo_dominion_path(ctx, "Gurio/brr")
    daemon.dominion.seed_account_dominion(repo_dom)

    candidate = daemon._primary_dominion_candidate(repo, cfg, ctx)

    assert candidate is not None
    assert candidate.path == repo_dom
    assert candidate.capture_root == ctx.dominion_repo


def test_primary_dominion_candidate_reuses_context_instead_of_rederiving(
    tmp_path, monkeypatch,
):
    """#1409: the equivalence above holds because forwarding *replaces*
    re-derivation, not merely because ``account.resolve_context`` happens to
    be deterministic. Pin the mechanism directly: break a *fresh*
    ``resolve_context`` call and confirm ``_primary_dominion_candidate``
    still succeeds using the already-resolved context it was handed.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    cfg = {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")}
    ctx = daemon.account.resolve_context(repo, cfg)
    repo_dom = daemon.account.repo_dominion_path(ctx, "Gurio/brr")
    daemon.dominion.seed_account_dominion(repo_dom)

    def _boom(*_a, **_k):
        raise AssertionError(
            "resolve_context re-derived instead of reusing the forwarded context"
        )

    monkeypatch.setattr(daemon.account, "resolve_context", _boom)

    candidate = daemon._primary_dominion_candidate(repo, cfg, ctx)

    assert candidate is not None
    assert candidate.path == repo_dom
    assert candidate.capture_root == ctx.dominion_repo


def test_capture_dominion_repo_label_can_diverge_from_config_label(tmp_path):
    """#1409: ``_capture_dominion``'s manual insert is *not* provably a duplicate.

    Unlike ``_primary_dominion_candidate``, this candidate's ``repo_label``
    is sourced from ``task.meta["repo_label"]`` (event-aware, via
    ``_repo_label``) rather than ``account.repo_label(repo_root, cfg)``
    (config/git-remote only) — the two genuinely disagree here, so the
    manual insert stays. This proves the divergence and that capture still
    lands correctly (on the shared ``capture_root``) despite it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    cfg = {"home.path": str(tmp_path / "account-home")}  # no repo.label configured
    ctx = daemon.account.resolve_context(repo, cfg)

    auto_candidates = daemon.dominion.resident_dominion_candidates(
        repo, cfg, account_context=ctx,
    )
    auto_path = auto_candidates[0].path

    task_meta_label = "Explicit/EventRepo"
    expected_manual_path = daemon.account.repo_dominion_path(ctx, task_meta_label)
    # The two label sources really do disagree — this is why the insert
    # in `_capture_dominion` stays instead of being removed like its
    # sibling in `_primary_dominion_candidate`.
    assert auto_path != expected_manual_path

    daemon.dominion.seed_account_dominion(expected_manual_path)
    (expected_manual_path / "notes.md").write_text("captured\n", encoding="utf-8")
    task = Run(
        id="run-diverge",
        event_id="evt-diverge",
        body="capture divergent label",
        source="telegram",
        status="done",
        meta={"repo_label": task_meta_label},
    )

    daemon._capture_dominion(repo, cfg, task, account_context=ctx)

    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=ctx.dominion_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log == "brnrd-home: capture account memory after run run-diverge"


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


def test_collect_levels_for_claude_falls_back_to_shared_spend_when_own_outbox_is_fresh(
    monkeypatch, tmp_path,
):
    """#1027: a *new* run's own outbox has no result-JSON reading of its own
    yet — that is exactly the wake-time state ``portal-state.json``'s
    ``spend``/``context_window`` facets render from. The last claude run's
    reading, durably written into the account-shared dir (``BRR_SHARED_DIR``,
    ``claude_status._shared_dir``), is what must be found instead of
    reporting ``absent`` for a fact that is one run stale, not unknown — and
    its summary must say whose reading it is: the shared slot is one mutable
    file every claude run overwrites, so serving its "...this session" text
    unchanged here would misattribute a different run's cost as this run's
    own (maintainer review, 2026-08-03 — see ``claude_status.mark_cross_run``).
    """
    monkeypatch.setattr(
        daemon.claude_usage, "load_snapshot", lambda outbox: None,
    )
    outbox_dir = tmp_path / "outbox" / "evt-fresh-run"
    outbox_dir.mkdir(parents=True)
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    daemon.claude_status.write_snapshot(
        shared_dir,
        {
            "source": "claude result JSON",
            "run_id": "run-earlier-worker",
            "spend": {"summary": "$0.42 this session (estimated)"},
            "context_window": {"summary": "80% context left (est)"},
        },
    )

    levels, slots = daemon._collect_levels(
        "claude", outbox_dir, tmp_path, refresh=False, shared_dir=shared_dir,
    )

    assert slots == {"quota", "spend", "context_window"}
    assert levels["spend"]["summary"] == (
        "$0.42 this session (estimated) — run-earlier-worker's reading, "
        "carried (not this run's)"
    )
    assert levels["context_window"]["summary"] == (
        "80% context left (est) — run-earlier-worker's reading, "
        "carried (not this run's)"
    )


def test_collect_levels_for_claude_prefers_own_fresh_reading_over_shared(
    monkeypatch, tmp_path,
):
    """The current run's own outbox copy — when it has already produced
    one — wins outright and keeps its unmodified "this session" wording; the
    cross-run attribution note is only for the shared-slot fallback."""
    monkeypatch.setattr(
        daemon.claude_usage, "load_snapshot", lambda outbox: None,
    )
    outbox_dir = tmp_path / "outbox" / "evt-this-run"
    outbox_dir.mkdir(parents=True)
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    daemon.claude_status.write_snapshot(
        outbox_dir,
        {
            "source": "claude result JSON",
            "run_id": "evt-this-run",
            "spend": {"summary": "$1.00 this session (estimated)"},
        },
    )
    daemon.claude_status.write_snapshot(
        shared_dir,
        {
            "source": "claude result JSON",
            "run_id": "run-earlier-worker",
            "spend": {"summary": "$0.42 this session (estimated)"},
        },
    )

    levels, _ = daemon._collect_levels(
        "claude", outbox_dir, tmp_path, refresh=False, shared_dir=shared_dir,
    )

    assert levels["spend"]["summary"] == "$1.00 this session (estimated)"


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
    assert child["strand"] is True
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
    # A strand-stack run: nesting is refused by design.
    task = Run(
        id="run-worker", event_id=event_id, body="original", source="telegram",
        meta={"strand": True},
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
    built without ``strand=`` — so the worker's prompt listed two of the
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
    event["strand"] = True
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


def test_run_state_produce_never_regresses_on_a_probe_glitch(tmp_path, monkeypatch):
    """#1309 item 4: a fresh collection missing a relic must not erase it.

    ``relics.collect`` is documented best-effort — a git-probe glitch
    (``relics.collection_scope`` reading an ambiguous branch/seed) can
    return a real-looking but *incomplete* record list without raising, so
    the writer's own ``except Exception`` guard never sees it. Unlike the
    fully-empty case (already handled: an empty ``rendered`` falls back to
    ``_existing_produce_lines``), a *non-empty but smaller* result sailed
    straight through and overwrote a previously-complete section — produce
    only grows within a run's life, so a relic that was already recorded
    disappearing is stronger evidence of a glitch than of a real change.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(
        id="run-produce-regress",
        event_id="evt-produce-regress",
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
    assert "[PR #487](https://forge/pr/487)" in text

    # A later heartbeat's probe glitches: it still resolves *something*
    # (never raises), but drops the PR relic the document already proved.
    monkeypatch.setattr(
        daemon.relics,
        "collect",
        lambda *_args, **_kwargs: [
            {"kind": "commit", "sha": "abc1234def", "subject": "do the thing",
             "url": "https://forge/commit/abc1234"},
        ],
    )
    path = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="running",
        work_dir=repo, outbox_dir=None,
    )
    text = path.read_text(encoding="utf-8")
    assert "[PR #487](https://forge/pr/487)" in text, (
        "a proven relic must survive a later probe's incomplete reading"
    )
    assert "[abc1234 do the thing](https://forge/commit/abc1234)" in text


def test_run_state_doc_carries_the_complete_bounded_bolt_declaration(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(
        id="run-bolt-frame",
        event_id="evt-bolt-frame",
        body="finish",
        source="telegram",
        status="done",
        meta={
            "bolt": {
                "accepted_at": "2026-08-08T00:00:00Z",
                "annotated": 1,
                "declaration_version": 1,
                "asks": [{"event": "evt-ask", "disposition": "answered"}],
                "owed": [],
                "decisions": ["wire: kept"],
                "spend_declared": "~$2",
                "next": None,
                "dissent": ["evt-ask still pending"],
            }
        },
    )

    text = daemon._persist_run_state_doc(
        ctx, task, repo_label="Gurio/brr", stage="finished",
    ).read_text(encoding="utf-8")

    assert "bolt: annotated 2026-08-08T00:00:00Z" in text
    assert "## Bolt Declaration" in text
    declaration_json = text.split("## Bolt Declaration\n\n```json\n", 1)[1].split(
        "\n```", 1,
    )[0]
    declaration = json.loads(declaration_json)
    assert declaration["asks"] == [{"event": "evt-ask", "disposition": "answered"}]
    assert declaration["dissent"] == ["evt-ask still pending"]
    assert "produce" not in declaration


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


def _capture_ctx(tmp_path):
    """The scaffold the `_capture_control_files` tests share."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = daemon.account.resolve_context(
        repo,
        {"repo.label": "Gurio/brr", "home.path": str(tmp_path / "account-home")},
    )
    task = Run(id="run-sym", event_id="evt-sym", body="work", source="telegram")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    return ctx, task, outbox, ctx.runs_dir / "Gurio__brr" / "run-sym"


def test_capture_control_files_refuses_a_symlink(tmp_path):
    """A name is not a file, and this destination is published.

    Everything under `.brr/` rides the repo bind mount, so a run environment
    can write its own outbox (#518) — including replacing a control file
    with a symlink. `Path.read_bytes` follows one silently, and
    `runs/<repo>/<run>/` is mirrored to brnrd.dev unredacted. So a one-line
    `.pr` nobody would ever inspect is enough to publish an ssh key or the
    account's `security.config`.

    Driven before it was fixed: `read_bytes` on a symlink returned the
    target's contents and the copy published them.
    """
    ctx, task, outbox, run_dir = _capture_ctx(tmp_path)
    secret = tmp_path / "security.config"
    secret.write_text("runner_cmd: /bin/anything\n", encoding="utf-8")
    (outbox / ".mood").symlink_to(secret)
    # A real file beside it, so the refusal is per-file and not a bail-out.
    (outbox / ".name").write_text("a-run-name\n", encoding="utf-8")

    written = daemon._capture_control_files(
        ctx, task, repo_label="Gurio/brr", outbox_dir=outbox,
    )

    assert not (run_dir / "mood").exists()
    assert (run_dir / "name").read_text(encoding="utf-8") == "a-run-name\n"
    assert all(p.name != "mood" for p in written)


def test_capture_control_files_refuses_a_symlink_to_a_directory(tmp_path):
    """The same class, in the shape a `read_bytes` guard alone would miss."""
    ctx, task, outbox, run_dir = _capture_ctx(tmp_path)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (outbox / ".relics.jsonl").symlink_to(target)

    daemon._capture_control_files(
        ctx, task, repo_label="Gurio/brr", outbox_dir=outbox,
    )
    assert not (run_dir / "relics.jsonl").exists()


def test_capture_control_files_skips_a_file_over_the_cap(tmp_path):
    """The readers cap what they *parse*; nothing capped what a run writes.

    `relics._MAX_RECORDS` bounds parsing, not the file. Without a cap here
    an unbounded control file is read whole into the daemon's memory at
    closeout and then committed to the account git repo, where it is
    permanent.

    Skipped, not truncated: half a JSONL file is a file that parses and lies.
    """
    ctx, task, outbox, run_dir = _capture_ctx(tmp_path)
    (outbox / ".relics.jsonl").write_bytes(
        b"x" * (daemon._MAX_PRESERVED_BYTES + 1)
    )
    (outbox / ".name").write_text("still-copied\n", encoding="utf-8")

    daemon._capture_control_files(
        ctx, task, repo_label="Gurio/brr", outbox_dir=outbox,
    )

    assert not (run_dir / "relics.jsonl").exists()
    assert (run_dir / "name").read_text(encoding="utf-8") == "still-copied\n"


def test_capture_control_files_keeps_a_file_at_exactly_the_cap(tmp_path):
    """The boundary is inclusive — the guard rejects *over*, not *at*."""
    ctx, task, outbox, run_dir = _capture_ctx(tmp_path)
    (outbox / ".relics.jsonl").write_bytes(b"x" * daemon._MAX_PRESERVED_BYTES)

    daemon._capture_control_files(
        ctx, task, repo_label="Gurio/brr", outbox_dir=outbox,
    )
    assert (run_dir / "relics.jsonl").stat().st_size == daemon._MAX_PRESERVED_BYTES


# ── _drain_outbox: await: (#959, collapsed by #1187) ──────────────────


def _drain_await(tmp_path, frontmatter, *, meta=None, stats=None, pre_pending=()):
    """Stage one ``await:`` directive and drain it; returns (promoted, task, outbox).

    *pre_pending*, when given, is a list of ``(source, event_meta)`` pairs
    created in the inbox *before* the directive is drained — events already
    pending at arm time, for exercising the #1327 snapshot.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(inbox, "telegram", "original", status="processing")
    event_id = path.stem
    for pre_source, pre_meta in pre_pending:
        protocol.create_event(inbox, pre_source, "pre-existing", **(pre_meta or {}))
    (outbox / "await.md").write_text(frontmatter, encoding="utf-8")
    task = Run(
        id="run-parent", event_id=event_id, body="original", source="telegram",
        meta=dict(meta or {}),
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, event_id),
        task, responses, event_id, outbox, inbox,
        stats=stats,
    )
    return promoted, task, outbox


def test_drain_outbox_arms_a_bare_await(tmp_path):
    """The whole documented shape: a marker and a ceiling, nothing else."""
    stats: dict[str, int] = {}
    promoted, task, outbox = _drain_await(
        tmp_path, "---\nawait: true\ntimeout: 20m\n---\n", stats=stats,
    )

    assert promoted == 1
    assert stats == {"await": 1}
    armed = task.meta["await"]
    assert armed["file"] is None
    assert armed["timeout_seconds"] == 1200.0
    assert armed["resolved"] is False
    assert armed["generation"]
    # The staged file is retired like every other drained verb — an armed
    # await isn't left sitting in the outbox to be misread as an
    # undelivered plain message.
    assert not list(outbox.glob("await*.md"))


def test_drain_outbox_arms_await_with_the_optional_file_trigger(tmp_path):
    promoted, task, _outbox = _drain_await(
        tmp_path, "---\nawait: true\ntimeout: 5m\nfile: /tmp/gate.log\n---\n",
    )

    assert promoted == 1
    assert task.meta["await"]["file"] == "/tmp/gate.log"


def test_drain_outbox_await_missing_timeout_is_dropped_with_a_notice(tmp_path):
    promoted, task, outbox = _drain_await(tmp_path, "---\nawait: true\n---\n")

    assert promoted == 0
    assert "await" not in task.meta
    notices = daemon._read_outbox_notices(outbox)
    assert len(notices) == 1
    assert notices[0]["kind"] == "dropped"
    assert "timeout" in notices[0]["text"]


def test_drain_outbox_await_refuses_a_retired_condition_by_name(tmp_path):
    """Never silently, and never by ignoring the extra terms (#1187): a
    directive still carrying v1's grammar is refused with a notice that
    names the verb which replaced it."""
    promoted, task, outbox = _drain_await(
        tmp_path, "---\nawait: spawn:evt-abcd | event\ntimeout: 5m\n---\n",
    )

    assert promoted == 0
    assert "await" not in task.meta
    notices = daemon._read_outbox_notices(outbox)
    assert len(notices) == 1
    assert "brnrd await" in notices[0]["text"]
    assert "no longer takes conditions" in notices[0]["text"]


def test_drain_outbox_await_arms_from_a_strand(tmp_path):
    """The refusal is gone, and its stated reason was false (#1187).

    A strand does not spend the resident's single-flight slot; it occupies a
    slot in the spawn pool and already holds it by existing. Refusing left a
    strand blocked on a subprocess with only a shell sleep loop — the exact
    boundary-free stretch #959 exists to end, forced by rule onto the runs
    least able to recover from it.
    """
    promoted, task, outbox = _drain_await(
        tmp_path, "---\nawait: true\ntimeout: 5m\n---\n",
        meta={"strand": True},
    )

    assert promoted == 1
    assert task.meta["await"]["timeout_seconds"] == 300.0
    assert daemon._read_outbox_notices(outbox) == []


def test_drain_outbox_await_key_present_with_empty_value_still_arms(tmp_path):
    """An ``await:`` with no value is not a refusal — the marker is the whole
    grammar, so "just tell me if anything comes in, or after N minutes" is
    exactly what a bare key means."""
    promoted, task, _outbox = _drain_await(
        tmp_path, "---\nawait:\ntimeout: 10m\n---\n",
    )

    assert promoted == 1
    assert task.meta["await"]["file"] is None
    assert task.meta["await"]["timeout_seconds"] == 600.0


def test_drain_outbox_await_arm_snapshots_currently_pending_ids(tmp_path):
    """#1327: arm time records which events are pending *right now*, so a
    fact this run already had in hand at arming — e.g. a ``spawn_completed``
    it already rendered and stamped ``observed_by`` in an earlier tick —
    cannot phantom-resolve every later tick's evaluation. See the resolve
    side's regression test,
    ``test_portal_state_await_excludes_a_completion_pending_before_arming``.
    """
    promoted, task, _outbox = _drain_await(
        tmp_path, "---\nawait: true\ntimeout: 20m\n---\n",
        pre_pending=[("spawn_completed", {"spawn_parent_run_id": "run-parent"})],
    )

    assert promoted == 1
    assert len(task.meta["await"]["armed_pending_ids"]) == 1


def test_drain_outbox_await_arm_snapshot_ignores_a_correspondents_message(tmp_path):
    """The snapshot is the *defect class*, not "everything pending now".

    #1327 is about a retired self-parented `spawn_completed`, which stays
    `status: pending` until run end because the #1146 observation stamp is
    not removal. A live correspondent's message is not that: it is pending
    because nobody has answered it. Excluding it would make the wait sleep
    straight through the person who wrote it, and
    `prompts/daemon-substrate.md` promises the opposite in as many words —
    "any *other* pending event resolves it the same way ... the queue never
    starves".

    Added on convergence: the first implementation snapshotted every pending
    id, and all three of its own tests went green over this, because each was
    derived from the implementation's shape rather than from the contract.
    """
    promoted, task, _outbox = _drain_await(
        tmp_path, "---\nawait: true\ntimeout: 20m\n---\n",
        pre_pending=[("telegram", None)],
    )

    assert promoted == 1
    assert task.meta["await"]["armed_pending_ids"] == []


def test_drain_outbox_await_arm_snapshot_ignores_another_parents_completion(tmp_path):
    """A completion belonging to some *other* parent is not this run's fact
    to have had in hand, so it never enters the snapshot either."""
    promoted, task, _outbox = _drain_await(
        tmp_path, "---\nawait: true\ntimeout: 20m\n---\n",
        pre_pending=[("spawn_completed", {"spawn_parent_run_id": "run-somebody-else"})],
    )

    assert promoted == 1
    assert task.meta["await"]["armed_pending_ids"] == []


def test_drain_outbox_await_arm_snapshot_is_empty_with_nothing_pending(tmp_path):
    """The ordinary case — nothing was already waiting — snapshots nothing,
    so a plain ``await:`` still resolves on the very next event exactly as
    it always has."""
    promoted, task, _outbox = _drain_await(
        tmp_path, "---\nawait: true\ntimeout: 20m\n---\n",
    )

    assert promoted == 1
    assert task.meta["await"]["armed_pending_ids"] == []


# ── cut: / the bolt (design-the-bolt.md) ─────────────────────────────


def test_issue_filing_claims_matches_the_conservative_pattern_set():
    """#1259's pattern set, pinned directly: adjacency to the `#N` token is
    what does the work of not firing on ordinary issue-number prose."""
    assert daemon._issue_filing_claims("Filed #12 to track this.") == {12: "filed"}
    assert daemon._issue_filing_claims("#12 filed, will follow up.") == {12: "filed"}
    assert daemon._issue_filing_claims("Opened #12 for the follow-up.") == {12: "filed"}
    assert daemon._issue_filing_claims("Closes #12 outright.") == {12: "closed"}
    assert daemon._issue_filing_claims("closing #12 now.") == {12: "closed"}
    # a later claim about the same number wins — a real filed-then-closed
    # lifecycle settles on the final state, not the first sentence.
    assert daemon._issue_filing_claims(
        "Filed #12 earlier this run.", "Closes #12.",
    ) == {12: "closed"}
    # ordinary mentions, no adjacent claim verb — nothing fires.
    assert daemon._issue_filing_claims("See #12 for context.") == {}
    assert daemon._issue_filing_claims("This fixes the bug described in #12.") == {}
    assert daemon._issue_filing_claims("") == {}
    # a PR opened with a number between the verb and the ref — the
    # adjacency requirement structurally excludes it.
    assert daemon._issue_filing_claims("Opened PR #12 for review.") == {}


def _drain_cut(
    tmp_path, frontmatter, *, meta=None, stats=None, repo_root=None, filename="cut.md",
    topics="the-loom",
):
    """Stage one ``cut:`` directive and drain it.

    Returns ``(promoted, task, outbox, inbox, responses, event_id)``. Model:
    ``_drain_await`` above — the current event is born ``processing`` (a
    live wake holding it), so a cut targeting "the current event" never
    also has to dispose of itself.

    *topics* stages a ``.topics`` claim by default so the topicless bolt
    check (added alongside this docstring) doesn't bounce every pre-existing
    cut fixture in this file — pass ``None`` to test the topicless path
    itself, which has its own dedicated tests below.
    """
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True, exist_ok=True)
    if topics is not None:
        (outbox / ".topics").write_text(topics + "\n", encoding="utf-8")
    path = protocol.create_event(inbox, "telegram", "original", status="processing")
    event_id = path.stem
    (outbox / filename).write_text(frontmatter, encoding="utf-8")
    task = Run(
        id="run-parent", event_id=event_id, body="original", source="telegram",
        meta=dict(meta or {}),
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, event_id),
        task, responses, event_id, outbox, inbox,
        repo_root=repo_root,
        stats=stats,
    )
    return promoted, task, outbox, inbox, responses, event_id


def test_drain_outbox_cut_minimal_bolt_is_accepted(tmp_path):
    """A bare ``cut: true`` with a woven body and nothing declared is a
    legal bolt — stopping is a result."""
    stats: dict[str, int] = {}
    promoted, task, outbox, _inbox, responses, event_id = _drain_cut(
        tmp_path, "---\ncut: true\n---\nAll done here.\n", stats=stats,
    )

    assert stats.get("cut") == 1
    bolt = task.meta["bolt"]
    assert bolt["annotated"] == 0
    assert bolt["accepted_at"]
    assert daemon._read_outbox_notices(outbox) == []
    # Delivered through the existing `event:` lane, on the current event.
    [partial_path] = protocol.list_partials(responses, event_id)
    assert "All done here." in protocol.read_partial(partial_path)
    assert promoted == 1
    assert not (outbox / "cut.md").exists()


def test_drain_outbox_cut_bounces_double_wrapped_staging_casualty(tmp_path):
    """Old porcelain output can remain on disk after an upgrade. The drain
    recognizes the stranded declaration before accepting a minimal bolt."""
    promoted, task, outbox, _inbox, responses, event_id = _drain_cut(
        tmp_path,
        "---\ncut: true\n---\n"
        "cut: true\n---\nasks:\n"
        "  - event: evt-old\n    disposition: answered\n",
        filename="do-old-cut-0.md",
    )

    assert promoted == 0
    assert "bolt" not in task.meta
    assert task.meta["cut_bounces"] == 1
    [notice] = daemon._read_outbox_notices(outbox)
    assert notice["source_file"] == "do-old-cut-0.md"
    assert "declaration-shaped body" in notice["text"]
    assert "staging casualty" in notice["text"]
    assert protocol.list_partials(responses, event_id) == []


def test_drain_outbox_cut_undispositioned_pending_event_bounces(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    protocol.create_event(inbox, source="telegram", body="a question", status="pending")

    promoted, task, outbox, _inbox, responses, event_id = _drain_cut(
        tmp_path, "---\ncut: true\n---\nDone.\n",
    )

    assert promoted == 0
    assert "bolt" not in task.meta
    assert task.meta["cut_bounces"] == 1
    [notice] = daemon._read_outbox_notices(outbox)
    assert notice["kind"] == "refused"
    assert "cut bounced:" in notice["text"]
    assert "undispositioned" in notice["text"]
    assert protocol.list_partials(responses, event_id) == []


def test_drain_outbox_cut_answered_row_for_still_pending_event_bounces(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    ask_path = protocol.create_event(
        inbox, source="telegram", body="a question", status="pending",
    )
    ask_id = ask_path.stem

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, f"---\ncut: true\nasks:\n  {ask_id}: answered\n---\nDone.\n",
    )

    assert promoted == 0
    [notice] = daemon._read_outbox_notices(outbox)
    assert "declared answered but is still pending" in notice["text"]


def test_drain_outbox_cut_asks_row_for_a_resolved_event_passes_regardless(tmp_path):
    """An event no longer pending is provably closed either way — the
    disposition claimed for it is not re-checked."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    resolved = protocol.create_event(
        inbox, source="telegram", body="handled elsewhere", status="pending",
    )
    resolved_id = resolved.stem
    ev = protocol.list_pending(inbox)[0]
    protocol.set_status(ev, "done")

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\n---\nDone.\n",
    )

    assert promoted == 1
    assert daemon._read_outbox_notices(outbox) == []
    assert task.meta["bolt"]["annotated"] == 0


def test_drain_outbox_cut_bounce_cap_then_accept_annotated(tmp_path):
    """Cap 3 (design doc, fork 3, signed): bounces 1 and 2 refuse; the 3rd
    accepts anyway, annotated with the daemon's own dissent."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    (outbox / ".topics").write_text("the-loom\n", encoding="utf-8")
    protocol.create_event(inbox, source="telegram", body="a question", status="pending")
    path = protocol.create_event(inbox, "telegram", "original", status="processing")
    event_id = path.stem
    task = Run(
        id="run-parent", event_id=event_id, body="original", source="telegram",
        meta={},
    )
    emit = daemon._WorkerEmit(brr_dir, None, event_id)

    for i in range(1, 3):
        (outbox / f"cut{i}.md").write_text("---\ncut: true\n---\nDone.\n")
        promoted = daemon._drain_outbox(
            emit, task, responses, event_id, outbox, inbox,
        )
        assert promoted == 0
        assert task.meta["cut_bounces"] == i
        assert "bolt" not in task.meta

    (outbox / "cut3.md").write_text("---\ncut: true\n---\nDone.\n")
    promoted = daemon._drain_outbox(emit, task, responses, event_id, outbox, inbox)

    assert promoted == 1
    assert task.meta["cut_bounces"] == 3
    bolt = task.meta["bolt"]
    assert bolt["annotated"] == 1
    [partial_path] = protocol.list_partials(responses, event_id)
    body = protocol.read_partial(partial_path)
    assert "daemon: 1 check unresolved" in body
    assert "undispositioned" in body


def test_drain_outbox_cut_persists_declaration_and_daemon_dissent(tmp_path):
    """The real drain seam keeps what it validated when cap-3 accepts with
    dissent; produce remains canonical in relics rather than duplicated."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    (outbox / ".topics").write_text("the-loom\n", encoding="utf-8")
    ask = protocol.create_event(
        inbox, source="telegram", body="which lane?", status="pending",
    ).stem
    current = protocol.create_event(
        inbox, "telegram", "original", status="processing",
    ).stem
    task = Run(
        id="run-parent", event_id=current, body="original", source="telegram",
        meta={"cut_bounces": 2},
    )
    (outbox / "cut.md").write_text(
        "---\ncut: true\n"
        f"asks:\n  {ask}:\n    disposition: answered\n    label: Which lane\n"
        "decisions:\n  storage: kept\n"
        "produce: none\n"
        "owed:\n  frontend:\n    ref: card widening\n"
        "    why: separate surface\n    where: sibling PR\n"
        "spend: ~$2, 20m\nnext: sibling PR\n---\nDone.\n",
        encoding="utf-8",
    )

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, current),
        task, responses, current, outbox, inbox,
    )

    assert promoted == 1
    bolt = task.meta["bolt"]
    assert bolt["asks"] == [{
        "event": ask,
        "disposition": "answered",
        "label": "Which lane",
    }]
    assert bolt["owed"] == [{
        "label": "frontend",
        "ref": "card widening",
        "why": "separate surface",
        "where": "sibling PR",
    }]
    assert bolt["decisions"] == ["storage: kept"]
    assert bolt["spend_declared"] == "~$2, 20m"
    assert bolt["next"] == "sibling PR"
    assert bolt["dissent"] == [
        f"{daemon.hooks_mod._short_event_id(ask)} declared answered but is still pending"
    ]
    assert "produce" not in bolt


def test_drain_outbox_cut_unknown_key_is_refused_with_a_notice(tmp_path):
    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\ndecision: kept\n---\nDone.\n",
    )

    assert promoted == 0
    assert "bolt" not in task.meta
    assert "cut_bounces" not in task.meta
    [notice] = daemon._read_outbox_notices(outbox)
    assert notice["kind"] == "dropped"
    assert "cut dropped:" in notice["text"]
    assert "decision" in notice["text"]


def test_drain_outbox_cut_produce_none_declared_but_commits_exist_bounces(tmp_path):
    repo_root = tmp_path / "repo"
    init_git_repo(repo_root)
    commit_files(repo_root, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo_root, check=True)
    commit_files(repo_root, {"b.txt": "2"}, message="add b")

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\nproduce: none\n---\nDone.\n",
        meta={"branch_name": "feature", "seed_ref": "main"},
        repo_root=repo_root,
    )

    assert promoted == 0
    [notice] = daemon._read_outbox_notices(outbox)
    assert "produce: none declared but" in notice["text"]
    assert "commit" in notice["text"]


def test_drain_outbox_cut_produce_attested_but_empty_bounces(tmp_path):
    repo_root = tmp_path / "repo"
    init_git_repo(repo_root)
    commit_files(repo_root, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo_root, check=True)

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\nproduce: attested\n---\nDone.\n",
        meta={"branch_name": "feature", "seed_ref": "main"},
        repo_root=repo_root,
    )

    assert promoted == 0
    [notice] = daemon._read_outbox_notices(outbox)
    assert "manifest is empty" in notice["text"]


def test_drain_outbox_cut_produce_attested_with_commits_is_clean(tmp_path):
    repo_root = tmp_path / "repo"
    init_git_repo(repo_root)
    commit_files(repo_root, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo_root, check=True)
    commit_files(repo_root, {"b.txt": "2"}, message="add b")

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\nproduce: attested\n---\nDone.\n",
        meta={"branch_name": "feature", "seed_ref": "main"},
        repo_root=repo_root,
    )

    assert promoted == 1
    assert daemon._read_outbox_notices(outbox) == []


def test_drain_outbox_cut_issue_filed_claim_with_no_relic_bounces(tmp_path):
    """#1259: a reply that claims "filed #N" with no matching `issue`
    relic in the manifest is the same claim-vs-receipt gap the `produce:`
    check already catches for branches — the run-260808-2* case that shipped
    six issues and a bolt card reading "nothing produced"."""
    repo_root = tmp_path / "repo"
    init_git_repo(repo_root)
    commit_files(repo_root, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo_root, check=True)
    commit_files(repo_root, {"b.txt": "2"}, message="add b")

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path,
        "---\ncut: true\nproduce: attested\n---\nFiled #9999 to track the follow-up.\n",
        meta={"branch_name": "feature", "seed_ref": "main"},
        repo_root=repo_root,
    )

    assert promoted == 0
    [notice] = daemon._read_outbox_notices(outbox)
    assert (
        "issue #9999 mentioned as filed but no issue relic exists"
        in notice["text"]
    )
    assert "brnrd relic issue 9999 --opened" in notice["text"]


def test_drain_outbox_cut_issue_filed_claim_with_relic_is_clean(tmp_path):
    """The same claim, recorded — `brnrd relic issue 9999 --opened` before
    the cut — clears the check."""
    repo_root = tmp_path / "repo"
    init_git_repo(repo_root)
    commit_files(repo_root, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo_root, check=True)
    commit_files(repo_root, {"b.txt": "2"}, message="add b")

    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-current"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    daemon.relics.append(outbox_dir, "issue", number=9999, action="opened")

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path,
        "---\ncut: true\nproduce: attested\n---\nFiled #9999 to track the follow-up.\n",
        meta={"branch_name": "feature", "seed_ref": "main"},
        repo_root=repo_root,
    )

    assert promoted == 1
    assert daemon._read_outbox_notices(outbox) == []
    assert task.meta["bolt"]["annotated"] == 0


def test_drain_outbox_cut_issue_closed_claim_read_off_the_card_bounces(tmp_path):
    """The scan reads `.card` as well as the reply body — a claim narrated
    there and nowhere else must still be caught."""
    repo_root = tmp_path / "repo"
    init_git_repo(repo_root)
    commit_files(repo_root, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo_root, check=True)
    commit_files(repo_root, {"b.txt": "2"}, message="add b")

    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-current"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / ".card").write_text(
        "## Now\ncloses #4242 — the stale-lock ticket\n", encoding="utf-8",
    )

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path,
        "---\ncut: true\nproduce: attested\n---\nDone.\n",
        meta={"branch_name": "feature", "seed_ref": "main"},
        repo_root=repo_root,
    )

    assert promoted == 0
    [notice] = daemon._read_outbox_notices(outbox)
    assert "issue #4242 mentioned as closed but no issue relic exists" in notice["text"]
    assert "brnrd relic issue 4242 --closed" in notice["text"]


def test_drain_outbox_cut_bare_issue_mention_does_not_false_positive(tmp_path):
    """#1259's own hard constraint: ordinary prose that mentions an issue
    number without claiming to have filed or closed it must not bounce —
    a false mismatch is worse than a missed one."""
    repo_root = tmp_path / "repo"
    init_git_repo(repo_root)
    commit_files(repo_root, {"a.txt": "1"}, message="seed")
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo_root, check=True)
    commit_files(repo_root, {"b.txt": "2"}, message="add b")

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path,
        "---\ncut: true\nproduce: attested\n---\n"
        "See #9999 for the context motivating this change; it stays open.\n",
        meta={"branch_name": "feature", "seed_ref": "main"},
        repo_root=repo_root,
    )

    assert promoted == 1
    assert daemon._read_outbox_notices(outbox) == []


def test_drain_outbox_cut_produce_survives_mid_run_branch_rename(tmp_path):
    """#1293: ``task.meta["branch_name"]`` is stamped once at prepare time,
    before the run has done any work — always the placeholder
    ``brr/<run-id>``. The receipts pin (``AGENTS.md`` / every dispatch spec)
    requires renaming that placeholder to a descriptive slug *before
    committing*, so a run that follows the pin correctly ends up with a
    stale ``branch_name`` in ``task.meta`` and all its commits on a branch
    that name no longer resolves to. The exact sequence r5ia drove by hand
    this morning (run-260810-0201-r5ia, cited in #1293): checkout starts on
    the placeholder, ``git branch -m`` to the descriptive slug mid-run, more
    commits land after the rename. The bolt's produce check must still
    derive all of it — before *and* after the rename — from the live
    branch, not the abandoned stamp."""
    repo_root = tmp_path / "repo"
    init_git_repo(repo_root)
    commit_files(repo_root, {"a.txt": "1"}, message="seed")
    subprocess.run(
        ["git", "checkout", "-b", "brr/run-xyz1"], cwd=repo_root, check=True,
    )
    commit_files(repo_root, {"b.txt": "2"}, message="before the rename")
    subprocess.run(
        ["git", "branch", "-m", "brr/a-descriptive-slug"],
        cwd=repo_root, check=True,
    )
    commit_files(repo_root, {"c.txt": "3"}, message="after the rename")

    # branch_name still names the placeholder — prepare-time stamp, never
    # re-read after the rename the run performed on its own worktree.
    meta = {"branch_name": "brr/run-xyz1", "seed_ref": "main"}

    branch, seed = daemon.relics.collection_scope(meta, repo_root)
    records = daemon.relics.collect(repo_root, branch=branch, seed_ref=seed, outbox_dir=None)
    subjects = {r.get("subject") for r in records if r["kind"] == "commit"}
    assert "before the rename" in subjects
    assert "after the rename" in subjects

    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\nproduce: attested\n---\nDone.\n",
        meta=meta,
        repo_root=repo_root,
    )

    assert promoted == 1
    assert daemon._read_outbox_notices(outbox) == []


def test_drain_outbox_cut_owed_promise_with_no_carried_row_bounces(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    responses = brr_dir / "responses"
    path = protocol.create_event(inbox, "telegram", "original", status="processing")
    event_id = path.stem
    promises.append(outbox, "pr", count=1, ref="the rollout")

    (outbox / "cut.md").write_text("---\ncut: true\nowed: none\n---\nDone.\n")
    task = Run(
        id="run-parent", event_id=event_id, body="original", source="telegram",
        meta={},
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, event_id),
        task, responses, event_id, outbox, inbox,
    )

    assert promoted == 0
    [notice] = daemon._read_outbox_notices(outbox)
    assert "the rollout" in notice["text"]
    assert "no carried row" in notice["text"]


def test_drain_outbox_cut_owed_carried_row_matching_ref_is_clean(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    (outbox / ".topics").write_text("the-loom\n", encoding="utf-8")
    responses = brr_dir / "responses"
    path = protocol.create_event(inbox, "telegram", "original", status="processing")
    event_id = path.stem
    promises.append(outbox, "pr", count=1, ref="the rollout")

    frontmatter = (
        "---\ncut: true\nowed:\n  x:\n    ref: the rollout\n"
        "    why: still open\n    where: next run\n---\nDone.\n"
    )
    (outbox / "cut.md").write_text(frontmatter)
    task = Run(
        id="run-parent", event_id=event_id, body="original", source="telegram",
        meta={},
    )
    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, event_id),
        task, responses, event_id, outbox, inbox,
    )

    assert promoted == 1
    assert daemon._read_outbox_notices(outbox) == []


def test_drain_outbox_cut_notice_carries_source_file_for_identity_join(tmp_path):
    """do.py's ``find_matching_notice`` prefers this over the text
    substring heuristic when present."""
    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\ndecision: kept\n---\nDone.\n", filename="cut-abc.md",
    )
    assert promoted == 0
    [notice] = daemon._read_outbox_notices(outbox)
    assert notice["source_file"] == "cut-abc.md"


# ── topic-per-run: the bolt asks once (the-run-that-claims-its-thread) ──


def test_drain_outbox_cut_topicless_bounces(tmp_path):
    """No `.topics` and no item taken ⇒ the bolt names it, once, and bounces
    like any other declared mismatch — the existing cap-3 ladder, not a new
    nag channel."""
    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\n---\nDone.\n", topics=None,
    )

    assert promoted == 0
    [notice] = daemon._read_outbox_notices(outbox)
    assert "cut bounced:" in notice["text"]
    assert "topicless" in notice["text"]
    assert "write .topics or take an item" in notice["text"]


def test_drain_outbox_cut_topics_claimed_is_clean(tmp_path):
    """A run that wrote `.topics` clears the check — no dissent."""
    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\n---\nDone.\n", topics="the-loom the-post",
    )

    assert promoted == 1
    assert daemon._read_outbox_notices(outbox) == []


def test_drain_outbox_cut_item_taken_satisfies_the_check_with_no_topics(tmp_path):
    """The items store's own signal — an `item` relic on this run's
    manifest — satisfies the check with no `.topics` file at all."""
    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\n---\nDone.\n", topics=None,
    )
    assert promoted == 0  # sanity: without the item relic this bounces

    # Re-run with the item relic present.
    outbox2 = tmp_path / ".brr" / "outbox" / "evt-current2"
    outbox2.mkdir(parents=True)
    inbox2 = tmp_path / ".brr" / "inbox"
    responses2 = tmp_path / ".brr" / "responses"
    daemon.relics.append(outbox2, "item", address="w-42")
    (outbox2 / "cut.md").write_text("---\ncut: true\n---\nDone.\n", encoding="utf-8")
    path = protocol.create_event(inbox2, "telegram", "original", status="processing")
    event_id2 = path.stem
    task2 = Run(
        id="run-parent-2", event_id=event_id2, body="original", source="telegram",
        meta={},
    )
    promoted2 = daemon._drain_outbox(
        daemon._WorkerEmit(tmp_path / ".brr", None, event_id2),
        task2, responses2, event_id2, outbox2, inbox2,
    )
    assert promoted2 == 1
    assert daemon._read_outbox_notices(outbox2) == []


def test_drain_outbox_cut_topicless_garbage_topics_file_still_bounces(tmp_path):
    """A `.topics` file with no slug-shaped token is the same as absent —
    lenient parse, never a crash, but not a claim either."""
    promoted, task, outbox, _inbox, _responses, _event_id = _drain_cut(
        tmp_path, "---\ncut: true\n---\nDone.\n", topics="!!! ### $$$ ___",
    )

    assert promoted == 0
    [notice] = daemon._read_outbox_notices(outbox)
    assert "topicless" in notice["text"]


# ── portal-state `bolt` projection ───────────────────────────────────


def test_portal_state_bolt_absent_when_never_cut(tmp_path):
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "bolt" not in payload


def test_portal_state_bolt_projects_accepted(tmp_path):
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["bolt"] = {
        "accepted_at": "2026-08-08T00:00:00Z", "annotated": 0, "spend_declared": None,
    }

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["bolt"] == {
        "accepted": True, "annotated": 0, "accepted_at": "2026-08-08T00:00:00Z",
    }


def test_portal_state_bolt_projects_annotated_count(tmp_path):
    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-1"
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["bolt"] = {
        "accepted_at": "2026-08-08T00:00:00Z", "annotated": 2, "spend_declared": "~$1",
    }

    path = daemon._write_live_portal_state(
        outbox_dir, inbox_dir, "evt-1", task, phase="running",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["bolt"]["annotated"] == 2


# ── THE WELD ignition source guard (#1383) ────────────────────────────────
#
# #972's ignition half fires on an event's own body; #1383 found a
# `source: schedule` recurring entry's *agenda* prose ("the w-14/w-45/…
# cluster") getting scanned as if it were a task, welding every firing's
# produce onto whatever ids the agenda happened to name. The fix that
# narrowed to "not schedule" still missed `source: spawn` — a dispatched
# strand's own spec is equally brnrd's prose about work, not a
# correspondent's — caught live when `run-260816-2051-l00q`'s own spawn
# event ignited two items it was never asked to touch. `_weld_ignition_body`
# asserts the property (source in `protocol.INTERNAL_SOURCES` ⇒ brnrd's own
# writing, never a correspondent addressing a task) instead of enumerating
# members, and `_weld_ignite` is the exact call `_run_worker` makes — these
# tests drive that function, not `items.scan_item_ids` in isolation.

_WELD_ITEM_TEXT = """# A gate chip

type: action
refs: hugimuni-labs/brnrd#1
prompt: Ship the gate chip.

Body text for the item file itself; irrelevant to ignition.
"""


def _warp_dir_with_item(tmp_path: Path, item_id: str = "w-8") -> Path:
    warp = tmp_path / "surface" / "warp"
    warp.mkdir(parents=True)
    (warp / f"{item_id}.md").write_text(_WELD_ITEM_TEXT, encoding="utf-8")
    return warp


def _account_ctx_for_warp(tmp_path: Path) -> "daemon.account.AccountContext":
    return daemon.account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path,
        dispatch_inbox=tmp_path / "dispatch" / "inbox",
        responses_dir=tmp_path / "dispatch" / "responses",
        runs_dir=tmp_path / "runs",
        repos={},
        default_repo=daemon.account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )


@pytest.mark.parametrize(
    "source", sorted(protocol.INTERNAL_SOURCES),
)
def test_weld_ignition_body_empty_for_every_internal_source(source):
    """Every brnrd-minted source (#1118's completeness-tested set) is
    system-authored prose, not a correspondent's task — including `spawn`,
    the member the narrower "not schedule" guard missed."""
    body = daemon._weld_ignition_body({"source": source, "body": "please do w-8"})
    assert body == ""


@pytest.mark.parametrize("source", ["telegram", "github", "slack", "signal", "cloud"])
def test_weld_ignition_body_passes_through_for_correspondent_sources(source):
    """A gate-ingress source is never in `INTERNAL_SOURCES` (nor could a
    future one be, without becoming a `create_event` call this package's
    own AST completeness test would catch) — it stays ignition-eligible
    with no edit to this predicate."""
    body = daemon._weld_ignition_body({"source": source, "body": "please do w-8"})
    assert body == "please do w-8"


def test_weld_ignite_schedule_source_leaves_item_unchanged(tmp_path):
    warp = _warp_dir_with_item(tmp_path)
    ctx = _account_ctx_for_warp(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    event = {
        "source": "schedule",
        "body": "the w-14/w-45/w-46/w-47 cluster and w-8 count as docket",
    }

    resolved = daemon._weld_ignite(event, ctx, outbox, "run-agenda-1")

    assert resolved == []
    assert "taken:" not in (warp / "w-8.md").read_text(encoding="utf-8")


def test_weld_ignite_spawn_source_leaves_item_unchanged(tmp_path):
    """The case the parent's own measurement caught: a `spawn:` dispatch's
    spec quoting an id as a citation must not ignite it either."""
    warp = _warp_dir_with_item(tmp_path)
    ctx = _account_ctx_for_warp(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    event = {
        "source": "spawn",
        "body": "Read the issue; it quotes the w-14/w-45/w-46/w-47 cluster and w-8.",
    }

    resolved = daemon._weld_ignite(event, ctx, outbox, "run-spawn-1")

    assert resolved == []
    assert "taken:" not in (warp / "w-8.md").read_text(encoding="utf-8")


def test_weld_ignite_correspondent_source_still_ignites(tmp_path):
    warp = _warp_dir_with_item(tmp_path)
    ctx = _account_ctx_for_warp(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    event = {"source": "telegram", "body": "please pick up w-8 today"}

    resolved = daemon._weld_ignite(event, ctx, outbox, "run-user-1")

    assert resolved == ["w-8"]
    assert "taken: run-user-1" in (warp / "w-8.md").read_text(encoding="utf-8")


# ── THE WELD ignition item-state guard (#1436 facet 1) ────────────────────
#
# `w-14` closed 2026-08-13 and was ignited eleven more times after that —
# every one of them a `source: schedule` / `source: spawn` firing that
# #1435's source guard now stops. But nothing about the source guard says a
# *correspondent* naming a done item is any different: ignition claims work
# is starting, and a closed item has no work to start, whatever addressed
# it. This is a property of the item, checked independently of — and in
# addition to — the source guard: a correspondent source (which #1435
# deliberately leaves ignition-eligible) must still refuse a done item.

_WELD_DONE_ITEM_TEXT = """# A gate chip

type: action
done: 2026-08-13 run-old

Body text for the item file itself; irrelevant to ignition.
"""

_WELD_RETIRED_ITEM_TEXT = """# A gate chip

type: action
retired: 2026-08-13 superseded

Body text for the item file itself; irrelevant to ignition.
"""


def test_weld_ignite_done_item_leaves_item_unchanged_even_from_a_correspondent(
    tmp_path,
):
    """The acceptance case verbatim: a `done:` item stays untouched no
    matter the source — including a live correspondent, which #1435's
    source guard alone would wave through."""
    warp = _warp_dir_with_item(tmp_path)
    (warp / "w-8.md").write_text(_WELD_DONE_ITEM_TEXT, encoding="utf-8")
    ctx = _account_ctx_for_warp(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    event = {"source": "telegram", "body": "please pick up w-8 today"}

    resolved = daemon._weld_ignite(event, ctx, outbox, "run-user-2")

    assert resolved == []
    text = (warp / "w-8.md").read_text(encoding="utf-8")
    assert "taken:" not in text
    relics_file = outbox / ".relics.jsonl"
    assert not relics_file.exists() or "w-8" not in relics_file.read_text(
        encoding="utf-8"
    )


def test_weld_ignite_retired_item_also_not_a_candidate(tmp_path):
    """A retired item is equally closed — no work left to start — so the
    same item-state guard covers it, not just `done:`."""
    warp = _warp_dir_with_item(tmp_path)
    (warp / "w-8.md").write_text(_WELD_RETIRED_ITEM_TEXT, encoding="utf-8")
    ctx = _account_ctx_for_warp(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    event = {"source": "telegram", "body": "please pick up w-8 today"}

    resolved = daemon._weld_ignite(event, ctx, outbox, "run-user-3")

    assert resolved == []
    assert "taken:" not in (warp / "w-8.md").read_text(encoding="utf-8")
