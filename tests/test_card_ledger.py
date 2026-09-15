"""The card in two halves (design-the-loom §19.3): the frame's ``## Ledger``."""

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


def hud_fixture(**over) -> dict:
    hud = {
        "produce": {"ledger": {
            "counts": {"knot": 3, "heddle": 2, "card": 0, "page": 1},
            # newest first, as hud.project_produce writes it
            "last": [
                {"kind": "page", "ref": "design-the-loom.md", "source": "relic"},
                {"kind": "heddle", "ref": "#1975", "source": "relic"},
                {"kind": "knot", "ref": "a06f566e0b7d1b2c3d4e5f60718293a4b5c6d7e8", "source": "frame",
                 "at": iso(T0)},
            ],
        }},
        "resources": {
            "allowance": {"status": "known", "scope": "resident", "explicit": False,
                          "spent": 28_431_977, "tokens": 400_000_000},
            "coexisting_runs": {"owned_children": [
                {"event_id": "evt-1789471812466580000-1v30", "run_id": "run-260915-1130-1v30",
                 "title": "move 5e — the message that spans topics"},
                {"event_id": "evt-1789471812466580000-qqrh", "run_id": "run-260915-1130-zgtd",
                 "title": "the card in two halves", "status": "submitted"},
            ]},
        },
        "heddles": [
            {"slug": "the-loom", "brightness": 0.4},
            {"slug": "the-workshop", "brightness": 0.9},
        ],
    }
    hud.update(over)
    return hud


PRODUCE_ROWS = [
    {"kind": "knot", "ref": "1c608786aa", "verb": "land", "pr": 1976, "at": iso(T0 + 20)},
    {"kind": "knot", "ref": "a06f566eff", "verb": "land", "pr": 1975, "at": iso(T0 + 10)},
    {"kind": "page", "ref": "x.md", "at": iso(T0 + 5)},  # not a land row
]
RELICS = [
    {"kind": "merge", "sha": "2bb3db13", "pr": 1983},
    {"kind": "merge", "sha": "ffffffff", "pr": 1976},  # already named by the land row
    {"kind": "commit", "sha": "deadbeef"},
]
EVENTS = [
    {"id": "evt-1790000050000000000-bbbb", "source": "spawn_completed", "created": iso(T0 + 50),
     "spawned_by_run": "run-260915-0900-bbbb", "spawn_status": "done", "spawn_pr_number": 1984},
    {"id": "evt-1790000030000000000-aaaa", "source": "spawn_completed", "created": iso(T0 + 30),
     "spawned_by_run": "run-260915-0800-aaaa", "spawn_status": "runner-failed"},
    {"id": "evt-1790000001000000000-old0", "source": "spawn_completed", "created": iso(T0 - 100),
     "spawned_by_run": "run-260915-0700-old0", "spawn_status": "done"},
    {"id": "evt-1790000040000000000-tele", "source": "telegram", "created": iso(T0 + 40)},
]


# ── the block's five parts ──────────────────────────────────────────────


def test_the_five_parts_from_fixtures():
    lines = card_frame.build_ledger(
        hud_fixture(), produce_rows=PRODUCE_ROWS, relics=RELICS, events=EVENTS, since=T0,
    )
    assert lines == [
        "- produce: knot 3 · heddle 2 · card 0 · page 1",
        "- last: knot a06f566 · heddle #1975 · page design-the-loom.md",
        "- strand live: run-260915-1130-1v30 — move 5e — the message that spans topics",
        "- strand live: run-260915-1130-zgtd — the card in two halves (submitted)",
        "- strand done: run-260915-0800-aaaa — runner-failed",
        "- strand done: run-260915-0900-bbbb — done · #1984",
        "- merges: #1975 → a06f566 · #1976 → 1c60878 · #1983 → 2bb3db1",
        "- spend: spend 28m",
        "- heddles: ♦ workshop · loom",
    ]


def test_each_part_only_when_non_empty():
    assert card_frame.build_ledger({}) == []
    assert card_frame.build_ledger({"produce": {"ledger": {"counts": {"knot": 0}}}}) == []


