"""Tests for the runner module — subprocess plumbing only.

Prompt-assembly tests live in ``tests/test_prompts.py``.
"""

import json
import sys

from brr.runner import (
    detect_runner,
    _build_cmd,
    RunnerArtifactSpec,
    RunnerInvocation,
    invoke_runner,
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
