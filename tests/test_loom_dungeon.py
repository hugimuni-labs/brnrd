"""The dungeon's feed (``brr.loom.dungeon``) — design-the-dungeon.md §5.

Each reader is driven with the files the daemon actually writes, and each
absence case pins the contract's law: an instrument that was not measured is
absent, never a fabricated zero."""

from __future__ import annotations

import json
from pathlib import Path

from brr.loom import dungeon

NOW = 1_789_573_000.0


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _jsonl(path: Path, rows: list[dict]) -> Path:
    return _write(path, "".join(json.dumps(r) + "\n" for r in rows))


# ── fuel ─────────────────────────────────────────────────────────────────


def _claude_snapshot(brr: Path, *, session=57.0, week=44.0, fable=28.0, at_session=NOW + 8000, at_week=NOW + 245_000):
    _write(brr / ".claude-usage-levels.json", json.dumps({
        "plan_type": "Max", "updated_at": "2026-09-16T15:47:00Z",
        "quota": {"buckets": {"session": {"remaining_percentage": session},
                              "week": {"remaining_percentage": week},
                              "week_models": {"Fable": {"remaining_percentage": fable}}},
                  "session_resets_at": at_session, "week_resets_at": at_week},
    }))


def test_fuel_windows_carry_reset_clocks_and_the_shells_own_names(tmp_path):
    brr = tmp_path / ".brr"
    _claude_snapshot(brr)
    _write(brr / ".codex-usage-levels.json", json.dumps({
        "plan_type": "plus", "quota": {
            "primary_remaining_percent": 90.0, "primary_resets_at": NOW + 17_000, "primary_window_minutes": 300.0,
            "secondary_remaining_percent": 29.0, "secondary_resets_at": NOW + 230_000, "secondary_window_minutes": 10080.0,
        }}))
    fuel = dungeon.read_fuel(brr, None, "claude", NOW)
    names = [(b["name"], b["seat"], [w["name"] for w in b["windows"]]) for b in fuel["buckets"]]
    assert names == [("claude", True, ["session", "week", "Fable week"]), ("codex", False, ["5h", "7d"])]
    session = fuel["buckets"][0]["windows"][0]
    assert session["pct_left"] == 57 and session["resets_in_s"] == 8000 and session["window_s"] == 18000
    # the binding window is the one with the least left, per bucket
    assert [w["name"] for b in fuel["buckets"] for w in b["windows"] if w["binding"]] == ["Fable week", "7d"]


def test_forecast_measured_from_samples_beats_window_arithmetic(tmp_path):
    brr = tmp_path / ".brr"
    _claude_snapshot(brr, session=50.0, at_session=NOW + 9000)
    _jsonl(brr / "usage-samples.jsonl", [
        {"at": NOW - 3600, "shell": "claude", "used_percent": 30.0, "window_minutes": 300.0, "resets_at": NOW + 9000},
        {"at": NOW - 1800, "shell": "claude", "used_percent": 40.0, "window_minutes": 300.0, "resets_at": NOW + 9000},
        {"at": NOW, "shell": "claude", "used_percent": 50.0, "window_minutes": 300.0, "resets_at": NOW + 9000},
        # another window's rows never leak in
        {"at": NOW, "shell": "claude", "used_percent": 99.0, "window_minutes": 10080.0, "resets_at": NOW + 9000},
    ])
    session = dungeon.read_fuel(brr, None, "claude", NOW)["buckets"][0]["windows"][0]
    fc = session["forecast"]
    assert fc["source"] == "measured" and fc["rate_pct_per_h"] == 20.0 and fc["span_s"] == 3600
    assert fc["dry_in_s"] == 9000 and fc["resets_in_s"] == 9000 and fc["dries_first"] is False
    assert fc["pct_left_at_reset"] == 0.0


