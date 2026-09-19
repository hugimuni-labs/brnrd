"""The seat waits for working children — never for sleeping ones.

The maintainer's rule, and the whole spec: *"the seat should never hold the
execution because of parked children. Holding it on the living strands makes
sense; if the strands are parked while the seat has quota to move — the seat
can close the strands if they draw too much, or the shell behind it dried
up."*

The defect underneath it was **not** the one the task assumed. A parked child
did not keep its parent waiting; it took the parent's chair.
``_arm_resource_hold``'s ``_is_strand`` early-return sat *after* the
supersession loop, so a strand parking on its own provider wall released its
parent's ``resume: strands`` hold as ``superseded``, folded the parent's
accumulated mail into its own ``refill`` record, and became ``_repo_seat``.
``TestAParkedStrandIsNotTheSeat`` is the regression that measures it.

Everything here drives the real callers — ``_arm_resource_hold``,
``_handle_resource_held_events``, ``_park_bolt_on_live_strands``,
``_apply_run_stop`` — over real ``Run`` manifests on disk, never the
predicates directly.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from brr import cut_verb, daemon, hooks, protocol, resource_hold
from brr.run import Run


@pytest.fixture(autouse=True)
def _isolated_controls(monkeypatch):
    monkeypatch.setattr(daemon, "_run_controls", {})


def _runs_dir(tmp_path: Path) -> Path:
    runs_dir = tmp_path / ".brr" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


def _parent_parked_on_its_children(tmp_path: Path, *, mail: str = "") -> Run:
    """A real seat, held on ``resume: strands``, with a letter waiting."""
    runs_dir = _runs_dir(tmp_path)
    parent = Run(
        id="run-parent", event_id="evt-p", body="",
        status=resource_hold.RUN_STATUS,
    )
    meta = resource_hold.build(
        reason=resource_hold.REASON_WAITING_ON_STRANDS, provider="claude",
        resume_condition=resource_hold.RESUME_STRANDS,
        conversation_key="cloud:telegram:1:", seat_key="seat",
    )
    if mail:
        meta = resource_hold.accumulate_event(meta, mail)
    parent.meta["resource_hold"] = meta
    parent.meta["child_run_ids"] = "run-kid"
    parent.save(runs_dir)
    return parent


def _strand_hits_its_wall(tmp_path: Path, *, run_id: str = "run-kid") -> Run:
    """A real child strand parking on a measured starvation wall."""
    runs_dir = _runs_dir(tmp_path)
    kid = Run(id=run_id, event_id=f"evt-{run_id}", body="", source="spawn")
    kid.meta["strand"] = True
    kid.meta["spawn_parent_run_id"] = "run-parent"
    kid.meta["runner_shell"] = "codex"
    daemon._arm_resource_hold(
        kid, runs_dir,
        conversation_key="cloud:telegram:1:",
        account_home=tmp_path / ".brr",
        repo_root=tmp_path,
        reason=resource_hold.REASON_QUOTA_STARVED,
        provider="codex",
        resume_condition=resource_hold.RESUME_REFILL,
        quota={
            "binding_remaining_pct": 1.0,
            "refill_floor_pct": 10.0,
            "starve_floor_pct": 2.0,
        },
    )
    return kid


def _reread(tmp_path: Path, run_id: str) -> Run:
    return Run.from_file(_runs_dir(tmp_path) / run_id / "run.md")


def _target(tmp_path: Path, *, source: str, eid: str, **extra):
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"id: {eid}", f"source: {source}", "status: pending"]
    lines += [f"{k}: {v}" for k, v in extra.items()]
    (inbox_dir / f"{eid}.md").write_text(
        "---\n" + "\n".join(lines) + "\n---\nbody\n", encoding="utf-8",
    )
    event = protocol._read_event(inbox_dir / f"{eid}.md")
    return daemon._DispatchTarget(
        event=event, repo_root=tmp_path, inbox_dir=inbox_dir,
        responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
    )


# ── the defect, measured ─────────────────────────────────────────────


class TestAParkedStrandIsNotTheSeat:
    def test_a_parking_strand_leaves_its_parents_hold_alone(self, tmp_path):
        parent = _parent_parked_on_its_children(tmp_path, mail="evt-letter")
        _strand_hits_its_wall(tmp_path)

        persisted = _reread(tmp_path, parent.id)
        hold = persisted.meta["resource_hold"]
        assert hold["released"] is False, "the seat was closed by its own child"
        assert "superseded_by" not in hold
        assert persisted.status == resource_hold.RUN_STATUS

    def test_the_parents_mail_stays_the_parents(self, tmp_path):
        _parent_parked_on_its_children(tmp_path, mail="evt-letter")
        kid = _strand_hits_its_wall(tmp_path)

        assert _reread(tmp_path, parent_id := "run-parent").meta[
            "resource_hold"
        ]["accumulated_event_ids"] == ["evt-letter"], parent_id
        assert _reread(tmp_path, kid.id).meta[
            "resource_hold"
        ]["accumulated_event_ids"] == []

    def test_the_seat_is_still_the_parent(self, tmp_path):
        _parent_parked_on_its_children(tmp_path)
        _strand_hits_its_wall(tmp_path)

        seat = daemon._repo_seat(_runs_dir(tmp_path), account_home=tmp_path / ".brr")
        assert seat is not None and seat.id == "run-parent"

    def test_two_parked_strands_do_not_supersede_each_other(self, tmp_path):
        _strand_hits_its_wall(tmp_path, run_id="run-kid-a")
        _strand_hits_its_wall(tmp_path, run_id="run-kid-b")

        for run_id in ("run-kid-a", "run-kid-b"):
            hold = _reread(tmp_path, run_id).meta["resource_hold"]
            assert hold["released"] is False, run_id
        assert daemon._repo_seat(
            _runs_dir(tmp_path), account_home=tmp_path / ".brr",
        ) is None

    def test_the_childs_own_return_reaches_the_parent(self, tmp_path):
        """The end-to-end shape: the child parks, its return wakes the seat.

        Before the fix this event was deferred for the five-year horizon
        against the *child's* refill record — the parent asleep behind a
        bucket it does not draw from.
        """
        _parent_parked_on_its_children(tmp_path)
        _strand_hits_its_wall(tmp_path)
        ret = _target(
            tmp_path, source="spawn_completed", eid="evt-kid-ret",
            spawn_parent_run_id="run-parent", spawned_by_run="run-kid",
            conversation_key="cloud:telegram:1:",
        )

        survivors = daemon._handle_resource_held_events([ret], None)

        assert survivors == [ret]
        assert protocol._read_event(
            ret.inbox_dir / "evt-kid-ret.md",
        ).get("defer_reason") is None
        assert _reread(tmp_path, "run-parent").meta[
            "resource_hold"
        ]["released_by"] == "strand"

    def test_a_release_clears_the_parked_word_on_the_parents_row(self, tmp_path):
        """A thawed child must not keep reading `parked` on the seat's row."""
        kid = _strand_hits_its_wall(tmp_path)
        _register("evt-kid", "run-parent", child_run_id="run-kid")
        daemon._park_run_control(
            "evt-kid", daemon._parked_child_projection(_reread(tmp_path, "run-kid")),
        )
        assert daemon._parked_child_controls("run-parent")

        daemon.release_held_run(
            _reread(tmp_path, kid.id), by="refill", why="measured_refill",
        )

        assert daemon._parked_child_controls("run-parent") == []
        assert [r["run_id"] for r in daemon._working_child_controls("run-parent")] == [
            "run-kid",
        ]

    def test_a_parked_strand_still_thaws_on_its_own_refill(self, tmp_path):
        """The exclusion is seat-scoped, not a new way to strand a strand.

        ``_held_runs_for_repo`` — which the refill/reset sweep reads — must
        keep listing a parked strand, or the fix above would trade one
        permanent sleep for another.
        """
        kid = _strand_hits_its_wall(tmp_path)
        listed = [run.id for run in daemon._held_runs_for_repo(_runs_dir(tmp_path))]
        assert kid.id in listed
        assert kid.id not in [
            run.id for run in daemon._seat_held_runs_for_repo(_runs_dir(tmp_path))
        ]