def test_a_finished_strand_leaves_the_live_list():
    hud = hud_fixture()
    events = [{"id": "evt-1790000050000000000-1v30", "source": "spawn_completed",
               "created": iso(T0 + 50), "spawned_by_run": "run-260915-1130-1v30",
               "spawned_by_event": "evt-1789471812466580000-1v30", "spawn_status": "done"}]
    lines = card_frame.build_ledger(hud, events=events, since=T0)
    assert "- strand done: run-260915-1130-1v30 — done" in lines
    assert not any(line.startswith("- strand live: run-260915-1130-1v30") for line in lines)


def test_draws_strands_are_the_fallback_for_live():
    hud = {"resources": {"quota": {"draws": {"self": 1, "strands": [
        {"run_id": "run-260915-1130-1v30", "title": "move 5e", "weighted": 10},
    ]}}}}
    assert card_frame.build_ledger(hud) == ["- strand live: run-260915-1130-1v30 — move 5e"]


@pytest.mark.parametrize("facet, expected", [
    ({"status": "known", "scope": "strand", "spent": 1_234_567, "tokens": 15_000_000},
     "- spend: spend 1.2m/15m · 8%"),
    ({"status": "known", "scope": "resident", "explicit": True, "spent": 38_412, "tokens": 120_000},
     "- spend: spend 38k/120k · 32%"),
    ({"status": "known", "scope": "resident", "spent": 28_000_000, "tokens": 1,
      "stake": {"state": "armed", "unit": "share", "share_pct": 5, "tokens": 5_000_000,
                "spent": 1_140_000}},
     "- spend: spend 28m · stake 1.1m/5% · 22%"),
    ({"status": "known", "scope": "resident", "spent": 28_000_000, "tokens": 1,
      "stake": {"state": "cut", "unit": "tokens", "tokens": 400_000, "spent": 412_000}},
     "- spend: spend 28m · stake 410k/400k · 103% · at cut-at"),
])
def test_spend_reads_the_allowance_and_the_stake(facet, expected):
    assert card_frame.build_ledger({"resources": {"allowance": facet}}) == [expected]


def test_forge_merges_join_the_land_rows_in_merge_order():
    merged = {1976: T0 + 20, 1990: T0 + 60, 1970: T0 + 1}
    assert card_frame.build_ledger({}, produce_rows=PRODUCE_ROWS, merged=merged) == [
        "- merges: #1970 · #1975 → a06f566 · #1976 → 1c60878 · #1990",
    ]


def test_ledger_merges_are_the_cards_since_the_run_began():
    began = card_frame.run_started("run-260915-1130-zgtd")
    assert card_frame._iso(began) == "2026-09-15T11:30:00Z"
    facts = card_frame.gather_facts(forge_prs=[
        {"number": 1983, "state": "MERGED", "branch": "brr/a", "merged_at": card_frame._iso(began + 60)},
        {"number": 1975, "state": "MERGED", "branch": "brr/b", "merged_at": card_frame._iso(began - 86400)},
        {"number": 1999, "state": "MERGED", "branch": "brr/stranger", "merged_at": card_frame._iso(began + 60)},
        {"number": 1984, "state": "OPEN", "branch": "brr/c"},
    ])
    card = "- [x] converge #1983\n- [ ] follow up on #1975\n- [ ] #1984\n"
    merged = card_frame.ledger_merges(facts, card_text=card, produce_rows=[], relics=[],
                                      meta={}, run_id="run-260915-1130-zgtd")
    assert merged == {1983: began + 60}
    assert card_frame.ledger_merges(facts, card_text=card, produce_rows=[], relics=[],
                                    meta={}, run_id="not-a-run-id") == {}


def test_a_long_title_is_cut_at_a_word():
    hud = {"resources": {"coexisting_runs": {"owned_children": [{"run_id": "run-260915-1130-1v30",
        "title": "move 5e — the message that spans topics: an inbound event stamped into several indexes"}]}}}
    assert card_frame.build_ledger(hud) == [
        "- strand live: run-260915-1130-1v30 — move 5e — the message that spans topics: an inbound event…",
    ]


