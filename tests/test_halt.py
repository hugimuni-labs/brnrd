"""``halt:`` — the verb that lets a seat end.

Acceptance bar (design-the-four-stops.md §"The two verbs", signed
2026-09-18): a seat can end, on purpose, with a reason on record and either
a successor's brief or a stated revival path — and **every refusal path is
pinned here**, because the whole defence of allowing an ending with no
approval gate is that the bounce checked the brief first.

Four layers, each at the altitude its failure lives at:

- ``TestParseHalt`` / ``TestNaming`` — the pure grammar and the one
  predicate the bounce is made of, no I/O.
- ``TestLedger`` — the account record the dashboard reads.
- ``TestDrain`` — the verb through the real outbox table, on files written
  the way a resident writes them and parsed by ``protocol`` (the #2016
  lesson: a suite that invents its own input shape ships inert).
- ``TestThroughTheWorker`` — end to end through
  ``daemon._run_worker_and_finalize``, the same production entry the
  resource-hold suite uses, with a fake runner that stages a real outbox
  file. This is the layer that proves a seat *actually ends*: status
  terminal, no park, and the turn-end safety net not firing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import daemon, envs, halt_verb, halts, protocol, resource_hold
from brr.outbox import table
from brr.outbox.shapes import DrainContext, OutboxFile
from brr.run import HALTED_STATUS, Run
from brr.runner import RunnerResult

from _helpers import make_event, write_repo_scaffold


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


# ── the grammar, and every refusal it owns ─────────────────────────────


class TestParseHalt:
    def test_carry_form_parses(self):
        declaration, error = halt_verb.parse_halt({
            "halt": "true",
            "reason": "900k of scroll at 83k a boundary",
            "carry": "finish #2011 phase B; the roast is in .brr/reports",
        })
        assert error is None
        assert declaration.carried is True
        assert declaration.kind == halt_verb.KIND_CARRIED
        assert declaration.brief.startswith("finish #2011")

    def test_stopped_form_parses(self):
        declaration, error = halt_verb.parse_halt({
            "halt": "true",
            "reason": "the spec cannot be satisfied as written",
            "resumable": "a maintainer ruling on the fork in design-x.md §3",
        })
        assert error is None
        assert declaration.carried is False
        assert declaration.kind == halt_verb.KIND_STOPPED
        assert declaration.brief.startswith("a maintainer ruling")

    def test_bare_marker_is_accepted_as_a_marker(self):
        # `halt:` with an empty value is the marker, like every other verb —
        # it fails on `reason:`, not on the marker, and the notice says so.
        declaration, error = halt_verb.parse_halt({"halt": ""})
        assert declaration is None
        assert "reason:" in error

    # ── refusal 1: the marker ──
    def test_unrecognised_marker_is_refused(self):
        declaration, error = halt_verb.parse_halt({"halt": "maybe"})
        assert declaration is None
        assert "'maybe'" in error and "halt: true" in error

    # ── refusal 2: reason is required and non-empty ──
    @pytest.mark.parametrize("fm", [
        {"halt": "true", "carry": "keep going"},
        {"halt": "true", "reason": "   ", "carry": "keep going"},
        {"halt": "true", "reason": "", "resumable": "a ruling"},
    ])
    def test_missing_or_blank_reason_is_refused(self, fm):
        declaration, error = halt_verb.parse_halt(fm)
        assert declaration is None
        assert "reason:" in error
        assert "required" in error

    # ── refusal 3: no carry ⇒ resumable is required ──
    def test_neither_carry_nor_resumable_is_refused(self):
        declaration, error = halt_verb.parse_halt({
            "halt": "true", "reason": "context is large",
        })
        assert declaration is None
        assert "resumable:" in error
        assert "the work stops here" in error

    # ── refusal 4: a next body with no brief ──
    @pytest.mark.parametrize("extra", [
        {"shell": "codex"}, {"core": "fable"}, {"shell": "claude", "core": "opus"},
    ])
    def test_shell_or_core_without_carry_is_refused(self, extra):
        declaration, error = halt_verb.parse_halt({
            "halt": "true", "reason": "worn out", "resumable": "ask me", **extra,
        })
        assert declaration is None
        assert "carry:" in error
        assert "loses everything" in error

    def test_shell_and_core_with_carry_are_kept(self):
        declaration, error = halt_verb.parse_halt({
            "halt": "true", "reason": "worn out", "carry": "the brief",
            "shell": "codex", "core": "gpt-6-astra",
        })
        assert error is None
        assert (declaration.shell, declaration.core) == ("codex", "gpt-6-astra")

    # ── refusal 5: unknown fields, by name (#1187) ──
    def test_unknown_field_is_refused_by_name(self):
        declaration, error = halt_verb.parse_halt({
            "halt": "true", "reason": "x", "carry_forward": "the brief",
        })
        assert declaration is None
        assert "carry_forward" in error

    def test_another_verbs_key_refuses_the_whole_file(self):
        # The safety property the table's precedence rests on: `halt:` sits
        # above `spawn:`, so it must never silently swallow a spawn request.
        declaration, error = halt_verb.parse_halt({
            "halt": "true", "reason": "x", "carry": "y", "spawn": "true",
        })
        assert declaration is None
        assert "spawn" in error

    def test_topic_is_an_act_modifier_not_an_unknown_field(self):
        declaration, error = halt_verb.parse_halt({
            "halt": "true", "reason": "x", "carry": "y", "topic": "the-clockwork",
        })
        assert error is None
        assert declaration.carry == "y"


class TestNaming:
    def _item(self) -> halt_verb.OpenItem:
        return halt_verb.OpenItem(
            kind="event", handle="evt-…a7hv", line="evt-…a7hv is pending",
            aliases=("evt-1789766584122038000-a7hv", "a7hv"),
        )

    def test_handle_names_it(self):
        assert self._item().named_by("I left evt-…a7hv for the next seat")

    def test_alias_names_it(self):
        assert self._item().named_by("a7hv needs an answer")

    def test_case_insensitive(self):
        assert self._item().named_by("A7HV needs an answer")

    def test_silence_does_not(self):
        assert not self._item().named_by("context is large")

    def test_empty_brief_never_names_anything(self):
        assert not self._item().named_by("")

    def test_unnamed_returns_exactly_the_unaccounted(self):
        declaration, _ = halt_verb.parse_halt({
            "halt": "true", "reason": "x", "resumable": "a7hv is answered in kb/foo",
        })
        items = [
            self._item(),
            halt_verb.OpenItem(kind="course", handle="course:2", line="course:2 open"),
        ]
        assert [i.handle for i in halt_verb.unnamed(declaration, items)] == ["course:2"]

    def test_durable_declaration_carries_kind_and_dissent(self):
        declaration, _ = halt_verb.parse_halt({
            "halt": "true", "reason": "worn out", "carry": "the brief",
        })
        record = halt_verb.durable_declaration(declaration, dissent=["a", "b"])
        assert record["kind"] == halt_verb.KIND_CARRIED
        assert record["dissent"] == ["a", "b"]
        assert record["resumable"] is None

    def test_durable_declaration_clips_rather_than_carrying_a_novel(self):
        declaration, _ = halt_verb.parse_halt({
            "halt": "true", "reason": "x" * 5000, "carry": "y",
        })
        record = halt_verb.durable_declaration(declaration)
        assert len(record["reason"]) <= halt_verb._MAX_TEXT_CHARS
        assert record["reason"].endswith("…")

    def test_durable_declaration_counts_what_it_dropped(self):
        declaration, _ = halt_verb.parse_halt({
            "halt": "true", "reason": "x", "carry": "y",
        })
        rows = [f"row {n}" for n in range(halt_verb._MAX_DISSENT + 5)]
        record = halt_verb.durable_declaration(declaration, dissent=rows)
        assert len(record["dissent"]) == halt_verb._MAX_DISSENT
        assert record["dissent_omitted"] == 5


# ── the account ledger the display reads ───────────────────────────────


class TestLedger:
    def test_roundtrip_and_counts_stay_apart(self, tmp_path):
        halts.record(
            tmp_path, run_id="run-a", kind=halt_verb.KIND_CARRIED,
            reason="worn out", carry="keep going",
        )
        halts.record(
            tmp_path, run_id="run-b", kind=halt_verb.KIND_STOPPED,
            reason="spec unsatisfiable", resumable="a ruling",
            open_items=["evt-…x is pending"],
        )
        rows = halts.read(tmp_path)
        assert [row["run"] for row in rows] == ["run-a", "run-b"]
        assert halts.counts(rows) == {
            halt_verb.KIND_CARRIED: 1, halt_verb.KIND_STOPPED: 1, "total": 2,
        }

    def test_open_queue_is_stopped_halts_with_open_items_newest_first(self, tmp_path):
        halts.record(
            tmp_path, run_id="clean", kind=halt_verb.KIND_STOPPED,
            reason="finished", resumable="nothing, it is done",
        )
        halts.record(
            tmp_path, run_id="carried", kind=halt_verb.KIND_CARRIED,
            reason="worn out", carry="go", open_items=["evt-…x"],
        )
        halts.record(
            tmp_path, run_id="abandoned-1", kind=halt_verb.KIND_STOPPED,
            reason="blocked", resumable="a ruling", open_items=["evt-…y"],
        )
        halts.record(
            tmp_path, run_id="abandoned-2", kind=halt_verb.KIND_STOPPED,
            reason="blocked", resumable="a ruling", open_items=["evt-…z"],
        )
        queue = halts.open_queue(halts.read(tmp_path))
        assert [row["run"] for row in queue] == ["abandoned-2", "abandoned-1"]

    def test_unknown_kind_records_as_stopped_never_as_carried(self, tmp_path):
        # The count is the point of the field: an unrecognised kind must
        # never silently land in the flattering column.
        row = halts.record(tmp_path, run_id="r", kind="whatever", reason="x")
        assert row["kind"] == halt_verb.KIND_STOPPED

    def test_no_home_is_silent(self):
        assert halts.record(None, run_id="r", kind="carried", reason="x") is None
        assert halts.read(None) == []

    def test_malformed_lines_degrade_to_fewer_rows(self, tmp_path):
        path = halts.ledger_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"run": "good", "kind": "carried"}) + "\n"
            + "{not json\n\n" + json.dumps(["not a dict"]) + "\n",
            encoding="utf-8",
        )
        assert [row["run"] for row in halts.read(tmp_path)] == ["good"]


# ── the verb through the real table ────────────────────────────────────


def _setup(tmp_path: Path, monkeypatch, *, meta: dict | None = None):
    """The drain harness ``test_outbox_verbs`` uses: real event, real parse."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    inbox.mkdir(parents=True)
    own = protocol.create_event(
        inbox, "telegram", "do the thing", status="processing",
        telegram_user_id="42", telegram_chat_id="42",
    )
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    emitted: list = []
    monkeypatch.setattr(daemon.updates, "emit", lambda _brr, pkt: emitted.append(pkt))
    monkeypatch.setattr(
        daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram",
    )
    task = Run(
        id="run-seat", event_id=own.stem, body="do the thing", source="telegram",
        meta=dict(meta or {}),
    )
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(
        brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem,
    )
    stats: dict[str, int] = {}

    def file(name: str, text: str) -> OutboxFile:
        path = outbox / name
        path.write_text(text, encoding="utf-8")
        fm, body = protocol.parse_outbox_message(text)
        return OutboxFile(
            path=path, frontmatter=fm, body=body.strip(), run=task,
            ctx=DrainContext(
                emit=emit, responses_dir=responses, event_id=own.stem,
                outbox_dir=outbox, inbox_dir=inbox, repo_root=None,
                account_context=None, stats=stats,
                address_sources=daemon._outbox_address_sources(
                    inbox, responses, None, None,
                ),
            ),
        )

    return file, outbox, inbox, task, stats


