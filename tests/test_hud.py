"""The HUD — the type, its round trip, the thin call, the verb, and produce as four kinds.

The byte-level proof that the split changed nothing lives in
``test_hud_golden.py``; this module pins what the shape *is*.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from brr import daemon, hooks, hud
from brr.cli import main
from brr.run import Run

from test_hud_golden import GOLDEN_DIR, SCENARIOS, drive, frozen_host

SRC = Path(hud.__file__).parent


def _built(name, tmp_path, monkeypatch) -> tuple[hud.HUD, str, Path]:
    text, _payload = drive(name, tmp_path, monkeypatch)
    path = tmp_path / ".brr" / "outbox" / "evt-1" / hud.PORTAL_STATE_NAME
    return hud.HUD.from_json(text), text, path


# ── the shape ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_to_json_is_the_written_file_and_round_trips(name, tmp_path, monkeypatch):
    current, text, _path = _built(name, tmp_path, monkeypatch)
    assert current.to_json() == text
    assert hud.HUD.from_json(current.to_json()) == current


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_one_field_per_top_level_key(name, tmp_path, monkeypatch):
    """Every key the writer on ``main`` emitted is a HUD field, and nothing more."""
    current, _text, _path = _built(name, tmp_path, monkeypatch)
    golden_keys = set(json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8")))
    assert set(current.to_dict()) == golden_keys


def test_the_field_table():
    """Top-level key → type. A new key is a schema change: add it here on purpose."""
    fields = {f.name: f.type for f in dataclasses.fields(hud.HUD)}
    assert fields == {
        "version": "int",
        "generated_at": "str",
        "tick": "Tick | None",
        "run": "RunFacet",
        "attention": "Attention",
        "notices": "list[dict[str, Any]]",
        "inbound": "Inbound",
        "outbound": "Outbound",
        "delivery": "dict[str, Any]",
        "card": "Card",
        "budget": "Budget",
        "await_": "dict[str, Any]",
        "shuttle": "ShuttleFacet | None",
        "strand": "Strand",
        "seat": "Seat",
        "resource_hold": "dict[str, Any] | None",
        "scm": "dict[str, Any] | None",
        "produce": "dict[str, Any]",
        "schedule": "Schedule",
        "knowledge": "Knowledge",
        "name": "Name",
        "resources": "dict[str, Any]",
        "bolt": "Bolt | None",
        "change_token": "str",
    }
    for shape in (hud.HUD, *hud._SHAPE_OF.values()):
        assert shape.__dataclass_params__.frozen, shape.__name__


def test_each_fixed_field_reads_back_typed(tmp_path, monkeypatch):
    current, _text, _path = _built("busy_seat", tmp_path, monkeypatch)
    assert current.version == 1 and current.generated_at == "2026-09-14T16:26:40Z"
    assert current.run == hud.RunFacet(
        id="run-1", event_id="evt-1", status="pending", phase="running", attempt=1,
        env="worktree", runner="claude", repo="acme__widgets", branch="brr/run-1",
    )
    assert current.attention == hud.Attention(2, 0, True)
    assert current.inbound.current_event == "evt-1" and current.inbound.current_event_replyable
    assert [e["id"] for e in current.inbound.events] == ["evt-2", "evt-3"]
    assert current.outbound == hud.Outbound(0, 1, 0, True, [], None)
    assert current.card == hud.Card(True, "## Now\nshipping\n", 700, False, None)
    assert current.budget == hud.Budget(1800)
    assert current.await_ == {"armed": False}
    assert current.strand == hud.Strand(False, False)
    assert current.seat == hud.Seat(True)
    assert current.schedule.armed[0]["id"] == "morning"
    assert current.knowledge == hud.Knowledge("https://example.invalid/kb/")
    assert current.name == hud.Name(True)
    assert current.bolt == hud.Bolt(True, 2, "2026-09-14T16:20:00Z")
    assert current.resources["coexisting_runs"]["spawn_pool"] == {"floor": None, "queued": 1}
    assert current.tick is None and current.shuttle is None
    assert current.resource_hold is None
    assert len(current.change_token) > 0


def test_optional_shapes_read_back_typed(tmp_path, monkeypatch):
    ticked, _t, _p = _built("with_tick", tmp_path / "a", monkeypatch)
    assert ticked.tick == hud.Tick(n=2, at="2026-09-14T16:26:40Z")
    shuttled, _t, _p = _built("with_shuttle", tmp_path / "b", monkeypatch)
    assert shuttled.shuttle == hud.ShuttleFacet("awake", "dispatch", "2026-09-14T16:05:00Z", "run-1")
    held, _t, _p = _built("hold_parked", tmp_path / "c", monkeypatch)
    assert held.resource_hold["resume"] == "strands"
    noticed, _t, _p = _built("with_notices", tmp_path / "d", monkeypatch)
    assert [n["kind"] for n in noticed.notices] == ["refused", "dropped"]
    assert noticed.outbound.pending_outbox_files == ["reply.md"]
    assert noticed.outbound.oldest_pending_age_seconds == 42.0


def test_bolt_is_omitted_until_accepted_and_await_keeps_its_key(tmp_path, monkeypatch):
    current, _text, _path = _built("fresh_run", tmp_path, monkeypatch)
    payload = current.to_dict()
    assert current.bolt is None and "bolt" not in payload
    assert "await" in payload and "await_" not in payload


def test_from_json_reads_an_older_portal_leniently():
    """A portal written before move 2b has no ``tick``/``shuttle``: absent reads as None."""
    old = {
        "version": 1, "generated_at": "2026-09-01T00:00:00Z",
        "run": {"id": "run-1", "event_id": "evt-1", "status": "running", "phase": "running",
                "attempt": 1, "env": "worktree", "runner": "claude", "repo": None, "branch": None},
        "unknown_future_key": {"x": 1},
    }
    current = hud.HUD.from_dict(old)
    assert current.tick is None and current.shuttle is None and current.attention is None
    assert current.run.runner == "claude"
    assert "unknown_future_key" not in current.to_dict()
    with pytest.raises(TypeError):
        hud.HUD.from_dict(["not", "a", "portal"])  # type: ignore[arg-type]


def test_load_is_none_for_missing_or_garbage(tmp_path):
    assert hud.HUD.load(tmp_path / "nope.json") is None
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert hud.HUD.load(tmp_path / "bad.json") is None


def test_hud_imports_no_brr_module_at_import_time():
    """The hooks subprocess reads this module on every boundary."""
    tree = ast.parse((SRC / "hud.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level:
            pytest.fail(f"module-level relative import in hud.py: {ast.unparse(node)}")


# ── the thin call and the call sites ─────────────────────────────────


_WRITER_PARAMS = [
    "outbox_dir", "inbox_dir", "current_event_id", "task", "phase", "attempt",
    "runner_name", "runner_meta", "runner_catalog", "quality_escalation",
    "relay_consent", "card_state", "output_stats", "start_monotonic", "work_dir",
    "quota_summary", "refresh_levels", "cfg", "brr_dir", "account_context",
    "repo_label", "shuttle_home",
]


def test_the_daemon_writer_keeps_its_signature_and_is_a_thin_call(tmp_path, monkeypatch):
    assert list(inspect.signature(daemon._write_live_portal_state).parameters) == _WRITER_PARAMS
    assert [f.name for f in dataclasses.fields(hud.HUDInputs)] == _WRITER_PARAMS
    seen = []
    monkeypatch.setattr(hud, "write_live", lambda inputs: seen.append(inputs) or tmp_path)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    out = daemon._write_live_portal_state(
        tmp_path / "o", tmp_path / "i", "evt-1", task, phase="running",
        attempt=3, refresh_levels=False, repo_label="acme__widgets",
    )
    assert out == tmp_path
    assert seen == [hud.HUDInputs(
        outbox_dir=tmp_path / "o", inbox_dir=tmp_path / "i", current_event_id="evt-1",
        task=task, phase="running", attempt=3, refresh_levels=False,
        repo_label="acme__widgets",
    )]


def test_no_outbox_writes_nothing(tmp_path):
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    assert daemon._write_live_portal_state(None, tmp_path, "evt-1", task, phase="running") is None


def test_an_oserror_is_swallowed_like_the_writer_did(tmp_path, monkeypatch):
    def boom(_inputs):
        raise OSError("disk full")

    monkeypatch.setattr(hud, "build", boom)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    assert daemon._write_live_portal_state(
        tmp_path / "o", tmp_path / "i", "evt-1", task, phase="running",
    ) is None


def test_the_worker_builds_hud_inputs_at_its_five_call_sites():
    sites = {}
    for module in ("prepare", "dispatch", "stream"):
        tree = ast.parse((SRC / "worker" / f"{module}.py").read_text(encoding="utf-8"))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        assert not [c for c in calls if c.func.attr == "_write_live_portal_state"], module
        built = [
            c for c in calls
            if c.func.attr == "write_live"
            and c.args and isinstance(c.args[0], ast.Call)
            and ast.unparse(c.args[0].func) == "hud.HUDInputs"
        ]
        for call in built:
            names = {k.arg for k in call.args[0].keywords}
            assert names <= set(_WRITER_PARAMS), names - set(_WRITER_PARAMS)
        sites[module] = len(built)
    assert sites == {"prepare": 1, "dispatch": 1, "stream": 3}


# ── the verb ─────────────────────────────────────────────────────────


def test_hud_verb_default_is_the_chips_bar_line_from_the_same_object(tmp_path, monkeypatch, capsys):
    current, text, path = _built("busy_seat", tmp_path, monkeypatch)
    rc = main(["hud", "--outbox", str(path.parent)])
    out = capsys.readouterr().out
    assert rc == 0
    expected = hooks.format_delta(json.loads(text), outbox_dir=path.parent.resolve())
    assert expected and out == expected + "\n"
    assert out == hud.render_bar(current, outbox_dir=path.parent.resolve()) + "\n"
    assert "⇡0+1" in out  # the delivery chip, off the HUD's outbound


def test_hud_verb_json_prints_the_hud(tmp_path, monkeypatch, capsys):
    _current, text, path = _built("with_tick", tmp_path, monkeypatch)
    assert main(["hud", "--json", "--outbox", str(path.parent)]) == 0
    assert capsys.readouterr().out == text


def test_hud_verb_reads_the_wake_outbox_from_the_environment(tmp_path, monkeypatch, capsys):
    _current, text, path = _built("fresh_run", tmp_path, monkeypatch)
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(path.parent))
    assert main(["hud", "--json"]) == 0
    assert capsys.readouterr().out == text


def test_hud_verb_without_a_portal_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    assert main(["hud"]) == 1
    assert "no run outbox" in capsys.readouterr().err
    assert main(["hud", "--outbox", str(tmp_path)]) == 1
    assert "no readable portal-state.json" in capsys.readouterr().err


def test_hud_is_a_hidden_verb():
    from brr.cli import HIDDEN_COMMANDS

    assert "hud" in HIDDEN_COMMANDS


# ── produce as four kinds ───────────────────────────────────────────

SHA = "a06f566" + "0" * 33


def test_projection_from_relics_alone():
    records = [
        {"kind": "commit", "sha": "a06f566", "subject": "x"},
        {"kind": "branch", "name": "brr/work"},
        {"kind": "kb", "path": "design-the-loom.md"},
        {"kind": "issue", "number": 1977, "action": "closed"},
        {"kind": "pr", "number": 1978},
        {"kind": "item", "address": "the-loom#hud"},
        {"kind": "comment", "url": "https://example.invalid/c/1"},
    ]
    ledger = hud.project_produce([], records)
    assert ledger["counts"] == {"knot": 1, "heddle": 2, "card": 2, "page": 1}
    assert ledger["unmapped"] == 1
    assert ledger["sources"] == {"frame": 0, "relic": 6}
    assert [(e["kind"], e["ref"]) for e in ledger["last"]] == [
        ("knot", "a06f566"), ("heddle", "brr/work"), ("page", "design-the-loom.md"),
        ("card", "#1977"), ("heddle", "#1978"),
    ]
    assert all(e["at"] is None and e["source"] == "relic" for e in ledger["last"])


def test_projection_folds_the_frame_rows_first_and_counts_a_land_once():
    rows = [
        {"kind": "knot", "ref": SHA, "at": "2026-09-14T16:00:00Z", "verb": "land", "pr": 1975},
        {"kind": "page", "ref": "design-the-loom.md", "at": "2026-09-14T16:10:00Z"},
        {"kind": "heddle", "ref": "brr/the-hud-is-one-shape", "at": "2026-09-14T15:00:00Z"},
        {"kind": "loot", "ref": "x", "at": "2026-09-14T16:20:00Z"},
        {"kind": "card", "ref": "", "at": "2026-09-14T16:30:00Z"},
    ]
    records = [
        {"kind": "merge", "sha": SHA, "pr": 1975},          # land's second write
        {"kind": "commit", "sha": "a06f566", "subject": "x"},  # the same knot, abbreviated
        {"kind": "kb", "path": "design-the-loom.md"},       # the same page
        {"kind": "issue", "number": 7},
    ]
    ledger = hud.project_produce(rows, records)
    assert ledger["counts"] == {"knot": 1, "heddle": 1, "card": 1, "page": 1}
    assert ledger["unmapped"] == 2
    assert ledger["sources"] == {"frame": 3, "relic": 1}
    assert [(e["kind"], e["source"], e["at"]) for e in ledger["last"]] == [
        ("page", "frame", "2026-09-14T16:10:00Z"),
        ("knot", "frame", "2026-09-14T16:00:00Z"),
        ("heddle", "frame", "2026-09-14T15:00:00Z"),
        ("card", "relic", None),
    ]


def test_projection_keeps_the_last_five():
    rows = [
        {"kind": "page", "ref": f"p{i}.md", "at": f"2026-09-14T16:0{i}:00Z"} for i in range(8)
    ]
    ledger = hud.project_produce(rows, [])
    assert ledger["counts"]["page"] == 8
    assert [e["ref"] for e in ledger["last"]] == ["p7.md", "p6.md", "p5.md", "p4.md", "p3.md"]


def test_the_hud_projects_produce_without_a_ledger_file(tmp_path, monkeypatch):
    current, _text, _path = _built("with_produce", tmp_path, monkeypatch)
    ledger = current.produce["ledger"]
    assert ledger["counts"] == {"knot": 1, "heddle": 2, "card": 1, "page": 1}
    assert ledger["sources"] == {"frame": 0, "relic": 5}
    # The relics facet the chip's ⚒ reads is untouched beside it.
    assert current.produce["counts"] == {"branch": 1, "commit": 1, "issue": 1, "kb": 1, "pr": 1}


def test_the_hud_projects_produce_from_the_ledger_file(tmp_path, monkeypatch):
    frozen_host(monkeypatch)
    args, kwargs = SCENARIOS["with_produce"](tmp_path)
    task = args[3]
    run_dir = tmp_path / ".brr" / "runs" / task.id
    run_dir.mkdir(parents=True)
    (run_dir / hud.PRODUCE_LEDGER_NAME).write_text(
        json.dumps({"kind": "knot", "ref": SHA, "at": "2026-09-14T16:25:00Z", "by": "frame"}) + "\n"
        + "not json\n"
        + json.dumps({"kind": "card", "ref": "#1977", "at": "2026-09-14T16:24:00Z"}) + "\n",
        encoding="utf-8",
    )
    path = daemon._write_live_portal_state(*args, **kwargs)
    ledger = hud.HUD.load(path).produce["ledger"]
    # The frame's knot and the relic commit a06f566 are one knot; the frame's
    # card and the closed issue #1977 are one card.
    assert ledger["counts"] == {"knot": 1, "heddle": 2, "card": 1, "page": 1}
    assert ledger["sources"] == {"frame": 2, "relic": 3}
    assert ledger["last"][0] == {
        "kind": "knot", "ref": SHA, "at": "2026-09-14T16:25:00Z", "source": "frame",
    }


def test_the_ledger_is_projected_even_with_no_work_tree(tmp_path, monkeypatch):
    current, _text, _path = _built("fresh_run", tmp_path, monkeypatch)
    assert current.produce == {
        "known": False,
        "ledger": {
            "counts": {"knot": 0, "heddle": 0, "card": 0, "page": 0},
            "last": [], "unmapped": 0, "sources": {"frame": 0, "relic": 0},
        },
    }


def test_the_change_token_ignores_the_ledger(tmp_path, monkeypatch):
    current, _text, _path = _built("with_produce", tmp_path, monkeypatch)
    payload = current.to_dict()
    payload["produce"] = {"known": False}
    assert daemon._change_token(payload) == current.change_token


def test_land_writes_where_the_hud_reads():
    from brr.outbox import land

    assert land.PRODUCE_NAME == hud.PRODUCE_LEDGER_NAME


def test_hud_verb_produce_prints_what_the_frame_attests(tmp_path, monkeypatch, capsys):
    _current, _text, path = _built("with_produce", tmp_path, monkeypatch)
    assert main(["hud", "--produce", "--outbox", str(path.parent)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "ledger: knot 1 · heddle 2 · card 1 · page 1 · unmapped 0"
    assert out[1] == "sources: frame 0 · relic 5"
    assert out[2].startswith("  knot a06f566 (relic)")
    assert out[-1] == "relics: branch 1 · commit 1 · issue 1 · kb 1 · pr 1"


def test_hud_verb_produce_on_a_pre_move_5_portal(tmp_path, capsys):
    payload = json.loads((GOLDEN_DIR / "fresh_run.json").read_text(encoding="utf-8"))
    (tmp_path / hud.PORTAL_STATE_NAME).write_text(json.dumps(payload), encoding="utf-8")
    assert main(["hud", "--produce", "--outbox", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "ledger: absent (a portal written before move 5)",
        "relics: unknown (no work tree measured)",
    ]
