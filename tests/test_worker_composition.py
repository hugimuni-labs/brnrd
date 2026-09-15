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

import brr.worker as worker
from brr import daemon, prompts, protocol, resource_hold, transcript
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
# Subtrees assembled from the *host* — which Shells and cores this machine
# has, its quota/context/spend caches. Neither their numbers nor their shape is
# the worker's: a CI runner without a stronger local core drops
# `resources.runner.quality_escalation` entirely (caught on the first CI run
# of this file). Opaque, and the probes that feed the worker are pinned below.
_HOST_OPAQUE_KEYS = frozenset({"resources", "levels", "runner_catalog", "catalog"})


def _normalise(value: Any, roots: list[str], *, key: str = "") -> Any:
    if key in _VOLATILE_KEYS and value not in (None, "", [], {}):
        return "<V>"
    if key in _HOST_OPAQUE_KEYS:
        return f"<{key}>" if value not in (None, "", [], {}) else value
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
    # Host capability probes the worker reads — pinned so the capture is the
    # worker's behaviour, not this machine's inventory of Shells.
    monkeypatch.setattr(daemon, "_quality_escalation_meta", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon.runner, "available_runner_catalog", lambda *_a, **_k: [])


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


def _scenario_transport_then_exhausted(tmp_path, monkeypatch):
    """Attempt 1 drops mid-response (a recorded failure, retried); attempt 2
    exits clean without its artifact and the budget is spent. The ending
    attempt failed differently from the first — the reading ``finalize``
    names is the one move 3b pins."""
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    _patch_runner(monkeypatch)

    def _invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        if invocation.label.endswith("attempt-1"):
            return _result(
                invocation, runner_name, code=1,
                stdout="API Error: Connection closed mid-response. The response above may be incomplete.",
            )
        return _result(invocation, runner_name, artifacts=[
            RunnerArtifactRecord(path=Path("out.md"), label="out.md", exists=False),
        ])

    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: StubWorktreeEnv(invoke_fn=_invoke))
    return make_event(tmp_path, eid="evt-transport-exhausted", body="big", telegram_chat_id=22), 1, daemon._run_worker


def _scenario_mounted_retry(tmp_path, monkeypatch):
    """A mounted Shell (claude, ``boot.mount`` on) that misses its artifact
    twice: three attempts, so attempt 3's argv shows how many resume argvs
    the lane carries by then."""
    monkeypatch.setattr(daemon, "SEAT_PARK_ON_TURN_END_DEFAULT", False)
    _patch_runner(monkeypatch)
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda root, _overrides=None: daemon.runner.runner_profile("claude", root),
    )
    mounts: list[str] = []

    def _mount(*_a, **_k):
        mounts.append(f"mount-{len(mounts) + 1}")
        return mounts[-1]

    monkeypatch.setattr(transcript, "mount_claude_session", _mount)
    # The scaffold renders no mountable block, so the sink would stay empty and
    # ``dispatch`` would never reach the prepend. One stand-in entry, only when
    # the builder was handed a sink — the prepend itself is ``dispatch``'s.
    real_build = prompts.build_daemon_prompt_with_score

    def _build(*a, _mount_sink=None, **kw):
        built = real_build(*a, _mount_sink=_mount_sink, **kw)
        if _mount_sink is not None:
            _mount_sink.setdefault("identity-core", "mounted")
        return built

    monkeypatch.setattr(prompts, "build_daemon_prompt_with_score", _build)

    def _invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        if not invocation.label.endswith("attempt-3"):
            return _result(invocation, runner_name, artifacts=[
                RunnerArtifactRecord(path=Path("out.md"), label="out.md", exists=False),
            ])
        _write_response(invocation, "done\n")
        return _result(invocation, runner_name, stdout="done\n")

    class _MountingEnv(StubWorktreeEnv):
        def session_seed_home(self, _ctx):
            return None

    monkeypatch.setattr(daemon.envs, "get_env", lambda _n: _MountingEnv(invoke_fn=_invoke))
    return make_event(tmp_path, eid="evt-mounted-retry", body="missing artifact", telegram_chat_id=23), 2, daemon._run_worker


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
    "transport_then_exhausted": _scenario_transport_then_exhausted,
    "mounted_retry": _scenario_mounted_retry,
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
    # Move 3b: the loop state no packet shows. What each attempt handed the
    # Shell (``extra_runner_args``), and the ``last_failure`` every boundary
    # carried — onward in ``next_attempt`` and into ``finalize``.
    attempts: list[dict[str, Any]] = []
    real_invoke = StubWorktreeEnv.invoke

    def _recording_invoke(self, ctx, runner_name, invocation, cfg=None, *, trace=False):
        attempts.append({
            "attempt": invocation.label.rsplit("-attempt-", 1)[-1],
            "runner": runner_name,
            "extra_runner_args": list(invocation.extra_runner_args or []),
        })
        return real_invoke(self, ctx, runner_name, invocation, cfg, trace=trace)

    monkeypatch.setattr(StubWorktreeEnv, "invoke", _recording_invoke)
    boundaries: list[dict[str, Any]] = []
    real_boundary, real_finalize = worker.boundary, worker.finalize

    def _recording_boundary(p, s):
        b = real_boundary(p, s)
        boundaries.append({
            "kind": b.kind,
            "attempt": b.attempt.n,
            "last_failure": b.attempt.last_failure,
            "next_last_failure": (
                b.next_attempt.last_failure if b.next_attempt is not None else None
            ),
        })
        return b

    def _recording_finalize(p, b):
        boundaries.append({
            "finalize": b.kind, "attempt": b.attempt.n, "last_failure": b.attempt.last_failure,
        })
        return real_finalize(p, b)

    monkeypatch.setattr(worker, "boundary", _recording_boundary)
    monkeypatch.setattr(worker, "finalize", _recording_finalize)
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
    if isinstance(portal, dict) and isinstance(portal.get("produce"), dict):
        # Move 5 (the HUD is one shape) adds one key to the portal on purpose:
        # `produce.ledger`, the loom's four kinds. These goldens predate it, so
        # it is taken out here — asserted present, never silently ignored — and
        # pinned on its own in test_hud.py.
        assert isinstance(portal["produce"].pop("ledger", None), dict), "produce.ledger missing"
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
        "attempts": attempts,
        "boundaries": boundaries,
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
