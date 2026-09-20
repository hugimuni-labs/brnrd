"""The verb table, one row at a time — ``brr.outbox.table`` and ``verbs``.

``test_outbox_drain_golden.py`` proves the split changed nothing a drain
produces; this module pins the *shape* the split added: which row claims a
file, what ``Handled`` each handler returns, that ``cut:`` falls through, and
that ``daemon._drain_outbox`` is only a call.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from brr import daemon, protocol
from brr.outbox import table, verbs
from brr.outbox.shapes import DrainContext, Handled, OutboxFile
from brr.run import Run


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _setup(tmp_path: Path, monkeypatch, *, meta: dict | None = None):
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
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram")
    task = Run(
        id="run-parent", event_id=own.stem, body="do the thing", source="telegram",
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
                address_sources=daemon._outbox_address_sources(inbox, responses, None, None),
            ),
        )

    return file, outbox, inbox, stats, emitted


def _verbs(results: list[Handled]) -> list[str]:
    return [r.verb for r in results]


# ── the table ──────────────────────────────────────────────────────────


def test_rows_are_the_precedence_main_applied():
    assert [row.key for row in table.ROWS] == [
        "runner_policy", "config_change", "halt", "respawn", "spawn", "ask",
        "submit",
        "to", "stop", "note", "await", "hold",
        "land", "fold", "topic", "mark", "stake", "cut-at",
        "cut", "gate", "thread", "event",
    ]
    # exactly one fallback, and it is last
    assert [row.key for row in table.ROWS if row.selects is None] == ["event"]


@pytest.mark.parametrize(
    ("frontmatter", "verb"),
    [
        ({"runner_policy": "propose"}, "runner_policy"),
        ({"config_change": "seat.x=1"}, "config_change"),
        ({"respawn": True}, "respawn"),
        ({"spawn": "true"}, "spawn"),
        ({"ask": "allowance +10k"}, "ask"),
        ({"submit": "yes"}, "submit"),
        ({"to": "evt-1"}, "to"),
        ({"stop": "evt-1"}, "stop"),
        ({"note": "evt-1"}, "note"),
        ({"await": True}, "await"),
        ({"hold": True}, "hold"),
        ({"land": 12}, "land"),
        ({"fold": "src"}, "fold"),
        ({"mark": "keep"}, "mark"),
        ({"stake": "5"}, "stake"),
        ({"cut-at": "5"}, "cut-at"),
        ({"cut": True}, "cut"),
        ({"gate": "telegram"}, "gate"),
        ({"thread": "telegram:42:"}, "thread"),
        ({"event": "evt-1", "thread": "telegram:42:"}, "event"),
        ({"event": "evt-1"}, "event"),
        ({}, "event"),
        ({"label": "x"}, "event"),
    ],
)
def test_each_key_selects_its_own_row(frontmatter, verb):
    first = next(
        row for row in table.ROWS
        if row.selects is None or row.selects(frontmatter)
    )
    assert first.key == verb


def test_a_falsy_selector_value_does_not_claim_the_file():
    # `spawn: false`, `to:` blank, `ask:` that is not an allowance — the
    # original `if` tests, kept: these fall to the reply.
    for fm in ({"spawn": "false"}, {"to": "  "}, {"ask": "a question"}, {"gate": ""}):
        first = next(r for r in table.ROWS if r.selects is None or r.selects(fm))
        assert first.key == "event", fm


def test_spawn_outranks_to_and_note(tmp_path, monkeypatch):
    file, _outbox, _inbox, _stats, _emitted = _setup(tmp_path, monkeypatch)
    f = file(
        "many.md",
        "---\nnote: evt-x\nto: evt-y\nspawn: true\nshell: claude\nenvironment: host\n---\nw\n",
    )
    results = table.dispatch(f)
    assert _verbs(results) == ["spawn"]
    assert results[0].outcome == "refused"
    assert "spawn refused" in (results[0].notice or "")


# ── Handled, per verb ──────────────────────────────────────────────────


def test_a_plain_reply_is_accepted_by_the_event_row(tmp_path, monkeypatch):
    file, outbox, _inbox, stats, emitted = _setup(tmp_path, monkeypatch)
    results = table.dispatch(file("001.md", "hello thread\n"))
    assert _verbs(results) == ["event"]
    assert results[0].outcome == "accepted"
    assert results[0].promoted == 1
    assert stats == {"current": 1, "delivered": 1}
    assert [p.type for p in emitted] == ["interim_response"]
    assert (outbox / ".processed" / "001.md").exists()


def test_note_on_an_unknown_event_is_refused_with_its_notice(tmp_path, monkeypatch):
    file, _outbox, _inbox, _stats, _emitted = _setup(tmp_path, monkeypatch)
    results = table.dispatch(file("note.md", "---\nnote: evt-1234567890123-zzzz\n---\n"))
    assert _verbs(results) == ["note"]
    (result,) = results
    assert result.outcome == "refused"
    assert result.promoted == 0
    assert result.notice and "note" in result.notice


def test_note_on_a_pending_event_is_accepted(tmp_path, monkeypatch):
    file, _outbox, inbox, stats, emitted = _setup(tmp_path, monkeypatch)
    other = protocol.create_event(
        inbox, "telegram", "fyi", telegram_user_id="42", telegram_chat_id="42",
    )
    (result,) = table.dispatch(file("note.md", f"---\nnote: {other.stem}\n---\n"))
    assert (result.verb, result.outcome, result.promoted) == ("note", "accepted", 1)
    assert stats == {"note": 1}
    assert [p.type for p in emitted] == ["event_noted"]


def test_to_an_unknown_strand_is_refused(tmp_path, monkeypatch):
    file, *_ = _setup(tmp_path, monkeypatch)
    (result,) = table.dispatch(file("steer.md", "---\nto: evt-nobody\n---\nleft\n"))
    assert (result.verb, result.outcome) == ("to", "refused")


def test_stop_on_a_converged_strand_is_refused(tmp_path, monkeypatch):
    file, *_ = _setup(tmp_path, monkeypatch)
    (result,) = table.dispatch(file("stop.md", "---\nstop: evt-ghost\n---\n"))
    assert (result.verb, result.outcome) == ("stop", "refused")
    assert "matches no live concurrent spawn" in (result.notice or "")


def test_spawn_accepted_is_accepted(tmp_path, monkeypatch):
    file, _outbox, _inbox, stats, _emitted = _setup(tmp_path, monkeypatch)
    (result,) = table.dispatch(file(
        "spawn.md",
        "---\nspawn: true\nshell: claude\nbranch: brr/child\n"
        f"report: {tmp_path / 'child-report.md'}\ntitle: a child\n---\nchild task\n",
    ))
    assert (result.verb, result.outcome, result.promoted) == ("spawn", "accepted", 1)
    assert stats == {"spawn": 1}


def test_await_armed_is_accepted_and_a_malformed_one_refused(tmp_path, monkeypatch):
    file, *_ = _setup(tmp_path, monkeypatch)
    (armed,) = table.dispatch(file("await.md", "---\nawait: true\ntimeout: 5m\n---\n"))
    assert (armed.verb, armed.outcome) == ("await", "accepted")
    (bad,) = table.dispatch(file("await2.md", "---\nawait: true\n---\n"))
    assert (bad.verb, bad.outcome) == ("await", "refused")
    assert bad.notice.startswith("await dropped:")


def test_hold_by_choice_is_refused(tmp_path, monkeypatch):
    file, *_ = _setup(tmp_path, monkeypatch)
    (result,) = table.dispatch(file("hold.md", "---\nhold: true\nresume: operator\n---\n"))
    assert (result.verb, result.outcome) == ("hold", "refused")


def test_gate_to_an_undeliverable_gate(tmp_path, monkeypatch):
    file, *_ = _setup(tmp_path, monkeypatch)
    (result,) = table.dispatch(file("gate.md", "---\ngate: slack\n---\nping\n"))
    assert result.verb == "gate"
    assert result.promoted == 0


def test_an_accepted_cut_falls_through_to_the_reply(tmp_path, monkeypatch):
    file, outbox, _inbox, stats, emitted = _setup(tmp_path, monkeypatch)
    (outbox / ".topics").write_text("daemon\n", encoding="utf-8")
    results = table.dispatch(file("cut.md", "---\ncut: true\n---\ndone here\n"))
    assert _verbs(results) == ["cut", "event"]
    cut, reply = results
    assert cut.then is not None and "event" not in cut.then.frontmatter
    assert (cut.outcome, cut.promoted) == ("deferred", 0)
    assert (reply.outcome, reply.promoted) == ("accepted", 1)
    assert stats == {"current": 1, "cut": 1, "delivered": 1}
    assert [p.type for p in emitted] == ["cut_accepted", "interim_response"]


def test_a_bounced_cut_does_not_fall_through(tmp_path, monkeypatch):
    file, *_ = _setup(tmp_path, monkeypatch)
    results = table.dispatch(file("cut.md", "---\ncut: true\n---\ndone here\n"))
    assert _verbs(results) == ["cut"]
    assert results[0].outcome == "refused"
    assert results[0].then is None


@pytest.mark.parametrize(
    ("key", "prefix"),
    [("mark", "mark dropped: "), ("stake", "stake refused: "), ("cut-at", "cut-at refused: ")],
)
def test_the_4b_verbs_refuse_what_their_grammar_does_not_read(tmp_path, monkeypatch, key, prefix):
    # Move 4b gave the three stubs their semantics; a value outside each
    # grammar still costs only the file, attributed to its own row.
    file, outbox, *_ = _setup(tmp_path, monkeypatch)
    (result,) = table.dispatch(file(f"{key}.md", f"---\n{key}: something\n---\nbody\n"))
    assert (result.verb, result.outcome, result.promoted) == (key, "refused", 0)
    assert result.notice.startswith(prefix)
    assert "not yet" not in result.notice
    rows = daemon._read_outbox_notices(outbox)
    assert rows[-1]["verb"] == key and rows[-1]["source_file"] == f"{key}.md"
    assert (outbox / ".processed" / f"{key}.md").exists()


def test_a_stake_riding_a_spawn_stays_the_spawns_modifier(tmp_path, monkeypatch):
    file, *_ = _setup(tmp_path, monkeypatch)
    results = table.dispatch(file(
        "spawn.md",
        f"---\nspawn: true\nshell: claude\nbranch: brr/c\nreport: {tmp_path / 'r.md'}\n"
        "stake: 5\n---\ntask\n",
    ))
    assert _verbs(results) == ["spawn"]


def test_a_handler_exception_is_quarantined_by_its_guard(tmp_path, monkeypatch):
    file, outbox, *_ = _setup(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(daemon, "_queue_stop_request", boom)
    (result,) = table.dispatch(file("stop.md", "---\nstop: evt-1\n---\n"))
    assert (result.verb, result.outcome) == ("stop", "refused")
    assert "drain error: RuntimeError: kaboom" in result.notice
    assert (outbox / ".poisoned").exists()


def test_helpers_are_reached_through_daemon_at_call_time(tmp_path, monkeypatch):
    """The patchability contract the split kept: a test patching a daemon
    helper still lands inside the moved handler."""
    file, *_ = _setup(tmp_path, monkeypatch)
    seen: list[str] = []
    monkeypatch.setattr(
        daemon, "_queue_child_message",
        lambda *a, **k: seen.append("called") or False,
    )
    table.dispatch(file("steer.md", "---\nto: evt-1\n---\nx\n"))
    assert seen == ["called"]


# ── the drain is a call ────────────────────────────────────────────────


def test_daemon_drain_outbox_is_a_call_into_outbox_drain():
    tree = ast.parse(Path(daemon.__file__).read_text(encoding="utf-8"))
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_drain_outbox"
    )
    calls = [
        ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)
    ]
    assert calls == ["outbox_drain.drain"]
    assert not any(isinstance(n, (ast.For, ast.While)) for n in ast.walk(fn))


def test_daemon_drain_outbox_signature_is_unchanged():
    import inspect

    params = inspect.signature(daemon._drain_outbox).parameters
    assert list(params) == [
        "emit", "task", "responses_dir", "event_id", "outbox_dir", "inbox_dir",
        "repo_root", "account_context", "stats",
    ]
    assert params["inbox_dir"].default is None
    assert all(
        params[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("repo_root", "account_context", "stats")
    )


def test_every_handler_is_documented_by_its_row():
    handlers = {row.handler for row in table.ROWS}
    public = {
        getattr(verbs, name) for name in dir(verbs) if name.startswith("handle_")
    }
    assert handlers == public
