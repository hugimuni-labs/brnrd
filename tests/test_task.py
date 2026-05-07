"""Tests for task module — Task dataclass, env resolution, persistence."""

from brr.task import Task, list_tasks


class TestTaskFromEvent:
    def test_basic(self):
        event = {"id": "evt-1", "source": "telegram", "body": "do stuff", "status": "pending"}
        task = Task.from_event(event)
        assert task.event_id == "evt-1"
        assert task.body == "do stuff"
        assert task.source == "telegram"
        assert task.env == "worktree"
        assert task.status == "pending"
        assert task.id.startswith("task-")

    def test_env_auto_defaults_to_worktree(self):
        event = {"id": "evt-auto-wt", "body": "anything"}
        task = Task.from_event(event, {"env": "auto"})
        assert task.env == "worktree"

    def test_environment_config_auto_prefers_configured_docker(self):
        event = {"id": "evt-auto-docker", "body": "change code"}
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

    def test_event_env_overrides_config_default(self):
        event = {"id": "evt-2", "body": "fix bug", "env": "host"}
        cfg = {"default_env": "worktree"}
        task = Task.from_event(event, cfg)
        assert task.env == "host"
        assert "env" not in task.meta

    def test_meta_preserved(self):
        event = {
            "id": "evt-3", "body": "hi", "source": "telegram",
            "status": "pending", "telegram_chat_id": 123,
        }
        task = Task.from_event(event)
        assert task.meta["telegram_chat_id"] == 123
        assert "id" not in task.meta
        assert "body" not in task.meta


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        task = Task(
            id="task-123", event_id="evt-456", body="implement feature",
            env="worktree", status="running",
            source="telegram", meta={"chat_id": 42},
        )
        path = task.save(tmp_path)
        assert path.exists()

        loaded = Task.from_file(path)
        assert loaded is not None
        assert loaded.id == "task-123"
        assert loaded.event_id == "evt-456"
        assert loaded.body == "implement feature"
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
            env="docker", status="conflict",
            source="slack",
        )
        text = task.to_frontmatter()
        assert "env: docker" in text
        assert "status: conflict" in text
        assert "the body\nwith lines" in text

    def test_from_file_accepts_environment_alias(self, tmp_path):
        path = tmp_path / "task-env.md"
        path.write_text(
            "---\n"
            "id: task-env\n"
            "event_id: evt-env\n"
            "environment: docker\n"
            "status: pending\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )

        task = Task.from_file(path)
        assert task is not None
        assert task.env == "docker"
