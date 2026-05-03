"""Tests for task module — Task dataclass, persistence, branch resolution."""

from brr.task import Task, list_tasks


class TestTaskFromEvent:
    def test_basic(self):
        event = {"id": "evt-1", "source": "telegram", "body": "do stuff", "status": "pending"}
        task = Task.from_event(event)
        assert task.event_id == "evt-1"
        assert task.body == "do stuff"
        assert task.source == "telegram"
        assert task.branch == "current"
        assert task.env == "host"
        assert task.status == "pending"
        assert task.id.startswith("task-")

    def test_config_defaults(self):
        event = {"id": "evt-2", "body": "fix bug"}
        cfg = {"default_branch": "auto"}
        task = Task.from_event(event, cfg)
        assert task.branch == "auto"
        assert task.env == "worktree"

    def test_env_auto_selects_host_for_current_branch(self):
        event = {"id": "evt-auto-host", "body": "answer question"}
        task = Task.from_event(event, {"env": "auto"})
        assert task.branch == "current"
        assert task.env == "host"

    def test_env_auto_selects_worktree_for_branch_work(self):
        event = {"id": "evt-auto-wt", "body": "change code", "branch": "auto"}
        task = Task.from_event(event, {"env": "auto"})
        assert task.env == "worktree"

    def test_environment_config_auto_prefers_configured_docker(self):
        event = {"id": "evt-auto-docker", "body": "change code", "branch": "auto"}
        task = Task.from_event(
            event,
            {"environment": "auto", "docker.image": "brr/test-runner:latest"},
        )
        assert task.env == "docker"

    def test_environment_overrides_env_config(self):
        event = {"id": "evt-env-override", "body": "answer question"}
        task = Task.from_event(event, {"environment": "host", "env": "docker"})
        assert task.env == "host"

    def test_event_environment_overrides_config(self):
        event = {
            "id": "evt-event-env",
            "body": "answer question",
            "environment": "host",
        }
        task = Task.from_event(
            event,
            {"environment": "docker", "docker.image": "brr/test-runner:latest"},
        )
        assert task.env == "host"

    def test_event_overrides_config_defaults(self):
        event = {
            "id": "evt-2", "body": "fix bug",
            "branch": "new:feature/task", "env": "worktree",
        }
        cfg = {"default_branch": "current", "default_env": "host"}
        task = Task.from_event(event, cfg)
        assert task.branch == "new:feature/task"
        assert task.env == "worktree"
        assert "branch" not in task.meta
        assert "env" not in task.meta

    def test_meta_preserved(self):
        event = {
            "id": "evt-3", "body": "hi", "source": "telegram",
            "status": "pending", "telegram_chat_id": 123,
        }
        task = Task.from_event(event)
        assert task.meta["telegram_chat_id"] == 123
        # Known fields should NOT be in meta
        assert "id" not in task.meta
        assert "body" not in task.meta

    def test_from_triage_output_applies_frontmatter_and_body(self):
        event = {
            "id": "evt-4",
            "body": "raw event body",
            "source": "telegram",
            "telegram_chat_id": 123,
        }
        text = (
            "---\n"
            "branch: auto\n"
            "env: worktree\n"
            "priority: high\n"
            "---\n"
            "refined task body\n"
        )
        task = Task.from_triage_output(text, event)
        assert task.event_id == "evt-4"
        assert task.body == "refined task body"
        assert task.branch == "auto"
        assert task.env == "worktree"
        assert task.meta["telegram_chat_id"] == 123
        assert task.meta["priority"] == "high"

    def test_from_triage_output_requires_frontmatter(self):
        event = {"id": "evt-5", "body": "raw event body"}
        try:
            Task.from_triage_output("refined task body", event)
        except ValueError as exc:
            assert "missing frontmatter" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    def test_from_triage_output_rejects_invalid_env_name(self):
        event = {"id": "evt-6", "body": "raw event body"}
        text = "---\nbranch: current\nenv: bad/env\n---\nrefined task body\n"
        try:
            Task.from_triage_output(text, event)
        except ValueError as exc:
            assert "invalid triage env" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    def test_from_triage_output_allows_future_env_names(self):
        event = {"id": "evt-7", "body": "raw event body"}
        text = "---\nbranch: current\nenv: docker\n---\nrefined task body\n"
        task = Task.from_triage_output(text, event)
        assert task.env == "docker"

    def test_triage_auto_respects_configured_environment_default(self):
        event = {"id": "evt-8", "body": "raw event body"}
        text = "---\nbranch: auto\nenv: auto\n---\nrefined task body\n"
        task = Task.from_triage_output(
            text,
            event,
            {"environment": "docker", "docker.image": "brr/test-runner:latest"},
        )
        assert task.env == "docker"