def test_forecast_window_fallback_and_the_reading_that_dries_first(tmp_path):
    brr = tmp_path / ".brr"
    # 60 % used with 2/5 of the 5h window elapsed ⇒ 30 %/h; 40 left ⇒ dry in 80 min, reset in 3 h
    _claude_snapshot(brr, session=40.0, at_session=NOW + 3 * 3600)
    fc = dungeon.read_fuel(brr, None, "claude", NOW)["buckets"][0]["windows"][0]["forecast"]
    assert fc["source"] == "window" and fc["rate_pct_per_h"] == 30.0
    assert fc["dry_in_s"] == 4800 and fc["dries_first"] is True and fc["pct_left_at_reset"] == -50.0


def test_forecast_is_absent_when_nothing_measurable_says(tmp_path):
    brr = tmp_path / ".brr"
    # three minutes into a 5h window: under the elapsed floor, no samples
    _claude_snapshot(brr, session=99.0, at_session=NOW + 18000 - 180)
    window = dungeon.read_fuel(brr, None, "claude", NOW)["buckets"][0]["windows"][0]
    assert window["forecast"] is None
    assert dungeon.forecast(50.0, None, 18000, NOW) is None          # no reset clock ⇒ no forecast


def test_fuel_without_snapshots_is_empty_not_zero(tmp_path):
    assert dungeon.read_fuel(tmp_path / ".brr", None, "claude", NOW) == {"buckets": []}


def test_fuel_prefers_the_runs_own_snapshot_beside_its_portal(tmp_path):
    brr = tmp_path / ".brr"
    _claude_snapshot(brr, session=10.0)
    outbox = brr / "outbox" / "evt-live"
    _write(outbox / ".claude-usage-levels.json", json.dumps({
        "quota": {"buckets": {"session": {"remaining_percentage": 77.0}}, "session_resets_at": NOW + 100}}))
    assert dungeon.read_fuel(brr, outbox, "claude", NOW)["buckets"][0]["windows"][0]["pct_left"] == 77


# ── pack ─────────────────────────────────────────────────────────────────


def test_pack_reads_the_wake_manifest(tmp_path):
    brr = tmp_path / ".brr"
    _write(brr / "runs" / "run-1" / "wake-manifest.json", json.dumps({"blocks": [
        {"name": "boot-kernel", "label": "Boot kernel", "bytes_kept": 1989, "budget_bytes": None, "present": True},
        {"name": "notebook", "label": "notebook.md", "bytes_kept": 4200, "budget_bytes": 20480},
        {"no": "name"},
    ]}))
    pack = dungeon.read_pack(brr, "run-1", {"ctx_tokens": 162_500, "full": {"resources": {"context_window": {"window_tokens": 200_000}}}})
    assert [b["name"] for b in pack["blocks"]] == ["boot-kernel", "notebook"]
    assert pack["bytes"] == 6189 and pack["ctx_tokens"] == 162_500 and pack["window_tokens"] == 200_000
    assert dungeon.read_pack(brr, "run-none", None) is None
    assert dungeon.read_pack(brr, None, None) is None


def test_pack_carries_each_block_source_and_what_the_render_cut(tmp_path):
    """The three readings the byte count alone cannot give, and their refusals.

    ``source`` is verbatim because it is a join key against
    ``beads[].chunks[].path``; ``source_rel`` is the display form and is
    absent when the file is not under the checkout. ``cut`` is what the render
    dropped, and 1 is the manifest's "nothing was cut" sentinel, not a cut.
    A synthesized block gets ``None`` for both paths, and that ``None`` means
    *unjoinable*, never *untouched* — the page draws no mark from it."""
    brr = tmp_path / ".brr"
    root = str(tmp_path)
    _write(brr / "runs" / "run-1" / "wake-manifest.json", json.dumps({"blocks": [
        {"name": "portals", "bytes_kept": 12130, "bytes_cut": 59752, "present": True,
         "owner": "product", "authority": "substrate",
         "sources": [{"path": root + "/src/brr/docs/portals.md", "store": "product-prompt"}]},
        {"name": "weave", "bytes_kept": 4976, "bytes_cut": 1, "present": True,
         "owner": "product", "sources": [{"path": root + "/src/brr/prompts/weave.md"}]},
        {"name": "prior-run", "bytes_kept": 1126, "bytes_cut": 4701, "present": True,
         "owner": "resident", "sources": [{"path": "/elsewhere/home/runs/body.md"}]},
        {"name": "work-surface", "bytes_kept": 47917, "present": True, "owner": "resident",
         "authority": "surface", "sources": [{"synthesized": True}]},
    ]}))
    blocks = {b["name"]: b for b in dungeon.read_pack(brr, "run-1", None)["blocks"]}

    portals = blocks["portals"]
    assert portals["cut"] == 59752 and portals["authority"] == "substrate"
    assert portals["source"] == root + "/src/brr/docs/portals.md"   # verbatim: the join key
    assert portals["source_rel"] == "src/brr/docs/portals.md"       # relative: the label

    assert blocks["weave"]["cut"] is None                           # 1 is the sentinel
    assert blocks["weave"]["source_rel"] == "src/brr/prompts/weave.md"

    off = blocks["prior-run"]                                       # outside the checkout
    assert off["cut"] == 4701 and off["source"] == "/elsewhere/home/runs/body.md"
    assert off["source_rel"] is None

    made = blocks["work-surface"]                                   # nothing to join
    assert made["source"] is None and made["source_rel"] is None and made["cut"] is None


