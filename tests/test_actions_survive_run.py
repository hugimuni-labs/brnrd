"""Step 6a of design-the-action-ledger.md: the ledger survives its run.

Two facts, each pinned per run-end path:

* ``.actions.jsonl`` is in the control-file capture (``daemon.PRESERVED``);
* by the time that capture runs, terminal endings (done, halted, stopped)
  have no ``attempted`` acts, while a held run keeps its attempts open.

Every ending funnels through ``_run_worker_and_finalize``'s tail
(``_stamp_unresolved_acts`` → ``_capture_control_files``), so the tests drive
that production entry with a runner that leaves one ``attempted`` row, and
look at the outbox *at the moment the capture is called* — the state the node
would receive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import actions, daemon, envs, resource_hold
from brr.run import HALTED_STATUS
from brr.runner import RunnerResult

from _helpers import make_event, write_repo_scaffold


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _stub_env(monkeypatch, tmp_path, *, final_status=None):
    worktree_path = tmp_path / ".brr" / "worktrees" / "stub"
    worktree_path.mkdir(parents=True, exist_ok=True)

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                    outbox_path=None):
            return envs.RunContext(
                name=self.name, cwd=worktree_path, repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path, response_path_env=response_path,
                outbox_host=outbox_path, outbox_env=outbox_path,
                branch_name=f"brr/{task.id}",
                env_state={"worktree_path": str(worktree_path)},
            )

        def invoke(self, ctx, runner_name, invocation, cfg=None, *, trace=False):
            raise NotImplementedError

        def finalize(self, ctx, task, runs_dir):
            if final_status is not None:
                task.update_status(final_status, runs_dir)
            return task

    monkeypatch.setattr(envs, "get_env", lambda _name: StubEnv())


def _wire(monkeypatch):
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)


def _drive(tmp_path, monkeypatch, *, eid, result, extra_files=None, cfg=None,
           stop=False, final_status=None):
    """Run one event; return (task, the seeded act's state as the capture saw it)."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid=eid)
    _stub_env(monkeypatch, tmp_path, final_status=final_status)
    _wire(monkeypatch)
    base_env = envs.get_env("worktree")
    acts: dict[str, str] = {}
    invoked: list[bool] = []
    if stop:
        # A stop is only real once the runner has been (and died); before that
        # the dispatcher would never have started the run.
        monkeypatch.setattr(
            daemon, "_stopped_run_control",
            lambda _eid: {"stopped_by": "run-parent"} if invoked else None,
        )

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        outbox = Path(invocation.env["BRR_OUTBOX_DIR"])
        outbox.mkdir(parents=True, exist_ok=True)
        # A POST whose reply this run never read: attempted, then the run ends.
        acts["mine"] = actions.append(
            outbox, verb="message", target="gate:telegram", state="attempted",
        )
        invoked.append(True)
        for name, text in (extra_files or {}).items():
            (outbox / name).write_text(text, encoding="utf-8")
        return result(invocation, runner_name)

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    seen: dict[str, dict] = {}
    real_capture = daemon._capture_control_files

    def spy(account_context, task, *, repo_label, outbox_dir):
        seen["rows"] = actions.read(outbox_dir)
        return real_capture(
            account_context, task, repo_label=repo_label, outbox_dir=outbox_dir,
        )

    monkeypatch.setattr(daemon, "_capture_control_files", spy)
    task = daemon._run_worker_and_finalize(
        event, tmp_path, tmp_path / ".brr" / "responses", cfg or {}, 0,
    )
    rows = seen.get("rows") or {}
    return task, rows[acts["mine"]]["state"]


def _ok(invocation, runner_name):
    Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
    Path(invocation.response_path).write_text("done\n", encoding="utf-8")
    return RunnerResult(
        invocation=invocation, runner_name=runner_name, command=["mock"],
        stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
    )


def _usage_limit(invocation, runner_name):
    return RunnerResult(
        invocation=invocation, runner_name=runner_name, command=["mock"],
        stdout="", stderr="codex task_complete error (usage limit exceeded): out of quota",
        returncode=1, trace_dir=None, artifacts=[],
        codex_task_error={"kind": "usage_limit_exceeded", "message": "out of quota"},
    )


def test_actions_ledger_is_a_preserved_control_file():
    assert daemon.PRESERVED[actions.CONTROL_NAME] == "actions.jsonl"


def test_a_finished_run_hands_the_capture_a_closed_ledger(tmp_path, monkeypatch):
    task, state = _drive(
        tmp_path, monkeypatch, eid="evt-act-done", result=_ok,
        cfg={"seat.park_on_turn_end": False},  # else a clean turn end parks
    )
    assert task.status == "done"
    assert state == "ambiguous"


def test_a_halted_run_hands_the_capture_a_closed_ledger(tmp_path, monkeypatch):
    task, state = _drive(
        tmp_path, monkeypatch, eid="evt-act-halt", result=_ok,
        extra_files={
            "0001-halt.md": (
                "---\nhalt: true\nreason: spent\nresumable: read the report\n"
                "---\nbye\n"
            ),
        },
    )
    assert task.status == HALTED_STATUS
    assert state == "ambiguous"


def test_a_stopped_run_hands_the_capture_a_closed_ledger(tmp_path, monkeypatch):
    task, state = _drive(
        tmp_path, monkeypatch, eid="evt-act-stop", result=_ok, stop=True,
    )
    assert task.status == "stopped"
    assert state == "ambiguous"


def test_a_held_run_hands_the_capture_an_open_ledger(tmp_path, monkeypatch):
    """A resource park is resumable, so capture must not declare it ended."""
    task, state = _drive(
        tmp_path, monkeypatch, eid="evt-act-hold", result=_usage_limit,
    )
    assert task.status == resource_hold.RUN_STATUS
    assert state == "attempted"


def test_a_hold_finalizing_as_terminal_hands_the_capture_a_closed_ledger(
    tmp_path, monkeypatch,
):
    """A terminal environment outcome wins over the active hold record."""
    task, state = _drive(
        tmp_path, monkeypatch, eid="evt-act-hold-error", result=_usage_limit,
        final_status="error",
    )
    assert task.status == "error"
    assert resource_hold.is_active(task.meta["resource_hold"])
    assert state == "ambiguous"
