"""Tests for the prompt-assembly module."""

from brr import dominion
from brr.prompts import (
    _build_context_block,
    _read_recent_log,
    build_daemon_prompt,
    build_run_prompt,
    diffense_create_pr_enabled,
    diffense_emit_enabled,
)


def _seed_pitfalls(repo_root, text: str) -> None:
    """Materialize a dominion dir with a ``pitfalls.md`` for prompt tests."""
    dom = dominion.dominion_path(repo_root)
    dom.mkdir(parents=True, exist_ok=True)
    (dom / "pitfalls.md").write_text(text, encoding="utf-8")


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

    def test_diffense_emit_enabled_defaults_off(self):
        # Off by default so routine or chat-only wakes do not pay the prompt
        # and review-pack tax; opt in explicitly when the surface is wanted.
        assert not diffense_emit_enabled({})
        assert not diffense_emit_enabled(None)
        assert diffense_emit_enabled({"diffense.emit_pack": True})
        assert not diffense_emit_enabled({"diffense.emit_pack": False})
        assert diffense_emit_enabled({"diffense_emit_pack": True})
        assert not diffense_emit_enabled({"diffense_emit_pack": False})

    def test_diffense_create_pr_enabled_defaults_off(self):
        assert not diffense_create_pr_enabled({})
        assert not diffense_create_pr_enabled(None)
        assert diffense_create_pr_enabled({"diffense.create_pr": True})
        assert not diffense_create_pr_enabled({"diffense.create_pr": False})
        assert diffense_create_pr_enabled({"diffense_create_pr": True})
        assert not diffense_create_pr_enabled({"diffense_create_pr": False})

    def test_daemon_prompt_includes_diffense_pack_when_enabled(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runtime_dir="/repo/.brr",
            diffense=True,
        )
        assert "Review pack (diffense)" in prompt
        assert "brr review --check" in prompt
        # The heavy publish plumbing is now inspected, not injected: the
        # block points at `brr docs review-pack` instead of re-narrating
        # the relay/gist/frontmatter procedure every diffense wake.
        assert "brr docs review-pack" in prompt
        # The pack path is explicit and absolute in the shared runtime dir
        # so it survives worktree teardown.
        assert "Review pack path: /repo/.brr/diffense/task-9/pack.json" in prompt

    def test_daemon_prompt_omits_diffense_pack_when_not_requested(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runtime_dir="/repo/.brr",
        )
        assert "Review pack (diffense)" not in prompt
        assert "Review pack path" not in prompt

    def test_daemon_prompt_surfaces_runner_medium(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runner_medium="codex",
        )
        assert "- Runner: codex" in prompt

    def test_daemon_prompt_omits_runner_medium_when_absent(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
        )
        assert "- Runner:" not in prompt

    def test_daemon_prompt_surfaces_runner_quota_when_known(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runner_medium="codex",
            runner_quota="weekly 0% - resets 2026-06-17T01:29Z",
        )
        assert (
            "- Runner: codex (weekly 0% - resets 2026-06-17T01:29Z)"
            in prompt
        )

    def test_daemon_prompt_includes_outbox_contract_when_given(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
        )
        assert "/repo/.brr/outbox/evt-1" in prompt
        assert "mid-thought" in prompt
        assert "/repo/.brr/outbox/evt-1/inbox.json" in prompt
        assert "plan / todo boundaries" in prompt
        # interim replies are framed as optional extras, not the final reply
        assert "optional" in prompt.lower()

    def test_daemon_prompt_omits_outbox_contract_without_path(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
        )
        assert "mid-thought" not in prompt
        assert "outbox directory" not in prompt

    def test_daemon_prompt_states_budget_and_keepalive(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            budget_seconds=3600,
            run_id="task-9",
        )
        assert "Budget:" in prompt
        assert "60m" in prompt
        # The extension how-to is anchored on the agent's outbox path.
        assert "/repo/.brr/outbox/evt-1/.keepalive" in prompt

    def test_daemon_prompt_omits_budget_without_value(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
        )
        assert "Budget:" not in prompt
        assert ".keepalive" not in prompt

    def test_daemon_prompt_includes_driver_manual(self, tmp_path):
        """The daemon path injects brr's driver's manual — the daemon-only
        machinery (single-flight, capture net, self-scheduled wakes) the
        host-agnostic playbook deliberately leaves out."""
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path, run_id="task-9",
        )
        assert "How brr drives you" in prompt
        assert "single-flight" in prompt
        assert "schedule.md" in prompt  # self-scheduled wakes live here now

    def test_run_prompt_omits_driver_manual(self, tmp_path):
        """`brr run` is a one-shot: no daemon to fire schedules or drain an
        outbox, so it doesn't carry the driver's manual."""
        prompt = build_run_prompt("ship it", tmp_path)
        assert "How brr drives you" not in prompt
        assert "schedule.md" not in prompt

    def test_daemon_prompt_lists_pending_events_and_fold_in_contract(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-A",
            run_id="task-A",
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
        assert "inbox.json" in prompt
        assert "snapshot from when you woke" not in prompt

    def test_daemon_prompt_omits_inbox_when_no_pending_events(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-A", run_id="task-A",
        )
        assert "other pending events" not in prompt

    def test_daemon_prompt_lists_present_thoughts(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path,
            run_id="task-A",
            present=[
                {"kind": "session", "stream": "telegram:9:", "run_id": "task-Z"},
            ],
        )
        assert "Also awake right now" in prompt
        assert "session" in prompt
        assert "telegram:9:" in prompt
        # The framing names reconciliation-by-judgement, not locking.
        assert "reconcile" in prompt.lower()

    def test_daemon_prompt_omits_presence_when_alone(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path, run_id="task-A",
        )
        assert "Also awake right now" not in prompt

    def test_daemon_prompt_injects_pitfall_when_trigger_hits(self, tmp_path):
        _seed_pitfalls(
            tmp_path,
            "## Blind retry\ntrigger: docker\n"
            "Rebuild the image before you trust the cache.\n",
        )
        prompt = build_daemon_prompt(
            "rebuild the docker image and ship", "evt-A", "/tmp/resp.md",
            tmp_path, run_id="task-A",
        )
        assert "Pitfalls that match this task" in prompt
        assert "Blind retry" in prompt
        assert "Rebuild the image before you trust the cache." in prompt

    def test_daemon_prompt_omits_pitfall_when_no_trigger_match(self, tmp_path):
        _seed_pitfalls(
            tmp_path,
            "## Blind retry\ntrigger: docker\nRebuild first.\n",
        )
        prompt = build_daemon_prompt(
            "update the readme wording", "evt-A", "/tmp/resp.md",
            tmp_path, run_id="task-A",
        )
        assert "Pitfalls that match this task" not in prompt

    def test_daemon_prompt_matches_pitfall_against_event_body(self, tmp_path):
        _seed_pitfalls(
            tmp_path,
            "## Billing math\ntrigger: invoice\nProrate on the day boundary.\n",
        )
        # The trigger is absent from the task summary but present in the
        # original event text — both feed the matcher.
        prompt = build_daemon_prompt(
            "handle the request", "evt-A", "/tmp/resp.md", tmp_path,
            run_id="task-A",
            event_body="the invoice total looks wrong for mid-month signups",
        )
        assert "Pitfalls that match this task" in prompt
        assert "Prorate on the day boundary." in prompt

    def test_daemon_prompt_includes_branch_and_runtime_paths(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="run-123",
            source="telegram",
            environment="docker",
            branch_name="feat/task-abstraction",
            seed_ref="feat/task-abstraction",
            branch_source="event:target_branch",
            branch_setup_notice="target branch held elsewhere; using run branch",
            runtime_dir="/repo/.brr",
            context_path="/repo/.brr/runs/run-123/context.md",
        )
        assert "Run ID: run-123" in prompt
        assert "Legacy task id" not in prompt
        assert f"Execution root: {tmp_path}" in prompt
        assert "Seed ref: feat/task-abstraction" in prompt
        assert "Current branch: feat/task-abstraction" in prompt
        assert (
            "Branch setup: target branch held elsewhere; using run branch"
            in prompt
        )
        assert "Shared runtime dir: /repo/.brr" in prompt
        assert "Run context file: /repo/.brr/runs/run-123/context.md" in prompt
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
            run_id="task-123",
            source="telegram",
            environment="docker",
            context_path="/repo/.brr/runs/task-123/context.md",
        )
        assert "### Mode" in prompt
        assert "Stage: brr daemon run" in prompt
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
            run_id="task-9",
        )
        assert "### Mode" in prompt
        assert "Stage: brr daemon run" in prompt
        assert "Delivery: stdout captured by brr" in prompt
        assert "Source:" not in prompt
        assert "Environment:" not in prompt
        assert "Runtime recovery:" not in prompt

    def test_daemon_prompt_describes_preserved_run_branch(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-123",
            branch_name="brr/task-123",
            seed_ref="main",
            branch_source="fallback:preserve",
            host_context_branch="feature/host",
        )

        assert "Seed ref: main" in prompt
        assert "Branch source: fallback:preserve" in prompt
        assert "Host context branch: feature/host" in prompt
        # No target branch → nudge the agent to rename the brr/<run-id>
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
            run_id="task-123",
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
                "run_id": "task-prev",
            },
        ]

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-123",
            branch_name="brr/task-123",
            seed_ref="feat/task",
            runtime_dir="/repo/.brr",
            recent_conversation=recent,
            event_body="please fix the login flow",
        )
        assert "Run Context Bundle" in prompt
        assert "Recent in this conversation" in prompt
        assert "earlier ping" in prompt
        assert "task-prev" in prompt
        assert "update done" in prompt
        assert "Original event body" in prompt
        assert "please fix the login flow" in prompt
        assert "Run ID: task-123" in prompt
        assert f"Execution root: {tmp_path}" in prompt
        assert "Seed ref: feat/task" in prompt
        assert "Workstream" not in prompt
        assert "Triage" not in prompt

    def test_daemon_prompt_with_communication_snapshot(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "follow up",
            "evt-2",
            "/tmp/resp.md",
            tmp_path,
            communication_snapshot={
                "current_thread": "cloud:telegram:77:",
                "correspondent_key": "telegram:user-id:42",
                "related_threads": [
                    {
                        "conversation_key": "telegram:77:",
                        "source": "telegram",
                        "kind": "gate_thread",
                        "record_count": 4,
                        "dialogue_count": 2,
                        "latest_ts": "2026-05-05T20:01:00Z",
                    },
                    {
                        "conversation_key": "cloud:telegram:77:",
                        "source": "cloud/telegram",
                        "kind": "gate_thread",
                        "record_count": 1,
                        "dialogue_count": 1,
                        "latest_ts": "2026-05-05T20:02:00Z",
                    },
                ],
                "history_groups": [
                    {
                        "label": "telegram thread telegram:77:",
                        "path": "/repo/.brr/runs/task/history/gate.jsonl",
                        "record_count": 4,
                    },
                ],
                "recent_turns": [
                    {
                        "ts": "2026-05-05T20:00:00Z",
                        "kind": "event",
                        "source": "telegram",
                        "conversation_key": "telegram:77:",
                        "body": "prior ask",
                    },
                    {
                        "ts": "2026-05-05T20:01:00Z",
                        "kind": "artifact",
                        "artifact_kind": "response",
                        "label": "response:evt-prev",
                        "body": "prior answer",
                    },
                ],
            },
        )

        assert "Communication snapshot" in prompt
        assert "Current thread: `cloud:telegram:77:`" in prompt
        assert "Correspondent: `telegram:user-id:42`" in prompt
        assert "Related input threads" in prompt
        assert "On-demand grouped history" in prompt
        assert "/repo/.brr/runs/task/history/gate.jsonl" in prompt
        assert "Recent turns (woven, oldest first)" in prompt
        assert "prior ask" in prompt
        assert "prior answer" in prompt

    def test_daemon_prompt_renders_prior_failure_facet(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "any update?",
            "evt-2",
            "/tmp/resp.md",
            tmp_path,
            communication_snapshot={
                "current_thread": "telegram:10:",
                "prior_failure": {
                    "reason": "Credit balance is too low",
                    "stage": "run",
                    "attempts": 3,
                    "exit_code": 1,
                    "ts": "2026-06-14T16:00:00Z",
                    "event_id": "evt-old",
                },
                "related_threads": [],
                "recent_turns": [],
            },
        )

        assert "Prior run on this thread failed (operational)" in prompt
        assert "Credit balance is too low" in prompt
        assert "3 attempt(s)" in prompt
        assert "This wake lands after that interruption" in prompt

    def test_daemon_prompt_renders_woven_dialogue_bodies(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        recent = [
            {
                "ts": "2026-05-05T20:00:00Z",
                "kind": "event",
                "source": "telegram",
                "body": "first line\nsecond line",
                "summary": "first line second line",
            },
            {
                "ts": "2026-05-05T20:01:00Z",
                "kind": "artifact",
                "artifact_kind": "response",
                "label": "response:evt-prev",
                "body": "agent reply\nwith detail",
                "path": "/tmp/evt-prev.md",
            },
        ]

        prompt = build_daemon_prompt(
            "next thing", "evt-2", "/tmp/resp.md", tmp_path,
            recent_conversation=recent,
        )

        assert "user (telegram):\n  first line\n  second line" in prompt
        assert "agent (response:evt-prev):\n  agent reply\n  with detail" in prompt
        assert "/tmp/evt-prev.md" not in prompt

    def test_daemon_prompt_does_not_repeat_identical_event_body(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        body = "long telegram request"
        prompt = build_daemon_prompt(
            body, "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-123",
            event_body=body,
        )

        assert "Original event body" in prompt
        assert body in prompt
        assert f"Run: {body}" not in prompt

    def test_daemon_prompt_without_recent_conversation(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "do thing", "evt-9", "/tmp/r.md", tmp_path,
            run_id="task-9",
        )
        assert "Workstream" not in prompt
        assert "Recent in this conversation" not in prompt
        assert "Original event body" not in prompt

    def test_bundled_daemon_prompt_points_at_portals_not_dead_commands(self, tmp_path):
        prompt = build_daemon_prompt(
            "do thing",
            "evt-9",
            "/tmp/r.md",
            tmp_path,
            run_id="task-9",
            context_path="/repo/.brr/runs/task-9/context.md",
        )
        assert "Run context file: /repo/.brr/runs/task-9/context.md" in prompt
        assert "brr inspect" not in prompt
        assert "brr stream" not in prompt
        # The portals manual is inspected, not injected: the daemon prompt
        # carries a one-line pointer to it (the protocol choreography lives
        # there, not re-narrated in full on every wake).
        assert "brr docs portals" in prompt

    def test_daemon_prompt_frames_delivery_as_conversational(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it",
            "evt-1",
            "/tmp/resp.md",
            tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
        )
        assert "stay in the conversation" in prompt
        assert "substantial work should use the card" in prompt
        assert "not waiting in the dark" in prompt

    def test_delivery_contract_carries_portal_model_summary(self, tmp_path):
        # The delivery contract injects a *summary* of the portal grammar so
        # the inbound/outbound/parked model rides hot without re-narrating the
        # whole manual. It must name the three forms and point at the manual as
        # the pull-only full reference — the anti-drift link the maintainer
        # asked for. The reciprocal half is pinned in test_docs.py
        # (test_portals_manual_links_back_to_delivery_contract); keep the two
        # tests in step so contract and manual can't silently diverge.
        prompt = build_daemon_prompt(
            "ship it",
            "evt-1",
            "/tmp/resp.md",
            tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
        )
        assert "portals" in prompt
        for form in ("*inbound*", "*outbound*", "*parked*"):
            assert form in prompt
        assert "injected summary" in prompt
        assert "brr docs portals" in prompt


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
    """Pin the prompt + AGENTS.md guidance for design-loaded / "reconsider"
    tasks. Stance refined 2026-06-20 (see `kb/log.md`): the default is
    *reconcile and act in the same thought*, not surface-and-wait; a
    chat-only reply is reserved for a genuine fork, where it still must be
    authorized so the diff-as-receipt rule can't force a half-fitting
    commit. Both failure modes — path-of-least-resistance compliance and
    aloof bounce-back — are guarded. See `kb/design-git-layer-rework.md`
    Phase 3 for the original revisit-signal rationale."""

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

    def test_run_prompt_biases_to_resolve_and_act(self):
        prompt = _read_bundled_run_prompt()
        # The default on a clear, reversible reconsider is to resolve it
        # in-thread, not to park it for a second "go do that" event.
        assert "this same thought" in prompt
        assert "round-trip" in prompt
        assert "Stewardship" in prompt

    def test_run_prompt_authorizes_no_commit_for_genuine_fork(self):
        prompt = _read_bundled_run_prompt()
        # The chat-only-reply outcome must stay named for the genuine-fork
        # case so the diff-as-receipt rule doesn't force a half-fitting
        # commit when there is no clear edit yet.
        assert "chat-only reply" in prompt
        assert "the complete task" in prompt

    def test_agents_md_self_review_contains_contradiction_check(self):
        agents = _read_bundled_agents_md()
        # The self-review bullet maps onto Stewardship and now catches
        # both failure modes, not just compliance.
        assert "reconcile it against the current state" in agents
        assert "aloof bounce-back" in agents
        assert "Stewardship" in agents


class TestDaemonModeGuardrails:
    """Pin the run.md changes that route daemon runners through the
    Run Context Bundle's Mode block and treat the run context file as
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


class TestIntrospectionMode:
    """The opt-in introspection/development toggle: when on, every wake
    invites the resident to inspect the shape of its own injected context
    and raise improvements with the user. See
    `kb/design-context-introspection.md`."""

    @staticmethod
    def _enable(repo_root) -> None:
        brr = repo_root / ".brr"
        brr.mkdir(parents=True, exist_ok=True)
        (brr / "config").write_text("introspect.enabled=true\n", encoding="utf-8")

    def test_off_by_default_run_prompt(self, tmp_path):
        # No config at all → the invitation never rides along.
        prompt = build_run_prompt("do something", tmp_path)
        assert "Look at it" not in prompt

    def test_off_by_default_daemon_prompt(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path, run_id="task-9",
        )
        assert "Look at it" not in prompt

    def test_injected_into_run_prompt_when_enabled(self, tmp_path):
        self._enable(tmp_path)
        prompt = build_run_prompt("do something", tmp_path)
        assert "Look at it" in prompt
        assert "The shape of the context itself" in prompt
        # It rides alongside the task; it must not displace the task text,
        # and it sits before the task as the last framing.
        assert "do something" in prompt
        assert prompt.index("Look at it") < prompt.index("do something")

    def test_injected_into_daemon_prompt_when_enabled(self, tmp_path):
        self._enable(tmp_path)
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path, run_id="task-9",
        )
        assert "Look at it" in prompt

    def test_bundled_introspection_keeps_awe_and_dialogue_intent(self):
        from pathlib import Path

        import brr

        text = (Path(brr.__file__).parent / "prompts" / "introspection.md").read_text(
            encoding="utf-8",
        )
        # The two halves the tone must hold: regard for the existing shape
        # before judging it, and surfacing what's found to the user as
        # dialogue rather than a silent edit. The current dev-mode prompt
        # also carries the standing-portal and pre-release cutting stance.
        assert "without flinching" in text
        assert "say it to" in text.lower()
        assert "silent edit" in text
        assert "standing portal" in text
        assert "pre-release" in text
