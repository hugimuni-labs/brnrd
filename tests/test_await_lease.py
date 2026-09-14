"""The wait that costs nothing — move 2c's lease, end to end.

``brnrd await`` used to return ``pending — call again`` every ~9m20s, and each
re-call was a full model turn. The lease holds one call until the daemon
resolves the wait or the lease ceiling passes. These tests drive the real
pieces together on a fake clock: the CLI stages the directive, the real
``_drain_outbox`` arms it (moving the Shuttle to ``listening``), the real
``_write_live_portal_state`` heartbeat evaluates it, and an event lands in the
inbox hours into the wait.
"""

from __future__ import annotations

import json
import time

import pytest

from brr import await_verb, cli, daemon, hooks, protocol, shuttle
from brr.cli import main
from brr.run import Run


# ── units ────────────────────────────────────────────────────────────────


def test_lease_ceiling_defaults_to_remaining_budget_capped_at_six_hours():
    assert await_verb.lease_ceiling(None) == await_verb.LEASE_MAX_SECONDS
    assert await_verb.lease_ceiling({"budget_seconds": None}) == await_verb.LEASE_MAX_SECONDS
    assert await_verb.lease_ceiling(
        {"budget_seconds": 3600, "elapsed_seconds": 600},
    ) == 3000
    assert await_verb.lease_ceiling(
        {"budget_seconds": 86400, "elapsed_seconds": 0},
    ) == await_verb.LEASE_MAX_SECONDS
    # --ceiling wins over the budget, and is still capped.
    assert await_verb.lease_ceiling(
        {"budget_seconds": 3600, "elapsed_seconds": 600}, 1800,
    ) == 1800
    assert await_verb.lease_ceiling(None, 48 * 3600) == await_verb.LEASE_MAX_SECONDS


def test_the_widened_shell_cap_covers_a_full_lease_with_margin():
    cap = await_verb.CLAUDE_BASH_MAX_TIMEOUT_MS / 1000
    assert cap - await_verb.CALL_CAP_MARGIN_SECONDS > await_verb.LEASE_MAX_SECONDS


@pytest.mark.parametrize("command,expected", [
    ("brnrd await", True),
    ("brnrd await --file /tmp/gate.log", True),
    ("cd /x && brnrd await --ceiling 30m", True),
    ("/opt/bin/brnrd await", True),
    ("brnrd do --mood calm", False),
    ("echo brnrd awaits", False),
    ("await", False),
    (None, False),
])
def test_is_await_command(command, expected):
    assert await_verb.is_await_command(command) is expected


def test_format_slept():
    assert await_verb.format_slept(12.4) == "12s"
    assert await_verb.format_slept(41 * 60 + 5) == "41m"
    assert await_verb.format_slept(3 * 3600 + 12 * 60) == "3h12m"
    assert await_verb.format_slept(2 * 3600) == "2h"


def test_lease_record_round_trip_and_chip(tmp_path):
    assert await_verb.read_lease_record(tmp_path) is None
    await_verb.write_lease_record(
        tmp_path, generation="g1", slept_seconds=11520.2, outcome="event",
    )
    record = await_verb.read_lease_record(tmp_path)
    assert record is not None
    assert await_verb.lease_chip(record) == "slept 3h12m · woke: event"
    assert not list(tmp_path.glob("*.tmp"))


# ── the lease, driven through the real drain and heartbeat ───────────────


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.hooks = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        for hook in self.hooks:
            hook()


class _Seat:
    """A resident seat with a real inbox/outbox, Shuttle and heartbeat."""

    def __init__(self, tmp_path, monkeypatch, *, stamped_cap_ms=None):
        self.brr_dir = tmp_path / ".brr"
        self.inbox = self.brr_dir / "inbox"
        self.responses = self.brr_dir / "responses"
        self.inbox.mkdir(parents=True)
        own = protocol.create_event(self.inbox, "telegram", "original", status="processing")
        self.eid = own.stem
        self.outbox = self.brr_dir / "outbox" / self.eid
        self.outbox.mkdir(parents=True)
        self.task = Run(id="run-parent", event_id=self.eid, body="original", source="telegram")
        shuttle.Shuttle.load(self.brr_dir).transition(
            "awake", why="event_dispatched", run_id=self.task.id,
        )
        self.states: list[tuple[float, str]] = []
        self.clock = _Clock()
        monkeypatch.setattr(time, "sleep", self.clock.sleep)
        monkeypatch.setattr(time, "monotonic", self.clock.monotonic)
        # A minute per poll keeps a multi-hour lease to a few hundred
        # heartbeats; the stat gate and the loop body are the real ones.
        monkeypatch.setattr(cli, "_AWAIT_POLL_INTERVAL_SECONDS", 60.0)
        monkeypatch.delenv("BRR_RUNNER", raising=False)
        if stamped_cap_ms is None:
            monkeypatch.delenv(await_verb.CALL_CAP_ENV, raising=False)
        else:
            monkeypatch.setenv(await_verb.CALL_CAP_ENV, str(stamped_cap_ms))
        self.beat()
        self.clock.hooks.append(self.beat)

    def beat(self):
        if list(self.outbox.glob("*.md")):
            daemon._drain_outbox(
                daemon._WorkerEmit(self.brr_dir, None, self.eid),
                self.task, self.responses, self.eid, self.outbox, self.inbox,
            )
        daemon._write_live_portal_state(
            self.outbox, self.inbox, self.eid, self.task,
            phase="running", shuttle_home=self.brr_dir,
        )
        self.states.append((self.clock.now, shuttle.Shuttle.load(self.brr_dir).state))

    def at(self, seconds, act):
        fired = {"done": False}

        def hook():
            if not fired["done"] and self.clock.now >= seconds:
                fired["done"] = True
                act()

        # Before the heartbeat, so the event is in the inbox the beat reads.
        self.clock.hooks.insert(0, hook)

    def shuttle_state(self):
        return shuttle.Shuttle.load(self.brr_dir).state

    def run(self, capsys, *argv):
        assert main(["await", "--outbox", str(self.outbox), "--json", *argv]) == 0
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


