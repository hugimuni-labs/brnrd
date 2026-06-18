"""Tests for run module — Run dataclass, env resolution, persistence."""

from brr.run import Run, list_runs


class TestRunFromEvent:
    def test_basic(self):
        event = {"id": "evt-1", "source": "telegram", "body": "do stuff", "status": "pending"}
        run = Run.from_event(event)
        assert run.event_id == "evt-1"
        assert run.body == "do stuff"
        assert run.source == "telegram"
        assert run.env == "worktree"
        assert run.status == "pending"
        assert run.id.startswith("run-")

    def test_env_auto_defaults_to_worktree(self):
        event = {"id": "evt-auto-wt", "body": "anything"}
        run = Run.from_event(event, {"env": "auto"})
        assert run.env == "worktree"

    def test_environment_config_auto_prefers_configured_docker(self):
        event = {"id": "evt-auto-docker", "body": "change code"}
        run = Run.from_event(
            event,
            {"environment": "auto", "docker.image": "brr/test-runner:latest"},
        )
        assert run.env == "docker"

    def test_environment_overrides_env_config(self):
        event = {"id": "evt-env-override", "body": "answer question"}
        run = Run.from_event(event, {"environment": "host", "env": "docker"})
        assert run.env == "host"

    def test_event_environment_overrides_config(self):
        event = {
            "id": "evt-event-env",
            "body": "answer question",
            "environment": "host",
        }
        run = Run.from_event(
            event,
            {"environment": "docker", "docker.image": "brr/test-runner:latest"},
        )
        assert run.env == "host"

    def test_event_env_overrides_config_default(self):
        event = {"id": "evt-2", "body": "fix bug", "env": "host"}
        cfg = {"default_env": "worktree"}
        run = Run.from_event(event, cfg)
        assert run.env == "host"
        assert "env" not in run.meta

    def test_meta_preserved(self):
        event = {
            "id": "evt-3", "body": "hi", "source": "telegram",
            "status": "pending", "telegram_chat_id": 123,
        }
        run = Run.from_event(event)
        assert run.meta["telegram_chat_id"] == 123
        assert "id" not in run.meta
        assert "body" not in run.meta


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        run = Run(
            id="run-123", event_id="evt-456", body="implement feature",
            env="worktree", status="running",
            source="telegram", meta={"chat_id": 42},
        )
        path = run.save(tmp_path)
        assert path.exists()
        assert path == tmp_path / "run-123" / "run.md"

        loaded = Run.from_file(path)
        assert loaded is not None
        assert loaded.id == "run-123"
        assert loaded.event_id == "evt-456"
        assert loaded.body == "implement feature"
        assert loaded.env == "worktree"
        assert loaded.status == "running"
        assert loaded.source == "telegram"
        assert loaded.meta["chat_id"] == 42

    def test_update_status(self, tmp_path):
        run = Run(id="run-1", event_id="evt-1", body="x")
        run.save(tmp_path)
        run.update_status("done", tmp_path)
        assert run.status == "done"

        reloaded = Run.from_file(tmp_path / "run-1" / "run.md")
        assert reloaded.status == "done"

    def test_list_runs(self, tmp_path):
        for i, status in enumerate(["pending", "running", "done"]):
            t = Run(id=f"run-{i}", event_id=f"evt-{i}", body="x", status=status)
            t.save(tmp_path)

        all_runs = list_runs(tmp_path)
        assert len(all_runs) == 3

        running = list_runs(tmp_path, status="running")
        assert len(running) == 1
        assert running[0].id == "run-1"

    def test_list_empty_dir(self, tmp_path):
        assert list_runs(tmp_path / "nonexistent") == []

    def test_from_file_bad_path(self, tmp_path):
        assert Run.from_file(tmp_path / "nope.md") is None

    def test_frontmatter_roundtrip(self):
        run = Run(
            id="run-rt", event_id="evt-rt", body="the body\nwith lines",
            env="docker", status="conflict",
            source="slack",
        )
        text = run.to_frontmatter()
        assert "env: docker" in text
        assert "status: conflict" in text
        assert "the body\nwith lines" in text

    def test_from_file_accepts_environment_alias(self, tmp_path):
        path = tmp_path / "run-env.md"
        path.write_text(
            "---\n"
            "id: run-env\n"
            "event_id: evt-env\n"
            "environment: docker\n"
            "status: pending\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )

        task = Run.from_file(path)
        assert task is not None
        assert task.env == "docker"
