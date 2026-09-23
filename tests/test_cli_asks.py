"""``brnrd asks`` — the LRU list of asks (design-the-ask.md §Build cut, 1).

Five items on a real ``tmp_path`` surface, commit times pinned per file
through ``GIT_COMMITTER_DATE`` so the ordering is a fact, not a race.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from brr import account
from brr.cli import main

from _helpers import init_git_repo


def _commit(surface: Path, rel: str, when: datetime) -> None:
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_COMMITTER_DATE": when.isoformat(),
        "GIT_AUTHOR_DATE": when.isoformat(),
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    for cmd in (["add", rel], ["commit", "-q", "-m", f"touch {rel}", "--", rel]):
        subprocess.run(["git", "-C", str(surface), *cmd], check=True, env=env,
                       capture_output=True)


def _surface(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    ctx = account.resolve_context(repo, {}, create=True)
    surface = account.work_surface_path(ctx)
    warp = surface / "warp"
    warp.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if not (surface.parent / ".git").exists():
        subprocess.run(["git", "-C", str(surface.parent), "init", "-q"],
                       check=True, env=env, capture_output=True)
    return surface, ctx.runs_dir


def _item(warp: Path, item_id: str, headline: str, rows: str = "") -> str:
    (warp / f"{item_id}.md").write_text(
        f"# {headline}\n\n{rows}\nbody text\n", encoding="utf-8")
    return f"warp/{item_id}.md"


def _five(tmp_path, monkeypatch):
    surface, runs_dir = _surface(tmp_path, monkeypatch)
    warp = surface / "warp"
    now = datetime.now(timezone.utc)
    spec = [
        ("w-1", "Fresh action", "type: action\n", now - timedelta(hours=3)),
        ("w-2", "Returned ask", "type: decision\nreturn: a page\nstage: making\n",
         now - timedelta(days=2)),
        ("w-3", "Old preparation", "type: preparation\n", now - timedelta(days=90)),
        ("w-4", "Older action", "type: action\n", now - timedelta(days=200)),
        ("g-1", "Ship the pipe", "type: goal\nmetric: tickets\ntarget: 8 a week\n"
         "horizon: Q4\n", now - timedelta(days=1)),
    ]
    for item_id, headline, rows, when in spec:
        _commit(surface, _item(warp, item_id, headline, rows), when)
    return surface, runs_dir, now


def _asks(capsys, *argv):
    assert main(["asks", *argv]) == 0
    return capsys.readouterr().out


def test_order_stale_bucket_and_goal_lines(tmp_path, monkeypatch, capsys):
    _five(tmp_path, monkeypatch)
    out = _asks(capsys)
    lines = out.splitlines()
    assert lines[0].startswith("◎ g-1 · Ship the pipe")
    assert "tickets · 8 a week · Q4" in lines[1]
    body = [ln for ln in lines if ln.startswith("w-") or ln.startswith("──")]
    assert [ln.split(" · ")[0] for ln in body] == ["w-1", "w-2", "── stale", "w-3", "w-4"]
    assert "stale · untouched > 60d" in out
    assert "g-1 · Ship" not in "\n".join(body)  # goals never in the LRU
    w2 = next(ln for ln in lines if ln.startswith("w-2"))
    assert w2 == "w-2 · Returned ask · decision · a page · touched 2d · says 0"
    w1 = next(ln for ln in lines if ln.startswith("w-1"))
    assert "· - ·" in w1 and "touched 3h" in w1


def test_asks_rows_lift_an_item_and_count_says(tmp_path, monkeypatch, capsys):
    surface, runs_dir, _ = _five(tmp_path, monkeypatch)
    run = runs_dir / "some__repo" / "run-1"
    run.mkdir(parents=True)
    rows = [{"event": "evt-1", "item": "w-3"}, {"event": "evt-2", "item": "w-3"},
            {"event": "evt-2", "item": "w-3"}]  # says counts binding rows, including repeated bindings
    (run / "asks.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    now = time.time()
    os.utime(run / "asks.jsonl", (now, now))
    run2 = runs_dir / "some__repo" / "run-2"
    run2.mkdir(parents=True)
    (run2 / ".asks.jsonl").write_text(
        json.dumps({"event": "evt-3", "item": "w-2"}) + "\n", encoding="utf-8")
    os.utime(run2 / ".asks.jsonl", (now - 3600, now - 3600))
    out = _asks(capsys)
    lines = [ln for ln in out.splitlines() if ln.startswith("w-")]
    # w-3 was 90d old; a fresh say relifts it out of the stale bucket
    assert lines[0].startswith("w-3") and "says 3" in lines[0]
    assert "touched now" in lines[0]
    assert lines[1].startswith("w-2") and "touched 1h" in lines[1] and "says 1" in lines[1]
    assert "── stale" in out and out.index("w-4") > out.index("── stale")


def test_json_shape(tmp_path, monkeypatch, capsys):
    _five(tmp_path, monkeypatch)
    rows = json.loads(_asks(capsys, "--json"))
    assert [r["id"] for r in rows] == ["g-1", "w-1", "w-2", "w-3", "w-4"]
    keys = {"id", "title", "type", "return", "stage", "touched_at", "says", "stale", "sign"}
    assert keys == set(rows[0])
    by = {r["id"]: r for r in rows}
    assert by["w-2"]["return"] == "a page" and by["w-2"]["stage"] == "making"
    assert by["w-1"]["return"] is None
    assert [by[i]["stale"] for i in ("w-1", "w-3", "w-4", "g-1")] == [False, True, True, False]
    assert all(datetime.fromisoformat(r["touched_at"]) for r in rows)


def test_all_adds_done_bucket_and_default_hides_it(tmp_path, monkeypatch, capsys):
    surface, _, _ = _five(tmp_path, monkeypatch)
    text = (surface / "warp" / "w-1.md").read_text()
    (surface / "warp" / "w-1.md").write_text(text.replace(
        "type: action\n", "type: action\ndone: 2026-09-01 run-x\n"))
    hidden = _asks(capsys)
    assert "w-1 ·" not in hidden
    shown = _asks(capsys, "--all")
    assert "── done / retired ──" in shown
    assert "✓ w-1 · Fresh action" in shown.split("── done / retired ──")[1]


def test_title_clipped_and_optional_rows_never_required(tmp_path, monkeypatch, capsys):
    surface, _ = _surface(tmp_path, monkeypatch)
    _commit(surface, _item(surface / "warp", "w-9", "x" * 200), datetime.now(timezone.utc))
    out = _asks(capsys)
    title = out.splitlines()[0].split(" · ")[1]
    assert len(title) == 72 and title.endswith("…")


def test_stale_horizon_reads_config(tmp_path, monkeypatch, capsys):
    from brr import asks
    assert asks.stale_after_days({"asks.stale_after_days": "14"}) == 14
    assert asks.stale_after_days({"asks.stale_after_days": "nope"}) == 60
    assert asks.stale_after_days({}) == 60


def test_reads_only_writes_nothing(tmp_path, monkeypatch, capsys):
    surface, _, _ = _five(tmp_path, monkeypatch)
    before = sorted((p.name, p.read_bytes()) for p in (surface / "warp").iterdir())
    _asks(capsys, "--all")
    after = sorted((p.name, p.read_bytes()) for p in (surface / "warp").iterdir())
    assert before == after


def test_optional_fields_do_not_hide_closed_state_or_goal_type(tmp_path, monkeypatch, capsys):
    surface, _ = _surface(tmp_path, monkeypatch)
    warp = surface / "warp"
    _item(warp, "w-1", "Delivered", "return: a page\nstage: delivered\ntype: action\ndone: 2026-09-01 run-x\n")
    _item(warp, "w-2", "Withdrawn", "type: decision\nsays: evt-1\nattempts: run-x\nretired: 2026-09-01\n")
    _item(warp, "g-1", "Goal", "touched: 2026-09-01\ntype: goal\nmetric: count\ntarget: 8\nhorizon: Q4\n")
    rows = json.loads(_asks(capsys, "--json"))
    assert [r["id"] for r in rows] == ["g-1"]
    out = _asks(capsys, "--all")
    assert out.splitlines()[:2] == ["◎ g-1 · Goal", "    count · 8 · Q4"]
    assert "✓ w-1 · Delivered · action · a page" in out
    assert "✕ w-2 · Withdrawn · decision" in out


def test_live_outbox_bindings_and_config_horizon(tmp_path, monkeypatch, capsys):
    surface, _, _ = _five(tmp_path, monkeypatch)
    repo = Path.cwd()
    runtime = repo / ".brr"
    runtime.mkdir(exist_ok=True)
    (runtime / "config").write_text("asks.stale_after_days=1\n")
    outbox = runtime / "outbox" / "evt-live"
    outbox.mkdir(parents=True)
    (outbox / ".asks.jsonl").write_text('{"event":"evt-live","item":"w-4"}\n')
    rows = json.loads(_asks(capsys, "--json"))
    assert rows[1]["id"] == "w-4" and rows[1]["says"] == 1
    assert next(r for r in rows if r["id"] == "w-2")["stale"] is True
