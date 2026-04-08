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
        assert task.env == "local"
        assert task.status == "pending"
        assert task.id.startswith("task-")

    def test_config_defaults(self):
        event = {"id": "evt-2", "body": "fix bug"}
        cfg = {"default_branch": "auto", "default_env": "worktree"}
        task = Task.from_event(event, cfg)
        assert task.branch == "auto"
        assert task.env == "worktree"

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
    def test_local_current(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="current", env="local")
        assert not task.needs_worktree

    def test_worktree_env(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="current", env="worktree")
        assert task.needs_worktree

    def test_branch_implies_worktree(self):
        task = Task(id="t-1", event_id="e-1", body="x", branch="auto", env="local")
        assert task.needs_worktree


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
