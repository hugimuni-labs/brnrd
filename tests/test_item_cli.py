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


# ── `brnrd goal record` / `brnrd goal show` ──────────────────────────────


def _new_goal(**kwargs) -> None:
    args = ["item", "new", "Grow attention", "--type", "goal"]
    for flag, value in kwargs.items():
        args += [f"--{flag}", value]
    main(args)


def test_goal_record_appends_and_echoes_latest(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal(metric="tickets", target="1000", horizon="Q4")
    capsys.readouterr()

    rc = main(["goal", "record", "g-1", "tickets", "10", "--source", "manual"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "g-1 tickets = 10" in out
    assert "via manual" in out


def test_goal_record_refuses_a_non_goal_item(tmp_path, monkeypatch, capsys):
    repo = _repo_with_home(tmp_path, monkeypatch)
    main(["item", "new", "Ship the digest", "--type", "action"])
    capsys.readouterr()

    rc = main(["goal", "record", "w-1", "tickets", "10"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not a goal" in err


def test_goal_record_refuses_an_unknown_id(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal()
    capsys.readouterr()

    rc = main(["goal", "record", "g-99", "tickets", "10"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "nothing matches" in err


def test_goal_show_says_no_readings_yet_when_unread(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal(metric="tickets", target="1000", horizon="Q4")
    capsys.readouterr()

    rc = main(["goal", "show", "g-1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "metric: tickets" in out
    assert "target: 1000" in out
    assert "horizon: Q4" in out
    assert "no readings yet" in out


def test_goal_show_renders_latest_delta_and_sample_count(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal(metric="tickets", target="1000", horizon="Q4")
    capsys.readouterr()

    main(["goal", "record", "g-1", "tickets", "10", "--source", "manual"])
    capsys.readouterr()
    main(["goal", "record", "g-1", "tickets", "15", "--source", "manual"])
    capsys.readouterr()

    rc = main(["goal", "show", "g-1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tickets: 15" in out
    assert "vs previous" in out
    assert "2 samples" in out


def test_goal_record_accepts_a_basis_flag(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal(metric="impressions", target="1000", horizon="Q4")
    capsys.readouterr()

    rc = main(
        [
            "goal", "record", "g-1", "impressions", "147",
            "--source", "x-api", "--basis", "window5",
        ]
    )
    assert rc == 0
    from brr import cli as cli_mod
    from brr import items as items_mod

    warp_root, err = cli_mod._item_context()
    assert err is None
    readings = items_mod.load_readings(warp_root, "g-1")
    assert readings[-1].basis == "window5"


def test_goal_record_warns_on_changed_basis_and_still_appends(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal(metric="impressions")
    capsys.readouterr()
    main(["goal", "record", "g-1", "impressions", "333", "--basis", "lifetime"])
    capsys.readouterr()

    rc = main(["goal", "record", "g-1", "impressions", "147", "--basis", "window5"])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("g-1 impressions = 147")
    assert "standing basis for impressions is lifetime" in captured.err
    assert "given basis is window5" in captured.err
    assert "Δ will be refused" in captured.err
    from brr import cli as cli_mod
    from brr import items as items_mod

    warp_root, err = cli_mod._item_context()
    assert err is None
    readings = items_mod.load_readings(warp_root, "g-1")
    assert [reading.basis for reading in readings] == ["lifetime", "window5"]


def test_goal_record_without_basis_prints_standing_basis(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal(metric="impressions")
    capsys.readouterr()
    main(["goal", "record", "g-1", "impressions", "333", "--basis", "lifetime"])
    capsys.readouterr()

    rc = main(["goal", "record", "g-1", "impressions", "334"])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("g-1 impressions = 334")
    assert "standing basis for impressions: lifetime" in captured.err


def test_goal_record_with_matching_basis_prints_no_warning(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal(metric="impressions")
    capsys.readouterr()
    main(["goal", "record", "g-1", "impressions", "333", "--basis", "lifetime"])
    capsys.readouterr()

    rc = main(["goal", "record", "g-1", "impressions", "334", "--basis", "lifetime"])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("g-1 impressions = 334")
    assert captured.err == ""


def test_goal_record_first_reading_prints_no_warning_and_appends(tmp_path, monkeypatch, capsys):
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal(metric="impressions")
    capsys.readouterr()

    rc = main(["goal", "record", "g-1", "impressions", "333", "--basis", "lifetime"])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("g-1 impressions = 333")
    assert captured.err == ""
    from brr import cli as cli_mod
    from brr import items as items_mod

    warp_root, err = cli_mod._item_context()
    assert err is None
    readings = items_mod.load_readings(warp_root, "g-1")
    assert len(readings) == 1


def test_goal_show_marks_a_refused_cross_basis_delta(tmp_path, monkeypatch, capsys):
    # The live bug's shape reproduced through the CLI end to end: same
    # key, same source, two denominators — `goal show` must say the
    # comparison was refused, not print a number that isn't one.
    _repo_with_home(tmp_path, monkeypatch)
    _new_goal(metric="impressions", target="1000", horizon="Q4")
    capsys.readouterr()

    main(
        [
            "goal", "record", "g-1", "impressions", "333",
            "--source", "x-api", "--basis", "lifetime",
        ]
    )
    capsys.readouterr()
    main(
        [
            "goal", "record", "g-1", "impressions", "147",
            "--source", "x-api", "--basis", "window5",
        ]
    )
    capsys.readouterr()

    rc = main(["goal", "show", "g-1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "impressions: 147" in out
    assert "Δ refused: basis differs" in out
    assert "Δ-186" not in out
    assert "Δ+" not in out and "Δ-" not in out
