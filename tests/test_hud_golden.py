"""The HUD is one shape — ``portal-state.json`` against a baseline from ``main``.

``daemon._write_live_portal_state`` assembled the portal by hand, one dict
literal and ~22 parameters. Move 5 of the daemon rewrite turns that into
``brr.hud`` (a typed ``HUD``, built from one ``HUDInputs``) with *no behaviour
change* as the contract. The existing suite is the first proof; this module is
the second: every scenario below was driven through the **unsplit** writer on
``main`` and the exact bytes it wrote were frozen under
``tests/fixtures/hud_golden/``. The same drive through the split writer must
reproduce each file byte for byte — with one named exception, move 5's
``produce.ledger`` projection, which is popped before the compare (see
:func:`without_ledger`) and pinned on its own in ``tests/test_hud.py``.

Two substitutions make a capture portable, and both are exact:

- the scenario's temp root is spelled ``<ROOT>``;
- ``change_token`` is spelled ``<TOKEN>`` — it hashes the payload, and the
  payload carries the temp root. The test recomputes the token from the
  written payload with the daemon's own ``_change_token`` and asserts it
  matches, so the substitution hides the host path, never the value.

Everything else a host would vary is pinned: the clocks, the quota level
collector, the transcript reads. Regenerating (``BRR_HUD_GOLDEN_WRITE=1``) is
only honest on a tree whose writer is the one the golden claims to describe; a
diff here is a behaviour change until proven otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from brr import daemon, schedule as schedule_mod, tick as tick_mod
from brr.run import Run

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "hud_golden"
WRITE = os.environ.get("BRR_HUD_GOLDEN_WRITE") == "1"

#: 2026-09-14T16:26:40Z — a fixed wall clock every scenario reads.
FROZEN_WALL = 1789403200.0
#: The monotonic clock at the moment of the write.
FROZEN_MONO = 50_000.0

#: The level snapshot the collector is pinned to: the Claude shape
#: (``session_*`` / ``week_*`` flattened on the top level), with a
#: per-model week bucket so the pacing path has something to bind.
FROZEN_LEVELS: dict[str, Any] = {
    "session_used_percentage": 12,
    "session_resets_at": FROZEN_WALL + 3 * 3600,
    "week_used_percentage": 31,
    "week_resets_at": FROZEN_WALL + 4 * 86400,
    "updated_at": "2026-09-14T16:20:00Z",
}


def frozen_host(monkeypatch) -> None:
    """Pin every input the writer reads from the host rather than its caller."""
    real_gmtime = time.gmtime
    monkeypatch.setattr(time, "time", lambda: FROZEN_WALL)
    monkeypatch.setattr(time, "monotonic", lambda: FROZEN_MONO)
    monkeypatch.setattr(
        time, "gmtime",
        lambda secs=None: real_gmtime(FROZEN_WALL if secs is None else secs),
    )
    monkeypatch.setattr(
        daemon, "_collect_levels",
        lambda *_a, **_k: (dict(FROZEN_LEVELS), frozenset({"quota"})),
    )
    monkeypatch.setattr(daemon.allowance, "latest_claude_transcript", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon.allowance, "collect_spent", lambda *_a, **_k: 4_200)
    tick_mod._reset_for_tests()
    monkeypatch.setattr(daemon, "_run_controls", {})


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_DATE": "2026-09-14T16:00:00Z",
        "GIT_COMMITTER_DATE": "2026-09-14T16:00:00Z",
        "GIT_AUTHOR_NAME": "HUD Golden", "GIT_AUTHOR_EMAIL": "hud@brr.invalid",
        "GIT_COMMITTER_NAME": "HUD Golden", "GIT_COMMITTER_EMAIL": "hud@brr.invalid",
    })
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout.strip()


def _dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    brr_dir = tmp_path / ".brr"
    outbox_dir = brr_dir / "outbox" / "evt-1"
    inbox_dir = brr_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    return brr_dir, outbox_dir, inbox_dir


def _write_event(inbox_dir: Path, eid: str, body: str, **fm: str) -> None:
    extra = "".join(f"{k}: {v}\n" for k, v in fm.items())
    (inbox_dir / f"{eid}.md").write_text(
        f"---\nid: {eid}\nstatus: pending\nsource: telegram\n"
        f"trust_tier: owner\n{extra}---\n{body}\n",
        encoding="utf-8",
    )


# ── scenarios: each returns the writer's (args, kwargs) ──────────────


Scenario = Callable[[Path], tuple[tuple, dict[str, Any]]]


def scenario_fresh_run(tmp_path: Path):
    """A run that just woke: nothing pending, no card, no produce, no clock."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="preparing", runner_name="codex", brr_dir=brr_dir,
    )


