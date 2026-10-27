"""One notice writer, with the file — ``brr.outbox.notices``.

The correlation gap ``portals.md`` names: a notice recorded its verb and
target only in ``text``, so ``brnrd do`` matched a refusal to "the directive I
just staged" by substring. Every notice written while the drain handles a
staged file now carries ``source_file`` · ``verb`` · ``run`` — including the
ones written deep inside a ``_queue_*`` helper that never saw the path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import daemon, hooks, protocol
from brr.run import Run


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _drain(tmp_path: Path, monkeypatch, files: dict[str, str], *, meta: dict | None = None):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    inbox.mkdir(parents=True)
    own = protocol.create_event(
        inbox, "telegram", "go", status="processing",
        telegram_user_id="42", telegram_chat_id="42",
    )
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    for name, text in files.items():
        (outbox / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_gate_can_deliver", lambda _brr, gate: gate == "telegram")
    task = Run(id="run-parent", event_id=own.stem, body="go", source="telegram", meta=meta or {})
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    daemon._drain_outbox(emit, task, responses, own.stem, outbox, inbox, stats={})
    return brr_dir, inbox, outbox, task


def test_a_helper_deep_refusal_carries_the_file_the_verb_and_the_run(tmp_path, monkeypatch):
    # `spawn refused` is written inside `_queue_spawn_request`, which is
    # handed `outbox_dir` but never the staged path.
    _brr, _inbox, outbox, _task = _drain(tmp_path, monkeypatch, {
        "spawn-host.md": "---\nspawn: true\nshell: claude\nenvironment: host\n---\nx\n",
    })
    (row,) = daemon._read_outbox_notices(outbox)
    assert row["text"].startswith("spawn refused:")
    assert row["kind"] == "refused"
    assert row["source_file"] == "spawn-host.md"
    assert row["verb"] == "spawn"
    assert row["run"] == "run-parent"


@pytest.mark.parametrize(
    ("name", "text", "verb"),
    [
        ("note.md", "---\nnote: evt-1234567890123-zzzz\n---\n", "note"),
        ("steer.md", "---\nto: evt-nobody\n---\nleft\n", "to"),
        ("stop.md", "---\nstop: evt-ghost\n---\n", "stop"),
        ("await.md", "---\nawait: true\n---\n", "await"),
        ("reply.md", "---\nevent: evt-1234567890123-abcd\n---\nhi\n", "event"),
        ("hold.md", "---\nhold: true\nresume: sometime\n---\n", "hold"),
        ("cut.md", "---\ncut: true\nproduce: [\n---\nbye\n", "cut"),
    ],
)
def test_every_verbs_refusal_names_its_file(tmp_path, monkeypatch, name, text, verb):
    _brr, _inbox, outbox, _task = _drain(tmp_path, monkeypatch, {name: text})
    rows = daemon._read_outbox_notices(outbox)
    assert rows, "expected a refusal notice"
    for row in rows:
        assert row["source_file"] == name
        assert row["verb"] == verb
        assert row["run"] == "run-parent"


def test_two_files_in_one_drain_are_attributed_apart(tmp_path, monkeypatch):
    _brr, _inbox, outbox, _task = _drain(tmp_path, monkeypatch, {
        "a-stop.md": "---\nstop: evt-ghost\n---\n",
        "b-steer.md": "---\nto: evt-nobody\n---\nleft\n",
    })
    rows = daemon._read_outbox_notices(outbox)
    assert {(r["source_file"], r["verb"]) for r in rows} == {
        ("a-stop.md", "stop"), ("b-steer.md", "to"),
    }


def test_the_staging_casualty_drop_is_attributed_before_any_row(tmp_path, monkeypatch):
    _brr, _inbox, outbox, _task = _drain(tmp_path, monkeypatch, {
        "do-1-cut-2.md": "no frontmatter\n",
    })
    (row,) = daemon._read_outbox_notices(outbox)
    assert (row["source_file"], row["verb"]) == ("do-1-cut-2.md", "cut")


def test_the_chip_still_counts_and_names_the_spawn_refusal(tmp_path, monkeypatch):
    _brr, _inbox, outbox, _task = _drain(tmp_path, monkeypatch, {
        "spawn-host.md": "---\nspawn: true\nshell: claude\nenvironment: host\n---\nx\n",
    })
    rows = daemon._read_outbox_notices(outbox)
    assert hooks._notices_chip(rows) == "!1"
    assert (hooks._spawn_refused_chip(rows) or "").startswith("✗ spawn refused")


def test_portal_state_notices_carry_the_new_fields(tmp_path, monkeypatch):
    brr_dir, inbox, outbox, task = _drain(tmp_path, monkeypatch, {
        "stop.md": "---\nstop: evt-ghost\n---\n",
    })
    path = daemon._write_live_portal_state(
        outbox, inbox, task.event_id, task, phase="running", brr_dir=brr_dir,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    (row,) = payload["notices"]
    assert (row["source_file"], row["verb"], row["run"]) == ("stop.md", "stop", "run-parent")
    assert row["text"].startswith("stop refused:")


def test_brnrd_do_joins_on_the_file_for_every_verb_now(tmp_path, monkeypatch):
    """``do.find_matching_notice`` already tried ``source_file`` first; the
    identity join now resolves a verb whose text never named the file."""
    from brr import do

    _brr, _inbox, outbox, _task = _drain(tmp_path, monkeypatch, {
        "steer.md": "---\nto: evt-nobody\n---\nleft\n",
    })
    rows = daemon._read_outbox_notices(outbox)
    hit = do.find_matching_notice(rows, ("no-such-needle",), source_file="steer.md")
    assert hit is not None and hit["verb"] == "to"
