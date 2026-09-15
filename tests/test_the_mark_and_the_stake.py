"""Move 4b — the mark and the stake (design-the-loom §6, §16).

Each seam driven through the function that owns it: the grammar and the
meter (``brr.stake``), the boundary's judgement (``daemon._stake_facet``),
the park and its one releaser (``daemon._handle_resource_held_events``), the
chip and the boundary row (``hooks``), the stake riding ``spawn:`` and
``respawn:``, and ``mark:`` / ``stake: refuse`` through the drain. The drain
goldens (``test_outbox_drain_golden.py``, ``mark_*`` / ``stake_*`` /
``cut_at_alone``) pin the files each verb leaves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import account, daemon, facets, hooks, protocol, resource_hold, stake
from brr.outbox import mark
from brr.run import Run
import importlib


@pytest.fixture(autouse=True)
def _controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _seat() -> Run:
    task = Run(id="run-seat", event_id="evt-seat", body="", env="host", source="telegram")
    task.meta.update({"runner_name": "claude-fable", "runner_shell": "claude", "runner_core": "fable"})
    task.conversation_key = "telegram:42:"
    return task


# ── the grammar and the unit ─────────────────────────────────────────


class TestGrammar:
    def test_a_share_normalises_against_the_default_window(self):
        row = stake.normalise("5%", None, cfg={})
        assert (row["tokens"], row["share_pct"], row["unit"]) == (5_000_000, 5.0, "share")
        assert (row["cut_at_tokens"], row["cut_at_share_pct"], row["cut_at_basis"]) == (7_500_000, 7.5, "default")
        assert (row["window_tokens"], row["window_basis"]) == (100_000_000, "default")

    def test_tokens_keep_the_share_beside_them(self):
        row = stake.normalise("2m", "3m", cfg={"stake.window_tokens": "40m"})
        assert (row["tokens"], row["share_pct"], row["unit"]) == (2_000_000, 5.0, "tokens")
        assert (row["cut_at_tokens"], row["cut_at_share_pct"], row["cut_at_basis"]) == (3_000_000, 7.5, "given")
        assert row["window_basis"] == "config"

    def test_an_explicit_cut_at_in_share(self):
        row = stake.normalise("5%", "12%", cfg={})
        assert row["cut_at_tokens"] == 12_000_000

    @pytest.mark.parametrize(("value", "cut"), [("lots", None), ("0%", None), ("150%", None), ("5%", "3%"), ("5%", "soon")])
    def test_what_the_grammar_does_not_read_is_refused(self, value, cut):
        with pytest.raises(stake.StakeError):
            stake.normalise(value, cut, cfg={})

    def test_a_request_in_frontmatter_or_a_messages_lead_lines(self):
        assert stake.request_from({"stake": "5%", "cut_at": "8%"}) == {"stake": "5%", "cut_at": "8%"}
        assert stake.request_from({}, "stake: 5%\ncut-at: 9%\n\nfix the drain") == {"stake": "5%", "cut_at": "9%"}
        assert stake.request_from({}, "fix the drain\nstake: 5%") is None
        assert stake.request_from({}, "cut-at: 9%\nfix") is None
        assert stake.request_from({"stake": "5%", "stake_refused": "no"}) is None

    def test_the_meter_folds_positive_deltas_and_survives_a_window_roll(self):
        row = stake.normalise("1m", None, cfg={})
        row.update(state="armed", spent=0, last_reading=None)
        stake.meter(row, 300_000)
        stake.meter(row, 500_000)
        stake.meter(row, None)
        assert row["spent"] == 500_000
        stake.meter(row, 100_000)  # the seat's window rolled; the reading restarted
        assert row["spent"] == 600_000
        assert row["pct"] == 60.0 and row["spent_share_pct"] == 0.6

    def test_the_chip_speaks_the_unit_it_was_given(self):
        row = stake.normalise("5%", None, cfg={})
        row.update(state="armed", spent=1_100_000)
        assert stake.chip(row) == "stake 1.1m/5% · 22%"
        row = stake.normalise("2m", None, cfg={})
        row.update(state="armed", spent=500_000)
        assert stake.chip(row) == "stake 500k/2m · 25%"
        assert stake.chip({**row, "state": "refused"}) is None


# ── the boundary's judgement ─────────────────────────────────────────


class TestStakeFacet:
    def test_the_waking_events_stake_arms_and_meters(self, tmp_path):
        task = _seat()
        task.meta["stake_request"] = {"stake": "5%", "cut_at": "", "event_id": "evt-seat"}
        task.meta["resident_allowance_spent"] = 0
        _state, row = daemon._stake_facet(task, None, {}, [], tmp_path)
        assert row["state"] == "armed" and row["spent"] == 0 and row["event_id"] == "evt-seat"
        task.meta["resident_allowance_spent"] = 1_100_000
        _state, row = daemon._stake_facet(task, None, {}, [], tmp_path)
        assert row["spent"] == 1_100_000 and row["pct"] == 22.0
        notices = daemon._read_outbox_notices(tmp_path)
        assert notices[0]["kind"] == "advisory" and notices[0]["text"].startswith("stake armed: stake 0/5%")

    def test_the_cut_stamps_the_hold_and_parks_the_await(self, tmp_path):
        task = _seat()
        task.meta["stake_request"] = {"stake": "1m", "cut_at": "", "event_id": "evt-seat"}
        task.meta["resident_allowance_spent"] = 0
        task.meta["await"] = {"armed": True, "resolved": False}
        daemon._stake_facet(task, None, {}, [], tmp_path)
        task.meta["resident_allowance_spent"] = 1_600_000
        state, row = daemon._stake_facet(task, {"armed": True, "resolved": False}, {}, [], tmp_path)
        assert row["state"] == "cut"
        hold = task.meta["pending_resource_hold"]
        assert hold["reason"] == resource_hold.REASON_STAKE_CUT
        assert hold["resume_condition"] == resource_hold.RESUME_RAISE
        assert "1.6m spent of 1.5m" in hold["detail"]
        assert state["outcome"] == "park" and state["stake_cut"] is True
        assert task.meta["await"]["outcome"] == "park"

    def test_no_stake_no_row(self, tmp_path):
        assert daemon._stake_facet(_seat(), {"armed": True}, {}, [], tmp_path) == ({"armed": True}, None)

    def test_a_strand_is_never_judged_here(self, tmp_path):
        task = _seat()
        task.meta["strand"] = True
        task.meta["stake_request"] = {"stake": "1m", "event_id": "e"}
        assert daemon._stake_facet(task, None, {}, [], tmp_path) == (None, None)

    def _armed(self, tmp_path) -> Run:
        task = _seat()
        task.meta["stake_request"] = {"stake": "1m", "cut_at": "", "event_id": "evt-seat"}
        task.meta["resident_allowance_spent"] = 0
        daemon._stake_facet(task, None, {}, [], tmp_path)
        task.meta["resident_allowance_spent"] = 1_600_000
        daemon._stake_facet(task, None, {}, [], tmp_path)
        assert "pending_resource_hold" in task.meta
        return task

    def test_an_owners_raise_on_the_thread_keeps_the_spend_and_lifts_the_cut(self, tmp_path):
        task = self._armed(tmp_path)
        raise_event = {
            "id": "evt-raise", "source": "telegram", "trust_tier": "owner",
            "conversation_key": "telegram:42:", "body": "stake: 3m\n\ncarry on",
        }
        # The boundary the raise arrives on spent 50k more — charged first.
        task.meta["resident_allowance_spent"] = 1_650_000
        _state, row = daemon._stake_facet(task, None, {}, [raise_event], tmp_path)
        assert row["state"] == "armed" and row["tokens"] == 3_000_000
        assert row["spent"] == 1_650_000 and row["raises"] == 1
        assert "pending_resource_hold" not in task.meta
        # Spend from here on moves it; the raise pinned the meter's reading.
        task.meta["resident_allowance_spent"] = 1_700_000
        _state, row = daemon._stake_facet(task, None, {}, [raise_event], tmp_path)
        assert row["spent"] == 1_700_000

    def test_a_stranger_cannot_raise(self, tmp_path):
        task = self._armed(tmp_path)
        event = {"id": "evt-x", "source": "telegram", "conversation_key": "telegram:42:", "stake": "9m"}
        _state, row = daemon._stake_facet(task, None, {}, [event], tmp_path)
        assert row["state"] == "cut" and "pending_resource_hold" in task.meta
        assert daemon._read_outbox_notices(tmp_path)[-1]["text"].startswith("stake refused: evt-x — a raise")

    def test_a_stake_on_another_thread_is_not_this_asks(self, tmp_path):
        task = _seat()
        event = {"id": "evt-o", "source": "telegram", "trust_tier": "owner",
                 "conversation_key": "telegram:99:", "stake": "5%"}
        assert daemon._stake_facet(task, None, {}, [event], tmp_path)[1] is None


# ── the park, and the one word that ends it ──────────────────────────


class TestTheCutAtTheDoor:
    def _held(self, tmp_path, *, spent: int = 1_600_000) -> Run:
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        task = _seat()
        task.id = "run-cut"
        task.status = resource_hold.RUN_STATUS
        row = stake.normalise("1m", None, cfg={})
        row.update(state="cut", spent=spent)
        task.meta["stake"] = row
        task.meta["resource_hold"] = resource_hold.build(**daemon._stake_hold_spec(task, row))
        task.save(runs_dir)
        return task

    def _target(self, tmp_path, eid: str, body: str, **meta) -> "daemon._DispatchTarget":
        inbox_dir = tmp_path / ".brr" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        extra = "".join(f"{k}: {v}\n" for k, v in meta.items())
        (inbox_dir / f"{eid}.md").write_text(
            f"---\nid: {eid}\nsource: telegram\nstatus: pending\n{extra}---\n{body}\n",
            encoding="utf-8",
        )
        responses = tmp_path / ".brr" / "responses"
        responses.mkdir(parents=True, exist_ok=True)
        return daemon._DispatchTarget(
            event=protocol._read_event(inbox_dir / f"{eid}.md"), repo_root=tmp_path,
            inbox_dir=inbox_dir, responses_dir=responses, repo_label="home",
        )

    def _partial(self, target) -> str:
        return "\n".join(
            protocol.read_partial(p) or "" for p in sorted(target.responses_dir.rglob("*"))
            if p.is_file() and target.event["id"] in str(p)
        )

    def _persisted(self, tmp_path, held: Run) -> Run:
        return Run.from_file(tmp_path / ".brr" / "runs" / held.id / "run.md")

    def test_the_hold_is_a_wall_a_tick_does_not_wake(self, tmp_path):
        held = self._held(tmp_path)
        meta = held.meta["resource_hold"]
        assert resource_hold.awaits_raise(meta) and resource_hold.is_resource_wall(meta)
        assert not resource_hold.schedule_event_releases(meta, {"source": "schedule"})
        assert not resource_hold.refuses_correspondent(meta)

    def test_the_parks_reply_is_the_terms(self, tmp_path):
        body = daemon._hold_body(self._held(tmp_path).meta["resource_hold"])
        assert body.startswith("Parked at the stake's cut-at — 1.6m spent of 1.5m (1.5% of the window)")
        assert "`stake: 8%`" in body

    def test_a_message_without_a_stake_is_kept_and_answered(self, tmp_path):
        held = self._held(tmp_path)
        target = self._target(tmp_path, "evt-hi", "are you there?", trust_tier="owner")
        assert daemon._handle_resource_held_events([target], None) == []
        hold = self._persisted(tmp_path, held).meta["resource_hold"]
        assert hold["released"] is False and "evt-hi" in hold["accumulated_event_ids"]
        assert protocol._read_event(target.inbox_dir / "evt-hi.md")["defer_reason"] == "resource_hold"
        partial = self._partial(target)
        assert partial.startswith("Parked at the stake's cut-at") and "Not raised" not in partial

    def test_an_owners_raise_releases_and_rides_the_event(self, tmp_path):
        held = self._held(tmp_path)
        target = self._target(tmp_path, "evt-up", "stake: 3m\ncut-at: 4m\n\ngo on", trust_tier="owner")
        assert daemon._handle_resource_held_events([target], None) == [target]
        assert self._persisted(tmp_path, held).meta["resource_hold"]["released_by"] == "raise"
        event = protocol._read_event(target.inbox_dir / "evt-up.md")
        assert (event["stake"], event["cut_at"], str(event["stake_carried_spent"]), event["stake_carried_from"]) == (
            "3m", "4m", "1600000", "run-cut",
        )

    def test_a_stranger_or_a_cut_under_the_spend_is_not_a_raise(self, tmp_path):
        held = self._held(tmp_path)
        stranger = self._target(tmp_path, "evt-s", "stake: 9m")
        low = self._target(tmp_path, "evt-l", "stake: 1m", trust_tier="owner")
        assert daemon._handle_resource_held_events([stranger, low], None) == []
        assert "Not raised — a raise spends more of the window" in self._partial(stranger)
        assert "is not above what this ask already spent (1.6m)" in self._partial(low)
        assert self._persisted(tmp_path, held).meta["resource_hold"]["released"] is False

    def test_the_resumed_run_arms_with_the_spend_carried(self, tmp_path):
        task = _seat()
        task.meta["stake_request"] = {"stake": "3m", "cut_at": "", "event_id": "evt-up", "carried_spent": "1600000"}
        task.meta["resident_allowance_spent"] = 0
        _state, row = daemon._stake_facet(task, None, {}, [], tmp_path)
        assert (row["state"], row["spent"], row["raises"]) == ("armed", 1_600_000, 1)
        assert daemon._read_outbox_notices(tmp_path)[0]["text"].startswith("stake raised:")


def test_prepare_reads_the_waking_events_stake_from_the_message(tmp_path):
    """The stamp `worker/prepare.py` makes is `stake.request_from` on the event."""
    event = {"id": "evt-1", "body": "stake: 5%\n\nfix it", "stake_carried_spent": ""}
    assert stake.request_from(event, event["body"]) == {"stake": "5%", "cut_at": ""}
    prepare_mod = importlib.import_module("brr.worker.prepare")
    assert "stake_mod.request_from(event" in Path(prepare_mod.__file__).read_text(encoding="utf-8")


# ── the chip, the portal facet, the boundary row ─────────────────────


def _resources(stake_row: dict | None) -> dict:
    allowance = {"tokens": 20_000_000, "spent": 3_000_000, "scope": "resident", "explicit": False}
    if stake_row is not None:
        allowance["stake"] = stake_row
    return facets.build(allowance=allowance)


def _armed_row(**over) -> dict:
    row = stake.normalise("5%", None, cfg={})
    row.update(state="armed", spent=1_100_000, spent_share_pct=1.1, pct=22.0)
    row.update(over)
    return row


class TestTheSurfaces:
    def test_the_facet_carries_the_stake_and_the_chip_reads_it(self):
        resources = _resources(_armed_row())
        facet = resources["allowance"]
        assert facet["stake"]["state"] == "armed" and facet["stake_summary"] == "stake 1.1m/5% · 22%"
        assert hooks._allowance_chip(resources) == "stake 1.1m/5% · 22%"

    def test_no_stake_leaves_the_seat_chip_and_facet_as_they_were(self):
        resources = _resources(None)
        assert "stake" not in resources["allowance"]
        assert hooks._allowance_chip(resources).startswith("spend 3m · pace")

    def test_every_boundary_row_carries_the_spend_against_the_stake(self):
        readings = hooks._boundary_readings({"resources": _resources(_armed_row())}, None, None)
        assert readings["spend"]["stake"] == {
            "state": "armed", "spent": 1_100_000, "spent_share_pct": 1.1,
            "tokens": 5_000_000, "share_pct": 5.0, "cut_at_tokens": 7_500_000,
        }
        assert "stake" not in hooks._boundary_readings({"resources": _resources(None)}, None, None)["spend"]

    def test_the_cut_line_fires_once(self):
        resources = _resources(_armed_row(state="cut", spent=7_600_000))
        line, gate = hooks._stake_directive(resources)
        assert line.startswith("- stake cut-at reached (7.6m of 7.5m)") and gate == "cut:7500000"
        assert hooks._stake_directive(_resources(_armed_row())) == (None, "")


# ── the stake riding spawn: and respawn: ─────────────────────────────


def _spawn_drive(tmp_path, monkeypatch, fm: str):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "telegram", "go", status="processing")
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    monkeypatch.setattr(daemon.updates, "emit", lambda *_a, **_k: None)
    task = Run(id="run-seat", event_id=own.stem, body="go", source="telegram")
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    report = tmp_path / "r.md"
    ok = daemon._queue_spawn_request(
        emit, task, inbox, own.stem,
        protocol.parse_outbox_message(f"---\nspawn: true\nbranch: brr/c\nreport: {report}\n{fm}---\nx\n")[0],
        "x", outbox,
    )
    children = [protocol._read_event(p) for p in inbox.glob("*.md") if p.stem != own.stem]
    return ok, children, daemon._read_outbox_notices(outbox)


class TestSpawnAndRespawn:
    def test_a_stake_on_a_spawn_is_its_allowance(self, tmp_path, monkeypatch):
        ok, (child,), _ = _spawn_drive(tmp_path, monkeypatch, "stake: 2%\ncut-at: 3%\n")
        assert ok
        assert int(child["spawn_allowance_tokens"]) == 2_000_000
        assert int(child["spawn_stake_cut_at_tokens"]) == 3_000_000

    def test_both_ceilings_or_a_bad_stake_refuse_the_spawn(self, tmp_path, monkeypatch):
        ok, children, notices = _spawn_drive(tmp_path, monkeypatch, "stake: 2%\nallowance: 5m\n")
        assert not ok and not children and "name one" in notices[-1]["text"]

    def test_a_bad_stake_refuses_the_spawn(self, tmp_path, monkeypatch):
        ok, _children, notices = _spawn_drive(tmp_path, monkeypatch, "stake: plenty\n")
        assert not ok and notices[-1]["text"].startswith("spawn refused: stake: 'plenty'")

    def test_a_respawn_carries_the_stake_and_what_it_spent(self, tmp_path, monkeypatch):
        brr_dir = tmp_path / ".brr"
        inbox = brr_dir / "inbox"
        inbox.mkdir(parents=True)
        own = protocol.create_event(inbox, "telegram", "go", status="processing")
        outbox = brr_dir / "outbox" / own.stem
        outbox.mkdir(parents=True)
        monkeypatch.setattr(daemon.updates, "emit", lambda *_a, **_k: None)
        task = Run(id="run-seat", event_id=own.stem, body="go", source="telegram")
        row = stake.normalise("5%", None, cfg={})
        row.update(state="armed", spent=900_000)
        task.meta["stake"] = row
        emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
        fm = protocol.parse_outbox_message("---\nrespawn: true\nshell: claude\nstake: 8%\n---\ncontinue\n")[0]
        assert daemon._queue_respawn_request(emit, task, tmp_path, inbox, own.stem, fm, "continue", outbox)
        (child,) = [protocol._read_event(p) for p in inbox.glob("*.md") if p.stem != own.stem]
        assert (child["stake"], str(child["stake_carried_spent"]), child["stake_carried_from"]) == ("8%", "900000", "run-seat")


# ── mark: and stake: refuse through the drain ───────────────────────


def _drain(tmp_path, monkeypatch, name: str, text: str, *, meta: dict | None = None, own_meta: dict | None = None):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    inbox.mkdir(parents=True, exist_ok=True)
    own = protocol.create_event(inbox, "telegram", "look", status="processing", **(own_meta or {}))
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda *_a, **_k: None)
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    ctx = account.resolve_context(repo, {"home.path": str(tmp_path / "home"), "repo.label": "hugimuni-labs/brnrd"})
    task = Run(id="run-seat", event_id=own.stem, body="look", source="telegram",
               meta={"repo_label": "hugimuni-labs/brnrd", **(meta or {})})
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    stats: dict[str, int] = {}
    promoted = daemon._drain_outbox(emit, task, responses, own.stem, outbox, inbox,
                                    repo_root=repo, account_context=ctx, stats=stats)
    return {"promoted": promoted, "notices": daemon._read_outbox_notices(outbox), "task": task,
            "inbox": inbox, "own": own.stem, "home": account.context_home_root(ctx)}


def _fold(home: Path, place: str = "src/brr/x.py", commit: str = "c0ffee") -> Path:
    path = home / "bench" / "hugimuni-labs__brnrd" / place / f"{commit}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nplace: {place}\ncommit: {commit}\n---\nbody\n", encoding="utf-8")
    return path


class TestMark:
    def test_keep_then_keep_again_appends_twice(self, tmp_path, monkeypatch):
        path = _fold(tmp_path / "home")
        _drain(tmp_path, monkeypatch, "m.md", "---\nmark: keep bench/hugimuni-labs__brnrd/src/brr/x.py/c0ffee.md\n---\n")
        (tmp_path / ".brr").rename(tmp_path / ".brr-1")
        result = _drain(tmp_path, monkeypatch, "m.md", f"---\nmark: KEEP {path}\n---\n")
        front = path.read_text(encoding="utf-8")
        assert front.count("mark: keep\n") == 2 and front.endswith("---\nbody\n")
        (ask,) = [protocol._read_event(p) for p in result["inbox"].glob("*.md") if p.stem != result["own"]]
        assert (ask["source"], ask["focus_place"], ask["focus_bench_path"]) == ("mark", "src/brr/x.py", str(path))

    @pytest.mark.parametrize(("value", "needle"), [
        ("keep hugimuni-labs__brnrd/src/brr/nope/c0ffee", "no bench file at"),
        ("keep ../../etc/passwd", "is not a path inside the bench"),
        ("promote hugimuni-labs__brnrd/src/brr/x.py/c0ffee", "mark dropped: `mark: promote"),
        ("keep", "is not a path inside the bench"),
    ])
    def test_refusals(self, tmp_path, monkeypatch, value, needle):
        _fold(tmp_path / "home")
        result = _drain(tmp_path, monkeypatch, "m.md", f"---\nmark: {value}\n---\n")
        assert result["promoted"] == 0 and needle in result["notices"][-1]["text"]

    def test_a_dropped_file_cannot_be_marked_again(self, tmp_path, monkeypatch):
        dropped = tmp_path / "home" / "bench" / "hugimuni-labs__brnrd" / "dropped" / "src" / "c0ffee.md"
        dropped.parent.mkdir(parents=True)
        dropped.write_text("---\nplace: src\n---\n", encoding="utf-8")
        result = _drain(tmp_path, monkeypatch, "m.md", f"---\nmark: drop {dropped}\n---\n")
        assert "is already dropped" in result["notices"][-1]["text"]

    def test_a_symlink_out_of_the_bench_is_outside_it(self, tmp_path, monkeypatch):
        outside = tmp_path / "secret.md"
        outside.write_text("---\n---\nkey\n", encoding="utf-8")
        link = tmp_path / "home" / "bench" / "hugimuni-labs__brnrd" / "x" / "c0ffee.md"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        result = _drain(tmp_path, monkeypatch, "m.md", f"---\nmark: keep {link}\n---\n")
        assert "is not a path inside the bench" in result["notices"][-1]["text"]
        assert outside.read_text(encoding="utf-8") == "---\n---\nkey\n"

    def test_resolve_speaks_the_bench_roots_spelling(self, tmp_path):
        real = tmp_path / "real" / "bench"
        (real / "r" / "p").mkdir(parents=True)
        (real / "r" / "p" / "c.md").write_text("x", encoding="utf-8")
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path / "real")
        # The caller names the file through one spelling, the root is another.
        found = mark.resolve(alias / "bench", str(real / "r" / "p" / "c.md"))
        assert found == alias / "bench" / "r" / "p" / "c.md"
        assert found.relative_to(alias / "bench").parts == ("r", "p", "c.md")

    def test_a_strand_may_not_mark(self, tmp_path, monkeypatch):
        _fold(tmp_path / "home")
        result = _drain(tmp_path, monkeypatch, "m.md", "---\nmark: keep hugimuni-labs__brnrd/src/brr/x.py/c0ffee\n---\n",
                        meta={"strand": True})
        assert "a strand cannot read" in result["notices"][-1]["text"]

    def test_the_page_can_come_from_a_page_produce_row(self, tmp_path, monkeypatch):
        path = _fold(tmp_path / "home")
        inbox = tmp_path / ".brr" / "inbox"
        inbox.mkdir(parents=True)
        ask = protocol.create_event(inbox, "mark", "keep", conversation_key="telegram:42:",
                                    focus_bench_path=str(path), mark_verdict="keep")
        produce = tmp_path / ".brr" / "runs" / "run-seat" / "produce.jsonl"
        produce.parent.mkdir(parents=True)
        produce.write_text('{"kind": "page", "ref": "repos/x/page.md"}\n', encoding="utf-8")
        _drain(tmp_path, monkeypatch, "r.md", f"---\nevent: {ask.stem}\n---\nwritten\n")
        assert "promoted_to: repos/x/page.md\n" in path.read_text(encoding="utf-8")

    def test_append_line_on_a_file_without_frontmatter(self, tmp_path):
        path = tmp_path / "f.md"
        path.write_text("just prose\n", encoding="utf-8")
        mark.append_line(path, "mark", "drop")
        assert path.read_text(encoding="utf-8") == "---\nmark: drop\n---\njust prose\n"


class TestStakeRefuse:
    def test_refusing_a_pending_messages_stake_parks_it_pending(self, tmp_path, monkeypatch):
        inbox = tmp_path / ".brr" / "inbox"
        inbox.mkdir(parents=True)
        msg = protocol.create_event(inbox, "telegram", "stake: 5%\n\nrefactor everything",
                                    conversation_key="telegram:42:")
        result = _drain(tmp_path, monkeypatch, "s.md", f"---\nstake: refuse\nevent: {msg.stem}\n---\nscope first\n")
        event = protocol._read_event(msg)
        assert event["status"] == "pending" and event["stake_refused"] == "scope first"
        assert msg.stem in result["task"].meta["stake_seen_events"]
        assert stake.request_from(event, event["body"]) is None

    def test_refusing_the_armed_stake_stops_it_and_lifts_a_stamped_cut(self, tmp_path, monkeypatch):
        row = stake.normalise("1m", None, cfg={})
        # The armed row names the waking event; stamp it with the id the drain mints.
        inbox = tmp_path / ".brr" / "inbox"
        inbox.mkdir(parents=True)
        own = protocol.create_event(inbox, "telegram", "look", status="processing", stake="1m")
        outbox = tmp_path / ".brr" / "outbox" / own.stem
        outbox.mkdir(parents=True)
        (outbox / "s.md").write_text("---\nstake: refuse\n---\nnot this one\n", encoding="utf-8")
        monkeypatch.setattr(daemon.updates, "emit", lambda *_a, **_k: None)
        task = Run(id="run-seat", event_id=own.stem, body="look", source="telegram")
        row.update(state="cut", spent=2_000_000, event_id=own.stem)
        task.meta["stake"] = row
        task.meta["pending_resource_hold"] = daemon._stake_hold_spec(task, row)
        emit = daemon._WorkerEmit(brr_dir=tmp_path / ".brr", conversation_key="telegram:42:", event_id=own.stem)
        daemon._drain_outbox(emit, task, tmp_path / ".brr" / "responses", own.stem, outbox, inbox)
        assert task.meta["stake"]["state"] == "refused" and task.meta["stake"]["refused"] == "not this one"
        assert "pending_resource_hold" not in task.meta
        assert "the cut it had stamped is lifted" in daemon._read_outbox_notices(outbox)[-1]["text"]

    def test_refuse_needs_a_why_and_a_stake(self, tmp_path, monkeypatch):
        result = _drain(tmp_path, monkeypatch, "s.md", "---\nstake: refuse\n---\n")
        assert "needs one line of why" in result["notices"][-1]["text"]
        (tmp_path / ".brr").rename(tmp_path / ".brr-1")
        result = _drain(tmp_path, monkeypatch, "s.md", "---\nstake: refuse\n---\nno\n")
        assert "carries no stake to refuse" in result["notices"][-1]["text"]


# ── through the frame: the portal, and the Shuttle at the cut ────────


def test_the_portal_carries_the_seats_stake_and_parks_at_its_cut(tmp_path, monkeypatch):
    """`hud.build` end to end: the waking event's stake arms at the first
    boundary, the seat's own meter moves it, the cut stamps the hold."""
    import json as json_mod

    from test_hud_golden import frozen_host

    frozen_host(monkeypatch)
    meter = {"cumulative": 4_200}
    monkeypatch.setattr(daemon.allowance, "collect_spent", lambda *_a, **_k: meter["cumulative"])
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["stake_request"] = {"stake": "1m", "cut_at": "", "event_id": "evt-1"}

    def boundary() -> dict:
        path = daemon._write_live_portal_state(
            outbox_dir, inbox_dir, "evt-1", task,
            phase="running", runner_name="claude", brr_dir=brr_dir,
        )
        return json_mod.loads(path.read_text(encoding="utf-8"))

    first = boundary()["resources"]["allowance"]
    assert first["stake"]["state"] == "armed" and first["stake"]["spent"] == 0
    meter["cumulative"] = 4_200 + 700_000
    second = boundary()["resources"]["allowance"]
    assert second["stake_summary"] == "stake 700k/1m · 70%"
    meter["cumulative"] = 4_200 + 1_600_000
    boundary()
    assert task.meta["pending_resource_hold"]["reason"] == resource_hold.REASON_STAKE_CUT


def test_the_shuttle_reads_parked_stake_cut(tmp_path):
    from brr import shuttle

    home = tmp_path / "home"
    runs_dir = tmp_path / ".brr" / "runs"
    runs_dir.mkdir(parents=True)
    task = _seat()
    row = stake.normalise("1m", None, cfg={})
    row.update(state="cut", spent=1_600_000)
    task.meta["stake"] = row
    task.save(runs_dir)
    daemon._arm_resource_hold(
        task, runs_dir, conversation_key="telegram:42:", account_home=home,
        **daemon._stake_hold_spec(task, row),
    )
    live = shuttle.Shuttle.load(home)
    assert (live.state, live.why) == ("parked", "stake_cut")
    assert hooks._hold_chip({}, {"state": live.state, "why": live.why}) == "parked·stake_cut"
    reloaded = Run.from_file(runs_dir / task.id / "run.md")
    assert reloaded.meta["stake"]["spent"] == 1_600_000