_FULL_CAP = await_verb.CLAUDE_BASH_MAX_TIMEOUT_MS
_TWO_HOURS = 2 * 3600


def _inject_message(seat):
    protocol.create_event(seat.inbox, "telegram", "still there?")


def _inject_strand_return(seat):
    protocol.create_event(
        seat.inbox, "spawn_completed", "child finished",
        spawn_parent_run_id=seat.task.id,
    )


def _inject_schedule_firing(seat):
    protocol.create_event(seat.inbox, "schedule", "every: 1h — tend the garden")


@pytest.mark.parametrize("inject", [
    _inject_message, _inject_strand_return, _inject_schedule_firing,
], ids=["event", "strand_return", "schedule_firing"])
def test_the_lease_holds_past_the_old_ten_minute_mark_and_returns_on_a_wake(
    tmp_path, monkeypatch, capsys, inject,
):
    seat = _Seat(tmp_path, monkeypatch, stamped_cap_ms=_FULL_CAP)
    seat.at(_TWO_HOURS, lambda: inject(seat))

    result = seat.run(capsys)

    assert result["outcome"] == "event"
    # Not the old slice: nothing returned at 560s / 600s.
    assert seat.clock.now >= _TWO_HOURS
    assert seat.clock.now < _TWO_HOURS + 5 * 60
    # `listening` for the whole held stretch (from the arm to the wake) ...
    held = [state for at, state in seat.states if 60 <= at < _TWO_HOURS]
    assert held and set(held) == {"listening"}
    # ... and `awake` after, through the existing transition.
    assert seat.shuttle_state() == "awake"
    whys = [row["why"] for row in shuttle.Shuttle.load(seat.brr_dir).transitions]
    assert whys[-2:] == ["await_armed", "await_resolved:event"]
    record = await_verb.read_lease_record(seat.outbox)
    assert record["outcome"] == "event"
    assert record["slept_seconds"] >= _TWO_HOURS


def test_the_lease_returns_pending_at_its_ceiling_and_the_wait_stands(
    tmp_path, monkeypatch, capsys,
):
    seat = _Seat(tmp_path, monkeypatch, stamped_cap_ms=_FULL_CAP)

    result = seat.run(capsys, "--ceiling", "3h")

    assert result["outcome"] == "pending"
    assert result["returned_on"] == "ceiling"
    assert 3 * 3600 <= seat.clock.now < 3 * 3600 + 120
    # The arming is untouched: still listening, still unresolved.
    assert seat.shuttle_state() == "listening"
    assert seat.task.meta["await"]["resolved"] is False
    assert await_verb.read_lease_record(seat.outbox)["outcome"] == "ceiling"


def test_the_default_ceiling_is_six_hours_without_a_budget(
    tmp_path, monkeypatch, capsys,
):
    seat = _Seat(tmp_path, monkeypatch, stamped_cap_ms=_FULL_CAP)

    result = seat.run(capsys)

    assert result["returned_on"] == "ceiling"
    assert await_verb.LEASE_MAX_SECONDS <= seat.clock.now
    assert seat.clock.now < await_verb.LEASE_MAX_SECONDS + 120


def test_a_stamped_shell_cap_under_the_lease_returns_before_the_kill(
    tmp_path, monkeypatch, capsys,
):
    """An operator's own narrower BASH_MAX_TIMEOUT_MS (or an older daemon
    that never widened it): the hook stamps the real cap, and the call
    answers `pending` just under it instead of being backgrounded."""
    seat = _Seat(tmp_path, monkeypatch, stamped_cap_ms=600_000)

    result = seat.run(capsys)

    assert result["outcome"] == "pending"
    assert result["returned_on"] == "shell_cap"
    assert seat.clock.now <= 600 - await_verb.CALL_CAP_MARGIN_SECONDS + 60
    assert seat.shuttle_state() == "listening"


