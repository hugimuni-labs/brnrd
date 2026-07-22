"""Retention GC (#501): window math, dry-run/real parity, live-run protection."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from brr import account
from brr import gitops
from brr import presence
from brr import retention

from _helpers import init_git_repo


NOW = 1_800_000_000.0  # fixed "now" so window math is deterministic
DAY = 86_400.0


# ── scaffolding ─────────────────────────────────────────────────────


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    return repo


def _ctx(tmp_path: Path) -> account.HomeContext:
    home = tmp_path / "home"
    ctx = account.HomeContext(
        account_id="acc_test",
        dominion_repo=home,
        dispatch_inbox=home / "dispatch" / "inbox",
        responses_dir=home / "dispatch" / "responses",
        runs_dir=home / "runs",
        repos={},
        default_repo=account.AccountRepo(label="r", root=tmp_path / "repo"),
        home_root=home,
    )
    for p in (ctx.dispatch_inbox, ctx.responses_dir, ctx.runs_dir):
        p.mkdir(parents=True, exist_ok=True)
    return ctx


def _account_ctx(tmp_path: Path, account_dir: str = "acc_test") -> account.HomeContext:
    home = tmp_path / "state" / "brnrd" / "accounts" / account_dir / "home"
    ctx = account.HomeContext(
        account_id=account_dir,
        dominion_repo=home,
        dispatch_inbox=home / "dispatch" / "inbox",
        responses_dir=home / "dispatch" / "responses",
        runs_dir=home / "runs",
        repos={},
        default_repo=account.AccountRepo(label="r", root=tmp_path / "repo"),
        kind="account",
        home_root=home,
    )
    for path in (ctx.dispatch_inbox, ctx.responses_dir, ctx.runs_dir):
        path.mkdir(parents=True, exist_ok=True)
    return ctx


def _write_aged(path: Path, text: str, age_days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    stamp = NOW - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def _iso(age_days: float) -> str:
    return datetime.fromtimestamp(NOW - age_days * DAY, tz=timezone.utc).isoformat()


def _windows(**days: float) -> retention.Windows:
    cfg = {f"retention.{k}_days": v for k, v in days.items()}
    return retention.Windows.from_config(cfg)


def _totals(reports: dict[str, retention.StoreReport]) -> tuple[int, int]:
    return (
        sum(r.items for r in reports.values()),
        sum(r.bytes for r in reports.values()),
    )


# ── window math ─────────────────────────────────────────────────────


def test_window_absent_zero_or_garbage_means_keep_forever():
    w = retention.Windows.from_config({
        "retention.messages_days": 0,
        "retention.inbox_days": -3,
        "retention.run_state_days": "not-a-number",
        "retention.run_history_days": "not-a-number",
    })
    assert w.conversations is None  # absent
    assert w.messages is None       # zero
    assert w.inbox is None          # negative
    assert w.run_history is None    # garbage
    assert w.run_state is None      # absent
    assert w.all_disabled()


def test_window_days_convert_to_seconds():
    w = retention.Windows.from_config({
        "retention.conversations_days": 90,
        "retention.ledger_days": "365",
    })
    assert w.conversations == 90 * DAY
    assert w.ledger == 365 * DAY
    assert not w.all_disabled()


# ── conversations ───────────────────────────────────────────────────


def test_conversations_prunes_old_logs_and_empty_dirs(tmp_path):
    repo = _repo(tmp_path)
    conv = gitops.shared_brr_dir(repo) / "conversations" / "chat__1"
    old = _write_aged(conv / "evt-old.jsonl", '{"x":1}\n', age_days=120)
    fresh = _write_aged(conv / "evt-new.jsonl", '{"x":2}\n', age_days=5)

    plan, reports = retention.gc(
        repo, None, _windows(conversations=90), dry_run=False, now=NOW)
    assert not old.exists()
    assert fresh.exists()
    assert conv.exists()  # dir kept: still holds the fresh log
    assert reports["conversations"].items == 1

    # once the last log ages out, the husk dir goes too
    _write_aged(conv / "evt-new.jsonl", '{"x":2}\n', age_days=200)
    retention.gc(repo, None, _windows(conversations=90), dry_run=False, now=NOW)
    assert not conv.exists()


# ── run-history (#500) ──────────────────────────────────────────────


def test_run_history_goes_but_bundle_body_stays(tmp_path):
    repo = _repo(tmp_path)
    run_dir = gitops.shared_brr_dir(repo) / "runs" / "run-old"
    _write_aged(run_dir / "history" / "000-thread.md", "old copy", age_days=60)
    body = _write_aged(run_dir / "body.md", "the card body", age_days=60)

    _plan, reports = retention.gc(
        repo, None, _windows(run_history=30), dry_run=False, now=NOW)
    assert not (run_dir / "history").exists()
    assert body.exists()
    assert reports["run-history"].items == 1


def test_live_run_history_is_protected(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = gitops.shared_brr_dir(repo)
    run_dir = brr_dir / "runs" / "run-live"
    _write_aged(run_dir / "history" / "000-thread.md", "old copy", age_days=60)
    presence.register(
        brr_dir, kind="daemon-run", run_id="run-live", pid=os.getpid())

    plan = retention.build_plan(repo, None, _windows(run_history=30), now=NOW)
    assert "run-live" in plan.live_run_ids
    assert plan.actions == []
    assert (run_dir / "history").exists()


# ── worktrees ───────────────────────────────────────────────────────


def test_stale_worktree_removed_live_and_fresh_kept(tmp_path):
    repo = _repo(tmp_path)
    brr_dir = gitops.shared_brr_dir(repo)
    stale = brr_dir / "worktrees" / "run-stale"
    _write_aged(stale / "somefile.txt", "x" * 100, age_days=45)
    fresh = brr_dir / "worktrees" / "run-fresh"
    _write_aged(fresh / "somefile.txt", "y", age_days=1)
    live = brr_dir / "worktrees" / "run-live"
    _write_aged(live / "somefile.txt", "z", age_days=45)
    presence.register(
        brr_dir, kind="daemon-run", run_id="run-live", pid=os.getpid())

    _plan, reports = retention.gc(
        repo, None, _windows(worktrees=30), dry_run=False, now=NOW)
    assert not stale.exists()
    assert fresh.exists()
    assert live.exists()
    assert reports["worktrees"].items == 1


# ── ledger ──────────────────────────────────────────────────────────


def test_ledger_rewrite_drops_old_keeps_fresh_and_malformed(tmp_path):
    repo = _repo(tmp_path)
    ledger = gitops.shared_brr_dir(repo) / "run-ledger.jsonl"
    rows = [
        json.dumps({"run_id": "old", "ended_at": _iso(400)}),
        json.dumps({"run_id": "fresh", "ended_at": _iso(10)}),
        "{malformed json row",
        json.dumps({"run_id": "no-stamp"}),
    ]
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("\n".join(rows) + "\n", encoding="utf-8")

    _plan, reports = retention.gc(
        repo, None, _windows(ledger=365), dry_run=False, now=NOW)
    kept = ledger.read_text(encoding="utf-8").splitlines()
    assert reports["ledger"].items == 1
    assert len(kept) == 3
    assert not any("old" in line and "ended_at" in line for line in kept)
    assert "{malformed json row" in kept  # GC never invents data loss


def test_ledger_rewrite_survives_append_between_plan_and_execute(tmp_path):
    """The appender is lock-free; a row landing after plan must survive."""
    repo = _repo(tmp_path)
    ledger = gitops.shared_brr_dir(repo) / "run-ledger.jsonl"
    rows = [
        json.dumps({"run_id": "old", "ended_at": _iso(400)}),
        json.dumps({"run_id": "fresh", "ended_at": _iso(10)}),
    ]
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("\n".join(rows) + "\n", encoding="utf-8")

    plan = retention.build_plan(repo, None, _windows(ledger=365), now=NOW)
    assert [a.store for a in plan.actions] == ["ledger"]

    # Daemon closes a run between plan and execute: lock-free append.
    raced = json.dumps({"run_id": "raced", "ended_at": _iso(0)})
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(raced + "\n")

    reports = retention.execute_plan(repo, plan)
    kept = ledger.read_text(encoding="utf-8").splitlines()
    assert reports["ledger"].items == 1
    assert raced in kept  # the appended row survives the rewrite
    assert len(kept) == 2
    assert not any('"old"' in line for line in kept)


# ── message store ───────────────────────────────────────────────────


def _message(status: str) -> str:
    return f"---\nstatus: {status}\n---\n\nhello\n"


def test_messages_terminal_old_deleted_pending_and_live_kept(tmp_path):
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path)
    base = ctx.runs_dir / "some__repo"
    old_done = _write_aged(
        base / "run-a" / "messages" / "m1.md", _message("delivered"), 120)
    old_pending = _write_aged(
        base / "run-a" / "messages" / "m2.md", _message("pending"), 120)
    fresh_done = _write_aged(
        base / "run-a" / "messages" / "m3.md", _message("collected"), 5)
    live_old = _write_aged(
        base / "run-live" / "messages" / "m4.md", _message("delivered"), 120)
    presence.register(
        gitops.shared_brr_dir(repo), kind="daemon-run",
        run_id="run-live", pid=os.getpid())

    _plan, reports = retention.gc(
        repo, ctx, _windows(messages=90), dry_run=False, now=NOW)
    assert not old_done.exists()
    assert old_pending.exists()   # undelivered mail is never GC'd
    assert fresh_done.exists()
    assert live_old.exists()      # live-run protection
    assert reports["messages"].items == 1


# ── inbox archives ──────────────────────────────────────────────────


def _event(status: str) -> str:
    return f"---\nstatus: {status}\nsource: telegram\n---\n\nbody\n"


def test_inbox_done_events_and_artifacts_deleted_pending_kept(tmp_path):
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path)
    inbox, resp = ctx.dispatch_inbox, ctx.responses_dir
    done = _write_aged(inbox / "evt-done.md", _event("done"), 120)
    attach = _write_aged(
        inbox / "evt-done.attachments" / "img.png", "PNG", 120).parent
    response = _write_aged(resp / "evt-done.md", "reply", 120)
    partial = _write_aged(resp / "evt-done.partials" / "0001.md", "p", 120).parent
    pending = _write_aged(inbox / "evt-pending.md", _event("pending"), 120)
    fresh_done = _write_aged(inbox / "evt-fresh.md", _event("done"), 5)
    orphan = _write_aged(resp / "evt-orphan.md", "orphan reply", 120)

    _plan, reports = retention.gc(
        repo, ctx, _windows(inbox=90), dry_run=False, now=NOW)
    assert not done.exists()
    assert not attach.exists()
    assert not response.exists()
    assert not partial.exists()
    assert not orphan.exists()
    assert pending.exists()      # unhandled events survive any age
    assert fresh_done.exists()
    assert reports["inbox"].items >= 4


# ── account-home run state (#320) ──────────────────────────────────


def test_run_state_window_boundary_and_keep_forever_default(tmp_path):
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path)
    old = _write_aged(
        ctx.runs_dir / "r" / "run-old" / "state.md", "old state", 90.01)
    boundary = _write_aged(
        ctx.runs_dir / "r" / "run-boundary" / "state.md", "boundary", 90)

    disabled = retention.build_plan(
        repo, ctx, retention.Windows.from_config({}), now=NOW)
    assert not any(action.store == "run-state" for action in disabled.actions)

    _plan, reports = retention.gc(
        repo, ctx, _windows(run_state=90), dry_run=False, now=NOW)
    assert not old.exists()
    assert boundary.exists()  # exactly at the cutoff is retained
    assert reports["run-state"].items == 1


def test_live_run_state_is_protected_and_legacy_docs_are_supported(tmp_path):
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path)
    live = _write_aged(
        ctx.runs_dir / "r" / "run-live" / "state.md", "live", 120)
    legacy = _write_aged(
        ctx.dominion_repo / "run-state" / "r" / "run-legacy.md",
        "legacy", 120,
    )
    presence.register(
        gitops.shared_brr_dir(repo), kind="daemon-run",
        run_id="run-live", pid=os.getpid())

    _plan, reports = retention.gc(
        repo, ctx, _windows(run_state=90), dry_run=False, now=NOW)
    assert live.exists()
    assert not legacy.exists()
    assert reports["run-state"].items == 1


# ── obsolete literal connected account (#320) ─────────────────────


def test_stale_connected_account_collected_without_general_orphan_sweep(tmp_path):
    repo = _repo(tmp_path)
    ctx = _account_ctx(tmp_path)
    accounts_root = ctx.home_root.parent.parent
    stale = accounts_root / "connected"
    _write_aged(stale / "home" / "knowledge" / "memory.md", "obsolete", 1)
    legitimate = accounts_root / "disconnected-legitimate"
    memory = _write_aged(
        legitimate / "home" / "knowledge" / "memory.md", "keep", 999)

    _plan, reports = retention.gc(
        repo, ctx, retention.Windows.from_config({}), dry_run=False, now=NOW)
    assert not stale.exists()
    assert memory.exists()
    assert reports["connected"].items == 1


def test_connected_account_is_kept_when_it_is_the_resolved_home(tmp_path):
    repo = _repo(tmp_path)
    ctx = _account_ctx(tmp_path, account_dir="connected")
    marker = _write_aged(ctx.home_root / "knowledge" / "memory.md", "keep", 999)

    plan, reports = retention.gc(
        repo, ctx, retention.Windows.from_config({}), dry_run=False, now=NOW)
    assert marker.exists()
    assert not any(action.store == "connected" for action in plan.actions)
    assert reports == {}


# ── dry-run vs real parity ──────────────────────────────────────────


def test_dry_run_deletes_nothing_and_reports_what_real_run_deletes(tmp_path):
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path)
    brr_dir = gitops.shared_brr_dir(repo)
    conv = _write_aged(
        brr_dir / "conversations" / "c" / "e.jsonl", '{"a":1}\n', 120)
    hist = _write_aged(
        brr_dir / "runs" / "run-x" / "history" / "h.md", "copy", 60)
    msg = _write_aged(
        ctx.runs_dir / "r" / "run-x" / "messages" / "m.md",
        _message("delivered"), 120)
    windows = _windows(conversations=90, run_history=30, messages=90)

    _dry_plan, dry = retention.gc(repo, ctx, windows, dry_run=True, now=NOW)
    assert conv.exists() and hist.exists() and msg.exists()

    _real_plan, real = retention.gc(repo, ctx, windows, dry_run=False, now=NOW)
    assert not conv.exists() and not hist.exists() and not msg.exists()

    assert _totals(dry) == _totals(real)
    assert {s: (r.items, r.bytes) for s, r in dry.items()} == \
           {s: (r.items, r.bytes) for s, r in real.items()}
    assert all(r.errors == 0 for r in real.values())


def test_dry_run_report_includes_account_home_stores(tmp_path):
    repo = _repo(tmp_path)
    ctx = _account_ctx(tmp_path)
    state = _write_aged(
        ctx.runs_dir / "r" / "run-old" / "state.md", "run state", 120)
    connected = ctx.home_root.parent.parent / "connected"
    marker = _write_aged(connected / "home" / "account" / "repos.json", "{}", 1)
    windows = _windows(run_state=90)

    _plan, reports = retention.gc(
        repo, ctx, windows, dry_run=True, now=NOW)
    rendered = retention.render_report(reports, windows, dry_run=True)
    assert state.exists() and marker.exists()
    assert reports["run-state"].items == 1
    assert reports["connected"].items == 1
    assert "run-state" in rendered
    assert "connected" in rendered
    assert "would delete" in rendered


def test_disabled_windows_touch_nothing(tmp_path):
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path)
    conv = _write_aged(
        gitops.shared_brr_dir(repo) / "conversations" / "c" / "e.jsonl",
        "{}\n", 999)
    plan, reports = retention.gc(
        repo, ctx, retention.Windows.from_config({}), dry_run=False, now=NOW)
    assert plan.actions == []
    assert reports == {}
    assert conv.exists()


# ── fresh-install seeding ───────────────────────────────────────────


def test_fresh_install_config_gets_retention_defaults(tmp_path):
    from brr import adopt
    from brr import config as conf

    repo = _repo(tmp_path)
    adopt._setup_brr_dir(repo)
    cfg = conf.load_config(repo)
    for key, value in retention.FRESH_INSTALL_DEFAULTS.items():
        assert cfg.get(key) == value


def test_existing_config_is_never_touched(tmp_path):
    from brr import adopt
    from brr import config as conf

    repo = _repo(tmp_path)
    (gitops.shared_brr_dir(repo)).mkdir(parents=True, exist_ok=True)
    conf.write_config(repo, {"runner": "auto"})
    adopt._setup_brr_dir(repo)
    cfg = conf.load_config(repo)
    assert "retention.conversations_days" not in cfg
    assert "retention.run_state_days" not in cfg


# ── daemon sweep plumbing ───────────────────────────────────────────


def test_daemon_sweep_disabled_by_interval_zero(tmp_path):
    from brr import config as conf
    from brr import daemon

    repo = _repo(tmp_path)
    conf.write_config(repo, {
        "retention.sweep_interval_hours": 0,
        "retention.conversations_days": 1,
    })
    doomed = _write_aged(
        gitops.shared_brr_dir(repo) / "conversations" / "c" / "e.jsonl",
        "{}\n", 999)
    assert daemon._retention_sweep(repo, None) == 0.0
    assert doomed.exists()


def test_daemon_sweep_runs_gc_and_returns_cadence(tmp_path):
    from brr import config as conf
    from brr import daemon

    repo = _repo(tmp_path)
    conf.write_config(repo, {"retention.conversations_days": 1})
    doomed = _write_aged(
        gitops.shared_brr_dir(repo) / "conversations" / "c" / "e.jsonl",
        "{}\n", 999)
    interval = daemon._retention_sweep(repo, None)
    assert interval == retention.SWEEP_INTERVAL_DEFAULT_HOURS * 3600.0
    assert not doomed.exists()
