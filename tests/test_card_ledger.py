"""The card in two halves (design-the-loom §19.3): the frame's ``## Ledger``."""

from __future__ import annotations

import json
import os
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


# ── the splice ───────────────────────────────────────────────────────────


WEAVER = "## Now\nconverging\n\n## Plan\n- [ ] converge #1976\n\n## Vector\n- steer folded\n"


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

    clock: float = T0 - 1  # the weaver's last write, on the test's own clock

    def card(self, text: str) -> None:
        # See test_card_frame.py: the frame cuts finished strands at the
        # card's mtime; pin it to the harness clock, not the wall clock.
        path = self.outbox / ".card"
        path.write_text(text, encoding="utf-8")
        os.utime(path, (self.clock, self.clock))

    def read_card(self) -> str:
        return (self.outbox / ".card").read_text(encoding="utf-8")

    def run(self, now: float, **kw):
        self.clock = now
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


# ── the delta's reduced set ──────────────────────────────────────────────


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


# ── the acts line (action ledger, step 5) ────────────────────────────────


