"""The HUD — the type, its round trip, the thin call, the verb, and produce as four kinds.

The byte-level proof that the split changed nothing lives in
``test_hud_golden.py``; this module pins what the shape *is*.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from brr import daemon, hud
from brr.run import Run


SRC = Path(hud.__file__).parent


# ── the shape ────────────────────────────────────────────────────────


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
    "place_root", "quota_summary", "refresh_levels", "cfg", "brr_dir", "account_context",
    "repo_label", "shuttle_home",
]


def test_the_daemon_writer_keeps_its_signature_and_is_a_thin_call(tmp_path, monkeypatch):
    assert list(inspect.signature(daemon._write_live_portal_state).parameters) == _WRITER_PARAMS
    # `events` is daemon2's seam (#2187): its door hands the projected list in.
    # The engine-1 writer never passes it, so its signature stays pinned.
    assert [f.name for f in dataclasses.fields(hud.HUDInputs)] == [*_WRITER_PARAMS, "events"]
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


def test_hud_is_a_hidden_verb():
    from brr.cli import HIDDEN_COMMANDS

    assert "hud" in HIDDEN_COMMANDS


# ── produce as four kinds ───────────────────────────────────────────

SHA = "a06f566" + "0" * 33


def test_land_writes_where_the_hud_reads():
    from brr.outbox import land

    assert land.PRODUCE_NAME == hud.PRODUCE_LEDGER_NAME


def test_build_hands_every_transcript_reader_this_runs_own_start(
    tmp_path, monkeypatch,
):
    """The floor `allowance.latest_claude_transcript` needs on `env: host`.

    `hud.build` is the one place that holds the run's start, so it is the one
    place that can tell a reader which transcripts are this run's. Pins the
    wiring: all three readers get the same wall-clock start, derived from the
    monotonic stamp (one start, never two that can disagree).
    """
    import time as _time

    from brr.run import Run

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        daemon, "_record_context_window",
        lambda *_a, not_before=None, **_k: seen.__setitem__("ctx", not_before),
    )
    monkeypatch.setattr(
        daemon, "_record_boot_cost",
        lambda *_a, not_before=None, **_k: seen.__setitem__("boot", not_before),
    )
    monkeypatch.setattr(
        daemon, "_collect_allowance_facet",
        lambda *_a, not_before=None, **_k: seen.__setitem__("allowance", not_before),
    )
    monkeypatch.setattr(
        daemon, "_collect_levels", lambda *_a, **_k: ({}, frozenset()),
    )

    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    started_ago = 600.0
    before = _time.time() - started_ago
    hud.build(hud.HUDInputs(
        outbox_dir=tmp_path / "outbox",
        inbox_dir=inbox_dir,
        current_event_id="evt-1",
        task=task,
        phase="running",
        runner_name="claude",
        work_dir=tmp_path / "work",
        start_monotonic=_time.monotonic() - started_ago,
        brr_dir=tmp_path / ".brr",
    ))

    assert set(seen) == {"ctx", "boot", "allowance"}
    assert len({round(float(v), 3) for v in seen.values()}) == 1
    # The run's own start, in wall-clock seconds — not "now", not None.
    assert before - 2 <= float(seen["ctx"]) <= before + 2
