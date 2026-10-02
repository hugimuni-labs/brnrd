"""A parked strand's control edge survives a fresh daemon image (#2160).

``_run_controls`` is memory-only. A strand parked on its own hold keeps its
edge on purpose, but once its process is reaped it is out of
``active_spawns`` — so the quiescent re-exec guard does not protect it, and
the next image woke with a held child manifest whose parent's ``to:`` was
refused as *matches no live concurrent spawn* (measured with a real
``execve`` on c9e7525f; see the issue).

Every test here boots through the real ``daemon.start`` (stopped at its
first heartbeat tick) over real ``Run`` manifests on disk, then drives the
real verbs — ``_queue_child_message`` / ``_queue_stop_request``. The
registry starts empty, which is what a new process is; nothing calls the
recovery helper by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import daemon, protocol, resource_hold
from brr.run import Run

from _helpers import write_repo_scaffold

CONV = "cloud:telegram:1:"
REPO = "test/repo"


@pytest.fixture(autouse=True)
def _fresh_image(monkeypatch):
    # A new process: no control survives from anywhere.
    monkeypatch.setattr(daemon, "_run_controls", {})


def _runs_dir(root: Path) -> Path:
    runs = root / ".brr" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    return runs


def _held_strand(
    root: Path,
    *,
    run_id: str = "run-kid",
    event_id: str = "evt-kid",
    parent_run_id: str = "run-parent",
    conversation_key: str = CONV,
    repo_label: str = REPO,
    strand: bool = True,
) -> Run:
    child = Run(
        id=run_id, event_id=event_id, body="", source="spawn",
        status=resource_hold.RUN_STATUS,
    )
    if strand:
        child.meta["strand"] = True
    if parent_run_id:
        child.meta["spawn_parent_run_id"] = parent_run_id
        child.meta["spawn_parent_conversation_key"] = conversation_key
    child.meta["repo_label"] = repo_label
    child.meta["runner_shell"] = "codex"
    child.meta["title"] = "the parked one"
    child.meta["spawn_allowance_tokens"] = 400_000
    child.meta["spawn_allowance_spent"] = 123_456
    child.meta["submitted"] = True
    child.meta["submitted_produce"] = {
        "spawn_submit_generation": 2, "spawn_branch": "brr/x",
    }
    child.meta["resource_hold"] = resource_hold.build(
        reason=resource_hold.REASON_QUOTA_STARVED, provider="codex",
        resume_condition=resource_hold.RESUME_REFILL,
    )
    child.save(_runs_dir(root))
    # Parked by the previous image, through the real writers the reap uses.
    daemon._register_run_control(
        event_id, parent_run_id, parent_conversation_key=conversation_key,
        repo_label=repo_label, allowance_tokens=400_000, title="the parked one",
    )
    daemon._bind_run_control(event_id, run_id)
    daemon._park_run_control(event_id, daemon._parked_child_projection(child))
    daemon._persist_parked_edge(root / ".brr" / "inbox", daemon._find_run_control(event_id))
    daemon._run_controls.clear()  # ...and that image is gone.
    return child


def _boot(root: Path, monkeypatch) -> None:
    """The real ``daemon.start`` boot, stopped at its first heartbeat tick."""
    if not (root / "AGENTS.md").exists():
        write_repo_scaffold(root)
    monkeypatch.setattr(daemon, "read_pid", lambda _root: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _root: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _root: None)
    monkeypatch.setattr(daemon.signal, "signal", lambda *a: None)
    monkeypatch.setattr(daemon, "_start_account_gates", lambda *a: [])
    monkeypatch.setattr(daemon, "_mount_home_knowledge", lambda *a: None)
    monkeypatch.setattr(
        daemon.conf, "load_config", lambda _root: {"dominion.enabled": False},
    )
    monkeypatch.setattr(
        daemon.release_availability, "refresh_if_stale_async",
        lambda *a, **kw: None,
    )
    # The codex bucket is still starved: the hold must stay armed.
    monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **kw: 1.0)

    def first_tick(*a, **kw):
        raise StopIteration

    monkeypatch.setattr(daemon, "_fire_due_schedules", first_tick)
    with pytest.raises(StopIteration):
        daemon.start(root)


def _asker(
    run_id: str = "run-parent",
    *,
    conversation_key: str = CONV,
    repo_label: str = REPO,
    strand_parent: str = "",
) -> Run:
    task = Run(
        id=run_id, event_id=f"evt-{run_id}", body="",
        conversation_key=conversation_key, meta={"repo_label": repo_label},
    )
    if strand_parent:
        task.meta["strand"] = True
        task.meta["spawn_parent_run_id"] = strand_parent
    return task


def _steer(root: Path, task: Run, target: str = "run-kid") -> bool:
    outbox = root / "outbox"
    outbox.mkdir(exist_ok=True)
    return daemon._queue_child_message(
        daemon._WorkerEmit(root / ".brr", task.conversation_key, task.event_id),
        task, root / ".brr" / "inbox", task.event_id, {"to": target},
        "stay parked", outbox,
    )


def _stop(root: Path, task: Run, target: str = "run-kid") -> bool:
    outbox = root / "outbox"
    outbox.mkdir(exist_ok=True)
    return daemon._queue_stop_request(
        daemon._WorkerEmit(root / ".brr", task.conversation_key, task.event_id),
        task, root / ".brr" / "inbox", task.event_id, {"stop": target},
        "enough", outbox,
    )


def _child(root: Path, run_id: str = "run-kid") -> Run:
    return Run.from_file(root / ".brr" / "runs" / run_id / "run.md")


class TestTheDispatcherKeepsItsVerbs:
    def test_the_parent_can_steer_a_held_child_after_boot(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)

        assert resource_hold.run_is_held(_child(tmp_path).status, _child(tmp_path).meta)
        assert _steer(tmp_path, _asker())
        steers = [
            ev for ev in protocol.list_pending(tmp_path / ".brr" / "inbox")
            if ev.get("spawn_message_for_event") == "evt-kid"
        ]
        assert len(steers) == 1
        assert steers[0].get("spawn_message_for_run") == "run-kid"

    def test_the_parent_can_stop_a_held_child_after_boot(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)

        assert _stop(tmp_path, _asker())
        child = _child(tmp_path)
        assert child.status == "stopped"
        assert not resource_hold.run_is_held(child.status, child.meta)
        assert daemon._find_run_control("evt-kid") is None

    def test_the_parent_sees_its_parked_row_with_its_facts(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)

        rows = daemon._parked_child_controls("run-parent")
        assert [r["run_id"] for r in rows] == ["run-kid"]
        assert rows[0]["hold_resume"] == resource_hold.RESUME_REFILL
        assert rows[0]["title"] == "the parked one"
        assert rows[0]["weighted"] == 123_456
        assert daemon._working_child_controls("run-parent") == []
        control = daemon._find_run_control("run-kid")
        assert control["allowance_tokens"] == 400_000
        assert control["submitted"] is True
        assert control["submit_generation"] == 2
        assert control["submitted_produce"]["spawn_branch"] == "brr/x"
        assert control["parent_conversation_key"] == CONV
        assert control["repo_label"] == REPO


class TestTheFenceIsUnchanged:
    def test_a_same_thread_resident_adopts_through_the_fence(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)

        resident = _asker("run-next-seat")
        assert _steer(tmp_path, resident)
        control = daemon._find_run_control("run-kid")
        assert control["parent_run_id"] == "run-next-seat"
        assert control["adopted_from_run_id"] == "run-parent"
        assert daemon._child_owner_route("evt-kid") == ("run-next-seat", CONV)
        # Adoption moved the authority: the adopter now stops it too.
        assert _stop(tmp_path, resident)
        assert _child(tmp_path).status == "stopped"

    @pytest.mark.parametrize("asker", [
        _asker("run-elsewhere", conversation_key="cloud:telegram:2:"),
        _asker("run-other-repo", repo_label="other/repo"),
        _asker("run-a-strand", strand_parent="run-someone"),
    ], ids=["foreign-conversation", "foreign-repo", "strand-asker"])
    def test_foreign_askers_stay_refused(self, tmp_path, monkeypatch, asker):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)

        assert not _steer(tmp_path, asker)
        assert not _stop(tmp_path, asker)
        control = daemon._find_run_control("run-kid")
        assert control["parent_run_id"] == "run-parent"
        assert "adopted_from_run_id" not in control
        assert resource_hold.run_is_held(_child(tmp_path).status, _child(tmp_path).meta)


class TestNothingIsRevived:
    def test_released_and_terminal_children_get_no_edge(self, tmp_path, monkeypatch):
        released = _held_strand(tmp_path, run_id="run-released", event_id="evt-released")
        released.meta["resource_hold"] = resource_hold.mark_released(
            released.meta["resource_hold"], by="refill",
        )
        released.transition("done", why="released:refill", by="refill")
        stopped = _held_strand(tmp_path, run_id="run-stopped", event_id="evt-stopped")
        stopped.meta["resource_hold"] = resource_hold.mark_released(
            stopped.meta["resource_hold"], by="run-parent",
        )
        stopped.transition("stopped", why="parent_closed_parked_child", by="run-parent")
        _boot(tmp_path, monkeypatch)

        assert daemon._run_controls == {}
        assert not _steer(tmp_path, _asker(), target="run-released")
        assert not _stop(tmp_path, _asker(), target="run-stopped")
        assert _child(tmp_path, "run-released").status == "done"
        assert _child(tmp_path, "run-stopped").status == "stopped"

    def test_a_held_seat_and_an_unparented_strand_are_not_edges(self, tmp_path, monkeypatch):
        _held_strand(tmp_path, run_id="run-seat", event_id="evt-seat", strand=False)
        _held_strand(tmp_path, run_id="run-nobody", event_id="evt-nobody", parent_run_id="")
        _boot(tmp_path, monkeypatch)

        assert daemon._run_controls == {}

    def test_a_second_boot_in_the_same_image_adds_nothing(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)
        _boot(tmp_path, monkeypatch)

        assert list(daemon._run_controls) == ["evt-kid"]


def _fresh_process(monkeypatch) -> None:
    monkeypatch.setattr(daemon, "_run_controls", {})


def _live_resident(run_id: str, event_id: str) -> None:
    """Register a running resident thought exactly as the dispatch loop does."""
    daemon._register_run_control(
        event_id, None, parent_conversation_key=CONV, repo_label=REPO,
    )
    daemon._bind_run_control(event_id, run_id)


class TestAdoptionIsCurrentOwnership:
    def test_the_adopter_owns_the_edge_after_a_fresh_image(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)
        assert _steer(tmp_path, _asker("run-next-seat"))
        child = _child(tmp_path)
        assert child.meta["spawn_parent_run_id"] == "run-parent"  # lineage kept
        record = json.loads((_runs_dir(tmp_path) / "run-kid" / "edge.json").read_text())
        assert record["owner_run_id"] == "run-next-seat"
        assert record["adopted_from_run_id"] == "run-parent"

        _fresh_process(monkeypatch)
        _boot(tmp_path, monkeypatch)
        _boot(tmp_path, monkeypatch)

        assert list(daemon._run_controls) == ["evt-kid"]
        control = daemon._find_run_control("evt-kid")
        assert control["parent_run_id"] == "run-next-seat"
        assert control["adopted_from_run_id"] == "run-parent"
        assert _steer(tmp_path, _asker("run-next-seat"))

    def test_the_old_dispatcher_cannot_bypass_a_live_adopter(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)
        assert _steer(tmp_path, _asker("run-next-seat"))
        _fresh_process(monkeypatch)
        _boot(tmp_path, monkeypatch)
        _live_resident("run-next-seat", "evt-next-seat")

        # Exact-id equality would have let the original dispatcher straight
        # back in had recovery reinstated it; the live adopter holds the edge.
        assert not _steer(tmp_path, _asker("run-parent"))
        assert not _stop(tmp_path, _asker("run-parent"))
        assert daemon._find_run_control("evt-kid")["parent_run_id"] == "run-next-seat"
        assert resource_hold.run_is_held(_child(tmp_path).status, _child(tmp_path).meta)
        assert _stop(tmp_path, _asker("run-next-seat"))
        assert _child(tmp_path).status == "stopped"

    def test_an_edge_record_the_fence_could_not_produce_recovers_nothing(
        self, tmp_path, monkeypatch,
    ):
        _held_strand(tmp_path)
        path = _runs_dir(tmp_path) / "run-kid" / "edge.json"
        record = json.loads(path.read_text())
        record.update(owner_run_id="run-elsewhere",
                      owner_conversation_key="cloud:telegram:2:",
                      adopted_from_run_id="run-parent")
        path.write_text(json.dumps(record))
        _boot(tmp_path, monkeypatch)

        assert daemon._run_controls == {}
        assert not _steer(tmp_path, _asker("run-elsewhere", conversation_key="cloud:telegram:2:"))
        assert not _steer(tmp_path, _asker("run-parent"))

    def test_a_held_strand_with_no_edge_record_is_not_recovered(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        (_runs_dir(tmp_path) / "run-kid" / "edge.json").unlink()
        _boot(tmp_path, monkeypatch)

        assert daemon._run_controls == {}

    def test_an_adoption_that_cannot_be_recorded_is_refused(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)
        writer = daemon._write_parked_edge

        def disk_full(*a, **kw):
            raise OSError("disk full")

        # Never ``monkeypatch.undo()`` here: it would also undo conftest's
        # XDG_STATE_HOME isolation and the next boot would write a real home.
        monkeypatch.setattr(daemon, "_write_parked_edge", disk_full)
        assert not _steer(tmp_path, _asker("run-next-seat"))
        assert not _stop(tmp_path, _asker("run-next-seat"))
        control = daemon._find_run_control("evt-kid")
        assert control["parent_run_id"] == "run-parent"
        assert "adopted_from_run_id" not in control
        record = json.loads((_runs_dir(tmp_path) / "run-kid" / "edge.json").read_text())
        assert record["owner_run_id"] == "run-parent"
        monkeypatch.setattr(daemon, "_write_parked_edge", writer)

        _fresh_process(monkeypatch)
        _boot(tmp_path, monkeypatch)
        _live_resident("run-next-seat", "evt-next-seat")

        # The refused adopter never owned it; the dispatcher still does.
        assert daemon._find_run_control("evt-kid")["parent_run_id"] == "run-parent"
        assert _steer(tmp_path, _asker("run-parent"))

    def test_a_failed_park_time_write_recovers_nothing(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)
        assert _steer(tmp_path, _asker("run-next-seat"))  # adopted, recorded
        writer = daemon._write_parked_edge

        def disk_full(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(daemon, "_write_parked_edge", disk_full)
        assert not daemon._persist_parked_edge(
            tmp_path / ".brr" / "inbox", daemon._find_run_control("evt-kid"),
        )
        monkeypatch.setattr(daemon, "_write_parked_edge", writer)
        _fresh_process(monkeypatch)
        _boot(tmp_path, monkeypatch)

        assert daemon._run_controls == {}
        assert not _steer(tmp_path, _asker("run-parent"))


class TestTheAllowanceRidesThePark:
    def test_a_grant_to_a_parked_child_survives_a_fresh_image(self, tmp_path, monkeypatch):
        _held_strand(tmp_path)
        _boot(tmp_path, monkeypatch)
        assert _steer_body(tmp_path, _asker(), "allowance: +100k\nmore room")
        assert daemon._find_run_control("run-kid")["allowance_tokens"] == 500_000

        _fresh_process(monkeypatch)
        _boot(tmp_path, monkeypatch)

        assert daemon._find_run_control("run-kid")["allowance_tokens"] == 500_000


def _steer_body(root: Path, task: Run, body: str) -> bool:
    outbox = root / "outbox"
    outbox.mkdir(exist_ok=True)
    return daemon._queue_child_message(
        daemon._WorkerEmit(root / ".brr", task.conversation_key, task.event_id),
        task, root / ".brr" / "inbox", task.event_id, {"to": "run-kid"},
        body, outbox,
    )