def test_spend_with_no_reading_is_absent():
    facet = {"status": "unimplemented", "spent": None}
    assert card_frame.build_ledger({"resources": {"allowance": facet}}) == []


def test_coarse_tokens_move_every_few_percent_not_every_boundary():
    assert [card_frame._coarse_tokens(n) for n in (0, 950, 38_412, 38_999, 380_512, 1_199_999, 15_000_000)] == [
        "0", "950", "38k", "38k", "380k", "1.1m", "15m",
    ]


def test_the_block_is_capped_at_24_lines():
    many = [{"run_id": f"run-260915-1130-{i:04d}", "title": f"t{i}"} for i in range(20)]
    done = [{"id": f"evt-17900000{i:02d}000000000-d{i:03d}", "source": "spawn_completed",
             "created": iso(T0 + i), "spawned_by_run": f"run-260915-0000-d{i:03d}", "spawn_status": "done"}
            for i in range(20)]
    hud = hud_fixture()
    hud["resources"]["coexisting_runs"]["owned_children"] = many
    lines = card_frame.build_ledger(hud, produce_rows=PRODUCE_ROWS, relics=RELICS, events=done)
    block = card_frame.render_ledger(lines).split("\n")
    assert len(block) <= card_frame.LEDGER_MAX_LINES
    assert "- strand live: +14 more" in lines
    assert "- strand done: +14 earlier" in lines
    # oldest → newest within the kind
    shown = [line for line in lines if line.startswith("- strand done: run-")]
    assert shown[0].startswith("- strand done: run-260915-0000-d014")
    assert shown[-1].startswith("- strand done: run-260915-0000-d019")


# ── the splice ───────────────────────────────────────────────────────────


WEAVER = "## Now\nconverging\n\n## Plan\n- [ ] converge #1976\n\n## Vector\n- steer folded\n"


def test_splice_goes_before_said_or_at_the_end_and_is_stable():
    section = card_frame.render_ledger(["- merges: #1 → abc"])
    with_said = WEAVER + "\n## Said\n- 17:00Z → abcd: hello\n"
    once = card_frame.splice_ledger(with_said, section)
    assert once.index("## Ledger") < once.index("## Said")
    assert once.startswith(WEAVER)
    assert card_frame.splice_ledger(once, section) == once
    at_end = card_frame.splice_ledger(WEAVER, section)
    assert at_end == WEAVER + "\n" + section + "\n"
    assert card_frame.splice_ledger(at_end, section) == at_end
    moved = card_frame.splice_ledger(at_end, card_frame.render_ledger(["- merges: #2 → def"]))
    assert moved == WEAVER + "\n" + card_frame.render_ledger(["- merges: #2 → def"]) + "\n"


def test_card_halves_split_the_weaver_from_the_frame():
    card = card_frame.splice_ledger(WEAVER + "\n## Said\n- 17:00Z → abcd: hi\n",
                                    card_frame.render_ledger(["- merges: #1"]))
    weaver, frame = card_frame.card_halves(card)
    assert weaver == WEAVER.strip("\n")
    assert frame.startswith("## Ledger\n")
    assert frame.endswith("## Said\n- 17:00Z → abcd: hi")


# ── the pass ─────────────────────────────────────────────────────────────


class Harness:
    def __init__(self, tmp_path: Path):
        self.outbox = tmp_path / "outbox"
        self.outbox.mkdir()
        self.run_dir = tmp_path / "runs" / "run-seat"
        self.run_dir.mkdir(parents=True)
        self.meta: dict = {"branch_name": "brr/seat"}
        self.stats: dict = {}
        self.events: list[dict] = []
        self.notices: list[tuple[str, str]] = []
        self.hud = hud_fixture()

    def card(self, text: str) -> None:
        (self.outbox / ".card").write_text(text, encoding="utf-8")

    def read_card(self) -> str:
        return (self.outbox / ".card").read_text(encoding="utf-8")

    def run(self, now: float, **kw):
        kw.setdefault("hud", self.hud)
        return card_frame.frame_pass(
            self.meta, outbox_dir=self.outbox, run_dir=self.run_dir, repo_root=None,
            stats=self.stats, events=self.events, now=now,
            notice=lambda kind, text: self.notices.append((kind, text)),
            run_id="run-260914-1644-ocgl", **kw,
        )


