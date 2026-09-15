"""The frame's hand on the card: ticks (§19.1) and the delta item (§19.2)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from brr import card_frame, daemon, heddles, protocol
from brr.run import Run

T0 = 1_790_000_000.0


def iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


CARD = """## Now
converging

## Plan
- [ ] converge #1976 (the HUD)
- [ ] read back brr/the-wait-that-costs-nothing
- [ ] fold in strand run-260914-1608-8jwz
- [ ] write the report
- [ ] follow up on #1975
"""


class Harness:
    def __init__(self, tmp_path: Path):
        self.outbox = tmp_path / "outbox"
        self.outbox.mkdir()
        self.run_dir = tmp_path / "runs" / "run-seat"
        self.run_dir.mkdir(parents=True)
        self.repo = tmp_path / "repo"
        (self.repo / ".brr").mkdir(parents=True)
        self.meta: dict = {"branch_name": "brr/seat"}
        self.stats: dict = {}
        self.events: list[dict] = []
        self.notices: list[tuple[str, str]] = []
        self.forge: list[dict] = []

    def card(self, text: str) -> None:
        (self.outbox / ".card").write_text(text, encoding="utf-8")

    def read_card(self) -> str:
        return (self.outbox / ".card").read_text(encoding="utf-8")

    def set_forge(self, rows: list[dict]) -> None:
        from brr import forge_pr_cache

        path = forge_pr_cache.cache_path(self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"prs": rows, "fetched_at": iso(T0)}), encoding="utf-8")
        card_frame._FORGE_CACHE.clear()

    def notice_rows(self, rows: list[dict]) -> None:
        with (self.outbox / ".notices.jsonl").open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def run(self, now: float, **kw):
        return card_frame.frame_pass(
            self.meta, outbox_dir=self.outbox, run_dir=self.run_dir, repo_root=self.repo,
            stats=self.stats, events=self.events, now=now,
            notice=lambda kind, text: self.notices.append((kind, text)),
            run_id="run-260914-1644-ocgl", **kw,
        )


@pytest.fixture
def h(tmp_path):
    card_frame._FORGE_CACHE.clear()
    return Harness(tmp_path)


# ── ticks ────────────────────────────────────────────────────────────────


def test_three_coordinate_kinds_tick_and_other_lines_are_untouched(h):
    h.card(CARD)
    h.run(T0)  # first sight of every open line
    h.set_forge([
        {"number": 1976, "state": "MERGED", "branch": "brr/the-hud-is-one-shape", "merged_at": iso(T0 + 60)},
        {"number": 1977, "state": "MERGED", "branch": "brr/the-wait-that-costs-nothing", "merged_at": iso(T0 + 70)},
        {"number": 1975, "state": "MERGED", "branch": "brr/the-outbox-is-a-verb-table", "merged_at": iso(T0 - 3600)},
    ])
    h.events = [{
        "id": "evt-1790000090000000000-abcd", "source": "spawn_completed",
        "created": iso(T0 + 90), "spawned_by_run": "run-260914-1608-8jwz",
    }]
    result = h.run(T0 + 100)
    card = h.read_card()
    assert "- [x] converge #1976 (the HUD)" in card
    assert "- [x] read back brr/the-wait-that-costs-nothing" in card
    assert "- [x] fold in strand run-260914-1608-8jwz" in card
    # no coordinate ⇒ the weaver's line; merged before the line was seen ⇒ left
    assert "- [ ] write the report" in card
    assert "- [ ] follow up on #1975" in card
    assert [t.why for t in result.ticks] == [
        "#1976 merged", "brr/the-wait-that-costs-nothing's PR merged",
        "run-260914-1608-8jwz returned",
    ]
    ((kind, text),) = h.notices
    assert kind == "advisory"
    assert text.startswith("card_ticks: ticked “- [ ] converge #1976 (the HUD)” — #1976 merged")


def test_a_line_naming_two_prs_waits_for_both(h):
    h.card("- [ ] land #10 and #11\n")
    h.run(T0)
    h.set_forge([{"number": 10, "state": "MERGED", "branch": "brr/a", "merged_at": iso(T0 + 5)}])
    h.run(T0 + 10)
    assert card_frame.card_halves(h.read_card())[0] == "- [ ] land #10 and #11"
    h.set_forge([
        {"number": 10, "state": "MERGED", "branch": "brr/a", "merged_at": iso(T0 + 5)},
        {"number": 11, "state": "MERGED", "branch": "brr/b", "merged_at": iso(T0 + 15)},
    ])
    h.run(T0 + 20)
    weaver, frame = card_frame.card_halves(h.read_card())
    assert weaver == "- [x] land #10 and #11"
    assert "- merges: #10 · #11" in frame  # the forge's merges of the card's PRs (§19.3)


def test_an_open_pr_and_an_unreturned_strand_tick_nothing(h):
    h.card(CARD)
    h.run(T0)
    h.set_forge([{"number": 1976, "state": "OPEN", "branch": "brr/the-hud-is-one-shape"}])
    h.events = [{"id": "evt-1-aaaa", "source": "telegram", "spawned_by_run": "run-260914-1608-8jwz"}]
    h.run(T0 + 100)
    assert h.read_card() == CARD
    assert h.notices == []


def test_a_land_produce_row_is_a_merge(h):
    h.card("- [ ] ship #42\n")
    h.run(T0)
    with (h.run_dir / "produce.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "knot", "ref": "abc", "verb": "land", "pr": 42, "at": iso(T0 + 1)}) + "\n")
    h.run(T0 + 2)
    weaver, frame = card_frame.card_halves(h.read_card())
    assert weaver == "- [x] ship #42"
    assert "- merges: #42 → abc" in frame  # the same row is now a ledger line (§19.3)


def test_the_frames_tick_is_not_the_weavers_write():
    ticked = ["- [ ] ship #42"]
    assert card_frame.intent_hash("- [x] ship #42\n", ticked) == card_frame.intent_hash("- [ ] ship #42\n")
    said = "## Now\nx\n\n## Said\n- 17:00Z → abcd: hello\n"
    assert card_frame.intent_hash(said) == card_frame.intent_hash("## Now\nx\n")


# ── the delta item ───────────────────────────────────────────────────────


def _baseline(h):
    h.card("## Now\nworking\n\n## Plan\n- [ ] converge #1976\n")
    h.run(T0)
    assert card_frame.portal_delta(h.meta) is None


def test_a_strand_return_is_a_moment_but_no_longer_the_deltas(h):
    # §19.3: the ledger shows the return; the delta keeps deliveries and refusals.
    _baseline(h)
    h.events = [{"id": "evt-x", "source": "spawn_completed", "created": iso(T0 + 30),
                 "spawned_by_run": "run-260914-1608-8jwz", "spawn_status": "done"}]
    result = h.run(T0 + 40)
    assert result.triggers == ["strand_returned"]
    assert card_frame.portal_delta(h.meta) is None
    assert "- strand done: run-260914-1608-8jwz — done" in h.read_card()


def test_a_merge_is_a_moment_but_no_longer_the_deltas(h):
    _baseline(h)
    h.set_forge([
        {"number": 1976, "state": "MERGED", "branch": "brr/x", "merged_at": iso(T0 + 30)},
        {"number": 1999, "state": "MERGED", "branch": "brr/stranger", "merged_at": iso(T0 + 30)},
    ])
    result = h.run(T0 + 40)
    assert result.triggers == ["pr_merged"]
    assert card_frame.portal_delta(h.meta) is None


def test_a_delivery_after_a_return_names_only_the_delivery(h):
    _baseline(h)
    h.events = [{"id": "evt-x", "source": "spawn_completed", "created": iso(T0 + 30),
                 "spawned_by_run": "run-260914-1608-8jwz"}]
    h.stats.update({"current": 1})
    result = h.run(T0 + 40)
    assert result.triggers == ["strand_returned", "delivery"]
    delta = card_frame.portal_delta(h.meta)
    assert delta["text"] == "since your last card write: 1 reply delivered"
    assert delta["id"] == "card-delta-ocgl-1"
    assert delta["trigger"] == "delivery"


def test_a_strangers_merge_is_no_trigger(h):
    _baseline(h)
    h.set_forge([{"number": 1999, "state": "MERGED", "branch": "brr/stranger", "merged_at": iso(T0 + 30)}])
    assert h.run(T0 + 40).triggers == []
    assert card_frame.portal_delta(h.meta) is None


def test_trigger_delivery_batch(h):
    _baseline(h)
    h.stats.update({"current": 1, "other": 1})
    result = h.run(T0 + 40)
    assert result.triggers == ["delivery"]
    assert card_frame.portal_delta(h.meta)["text"] == "since your last card write: 2 replies delivered"


def test_trigger_refusal_ignores_advisories_and_standing(h):
    _baseline(h)
    h.notice_rows([
        {"kind": "advisory", "text": "fyi", "lifetime": "run"},
        {"kind": "refused", "text": "x", "lifetime": "standing"},
    ])
    assert h.run(T0 + 30).triggers == []
    h.notice_rows([
        {"kind": "advisory", "text": "fyi", "lifetime": "run"},
        {"kind": "refused", "text": "x", "lifetime": "standing"},
        {"kind": "refused", "text": "spawn refused", "lifetime": "run"},
    ])
    result = h.run(T0 + 40)
    assert result.triggers == ["refusal"]
    assert card_frame.portal_delta(h.meta)["text"] == "since your last card write: 1 refusal"


def test_the_item_is_redrafted_in_place_then_folded_by_a_card_edit(h):
    _baseline(h)
    h.stats.update({"current": 1})
    h.run(T0 + 10)
    h.notice_rows([{"kind": "dropped", "text": "x", "lifetime": "run"}])
    h.run(T0 + 20)
    delta = card_frame.portal_delta(h.meta)
    assert delta["id"] == "card-delta-ocgl-1"
    assert delta["text"] == "since your last card write: 1 reply delivered · 1 refusal"
    # the weaver edits the card: the item leaves, and the count restarts
    h.card("## Now\ncaught up\n\n## Plan\n- [ ] converge #1976\n")
    h.run(T0 + 30)
    assert card_frame.portal_delta(h.meta) is None
    h.stats.update({"current": 2})
    h.run(T0 + 40)
    delta = card_frame.portal_delta(h.meta)
    assert delta["id"] == "card-delta-ocgl-2"
    assert delta["text"] == "since your last card write: 1 reply delivered"


def test_a_said_projection_is_not_a_card_write(h):
    _baseline(h)
    h.stats.update({"current": 1})
    h.run(T0 + 10)
    task = Run(id="run-seat", event_id="evt-1-aaaa", body="", source="telegram", meta=h.meta)
    daemon._project_said(task, h.outbox, "evt-1-aaaa", "done — merged")
    h.run(T0 + 20)
    assert card_frame.portal_delta(h.meta) is not None


def test_note_clears_the_item_through_the_outbox(h, tmp_path, monkeypatch):
    _baseline(h)
    h.stats.update({"current": 1})
    h.run(T0 + 10)
    delta_id = card_frame.portal_delta(h.meta)["id"]
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "telegram", "x", status="processing",
                                telegram_user_id="42", telegram_chat_id="42")
    (h.outbox / "n.md").write_text(f"---\nnote: {delta_id}\n---\n", encoding="utf-8")
    monkeypatch.setattr(daemon.updates, "emit", lambda _b, pkt: None)
    task = Run(id="run-seat", event_id=own.stem, body="x", source="telegram", meta=h.meta)
    task.conversation_key = "telegram:42:"
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="telegram:42:", event_id=own.stem)
    promoted = daemon._drain_outbox(emit, task, brr_dir / "responses", own.stem, h.outbox, inbox,
                                    repo_root=tmp_path, account_context=None, stats={})
    assert promoted == 1
    assert card_frame.portal_delta(h.meta) is None
    assert daemon._read_outbox_notices(h.outbox) == []
    assert not (h.outbox / "n.md").exists()


def test_a_strand_gets_ticks_and_no_delta(h):
    _baseline(h)
    h.stats.update({"current": 1})
    h.run(T0 + 10, is_strand=True)
    assert card_frame.portal_delta(h.meta) is None


def test_nothing_moved_means_no_item_and_a_missing_outbox_is_a_noop(h):
    _baseline(h)
    assert h.run(T0 + 10).triggers == []
    assert card_frame.frame_pass({}, outbox_dir=None).delta is None


# ── the heartbeat wiring ─────────────────────────────────────────────────


def test_frame_heartbeat_stashes_heddles_and_the_portal_publishes_both(tmp_path, monkeypatch):
    from brr import account

    heddles.reset_cache()
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = account.resolve_context(
        repo, {"home.path": str(tmp_path / "home"), "repo.label": "hugimuni-labs/brnrd"},
    )
    topics = account.work_surface_path(ctx) / "topics"
    topics.mkdir(parents=True)
    (topics / "the-workshop.md").write_text(
        "---\nrune: ⚒\nsignature:\n  places: [src/brr/**]\n---\n# The workshop\n", encoding="utf-8",
    )
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    inbox.mkdir(parents=True)
    own = protocol.create_event(inbox, "telegram", "x", status="processing",
                                telegram_user_id="42", telegram_chat_id="42")
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)
    (outbox / ".card").write_text("## Now\nx\n", encoding="utf-8")
    run_dir = brr_dir / "runs" / "run-seat"
    run_dir.mkdir(parents=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (run_dir / "boundaries.jsonl").write_text(
        json.dumps({"at": now, "place": {"path": str(repo / "src/brr/hooks.py"), "commit": None}}) + "\n",
        encoding="utf-8",
    )
    task = Run(id="run-seat", event_id=own.stem, body="x", source="telegram",
               meta={"repo_label": "hugimuni-labs/brnrd"})
    task.conversation_key = "telegram:42:"
    card_state: dict = {}
    daemon._write_live_inbox(outbox, inbox, own.stem, account_context=ctx,
                             repo_label="hugimuni-labs/brnrd", observer_run_id=task.id)
    daemon._frame_heartbeat(
        task, outbox_dir=outbox, card_state=card_state, output_stats={"current": 0},
        brr_dir=brr_dir, account_context=ctx, repo_label="hugimuni-labs/brnrd",
        work_dir=repo, repo_root=repo,
    )
    (lit,) = card_state["heddles"]
    assert lit["slug"] == "the-workshop"
    assert lit["rune"] == "⚒"
    assert lit["matched_by"] == ["place"]
    assert lit["brightness"] > 0.9
    assert "card_frame" in task.meta
    path = daemon._write_live_portal_state(
        outbox, inbox, own.stem, task, phase="running", card_state=card_state,
        output_stats={}, refresh_levels=False, brr_dir=brr_dir,
        account_context=ctx, repo_label="hugimuni-labs/brnrd",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [h["slug"] for h in payload["heddles"]] == ["the-workshop"]
    assert payload["card"]["delta"] is None
