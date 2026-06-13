"""Tests for the runner module — subprocess plumbing only.

Prompt-assembly tests live in ``tests/test_prompts.py``.
"""

import json
import subprocess
import sys

from brr import runner as runner_mod
from brr.runner import (
    DEFAULT_RUNNER_TIMEOUT,
    RunnerArtifactSpec,
    RunnerInvocation,
    RunnerResult,
    _build_cmd,
    detect_runner,
    invoke_runner,
    runner_timeout,
)


def test_detect_runner_returns_string_or_none():
    result = detect_runner()
    assert result is None or isinstance(result, str)


class TestCommandBuilding:
    def test_build_cmd_codex_headless(self):
        cmd = _build_cmd("codex", "fix it", {})
        assert cmd == [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "-c",
            "base_instructions=You are brr agent. Find your orientation in AGENTS.md",
            "-c",
            "include_permissions_instructions=false",
            "-c",
            "include_apps_instructions=false",
            "-c",
            "include_collaboration_mode_instructions=false",
            "-c",
            "include_skill_instructions=false",
            "fix it",
        ]

    def test_build_cmd_claude_headless(self):
        cmd = _build_cmd("claude", "fix it", {})
        assert cmd == [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "fix it",
        ]

    def test_build_cmd_gemini_headless_uses_yolo(self):
        cmd = _build_cmd("gemini", "fix it", {})
        assert cmd == [
            "gemini",
            "-p",
            "--yolo",
            "fix it",
        ]

    def test_build_cmd_runner_cmd_override_substitutes_prompt_only(self):
        cfg = {"runner_cmd": ["mock", "--flag", "{prompt}"]}
        cmd = _build_cmd("codex", "do work", cfg)
        assert cmd == ["mock", "--flag", "do work"]