def _notices(outbox: Path) -> list[dict]:
    try:
        raw = (outbox / daemon.NOTICES_FILE).read_text(encoding="utf-8")
    except OSError:
        return []
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


class TestDrain:
    def test_halt_row_claims_the_file_and_falls_through_to_delivery(
        self, tmp_path, monkeypatch,
    ):
        file, outbox, _inbox, task, _stats = _setup(tmp_path, monkeypatch)
        f = file("0001-halt.md", (
            "---\nhalt: true\nreason: this body is spent\n"
            "resumable: the branch is pushed; pick it up from the report\n---\n"
            "Halting here — the report says what is left.\n"
        ))
        results = table.dispatch(f)
        assert [r.verb for r in results] == ["halt", "event"]
        assert results[0].outcome == "accepted"
        assert task.meta["pending_halt"]["declaration"]["kind"] == (
            halt_verb.KIND_STOPPED
        )

    def test_accepted_halt_hands_the_body_on_as_the_announcement(
        self, tmp_path, monkeypatch,
    ):
        file, _outbox, _inbox, _task, _stats = _setup(tmp_path, monkeypatch)
        f = file("0001-halt.md", (
            "---\nhalt: true\nreason: spent\nresumable: read the report\n---\n"
            "Halting — the report says what is left.\n"
        ))
        results = table.dispatch(f)
        # The delivery row received the prose, with no halt key left on it.
        assert results[0].then is not None
        handed = results[0].then
        assert "Halting — the report says what is left." in handed.body
        assert not set(handed.frontmatter) & set(daemon.halt_verb._KNOWN_KEYS)

    def test_a_bare_halt_still_announces_its_reason(self, tmp_path, monkeypatch):
        file, _outbox, _inbox, _task, _stats = _setup(tmp_path, monkeypatch)
        f = file("0001-halt.md", (
            "---\nhalt: true\nreason: the spec cannot be satisfied\n"
            "resumable: a maintainer ruling on §3\n---\n"
        ))
        results = table.dispatch(f)
        handed = results[0].then
        assert 'halt — "the spec cannot be satisfied"' in handed.body
        assert "a maintainer ruling on §3" in handed.body

    def test_parse_refusal_stages_nothing(self, tmp_path, monkeypatch):
        file, outbox, _inbox, task, _stats = _setup(tmp_path, monkeypatch)
        f = file("0001-halt.md", "---\nhalt: true\ncarry: keep going\n---\nbye\n")
        results = table.dispatch(f)
        assert [r.verb for r in results] == ["halt"]
        assert results[0].outcome == "refused"
        assert "pending_halt" not in task.meta
        assert any("reason:" in n["text"] for n in _notices(outbox))

    def test_a_strand_may_not_halt_the_seat(self, tmp_path, monkeypatch):
        file, outbox, _inbox, task, _stats = _setup(
            tmp_path, monkeypatch, meta={"strand": True, "spawn_parent_run_id": "run-p"},
        )
        f = file("0001-halt.md", (
            "---\nhalt: true\nreason: done here\nresumable: the parent reads the branch\n"
            "---\nbye\n"
        ))
        results = table.dispatch(f)
        assert results[0].outcome == "refused"
        assert "pending_halt" not in task.meta
        assert any("submit: true" in n["text"] for n in _notices(outbox))

    def test_a_pending_sibling_event_bounces_the_halt_once(self, tmp_path, monkeypatch):
        file, outbox, inbox, task, _stats = _setup(tmp_path, monkeypatch)
        sibling = protocol.create_event(
            inbox, "telegram", "and also this", status="pending",
            telegram_user_id="42", telegram_chat_id="42",
        )
        text = (
            "---\nhalt: true\nreason: spent\nresumable: read the report\n---\nbye\n"
        )
        first = table.dispatch(file("0001-halt.md", text))
        assert first[0].outcome == "refused"
        assert "pending_halt" not in task.meta
        bounce = [n for n in _notices(outbox) if "halt bounced" in n["text"]]
        assert bounce and sibling.stem.rsplit("-", 1)[-1] in bounce[0]["text"]

        # Bounces once: the same declaration, staged again, stands —
        # annotated with exactly what it went ahead over.
        second = table.dispatch(file("0002-halt.md", text))
        assert second[0].outcome == "accepted"
        dissent = task.meta["pending_halt"]["declaration"]["dissent"]
        assert dissent and sibling.stem.rsplit("-", 1)[-1] in dissent[0]

    def test_naming_the_pending_event_lets_the_halt_through_first_time(
        self, tmp_path, monkeypatch,
    ):
        file, outbox, inbox, task, _stats = _setup(tmp_path, monkeypatch)
        sibling = protocol.create_event(
            inbox, "telegram", "and also this", status="pending",
            telegram_user_id="42", telegram_chat_id="42",
        )
        tail = sibling.stem.rsplit("-", 1)[-1]
        results = table.dispatch(file("0001-halt.md", (
            "---\nhalt: true\nreason: spent\n"
            f"resumable: {tail} is unanswered — the next seat should read it first\n"
            "---\nbye\n"
        )))
        assert results[0].outcome == "accepted"
        assert task.meta["pending_halt"]["declaration"]["dissent"] == []

    def test_an_unticked_course_row_is_an_open_item(self, tmp_path, monkeypatch):
        file, outbox, _inbox, task, _stats = _setup(tmp_path, monkeypatch)
        (outbox / daemon._CARD_CONTROL_NAME).write_text(
            "# card\n\n## Plan\n- [x] read the ticket\n- [ ] fix the parser\n",
            encoding="utf-8",
        )
        results = table.dispatch(file("0001-halt.md", (
            "---\nhalt: true\nreason: spent\nresumable: read the report\n---\nbye\n"
        )))
        assert results[0].outcome == "refused"
        bounce = [n for n in _notices(outbox) if "halt bounced" in n["text"]][0]
        assert "course:2" in bounce["text"]
        assert "fix the parser" in bounce["text"]

    def test_naming_a_course_row_by_its_own_text_counts(self, tmp_path, monkeypatch):
        file, outbox, _inbox, task, _stats = _setup(tmp_path, monkeypatch)
        (outbox / daemon._CARD_CONTROL_NAME).write_text(
            "## Plan\n- [ ] fix the parser\n", encoding="utf-8",
        )
        results = table.dispatch(file("0001-halt.md", (
            "---\nhalt: true\nreason: spent\n"
            "resumable: fix the parser — the failing case is in tests/test_x.py\n"
            "---\nbye\n"
        )))
        assert results[0].outcome == "accepted"

    def test_a_live_strand_is_an_open_item(self, tmp_path, monkeypatch):
        file, outbox, _inbox, task, _stats = _setup(tmp_path, monkeypatch)
        with daemon._run_controls_lock:
            daemon._run_controls["evt-child"] = {
                "parent_run_id": "run-seat", "run_id": "run-child",
                "event_id": "evt-child", "title": "the audit",
            }
        results = table.dispatch(file("0001-halt.md", (
            "---\nhalt: true\nreason: spent\nresumable: read the report\n---\nbye\n"
        )))
        assert results[0].outcome == "refused"
        bounce = [n for n in _notices(outbox) if "halt bounced" in n["text"]][0]
        assert "run-child" in bounce["text"]

    def test_halt_drops_a_park_staged_the_same_turn(self, tmp_path, monkeypatch):
        file, outbox, _inbox, task, _stats = _setup(tmp_path, monkeypatch)
        task.meta["pending_resource_hold"] = {"reason": "turn_ended"}
        results = table.dispatch(file("0001-halt.md", (
            "---\nhalt: true\nreason: spent\nresumable: read the report\n---\nbye\n"
        )))
        assert results[0].outcome == "accepted"
        assert "pending_resource_hold" not in task.meta
        assert any("does not park instead" in n["text"] for n in _notices(outbox))