@pytest.fixture
def h(tmp_path):
    card_frame._FORGE_CACHE.clear()
    return Harness(tmp_path)


def _spend(h, spent: int) -> None:
    h.hud["resources"]["allowance"] = {"status": "known", "scope": "strand",
                                       "spent": spent, "tokens": 15_000_000}


def test_rebuilt_on_every_pass_from_the_hud(h):
    h.card(WEAVER)
    result = h.run(T0)
    assert result.ledger_written
    assert "- spend: spend 28m" in h.read_card()
    _spend(h, 2_000_000)
    assert h.run(T0 + 30).ledger_written
    card = h.read_card()
    assert "- spend: spend 2m/15m · 13%" in card
    assert card.count("## Ledger") == 1
    # nothing moved ⇒ no write
    assert not h.run(T0 + 60).ledger_written


def test_the_portal_file_is_read_when_no_hud_is_passed(h):
    h.card(WEAVER)
    (h.outbox / "portal-state.json").write_text(json.dumps(hud_fixture()), encoding="utf-8")
    h.run(T0, hud=None)
    assert "- produce: knot 3 · heddle 2 · card 0 · page 1" in h.read_card()


def test_heddles_passed_by_the_heartbeat_outrank_the_portals(h):
    h.card(WEAVER)
    h.run(T0, heddles=[{"slug": "the-summit", "brightness": 1.0}])
    assert "- heddles: ♦ summit" in h.read_card()


def test_no_card_and_an_empty_ledger_write_nothing(h):
    h.hud = {}
    h.run(T0)
    assert not (h.outbox / ".card").exists()
    h.card(WEAVER)
    assert not h.run(T0 + 30).ledger_written
    assert h.read_card() == WEAVER


def test_a_ledger_change_is_never_a_weaver_write(h):
    h.card(WEAVER)
    h.run(T0)
    assert card_frame.intent_hash(h.read_card()) == card_frame.intent_hash(WEAVER)
    h.stats.update({"current": 1})
    h.run(T0 + 10)
    delta = card_frame.portal_delta(h.meta)
    assert delta is not None
    weaver_at = h.meta[card_frame.META_KEY]["weaver_at"]
    _spend(h, 3_000_000)
    assert h.run(T0 + 20).ledger_written
    # the ledger moved; the weaver did not: the delta stands, weaver_at holds
    assert card_frame.portal_delta(h.meta) == delta
    assert h.meta[card_frame.META_KEY]["weaver_at"] == weaver_at


def test_finished_strands_are_cut_at_the_weavers_last_write(h):
    h.card(WEAVER)
    h.run(T0)
    h.events = [{"id": "evt-1790000030000000000-aaaa", "source": "spawn_completed",
                 "created": iso(T0 + 30), "spawned_by_run": "run-260915-0800-aaaa", "spawn_status": "done"}]
    h.run(T0 + 40)
    assert "- strand done: run-260915-0800-aaaa — done" in h.read_card()
    weaver, _ = card_frame.card_halves(h.read_card())
    h.card(h.read_card().replace("converging", "folded the return in"))
    h.run(T0 + 50)
    assert "strand done" not in h.read_card()


def test_the_heading_is_readded_once_then_the_weaver_wins(h):
    h.card(WEAVER)
    h.run(T0)
    h.card(WEAVER)  # the weaver rewrites the card whole, without the heading
    assert h.run(T0 + 10).ledger_written
    assert "## Ledger" in h.read_card()
    ((kind, text),) = h.notices
    assert kind == "advisory"
    assert text.startswith("card_ledger: `## Ledger` was deleted from the card — re-added once")
    h.card(WEAVER)  # and again
    for i in range(3):
        assert not h.run(T0 + 20 + i).ledger_written
    assert h.read_card() == WEAVER
    assert len(h.notices) == 1