# ── which children count as living ───────────────────────────────────


def _register(event_id, parent_run_id, *, child_run_id=None, title=""):
    daemon._register_run_control(event_id, parent_run_id, title=title)
    if child_run_id:
        daemon._bind_run_control(event_id, child_run_id)


class TestWorkingVersusParked:
    def _parked_kid(self, tmp_path, *, run_id="run-kid"):
        kid = _strand_hits_its_wall(tmp_path, run_id=run_id)
        _register(f"evt-{run_id}", "run-parent", child_run_id=run_id, title="the lift")
        parked = daemon._parked_child_projection(_reread(tmp_path, run_id))
        assert parked is not None
        assert daemon._park_run_control(f"evt-{run_id}", parked)
        return kid

    def test_a_parked_child_keeps_its_edge_and_says_so(self, tmp_path):
        self._parked_kid(tmp_path)
        rows = daemon._owned_child_controls("run-parent")
        assert [row["run_id"] for row in rows] == ["run-kid"]
        row = rows[0]
        assert row["status"] == "parked"
        assert row["hold_reason"] == resource_hold.REASON_QUOTA_STARVED
        assert row["hold_resume"] == resource_hold.RESUME_REFILL
        assert row["waiting_on"] == "a measured quota refill on its own bucket"
        assert row["hold_wall"] is True

    def test_working_read_drops_it_and_parked_read_keeps_it(self, tmp_path):
        self._parked_kid(tmp_path)
        _register("evt-live", "run-parent", child_run_id="run-live")

        assert [r["run_id"] for r in daemon._working_child_controls("run-parent")] == [
            "run-live",
        ]
        assert [r["run_id"] for r in daemon._parked_child_controls("run-parent")] == [
            "run-kid",
        ]

    def test_submitted_is_living(self, tmp_path):
        """A submit is a boundary, not an end — it still spends and steers."""
        _register("evt-sub", "run-parent", child_run_id="run-sub")
        with daemon._run_controls_lock:
            daemon._run_controls["evt-sub"]["submitted"] = True

        rows = daemon._working_child_controls("run-parent")
        assert [row["run_id"] for row in rows] == ["run-sub"]
        assert rows[0]["status"] == "submitted"

    def test_parked_outranks_submitted(self, tmp_path):
        """Submitted *then* parked is parked: it is not spending any more."""
        self._parked_kid(tmp_path)
        with daemon._run_controls_lock:
            daemon._run_controls["evt-run-kid"]["submitted"] = True

        assert daemon._owned_child_controls("run-parent")[0]["status"] == "parked"
        assert daemon._working_child_controls("run-parent") == []

    def test_a_projection_of_a_working_run_is_none(self, tmp_path):
        running = Run(id="run-busy", event_id="evt-b", body="", status="running")
        assert daemon._parked_child_projection(running) is None


