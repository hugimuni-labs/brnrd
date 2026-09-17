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
STAMP = "run-260921-0700-stmp"
CLAIM = "run-260921-0600-clam"


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
                {"run_id": CHILD, "event_id": "evt-kid", "title": "the kid", "weighted": 4200},
                {"run_id": "", "event_id": "evt-queued", "title": "not started"},
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
    _write(outbox / ".topics", "topics: the-clockwork\n")
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
                   "paths": [str(brr / "outbox" / "evt-live" / ".card"),
                             str(brr / "worktrees" / RUN / "src" / "brr" / "w.py"), "media/post/a.png"]},
         "inject": None},
        # where the act looked: the file the command names, not the cwd the frame recorded
        {"at": iso(NOW - 90), "act": "probe", "detail": "sed -n 1,9p src/brr/real.py > /tmp/scratch.txt", "cwd": str(repo),
         "place": {"path": str(repo), "paths": [str(repo)]}, "inject": None},
        {"at": iso(NOW - 80), "act": "orient",
         "detail": f"cat {home}/knowledge/repos/acme/design.md {home}/shuttle.json", "cwd": str(repo), "inject": None},
    ])
    _write(repo / "src" / "brr" / "real.py", "print('real')\n")
    _write(home / "knowledge" / "repos" / "acme" / "design.md", "# design\n")

    # ── the strand, submitted ──
    kid_outbox = brr / "outbox" / "evt-kid"
    _run_md(brr, CHILD, kid_outbox, shell="codex", core="astra", title="the kid",
            spawn_parent_run_id=RUN, spawn_allowance_tokens="15000000", transitions=_transitions(NOW - 500))
    _write(kid_outbox / "portal-state.json", json.dumps({"run": {"id": CHILD}, "strand": {"is_strand": True, "submitted": True}}))
    _jsonl(kid_outbox / ".relics.jsonl", [{"kind": "commit", "sha": "abc1234"}, {"kind": "pr", "number": 12}])
    # A worktree strand's rows carry worktree-absolute paths and a worktree cwd
    # (every other row here); relative ones are taken as repo places already.
    kid_tree = brr / "worktrees" / CHILD
    kid_rows = [
        {"at": iso(NOW - 400 + i), "act": "mutate", "cwd": str(kid_tree),
         "place": {"path": str(kid_tree / "src" / "brr" / f"f{i % 15}.py") if i % 2 == 0 else f"src/brr/f{i % 15}.py",
                   "paths": []}}
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
    _run_md(brr, STAMP, brr / "outbox" / "evt-stamp", status="done", topic="the-loom")
    from brr import account

    node = home / "runs" / account.slug_repo_label("acme/widgets")
    _write(node / CLAIM / "topics.md", "topics: the-post not-minted-yet\n")  # the dashboard's claim, nothing else
    _write(node / STAMP / "topics.md", "topics: the-post the-loom\n")
    _jsonl(brr / "run-ledger.jsonl", [
        {"run_id": CLAIM, "started_at": iso(NOW - 50000), "ended_at": iso(NOW - 49000), "name": "the failover that says what it did",
         "repo_label": "acme/widgets"},
        {"run_id": STAMP, "started_at": iso(NOW - 40000), "ended_at": iso(NOW - 39000), "name": "stamped, never indexed",
         "repo_label": "acme/widgets"},
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
    assert out["cloth"] == {"rows": []} and out["bench"] == {"folds": []}
    assert out["tree"] == {"repo": [], "places": [], "home": {"places": []}}
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
    assert [r["run"] for r in out["cloth"]["rows"]] == [CLAIM, STAMP, RUN, OLD]  # ledger order: each run's last entry
    assert out["cloth"]["rows"][2]["ended"] == iso(NOW - 29000)  # not live now: the ledger's end stands


def test_shuttle_keeps_the_last_twelve_transitions(machine):
    shuttle = state.build(machine["repo"], machine["home"], now=NOW)["shuttle"]
    assert shuttle["state"] == "awake" and shuttle["run_id"] == RUN and shuttle["why"] == "event_dispatched"
    assert [t["why"] for t in shuttle["transitions"]] == [f"t{i}" for i in range(2, 14)]
    assert set(shuttle["transitions"][0]) == {"at", "from", "to", "why", "tick"}
    assert [t["tick"] for t in shuttle["transitions"]] == list(range(2, 14))
    assert shuttle["tick"] is None  # no loop has ticked on this home


def test_shuttle_tick_is_the_frames_latest_beat(machine):
    _write(machine["home"] / "tick.json", json.dumps({"n": 4242, "at": iso(NOW), "mono": 1.0}))
    assert state.build(machine["repo"], machine["home"], now=NOW)["shuttle"]["tick"] == 4242


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
    kid, queued = hud["strands"]
    assert queued["id"] is None and queued["event_id"] == "evt-queued"
    assert {k: kid[k] for k in ("id", "event_id", "title", "status", "spent", "allowance")} == {
        "id": CHILD, "event_id": "evt-kid", "title": "the kid", "status": "submitted", "spent": 4200,
        "allowance": 15_000_000,
    }
    # the last 12 distinct places of its own log, newest first; the pre-tool row is no bead
    assert kid["places"] == [f"src/brr/f{i % 15}.py" for i in range(29, 17, -1)]
    assert kid["last_bead_at"] == iso(NOW - 400 + 29) and kid["last_place_kind"] == "file"


def test_hud_full_is_the_typed_hud_as_hud_json_prints_it(machine):
    from brr import hud as hud_mod

    portal = json.loads((machine["brr"] / "outbox" / "evt-live" / "portal-state.json").read_text())
    full = state.build(machine["repo"], machine["home"], now=NOW)["hud"]["full"]
    assert full == json.loads(hud_mod.HUD.from_dict(portal).to_json())
    assert full["run"]["id"] == RUN and "attention" in full and "resources" in full


def test_a_strand_without_a_log_has_no_position(machine):
    (machine["brr"] / "runs" / CHILD / "boundaries.jsonl").unlink()
    kid, queued = state.build(machine["repo"], machine["home"], now=NOW)["hud"]["strands"]
    assert kid["places"] == [] and kid["last_bead_at"] is None
    # dispatched but not started: the portal's edge has no run id yet
    assert queued == {"id": None, "event_id": "evt-queued", "title": "not started", "status": None,
                      "spent": None, "allowance": None, "places": [], "last_bead_at": None,
                      "last_place_kind": None}


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
                         "needs": ["w-1"], "state": "ready", "taken": None,
                         "visited_at": None, "footprints": []}


def test_beads_carry_places_and_topics(machine):
    beads = state.build(machine["repo"], machine["home"], now=NOW)["beads"]
    assert [b["act"] for b in beads] == ["probe", "mutate", "probe", "orient"]  # the pre-tool row is no bead
    assert [b["n"] for b in beads] == [0, 1, 2, 3]
    probe, mutate, looked, home_read = beads
    assert looked["places"] == ["src/brr/real.py"]  # detail first; /tmp is no place; the cwd only as a last resort
    assert home_read["places"] == [] and home_read["home_places"] == ["knowledge/repos/acme/design.md"]
    assert home_read["place_kind"] == "home" and looked["place_kind"] == "file"
    assert probe["places"] == ["src/brr/hud.py"]
    assert probe["topics"] == ["the-loom"]
    assert len(probe["detail"]) == 160 and (probe["ctx_after"], probe["delta"]) == (272_600, 700)
    # the outbox path is runtime, not the tree; a worktree path is a repo place
    assert mutate["places"] == ["src/brr/w.py", "media/post/a.png"]
    assert mutate["topics"] == ["the-post"]


def test_cloth_joins_the_ledger_with_index_refs(machine):
    rows = state.build(machine["repo"], machine["home"], now=NOW)["cloth"]["rows"]
    assert [r["run"] for r in rows] == [CLAIM, STAMP, RUN, OLD, CHILD]  # ledger order (each run's last entry), then open strands
    claim, stamp, live, old, kid = rows
    assert claim["topics"] == ["the-post", "not-minted-yet"]  # only a claim, on its node; unknown slugs stay
    assert stamp["topics"] == ["the-loom", "the-post"]  # the stamp, then the claim, deduped
    assert old["name"] == "the old run"  # the run's last ledger entry wins
    assert old["topics"] == ["the-loom", "the-post"]
    assert (old["prs"], old["knots"], old["pages"], old["tokens"]) == ([7, 9], 2, 1, 60)
    assert (old["ended"], old["shell"], old["core"], old["parent"]) == (iso(NOW - 18000), "claude", "opus", None)
    assert old["duration_s"] == 2000
    # the live run's earlier stint is in the ledger; its end is still null
    assert live["ended"] is None and live["duration_s"] is None
    # a live run reads its own controls even when an earlier stint is in the ledger
    assert live["topics"] == ["the-post", "the-clockwork"] and live["name"] == "the live seat" and live["mood"] == "curious"
    assert kid["ended"] is None and kid["duration_s"] is None
    assert (kid["parent"], kid["shell"], kid["core"], kid["prs"], kid["knots"]) == (RUN, "codex", "astra", [12], 1)


def test_tree_heat_decays_by_the_hour(machine):
    places = {p["path"]: p for p in state.build(machine["repo"], machine["home"], now=NOW)["tree"]["places"]}
    assert {"src/brr/hud.py", "media/post/a.png", "src/brr/old.py", "src/brr/real.py"} <= set(places)
    tree = state.build(machine["repo"], machine["home"], now=NOW)["tree"]
    assert tree["repo"] == tree["places"]  # the alias, for one version
    assert [p["path"] for p in tree["home"]["places"]] == ["knowledge/repos/acme/design.md"]
    assert tree["home"]["places"][0]["heat"] > 0.95 and tree["home"]["places"][0]["last"] == iso(NOW - 80)
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


# ── the place-kind classifier: one row per kind ──────────────────────────


def _where(machine):
    return state.locate(machine["repo"], machine["home"])


@pytest.mark.parametrize("kind,row,source", [
    ("file", {"act": "probe", "detail": "rg foo src/brr/hud.py"}, ""),
    ("home", {"act": "orient", "detail": "cat {home}/dominion/pitfalls.md"}, ""),
    ("forge", {"act": "publish", "detail": "gh pr create --title x"}, ""),
    ("forge", {"act": "publish", "detail": "git push -u origin brr/x"}, ""),
    ("wire", {"act": "dispatch", "detail": "cat > $O/000007-plan.md <<EOF --- event: evt-1 ---",
              "place": {"path": "{brr}/outbox/evt-live", "paths": ["{brr}/outbox/evt-live"]}}, ""),
    ("wire", {"act": "mutate", "detail": "edit", "place": {"path": "{brr}/outbox/evt-live/.card", "paths": []}}, ""),
    ("wire", {"act": "probe", "detail": "ls {home}/conversations/x"}, ""),
    ("shed", {"act": "wait", "detail": "brnrd await --file /tmp/x"}, ""),
    ("crew", {"act": "dispatch", "detail": "cat > $O/000009-kid.md <<EOF --- spawn: true branch: brr/x ---"}, ""),
    ("crew", {"act": "dispatch", "detail": "printf -- '--- to: run-260922-1010-kid1 ---' > $O/000010.md"}, ""),
    ("clock", {"phase": "session-start", "cwd": "/"}, "schedule"),
    ("crew", {"phase": "session-start", "cwd": "/"}, "spawn"),
])
def test_place_kind_classifies_one_row_per_kind(machine, kind, row, source):
    text = json.dumps(row).replace("{home}", str(machine["home"])).replace("{brr}", str(machine["brr"]))
    assert state.place_kind(json.loads(text), _where(machine), source=source) == kind


def test_detail_paths_must_exist_and_sit_in_repo_or_home(machine):
    where = _where(machine)
    row = {"act": "probe", "cwd": str(machine["repo"]),
           "detail": f"diff src/brr/real.py src/brr/ghost.py /tmp/x {machine['home']}/shuttle.json"}
    assert state.row_paths(row, where) == (["src/brr/real.py"], [])
    # nothing in detail: the frame's place, then the cwd
    assert state.row_paths({"act": "probe", "detail": "ls", "cwd": str(machine["repo"] / "src")}, where) == (["src"], [])


def test_cloth_rows_carry_their_trail(machine):
    rows = {r["run"]: r for r in state.build(machine["repo"], machine["home"], now=NOW)["cloth"]["rows"]}
    assert rows[OLD]["trail"] == [
        {"path": "src/brr/hud.py", "at": iso(NOW - 5 * 3600)}, {"path": "src/brr/old.py", "at": iso(NOW - 5 * 3600)},
    ]
    assert [t["path"] for t in rows[CHILD]["trail"]] == [f"src/brr/f{i}.py" for i in range(14, 6, -1)]
    assert rows[RUN]["trail"][0] == {"path": "src/brr/real.py", "at": iso(NOW - 90)}
    assert rows[CLAIM]["trail"] == []  # no boundaries log


# ── bead → warp item join ─────────────────────────────────────────────────


def test_bead_items_finds_ids_by_home_path_detail_and_outbox_reply_body(tmp_path):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    brr = repo / ".brr"
    (repo / "src").mkdir(parents=True)
    where = state.locate(repo, home)

    # the item's own authored file, as a home place
    home_row = {"act": "orient", "place": {"path": str(home / "surface" / "warp" / "w-9.md"), "paths": []}}
    assert state.bead_items(home_row, where, state.row_paths(home_row, where)[1]) == ["w-9"]

    # a bare token in detail — a goal id too, never guessed from topics
    detail_row = {"act": "reply", "detail": "unblocked w-9 and g-2 both"}
    assert state.bead_items(detail_row, where, []) == ["w-9", "g-2"]

    # an outbox reply/note file the row names — its body scanned the same way
    note = brr / "outbox" / "evt-x" / "000005-reply.md"
    _write(note, "---\nevent: evt-x\n---\nsettling w-9, see also w-11\n")
    note_row = {"act": "dispatch", "place": {"path": str(note), "paths": [str(note)]}}
    assert state.bead_items(note_row, where, []) == ["w-9", "w-11"]

    # nothing named ⇒ nothing found
    assert state.bead_items({"act": "probe", "detail": "ls -la"}, where, []) == []


def test_beads_and_warp_items_join_on_bead_touches(tmp_path):
    """Through the real caller (:func:`state.build`): a bead whose path is
    the item's own file, a bead whose detail names it, and a bead naming
    nothing ⇒ ``beads[].items`` per row, ``warp.items[w-18].visited_at`` the
    later of the two, ``footprints`` in order."""
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    brr = repo / ".brr"
    run_id = "run-260923-1200-w18xx"
    outbox = brr / "outbox" / "evt-w18"

    _write(home / "shuttle.json", json.dumps({
        "key": "acc", "state": "awake", "why": "event_dispatched", "run_id": run_id,
        "repo_root": str(repo), "conversation_key": "cloud:x", "since": iso(NOW - 60),
        "transitions": [],
    }))
    _run_md(brr, run_id, outbox, transitions=_transitions(NOW - 300))
    _write(outbox / "portal-state.json", json.dumps({"run": {"id": run_id}}))
    _write(home / "surface" / "warp" / "w-18.md", "# Eighteen\n\ntype: action\n")

    w18_path = home / "surface" / "warp" / "w-18.md"
    _jsonl(brr / "runs" / run_id / "boundaries.jsonl", [
        {"at": iso(NOW - 200), "act": "orient", "cwd": str(repo),
         "place": {"path": str(w18_path), "paths": [str(w18_path)]}},
        {"at": iso(NOW - 100), "act": "reply", "detail": "reply: w-18 unblocked", "cwd": str(repo)},
        {"at": iso(NOW - 50), "act": "probe", "detail": "ls -la", "cwd": str(repo)},
    ])

    out = state.build(repo, home, now=NOW)
    assert [b["items"] for b in out["beads"]] == [["w-18"], ["w-18"], []]

    by = {i["id"]: i for i in out["warp"]["items"]}
    assert by["w-18"]["visited_at"] == iso(NOW - 100)  # the later of the two
    assert by["w-18"]["footprints"] == [
        {"run": run_id, "n": 0, "at": iso(NOW - 200), "act": "orient"},
        {"run": run_id, "n": 1, "at": iso(NOW - 100), "act": "reply"},
    ]


def test_beads_carry_chunks_straight_through(tmp_path):
    """Phase A: `record["chunks"]` (`hooks.record_boundary`) rides `beads[]`
    unchanged — no aggregation, no relativizing, the same additive
    treatment `items` got in #2004; a row that never carried one reads back
    as `[]`, never a missing key."""
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    brr = repo / ".brr"
    run_id = "run-260924-0900-chunk1"
    outbox = brr / "outbox" / "evt-chunk"

    _write(home / "shuttle.json", json.dumps({
        "key": "acc", "state": "awake", "why": "event_dispatched", "run_id": run_id,
        "repo_root": str(repo), "conversation_key": "cloud:x", "since": iso(NOW - 60),
        "transitions": [],
    }))
    _run_md(brr, run_id, outbox, transitions=_transitions(NOW - 300))
    _write(outbox / "portal-state.json", json.dumps({"run": {"id": run_id}}))

    x_path = repo / "src" / "x.py"
    _jsonl(brr / "runs" / run_id / "boundaries.jsonl", [
        {"at": iso(NOW - 200), "act": "orient", "cwd": str(repo),
         "place": {"path": str(x_path), "paths": [str(x_path)]},
         "chunks": [{"path": str(x_path), "from": 1, "to": 20, "kind": "read"}]},
        {"at": iso(NOW - 100), "act": "probe", "detail": "ls -la", "cwd": str(repo)},
    ])

    out = state.build(repo, home, now=NOW)
    assert [b["chunks"] for b in out["beads"]] == [
        [{"path": str(x_path), "from": 1, "to": 20, "kind": "read"}],
        [],
    ]
