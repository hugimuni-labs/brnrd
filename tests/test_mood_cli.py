"""`brnrd mood <feeling>` — the mood seam's front door (2026-08-03).

Collapses the lookup-then-write round trip `brnrd emotes <query>` then a
hand-written `.mood` used to leave to the resident. Every test drives the
CLI verb end to end (`cli.main`), the same discipline
`tests/test_promises.py` / the `relic` tests in `tests/test_cli.py` use for
their own front doors — the defect worth guarding is the resolver and the
write disagreeing, or a no-match guessing a face, not a private helper's
return value.
"""

from __future__ import annotations

from pathlib import Path

from brr import cli, hooks


def _run(monkeypatch, outbox, argv):
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))
    return cli.main(argv)


def _mood_text(outbox: Path) -> str:
    return (outbox / hooks.MOOD_NAME).read_text(encoding="utf-8")


def test_mood_resolves_a_feeling_and_writes_the_handle(monkeypatch, tmp_path, capsys):
    assert _run(monkeypatch, tmp_path, ["mood", "focus"]) == 0
    assert _mood_text(tmp_path) == "fo.cus\n"
    out = capsys.readouterr().out
    assert "fo.cus" in out


def test_mood_exact_handle_wins_and_writes_it_verbatim(monkeypatch, tmp_path):
    """An exact handle short-circuits the fuzzy resolver entirely."""
    assert _run(monkeypatch, tmp_path, ["mood", "flow_"]) == 0
    assert _mood_text(tmp_path) == "flow_\n"


def test_mood_accepts_trailing_narration(monkeypatch, tmp_path, capsys):
    assert _run(
        monkeypatch, tmp_path,
        ["mood", "lock_", "the", "repro", "is", "in", "hand"],
    ) == 0
    assert _mood_text(tmp_path) == "lock_\nthe repro is in hand\n"
    assert "the repro is in hand" in capsys.readouterr().out


def test_mood_family_word_matches_nothing_and_writes_nothing(
    monkeypatch, tmp_path, capsys,
):
    """A family word is several faces at once — never a guess between them."""
    assert _run(monkeypatch, tmp_path, ["mood", "satisfied"]) == 1
    assert not (tmp_path / hooks.MOOD_NAME).exists()
    err = capsys.readouterr().err
    assert "no face matches" in err
    assert "nothing was written" in err.lower()
    # The near misses: the same family `emotes.near_misses` would name.
    assert "ahh_" in err or "clean_" in err or "fine_" in err


def test_mood_invented_handle_with_no_near_misses_still_writes_nothing(
    monkeypatch, tmp_path, capsys,
):
    assert _run(monkeypatch, tmp_path, ["mood", "zzzqzxnotaface"]) == 1
    assert not (tmp_path / hooks.MOOD_NAME).exists()
    err = capsys.readouterr().err
    assert "no face matches" in err
    assert "nothing was written" in err.lower()


def test_mood_outside_a_run_says_why(monkeypatch, capsys):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert cli.main(["mood", "flow_"]) == 1
    err = capsys.readouterr().err
    assert "no run outbox" in err
    assert "BRR_OUTBOX_DIR" in err


def test_mood_outbox_flag_overrides_a_missing_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert cli.main(["mood", "flow_", "--outbox", str(tmp_path)]) == 0
    assert _mood_text(tmp_path) == "flow_\n"


def test_mood_resolves_the_outbox_from_the_portal_path(tmp_path, monkeypatch):
    """Same fallback every other control-file consumer already trusts."""
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.setenv("BRR_PORTAL_STATE", str(tmp_path / "portal-state.json"))

    assert cli.main(["mood", "flow_"]) == 0
    assert _mood_text(tmp_path) == "flow_\n"


def test_mood_is_hidden_but_still_parses():
    from brr.cli import HIDDEN_COMMANDS

    assert "mood" in HIDDEN_COMMANDS
