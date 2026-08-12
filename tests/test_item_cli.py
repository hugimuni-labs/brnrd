"""``brnrd item`` CLI, goal-node round (design-goal-oriented-engineering.md):
``item new --type goal`` and the goals band ``item list`` now renders
above ready/held — thin argparse-to-``items.py`` wiring, exercised
end-to-end once here since ``items.py``'s own tests already cover the
grammar and derivation in isolation.
"""

from __future__ import annotations

from pathlib import Path

from brr.cli import main

from _helpers import init_git_repo


def _repo_with_home(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    from brr import account

    account.resolve_context(repo, {}, create=True)
    return repo


def test_item_new_type_goal_mints_g_prefixed_id(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    rc = main([
        "item", "new", "Grow attention on the account",
        "--type", "goal",
        "--metric", "tickets bought",
        "--target", "exponential",
        "--horizon", "ongoing",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("g-1 — ")
    path = Path(out.split(" — ", 1)[1].strip())
    text = path.read_text(encoding="utf-8")
    assert "type: goal" in text
    assert "metric: tickets bought" in text
    assert "target: exponential" in text
    assert "horizon: ongoing" in text


def test_item_new_action_still_mints_w_prefixed_id(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    rc = main(["item", "new", "Ship the digest", "--type", "action"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("w-1 — ")


def test_item_new_advances_row_on_a_non_goal_item(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    main(["item", "new", "Grow attention", "--type", "goal"])
    capsys.readouterr()
    rc = main([
        "item", "new", "Ship the digest", "--type", "action", "--advances", "g-1",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    path = Path(out.split(" — ", 1)[1].strip())
    assert "advances: g-1" in path.read_text(encoding="utf-8")


def test_item_list_renders_goals_band_above_ready_held(tmp_path, monkeypatch, capsys):
    repo = _repo_with_home(tmp_path, monkeypatch)
    main([
        "item", "new", "Grow attention", "--type", "goal",
        "--metric", "tickets", "--target", "1000", "--horizon", "Q4",
    ])
    capsys.readouterr()
    main(["item", "new", "Decide the shape", "--type", "decision"])
    capsys.readouterr()

    rc = main(["item", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = out.strip().split("\n")
    assert lines[0] == "goals:"
    assert "g-1" in lines[1] and "metric: tickets" in lines[1]
    assert "ready:" in lines
    assert lines.index("goals:") < lines.index("ready:")
