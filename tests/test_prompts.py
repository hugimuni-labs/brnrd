"""Tests for the prompt-assembly module."""

from brr.prompts import (
    _build_context_block,
    _read_recent_log,
    build_daemon_prompt,
    build_run_prompt,
    diffense_create_pr_enabled,
    diffense_emit_enabled,
)


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
        assert "Entry 13" in result
        assert "Entry 14" in result
        assert "Entry 15" in result
        assert "Entry 1\n" not in result

    def test_read_recent_log_byte_budget_stops_at_first_overflow(self, tmp_path):
        """A byte budget caps how much the conversation block can grow.

        Older entries are dropped (newest-first) once adding the next
        one would exceed the budget. The newest entry is always
        included so the most recent context never silently disappears,
        even if it alone exceeds the budget.
        """
        kb = tmp_path / "kb"
        kb.mkdir()
        bulk = "x" * 600
        entries = "\n\n".join(
            f"## [2026-04-{i:02d}] implement | Entry {i}\n\n{bulk}"
            for i in range(1, 6)
        )
        (kb / "log.md").write_text(f"# Log\n\n{entries}\n")
        # ~700 bytes per entry → ~1500-byte budget admits exactly 2.
        result = _read_recent_log(tmp_path, max_entries=10, max_bytes=1500)
        assert "Entry 5" in result
        assert "Entry 4" in result
        assert "Entry 3" not in result
        assert "Entry 1" not in result
        # Oldest of the included pair comes first (natural reading order).
        assert result.index("Entry 4") < result.index("Entry 5")

    def test_read_recent_log_byte_budget_keeps_newest_even_when_oversized(
        self, tmp_path,
    ):
        """When the single newest entry exceeds the budget, brr still
        includes it. Silent dropping of the most recent context would
        be worse than a slightly oversized prompt."""
        kb = tmp_path / "kb"
        kb.mkdir()
        huge = "x" * 5000
        (kb / "log.md").write_text(
            "# Log\n\n## [2026-05-01] implement | Big\n\n" + huge + "\n"
        )
        result = _read_recent_log(tmp_path, max_bytes=512)
        assert "Big" in result
        assert huge in result

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
    def test_run_prompt_includes_context(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "log.md").write_text(
            "# Log\n\n## [2026-04-08] fix | Bug fix\n\nFixed a bug.\n"
        )
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_run_prompt("do something", tmp_path)
        assert "Bug fix" in prompt
        assert "do something" in prompt

    def test_run_prompt_injects_kb_health_when_findings(self, tmp_path, monkeypatch):
        """A non-clean deterministic preflight rides into the wake prompt
        so the resident folds kb fixes into its own thought (replacing
        the retired post-task kb-maintenance spawn)."""
        from brr import kb_preflight

        prompts_dir = tmp_path / ".brr" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "run.md").write_text("You are an agent.")
        monkeypatch.setattr(
            kb_preflight, "scan",
            lambda _root: [
                kb_preflight.Finding(
                    type="missing-from-index",
                    target="kb/decision-orphan.md",
                    description="needs an index entry",
                ),
            ],
        )

        prompt = build_run_prompt("do something", tmp_path)
        assert "kb health (deterministic preflight)" in prompt
        assert "missing-from-index" in prompt
        assert "kb/decision-orphan.md" in prompt

    def test_run_prompt_omits_kb_health_when_clean(self, tmp_path, monkeypatch):
        """A clean preflight is silent — no wake-time tax."""
        from brr import kb_preflight

        prompts_dir = tmp_path / ".brr" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "run.md").write_text("You are an agent.")
        monkeypatch.setattr(kb_preflight, "scan", lambda _root: [])

        prompt = build_run_prompt("do something", tmp_path)
        assert "kb health" not in prompt

    def test_run_prompt_kb_health_disabled_with_never(self, tmp_path, monkeypatch):
        """``kb_maintenance=never`` opts out of the wake-time inject even
        when the preflight has findings."""
        from brr import config as conf
        from brr import kb_preflight

        prompts_dir = tmp_path / ".brr" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "run.md").write_text("You are an agent.")
        monkeypatch.setattr(
            kb_preflight, "scan",
            lambda _root: [
                kb_preflight.Finding(
                    type="broken-link", target="kb/x.md",
                    description="dangling reference",
                ),
            ],
        )
        monkeypatch.setattr(
            conf, "load_config", lambda _root: {"kb_maintenance": "never"},
        )

        prompt = build_run_prompt("do something", tmp_path)
        assert "kb health" not in prompt

    def test_diffense_emit_enabled_defaults_on(self):
        # On by default now that the publish kernel consumes the pack;
        # opt out explicitly.
        assert diffense_emit_enabled({})
        assert diffense_emit_enabled(None)
        assert diffense_emit_enabled({"diffense.emit_pack": True})
        assert not diffense_emit_enabled({"diffense.emit_pack": False})
        assert not diffense_emit_enabled({"diffense_emit_pack": False})

    def test_diffense_create_pr_enabled_defaults_on(self):
        assert diffense_create_pr_enabled({})
        assert diffense_create_pr_enabled(None)
        assert diffense_create_pr_enabled({"diffense.create_pr": True})
        assert not diffense_create_pr_enabled({"diffense.create_pr": False})
        assert not diffense_create_pr_enabled({"diffense_create_pr": False})

    def test_daemon_prompt_includes_diffense_pack_when_enabled(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-9",
            runtime_dir="/repo/.brr",
            diffense=True,
        )
        assert "Review pack (diffense)" in prompt
        assert "brr review --check" in prompt
        # The pack path is explicit and absolute in the shared runtime dir
        # so it survives worktree teardown.
        assert "Review pack path: /repo/.brr/diffense/task-9/pack.json" in prompt

    def test_daemon_prompt_omits_diffense_pack_when_not_requested(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-9",
            runtime_dir="/repo/.brr",
        )
        assert "Review pack (diffense)" not in prompt
        assert "Review pack path" not in prompt

    def test_daemon_prompt_includes_outbox_contract_when_given(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            task_id="task-9",
        )
        assert "/repo/.brr/outbox/evt-1" in prompt
        assert "mid-thought" in prompt
        # interim replies are framed as optional extras, not the final reply
        assert "optional" in prompt.lower()

    def test_daemon_prompt_omits_outbox_contract_without_path(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-9",
        )
        assert "mid-thought" not in prompt
        assert "outbox directory" not in prompt

    def test_daemon_prompt_lists_pending_events_and_fold_in_contract(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-A",
            task_id="task-A",
            pending_events=[
                {"id": "evt-B", "source": "telegram",
                 "summary": "quick question about X"},
            ],
        )
        assert "Inbox — other pending events" in prompt
        assert "evt-B" in prompt
        assert "quick question about X" in prompt
        # The fold-in contract names the frontmatter handle.
        assert "event: <id>" in prompt

    def test_daemon_prompt_omits_inbox_when_no_pending_events(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-A", task_id="task-A",
        )
        assert "other pending events" not in prompt

    def test_daemon_prompt_includes_branch_and_runtime_paths(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-123",
            source="telegram",
            environment="docker",
            branch_name="feat/task-abstraction",
            seed_ref="feat/task-abstraction",
            branch_source="event:target_branch",
            runtime_dir="/repo/.brr",
            context_path="/repo/.brr/runs/task-123/context.md",
        )
        assert "Task ID: task-123" in prompt
        assert f"Execution root: {tmp_path}" in prompt
        assert "Seed ref: feat/task-abstraction" in prompt
        assert "Current branch: feat/task-abstraction" in prompt
        assert "Shared runtime dir: /repo/.brr" in prompt
        assert "Run context file: /repo/.brr/runs/task-123/context.md" in prompt
        assert "brr captures stdout and stores it at /tmp/resp.md" in prompt
        assert "fix it" in prompt
        assert "kb/log-" not in prompt
        assert "gh pr create" not in prompt

    def test_daemon_prompt_includes_mode_block(self, tmp_path):
        """The Mode block names the stage, source, environment, and
        runtime-recovery surface so the runner can identify "where am
        I?" from the bundle alone without opening the run context file
        on every task."""
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-123",
            source="telegram",
            environment="docker",
            context_path="/repo/.brr/runs/task-123/context.md",
        )
        assert "### Mode" in prompt
        assert "Stage: brr daemon task" in prompt
        assert "Source: telegram" in prompt
        assert "Environment: docker" in prompt
        assert "Delivery: stdout captured by brr" in prompt
        # Runtime-recovery line points at the context file and frames it
        # as opt-in detail, not routine reading.
        assert (
            "Runtime recovery: /repo/.brr/runs/task-123/context.md"
            in prompt
        )
        assert "open only if" in prompt

    def test_daemon_prompt_mode_block_drops_missing_fields(self, tmp_path):
        """Source, environment, and runtime-recovery lines disappear
        when the daemon couldn't determine them. Stage and Delivery are
        always present because they're invariant for this builder."""
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "do thing", "evt-9", "/tmp/r.md", tmp_path,
            task_id="task-9",
        )
        assert "### Mode" in prompt
        assert "Stage: brr daemon task" in prompt
        assert "Delivery: stdout captured by brr" in prompt
        assert "Source:" not in prompt
        assert "Environment:" not in prompt
        assert "Runtime recovery:" not in prompt

    def test_daemon_prompt_describes_preserved_task_branch(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-123",
            branch_name="brr/task-123",
            seed_ref="main",
            branch_source="fallback:preserve",
            host_context_branch="feature/host",
        )

        assert "Seed ref: main" in prompt
        assert "Branch source: fallback:preserve" in prompt
        assert "Host context branch: feature/host" in prompt
        # No target branch → nudge the agent to rename the brr/<task-id>
        # placeholder to something descriptive.
        assert "rename the branch" in prompt
        assert "brr/<short-slug>" in prompt
        # The forge-locked `gh pr create` nudge is gone — brr now emits
        # a forge URL in the response card automatically, and PR
        # creation is forge-specific behaviour that doesn't belong in
        # the default prompt.
        assert "gh pr create" not in prompt

    def test_daemon_prompt_warns_against_local_paths_in_chat_reply(self, tmp_path):
        """The agent shouldn't tell the remote user to click on a
        worktree path that only exists on the host running brr.
        Telegram in particular doesn't render those as links and the
        user can't reach them anyway."""
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-123",
            branch_name="brr/task-123",
            seed_ref="main",
        )

        assert "remotely" in prompt
        assert "basename only" in prompt
        assert ".brr/worktrees/" in prompt  # cited as the bad pattern
        assert "forge-hosted branch URL" in prompt

    def test_daemon_prompt_with_recent_conversation(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        recent = [
            {
                "ts": "2026-05-05T20:00:00Z",
                "kind": "event",
                "event_id": "evt-prev",
                "source": "telegram",
                "summary": "earlier ping",
            },
            {
                "ts": "2026-05-05T20:00:05Z",
                "kind": "update",
                "type": "done",
                "task_id": "task-prev",
            },
        ]

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-123",
            branch_name="brr/task-123",
            seed_ref="feat/task",
            runtime_dir="/repo/.brr",
            recent_conversation=recent,
            event_body="please fix the login flow",
        )
        assert "Task Context Bundle" in prompt
        assert "Recent in this conversation" in prompt
        assert "earlier ping" in prompt
        assert "task-prev" in prompt
        assert "update done" in prompt
        assert "Original event body" in prompt
        assert "please fix the login flow" in prompt
        assert "Task ID: task-123" in prompt
        assert f"Execution root: {tmp_path}" in prompt
        assert "Seed ref: feat/task" in prompt
        assert "Workstream" not in prompt
        assert "Triage" not in prompt

    def test_daemon_prompt_does_not_repeat_identical_event_body(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        body = "long telegram request"
        prompt = build_daemon_prompt(
            body, "evt-1", "/tmp/resp.md", tmp_path,
            task_id="task-123",
            event_body=body,
        )

        assert "Original event body" in prompt
        assert body in prompt
        assert f"Task: {body}" not in prompt

    def test_daemon_prompt_without_recent_conversation(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "do thing", "evt-9", "/tmp/r.md", tmp_path,
            task_id="task-9",
        )
        assert "Workstream" not in prompt
        assert "Recent in this conversation" not in prompt
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


# ── Phase 3 guardrails: revisit-signal handling ──────────────────────


def _read_bundled_run_prompt() -> str:
    """Read the bundled prompt directly so we pin its shipped content."""
    from pathlib import Path

    import brr

    return (Path(brr.__file__).parent / "prompts" / "run.md").read_text(
        encoding="utf-8",
    )


def _read_bundled_agents_md() -> str:
    from pathlib import Path

    import brr

    return (Path(brr.__file__).parent / "AGENTS.md").read_text(encoding="utf-8")


class TestRevisitSignalGuardrails:
    """Pin the prompt + AGENTS.md guidance that prevents path-of-least-
    resistance shipping on design-loaded tasks. See
    `kb/design-git-layer-rework.md` Phase 3 for the rationale."""

    def test_run_prompt_mentions_revisit_signals(self):
        prompt = _read_bundled_run_prompt()
        # Section header that gates the guidance.
        assert "When the task asks you to reconsider" in prompt
        # The trigger is ownership intent, not a brittle keyword list:
        # the stance lives in the resident playbook and AGENTS.md →
        # Stewardship, which this section leans on instead of
        # re-enumerating trigger phrases.
        assert "engage with the substance" in prompt
        assert "ownership stance" in prompt

    def test_run_prompt_authorizes_no_commit_for_revisit(self):
        prompt = _read_bundled_run_prompt()
        # The chat-only-reply outcome must be named explicitly so the
        # diff-as-receipt rule doesn't override it on revisit tasks.
        assert "chat-only reply" in prompt
        assert "complete and successful task" in prompt
        assert "Stewardship" in prompt

    def test_agents_md_self_review_contains_contradiction_check(self):
        agents = _read_bundled_agents_md()
        # The new self-review bullet must reference the Stewardship
        # section it maps onto so the link between checklist and
        # principle stays explicit.
        assert "did you surface it before resolving it" in agents
        assert "Stewardship" in agents


class TestDaemonModeGuardrails:
    """Pin the run.md changes that route daemon runners through the
    Task Context Bundle's Mode block and treat the run context file as
    recovery detail rather than routine reading.  See
    `kb/research-cursor-orientation-ergonomics-2026-05-16.md` and
    `kb/plan-agent-orientation-layering.md`."""

    def test_run_prompt_names_mode_block_and_recovery_role(self):
        prompt = _read_bundled_run_prompt()
        # The bundle's Mode section is the authoritative "where am I?".
        assert "Mode" in prompt
        # Injected Recent Activity counts toward the kb/log.md step so
        # daemon runs don't re-read the log when the prompt already
        # carries an extract. Checked as separate anchors so the
        # paragraph can rewrap without breaking the guardrail.
        assert "Recent Activity (from kb/log.md)" in prompt
        assert "satisfies" in prompt
        assert "kb/log.md startup step" in prompt
        # The run context file is recovery detail, not routine reading.
        assert "Runtime recovery" in prompt
        assert "recovery detail" in prompt
