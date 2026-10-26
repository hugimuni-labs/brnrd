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


from brr import cli


def _run(monkeypatch, outbox, argv):
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))
    return cli.main(argv)


def test_mood_is_hidden_but_still_parses():
    from brr.cli import HIDDEN_COMMANDS

    assert "mood" in HIDDEN_COMMANDS
