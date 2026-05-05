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
            base_branch="feat/task-abstraction",
            runtime_dir="/repo/.brr",
            context_path="/repo/.brr/runs/task-123/context.md",
            log_file="kb/log-task-123.md",
        )
        assert "Task ID: task-123" in prompt
        assert f"Execution root: {tmp_path}" in prompt
        assert "Base branch: feat/task-abstraction" in prompt
        assert "Current branch: brr/task-123" in prompt
        assert "do not rebase or retarget to main" in prompt
        assert "Shared runtime dir: /repo/.brr" in prompt
        assert "Run context file: /repo/.brr/runs/task-123/context.md" in prompt
        assert "kb/log-task-123.md" in prompt
        assert "brr captures stdout and stores it at /tmp/resp.md" in prompt
        assert "fix it" in prompt

    def test_daemon_prompt_with_stream_context_bundle(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        from brr.stream import StreamManifest

        stream = StreamManifest(
            id="stream-abc",
            title="Refactor auth flow",
            status="active",
            intent="Make login testable",
            summary="Found offending coupling",
            open_questions="Keep cookie fallback?",
            gate_context={"source": "telegram", "telegram_chat_id": 42},
            reply_route={
                "preferred": "input_gate",
                "selected": "input_gate",
                "allowed": ["input_gate", "git_pr"],
            },
        )

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-123",
            branch_name="brr/task-123",
            base_branch="feat/task",
            runtime_dir="/repo/.brr",
            stream=stream,
            event_body="please fix the login flow",
            stage_feedback=True,
        )
        assert "Task Context Bundle" in prompt
        assert "Stream ID: stream-abc" in prompt
        assert "Refactor auth flow" in prompt
        assert "Make login testable" in prompt
        assert "Found offending coupling" in prompt
        assert "Keep cookie fallback" in prompt
        assert "telegram_chat_id=42" in prompt
        assert "preferred=input_gate" in prompt
        assert "Stage feedback requested: yes" in prompt
        assert "Original event body" in prompt
        assert "please fix the login flow" in prompt
        # Backwards-compatible substrings preserved.
        assert "Task ID: task-123" in prompt
        assert f"Execution root: {tmp_path}" in prompt
        assert "Base branch: feat/task" in prompt

    def test_daemon_prompt_without_stream_context(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "do thing", "evt-9", "/tmp/r.md", tmp_path,
            task_id="task-9",
        )
        assert "Workstream" not in prompt
        assert "Stage feedback requested: no" in prompt
        assert "Original event body" not in prompt

    def test_bundled_daemon_prompt_is_command_free(self, tmp_path):
        prompt = build_daemon_prompt(
            "do thing",
            "evt-9",
            "/tmp/r.md",
            tmp_path,
            task_id="task-9",
            context_path="/repo/.brr/runs/task-9/context.md",
        )
        assert "Run context file: /repo/.brr/runs/task-9/context.md" in prompt
        assert "brr inspect" not in prompt
        assert "brr stream" not in prompt
        assert "brr docs" not in prompt

    def test_triage_prompt(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "triage.md").write_text("You are a triage agent.")

        prompt = build_triage_prompt("add logging", "evt-1", tmp_path)
        assert "triage agent" in prompt
        assert "add logging" in prompt

    def test_triage_prompt_with_stream_and_stage_feedback(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "triage.md").write_text("You are a triage agent.")

        from brr.stream import StreamManifest

        stream = StreamManifest(
            id="stream-xyz",
            title="Auth rework",
            intent="Plan migration",
        )
        prompt = build_triage_prompt(
            "kick off", "evt-2", tmp_path,
            stream=stream,
            stage_feedback=True,
        )
        assert "Workstream" in prompt
        assert "stream-xyz" in prompt
        assert "Auth rework" in prompt
        assert "Plan migration" in prompt
        assert "Stage feedback requested" in prompt

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