# ── warp enrichment ──────────────────────────────────────────────────────


def test_enrich_warp_adds_opens_visited_and_stake(tmp_path):
    home = tmp_path / "home"
    _write(home / "surface" / "warp" / "w-1.md", "# One\n\ntype: decision\nstake: 200k, his\n")
    _write(home / "surface" / "warp" / "w-2.md", "# Two\n\ntype: action\nneeds: w-1\n")
    warp = {"goals": [], "items": [
        {"id": "w-1", "type": "decision", "needs": [], "state": "ready"},
        {"id": "w-2", "type": "action", "needs": ["w-1"], "state": "held"},
        {"id": "w-3", "type": "action", "needs": ["w-1"], "state": "done"},   # done ⇒ opens nothing
    ]}
    tree = {"home": {"places": [{"path": "surface/warp/w-2.md", "last": "2026-09-16T13:09:28Z"}]}}
    out = dungeon.enrich_warp(warp, home, tree)
    by = {i["id"]: i for i in out["items"]}
    assert by["w-1"]["opens"] == ["w-2"] and by["w-1"]["stake"] == "200k, his" and by["w-1"]["visited_at"] is None
    assert by["w-2"]["visited_at"] == "2026-09-16T13:09:28Z" and by["w-2"]["opens"] == [] and by["w-2"]["stake"] is None
    assert all(i["receipt"] is None for i in out["items"])


# ── the actor's room ─────────────────────────────────────────────────────


def test_shuttle_place_prefers_listening_then_taken_then_the_last_bead():
    warp = {"items": [{"id": "w-9", "taken": "run-1", "state": "ready"}]}
    beads = [{"place_kind": "file"}, {"place_kind": "forge"}]
    assert dungeon.shuttle_place({"state": "listening"}, "run-1", warp, beads) == "shed"
    assert dungeon.shuttle_place({"state": "awake"}, "run-1", warp, beads) == "w-9"
    assert dungeon.shuttle_place({"state": "awake"}, "run-2", warp, beads) == "forge"
    assert dungeon.shuttle_place({"state": "awake"}, "run-2", warp, [{"place_kind": "file"}]) == "archive"
    assert dungeon.shuttle_place({"state": "awake"}, None, {"items": []}, []) == "shed"
    assert dungeon.shuttle_place(None, None, {"items": []}, []) is None


# ── relics ───────────────────────────────────────────────────────────────


def test_relics_drop_in_their_room(tmp_path):
    outbox = tmp_path / "outbox"
    _write(outbox / ".relics.jsonl", '{"action":"opened","kind":"issue","number":1995}\n'
                                     '{"kind":"page","path":"kb/design-the-dungeon.md"}\nnot json\n')
    out = dungeon.read_relics(outbox)
    assert [(r["kind"], r["place"], r["number"], r["ref"]) for r in out] == [
        ("issue", "forge", 1995, None), ("page", "archive", None, "kb/design-the-dungeon.md")]
    assert dungeon.read_relics(None) == [] and dungeon.read_relics(tmp_path / "none") == []