class TestBranchResolution:
    def test_current(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="current")
        assert task.resolve_branch_name() is None

    def test_auto(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="auto")
        assert task.resolve_branch_name() == "brr/t-1"

    def test_task_mode(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="task")
        assert task.resolve_branch_name() == "brr/t-1"

    def test_new_named(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="new:feature/foo")
        assert task.resolve_branch_name() == "feature/foo"

    def test_explicit_name(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="my-branch")
        assert task.resolve_branch_name() == "my-branch"


class TestNeedsWorktree:
    def test_host_current(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="current", env="host")
        assert not task.needs_worktree

    def test_worktree_env(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="current", env="worktree")
        assert task.needs_worktree

    def test_branch_implies_worktree(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="auto", env="host")
        assert task.needs_worktree

    def test_future_env_does_not_imply_worktree(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="auto", env="docker")
        assert not task.needs_worktree


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        task = Task(
            id="task-123", event_id="evt-456", body="implement feature",
            branch="auto", env="worktree", status="running",
            source="telegram", meta={"chat_id": 42},
        )
        path = task.save(tmp_path)
        assert path.exists()

        loaded = Task.from_file(path)
        assert loaded is not None
        assert loaded.id == "task-123"
        assert loaded.event_id == "evt-456"
        assert loaded.body == "implement feature"
        assert loaded.branch == "auto"
        assert loaded.env == "worktree"
        assert loaded.status == "running"
        assert loaded.source == "telegram"
        assert loaded.meta["chat_id"] == 42

    def test_update_status(self, tmp_path):
        task = Task(id="task-1", event_id="evt-1", body="x")
        task.save(tmp_path)
        task.update_status("done", tmp_path)
        assert task.status == "done"

        reloaded = Task.from_file(tmp_path / "task-1.md")
        assert reloaded.status == "done"

    def test_list_tasks(self, tmp_path):
        for i, status in enumerate(["pending", "running", "done"]):
            t = Task(id=f"task-{i}", event_id=f"evt-{i}", body="x", status=status)
            t.save(tmp_path)

        all_tasks = list_tasks(tmp_path)
        assert len(all_tasks) == 3

        running = list_tasks(tmp_path, status="running")
        assert len(running) == 1
        assert running[0].id == "task-1"

    def test_list_empty_dir(self, tmp_path):
        assert list_tasks(tmp_path / "nonexistent") == []

    def test_from_file_bad_path(self, tmp_path):
        assert Task.from_file(tmp_path / "nope.md") is None

    def test_frontmatter_roundtrip(self):
        task = Task(
            id="task-rt", event_id="evt-rt", body="the body\nwith lines",
            branch="new:feat/x", env="docker", status="needs_context",
            source="slack",
        )
        text = task.to_frontmatter()
        assert "branch: new:feat/x" in text
        assert "status: needs_context" in text
        assert "the body\nwith lines" in text

    def test_from_file_accepts_environment_alias(self, tmp_path):
        path = tmp_path / "task-env.md"
        path.write_text(
            "---\n"
            "id: task-env\n"
            "event_id: evt-env\n"
            "branch: current\n"
            "environment: docker\n"
            "status: pending\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )

        task = Task.from_file(path)
        assert task is not None
        assert task.env == "docker"
