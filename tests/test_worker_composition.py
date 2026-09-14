"""The throw has phases — composition against a baseline captured from ``main``.

``daemon._run_worker`` was split into ``brr.worker`` (prepare · dispatch ·
stream · boundary · finalize) with *no behaviour change* as the contract. The
existing suite is the first proof; this module is the second: every scenario
below was driven through ``_run_worker`` on ``main`` **before** the split, and
what it produced — the packets emitted, in order; the run record; the portal
payload; the event's statuses; the response; the Shuttle rows; the log lines —
was normalised and frozen under ``tests/fixtures/worker_golden/``. The same
drive through the phased worker must reproduce each file exactly.

Regenerating a golden (``BRR_WORKER_GOLDEN_WRITE=1``) is only honest on a
tree whose worker is the one the golden claims to describe; a diff here is a
behaviour change until proven otherwise.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import pytest

from brr import daemon, envs, protocol, resource_hold
from brr.run import Run
from brr.runner import RunnerArtifactRecord, RunnerResult

from _helpers import StubWorktreeEnv, make_event, succeed_invoke, write_repo_scaffold

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "worker_golden"
WRITE = os.environ.get("BRR_WORKER_GOLDEN_WRITE") == "1"

_RUN_ID_RE = re.compile(r"run-\d{6}-\d{4}-[a-z0-9]{4}")
_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_HEX_TOKEN_RE = re.compile(r"\b[0-9a-f]{16,64}\b")
# Keys whose values are clocks, pids or content hashes: present-or-absent is
# the behaviour; the value is the machine's.
_VOLATILE_KEYS = frozenset({
    "ts", "at", "since", "created", "updated", "generated_at", "change_token",
    "pid", "elapsed_seconds", "start_monotonic", "mono", "armed_at",
    "deadline", "released_at", "started_at", "ended_at", "duration_seconds",
    "presence_id", "last_heartbeat", "elapsed", "runtime_seconds",
    "attempt_elapsed_seconds", "run_elapsed_seconds", "n", "tick",
    "started", "finished", "heartbeat_at", "expires_at", "mtime",
    "session_id", "claude_session_id", "age_seconds",
})
# Subtrees read from machine-level caches (quota/context/spend collectors);
# their *shape* is the worker's, their numbers are not.
_SHAPE_ONLY_KEYS = frozenset({"resources", "levels", "runner_catalog", "catalog"})


def _normalise(value: Any, roots: list[str], *, key: str = "") -> Any:
    if key in _VOLATILE_KEYS and value not in (None, "", [], {}):
        return "<V>"
    if key in _SHAPE_ONLY_KEYS:
        return _shape(value)
    if isinstance(value, dict):
        return {
            _normalise_str(str(k), roots): _normalise(v, roots, key=str(k))
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalise(v, roots) for v in value]
    if isinstance(value, set):
        return sorted(_normalise(v, roots) for v in value)
    if isinstance(value, Path):
        return _normalise_str(str(value), roots)
    if isinstance(value, str):
        return _normalise_str(value, roots)
    if isinstance(value, float):
        return "<F>"
    return value


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _shape(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        return ["<item>"] if value else []
    return type(value).__name__


def _normalise_str(text: str, roots: list[str]) -> str:
    for root in roots:
        text = text.replace(root, "<TMP>")
    text = _RUN_ID_RE.sub("<RUN>", text)
    text = _ISO_RE.sub("<TS>", text)
    text = _HEX_TOKEN_RE.sub("<HEX>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?s\b", "<N>s", text)
    return text


def _patch_runner(monkeypatch, *, fallback: Callable[..., Any] | None = None) -> None:
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda root, _overrides=None: daemon.runner.runner_profile("codex", root),
    )
    monkeypatch.setattr(
        daemon.runner, "fallback_runner_profile",
        fallback or (lambda *_a, **_k: None),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, _root, **kw: f"RUN {eid} -> {rp}",
    )
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)


def _result(invocation, runner_name, *, stdout="", stderr="", code=0, **kw) -> RunnerResult:
    return RunnerResult(
        invocation=invocation, runner_name=runner_name, command=["mock"],
        stdout=stdout, stderr=stderr, returncode=code, trace_dir=None,
        artifacts=kw.pop("artifacts", []), **kw,
    )


def _write_response(invocation, text: str) -> None:
    Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
    Path(invocation.response_path).write_text(text, encoding="utf-8")


# ── scenarios: each returns (event, max_retries, driver) ─────────────


def _scenario_close(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    _patch_runner(monkeypatch)
    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=succeed_invoke()))
    return make_event(tmp_path, eid="evt-close", body="ship it", telegram_chat_id=10), 0, daemon._run_worker


def _scenario_park_at_turn_end(tmp_path, monkeypatch):
    _patch_runner(monkeypatch)
    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=succeed_invoke()))
    return make_event(tmp_path, eid="evt-park", body="ship it", telegram_chat_id=11), 0, daemon._run_worker


def _scenario_artifact_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    _patch_runner(monkeypatch)

    def _invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        if invocation.label.endswith("attempt-1"):
            return _result(invocation, runner_name, artifacts=[
                RunnerArtifactRecord(path=Path("out.md"), label="out.md", exists=False),
            ])
        _write_response(invocation, "done\n")
        return _result(invocation, runner_name, stdout="done\n")

    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=_invoke))
    return make_event(tmp_path, eid="evt-retry", body="missing artifact", telegram_chat_id=20), 1, daemon._run_worker


def _scenario_transport_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    _patch_runner(monkeypatch)

    def _invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        if invocation.label.endswith("attempt-1"):
            return _result(
                invocation, runner_name, code=1,
                stdout="API Error: Connection closed mid-response. The response above may be incomplete.",
            )
        _write_response(invocation, "landed\n")
        return _result(invocation, runner_name, stdout="landed\n")

    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=_invoke))
    return make_event(tmp_path, eid="evt-transport", body="big", telegram_chat_id=21), 2, daemon._run_worker


def _scenario_hard_failure(tmp_path, monkeypatch):
    _patch_runner(monkeypatch)

    def _invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return _result(
            invocation, runner_name, code=124,
            stderr="OpenAI Codex v0.128.0\nthinking…\nrunner timed out after 3600s",
        )

    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=_invoke))
    return make_event(tmp_path, eid="evt-timeout", body="big task", telegram_chat_id=40), 3, daemon._run_worker


def _scenario_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    _patch_runner(
        monkeypatch,
        fallback=lambda _repo, _current, kind, *, tried=(), **_kw: (
            daemon.runner.runner_profile("claude", _repo) if kind == "quota_exhausted" else None
        ),
    )

    def _invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        if runner_name == "codex":
            return _result(invocation, runner_name, code=1, stderr="You've hit your session limit")
        _write_response(invocation, "done\n")
        return _result(invocation, runner_name, stdout="done\n")

    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=_invoke))
    return make_event(tmp_path, eid="evt-fallback", body="big task", telegram_chat_id=42), 0, daemon._run_worker


def _scenario_quota_hold(tmp_path, monkeypatch):
    _patch_runner(monkeypatch)

    def _invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        return _result(
            invocation, runner_name, code=1,
            stderr="codex task_complete error (usage limit exceeded): out of quota",
            codex_task_error={"kind": "usage_limit_exceeded", "message": "out of quota"},
        )

    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=_invoke))
    return make_event(tmp_path, eid="evt-quota", body="q", telegram_chat_id=50), 3, daemon._run_worker_and_finalize


def _scenario_trust_refused(tmp_path, monkeypatch):
    _patch_runner(monkeypatch)
    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=succeed_invoke()))
    return (
        make_event(tmp_path, eid="evt-untrusted", body="hi", source="github", trust_tier="untrusted"),
        0, daemon._run_worker,
    )


def _scenario_env_failure(tmp_path, monkeypatch):
    _patch_runner(monkeypatch)

    class _BrokenEnv(StubWorktreeEnv):
        def prepare(self, *_a, **_k):
            raise RuntimeError("worktree add failed")

    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: _BrokenEnv(invoke_fn=succeed_invoke()))
    return make_event(tmp_path, eid="evt-envfail", body="x", telegram_chat_id=60), 0, daemon._run_worker


SCENARIOS = {
    "close": _scenario_close,
    "park_at_turn_end": _scenario_park_at_turn_end,
    "artifact_retry": _scenario_artifact_retry,
    "transport_retry": _scenario_transport_retry,
    "hard_failure": _scenario_hard_failure,
    "fallback": _scenario_fallback,
    "quota_hold": _scenario_quota_hold,
    "trust_refused": _scenario_trust_refused,
    "env_failure": _scenario_env_failure,
}


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _capture(name: str, tmp_path: Path, monkeypatch, capsys) -> dict[str, Any]:
    write_repo_scaffold(tmp_path)
    event, max_retries, drive = SCENARIOS[name](tmp_path, monkeypatch)
    packets: list[dict[str, Any]] = []
    real_emit = daemon.updates.emit

    def _recording_emit(brr_dir, packet):
        packets.append({"type": packet.type, "payload": dict(packet.payload)})
        return real_emit(brr_dir, packet)

    monkeypatch.setattr(daemon.updates, "emit", _recording_emit)
    capsys.readouterr()
    task = drive(event, tmp_path, tmp_path / ".brr" / "responses", {}, max_retries)
    out = capsys.readouterr().out

    roots = sorted(
        {str(tmp_path), os.path.realpath(tmp_path), tmp_path.name}, key=len, reverse=True,
    )
    brr = tmp_path / ".brr"
    persisted = Run.from_file(brr / "runs" / task.id / "run.md")
    outbox = brr / "outbox" / event["id"]
    portal_path = outbox / "portal-state.json"
    portal = json.loads(portal_path.read_text(encoding="utf-8")) if portal_path.exists() else None
    shuttle_path = brr / "shuttle.json"
    shuttle_rows = (
        json.loads(shuttle_path.read_text(encoding="utf-8")) if shuttle_path.exists() else None
    )
    inbox_file = Path(event["_path"])
    captured = {
        "returned": {"status": task.status, "env": task.env, "body": task.body},
        "persisted": None if persisted is None else {
            "status": persisted.status,
            "meta": persisted.meta,
            "terminal_reply": getattr(persisted, "terminal_reply", None),
        },
        "event": {
            k: event.get(k) for k in ("status", "run_outcome", "run_id", "repo_label")
        },
        "inbox_file": inbox_file.read_text(encoding="utf-8") if inbox_file.exists() else None,
        "response": protocol.read_response(brr / "responses", event["id"]),
        "packets": packets,
        "portal": portal,
        "shuttle": shuttle_rows,
        "log": [line for line in out.splitlines() if line.startswith("[brnrd]")],
        "hold_active": resource_hold.is_active(task.meta.get("resource_hold")),
    }
    return _normalise(captured, roots)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_phased_worker_reproduces_main(name, tmp_path, monkeypatch, capsys):
    captured = _capture(name, tmp_path, monkeypatch, capsys)
    golden = GOLDEN_DIR / f"{name}.json"
    rendered = json.dumps(captured, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if WRITE:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(rendered, encoding="utf-8")
        pytest.skip(f"golden written: {golden.name}")
    assert golden.exists(), f"no golden for {name} — capture it from main first"
    expected = golden.read_text(encoding="utf-8")
    if rendered != expected and os.environ.get("BRR_WORKER_GOLDEN_DUMP"):
        Path(os.environ["BRR_WORKER_GOLDEN_DUMP"], f"{name}.json").write_text(rendered, encoding="utf-8")
    assert rendered == expected