def test_the_weavers_headings_are_byte_identical_across_ten_heartbeats(h, tmp_path):
    card = WEAVER + "\n## Custom\nweaver's own heading\n"
    h.card(card)
    task = Run(id="run-seat", event_id="evt-1-aaaa", body="", source="telegram", meta=h.meta)
    writes = 0
    for i in range(10):
        _spend(h, 1_000_000 * (i + 1))
        h.events = [{"id": f"evt-17900000{i:02d}000000000-s{i:03d}", "source": "spawn_completed",
                     "created": iso(T0 + i * 30 + 1), "spawned_by_run": f"run-260915-0000-s{i:03d}",
                     "spawn_status": "done"}]
        writes += h.run(T0 + i * 30 + 5).ledger_written
        if i == 4:
            daemon._project_said(task, h.outbox, "evt-1-aaaa", "done — merged")
        weaver, frame = card_frame.card_halves(h.read_card())
        assert weaver == card.strip("\n"), i
        assert h.read_card().startswith(card)
    assert writes == 10
    assert h.read_card().count("## Ledger") == 1
    assert h.read_card().count("## Said") == 1


def test_a_weaver_write_racing_the_frame_wins(tmp_path):
    path = tmp_path / ".card"
    path.write_text("weaver's newer text\n", encoding="utf-8")
    assert not card_frame._write_card_if_unchanged(path, "older text\n", "frame's text\n")
    assert path.read_text(encoding="utf-8") == "weaver's newer text\n"


# ── the delta's reduced set ──────────────────────────────────────────────


def test_the_delta_keeps_deliveries_and_refusals_only():
    assert card_frame.DELTA_KEEPS == ("delivered", "refusals")
    assert card_frame.draft_delta(delivered=0, refusals=0) is None
    assert card_frame.draft_delta(delivered=2, refusals=1) == (
        "since your last card write: 2 replies delivered · 1 refusal"
    )
    with pytest.raises(TypeError):
        card_frame.draft_delta(returns=["run-x"], merges=[1], delivered=0, refusals=0)  # type: ignore[call-arg]


def test_a_commit_merge_and_return_alone_raise_no_delta(h):
    h.card(WEAVER)
    h.run(T0)
    (h.run_dir / "produce.jsonl").write_text(json.dumps(
        {"kind": "knot", "ref": "abcdef1234", "verb": "land", "pr": 1976, "at": iso(T0 + 5)},
    ) + "\n", encoding="utf-8")
    h.events = [{"id": "evt-1790000030000000000-aaaa", "source": "spawn_completed",
                 "created": iso(T0 + 30), "spawned_by_run": "run-260915-0800-aaaa"}]
    result = h.run(T0 + 40)
    assert result.triggers == ["strand_returned", "pr_merged"]
    assert card_frame.portal_delta(h.meta) is None
    assert "- merges: #1976 → abcdef1" in h.read_card()


# ── the card drain ───────────────────────────────────────────────────────