# ── end to end, through the production worker ──────────────────────────


def _stub_env_isolated(monkeypatch, tmp_path):
    worktree_path = tmp_path / ".brr" / "worktrees" / "stub"
    worktree_path.mkdir(parents=True, exist_ok=True)

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                    outbox_path=None):
            return envs.RunContext(
                name=self.name,
                cwd=worktree_path,
                repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                outbox_host=outbox_path,
                outbox_env=outbox_path,
                branch_name=f"brr/{task.id}",
                env_state={"worktree_path": str(worktree_path)},
            )

        def invoke(self, ctx, runner_name, invocation, cfg=None, *, trace=False):
            raise NotImplementedError("override in test")

        def finalize(self, ctx, task, runs_dir):
            return task

    monkeypatch.setattr(envs, "get_env", lambda _name: StubEnv())
    return worktree_path


def _wire_common(monkeypatch):
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)


def _run_with_halt_file(tmp_path, monkeypatch, *, eid: str, halt_text: str):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid=eid)
    _stub_env_isolated(monkeypatch, tmp_path)
    _wire_common(monkeypatch)
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        outbox_dir = Path(invocation.env["BRR_OUTBOX_DIR"])
        outbox_dir.mkdir(parents=True, exist_ok=True)
        (outbox_dir / "0001-halt.md").write_text(halt_text, encoding="utf-8")
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("Halting.\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="Halting.\n", stderr="", returncode=0,
            trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)
    return daemon._run_worker_and_finalize(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )


class TestThroughTheWorker:
    def test_a_halt_ends_the_seat_instead_of_parking_it(self, tmp_path, monkeypatch):
        task = _run_with_halt_file(
            tmp_path, monkeypatch, eid="evt-halt-1", halt_text=(
                "---\nhalt: true\nreason: this body is spent\n"
                "resumable: the branch is pushed; the report says what is left\n"
                "---\nHalting here.\n"
            ),
        )
        # The whole point: terminal, and *not* the turn-end park, which is on
        # by default and would otherwise have claimed this clean turn end.
        assert task.status == HALTED_STATUS
        assert task.status != resource_hold.RUN_STATUS
        assert "resource_hold" not in task.meta
        record = task.meta["halt"]
        assert record["kind"] == halt_verb.KIND_STOPPED
        assert record["reason"] == "this body is spent"
        assert record["halted_at"]

    def test_the_halt_is_terminal_so_no_janitor_reaps_it(self, tmp_path, monkeypatch):
        task = _run_with_halt_file(
            tmp_path, monkeypatch, eid="evt-halt-2", halt_text=(
                "---\nhalt: true\nreason: spent\nresumable: read the report\n---\nbye\n"
            ),
        )
        assert task.status not in daemon._UNFINISHED_RUN_STATUSES
        # And it is not "held": nothing resumes it, so no hold record exists
        # for a release path to find.
        assert not resource_hold.run_is_held(task.status, task.meta)

    def test_carry_mints_a_successor_event_that_inherits_no_scroll(
        self, tmp_path, monkeypatch,
    ):
        task = _run_with_halt_file(
            tmp_path, monkeypatch, eid="evt-halt-3", halt_text=(
                "---\nhalt: true\nreason: 900k of scroll at 83k a boundary\n"
                "carry: finish the phase B measurement; the roast is in .brr/reports\n"
                "---\nHanding over.\n"
            ),
        )
        assert task.status == HALTED_STATUS
        assert task.meta["halt"]["kind"] == halt_verb.KIND_CARRIED
        successor_id = task.meta["halt"]["successor_event"]
        assert successor_id

        path = tmp_path / ".brr" / "inbox" / f"{successor_id}.md"
        event = protocol._read_event(path)
        assert "finish the phase B measurement" in str(event.get("body") or "")
        assert event.get(resource_hold.HANDOVER_KEY)
        # The three measured mechanisms, all of which handed a successor the
        # predecessor's scroll: none of them can fire here, because a halted
        # run is terminal and has no hold record to re-derive a stamp from.
        assert not event.get("resume_native_session_id")
        assert "resource_hold" not in task.meta

    def test_a_halt_with_no_carry_mints_nothing(self, tmp_path, monkeypatch):
        task = _run_with_halt_file(
            tmp_path, monkeypatch, eid="evt-halt-4", halt_text=(
                "---\nhalt: true\nreason: the spec cannot be satisfied as written\n"
                "resumable: a maintainer ruling on design-x.md §3\n---\nStopping.\n"
            ),
        )
        assert task.meta["halt"].get("successor_event") in (None, "")
        inbox = tmp_path / ".brr" / "inbox"
        assert [p.name for p in inbox.glob("*.md")] == ["evt-halt-4.md"]

    def test_a_refused_halt_leaves_the_seat_where_it_was(self, tmp_path, monkeypatch):
        task = _run_with_halt_file(
            tmp_path, monkeypatch, eid="evt-halt-5", halt_text=(
                "---\nhalt: true\ncarry: keep going\n---\nbye\n"
            ),
        )
        # No reason ⇒ nothing happens, which is the point of refusing rather
        # than half-accepting: the seat is still here to be told again.
        assert task.status != HALTED_STATUS
        assert "halt" not in task.meta