def scenario_await_armed(tmp_path: Path):
    """An armed, unresolved ``await:`` with no ceiling and nothing pending."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    task.meta["await"] = {
        "armed_at": FROZEN_WALL - 90, "timeout_seconds": None, "file": None,
        "generation": "gen-1", "resolved": False, "armed_pending_ids": [],
    }
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="running", attempt=1, runner_name="codex", brr_dir=brr_dir,
        start_monotonic=FROZEN_MONO - 600, cfg={},
    )


def scenario_hold_parked(tmp_path: Path):
    """A held seat: the hold record on the run, status ``held``."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    task = Run(
        id="run-1", event_id="evt-1", body="", source="telegram", status="held",
        meta={"resource_hold": {
            "active": True, "resume": "strands", "reason": "children in flight",
            "armed_at": "2026-09-14T16:00:00Z", "seat_key": "acc-1",
        }},
    )
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="finalizing", attempt=1, runner_name="codex", brr_dir=brr_dir,
        start_monotonic=FROZEN_MONO - 3600,
    )


def scenario_strand_run(tmp_path: Path):
    """A strand that has submitted once, with a parent to return to."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    task = Run(
        id="run-2", event_id="evt-1", body="", source="spawn",
        meta={
            "strand": True, "spawn_parent_run_id": "run-parent",
            "submitted": True, "branch_name": "brr/the-strand",
            "repo_label": "acme__widgets",
        },
    )
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="running", attempt=1, runner_name="claude", brr_dir=brr_dir,
        start_monotonic=FROZEN_MONO - 240, runner_meta={"model": "opus"},
    )


def scenario_with_produce(tmp_path: Path):
    """A worktree run on its own branch with a commit, a reported kb page and a PR."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    seed_oid = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "brr/work")
    (repo / "hud.py").write_text("HUD = 1\n", encoding="utf-8")
    _git(repo, "add", "hud.py")
    _git(repo, "commit", "-m", "The HUD is one shape")
    (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    outbox_dir.mkdir(parents=True)
    (outbox_dir / ".relics.jsonl").write_text(
        json.dumps({"kind": "kb", "path": "design-the-loom.md"}) + "\n"
        + json.dumps({"kind": "issue", "number": 1977, "action": "closed"}) + "\n",
        encoding="utf-8",
    )
    (outbox_dir / ".pr").write_text("1978\n", encoding="utf-8")
    task = Run(
        id="run-1", event_id="evt-1", body="", source="telegram",
        meta={
            "branch_name": "brr/work", "seed_ref": "main", "seed_oid": seed_oid,
            "repo_label": "acme__widgets",
        },
    )
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="running", attempt=1, runner_name="codex", brr_dir=brr_dir,
        work_dir=repo, start_monotonic=FROZEN_MONO - 900,
        output_stats={"current": 1, "other": 0, "outbound": 2},
    )


def scenario_with_notices(tmp_path: Path):
    """Refused and dropped directives on the notices log, a staged outbox file."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    outbox_dir.mkdir(parents=True)
    rows = [
        {"at": "2026-09-14T16:10:00Z", "text": "spawn refused: no branch declared",
         "kind": "refused", "lifetime": "run", "source_file": "spawn.md",
         "verb": "spawn", "run": "run-1"},
        {"at": "2026-09-14T16:11:00Z", "text": "note dropped: event evt-9 is not pending",
         "kind": "dropped", "lifetime": "run"},
    ]
    (outbox_dir / daemon.NOTICES_FILE).write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8",
    )
    staged = outbox_dir / "reply.md"
    staged.write_text("hello\n", encoding="utf-8")
    os.utime(staged, (FROZEN_WALL - 42, FROZEN_WALL - 42))
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="running", attempt=2, runner_name="codex", brr_dir=brr_dir,
        start_monotonic=FROZEN_MONO - 60,
    )


def scenario_with_shuttle(tmp_path: Path):
    """The Shuttle projection: a home whose Shuttle is awake on this run."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "shuttle.json").write_text(json.dumps({
        "key": "acc-1", "state": "awake", "why": "dispatch",
        "since": "2026-09-14T16:05:00Z", "run_id": "run-1",
        "repo_root": None, "conversation_key": "telegram:1",
        "transitions": [],
    }), encoding="utf-8")
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="running", attempt=1, runner_name="codex", brr_dir=brr_dir,
        shuttle_home=home, start_monotonic=FROZEN_MONO - 30,
    )