def test_a_frame_only_card_change_sends_no_packet_and_is_no_write(tmp_path):
    card_path = tmp_path / ".card"
    card_path.write_text(WEAVER, encoding="utf-8")
    task = Run(id="run-seat", event_id="evt-1-aaaa", body="", source="telegram", meta={})
    packets: list[dict] = []

    def emit(kind, **kw):
        packets.append({"kind": kind, **kw})

    state: dict = {}
    assert daemon._drain_agent_card(emit, task, "evt-1-aaaa", card_path, state)
    written = state["written_monotonic"]
    card_path.write_text(card_frame.splice_ledger(WEAVER, card_frame.render_ledger(["- spend: spend 1m"])),
                         encoding="utf-8")
    assert not daemon._drain_agent_card(emit, task, "evt-1-aaaa", card_path, state)
    card_path.write_text(card_frame.splice_ledger(WEAVER, card_frame.render_ledger(["- spend: spend 2m"])),
                         encoding="utf-8")
    assert not daemon._drain_agent_card(emit, task, "evt-1-aaaa", card_path, state)
    assert len(packets) == 1
    assert state["written_monotonic"] == written
    # a frame tick moves the course: the packet goes, the write clock does not
    task.meta[card_frame.META_KEY] = {"ticked": ["- [ ] converge #1976"]}
    card_path.write_text(card_path.read_text(encoding="utf-8").replace("- [ ] converge", "- [x] converge"),
                         encoding="utf-8")
    assert daemon._drain_agent_card(emit, task, "evt-1-aaaa", card_path, state)
    assert len(packets) == 2
    assert state["written_monotonic"] == written
    # the weaver's own edit is a write
    card_path.write_text(card_path.read_text(encoding="utf-8").replace("converging", "landed"),
                         encoding="utf-8")
    assert daemon._drain_agent_card(emit, task, "evt-1-aaaa", card_path, state)
    assert len(packets) == 3
    assert state["written_monotonic"] > written


# ── the heartbeat wiring and the verb ────────────────────────────────────


def test_frame_heartbeat_writes_the_ledger_with_the_heddles_it_lit(tmp_path):
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
    daemon._write_live_inbox(outbox, inbox, own.stem, account_context=ctx,
                             repo_label="hugimuni-labs/brnrd", observer_run_id=task.id)
    daemon._frame_heartbeat(
        task, outbox_dir=outbox, card_state={}, output_stats={"current": 0},
        brr_dir=brr_dir, account_context=ctx, repo_label="hugimuni-labs/brnrd",
        work_dir=repo, repo_root=repo,
    )
    card = (outbox / ".card").read_text(encoding="utf-8")
    assert card.startswith("## Now\nx\n\n## Ledger\n")
    assert "- heddles: ♦ workshop" in card


def test_hud_card_prints_both_halves(tmp_path, capsys, monkeypatch):
    from brr import cli

    brr_dir = tmp_path / ".brr"
    outbox = brr_dir / "outbox" / "evt-1-aaaa"
    outbox.mkdir(parents=True)
    run_dir = brr_dir / "runs" / "run-seat"
    run_dir.mkdir(parents=True)
    (run_dir / "produce.jsonl").write_text("\n".join(json.dumps(r) for r in PRODUCE_ROWS) + "\n",
                                           encoding="utf-8")
    (outbox / ".relics.jsonl").write_text("\n".join(json.dumps(r) for r in RELICS) + "\n",
                                          encoding="utf-8")
    (outbox / "inbox.json").write_text(json.dumps({"events": EVENTS[:2]}), encoding="utf-8")
    hud = hud_fixture(run={"id": "run-seat"})
    (outbox / "portal-state.json").write_text(json.dumps(hud), encoding="utf-8")
    stale_ledger = card_frame.render_ledger(["- spend: spend 1k"])
    (outbox / ".card").write_text(
        card_frame.splice_ledger(WEAVER + "\n## Said\n- 17:00Z → abcd: hi\n", stale_ledger),
        encoding="utf-8",
    )
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    before = (outbox / ".card").read_text(encoding="utf-8")
    cli.main(["hud", "--card", "--outbox", str(outbox)])
    out = capsys.readouterr().out
    assert out.startswith("── the weaver's half (as .card holds it) ──\n" + WEAVER.strip("\n") + "\n\n")
    frame = out.split("── the frame's half (as the frame would write it now) ──\n", 1)[1]
    assert frame.startswith("## Ledger\n" + card_frame.LEDGER_LEGEND + "\n- produce: knot 3")
    assert "- merges: #1975 → a06f566 · #1976 → 1c60878 · #1983 → 2bb3db1" in frame
    assert "- strand done: run-260915-0900-bbbb — done · #1984" in frame
    assert "spend 1k" not in frame  # rebuilt, not read back
    assert frame.rstrip("\n").endswith("## Said\n- 17:00Z → abcd: hi")
    assert (outbox / ".card").read_text(encoding="utf-8") == before  # read-only