class TestAHaltedParentIsNotACollector:
    """The one hazard the bounce can name but not fix: a seat halts while a
    strand it dispatched is still working, and the strand's report comes
    back to a run that is not there.

    Checked rather than assumed (#1887: a strand's return read
    ``delivered`` on disk and was dispatched to nothing). The answer is
    that ``halted`` being a *terminal* status is load-bearing:
    ``_spawn_parent_still_collecting`` reads liveness off the manifest's
    status, so a halted parent stops claiming the dispatch edge and the
    child's terminal route falls back to ``notify.gate`` — a chat message
    a person actually reads — instead of an edge nobody owns.
    """

    def _parent(self, runs_dir: Path, status: str) -> Run:
        task = Run(id="run-parent", event_id="evt-p", body="", status=status)
        task.save(runs_dir)
        return task

    def test_a_running_parent_still_collects(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True)
        self._parent(runs_dir, "running")
        assert daemon._spawn_parent_still_collecting("run-parent", runs_dir)

    def test_a_halted_parent_does_not(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True)
        self._parent(runs_dir, HALTED_STATUS)
        assert not daemon._spawn_parent_still_collecting("run-parent", runs_dir)

    def test_a_parked_parent_still_collects(self, tmp_path):
        # The contrast that makes the line above meaningful: a *park* keeps
        # the edge, which is why a park is the right answer for live
        # strands and a halt is the declared, announced exception.
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True)
        self._parent(runs_dir, resource_hold.RUN_STATUS)
        assert daemon._spawn_parent_still_collecting("run-parent", runs_dir)