# ── the hook half: the call's timeout, raised and stamped ────────────────


def _pre_tool(tmp_path, command, *, flavour="claude", extra_env=None, **tool_input):
    env = {
        "BRR_RUN_ID": "run-1",
        "BRR_RUNNER": flavour,
        "BRR_OUTBOX_DIR": str(tmp_path),
        **(extra_env or {}),
    }
    payload = {"tool_name": "Bash", "tool_input": {"command": command, **tool_input}}
    return hooks.run_hook(hooks.PHASE_PRE_TOOL, json.dumps(payload), env)


def test_pre_tool_raises_and_stamps_an_await_calls_timeout(tmp_path):
    out, code = _pre_tool(
        tmp_path, "brnrd await --file /tmp/gate.log", timeout=600000,
        extra_env={"BASH_MAX_TIMEOUT_MS": str(_FULL_CAP)},
    )
    assert code == 0
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in spec
    updated = spec["updatedInput"]
    assert updated["timeout"] == _FULL_CAP
    assert updated["command"] == (
        f"export {await_verb.CALL_CAP_ENV}={_FULL_CAP}; brnrd await --file /tmp/gate.log"
    )


def test_pre_tool_stamps_claudes_default_cap_when_the_daemon_did_not_widen_it(tmp_path):
    out, _ = _pre_tool(tmp_path, "brnrd await")
    updated = out["hookSpecificOutput"]["updatedInput"]
    assert updated["timeout"] == 600_000
    assert f"{await_verb.CALL_CAP_ENV}=600000;" in updated["command"]


@pytest.mark.parametrize("case", [
    {"command": "pytest -q"},
    {"command": "brnrd await", "flavour": "codex"},
    {"command": "brnrd await", "run_in_background": True},
    {"command": f"export {await_verb.CALL_CAP_ENV}=1; brnrd await"},
])
def test_pre_tool_leaves_every_other_bash_call_alone(tmp_path, case):
    case = dict(case)
    command = case.pop("command")
    flavour = case.pop("flavour", "claude")
    out, code = _pre_tool(tmp_path, command, flavour=flavour, **case)
    assert (out, code) == ({}, 0)


def test_pre_tool_needs_a_daemon_outbox(tmp_path):
    payload = {"tool_name": "Bash", "tool_input": {"command": "brnrd await"}}
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL, json.dumps(payload), {"BRR_RUNNER": "claude"},
    )
    assert (out, code) == ({}, 0)


def test_claude_settings_route_bash_through_the_pre_tool_hook():
    settings = hooks._claude_hook_settings("brnrd")
    matcher = settings["hooks"]["PreToolUse"][0]["matcher"]
    assert "Bash" in matcher.split("|")


# ── the chip: slept … · woke: …, once ────────────────────────────────────


def _portal(tmp_path, token):
    payload = {
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"current_event": "evt-1", "current_event_replyable": True, "events": []},
        "outbound": {"replies_current": 0, "replies_other": 0, "outbound_messages": 0},
        "change_token": token,
    }
    (tmp_path / "portal-state.json").write_text(json.dumps(payload), encoding="utf-8")


def _post_tool(tmp_path):
    env = {
        "BRR_RUN_ID": "run-1",
        "BRR_EVENT_ID": "evt-1",
        "BRR_RUNNER": "claude",
        "BRR_OUTBOX_DIR": str(tmp_path),
        "BRR_PORTAL_STATE": str(tmp_path / "portal-state.json"),
    }
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    return (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""


def test_the_first_boundary_after_a_lease_says_how_long_and_why_once(tmp_path):
    _portal(tmp_path, "t1")
    _post_tool(tmp_path)  # an ordinary boundary before any lease

    await_verb.write_lease_record(
        tmp_path, generation="g1", slept_seconds=3 * 3600 + 12 * 60, outcome="event",
    )
    _portal(tmp_path, "t2")
    first = _post_tool(tmp_path)
    assert "slept 3h12m · woke: event" in first

    _portal(tmp_path, "t3")
    second = _post_tool(tmp_path)
    assert "slept" not in second

    # The next lease is news again, even with the same duration and outcome.
    time.sleep(0.01)
    await_verb.write_lease_record(
        tmp_path, generation="g2", slept_seconds=3 * 3600 + 12 * 60, outcome="event",
    )
    _portal(tmp_path, "t4")
    assert "slept 3h12m · woke: event" in _post_tool(tmp_path)


def test_the_lease_chip_is_a_documented_delta_segment():
    assert hooks.SEGMENT_CLASS["lease_wake"] == hooks.DELTA
