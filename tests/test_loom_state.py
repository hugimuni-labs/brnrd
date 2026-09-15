"""The loom feed's state (``brr.loom.state.build``) from fixture files.

One fixture machine: a shuttle.json, one live run with its outbox (portal,
.card, controls) and boundaries.jsonl, a submitted strand, two topics each
with an index, four warp items with a needs chain and a goal, a run-ledger
tail whose older run has its own boundaries, and one bench fold.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from brr.loom import state

NOW = 1_790_000_000.0
RUN = "run-260922-1000-live"
CHILD = "run-260922-1010-kid1"
OLD = "run-260921-0800-old1"


def iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


@pytest.fixture(autouse=True)
def _strict(monkeypatch):
    monkeypatch.setenv("BRNRD_LOOM_STRICT", "1")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _jsonl(path: Path, rows: list[dict]) -> Path:
    return _write(path, "".join(json.dumps(r) + "\n" for r in rows))


def _run_md(brr: Path, run_id: str, outbox: Path, **meta: str) -> None:
    lines = [
        "---",
        f"id: {run_id}",
        f"event_id: evt-{run_id}",
        f"status: {meta.pop('status', 'running')}",
        "source: spawn",
        f"outbox_path: {outbox}",
    ]
    lines += [f"{k}: {v}" for k, v in meta.items()]
    lines += ["---", "", "body"]
    _write(brr / "runs" / run_id / "run.md", "\n".join(lines) + "\n")


def _transitions(at: float) -> str:
    return json.dumps([{"at": iso(at), "by": None, "from": "pending", "to": "running", "why": "update_status"}])


@pytest.fixture
def machine(tmp_path: Path) -> dict:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    brr = repo / ".brr"
    (repo / "src" / "brr").mkdir(parents=True)

    # ── the seat ──
    transitions = [
        {"at": iso(NOW - 3600 + i), "from": "awake" if i % 2 else "listening",
         "to": "listening" if i % 2 else "awake", "why": f"t{i}", "by": "daemon", "tick": i}
        for i in range(14)
    ]
    _write(home / "shuttle.json", json.dumps({
        "key": "acc", "state": "awake", "why": "event_dispatched", "run_id": RUN,
        "repo_root": str(repo), "conversation_key": "cloud:x", "since": iso(NOW - 60),
        "transitions": transitions,
    }))

    # ── the live run ──
    outbox = brr / "outbox" / "evt-live"
    _run_md(brr, RUN, outbox, runner_name="claude-fable", transitions=_transitions(NOW - 600))
    _write(outbox / "portal-state.json", json.dumps({
        "run": {"id": RUN, "repo": "acme/widgets", "topic": "the-loom"},
        "budget": {"elapsed_seconds": 600},
        "strand": {"is_strand": False, "submitted": False},
        "heddles": [{"slug": "the-loom", "rune": "ᛗ", "brightness": 0.9, "last_match_at": iso(NOW - 30)}],
        "resources": {
            "allowance": {"status": "known", "spent": 2_200_000, "tokens": 20_000_000, "pct": 11.0},
            "quota": {"status": "known", "summary": "session 85% left (resets 3pm); week 56% left (resets Sep 19)"},
            "runner": {"catalog": [{"name": "claude-fable", "shell": "claude", "model": "fable"}]},
            "coexisting_runs": {"owned_children": [
                {"run_id": CHILD, "title": "the kid", "weighted": 4200},
            ]},
        },
    }))
    _write(outbox / ".card", "\n".join([
        "## Now", "weaving the feed", "",
        "## Plan", "- [x] read", "- [ ] write", "",
        "## Vector", "- steer one", "- steer two", "",
        "## Ledger", "- spend: 2.2m", "- strands: 1 live", "",
    ]))
    _write(outbox / ".name", "the live seat\n")
    _write(outbox / ".mood", "curious\nnarration\n")
    _write(outbox / ".topic", "the-loom\n")
    long_detail = "cat src/brr/hud.py " + "x" * 400
    _jsonl(brr / "runs" / RUN / "boundaries.jsonl", [
        {"at": iso(NOW - 120), "phase": "pre-tool", "place": {"path": None, "paths": []}, "inject": None},
        {"at": iso(NOW - 110), "act": "probe", "detail": long_detail, "cwd": str(repo),
         "ctx": {"delta": 700, "tokens_after": 272_600},
         "place": {"path": str(repo / "src" / "brr" / "hud.py"), "paths": [str(repo / "src" / "brr" / "hud.py")]},
         "inject": "⌁[b·o·d]: ⏱ 10m │ spend 2.2m\n- a line"},
        {"at": iso(NOW - 100), "act": "mutate", "detail": "write the post", "cwd": str(repo),
         "ctx": {"delta": 300, "tokens_after": 272_900},
         "place": {"path": str(brr / "outbox" / "evt-live" / ".card"),
                   "paths": [str(brr / "outbox" / "evt-live" / ".card"), "media/post/a.png"]},
         "inject": None},
    ])

    # ── the strand, submitted ──
    kid_outbox = brr / "outbox" / "evt-kid"
    _run_md(brr, CHILD, kid_outbox, shell="codex", core="astra", title="the kid",
            spawn_parent_run_id=RUN, spawn_allowance_tokens="15000000", transitions=_transitions(NOW - 500))
    _write(kid_outbox / "portal-state.json", json.dumps({"run": {"id": CHILD}, "strand": {"is_strand": True, "submitted": True}}))
    _jsonl(kid_outbox / ".relics.jsonl", [{"kind": "commit", "sha": "abc1234"}, {"kind": "pr", "number": 12}])
    kid_rows = [
        {"at": iso(NOW - 400 + i), "act": "mutate", "place": {"path": f"src/brr/f{i % 15}.py", "paths": []}}
        for i in range(30)
    ]
    kid_rows.append({"at": iso(NOW - 300), "phase": "pre-tool", "place": {"path": "src/brr/nope.py", "paths": []}})
    _jsonl(brr / "runs" / CHILD / "boundaries.jsonl", kid_rows)

    # ── topics and their indexes ──
    topics = home / "surface" / "topics"
    _write(topics / "the-loom.md", "---\nrune: ᛗ\nsignature:\n  places: [src/brr/hud.py]\n  words: [loom]\n---\n# The loom\n")
    _write(topics / "the-post.md", "---\nrune: ᛈ\nsignature:\n  places: [media/**]\n  words: [post]\n---\n# The post\n")
    _jsonl(topics / "the-loom.index.jsonl", [{"kind": "message", "ref": "m1", "at": iso(NOW - 7200), "run": OLD}])
    _jsonl(topics / "the-post.index.jsonl", [
        {"kind": "strand", "ref": "s1", "at": iso(NOW - 7000), "run": OLD},
        {"kind": "message", "ref": "m2", "at": iso(NOW - 50), "run": RUN},
    ])

    # ── the warp: w-2 needs w-1 (done) ⇒ ready; w-3 needs w-4 (open) ⇒ held ──
    warp = home / "surface" / "warp"
    _write(warp / "w-1.md", "# One\n\ntype: action\ntopics: the-loom\ndone: 2026-09-01 run-1\n")
    _write(warp / "w-2.md", "# Two\n\ntype: decision\ntopics: the-loom\nneeds: w-1\n")
    _write(warp / "w-3.md", "# Three\n\ntype: action\ntopics: the-post\nneeds: w-4\ntaken: run-260922-0900-aaaa run-260922-0930-bbbb\n")
    _write(warp / "w-4.md", "# Four\n\ntype: preparation\ntopics: the-post\n")
    _write(warp / "g-1.md", "# The goal\n\ntype: goal\nmetric: stars\n")

    # ── the run ledger; the old run's own boundaries, five hours back ──
    _jsonl(brr / "run-ledger.jsonl", [
        {"run_id": OLD, "started_at": iso(NOW - 20000), "ended_at": None, "name": "stale copy"},
        {"run_id": RUN, "started_at": iso(NOW - 30000), "ended_at": iso(NOW - 29000), "name": "an earlier stint"},
        {"run_id": OLD, "started_at": iso(NOW - 20000), "ended_at": iso(NOW - 18000), "name": "the old run",
         "runner_shell": "claude", "runner_core": "opus", "parent_run_id": None,
         "tokens_input": 10, "tokens_output": 20, "tokens_cache_creation": 30, "tokens_cache_read": 999_999,
         "external_refs": [
             {"kind": "commit", "sha": "a1"}, {"kind": "merge", "pr": 7, "sha": "b2"},
             {"kind": "pr", "number": 7}, {"kind": "pr", "number": 9}, {"kind": "kb", "path": "p.md"},
         ]},
    ])
    _jsonl(brr / "runs" / OLD / "boundaries.jsonl", [
        {"at": iso(NOW - 5 * 3600), "act": "mutate", "place": {"path": "src/brr/old.py", "paths": ["src/brr/old.py"]}},
        {"at": iso(NOW - 5 * 3600), "act": "probe", "place": {"path": str(repo / "src/brr/hud.py"), "paths": []}},
    ])

    # ── the bench ──
    _write(home / "bench" / "acme__widgets" / "src" / "brr" / "daemon.py" / "abc1234.md",
           "---\nplace: src/brr/daemon.py\ncommit: abc1234\nmark: keep\nmark: drop\n---\nbody\n")
    return {"repo": repo, "home": home, "brr": brr}


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


def test_every_contract_key_present_in_order(machine):
    out = state.build(machine["repo"], machine["home"], now=NOW)
    assert tuple(out) == state.KEYS
    assert out["at"] == iso(NOW) and out["beat_ms"] == 600 and out["repo"] == "acme/widgets"
    json.dumps(out)  # the wire shape is JSON


def test_absence_is_null_or_empty_never_a_guess(tmp_path):
    out = state.build(tmp_path / "nowhere", tmp_path / "nohome", now=NOW)
    assert tuple(out) == state.KEYS
    assert out["repo"] is None and out["shuttle"] is None and out["run"] is None and out["hud"] is None
    assert out["heddles"] == [] and out["beads"] == []
    assert out["warp"] == {"goals": [], "items": []}
    assert out["cloth"] == {"rows": []} and out["tree"] == {"places": []} and out["bench"] == {"folds": []}
    assert not (tmp_path / "nohome" / "shuttle.json").exists()  # the loader would have minted one


def test_build_writes_nothing(machine, tmp_path):
    before = _snapshot(tmp_path)
    state.build(machine["repo"], machine["home"], now=NOW)
    assert _snapshot(tmp_path) == before


def test_released_seat_serves_no_run_but_the_rest(machine):
    record = json.loads((machine["home"] / "shuttle.json").read_text())
    record["state"] = "released"
    (machine["home"] / "shuttle.json").write_text(json.dumps(record))
    out = state.build(machine["repo"], machine["home"], now=NOW)
    assert out["run"] is None and out["hud"] is None and out["beads"] == []
    assert out["shuttle"]["state"] == "released"
    assert [i["id"] for i in out["warp"]["items"]] == ["w-1", "w-2", "w-3", "w-4"]
    assert [r["run"] for r in out["cloth"]["rows"]] == [RUN, OLD]  # ledger order: each run's last entry
    assert out["cloth"]["rows"][0]["ended"] == iso(NOW - 29000)  # not live now: the ledger's end stands


def test_shuttle_keeps_the_last_twelve_transitions(machine):
    shuttle = state.build(machine["repo"], machine["home"], now=NOW)["shuttle"]
    assert shuttle["state"] == "awake" and shuttle["run_id"] == RUN and shuttle["why"] == "event_dispatched"
    assert [t["why"] for t in shuttle["transitions"]] == [f"t{i}" for i in range(2, 14)]
    assert set(shuttle["transitions"][0]) == {"at", "from", "to", "why"}


def test_run_facet_reads_controls_and_card_halves(machine):
    run = state.build(machine["repo"], machine["home"], now=NOW)["run"]
    assert run["id"] == RUN and run["name"] == "the live seat" and run["mood"] == "curious"
    assert run["mood_glyph"]  # emotes knows "curious"
    assert run["started"] == iso(NOW - 600) and run["elapsed_s"] == 600
    assert (run["topic"], run["shell"], run["core"]) == ("the-loom", "claude", "fable")
    card = run["card"]
    assert card["now"] == "weaving the feed"
    assert card["plan"] == [{"text": "read", "done": True}, {"text": "write", "done": False}]
    assert card["vector"] == ["steer one", "steer two"]
    assert card["ledger"] == ["- spend: 2.2m", "- strands: 1 live"]


def test_hud_reads_portal_chip_quota_spend_and_strands(machine):
    hud = state.build(machine["repo"], machine["home"], now=NOW)["hud"]
    assert hud["chip"] == "⌁[b·o·d]: ⏱ 10m │ spend 2.2m"
    assert hud["quota"] == {"session_pct_left": 85, "week_pct_left": 56}
    assert hud["spend"] == {"tokens": 2_200_000, "allowance_tokens": 20_000_000, "pct": 11}
    assert hud["ctx_tokens"] == 272_900
    (kid,) = hud["strands"]
    assert {k: kid[k] for k in ("id", "title", "status", "spent", "allowance")} == {
        "id": CHILD, "title": "the kid", "status": "submitted", "spent": 4200, "allowance": 15_000_000,
    }
    # the last 12 distinct places of its own log, newest first; the pre-tool row is no bead
    assert kid["places"] == [f"src/brr/f{i % 15}.py" for i in range(29, 17, -1)]
    assert kid["last_bead_at"] == iso(NOW - 400 + 29)


def test_hud_full_is_the_typed_hud_as_hud_json_prints_it(machine):
    from brr import hud as hud_mod

    portal = json.loads((machine["brr"] / "outbox" / "evt-live" / "portal-state.json").read_text())
    full = state.build(machine["repo"], machine["home"], now=NOW)["hud"]["full"]
    assert full == json.loads(hud_mod.HUD.from_dict(portal).to_json())
    assert full["run"]["id"] == RUN and "attention" in full and "resources" in full


def test_a_strand_without_a_log_has_no_position(machine):
    (machine["brr"] / "runs" / CHILD / "boundaries.jsonl").unlink()
    (kid,) = state.build(machine["repo"], machine["home"], now=NOW)["hud"]["strands"]
    assert kid["places"] == [] and kid["last_bead_at"] is None


def test_quota_labels_stay_the_shells_own(machine):
    portal_path = machine["brr"] / "outbox" / "evt-live" / "portal-state.json"
    portal = json.loads(portal_path.read_text())
    portal["resources"]["quota"]["summary"] = "5h 55% left (resets 23:25Z); 7d 38% left (resets 08:26Z)"
    portal_path.write_text(json.dumps(portal))
    hud = state.build(machine["repo"], machine["home"], now=NOW)["hud"]
    assert hud["quota"] == {"5h_pct_left": 55, "7d_pct_left": 38}


def test_quota_prefers_the_runner_snapshot(machine):
    outbox = machine["brr"] / "outbox" / "evt-live"
    _write(outbox / ".claude-usage-levels.json", json.dumps({"quota": {"buckets": {
        "session": {"remaining_percentage": 70.0}, "week": {"remaining_percentage": 50.0},
        "week_models": {"Fable": {"remaining_percentage": 41.0}},
    }}}))
    hud = state.build(machine["repo"], machine["home"], now=NOW)["hud"]
    assert hud["quota"] == {"session_pct_left": 70, "week_pct_left": 50, "fable_pct_left": 41}


def test_heddles_join_topic_files_with_the_portal_lit_list(machine):
    heddles = state.build(machine["repo"], machine["home"], now=NOW)["heddles"]
    by = {h["slug"]: h for h in heddles}
    assert by["the-loom"]["lit"] == 0.9 and by["the-loom"]["last_lit"] == iso(NOW - 30) and by["the-loom"]["rune"] == "ᛗ"
    assert by["the-post"]["lit"] == 0.0 and by["the-post"]["last_lit"] is None
    assert by["the-post"]["signature"] == {"places": ["media/**"], "words": ["post"], "produce": [], "threads": []}


def test_warp_state_is_derived(machine):
    warp = state.build(machine["repo"], machine["home"], now=NOW)["warp"]
    assert warp["goals"] == [{"id": "g-1", "title": "The goal", "metric": "stars"}]
    by = {i["id"]: i for i in warp["items"]}
    assert by["w-1"]["state"] == "done"      # done stays done
    assert by["w-2"]["state"] == "ready"     # its need is done
    assert by["w-3"]["state"] == "held"      # needs w-4, still open
    assert by["w-4"]["state"] == "ready"
    assert by["w-3"]["taken"] == "run-260922-0930-bbbb" and by["w-4"]["taken"] is None
    assert by["w-2"] == {"id": "w-2", "type": "decision", "title": "Two", "topics": ["the-loom"],
                         "needs": ["w-1"], "state": "ready", "taken": None}


def test_beads_carry_places_and_topics(machine):
    beads = state.build(machine["repo"], machine["home"], now=NOW)["beads"]
    assert [b["act"] for b in beads] == ["probe", "mutate"]  # the pre-tool row is no bead
    probe, mutate = beads
    assert probe["places"] == ["src/brr/hud.py"]
    assert probe["topics"] == ["the-loom"]
    assert len(probe["detail"]) == 160 and (probe["ctx_after"], probe["delta"]) == (272_600, 700)
    assert mutate["places"] == ["media/post/a.png"]  # the outbox path is runtime, not the tree
    assert mutate["topics"] == ["the-post"]


def test_cloth_joins_the_ledger_with_index_refs(machine):
    rows = state.build(machine["repo"], machine["home"], now=NOW)["cloth"]["rows"]
    assert [r["run"] for r in rows] == [RUN, OLD, CHILD]  # ledger order (each run's last entry), then open strands
    live, old, kid = rows
    assert old["name"] == "the old run"  # the run's last ledger entry wins
    assert old["topics"] == ["the-loom", "the-post"]
    assert (old["prs"], old["knots"], old["pages"], old["tokens"]) == ([7, 9], 2, 1, 60)
    assert (old["ended"], old["shell"], old["core"], old["parent"]) == (iso(NOW - 18000), "claude", "opus", None)
    assert old["duration_s"] == 2000
    # the live run's earlier stint is in the ledger; its end is still null
    assert live["ended"] is None and live["duration_s"] is None
    assert live["topics"] == ["the-post"] and live["name"] == "an earlier stint"
    assert kid["ended"] is None and kid["duration_s"] is None
    assert (kid["parent"], kid["shell"], kid["core"], kid["prs"], kid["knots"]) == (RUN, "codex", "astra", [12], 1)


def test_tree_heat_decays_by_the_hour(machine):
    places = {p["path"]: p for p in state.build(machine["repo"], machine["home"], now=NOW)["tree"]["places"]}
    assert {"src/brr/hud.py", "media/post/a.png", "src/brr/old.py"} <= set(places)
    assert places["src/brr/f14.py"]["knots"] == 1  # the open strand's own log counts too
    assert places["src/brr/old.py"]["heat"] == pytest.approx(0.5 ** 5, abs=1e-4)
    assert places["src/brr/old.py"]["knots"] == 1  # the old run mutated it
    assert places["src/brr/hud.py"]["heat"] > 0.95  # touched by a bead two minutes ago
    assert places["src/brr/hud.py"]["last"] == iso(NOW - 110) and places["src/brr/hud.py"]["knots"] == 0
    assert places["src/brr/hud.py"]["topics"] == ["the-loom"]
    later = {p["path"]: p for p in state.build(machine["repo"], machine["home"], now=NOW + 3600)["tree"]["places"]}
    assert later["src/brr/old.py"]["heat"] == pytest.approx(0.5 ** 6, abs=1e-4)


def test_bench_folds(machine):
    bench = state.build(machine["repo"], machine["home"], now=NOW)["bench"]
    assert bench == {"folds": [{
        "path": "acme__widgets/src/brr/daemon.py/abc1234", "place": "src/brr/daemon.py", "marks": ["keep", "drop"],
    }]}


def test_a_broken_part_degrades_without_strict(machine, monkeypatch):
    monkeypatch.delenv("BRNRD_LOOM_STRICT")
    monkeypatch.setattr(state, "read_warp", lambda home: 1 / 0)
    out = state.build(machine["repo"], machine["home"], now=NOW)
    assert out["warp"] == {"goals": [], "items": []} and out["run"]["id"] == RUN


def test_tail_rows_reads_the_end_of_a_long_file(tmp_path):
    path = _jsonl(tmp_path / "rows.jsonl", [{"n": i, "pad": "x" * 50} for i in range(2000)])
    rows = state.tail_rows(path, 3, cap=1000)
    assert [r["n"] for r in rows] == [1997, 1998, 1999]