def scenario_with_tick(tmp_path: Path):
    """A loop that has ticked twice: ``tick{n, at}`` on the capsule."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    tick_mod.advance(home)
    tick_mod.advance(home)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="running", attempt=1, runner_name="codex", brr_dir=brr_dir,
        shuttle_home=home, start_monotonic=FROZEN_MONO - 30,
    )


def scenario_busy_seat(tmp_path: Path):
    """Pending mail, an accepted bolt, a card, armed letters, a queued spawn."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    _write_event(inbox_dir, "evt-1", "ship it")
    _write_event(inbox_dir, "evt-2", "and one more thing", conversation_key="telegram:1")
    _write_event(
        inbox_dir, "evt-3", "child spec",
        spawn_immediate="true", spawn_quota_queued="true",
    )
    schedule_mod.save_armed_letters(brr_dir, [{
        "id": "morning", "when": "2026-09-15T06:00:00Z", "at": FROZEN_WALL + 50_000,
        "heading": "Morning sweep", "premise": "the queue is still there",
    }])
    task = Run(
        id="run-1", event_id="evt-1", body="", source="telegram",
        conversation_key="telegram:1",
        meta={
            "bolt": {"annotated": 2, "accepted_at": "2026-09-14T16:20:00Z"},
            "kb_base_url": "https://example.invalid/kb/",
            "repo_label": "acme__widgets", "branch_name": "brr/run-1",
        },
    )
    outbox_dir.mkdir(parents=True)
    (outbox_dir / ".name").write_text("the busy seat\n", encoding="utf-8")
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="running", attempt=1, runner_name="claude", brr_dir=brr_dir,
        runner_meta={"model": "fable"},
        card_state={"last": "## Now\nshipping\n", "written_monotonic": FROZEN_MONO - 700},
        output_stats={"current": 0, "other": 1, "outbound": 0},
        start_monotonic=FROZEN_MONO - 1800, quota_summary="session 88% left",
        cfg={}, repo_label="acme__widgets",
    )


def scenario_home_run(tmp_path: Path):
    """An account-home run: the forge lane is overwritten to absent."""
    brr_dir, outbox_dir, inbox_dir = _dirs(tmp_path)
    task = Run(
        id="run-1", event_id="evt-1", body="", source="schedule",
        meta={"root_kind": "home", "github_pr_number": 12},
    )
    return (outbox_dir, inbox_dir, "evt-1", task), dict(
        phase="running", attempt=1, runner_name="codex", brr_dir=brr_dir,
        start_monotonic=FROZEN_MONO - 5,
    )


SCENARIOS: dict[str, Scenario] = {
    "fresh_run": scenario_fresh_run,
    "await_armed": scenario_await_armed,
    "hold_parked": scenario_hold_parked,
    "strand_run": scenario_strand_run,
    "with_produce": scenario_with_produce,
    "with_notices": scenario_with_notices,
    "with_shuttle": scenario_with_shuttle,
    "with_tick": scenario_with_tick,
    "busy_seat": scenario_busy_seat,
    "home_run": scenario_home_run,
}


def portable(text: str, tmp_path: Path) -> str:
    """The written bytes with the temp root and the token spelled portably."""
    payload = json.loads(text)
    token = payload.get("change_token")
    for root in {str(tmp_path.resolve()), str(tmp_path)}:
        text = text.replace(root, "<ROOT>")
    if isinstance(token, str) and token:
        text = text.replace(f'"change_token": "{token}"', '"change_token": "<TOKEN>"')
    return text


def drive(name: str, tmp_path: Path, monkeypatch) -> tuple[str, dict[str, Any]]:
    """Run scenario *name* through ``daemon._write_live_portal_state``."""
    frozen_host(monkeypatch)
    args, kwargs = SCENARIOS[name](tmp_path)
    path = daemon._write_live_portal_state(*args, **kwargs)
    assert path is not None, f"{name}: the writer returned no path"
    text = path.read_text(encoding="utf-8")
    return text, json.loads(text)


def without_ledger(text: str) -> tuple[str, Any]:
    """The payload as ``main`` wrote it: move 5's one added key taken back out.

    ``produce.ledger`` (the loom's four kinds, §5 of the move) is the only key
    the split adds. It is popped and the payload re-serialised with the
    writer's own ``json.dumps`` arguments, so every other byte is still
    compared against the capture from the unsplit writer. Absent ⇒ the text
    is returned untouched (the capture itself runs through here).
    """
    payload = json.loads(text)
    produce = payload.get("produce")
    if not isinstance(produce, dict) or "ledger" not in produce:
        return text, None
    ledger = produce.pop("ledger")
    return json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", ledger


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_portal_state_golden(name, tmp_path, monkeypatch):
    text, payload = drive(name, tmp_path, monkeypatch)
    # The token substitution hides the value only after proving it.
    assert payload["change_token"] == daemon._change_token(
        {k: v for k, v in payload.items() if k != "change_token"}
    )
    main_text, ledger = without_ledger(text)
    if not WRITE:
        assert ledger is not None, "the split writer projects produce.ledger on every tick"
    got = portable(main_text, tmp_path)
    golden = GOLDEN_DIR / f"{name}.json"
    if WRITE:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(got, encoding="utf-8")
        return
    assert golden.exists(), f"missing golden {golden.name}: capture on main first"
    assert got == golden.read_text(encoding="utf-8")