class TestInvocationTracing:
    def test_invoke_runner_writes_stdout_to_response_path(self, tmp_path):
        repo_root = tmp_path
        (repo_root / ".brr").mkdir()
        response_path = repo_root / ".brr" / "responses" / "evt-1.md"
        cfg = {
            "runner_cmd": [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('final reply\\n')",
                "{prompt}",
            ]
        }
        invocation = RunnerInvocation(
            kind="daemon-run",
            label="evt-1-attempt-1",
            prompt="capture this",
            cwd=repo_root,
            repo_root=repo_root,
            response_path=str(response_path),
        )

        result = invoke_runner("mock-runner", invocation, cfg=cfg)

        assert result.ok
        assert result.validation_ok
        assert result.has_response
        assert response_path.read_text(encoding="utf-8") == "final reply\n"

    def test_invoke_runner_skips_response_write_when_no_path(self, tmp_path):
        repo_root = tmp_path
        cfg = {
            "runner_cmd": [
                sys.executable, "-c", "print('hi')", "{prompt}",
            ]
        }
        invocation = RunnerInvocation(
            kind="executor",
            label="adhoc",
            prompt="ignored",
            cwd=repo_root,
            repo_root=repo_root,
        )

        result = invoke_runner("mock-runner", invocation, cfg=cfg)

        assert result.ok
        assert result.validation_ok
        assert not list(repo_root.glob("**/responses/*.md"))

    def test_invoke_runner_reports_empty_stdout_as_missing_response(self, tmp_path):
        repo_root = tmp_path
        (repo_root / ".brr").mkdir()
        response_path = repo_root / ".brr" / "responses" / "evt-2.md"
        cfg = {
            "runner_cmd": [
                sys.executable, "-c", "import sys; sys.stderr.write('progress only\\n')", "{prompt}",
            ]
        }
        invocation = RunnerInvocation(
            kind="daemon-run",
            label="evt-2-attempt-1",
            prompt="empty reply",
            cwd=repo_root,
            repo_root=repo_root,
            response_path=str(response_path),
        )

        result = invoke_runner("mock-runner", invocation, cfg=cfg)

        assert result.ok
        assert not result.validation_ok
        assert result.retry_reason() == "runner produced no response on stdout"
        assert not response_path.exists()

    def test_invoke_runner_persists_trace(self, tmp_path):
        repo_root = tmp_path
        (repo_root / ".brr").mkdir()
        response_path = repo_root / ".brr" / "responses" / "evt-3.md"
        prompt = "trace this prompt"
        cfg = {
            "runner_cmd": [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('runner stdout\\n'); sys.stdout.write(sys.argv[1])",
                "{prompt}",
            ]
        }
        invocation = RunnerInvocation(
            kind="daemon-run",
            label="evt-3-attempt-1",
            prompt=prompt,
            cwd=repo_root,
            repo_root=repo_root,
            response_path=str(response_path),
        )

        result = invoke_runner("mock-runner", invocation, cfg=cfg, trace=True)

        assert result.ok
        assert result.validation_ok
        assert response_path.read_text(encoding="utf-8") == result.stdout
        assert result.trace_dir is not None
        assert (result.trace_dir / "prompt.md").read_text(encoding="utf-8") == prompt
        assert (result.trace_dir / "stdout.txt").read_text(encoding="utf-8") == result.stdout
        meta = json.loads((result.trace_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["kind"] == "daemon-run"
        assert meta["validation_ok"] is True
        assert meta["response_path"] == str(response_path)

    def test_invoke_runner_reports_missing_required_artifacts(self, tmp_path):
        repo_root = tmp_path
        (repo_root / ".brr").mkdir()
        missing = repo_root / ".brr" / "outputs" / "expected.md"
        cfg = {
            "runner_cmd": [sys.executable, "-c", "print('no artifact created')", "{prompt}"]
        }
        invocation = RunnerInvocation(
            kind="adopt",
            label="setup",
            prompt="missing output",
            cwd=repo_root,
            repo_root=repo_root,
            required_artifacts=[RunnerArtifactSpec(missing, "expected.md")],
        )

        result = invoke_runner("mock-runner", invocation, cfg=cfg)

        assert result.ok
        assert not result.validation_ok
        assert result.retry_reason() == "missing required output(s): expected.md"
        assert result.missing_artifacts[0].path == missing


class TestTimeoutConfig:
    def test_runner_timeout_defaults_to_one_hour(self):
        assert runner_timeout(None) == DEFAULT_RUNNER_TIMEOUT == 3600

    def test_runner_timeout_reads_dotted_config_key(self):
        assert runner_timeout({"runner.timeout_seconds": 120}) == 120

    def test_runner_timeout_accepts_string_int(self):
        assert runner_timeout({"runner.timeout_seconds": "7200"}) == 7200

    def test_runner_timeout_rejects_garbage(self):
        assert runner_timeout({"runner.timeout_seconds": "soon"}) == DEFAULT_RUNNER_TIMEOUT

    def test_runner_timeout_rejects_non_positive(self):
        assert runner_timeout({"runner.timeout_seconds": 0}) == DEFAULT_RUNNER_TIMEOUT
        assert runner_timeout({"runner.timeout_seconds": -5}) == DEFAULT_RUNNER_TIMEOUT

    def test_invoke_runner_passes_configured_timeout_to_communicate(
        self, tmp_path, monkeypatch,
    ):
        """The configured timeout must flow into ``proc.communicate`` so
        long-reasoning models can finish; the historical hardcoded 600s
        was killing live work mid-run."""
        captured: dict[str, object] = {}

        class _FakeProc:
            returncode = 0

            def communicate(self, timeout=None):
                captured["timeout"] = timeout
                return ("ok\n", "")

            def kill(self):  # pragma: no cover - not exercised here
                pass

        def _fake_popen(*_args, **kwargs):
            captured["stdin"] = kwargs.get("stdin")
            return _FakeProc()

        monkeypatch.setattr(runner_mod.subprocess, "Popen", _fake_popen)
        invocation = RunnerInvocation(
            kind="executor",
            label="cfg-timeout",
            prompt="hi",
            cwd=tmp_path,
            repo_root=tmp_path,
        )
        result = invoke_runner(
            "mock", invocation, cfg={"runner.timeout_seconds": 2400},
        )

        assert result.ok
        assert captured["timeout"] == 2400
        # stdin must be muted so codex's "Reading additional input from
        # stdin..." path sees an immediate EOF rather than hanging on an
        # open-but-silent fd inherited from the daemon's terminal.
        assert captured["stdin"] == subprocess.DEVNULL

    def test_invoke_runner_timeout_message_uses_configured_value(
        self, tmp_path, monkeypatch,
    ):
        """The appended stderr line must report the actual configured
        ceiling — operators reading the failed packet need to know what
        the budget was, not a stale hardcoded number."""
        class _Proc:
            returncode = -9

            def __init__(self) -> None:
                self._raised = False

            def communicate(self, timeout=None):
                if not self._raised:
                    self._raised = True
                    raise subprocess.TimeoutExpired(cmd=["mock"], timeout=timeout)
                return ("", "partial stderr")

            def kill(self):
                pass

        monkeypatch.setattr(runner_mod.subprocess, "Popen", lambda *a, **k: _Proc())
        invocation = RunnerInvocation(
            kind="executor",
            label="cfg-timeout-msg",
            prompt="hi",
            cwd=tmp_path,
            repo_root=tmp_path,
        )
        result = invoke_runner(
            "mock", invocation, cfg={"runner.timeout_seconds": 42},
        )
        assert result.returncode == 124
        assert "runner timed out after 42s" in result.stderr


class TestRetryReason:
    def _result(self, *, returncode: int, stdout: str, response_path: str) -> RunnerResult:
        invocation = RunnerInvocation(
            kind="daemon-run",
            label="x",
            prompt="p",
            cwd=None,
            repo_root=None,  # type: ignore[arg-type]
            response_path=response_path,
        )
        return RunnerResult(
            invocation=invocation,
            runner_name="mock",
            command=["mock"],
            stdout=stdout,
            stderr="some failure tail",
            returncode=returncode,
            trace_dir=None,
            artifacts=[],
        )

    def test_retry_reason_none_on_hard_failure(self, tmp_path):
        """Non-zero exit (timeout, crash) is not retryable: the daemon
        would just pay for another expensive attempt that fails the same
        way. The give-up branch bubbles the captured error instead."""
        result = self._result(
            returncode=124,
            stdout="",
            response_path=str(tmp_path / "r.md"),
        )
        assert result.retry_reason() is None
        assert result.error_detail() == "some failure tail"

    def test_retry_reason_still_set_on_clean_empty_stdout(self, tmp_path):
        """A clean exit with no stdout is the original retry-worthy
        case — the runner ran fine but forgot to print the final reply."""
        result = self._result(
            returncode=0,
            stdout="",
            response_path=str(tmp_path / "r.md"),
        )
        assert result.retry_reason() == "runner produced no response on stdout"


class TestKillActive:
    """kill_active is the cross-thread handle the daemon's heartbeat and
    shutdown use to reclaim the single-flight slot."""

    def test_kills_live_process_then_noop(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        with runner_mod._proc_lock:
            runner_mod._active_proc = proc
        try:
            assert runner_mod.kill_active() is True
            proc.wait(timeout=5)
            assert proc.returncode != 0
            # Already dead: nothing live to signal.
            assert runner_mod.kill_active() is False
        finally:
            with runner_mod._proc_lock:
                runner_mod._active_proc = None
            if proc.poll() is None:
                proc.kill()

    def test_noop_when_idle(self):
        with runner_mod._proc_lock:
            runner_mod._active_proc = None
        assert runner_mod.kill_active() is False

    def test_invocation_timeout_seconds_overrides_cfg_default(self):
        # The daemon passes a generous hard cap here; cfg's
        # runner.timeout_seconds is the fallback only when unset.
        inv = RunnerInvocation(
            kind="daemon-run", label="x", prompt="p", repo_root=None,
            timeout_seconds=99,
        )
        assert inv.timeout_seconds == 99
        assert RunnerInvocation(
            kind="daemon-run", label="x", prompt="p", repo_root=None,
        ).timeout_seconds is None
