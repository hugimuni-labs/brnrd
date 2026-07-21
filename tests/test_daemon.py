"""Tests for the daemon worker after the triage stage was removed."""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from brr import daemon, envs, presence, protocol
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
    """A tap naming a profile this daemon doesn't know (stale rack, another
    daemon's catalog) is spent rather than left to wedge every future wake."""
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

    # No override applied, but the request is consumed (acked as spent).
    assert seen_overrides and seen_overrides[0] is None
    assert wake_request_mod.pending(brr_dir) is None
    assert wake_request_mod.consumed_ids(brr_dir) == ["wake_11"]


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


def _policy_control_target(tmp_path, body, *, conversation_key="telegram:42:"):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    path = protocol.create_event(
        inbox,
        "telegram",
        body,
        conversation_key=conversation_key,
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
        if len(pending_calls) == 1:
            return [event]
        # Second call breaks the loop in the main thread. The finally
        # block waits for the in-flight worker to finish before
        # tearing the pool down, so statuses observed by the worker
        # thread are present when pytest.raises captures the exit.
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

    assert calls == ["write-pid", "scan", "clear-pid"]


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

    assert order == ["write-pid", "watcher", "watch", "reexec", "clear-pid"]


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
    assert seen == {
        "repo_root": work_dir,
        "branch": "brr/work",
        "seed_ref": "main",
        "outbox_dir": outbox_dir,
    }


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
    monkeypatch.setattr(daemon, "_retire_internal_event", lambda _event, _responses: False)

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