# ── the bolt: the seat closes rather than sleeping on a sleeper ──────


class TestTheBoltDoesNotParkOnSleepers:
    def _declaration(self, **strands):
        return cut_verb.CutDeclaration(
            strands=tuple(
                cut_verb.StrandDisposition(run=run, disposition=disp)
                for run, disp in strands.items()
            ),
        )

    def _parent(self):
        parent = Run(id="run-parent", event_id="evt-p", body="", env="host")
        parent.meta["runner_shell"] = "claude"
        return parent

    def test_handoff_of_a_parked_child_is_an_ordinary_close(self, tmp_path):
        parent = self._parent()
        _strand_hits_its_wall(tmp_path)
        _register("evt-kid", parent.id, child_run_id="run-kid")
        daemon._park_run_control(
            "evt-kid", daemon._parked_child_projection(_reread(tmp_path, "run-kid")),
        )

        parked = daemon._park_bolt_on_live_strands(
            parent, self._declaration(**{"run-kid": "handoff — successor takes it"}),
            outbox_dir=tmp_path,
        )

        assert parked == []
        assert "pending_resource_hold" not in parent.meta

    def test_it_says_why_rather_than_going_quiet(self, tmp_path):
        parent = self._parent()
        _strand_hits_its_wall(tmp_path)
        _register("evt-kid", parent.id, child_run_id="run-kid")
        daemon._park_run_control(
            "evt-kid", daemon._parked_child_projection(_reread(tmp_path, "run-kid")),
        )
        outbox = tmp_path / "outbox"
        outbox.mkdir()

        daemon._park_bolt_on_live_strands(
            parent, self._declaration(**{"run-kid": "handoff — successor takes it"}),
            outbox_dir=outbox,
        )

        notices = daemon._read_outbox_notices(outbox)
        assert any("parked on a hold of its own" in str(n.get("text") or "")
                   for n in notices), notices

    def test_a_working_child_still_parks_the_seat(self, tmp_path):
        """The rule this machinery exists for is untouched."""
        parent = self._parent()
        _register("evt-kid", parent.id, child_run_id="run-kid")

        parked = daemon._park_bolt_on_live_strands(
            parent, self._declaration(**{"run-kid": "handoff — successor takes it"}),
            outbox_dir=tmp_path,
        )

        assert parked == ["run-kid"]
        assert parent.meta["pending_resource_hold"][
            "resume_condition"
        ] == resource_hold.RESUME_STRANDS

    def test_a_mixed_fleet_parks_on_the_working_one_only(self, tmp_path):
        parent = self._parent()
        _strand_hits_its_wall(tmp_path, run_id="run-asleep")
        _register("evt-asleep", parent.id, child_run_id="run-asleep")
        daemon._park_run_control(
            "evt-asleep",
            daemon._parked_child_projection(_reread(tmp_path, "run-asleep")),
        )
        _register("evt-awake", parent.id, child_run_id="run-awake")

        parked = daemon._park_bolt_on_live_strands(
            parent,
            self._declaration(**{
                "run-asleep": "handoff — still owed",
                "run-awake": "handoff — mid-flight",
            }),
            outbox_dir=tmp_path,
        )

        assert parked == ["run-awake"]


