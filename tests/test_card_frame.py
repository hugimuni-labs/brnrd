"""The frame's hand on the card: ticks (§19.1) and the delta item (§19.2)."""

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

    clock: float = T0 - 1  # the weaver's last write, on the test's own clock

    def card(self, text: str) -> None:
        # The frame cuts finished strands at the card's mtime — the weaver's
        # last write. Pin it to the harness clock, not the wall clock: T0 is
        # 2026-09-21T14:13:20Z, and from that minute on the real mtime sat
        # *after* every T0+n event and the ledger went silent (CI red 14:38Z).
        path = self.outbox / ".card"
        path.write_text(text, encoding="utf-8")
        os.utime(path, (self.clock, self.clock))

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
        self.clock = now
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


# ── the delta item ───────────────────────────────────────────────────────


def _baseline(h):
    h.card("## Now\nworking\n\n## Plan\n- [ ] converge #1976\n")
    h.run(T0)
    assert card_frame.portal_delta(h.meta) is None


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
