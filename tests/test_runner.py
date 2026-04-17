"""Tests for runner module."""

import json
import sys

from brr.runner import (
    detect_runner,
    _build_cmd,
    _read_recent_log,
    _build_context_block,
    build_run_prompt,
    build_daemon_prompt,
    build_triage_prompt,
    RunnerArtifactSpec,
    RunnerInvocation,
    invoke_runner,
)


def test_detect_runner_returns_string_or_none():
    result = detect_runner()
    assert result is None or isinstance(result, str)


class TestContextInjection:
    def test_read_recent_log_missing(self, tmp_path):
        assert _read_recent_log(tmp_path) == ""

    def test_read_recent_log_basic(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "log.md").write_text(
            "# Activity Log\n\n"
            "## [2026-04-07] implement | Setup\n\nDid setup.\n\n"
            "## [2026-04-08] plan | Design\n\nDesigned stuff.\n"
        )
        result = _read_recent_log(tmp_path)
        assert "## [2026-04-07]" in result
        assert "## [2026-04-08]" in result

    def test_read_recent_log_truncates(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        entries = "\n\n".join(
            f"## [2026-04-{i:02d}] implement | Entry {i}\n\nDid thing {i}."
            for i in range(1, 16)
        )
        (kb / "log.md").write_text(f"# Log\n\n{entries}\n")
        result = _read_recent_log(tmp_path, max_entries=3)
        # Should only have the last 3
        assert "Entry 13" in result
        assert "Entry 14" in result
        assert "Entry 15" in result
        assert "Entry 1\n" not in result

    def test_context_block_empty(self, tmp_path):
        assert _build_context_block(tmp_path) == ""

    def test_context_block_with_log(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "log.md").write_text(
            "# Log\n\n## [2026-04-08] plan | Test\n\nTest entry.\n"
        )
        block = _build_context_block(tmp_path)
        assert "Recent Activity" in block
        assert "## [2026-04-08]" in block


class TestPromptBuilding:
    def test_build_cmd_uses_noninteractive_codex_exec(self):
        cmd = _build_cmd("codex", "fix it", {})
        assert cmd == ["codex", "exec", "--full-auto", "fix it"]

    def test_build_cmd_codex_auto_approve_adds_bypass_and_output_path(self):
        cmd = _build_cmd(
            "codex", "fix it", {"auto_approve": True}, response_path="/tmp/resp.md",
        )
        assert cmd == [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--output-last-message",
            "/tmp/resp.md",
            "fix it",
        ]

    def test_run_prompt_includes_context(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "log.md").write_text(
            "# Log\n\n## [2026-04-08] fix | Bug fix\n\nFixed a bug.\n"
        )
        # Write run.md prompt
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_run_prompt("do something", tmp_path)
        assert "Bug fix" in prompt
        assert "do something" in prompt

    def test_daemon_prompt_with_log_file(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-123",
            branch_name="brr/task-123",
            runtime_dir="/repo/.brr",
            log_file="kb/log-task-123.md",
        )
        assert "Task ID: task-123" in prompt
        assert f"Execution root: {tmp_path}" in prompt
        assert "Current branch: brr/task-123" in prompt
        assert "Shared runtime dir: /repo/.brr" in prompt
        assert "kb/log-task-123.md" in prompt
        assert "Some runners capture your final response automatically" in prompt
        assert "fix it" in prompt

    def test_triage_prompt(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "triage.md").write_text("You are a triage agent.")

        prompt = build_triage_prompt("add logging", "evt-1", tmp_path)
        assert "triage agent" in prompt
        assert "add logging" in prompt

    def test_triage_prompt_uses_reduced_context(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "triage.md").write_text("You are a triage agent.")
        kb = tmp_path / "kb"
        kb.mkdir()
        entries = "\n\n".join(
            f"## [2026-04-{i:02d}] implement | Entry {i}\n\nDid thing {i}."
            for i in range(1, 11)
        )
        (kb / "log.md").write_text(f"# Log\n\n{entries}\n")

        prompt = build_triage_prompt("add logging", "evt-1", tmp_path)
        assert "Entry 10" in prompt
        assert "Entry 9" in prompt
        assert "Entry 8" in prompt
        assert "Entry 7" not in prompt
        assert "last 3 entries" in prompt


class TestInvocationTracing:
    def test_invoke_runner_persists_trace_and_artifact_copy(self, tmp_path):
        repo_root = tmp_path
        (repo_root / ".brr").mkdir()
        produced = repo_root / ".brr" / "responses" / "evt-1.md"
        produced.parent.mkdir(parents=True)
        prompt = "trace this prompt"
        cfg = {
            "runner_cmd": [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "Path(sys.argv[2]).write_text('saved output\\n', encoding='utf-8'); "
                    "print('runner stdout'); "
                    "print(sys.argv[1])"
                ),
                "{prompt}",
                "{response_path}",
            ]
        }
        invocation = RunnerInvocation(
            kind="daemon-run",
            label="evt-1-attempt-1",
            prompt=prompt,
            cwd=repo_root,
            repo_root=repo_root,
            response_path=str(produced),
            required_artifacts=[RunnerArtifactSpec(produced, "response:evt-1")],
        )

        result = invoke_runner("mock-runner", invocation, cfg=cfg, trace=True)

        assert result.ok
        assert result.validation_ok
        assert result.output == f"runner stdout\n{prompt}\n"
        assert result.trace_dir is not None
        assert (result.trace_dir / "prompt.md").read_text(encoding="utf-8") == prompt
        assert (result.trace_dir / "stdout.txt").read_text(encoding="utf-8") == result.output
        meta = json.loads((result.trace_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["kind"] == "daemon-run"
        assert meta["validation_ok"] is True
        assert meta["artifacts"][0]["label"] == "response:evt-1"
        artifact_copy = result.artifacts[0].trace_copy
        assert artifact_copy is not None
        assert artifact_copy.read_text(encoding="utf-8") == "saved output\n"

    def test_invoke_runner_reports_missing_required_artifacts(self, tmp_path):
        repo_root = tmp_path
        (repo_root / ".brr").mkdir()
        missing = repo_root / ".brr" / "responses" / "evt-2.md"
        cfg = {
            "runner_cmd": [sys.executable, "-c", "print('no artifact created')", "{prompt}"]
        }
        invocation = RunnerInvocation(
            kind="daemon-run",
            label="evt-2-attempt-1",
            prompt="missing output",
            cwd=repo_root,
            repo_root=repo_root,
            required_artifacts=[RunnerArtifactSpec(missing, "response:evt-2")],
        )

        result = invoke_runner("mock-runner", invocation, cfg=cfg)

        assert result.ok
        assert not result.validation_ok
        assert result.retry_reason() == "missing required output(s): response:evt-2"
        assert result.missing_artifacts[0].path == missing