# ── the seat's own act: stop: closes a parked child for real ─────────


class TestTheSeatMayCloseAParkedChild:
    def _setup(self, tmp_path):
        _strand_hits_its_wall(tmp_path)
        _register("evt-kid", "run-parent", child_run_id="run-kid")
        daemon._park_run_control(
            "evt-kid", daemon._parked_child_projection(_reread(tmp_path, "run-kid")),
        )
        inbox = tmp_path / ".brr" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        return inbox

    def test_stop_consumes_the_hold_and_ends_the_run(self, tmp_path):
        inbox = self._setup(tmp_path)

        stage = daemon._apply_run_stop(
            daemon._run_controls["evt-kid"], inbox,
            stopped_by="run-parent", reason="the shell behind it dried up",
        )

        assert stage == "stopped-parked"
        kid = _reread(tmp_path, "run-kid")
        assert kid.status == "stopped"
        assert kid.meta["resource_hold"]["released"] is True
        assert kid.meta["stop_reason"] == "the shell behind it dried up"

    def test_stop_retires_the_edge(self, tmp_path):
        inbox = self._setup(tmp_path)
        daemon._apply_run_stop(
            daemon._run_controls["evt-kid"], inbox, stopped_by="run-parent",
        )
        assert daemon._owned_child_controls("run-parent") == []

    def test_stop_posts_the_completion_note_here(self, tmp_path):
        """No future is left to reap, so the notify cannot be deferred to one."""
        inbox = self._setup(tmp_path)
        daemon._apply_run_stop(
            daemon._run_controls["evt-kid"], inbox,
            stopped_by="run-parent", reason="drawing too much",
        )

        notes = [
            ev for ev in protocol.list_pending(inbox)
            if ev.get("source") == "spawn_completed"
        ]
        assert len(notes) == 1
        note = notes[0]
        assert note.get("spawn_parent_run_id") == "run-parent"
        assert note.get("spawn_status") == "stopped"
        assert note.get("spawn_was_parked") is True
        assert "parked on quota_starved" in note.get("body", "")

    def test_a_working_child_still_takes_the_kill_path(self, tmp_path, monkeypatch):
        killed = []
        monkeypatch.setattr(
            daemon.runner, "kill_matching", lambda prefix: killed.append(prefix),
        )
        _register("evt-live", "run-parent", child_run_id="run-live")
        inbox = tmp_path / ".brr" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        stage = daemon._apply_run_stop(
            daemon._run_controls["evt-live"], inbox, stopped_by="run-parent",
        )

        assert stage == "running"
        assert killed == ["evt-live-attempt-"]


# ── what the seat reads at its boundary ──────────────────────────────


class TestTheBoundarySurface:
    def _payload(self, **row):
        entry = {"parent_run_id": "run-parent", "run_id": "run-kid", **row}
        return {
            "run": {"id": "run-parent"},
            "resources": {"coexisting_runs": {"owned_children": [entry]}},
        }

    def _parked_payload(self):
        return self._payload(
            status="parked", title="the lift",
            hold_reason="quota_starved",
            waiting_on="a measured quota refill on its own bucket",
        )

    def test_the_row_names_the_child_its_reason_and_its_wait(self):
        line = hooks._parked_child_lines(self._parked_payload())[0]
        assert "run-kid" in line
        assert "the lift" in line
        assert "quota_starved" in line
        assert "a measured quota refill on its own bucket" in line

    def test_the_three_overrides_are_all_offered(self):
        line = hooks._parked_child_lines(self._parked_payload())[0]
        assert "`to: run-kid`" in line
        assert "`stop: run-kid`" in line
        assert "respawn" in line

    def test_the_respawn_is_priced_as_a_boot(self):
        line = hooks._parked_child_lines(self._parked_payload())[0]
        assert "boot" in line
        assert "cold read" in line
        assert "across Shells it cannot reopen the transcript" in line

    def test_nothing_executes_on_its_own(self):
        line = hooks._parked_child_lines(self._parked_payload())[0]
        assert "Nothing happens unless you say so." in line

    def test_a_working_child_renders_no_parked_row(self):
        assert hooks._parked_child_lines(self._payload()) == []
        assert hooks._parked_child_lines(self._payload(status="submitted")) == []

    def test_someone_elses_parked_child_is_not_mine(self):
        payload = self._parked_payload()
        payload["resources"]["coexisting_runs"]["owned_children"][0][
            "parent_run_id"
        ] = "run-elsewhere"
        assert hooks._parked_child_lines(payload) == []

    def test_a_parked_child_is_not_an_armed_spawn(self):
        """`_spawn_child_armed` gates the linger: a sleeper is not a worker."""
        assert hooks._spawn_child_armed(
            self._parked_payload(), "run-parent",
        ) is False
        assert hooks._spawn_child_armed(self._payload(), "run-parent") is True

    def test_the_handover_line_leaves_parked_rows_to_their_own_row(self):
        assert hooks._live_child_handover_line(self._parked_payload()) is None
        assert "run-kid" in (hooks._live_child_handover_line(self._payload()) or "")
