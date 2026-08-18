"""Tests for CLI dispatch."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types

import pytest

from brr.cli import main

from _helpers import init_git_repo


REPO = Path(__file__).parents[1]


def _write_review_pack(path):
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1-test",
                "metadata": {"pr": {"title": "Review pack title"}},
                "reading_order": ["summary:x"],
                "cards": [
                    {
                        "id": "summary:x",
                        "kind": "summary",
                        "identity": {"label": "the change in shape"},
                        "lore": {"descriptive": "a small honest change"},
                        "provenance": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


@pytest.mark.parametrize("command", ["status", "inspect", "streams", "stream", "eject"])
def test_removed_diagnostic_commands_are_not_public(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


def test_docs_lists_topics(capsys):
    # `brnrd docs` (no topic) lists the bundled topics. Re-introduced as the
    # inspect surface for the portals manual (G5) — the docs module and
    # decision-bundled-docs.md always assumed this command existed.
    assert main(["docs"]) == 0
    out = capsys.readouterr().out
    assert "portals" in out
    assert "execution-map" in out


def test_docs_prints_topic(capsys):
    assert main(["docs", "portals"]) == 0
    out = capsys.readouterr().out
    assert "control-file" in out.lower() or "portal" in out.lower()


def test_docs_unknown_topic_errors(capsys):
    assert main(["docs", "does-not-exist"]) == 1


def test_legend_lists_every_bar_segment_key(capsys):
    """One source of truth: every `BAR_SEGMENTS` key must show up in the
    legend's output, or the command has drifted from what the bar actually
    renders."""
    from brr import hooks

    assert main(["legend"]) == 0
    out = capsys.readouterr().out
    for segment in hooks.BAR_SEGMENTS:
        assert segment.key in out, segment.key
    # The hand-declared row this vocabulary cannot carry itself.
    assert "pending_unknown" in out


def test_legend_is_hidden_but_still_parses():
    from brr.cli import HIDDEN_COMMANDS

    assert "legend" in HIDDEN_COMMANDS


# ── brnrd relic issue (#686) ─────────────────────────────────────────────────
#
# The front door onto `.relics.jsonl`. Issue produce is the one relic kind
# the daemon cannot derive — `gh issue close` happens in the resident's
# shell — so the record exists only if the resident writes it, and until now
# that meant remembering a JSON shape.


def _relic_env(monkeypatch, outbox):
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))


def _relic_lines(outbox):
    return [
        json.loads(line)
        for line in (outbox / ".relics.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_relic_issue_writes_the_grammar_record(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "issue", "686", "--closed"]) == 0
    assert _relic_lines(outbox) == [
        {"action": "closed", "kind": "issue", "number": 686},
    ]
    assert "#686 closed" in capsys.readouterr().out


def test_relic_issue_accepts_a_hash_prefix_and_a_foreign_repo(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(
        ["relic", "issue", "#317", "--opened", "--repo", "hugimuni-labs/brnrd"],
    ) == 0
    assert _relic_lines(outbox) == [
        {
            "action": "opened", "kind": "issue", "number": 317,
            "repo": "hugimuni-labs/brnrd",
        },
    ]


def test_relic_issue_appends_and_never_rewrites(tmp_path, monkeypatch):
    """The control file is a JSONL the resident may already have written into
    by hand — a second record must land beside the first, not replace it."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / ".relics.jsonl").write_text(
        '{"kind": "summary", "text": "hand-written"}\n', encoding="utf-8",
    )
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "issue", "686", "--opened"]) == 0
    assert main(["relic", "issue", "687", "--closed"]) == 0
    kinds = [(r["kind"], r.get("number")) for r in _relic_lines(outbox)]
    assert kinds == [("summary", None), ("issue", 686), ("issue", 687)]


def test_relic_issue_refuses_a_non_number_with_the_shape_it_wanted(
    tmp_path, monkeypatch, capsys,
):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    for bad in ["abc", "0", "-4", "12.5", ""]:
        assert main(["relic", "issue", bad, "--closed"]) == 1
    err = capsys.readouterr().err
    assert "positive integer" in err
    assert "nothing was written" in err.lower()
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_issue_requires_an_action_flag(tmp_path, monkeypatch, capsys):
    """The judgement call (#686): no default. Defaulting the bare form to
    `opened` would manufacture the very asymmetry the issue is about, and
    defaulting to no action would write a record that counts in neither
    bucket — a front door onto the room the resident already stood in."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "issue", "686"]) == 1
    err = capsys.readouterr().err
    assert "--opened" in err and "--closed" in err
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_issue_refuses_both_action_flags_at_once(tmp_path, monkeypatch):
    """One action per invocation."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    with pytest.raises(SystemExit) as exc:
        main(["relic", "issue", "686", "--opened", "--closed"])
    assert exc.value.code == 2
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_issue_refuses_a_malformed_repo(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "issue", "686", "--closed", "--repo", "brnrd"]) == 1
    assert "owner/name" in capsys.readouterr().err
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_issue_outside_a_run_says_why(monkeypatch, capsys):
    """No outbox in the environment ⇒ a reason, not a traceback."""
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert main(["relic", "issue", "686", "--closed"]) == 1
    err = capsys.readouterr().err
    assert "no run outbox" in err
    assert "BRR_OUTBOX_DIR" in err


def test_relic_issue_resolves_the_outbox_from_the_portal_path(tmp_path, monkeypatch):
    """One resolution path, shared with every other control-file consumer:
    `hooks.HookContext` falls back to the portal file's parent directory."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.setenv("BRR_PORTAL_STATE", str(outbox / "portal-state.json"))

    assert main(["relic", "issue", "686", "--closed"]) == 0
    assert _relic_lines(outbox) == [
        {"action": "closed", "kind": "issue", "number": 686},
    ]


# ── brnrd relic pr ───────────────────────────────────────────────────────────
#
# The second-PR front door: `.pr` holds exactly one PR per run, so a run
# that opens more than one had no legal way to self-report the rest onto
# the same `{"kind": "pr", "number": N}` grammar `collect()` parses.


def test_relic_pr_writes_the_grammar_record(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "pr", "1175", "--summary", "fixed the thing"]) == 0
    assert _relic_lines(outbox) == [
        {"kind": "pr", "number": 1175, "summary": "fixed the thing"},
    ]
    assert "pr #1175" in capsys.readouterr().out


def test_relic_pr_accepts_a_hash_prefix_and_a_url(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "pr", "#42"]) == 0
    assert main(["relic", "pr", "https://github.com/o/r/pull/43"]) == 0
    numbers = [r["number"] for r in _relic_lines(outbox)]
    assert numbers == [42, 43]


def test_relic_pr_keeps_the_repo_a_url_names(tmp_path, monkeypatch, capsys):
    """#1461: a full PR URL names its own ``owner/repo`` — kept, not reduced
    to a bare number the way ``forges.parse_pull_request_number`` used to
    throw it away."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "pr", "https://github.com/hugimuni-labs/brnrd/pull/1461"]) == 0
    assert _relic_lines(outbox) == [
        {"kind": "pr", "number": 1461, "repo": "hugimuni-labs/brnrd"},
    ]
    assert "pr #1461 in hugimuni-labs/brnrd" in capsys.readouterr().out


def test_relic_pr_explicit_repo_wins_over_the_url(tmp_path, monkeypatch):
    """An explicit ``--repo`` is a declaration; it outranks a URL's own
    reading, the same "declaration outranks inference" order
    ``_ForgeLinks._thread_repo`` already uses for every other relic kind."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main([
        "relic", "pr", "https://github.com/hugimuni-labs/brnrd/pull/1461",
        "--repo", "hugimuni-labs/fork",
    ]) == 0
    assert _relic_lines(outbox) == [
        {"kind": "pr", "number": 1461, "repo": "hugimuni-labs/fork"},
    ]


def test_relic_pr_bare_number_stays_repo_less(tmp_path, monkeypatch):
    """A bare number or ``#N`` names no repo — it means this checkout's own
    origin, same as always, and carries no ``repo`` key at all."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "pr", "#42"]) == 0
    assert _relic_lines(outbox) == [{"kind": "pr", "number": 42}]


def test_relic_pr_refuses_a_malformed_repo(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "pr", "1175", "--repo", "brnrd"]) == 1
    assert "owner/name" in capsys.readouterr().err
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_pr_refuses_unparseable_input(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "pr", "not-a-pr"]) == 1
    err = capsys.readouterr().err
    assert "not a PR number or URL" in err
    assert "nothing was written" in err.lower()
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_pr_outside_a_run_says_why(monkeypatch, capsys):
    """No outbox in the environment ⇒ a reason, not a traceback."""
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert main(["relic", "pr", "42"]) == 1
    err = capsys.readouterr().err
    assert "no run outbox" in err
    assert "BRR_OUTBOX_DIR" in err


def test_relic_pr_reports_a_failed_append(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    from brr import relics as relics_mod

    monkeypatch.setattr(relics_mod, "append", lambda *a, **k: None)
    assert main(["relic", "pr", "1175"]) == 1
    assert "could not append" in capsys.readouterr().err


# ── brnrd relic item (#972, THE WELD) ────────────────────────────────────────
#
# The warp-item half of the manifest: the run's ancestry address
# (`<layer>#<slug>`), written by the daemon at ignition or by a resident that
# only learned mid-run which item it serves.


def test_relic_item_writes_the_address_record(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "item", "w-42"]) == 0
    assert _relic_lines(outbox) == [
        {"address": "w-42", "kind": "item"},
    ]
    assert "w-42" in capsys.readouterr().out


def test_relic_item_refuses_a_malformed_address(tmp_path, monkeypatch, capsys):
    """Grammar-invalid means never written: a bad address in the manifest is
    an unresolvable claim every downstream renderer would carry forever."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    for bad in [
        "", "#slug", "the-loom#", "The-Loom", "under_score",
        "owner/repo#42", "two words",
    ]:
        assert main(["relic", "item", bad]) == 1
    err = capsys.readouterr().err
    assert "item id" in err
    assert "nothing was written" in err.lower()
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_item_outside_a_run_says_why(monkeypatch, capsys):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert main(["relic", "item", "w-42"]) == 1
    err = capsys.readouterr().err
    assert "no run outbox" in err


def test_relic_issue_reports_a_failed_append(tmp_path, monkeypatch, capsys):
    """`relics.append` is best-effort by design — right at closeout, wrong at
    a prompt, where a silent drop is a resident who believes the close is
    recorded."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    from brr import relics as relics_mod

    monkeypatch.setattr(relics_mod, "append", lambda *a, **k: None)
    assert main(["relic", "issue", "686", "--closed"]) == 1
    assert "could not append" in capsys.readouterr().err


def test_relic_issue_feeds_the_produce_split(tmp_path, monkeypatch):
    """End to end: what the command writes is what the display block counts."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    from brr import relics as relics_mod

    assert main(["relic", "issue", "686", "--opened"]) == 0
    assert main(["relic", "issue", "317", "--closed"]) == 0
    actions = relics_mod.issue_actions(relics_mod.read_reported(outbox))
    assert relics_mod.issues_phrase(actions) == "1 created · 1 completed"


# ── brnrd relic comment / message / file (#1060) ────────────────────────────
#
# `_PROMISABLE` names nine kinds; `relic` used to expose two front doors
# (`issue`, `item`). These three close the gap for the hand-attested kinds
# that had no subcommand at all — same shape as `issue`/`item`: one
# positional for the grammar's own field, the same outside-a-run and
# failed-append refusals.


def test_relic_comment_writes_the_grammar_record(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "comment", "issue #903 — stale-open sweep"]) == 0
    assert _relic_lines(outbox) == [
        {"kind": "comment", "on": "issue #903 — stale-open sweep"},
    ]
    assert "issue #903 — stale-open sweep" in capsys.readouterr().out


def test_relic_comment_refuses_a_blank_on(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "comment", "  "]) == 1
    err = capsys.readouterr().err
    assert "say what the comment was on" in err
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_comment_outside_a_run_says_why(monkeypatch, capsys):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert main(["relic", "comment", "issue #5"]) == 1
    assert "no run outbox" in capsys.readouterr().err


def test_relic_comment_reports_a_failed_append(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    from brr import relics as relics_mod

    monkeypatch.setattr(relics_mod, "append", lambda *a, **k: None)
    assert main(["relic", "comment", "issue #5"]) == 1
    assert "could not append" in capsys.readouterr().err


def test_relic_message_writes_the_grammar_record(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(
        ["relic", "message", "design fork answered", "--channel", "telegram"],
    ) == 0
    assert _relic_lines(outbox) == [
        {"kind": "message", "note": "design fork answered", "channel": "telegram"},
    ]
    assert "design fork answered" in capsys.readouterr().out


def test_relic_message_without_a_channel_omits_the_field(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "message", "pinged the maintainer"]) == 0
    assert _relic_lines(outbox) == [
        {"kind": "message", "note": "pinged the maintainer"},
    ]


def test_relic_message_refuses_a_blank_note(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "message", " "]) == 1
    err = capsys.readouterr().err
    assert "say what the message was" in err
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_message_outside_a_run_says_why(monkeypatch, capsys):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert main(["relic", "message", "note"]) == 1
    assert "no run outbox" in capsys.readouterr().err


def test_relic_file_writes_the_grammar_record(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "file", "/tmp/report.md"]) == 0
    assert _relic_lines(outbox) == [
        {"kind": "file", "path": "/tmp/report.md"},
    ]
    assert "/tmp/report.md" in capsys.readouterr().out


def test_relic_file_refuses_a_blank_path(tmp_path, monkeypatch, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    assert main(["relic", "file", " "]) == 1
    err = capsys.readouterr().err
    assert "say which file" in err
    assert not (outbox / ".relics.jsonl").exists()


def test_relic_file_outside_a_run_says_why(monkeypatch, capsys):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

    assert main(["relic", "file", "/tmp/x"]) == 1
    assert "no run outbox" in capsys.readouterr().err


def test_portal_state_prints_text_view(tmp_path, capsys):
    state = tmp_path / "portal-state.json"
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "change_token": "abc123",
                "run": {
                    "id": "run-1",
                    "event_id": "evt-1",
                    "phase": "running",
                    "attempt": 1,
                },
                "attention": {
                    "pending_event_count": 1,
                    "pending_outbox_file_count": 0,
                },
                "inbound": {
                    "events": [
                        {
                            "id": "evt-2",
                            "source": "telegram",
                            "summary": "quick follow-up",
                        }
                    ]
                },
                "outbound": {
                    "replies_current": 1,
                    "replies_other": 0,
                    "outbound_messages": 0,
                    "pending_outbox_files": [],
                },
                "budget": {
                    "elapsed_seconds": 65,
                    "budget_seconds": 3600,
                    "keepalive": {"status": "absent"},
                },
                "card": {"text": "working"},
            }
        ),
        encoding="utf-8",
    )

    assert main(["portal", "state", "--path", str(state)]) == 0

    out = capsys.readouterr().out
    assert "run=run-1" in out
    assert "token=abc123" in out
    assert "evt-2 telegram: quick follow-up" in out
    assert "card: working" in out


def test_portal_state_prints_json_from_env(tmp_path, capsys, monkeypatch):
    state = tmp_path / "portal-state.json"
    state.write_text('{"version": 1, "run": {"id": "run-env"}}\n', encoding="utf-8")
    monkeypatch.setenv("BRR_PORTAL_STATE", str(state))

    assert main(["portal", "state", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["id"] == "run-env"


def test_portal_facets_schema_only_without_run(capsys, monkeypatch):
    # Outside a wake the catalogue still prints — the schema is in code, not in
    # a run — so an operator can always ask "what are the implemented facets?".
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    assert main(["portal", "facets"]) == 0
    out = capsys.readouterr().out
    assert "boundary facet catalogue" in out
    assert "quota [level, required]" in out
    assert "coexisting-runs [state, optional]" in out
    assert "no live run detected" in out


def test_portal_facets_with_live_status(tmp_path, capsys, monkeypatch):
    from brr import facets

    res = facets.build(quota_summary="weekly 42%", branch="brr/x")
    state = tmp_path / "portal-state.json"
    state.write_text(
        json.dumps({"version": 1, "resources": res}) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("BRR_PORTAL_STATE", str(state))
    assert main(["portal", "facets"]) == 0
    out = capsys.readouterr().out
    assert "with live status" in out
    assert "quota [level, required] — known: weekly 42%" in out


def test_portal_facets_json(capsys, monkeypatch):
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    assert main(["portal", "facets", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["key"] for r in rows} == {
        "quota", "spend", "context_window", "coexisting_runs", "remote_scm"
    }


def test_format_portal_state_surfaces_missing_data():
    from brr.cli import _format_portal_state

    out = _format_portal_state({
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "outbound": {"replies_current": 0, "replies_other": 0,
                     "outbound_messages": 0, "any_sent": False},
        "budget": {"elapsed_seconds": 4000, "budget_seconds": 3600,
                   "long_running": True, "keepalive": {"status": "-"}},
        "resources": {
            "quota": {"status": "absent", "note": "no snapshot for this medium"},
            "spend": {"status": "unimplemented", "note": "not metered yet"},
            "context_window": {"status": "unimplemented",
                               "note": "not exposed by this medium"},
            "coexisting_runs": {"status": "unimplemented"},
            "remote_scm": {"status": "absent",
                           "note": "no PR recorded for this branch yet"},
        },
    })
    assert "nothing sent yet" in out
    assert "running long" in out
    assert "spend=unimplemented (not metered yet)" in out
    assert "remote-scm=absent (no PR recorded for this branch yet)" in out
    assert "unavailable" not in out


def test_portal_state_errors_without_file(capsys, monkeypatch):
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: None)

    assert main(["portal", "state"]) == 1
    assert "no live portal-state.json" in capsys.readouterr().err


# ── brnrd await (#959, collapsed by #1187) ────────────────────────────


class _FakeClock:
    """A monotonic clock that only moves when something sleeps.

    ``cmd_await``'s slice ceiling and ``do.await_verdict``'s staging wait are
    both real wall-clock loops; driving them from a fake clock keeps the
    tests instant while still exercising the true loop bodies.
    """

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.slept = []
        self._on_sleep = on_sleep

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.slept.append(seconds)
        if self._on_sleep is not None:
            self._on_sleep()


def _await_outbox(tmp_path, *, budget=None, await_state=None, notices=None):
    outbox = tmp_path / "outbox"
    outbox.mkdir(exist_ok=True)
    payload = {"version": 1}
    if budget is not None:
        payload["budget"] = budget
    if await_state is not None:
        payload["await"] = await_state
    if notices is not None:
        payload["notices"] = notices
    (outbox / "portal-state.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    return outbox


def _staged_await(outbox):
    return [p for p in outbox.glob("do-*-await-*.md")]


def test_await_takes_no_positional_arguments():
    """The point of the collapse: nothing to supply means nothing to typo.

    A resident reaching for the old ``spawn:<id>`` grammar gets a parse
    error, not a wait that silently filters on a mistyped id (#1187).
    """
    with pytest.raises(SystemExit):
        main(["await", "spawn:evt-abcd"])


def test_await_errors_without_an_outbox(capsys, monkeypatch):
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: None)

    assert main(["await"]) == 1
    assert "no run outbox" in capsys.readouterr().err


def test_await_errors_without_live_portal_state(tmp_path, capsys):
    outbox = tmp_path / "outbox"
    outbox.mkdir()

    assert main(["await", "--outbox", str(outbox)]) == 1
    assert "no live portal-state.json" in capsys.readouterr().err


def test_await_explicit_bad_outbox_names_the_path(tmp_path, capsys):
    """#1337: an explicit ``--outbox`` that resolves to no directory is a
    caller mistake and must say so, distinct from the legitimate "no live
    portal-state.json" read for an absent env-derived run above.
    """
    bad = tmp_path / "does-not-exist"

    assert main(["await", "--outbox", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "no such directory" in err
    assert "no live portal-state.json" not in err


def test_await_rejects_an_unparseable_timeout(tmp_path, capsys):
    outbox = _await_outbox(tmp_path)

    assert main(["await", "--outbox", str(outbox), "--timeout", "banana"]) == 1
    assert "not a positive duration" in capsys.readouterr().err
    assert _staged_await(outbox) == [], "nothing may be staged on a bad ceiling"


def test_await_defaults_the_ceiling_from_the_runs_remaining_budget(
    tmp_path, capsys, monkeypatch,
):
    """The daemon already knows how long this run has left (#1187's lesson).

    Asking the resident to restate it is the same mistake ``spawn:<id>`` was,
    so an argument-free call arms ``budget_seconds - elapsed_seconds``.
    """
    outbox = _await_outbox(
        tmp_path, budget={"budget_seconds": 600, "elapsed_seconds": 60},
    )
    staged_text = {}

    def drain():
        for path in _staged_await(outbox):
            staged_text["body"] = path.read_text(encoding="utf-8")
            path.unlink()

    clock = _FakeClock(on_sleep=drain)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)

    assert main(["await", "--outbox", str(outbox), "--json"]) == 0
    assert "timeout: 540s" in staged_text["body"]
    assert "await: true" in staged_text["body"]
    assert "file:" not in staged_text["body"]


def _staged_timeout_seconds(body):
    import re

    match = re.search(r"^timeout:\s*(\d+)s", body, re.MULTILINE)
    assert match, f"no timeout in staged directive: {body!r}"
    return int(match.group(1))


def _drive_await(outbox, monkeypatch, argv=("--json",)):
    staged_text = {}

    def drain():
        for path in _staged_await(outbox):
            staged_text["body"] = path.read_text(encoding="utf-8")
            path.unlink()

    clock = _FakeClock(on_sleep=drain)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    assert main(["await", "--outbox", str(outbox), *argv]) == 0
    return staged_text["body"]


def _in(seconds):
    from datetime import datetime, timedelta, timezone

    stamp = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return stamp.isoformat().replace("+00:00", "Z")


def test_await_recall_continues_the_standing_deadline(tmp_path, monkeypatch):
    """``pending`` is *this call's* ceiling, never the wait's — so calling
    again continues the same vigil rather than starting a longer one.

    Before this, a bare re-call re-derived the ceiling from the run's
    remaining budget every time, so a deliberate short hold grew on each
    pass with nothing on any surface saying so: three re-calls turned a
    12-minute vigil into hours. The deadline is already in
    ``portal-state.json`` — the verb's own rule (#1187) is that the caller
    never restates what the daemon already tracks.

    Neuter check (by hand, don't ship it): make ``cmd_await`` call
    ``_await_default_timeout`` unconditionally and this goes red at 6300s.
    """
    outbox = _await_outbox(
        tmp_path,
        budget={"budget_seconds": 7200, "elapsed_seconds": 900},
        await_state={
            "armed": True, "generation": "111", "resolved": False,
            "deadline": _in(300), "capped": False,
        },
    )

    staged = _staged_timeout_seconds(_drive_await(outbox, monkeypatch))
    assert 280 <= staged <= 300, staged
    assert staged < 6300, "the re-call re-armed at the budget default"


def test_await_explicit_timeout_still_beats_a_standing_deadline(
    tmp_path, monkeypatch,
):
    """Continuing and re-arming are different acts, and ``--timeout`` is how
    a caller says the second one."""
    outbox = _await_outbox(
        tmp_path,
        budget={"budget_seconds": 7200, "elapsed_seconds": 900},
        await_state={
            "armed": True, "generation": "111", "resolved": False,
            "deadline": _in(300), "capped": False,
        },
    )

    body = _drive_await(outbox, monkeypatch, argv=("--json", "--timeout", "20m"))
    assert _staged_timeout_seconds(body) == 1200


def test_await_falls_back_to_budget_when_no_vigil_is_standing(
    tmp_path, monkeypatch,
):
    """A *resolved* arming is a finished wait, not a standing one — the next
    call is a fresh vigil and takes the budget-derived default again. Same
    for an expired deadline: there is nothing left to continue."""
    for index, state in enumerate((
        {"armed": True, "generation": "111", "resolved": True,
         "outcome": "timeout", "deadline": _in(300)},
        {"armed": True, "generation": "111", "resolved": False,
         "deadline": _in(-30)},
        {"armed": False},
    )):
        case_dir = tmp_path / f"case-{index}"
        case_dir.mkdir()
        outbox = _await_outbox(
            case_dir,
            budget={"budget_seconds": 600, "elapsed_seconds": 60},
            await_state=state,
        )
        body = _drive_await(outbox, monkeypatch)
        assert _staged_timeout_seconds(body) == 540, state


def test_await_file_flag_rides_along_as_an_extra_trigger(
    tmp_path, capsys, monkeypatch,
):
    """``--file`` composes; it never becomes the wait's only condition.

    The staged directive still says nothing about *which* events count —
    the daemon's own "anything pending resolves this" semantics are
    structural, so the file can only ever add a trigger.
    """
    outbox = _await_outbox(tmp_path)
    staged_text = {}

    def drain():
        for path in _staged_await(outbox):
            staged_text["body"] = path.read_text(encoding="utf-8")
            path.unlink()

    clock = _FakeClock(on_sleep=drain)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)

    assert main([
        "await", "--outbox", str(outbox), "--file", "/tmp/gate.log", "--json",
    ]) == 0
    assert "file: /tmp/gate.log" in staged_text["body"]


def test_await_reports_the_daemons_resolution(tmp_path, capsys, monkeypatch):
    outbox = _await_outbox(tmp_path, await_state={"armed": False})
    state = outbox / "portal-state.json"

    def drain():
        if not _staged_await(outbox):
            return
        for path in _staged_await(outbox):
            path.unlink()
        state.write_text(
            json.dumps({
                "version": 1,
                "await": {
                    "armed": True, "generation": "222", "resolved": True,
                    "outcome": "event", "which": None,
                    "deadline": "2026-08-07T12:00:00Z", "capped": False,
                },
            }),
            encoding="utf-8",
        )

    clock = _FakeClock(on_sleep=drain)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)

    assert main(["await", "--outbox", str(outbox), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "event"
    assert payload["deadline"] == "2026-08-07T12:00:00Z"


def test_await_reports_its_own_arming_verdict(tmp_path, capsys, monkeypatch):
    """#1187, killed by construction: a directive that fails to arm says so
    *in the call that armed it*, instead of leaving the previous wait's
    ``resolved: true`` in place looking like an answer."""
    outbox = _await_outbox(tmp_path, notices=[])
    state = outbox / "portal-state.json"

    def drain():
        if not _staged_await(outbox):
            return
        for path in _staged_await(outbox):
            path.unlink()
        state.write_text(
            json.dumps({
                "version": 1,
                "notices": [{
                    "at": "2026-08-07T11:20:00Z", "kind": "dropped",
                    "text": "await dropped: timeout: must be positive",
                }],
            }),
            encoding="utf-8",
        )

    clock = _FakeClock(on_sleep=drain)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)

    assert main(["await", "--outbox", str(outbox), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "failed"
    assert "await dropped" in payload["detail"]


def test_await_never_reports_a_previous_calls_resolution(
    tmp_path, capsys, monkeypatch,
):
    """The stale-answer guard. ``brnrd await`` re-arms on every call, and a
    resolved outcome is sticky in ``portal-state.json`` until the daemon's
    next heartbeat rewrites it — so a portal-state file written *before* this
    call's arming still carries the previous wait's answer.

    Neuter check (do this by hand, don't ship it): drop the ``fresh``
    generation comparison in ``cli.cmd_await``'s poll loop and rerun — this
    test goes red with ``outcome == "timeout"``, i.e. the command answering
    a wait it never actually waited on.
    """
    outbox = _await_outbox(tmp_path, await_state={
        "armed": True, "generation": "111", "resolved": True,
        "outcome": "timeout", "which": None, "capped": False,
    })

    def drain():
        for path in _staged_await(outbox):
            path.unlink()

    clock = _FakeClock(on_sleep=drain)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)

    assert main(["await", "--outbox", str(outbox), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "pending"


def test_await_slice_returns_pending_at_its_own_ceiling(
    tmp_path, capsys, monkeypatch,
):
    """The call ends by answering, never by being killed mid-wait: it returns
    ``pending`` at its own ceiling, which sits under the tightest Shell
    per-tool-call cap. "Call again" is then the whole instruction."""
    from brr import cli

    outbox = _await_outbox(tmp_path)

    def drain():
        for path in _staged_await(outbox):
            path.unlink()

    clock = _FakeClock(on_sleep=drain)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)

    assert main(["await", "--outbox", str(outbox), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "pending"
    assert clock.now >= cli._AWAIT_SLICE_CEILING_SECONDS


def test_run_requires_instruction():
    with pytest.raises(SystemExit):
        main(["run"])


def test_bind_accepts_repo_and_gate(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    calls = []

    class Gate:
        @staticmethod
        def bind(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: Gate)

    assert main(["gate", "bind", str(repo), "telegram"]) is None

    out = capsys.readouterr().out
    assert calls == [repo / ".brr"]
    assert "project home" in out
    assert "telegram" in out


def test_add_registers_repo_in_connected_account_home(monkeypatch, tmp_path, capsys):
    current = tmp_path / "current"
    target = tmp_path / "target"
    init_git_repo(current)
    init_git_repo(target)
    cloud_dir = current / ".brr" / "gates"
    cloud_dir.mkdir(parents=True)
    (cloud_dir / "cloud.json").write_text(
        json.dumps({
            "brnrd_url": "https://brnrd.example",
            "token": "tok",
            "account_id": "acct-1",
            "repo_id": "repo-1",
        }),
        encoding="utf-8",
    )
    monkeypatch.chdir(current)

    assert main(["account", "add", str(target)]) is None

    out = capsys.readouterr().out
    assert "added target" in out
    # tests/conftest isolates XDG_STATE_HOME at a generated temp path, so read
    # the resolver instead of guessing its parent from this test's tmp_path.
    from brr import account, config as conf

    ctx = account.resolve_context(current, conf.load_config(current), create=False)
    registry = json.loads((ctx.dominion_repo / "account" / "repos.json").read_text())
    assert {item["label"] for item in registry["repos"]} == {
        "home", "current", "target",
    }
    assert registry["home_kind"] == "account"
    assert registry["account_id"] == "acct-1"


def test_account_disconnect_removes_gate_state_but_keeps_the_home(
    monkeypatch, tmp_path, capsys
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    from brr import account
    from brr.gates import cloud, runtime

    ctx = account.resolve_context(
        repo,
        {
            "home.kind": "account",
            "account.id": "acct-1",
            "repo.label": "owner/repo",
        },
    )
    account_root = account.context_home_root(ctx) / "account"
    runtime.save_state(
        account_root,
        "cloud",
        {
            "brnrd_url": "https://brnrd.example",
            "token": "secret",
            "account_id": "acct-1",
            "repo_id": "repo-1",
        },
    )
    credential = account_root / "credentials" / "github" / "token"
    credential.parent.mkdir(parents=True)
    credential.write_text("derived-token\n", encoding="utf-8")

    assert main(["account", "disconnect"]) is None

    assert not runtime.state_path(account_root, "cloud").exists()
    assert not credential.parent.exists()
    assert (account_root / "repos.json").exists()
    assert not cloud.is_configured(repo / ".brr")
    assert "Disconnected this daemon" in capsys.readouterr().out


def test_account_connect_refused_connection_exits_with_recovery_not_traceback(
    tmp_path,
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    env = {
        **os.environ,
        "BRNRD_URL": "http://127.0.0.1:9",
        "PYTHONPATH": str(REPO / "src"),
    }

    result = subprocess.run(
        [sys.executable, "-m", "brr", "account", "connect"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "account service at http://127.0.0.1:9 is unreachable" in output
    assert "Check the URL and network connection" in output
    assert "re-run `brnrd account connect`" in output
    assert "Traceback" not in output


def test_account_connect_pairing_timeout_exits_with_approval_recovery(
    tmp_path, monkeypatch,
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    from brr.gates import cloud

    replies = iter([
        {"pair_code": "BR-TEST", "pair_url": "https://pair", "poll_secret": "secret"},
        {"status": "pending"},
    ])
    ticks = iter([0.0, 601.0])
    monkeypatch.setattr(cloud, "_request", lambda *_args, **_kwargs: next(replies))
    monkeypatch.setattr(cloud.time, "monotonic", lambda: next(ticks))

    with pytest.raises(SystemExit) as excinfo:
        main(["account", "connect", "https://brnrd.example"])

    assert excinfo.value.code != 0
    message = str(excinfo.value)
    assert "pairing was not approved" in message
    assert "Approve the pairing link" in message
    assert "re-run `brnrd account connect`" in message


def test_account_connect_pairs_installs_and_starts_service(
    monkeypatch, tmp_path, capsys,
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    # The initialized-repo shape; the no-AGENTS.md path installs identically
    # since #1244 fork 1 — see test_account_connect_installs_without_agents_md.
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    calls = []

    monkeypatch.setattr(
        "brr.gates.cloud.connect",
        lambda brr_dir, **kwargs: calls.append(("connect", brr_dir, kwargs)),
    )
    monkeypatch.setattr(
        "brr.daemon_install.install",
        lambda **kwargs: calls.append(("install", kwargs)) or 0,
    )

    assert main([
        "account",
        "connect",
        "https://brnrd.example",
        "--daemon-name",
        "wyrd-box",
        "--yes-linger",
    ]) is None

    assert calls == [
        (
            "connect",
            repo / ".brr",
            {
                "brnrd_url": "https://brnrd.example",
                "daemon_name": "wyrd-box",
            },
        ),
        (
            "install",
            {
                "no_start": False,
                "prompt_linger": True,
                "assume_yes_linger": True,
            },
        ),
    ]
    assert "Connected and listening in the background" in capsys.readouterr().out


def test_account_connect_reports_when_the_service_does_not_come_up(
    monkeypatch, tmp_path, capsys,
):
    """A liveness-probe failure from `daemon_install.install()` (#1238) must
    not be papered over with the same claim a healthy install prints."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: 1)

    assert main(["account", "connect", "https://brnrd.example"]) is None

    out = capsys.readouterr().out
    assert "Connected and listening in the background" not in out
    assert "did not come up" in out


def test_account_connect_installs_without_agents_md(
    monkeypatch, tmp_path, capsys,
):
    """#1244 fork 1: `connect` before `init` now installs normally.

    This used to skip the service install entirely (#1238's preflight):
    `daemon.start` immediately hard-exited with no `AGENTS.md`, so
    installing anyway handed the service manager a job whose first line
    was that exit — a crash loop with nothing proving it. `daemon.start`
    no longer exits for that reason (it prints and boots), so the skip's
    whole premise is gone and install proceeds exactly as it would for an
    initialized repo — see `test_account_connect_pairs_installs_and_starts_service`,
    the same shape, with no `AGENTS.md` written."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    assert not (repo / "AGENTS.md").exists()
    monkeypatch.chdir(repo)
    installed = []

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr(
        "brr.daemon_install.install",
        lambda **kwargs: installed.append(kwargs) or 0,
    )

    assert main(["account", "connect", "https://brnrd.example"]) is None

    assert len(installed) == 1
    out = capsys.readouterr().out
    assert "Connected and listening in the background" in out


def test_account_connect_no_service_keeps_foreground_escape(
    monkeypatch, tmp_path, capsys,
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    installed = []

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "brr.daemon_install.install",
        lambda **kwargs: installed.append(kwargs),
    )

    assert main(["account", "connect", "--no-service"]) is None

    assert installed == []
    assert "brnrd up --foreground" in capsys.readouterr().out


def test_account_connect_queues_greeting_when_a_door_is_configured(
    monkeypatch, tmp_path, capsys,
):
    """#1244 fork 2: connect ends by queueing the first-wake greeting when
    `AGENTS.md` is missing and a door that can actually carry it (here,
    telegram, bound before this connect ran) is configured."""
    from brr import protocol
    from brr.gates import runtime as gate_runtime

    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    gate_runtime.save_state(repo / ".brr", "telegram", {"token": "t", "chat_id": 777})

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: 0)

    assert main(["account", "connect", "https://brnrd.example"]) is None

    out = capsys.readouterr().out
    assert "queued the setup interview" in out
    assert "telegram" in out
    pending = protocol.list_pending(repo / ".brr" / "inbox")
    assert len(pending) == 1
    assert pending[0]["source"] == "telegram"


def test_account_connect_no_door_names_the_fallback(monkeypatch, tmp_path, capsys):
    """No chat gate configured (only the just-completed cloud pairing, which
    can't originate a message) ⇒ no greeting queued, and the reply names
    both remaining paths (`brnrd init` at a terminal, `--defaults`)."""
    from brr import protocol

    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: 0)

    assert main(["account", "connect", "https://brnrd.example"]) is None

    out = capsys.readouterr().out
    assert "no interview queued" in out
    assert "--defaults" in out
    assert "brnrd init" in out
    assert protocol.list_pending(repo / ".brr" / "inbox") == []


def test_account_connect_defaults_flag_writes_init_defaults_not_the_interview(
    monkeypatch, tmp_path, capsys,
):
    """The signed opt-out rider: `--defaults` skips the interview and writes
    today's `brnrd init` defaults directly — no greeting event queued."""
    from brr import protocol

    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: 0)
    calls = []
    monkeypatch.setattr(
        "brr.adopt.init_repo",
        lambda *a, **kw: calls.append((a, kw)),
    )

    assert main([
        "account", "connect", "https://brnrd.example", "--defaults",
    ]) is None

    assert calls == [((), {"defaults": True})]
    assert "writing brnrd init defaults" in capsys.readouterr().out
    assert protocol.list_pending(repo / ".brr" / "inbox") == []


def test_account_connect_skips_setup_entirely_once_agents_md_exists(
    monkeypatch, tmp_path, capsys,
):
    """An already-initialized repo gets neither the greeting nor the
    `--defaults` writer — connect is purely pairing there, unchanged."""
    from brr import protocol

    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: 0)
    monkeypatch.setattr(
        "brr.adopt.init_repo",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("init_repo must not run when AGENTS.md exists"),
        ),
    )

    assert main(["account", "connect", "https://brnrd.example"]) is None

    out = capsys.readouterr().out
    assert "queued the setup interview" not in out
    assert "no interview queued" not in out
    assert protocol.list_pending(repo / ".brr" / "inbox") == []


def test_account_connect_ctrl_c_during_pairing_exits_cleanly(
    monkeypatch, tmp_path,
):
    """#1244 fork 2, signed rider: a ^C mid-pairing must not abandon the
    terminal to a raw traceback — the daemon's default top-level handling
    of an uncaught KeyboardInterrupt does exactly that (confirmed: this
    test was red before the ``except KeyboardInterrupt`` clause landed,
    propagating a bare traceback out of ``main()`` instead of a clean
    ``SystemExit``)."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    def _raise(*_a, **_kw):
        raise KeyboardInterrupt

    monkeypatch.setattr("brr.gates.cloud.connect", _raise)

    with pytest.raises(SystemExit) as excinfo:
        main(["account", "connect", "https://brnrd.example"])

    message = str(excinfo.value)
    assert "interrupted" in message
    assert "pairing approval" in message
    assert "account connect" in message


def test_account_connect_ctrl_c_during_service_install_exits_cleanly(
    monkeypatch, tmp_path,
):
    """Same contract, the other interactive stretch: pairing already
    succeeded and was reported before the install's own linger prompt is
    ever reached, so only the (independently re-runnable) install is what
    the message names as cut off."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})

    def _raise(**_kw):
        raise KeyboardInterrupt

    monkeypatch.setattr("brr.daemon_install.install", _raise)

    with pytest.raises(SystemExit) as excinfo:
        main(["account", "connect", "https://brnrd.example"])

    message = str(excinfo.value)
    assert "interrupted" in message
    assert "service install" in message


def test_home_link_yes_asks_nothing(monkeypatch, tmp_path, capsys):
    """``--yes`` is the whole non-interactive contract: no confirm, no stdin read."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    from brr import home_link

    calls = []

    def fake_link_home(repo_root, cfg, **kwargs):
        calls.append(kwargs)
        results = [
            home_link.RepoLinkResult("dominion", repo, "https://x/d", "created", True),
            home_link.RepoLinkResult("knowledge", repo, "https://x/k", "created", True),
        ]
        on_result = kwargs.get("on_result")
        if on_result is not None:
            for result in results:
                on_result(result)
        return results

    monkeypatch.setattr(home_link, "link_home", fake_link_home)

    def _fail_confirm(*_a, **_kw):  # pragma: no cover
        raise AssertionError("--yes must not prompt")

    monkeypatch.setattr("brr.adopt._confirm", _fail_confirm)

    assert main(["home", "link", "--yes"]) is None

    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "dominion: created" in out
    assert "knowledge: created" in out


def test_home_link_without_yes_needs_a_tty(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(SystemExit) as exc:
        main(["home", "link"])
    assert "--yes" in str(exc.value)


def test_home_link_reports_actionable_error_with_no_traceback(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    from brr import home_link

    def boom(*a, **kw):
        raise home_link.HomeLinkError("gh is not authenticated — run `gh auth login` first")

    monkeypatch.setattr(home_link, "link_home", boom)

    with pytest.raises(SystemExit) as exc:
        main(["home", "link", "--yes"])
    assert "gh is not authenticated" in str(exc.value)


def _scaffold_project_home(tmp_path, name="repo"):
    """A just-scaffolded project home, matching account.py's own test helper."""
    from brr import account

    repo = tmp_path / name
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    (repo / ".brr" / "inbox").mkdir(parents=True)
    (repo / ".brr" / "responses").mkdir(parents=True)
    home = tmp_path / f"{name}-home"
    account.resolve_context(repo, {"home.path": str(home)})
    return home


def test_home_sweep_orphans_lists_without_deleting_by_default(monkeypatch, tmp_path, capsys):
    home = _scaffold_project_home(tmp_path)
    monkeypatch.setattr("brr.account.list_project_homes", lambda: [home])

    assert main(["home", "sweep-orphans"]) == 0

    out = capsys.readouterr().out
    assert str(home) in out
    assert "orphan" in out
    assert "dry run" in out
    assert home.is_dir()  # nothing deleted


def test_home_sweep_orphans_keeps_a_home_with_real_content(monkeypatch, tmp_path, capsys):
    home = _scaffold_project_home(tmp_path)
    (home / "runs" / "repo" / "run-x").mkdir(parents=True)
    (home / "runs" / "repo" / "run-x" / "state.md").write_text("# state\n", encoding="utf-8")
    monkeypatch.setattr("brr.account.list_project_homes", lambda: [home])

    assert main(["home", "sweep-orphans"]) == 0

    out = capsys.readouterr().out
    assert "keep" in out
    assert "0 default-scaffold" in out
    assert home.is_dir()


def test_home_sweep_orphans_delete_yes_removes_the_orphan(monkeypatch, tmp_path, capsys):
    home = _scaffold_project_home(tmp_path)
    monkeypatch.setattr("brr.account.list_project_homes", lambda: [home])

    def _fail_confirm(*_a, **_kw):  # pragma: no cover
        raise AssertionError("--yes must not prompt")

    monkeypatch.setattr("brr.adopt._confirm", _fail_confirm)

    assert main(["home", "sweep-orphans", "--delete", "--yes"]) == 0

    out = capsys.readouterr().out
    assert "deleted" in out
    assert not home.parent.exists()


def test_home_sweep_orphans_delete_without_yes_needs_a_tty(monkeypatch, tmp_path):
    home = _scaffold_project_home(tmp_path)
    monkeypatch.setattr("brr.account.list_project_homes", lambda: [home])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(SystemExit) as exc:
        main(["home", "sweep-orphans", "--delete"])
    assert "--yes" in str(exc.value)
    assert home.is_dir()  # refused, not deleted


def test_review_prints_pr_title_and_body(tmp_path, capsys):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)

    assert main(["review", str(pack), "--pr-title", "--fallback-title", "fallback"]) == 0
    assert "Review pack title" in capsys.readouterr().out

    assert main(["review", str(pack), "--pr-body", "--render-url", "https://r.example"]) == 0
    body = capsys.readouterr().out
    assert "## Summary" in body
    assert "https://r.example" in body
    assert "diffense:pack:v1" in body


def test_review_relay_prefers_gist_owned_pack(tmp_path, capsys, monkeypatch):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)

    from brr.diffense import gist

    monkeypatch.setattr(
        gist,
        "create_pack_gist",
        lambda _pack, **_kwargs: gist.GistPack(
            html_url="https://gist.github.com/octo/abc",
            raw_url="https://gist.githubusercontent.com/octo/abc/raw/sha/diffense-pack.json",
        ),
    )
    monkeypatch.setattr(gist, "renderer_shell_available", lambda _base_url: True)
    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)

    assert main(["review", str(pack), "--pr-body", "--relay"]) == 0

    body = capsys.readouterr().out
    assert "https://brnrd.dev/r?pack=" in body
    assert "Pack source: https://gist.github.com/octo/abc" in body


def test_review_relay_falls_back_to_transient_cloud_relay(
    tmp_path, capsys, monkeypatch,
):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)
    brr_dir = tmp_path / ".brr"

    from brr.diffense import gist
    import brr.gates as gates

    cloud = types.ModuleType("brr.gates.cloud")
    cloud.is_configured = lambda _brr_dir: True
    cloud.relay_pack = lambda _brr_dir, _pack: "https://brnrd.example/r/tok"
    monkeypatch.setattr(gist, "renderer_shell_available", lambda _base_url: True)
    monkeypatch.setattr(gist, "review_url_available", lambda _url: True)
    monkeypatch.setattr(gist, "create_pack_gist", lambda _pack, **_kwargs: None)
    monkeypatch.setattr("brr.cli._diffense_current_repo", lambda: None)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: brr_dir)
    monkeypatch.setitem(sys.modules, "brr.gates.cloud", cloud)
    monkeypatch.setattr(gates, "cloud", cloud, raising=False)

    assert main(["review", str(pack), "--pr-body", "--relay"]) == 0

    body = capsys.readouterr().out
    assert "https://brnrd.example/r/tok" in body
    assert "Transient link" in body


def test_review_relay_falls_back_when_renderer_shell_is_not_live(
    tmp_path, capsys, monkeypatch,
):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)
    brr_dir = tmp_path / ".brr"

    from brr.diffense import gist
    import brr.gates as gates

    cloud = types.ModuleType("brr.gates.cloud")
    cloud.is_configured = lambda _brr_dir: True
    cloud.relay_pack = lambda _brr_dir, _pack: "https://brnrd.example/r/tok"
    monkeypatch.setattr(gist, "renderer_shell_available", lambda _base_url: False)
    monkeypatch.setattr(gist, "review_url_available", lambda _url: True)

    def fail_create(*_args, **_kwargs):
        raise AssertionError("dead renderer links should not create gists")

    monkeypatch.setattr(gist, "create_pack_gist", fail_create)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: brr_dir)
    monkeypatch.setitem(sys.modules, "brr.gates.cloud", cloud)
    monkeypatch.setattr(gates, "cloud", cloud, raising=False)

    assert main(["review", str(pack), "--pr-body", "--relay"]) == 0

    body = capsys.readouterr().out
    assert "https://brnrd.example/r/tok" in body
    assert "Pack source" not in body
    assert "Transient link" in body


def test_review_relay_omits_link_when_transient_relay_render_fails(
    tmp_path, capsys, monkeypatch,
):
    pack = tmp_path / "pack.json"
    _write_review_pack(pack)
    brr_dir = tmp_path / ".brr"

    from brr.diffense import gist
    import brr.gates as gates

    cloud = types.ModuleType("brr.gates.cloud")
    cloud.is_configured = lambda _brr_dir: True
    cloud.relay_pack = lambda _brr_dir, _pack: "https://brnrd.example/r/tok"
    monkeypatch.setattr(gist, "renderer_shell_available", lambda _base_url: False)
    monkeypatch.setattr(gist, "review_url_available", lambda _url: False)
    monkeypatch.setattr(gist, "create_pack_gist", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: brr_dir)
    monkeypatch.setitem(sys.modules, "brr.gates.cloud", cloud)
    monkeypatch.setattr(gates, "cloud", cloud, raising=False)

    assert main(["review", str(pack), "--pr-body", "--relay"]) == 0

    body = capsys.readouterr().out
    assert "Interactive review" not in body
    assert "https://brnrd.example/r/tok" not in body
    assert "## Summary" in body


def test_up_dev_reload_flag_passes_to_daemon(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr("brr.cli._repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "brr.daemon.start",
        lambda repo_root, *, dev_reload=None: calls.append(
            (repo_root, dev_reload),
        ),
    )

    main(["up", "--dev-reload"])

    assert calls == [(tmp_path, True)]


def test_daemon_up_foreground_uses_existing_daemon_start(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr("brr.cli._repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "brr.daemon.start",
        lambda repo_root, *, dev_reload=None: calls.append(
            (repo_root, dev_reload),
        ),
    )

    main(["daemon", "up", "--foreground", "--dev-reload"])

    assert calls == [(tmp_path, True)]


def test_daemon_install_dispatches_to_native_installer(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "brr.daemon_install.install",
        lambda **kwargs: calls.append(kwargs),
    )

    main(["daemon", "install", "--no-start", "--no-linger"])

    assert calls == [
        {
            "no_start": True,
            "prompt_linger": False,
            "assume_yes_linger": False,
        },
    ]


def test_daemon_logs_dispatches_to_native_helper(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "brr.daemon_install.logs",
        lambda **kwargs: calls.append(kwargs) or 0,
    )

    assert main(["daemon", "logs", "-n", "25", "--no-follow"]) == 0
    assert calls == [{"follow": False, "lines": 25}]


def test_daemon_status_does_not_require_repo(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: tmp_path / ".brr")
    monkeypatch.setattr(
        "brr.daemon_install.status",
        lambda *, direct_brr_dir=None: calls.append(direct_brr_dir),
    )

    main(["daemon", "status"])

    assert calls == [tmp_path / ".brr"]


def test_agent_inject_prints_wake_context(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_inject(repo_root, *, task_text=None):
        seen["args"] = (repo_root, task_text)
        return "WAKE-CONTEXT-DIGEST"

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: tmp_path)
    monkeypatch.setattr("brr.prompts.build_injected_context", fake_inject)

    assert main(["agent", "inject", "--task", "fix the parser"]) == 0
    assert seen["args"] == (tmp_path, "fix the parser")
    assert "WAKE-CONTEXT-DIGEST" in capsys.readouterr().out


def test_agent_inject_requires_repo(monkeypatch):
    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    assert main(["agent", "inject"]) == 2


def test_agent_inject_reports_empty_dominion(monkeypatch, tmp_path):
    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "brr.prompts.build_injected_context",
        lambda repo_root, *, task_text=None: "",
    )
    assert main(["agent", "inject"]) == 1


def test_agent_requires_subcommand(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["agent"])
    assert exc.value.code == 2


def test_bind_dispatches_to_gate_bind(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    calls = []

    class FakeGate:
        @staticmethod
        def bind(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)

    main(["gate", "bind", str(repo), "telegram"])

    assert calls == [repo / ".brr"]


def test_setup_dispatches_to_gate_setup(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def setup(brr_dir):
            calls.append(brr_dir)

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["gate", "setup", "telegram"])

    assert calls == [tmp_path / ".brr"]


def test_setup_falls_back_to_auth_then_bind(monkeypatch, tmp_path):
    calls = []

    class FakeGate:
        @staticmethod
        def auth(brr_dir):
            calls.append(("auth", brr_dir))

        @staticmethod
        def bind(brr_dir):
            calls.append(("bind", brr_dir))

    monkeypatch.setattr("brr.cli._load_gate", lambda name: FakeGate)
    monkeypatch.setattr("brr.cli._brr_dir", lambda: tmp_path / ".brr")

    main(["gate", "setup", "telegram"])

    assert calls == [
        ("auth", tmp_path / ".brr"),
        ("bind", tmp_path / ".brr"),
    ]


# ── brnrd runners list (step 2, design-runner-cores.md) ───────────────────────


def test_runners_list_text_output(monkeypatch, capsys):
    """Text output shows unified catalog with availability and stale marks."""
    import shutil as _shutil

    from brr import runner as runner_mod, runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    # Pretend claude is on PATH, codex is not
    monkeypatch.setattr(
        runner_cores.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )
    monkeypatch.setattr(
        _shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )
    monkeypatch.setattr(
        runner_mod.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )

    assert main(["runners", "list"]) == 0
    out = capsys.readouterr().out

    # Unified catalog header
    assert "runner catalog" in out
    # Claude cores appear (available ✓)
    assert "claude-haiku" in out or "claude-sonnet" in out
    # Unavailable profiles also shown (with ✗)
    assert "codex-mini" in out
    assert "✗" in out


def test_runners_list_all_is_noop(monkeypatch, capsys):
    """--all is accepted for backwards-compat; unavailable rows appear by default."""
    import shutil as _shutil

    from brr import runner as runner_mod, runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    monkeypatch.setattr(runner_cores.shutil, "which", lambda name: None)
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name: None)

    assert main(["runners", "list", "--all"]) == 0
    out = capsys.readouterr().out
    # Even with no shells on PATH, profiles still shown with ✗ marks
    assert "claude-haiku" in out or "claude-sonnet" in out


def test_runners_list_json_output(monkeypatch, capsys):
    """--json emits machine-readable JSON with unified profiles list."""
    import shutil as _shutil

    from brr import runner as runner_mod, runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: None)
    monkeypatch.setattr(runner_cores.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner_mod.shutil, "which",
                        lambda name: f"/usr/bin/{name}")

    assert main(["runners", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "profiles" in payload
    assert isinstance(payload["profiles"], list)
    # All bundled cores visible when all Shells are on PATH
    names = [r["name"] for r in payload["profiles"]]
    assert "claude-haiku" in names
    assert "codex-mini" in names


def test_runners_list_marks_current_runner(monkeypatch, capsys, tmp_path):
    """Currently resolved runner is marked with ★ in the text view."""
    import shutil as _shutil

    from brr import runner as runner_mod, runner_cores

    monkeypatch.setattr("brr.cli._maybe_repo_root", lambda: tmp_path)
    monkeypatch.setattr(runner_mod, "resolve_runner", lambda _root: "claude")
    monkeypatch.setattr(runner_mod, "_load_profiles", lambda _root=None: {
        "claude": {"class": "balanced", "cost_rank": 30},
        "codex": {"class": "balanced", "cost_rank": 25},
    })
    monkeypatch.setattr(
        runner_cores.shutil, "which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert main(["runners", "list"]) == 0
    out = capsys.readouterr().out
    # The ★ marker should appear next to the currently selected runner
    assert "★" in out
    assert "claude" in out


# ── the CLI surface itself (#49) ──────────────────────────────────────────────
#
# The verb list is a contract: `brnrd --help` is the front door, and every doc,
# prompt, and muscle-memory habit spells against it. Before #49 the tree had
# drifted for months without a single test noticing. These pin the surface so
# the next drift is a test failure, not a discovery.


def _subparsers_action():
    import argparse

    from brr.cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("brnrd has no subparsers")


def test_public_commands_are_exactly_what_help_lists():
    from brr.cli import PUBLIC_COMMANDS

    listed = [c.dest for c in _subparsers_action()._choices_actions]
    assert sorted(listed) == sorted(PUBLIC_COMMANDS)


def test_all_commands_are_exactly_what_parses():
    from brr.cli import ALL_COMMANDS

    assert sorted(_subparsers_action().choices) == sorted(ALL_COMMANDS)


def test_help_stays_small_enough_to_read():
    # The whole point of the noun consolidation: a front door a human can scan.
    # Not a golden count — a ceiling. Adding a top-level verb should require
    # arguing that it earns one of these slots.
    from brr.cli import PUBLIC_COMMANDS

    # `enable` earns the nineteenth slot as the tier-1 adoption verb.
    assert len(PUBLIC_COMMANDS) <= 19


def test_hidden_commands_parse_but_are_not_listed():
    from brr.cli import HIDDEN_COMMANDS

    action = _subparsers_action()
    listed = {c.dest for c in action._choices_actions}
    for name in HIDDEN_COMMANDS:
        assert name in action.choices, f"{name} must still parse"
        assert name not in listed, f"{name} must not spend a --help line"


def test_help_does_not_leak_the_suppress_sentinel(capsys):
    # `help=argparse.SUPPRESS` on add_parser does not hide a subparser — it
    # renders a literal "==SUPPRESS==" line. Omitting the kwarg is the lever.
    # This pins the symptom so nobody "fixes" the hiding back into a leak.
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "SUPPRESS" not in out


@pytest.mark.parametrize(
    "argv,pointer",
    [
        (["auth", "telegram"], "brnrd gate auth"),
        (["bind", ".", "telegram"], "brnrd gate bind"),
        (["setup", "telegram"], "brnrd gate setup"),
        (["add", "."], "brnrd account add"),
        (["connect"], "brnrd account connect"),
    ],
)
def test_retired_spellings_fail_with_a_pointer(argv, pointer, capsys):
    # Pre-release: the old spellings do not survive as silent aliases. They
    # fail — but they fail *pointing*, not with argparse's bare "invalid
    # choice", which would leave the reader to guess where the verb went.
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert pointer in capsys.readouterr().err


def test_retired_spellings_do_not_run_their_old_command(monkeypatch):
    # The pointer must fire *before* any gate work: a retired spelling that
    # still authed would be an alias wearing an error message.
    calls = []
    monkeypatch.setattr("brr.cli._load_gate", lambda name: calls.append(name))
    for argv in (["auth", "telegram"], ["setup", "telegram"], ["bind", ".", "telegram"]):
        with pytest.raises(SystemExit):
            main(argv)
    assert calls == []


def test_up_and_daemon_up_are_the_same_implementation():
    # The #49 drift in one assertion: `up` used to be a second implementation
    # that skipped the installed service.
    from brr.cli import cmd_daemon_up

    action = _subparsers_action()
    top_up = action.choices["up"].get_default("func")
    daemon_up = _subcommand_default(action.choices["daemon"], "up", "func")
    assert top_up is daemon_up is cmd_daemon_up


def test_daemon_up_with_dev_reload_never_delegates_to_the_service(monkeypatch):
    # `--dev-reload` is a foreground concept the installed service cannot
    # carry; delegating would silently drop it (and lie "service started"
    # while the caller's flag did nothing).
    import argparse
    from pathlib import Path

    from brr.cli import cmd_daemon_up

    delegated = []
    monkeypatch.setattr(
        "brr.daemon_install.start_service", lambda: delegated.append(True) or 0,
    )
    started = []
    monkeypatch.setattr("brr.daemon.start", lambda root, dev_reload: started.append(dev_reload))
    monkeypatch.setattr("brr.cli._repo_root", lambda: Path("/tmp/repo"))

    args = argparse.Namespace(foreground=False, dev_reload=True)
    cmd_daemon_up(args)

    assert delegated == []
    assert started == [True]


def test_daemon_up_service_path_reports_available_update(monkeypatch, capsys):
    import argparse

    from brr import release_availability
    from brr.cli import cmd_daemon_up

    monkeypatch.setattr("brr.daemon_install.start_service", lambda: 0)
    monkeypatch.setattr(
        release_availability,
        "refresh_if_stale",
        lambda _root: release_availability.Availability("0.1.0", "0.2.0"),
    )

    assert cmd_daemon_up(argparse.Namespace(foreground=False, dev_reload=None)) == 0
    assert "[brnrd] update available: 0.1.0 → 0.2.0" in capsys.readouterr().out


def test_down_and_daemon_down_are_the_same_implementation():
    from brr.cli import cmd_daemon_down

    action = _subparsers_action()
    top_down = action.choices["down"].get_default("func")
    daemon_down = _subcommand_default(action.choices["daemon"], "down", "func")
    assert top_down is daemon_down is cmd_daemon_down


def test_top_level_up_accepts_the_same_flags_as_daemon_up():
    # A thin alias that dropped --foreground would be a different verb.
    action = _subparsers_action()
    top = {o for a in action.choices["up"]._actions for o in a.option_strings}
    nested_parser = _subcommand_parser(action.choices["daemon"], "up")
    nested = {o for a in nested_parser._actions for o in a.option_strings}
    assert top == nested


def _subcommand_parser(parser, name):
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and name in action.choices:
            return action.choices[name]
    raise AssertionError(f"no subcommand {name}")


def _subcommand_default(parser, name, key):
    return _subcommand_parser(parser, name).get_default(key)


# ── brnrd gate list / account status / completions (#49, new surfaces) ────────


def test_gate_list_reads_each_gates_own_is_configured(monkeypatch, tmp_path, capsys):
    seen = []

    def fake_load(name):
        seen.append(name)
        mod = types.SimpleNamespace(is_configured=lambda brr_dir: name == "telegram")
        return mod

    monkeypatch.setattr("brr.cli._load_gate", fake_load)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: tmp_path / ".brr")

    assert main(["gate", "list"]) == 0

    from brr.cli import GATES

    assert seen == list(GATES)
    out = capsys.readouterr().out
    assert "✓ telegram   configured" in out
    assert "· slack      not configured" in out


def test_gate_list_json_shape(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "brr.cli._load_gate",
        lambda name: types.SimpleNamespace(is_configured=lambda d: False),
    )
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: tmp_path / ".brr")

    assert main(["gate", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    from brr.cli import GATES

    assert [g["name"] for g in payload["gates"]] == list(GATES)
    assert all(g["configured"] is False for g in payload["gates"])


def test_gate_list_outside_a_repo_reports_unknown_not_false(monkeypatch, capsys):
    # Honesty: with no .brr to read, "not configured" would be a claim we
    # cannot support. The catalogue still prints; each gate reports unknown.
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: None)

    assert main(["gate", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["brr_dir"] is None
    assert all(g["configured"] is None for g in payload["gates"])


def test_gate_list_survives_a_broken_gate(monkeypatch, tmp_path, capsys):
    def boom(name):
        if name == "slack":
            raise RuntimeError("gate state is corrupt")
        return types.SimpleNamespace(is_configured=lambda d: True)

    monkeypatch.setattr("brr.cli._load_gate", boom)
    monkeypatch.setattr("brr.cli._maybe_brr_dir", lambda: tmp_path / ".brr")

    assert main(["gate", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    states = {g["name"]: g["configured"] for g in payload["gates"]}
    assert states["slack"] is None  # unknown, not a crash
    assert states["telegram"] is True


def test_account_status_does_not_create_the_home_it_reports(monkeypatch, tmp_path):
    # A status command that materializes a home is lying about what it found.
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    seen = {}

    real = __import__("brr.account", fromlist=["resolve_context"]).resolve_context

    def spy(repo_root, cfg=None, *, create=True):
        seen["create"] = create
        return real(repo_root, cfg, create=create)

    monkeypatch.setattr("brr.account.resolve_context", spy)

    assert main(["account", "status"]) == 0
    assert seen["create"] is False


def test_account_status_json_reports_the_resolved_home(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    assert main(["account", "status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] in {"project", "account"}
    assert payload["dominion_repo"]
    assert any(r["default"] for r in payload["repos"])
    home = next(r for r in payload["repos"] if r["label"] == "home")
    assert home["kind"] == "home"
    assert home["root"] == payload["dominion_repo"]


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completions_emit_every_public_verb(shell, capsys):
    from brr.cli import PUBLIC_COMMANDS

    assert main(["completions", shell]) == 0
    out = capsys.readouterr().out
    for verb in PUBLIC_COMMANDS:
        assert verb in out, f"{shell} completions omit {verb}"


def test_completions_track_the_parser_not_a_hand_list(capsys):
    # The generator walks the live tree, so a new subcommand shows up in
    # completions without anyone remembering to update a table.
    assert main(["completions", "bash"]) == 0
    out = capsys.readouterr().out
    assert "add connect disconnect relabel status" in out  # brnrd account
    assert "auth bind list setup" in out  # brnrd gate


def test_completions_omit_retired_and_hidden_spellings(capsys):
    # Scoped to the *top-level* completion context on purpose: `add`, `connect`,
    # `auth`, `bind`, `setup` are retired as top-level verbs but are the real
    # spellings one level down (`brnrd account add`), so a bare substring check
    # would fail on the very nesting this slice introduced.
    from brr.cli import HIDDEN_COMMANDS, RETIRED_COMMANDS

    assert main(["completions", "fish"]) == 0
    top_level = {
        line.split('-a "')[1].rstrip('"')
        for line in capsys.readouterr().out.splitlines()
        if "__fish_use_subcommand" in line
    }
    assert top_level.isdisjoint(RETIRED_COMMANDS)
    assert top_level.isdisjoint(HIDDEN_COMMANDS)


def test_completions_rejects_an_unknown_shell():
    with pytest.raises(SystemExit) as exc:
        main(["completions", "nushell"])
    assert exc.value.code == 2


# ── brnrd kb — optional query (#649) ─────────────────────────────────────────


def _kb_repo(tmp_path):
    """Set up a minimal repo+kb dir for cmd_kb tests."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    kb = repo / "kb"
    kb.mkdir()
    # Two pages so compute_graph_stats returns a non-empty GraphStats.
    (kb / "index.md").write_text("# Index\n", encoding="utf-8")
    (kb / "subject-test.md").write_text("# Test subject\n", encoding="utf-8")
    return repo, kb


def test_cmd_kb_no_query_exits_0_and_prints_graph_header(tmp_path, capsys, monkeypatch):
    """brnrd kb with no query prints the graph report and exits 0."""
    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None, **_: kb)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: kb)

    rc = main(["kb"])
    out = capsys.readouterr().out
    assert rc == 0
    # Assert on a real section header from format_graph_stats, not a hardcoded guess.
    from brr.kb_health import format_graph_stats, GraphStats
    sample = format_graph_stats(GraphStats(total_pages=1, total_bytes=1))
    header = sample.splitlines()[0]
    assert header in out


def test_cmd_kb_no_query_on_unresolvable_kb_is_not_a_silent_success(
    tmp_path, capsys, monkeypatch
):
    """A root with no resolvable kb must say so and exit non-zero.

    `knowledge.active_kb_dir` returns None whenever `sources()` finds neither
    `home` nor `repo-kb` — a fresh checkout before `brnrd init`, and, far more
    commonly, **any run worktree**, which is the default `worktree` environment
    every brnrd run uses. Driven against a live worktree, that path printed
    zero bytes to stdout, zero to stderr, and exited 0: a silent success, and
    strictly worse than the `exit 2` usage error this command replaced.

    Note the sibling test above monkeypatches `active_kb_dir` to a real
    directory. That fixture chooses a resolution the runtime does not produce
    from a worktree — the same shape as a fixture that chooses a lifecycle
    moment. This test asserts the resolution the runtime *does* produce there.
    """
    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None, **_: kb)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: None)

    rc = main(["kb"])
    captured = capsys.readouterr()
    assert rc == 1, "an unresolvable kb must not report success"
    assert captured.out.strip(), "an unresolvable kb must not print nothing"
    # Name what was looked for and where, so the reader can act on it.
    assert "[brnrd kb]" in captured.out
    assert str(repo) in captured.out
    # The fixture repo *does* have a populated `kb/`. Reporting on it here
    # would mean None had been read as "unspecified" rather than as "none".
    assert "Graph stats" not in captured.out


def test_cmd_kb_no_query_on_empty_kb_dir_is_not_a_silent_success(
    tmp_path, capsys, monkeypatch
):
    """A resolved-but-pageless kb dir must say so and exit non-zero.

    `format_graph_stats` renders zeroed stats as the empty string, so the
    unguarded path printed zero bytes and returned 0 — a silent success, and
    strictly worse than the `exit 2` usage error this command replaced.
    """
    repo, _kb = _kb_repo(tmp_path)
    empty = tmp_path / "empty-kb"
    empty.mkdir()
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None, **_: empty)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: empty)

    rc = main(["kb"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no pages" in captured.out
    assert str(empty) in captured.out


def test_cmd_kb_no_query_names_the_directory_it_walked(
    tmp_path, capsys, monkeypatch
):
    """The report says which knowledge root it walked.

    One repo has several plausible knowledge roots — an account-scoped home, a
    project-scoped home, the `.brnrd-kb` checkout clone, a committed `kb/` —
    and which one `active_kb_dir` returns depends on where the command ran.
    Driven 2026-07-24: from one worktree it resolved to a project-scoped root
    holding a single page while the same wake's kb-health block reported 155.
    Both numbers were stated flatly and neither named its corpus. A report a
    reader cannot attribute is a report they cannot reconcile.
    """
    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None, **_: kb)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: kb)

    rc = main(["kb"])
    out = capsys.readouterr().out
    assert rc == 0
    assert str(kb) in out, "the report must name the directory it walked"


# ── #1193 — never answer from a home you just made ──────────────────────


def test_cmd_kb_project_fallback_names_the_reason_and_scaffolds_nothing(
    tmp_path, capsys, monkeypatch
):
    """No account link resolvable ⇒ the read names the fallback and why.

    The bug: `cmd_kb` resolved through `resolve_context()`'s default
    `create=True`, which silently mints a fresh, empty project home
    (mkdir + git init) whenever no account link is found, then reported on
    it exactly as it would a real, populated home — nothing distinguished
    "genuinely empty" from "just invented for this call". Real
    `ensure_checkout`/`resolve_context` run here (not mocked): the
    assertion that matters is that neither the reason nor the absence of a
    scaffolded home is a mock artifact.
    """
    from brr import account as account_mod

    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: kb)

    # Where a project-fallback home would land for this repo, computed the
    # same read-only way the fix does it — never created ahead of the call.
    project_home = account_mod.resolve_context(repo, {}, create=False).dominion_repo
    assert not project_home.exists(), "fixture must start with no home minted"

    rc = main(["kb"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "project fallback" in out
    assert "no account link found" in out
    assert str(repo) in out
    # The whole point: reading must not have originated the thing it reports
    # having not found linked.
    assert not project_home.exists(), "a read must not scaffold a home to report on"


def test_cmd_kb_account_linked_read_reports_cleanly(tmp_path, capsys, monkeypatch):
    """An account-resolved read still reports cleanly — no regression (#1193).

    Most wakes on this machine do have an account link; the common case
    must render exactly as before, now carrying the (correct) reason.
    """
    repo, kb = _kb_repo(tmp_path)
    (repo / ".brr").mkdir(exist_ok=True)
    (repo / ".brr" / "config").write_text(
        "account.id=acct-test\n", encoding="utf-8",
    )
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: kb)

    rc = main(["kb"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "account acct-test (linked)" in out
    assert "project fallback" not in out


def test_cmd_notes_check_names_the_resolution_reason(tmp_path, capsys, monkeypatch):
    """``brnrd notes check`` carries the same account/fallback reason (#1193)."""
    from brr import cli

    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.setattr(cli, "_repo_root", lambda: repo)

    rc = main(["notes", "check"])
    out = capsys.readouterr().out
    assert rc in (0, 1)
    assert "project fallback" in out
    assert "no account link found" in out


def test_cmd_kb_with_query_hit_exits_0(tmp_path, capsys, monkeypatch):
    """brnrd kb <query> with a match exits 0 and prints the hit — unchanged."""
    repo, kb = _kb_repo(tmp_path)
    (kb / "needle-page.md").write_text("the needle is here\n", encoding="utf-8")
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None, **_: kb)

    rc = main(["kb", "needle"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "needle" in out


def test_cmd_kb_with_query_no_match_exits_1(tmp_path, capsys, monkeypatch):
    """brnrd kb <query> with no match exits 1 — unchanged behaviour."""
    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.ensure_checkout", lambda root, cfg=None, **_: kb)

    rc = main(["kb", "xyzzy-no-such-term-8675309"])
    assert rc == 1


# ── `brnrd prompts wake` — a run's context, both halves ──────────────────


def _run_dir_with(tmp_path, *, prompt=None, boundaries=None):
    run_dir = tmp_path / ".brr" / "runs" / "run-260727-1716-54h0"
    run_dir.mkdir(parents=True)
    if prompt is not None:
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    if boundaries is not None:
        (run_dir / "boundaries.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in boundaries),
            encoding="utf-8",
        )
    return run_dir


def test_wake_dump_renders_the_boot_then_every_boundary_in_order(tmp_path):
    from brr.cli import _wake_dump

    run_dir = _run_dir_with(
        tmp_path,
        prompt="# the wake\nbody\n",
        boundaries=[
            {"at": "2026-07-27T17:21:31Z", "phase": "session-start",
             "inject": "seed capsule", "block": False, "block_reason": None},
            {"at": "2026-07-27T17:21:34Z", "phase": "post-tool",
             "inject": None, "block": False, "block_reason": None},
            {"at": "2026-07-27T17:25:02Z", "phase": "stop",
             "inject": "closeout", "block": True,
             "block_reason": "one more thing"},
        ],
    )
    out = _wake_dump(run_dir, boot=True, limit=None)

    assert "# the wake" in out
    assert "3 hook fire(s)" in out
    # Order is the run's own, and it is the whole point of the file.
    assert out.index("seed capsule") < out.index("closeout")
    # A silent boundary is rendered, not skipped — the count must stay honest.
    assert "_silent" in out
    assert "**BLOCKED**" in out
    assert "one more thing" in out


def test_wake_dump_distinguishes_an_old_run_from_a_quiet_one(tmp_path):
    """No transcript file is 'this run predates it', never 'no boundaries'.

    Absent and empty are different answers, and every run written before the
    transcript existed has no file — reading that as "nothing was injected"
    would be a wrong fact about the busiest runs in the archive.
    """
    from brr.cli import _wake_dump

    run_dir = _run_dir_with(tmp_path, prompt="# the wake\n")
    out = _wake_dump(run_dir, boot=True, limit=None)

    assert "predates the boundary transcript" in out
    assert "hook fire(s)" not in out


def test_wake_dump_limit_says_it_is_showing_only_the_first_n(tmp_path):
    from brr.cli import _wake_dump

    run_dir = _run_dir_with(
        tmp_path,
        prompt="# the wake\n",
        boundaries=[
            {"at": f"2026-07-27T17:2{i}:00Z", "phase": "post-tool",
             "inject": f"tick {i}", "block": False, "block_reason": None}
            for i in range(5)
        ],
    )
    out = _wake_dump(run_dir, boot=False, limit=2)

    assert "5 hook fire(s), showing the first 2" in out
    assert "tick 1" in out
    assert "tick 4" not in out
    assert "# the wake" not in out  # --no-boot


def test_wake_dump_names_a_missing_boot_rather_than_omitting_it(tmp_path):
    from brr.cli import _wake_dump

    run_dir = _run_dir_with(tmp_path, boundaries=[])
    out = _wake_dump(run_dir, boot=True, limit=None)

    assert "absent: no `prompt.md`" in out


def test_the_default_run_is_my_own_run_not_the_newest_directory(tmp_path, monkeypatch):
    """Inside a run that spawned a worker, newest-wins picks the *child*.

    The failure this guards is silent: the command answers a different
    question than the one asked and nothing in the output says so. A run
    asking for "the wake" means its own.
    """
    import os
    from brr.cli import _default_wake_run

    runs_dir = tmp_path / "runs"
    mine = runs_dir / "run-260727-1716-54h0"
    child = runs_dir / "run-260727-1724-szdg"
    mine.mkdir(parents=True)
    child.mkdir(parents=True)
    os.utime(child, (2_000_000_000, 2_000_000_000))  # decisively newer

    monkeypatch.setenv("BRR_RUN_ID", mine.name)
    assert _default_wake_run(runs_dir) == mine

    # Outside a run there is no "my own", so newest is the honest answer.
    monkeypatch.delenv("BRR_RUN_ID")
    assert _default_wake_run(runs_dir) == child

    # A stale id naming a directory that no longer exists must not win.
    monkeypatch.setenv("BRR_RUN_ID", "run-gone")
    assert _default_wake_run(runs_dir) == child


# ── brnrd prompts replay: w-56 rung 1 ─────────────────────────────────
#
# CLI-level coverage; `tests/test_replay.py` covers `brr.replay` itself
# (locate mechanism, splice, curated-extract handling) against real
# `build_daemon_prompt_with_score` fixtures. These tests only need to check
# the CLI wires args -> `replay_mod` correctly and reports exit codes.


def _replay_repo_with_run(tmp_path: Path, run_id: str = "run-cli-0001") -> Path:
    """A real git repo with one real captured (unmounted) daemon run."""
    from brr import bootscore
    from brr.prompts import build_daemon_prompt_with_score

    repo = tmp_path / "repo"
    init_git_repo(repo)

    prompt, score = build_daemon_prompt_with_score(
        "Implement a small feature, commit, and push a branch.",
        "evt-cli-0001", "/tmp/brr-cli-response.md", repo,
        outbox_path="/tmp/brr-cli-outbox", run_id=run_id, source="spawn",
        environment="worktree", branch_name="brr/cli-test", budget_seconds=7200,
        hooks_installed=True, runner_name="claude-sonnet", runner_shell="claude",
        runner_core="claude-sonnet-4-6",
    )
    run_dir = repo / ".brr" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (run_dir / "boot-score.json").write_text(
        json.dumps(bootscore.to_dict(score), indent=2, sort_keys=True), encoding="utf-8",
    )
    return repo


def test_prompts_replay_reports_substitutions_and_exits_zero(tmp_path, monkeypatch, capsys):
    repo = _replay_repo_with_run(tmp_path)
    monkeypatch.chdir(repo)

    prompts_dir = tmp_path / "edited"
    prompts_dir.mkdir()
    (prompts_dir / "weave.md").write_text("# Edited weave\n", encoding="utf-8")

    code = main(["prompts", "replay", "run-cli-0001", "--prompts", str(prompts_dir)])

    assert code == 0
    out = capsys.readouterr().out
    assert "weave" in out
    assert "daemon-substrate" in out  # unchanged blocks still print in the roster
    assert "total delta:" in out


def test_prompts_replay_json_output_is_parseable(tmp_path, monkeypatch, capsys):
    repo = _replay_repo_with_run(tmp_path)
    monkeypatch.chdir(repo)

    prompts_dir = tmp_path / "edited"
    prompts_dir.mkdir()
    (prompts_dir / "weave.md").write_text("# Edited weave\n", encoding="utf-8")

    code = main(["prompts", "replay", "run-cli-0001", "--prompts", str(prompts_dir), "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run-cli-0001"
    weave = next(b for b in payload["blocks"] if b["block_key"] == "weave")
    assert weave["status"] == "substituted"


def test_prompts_replay_refuses_an_unknown_run(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    code = main(["prompts", "replay", "run-does-not-exist", "--prompts", str(tmp_path)])

    assert code == 1
    assert "unknown run" in capsys.readouterr().err


def test_prompts_replay_refuses_a_layout_it_cannot_verify(tmp_path, monkeypatch, capsys):
    """The offline mirror of the mounted-wake finding: a captured prompt.md
    whose bytes don't reconcile with its own boot-score.json must refuse,
    not report a plausible-looking empty diff."""
    repo = _replay_repo_with_run(tmp_path)
    monkeypatch.chdir(repo)
    # Truncate the captured prompt as a mount (or any corruption) would.
    prompt_path = repo / ".brr" / "runs" / "run-cli-0001" / "prompt.md"
    prompt_path.write_text(prompt_path.read_text(encoding="utf-8")[:100], encoding="utf-8")

    code = main(["prompts", "replay", "run-cli-0001", "--prompts", str(tmp_path)])

    assert code == 1
    assert "refusing" in capsys.readouterr().err


def test_prompts_replay_rejects_a_missing_prompts_dir(tmp_path, monkeypatch, capsys):
    repo = _replay_repo_with_run(tmp_path)
    monkeypatch.chdir(repo)

    code = main(["prompts", "replay", "run-cli-0001", "--prompts", str(tmp_path / "nope")])

    assert code == 1
    assert "not a directory" in capsys.readouterr().err


# ── brnrd gate-run: the shipped `hooks.gate_command` writer ──────────
#
# hooks.gate_command arms a Stop-hook obligation that reads
# .gate-receipts.json — a map keyed per tree (#820) — but only this repo's
# own unshipped scripts/gate.py ever wrote one — any other adopter got a
# permanent "the gate never ran" (kb/design-io-layer-trim.md, THE OBLIGATION
# NOTHING CAN SATISFY). These drive the real `brnrd gate-run` entry point
# end to end.


def _gate_run_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "seed"], cwd=repo, check=True, capture_output=True,
    )
    return repo


def test_gate_run_requires_outbox_dir(tmp_path, monkeypatch):
    """Only writes anything from inside a run — a bare shell invocation with
    no Stop-hook obligation watching it has nothing to satisfy."""
    repo = _gate_run_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["gate-run", "--override-command", "true"])
    assert "BRR_OUTBOX_DIR" in str(exc.value)


def test_gate_run_requires_a_command(tmp_path, monkeypatch):
    repo = _gate_run_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(tmp_path / "outbox"))
    with pytest.raises(SystemExit) as exc:
        main(["gate-run"])
    assert "hooks.gate_command" in str(exc.value)


def test_gate_run_reads_hooks_gate_command_from_config(tmp_path, monkeypatch):
    """The ordinary path: no override flag, just the repo's own configured
    gate command — the same value the resident set via the init playbook."""
    from brr import config as conf
    from brr import gate_receipt

    repo = _gate_run_repo(tmp_path)
    # Not "true"/"false" — the flat `key=value` config format coerces those
    # to Python bools (`_parse_value`), which is a separate footgun this
    # test must not trip over.
    conf.write_config(repo, {"hooks.gate_command": "exit 0"})
    outbox = tmp_path / "outbox"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))
    monkeypatch.setenv("BRR_RUN_ID", "run-abc")

    with pytest.raises(SystemExit) as exc:
        main(["gate-run"])
    assert exc.value.code == 0

    payload = gate_receipt.read_receipt(outbox, repo)
    assert payload["verdict"] == "GREEN"
    assert payload["gate_command"] == "exit 0"
    assert payload["run_id"] == "run-abc"


def test_gate_run_forwards_a_failing_commands_exit_code(tmp_path, monkeypatch):
    from brr import gate_receipt

    repo = _gate_run_repo(tmp_path)
    outbox = tmp_path / "outbox"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))

    with pytest.raises(SystemExit) as exc:
        main(["gate-run", "--override-command", "exit 1"])
    assert exc.value.code == 1
    payload = gate_receipt.read_receipt(outbox, repo)
    assert payload["verdict"] == "RED"


def test_gate_run_satisfies_the_stop_hook_obligation_end_to_end(tmp_path, monkeypatch):
    """The whole point, proven through both real callers: `brnrd gate-run`
    writes the receipt, and `hooks._gate_closeout_clause` — the guard that
    used to fire forever for any adopter — reads it and goes silent."""
    from brr import config as conf
    from brr import hooks

    repo = _gate_run_repo(tmp_path)
    conf.write_config(repo, {"hooks.gate_command": "exit 0"})
    (repo / "feature.py").write_text("new work\n", encoding="utf-8")
    outbox = tmp_path / "outbox"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))

    with pytest.raises(SystemExit) as exc:
        main(["gate-run"])
    assert exc.value.code == 0

    ctx = hooks.HookContext({
        "BRR_OUTBOX_DIR": str(outbox),
        "BRR_REPO_DIR": str(repo),
        "BRR_SEED_REF": "main",
        "BRR_GATE_COMMAND": "exit 0",
    })
    assert hooks._gate_closeout_clause(ctx) is None


def test_gate_run_refuses_a_tree_the_command_moved_under_itself(tmp_path, monkeypatch):
    """#917 through the writer that actually ships, end to end.

    The command writes a repo file *while it is running* — the shape of a
    four-minute gate and a resident who keeps working through it. That file
    was never gated, and before this the receipt certified it anyway, because
    both the writer's sample and the hook's recomputation happened after the
    last leg and therefore agreed.

    This is the guard that was driven red on purpose: making the pre-capture
    equal the post-capture (sampling `before` after `run()` in
    `gate_receipt.gated_run`) turns `tree_moved_during_gate` false, the hook
    goes silent, and both halves of this test fail.
    """
    from brr import gate_receipt, hooks

    repo = _gate_run_repo(tmp_path)
    outbox = tmp_path / "outbox"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))

    with pytest.raises(SystemExit) as exc:
        main(["gate-run", "--override-command",
              "printf 'no leg ever saw this\\n' > written-mid-gate.py"])
    assert exc.value.code == 0  # the command itself succeeded

    payload = gate_receipt.read_receipt(outbox, repo)
    assert payload["verdict"] == "GREEN"
    assert payload["tree_moved_during_gate"] is True
    assert "written-mid-gate.py" in payload["status"]
    assert "written-mid-gate.py" not in payload["gated_from"]["status"]

    # ...and a GREEN receipt for that tree does not end the run.
    ctx = hooks.HookContext({
        "BRR_OUTBOX_DIR": str(outbox),
        "BRR_REPO_DIR": str(repo),
        "BRR_SEED_REF": "main",
        "BRR_GATE_COMMAND": "make test",
    })
    reason = hooks._gate_closeout_clause(ctx)
    assert reason is not None
    assert "written-mid-gate.py" in reason
    assert "the gate never ran" not in reason


def test_gate_run_on_two_trees_under_one_outbox_both_survive(tmp_path, monkeypatch):
    """#820 through the real entry point twice: this repo's own documented
    `host` pattern is `git worktree add /tmp/brr-wt-<slug>`, gate there, then
    gate the checkout too — one `BRR_OUTBOX_DIR` shared by both invocations.
    The second `brnrd gate-run` must not destroy the first tree's receipt,
    and each tree's own `_gate_closeout_clause` reader must see only its own."""
    from brr import gate_receipt, hooks

    scratch = _gate_run_repo(tmp_path / "scratch")
    checkout = _gate_run_repo(tmp_path / "checkout")
    outbox = tmp_path / "outbox"
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))

    monkeypatch.chdir(scratch)
    with pytest.raises(SystemExit) as exc:
        main(["gate-run", "--override-command", "exit 0"])
    assert exc.value.code == 0

    monkeypatch.chdir(checkout)
    with pytest.raises(SystemExit) as exc:
        main(["gate-run", "--override-command", "exit 1"])
    assert exc.value.code == 1

    scratch_entry = gate_receipt.read_receipt(outbox, scratch)
    checkout_entry = gate_receipt.read_receipt(outbox, checkout)
    assert scratch_entry["verdict"] == "GREEN"
    assert checkout_entry["verdict"] == "RED"

    # And a guard asking about `scratch` is satisfied by `scratch`'s own
    # entry, unmoved by `checkout`'s RED sitting right beside it.
    ctx = hooks.HookContext({
        "BRR_OUTBOX_DIR": str(outbox),
        "BRR_REPO_DIR": str(scratch),
        "BRR_SEED_REF": "main",
        "BRR_GATE_COMMAND": "exit 0",
    })
    assert hooks._gate_closeout_clause(ctx) is None


def test_close_check_refuses_a_pr_body_and_exits_nonzero(tmp_path, capsys):
    """The opt-in half of #839.

    A PR opened by hand with `gh pr create` passes through no brnrd code, so
    the only honest coverage there is a verb the run calls itself — and the
    exit code is the whole product: `brnrd close-check body.md && gh pr create
    --body-file body.md` is one command line.
    """
    body = tmp_path / "body.md"
    body.write_text(
        "Ships move 5.\n\nCloses #749 move 5 (the ticket stays open for 1-4).\n"
    )

    assert main(["close-check", str(body)]) == 1
    out = capsys.readouterr().out
    assert "pr-body:3: close keyword with a tail" in out
    assert "Mask the digits" in out


def test_close_check_passes_a_clean_body(tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("Ships the whole thing.\n\nCloses #839.\n")

    assert main(["close-check", str(body)]) == 0
    out = capsys.readouterr().out
    assert "will close 1 issue(s) (pr-body)" in out
    assert "Closes #839" in out


def test_close_check_json_carries_the_rule_and_line(tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("Fix #533: split config and closes #534\n")

    assert main(["close-check", str(body), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["findings"][0]["rule"] == "colon-close"
    assert payload["findings"][0]["line_number"] == 1


def test_close_check_reads_stdin_and_honours_the_channel(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO("This does not close #477.\n"),
    )
    assert main(["close-check", "--channel", "commit-msg"]) == 1
    out = capsys.readouterr().out
    assert "commit-msg:1: close keyword not at the start of a line" in out
    assert "Bypass: git commit --no-verify" in out


def test_close_check_missing_file_is_a_clean_error(tmp_path, capsys):
    assert main(["close-check", str(tmp_path / "nope.md")]) == 2
    assert "[brnrd close-check]" in capsys.readouterr().out


def test_close_check_enumerates_two_close_keywords(tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("Ships the fix.\n\nCloses #1433, #1434.\n")

    assert main(["close-check", str(body)]) == 0
    out = capsys.readouterr().out
    assert "will close 2 issue(s) (pr-body)" in out
    assert "Closes #1433" in out
    assert "Closes #1434" in out


def test_close_check_no_close_keywords(tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("Ships the feature.\n\nThis is a great change.\n")

    assert main(["close-check", str(body)]) == 0
    out = capsys.readouterr().out
    assert "no close keywords (pr-body)" in out


def test_close_check_resolve_with_unreachable_forge(tmp_path, capsys, monkeypatch):
    """Test --resolve when forge is unreachable."""
    body = tmp_path / "body.md"
    body.write_text("Ships the fix.\n\nCloses #1433.\n")

    # Mock subprocess.run to simulate unreachable forge
    import subprocess as subprocess_module

    def mock_run(*args, **kwargs):
        # Simulate forge unreachable
        raise Exception("Connection refused")

    monkeypatch.setattr(subprocess_module, "run", mock_run)

    assert main(["close-check", str(body), "--resolve"]) == 0
    out = capsys.readouterr().out
    assert "Closes #1433 (UNKNOWN)" in out


def _mock_gh(returncode, stdout="", stderr=""):
    """A fake ``gh`` invocation with the exit code and streams a caller sets."""
    import subprocess as subprocess_module

    class _R:
        pass

    r = _R()
    r.returncode, r.stdout, r.stderr = returncode, stdout, stderr
    return subprocess_module, lambda *a, **k: r


def test_close_check_resolve_says_unknown_when_gh_is_not_authenticated(
    tmp_path, capsys, monkeypatch,
):
    """#1433 — the failure wording is the product, not a detail.

    Every non-zero ``gh`` exit used to render ``NOT_FOUND``, which a reader
    takes as *harmless — that ref does not exist*. Driven 2026-08-17 against
    two genuinely OPEN issues: an empty ``GH_CONFIG_DIR`` reported
    ``NOT_FOUND`` for both, and so did running from a directory outside the
    repo. That is a confident lie in the optimistic direction inside a command
    whose entire purpose is that a verdict must not over-promise.

    This pins the unauthenticated shape, which is the one a resident meets:
    it must say ``UNKNOWN``, never ``NOT_FOUND``.
    """
    body = tmp_path / "body.md"
    body.write_text("Ships the fix.\n\nCloses #1433.\n")
    mod, fake = _mock_gh(
        4,
        stderr=(
            "To get started with GitHub CLI, please run:  gh auth login\n"
            "Alternatively, populate the GH_TOKEN environment variable...\n"
        ),
    )
    monkeypatch.setattr(mod, "run", fake)

    assert main(["close-check", str(body), "--resolve"]) == 0
    out = capsys.readouterr().out
    assert "Closes #1433 (UNKNOWN)" in out
    assert "NOT_FOUND" not in out


def test_close_check_resolve_still_says_not_found_for_a_real_missing_ref(
    tmp_path, capsys, monkeypatch,
):
    """The other half, and it has to be here or the guard above goes green on
    a version that answers ``UNKNOWN`` to everything and knows nothing.

    ``gh``'s own wording for a genuine miss (verbatim, 2026-08-17):
    ``GraphQL: Could not resolve to an issue or pull request with the number
    of N.``
    """
    body = tmp_path / "body.md"
    body.write_text("Ships the fix.\n\nCloses #999999.\n")
    mod, fake = _mock_gh(
        1,
        stderr=(
            "GraphQL: Could not resolve to an issue or pull request with the "
            "number of 999999. (repository.issue)\n"
        ),
    )
    monkeypatch.setattr(mod, "run", fake)

    assert main(["close-check", str(body), "--resolve"]) == 0
    assert "Closes #999999 (NOT_FOUND)" in capsys.readouterr().out


def test_close_check_resolve_scopes_the_lookup_to_repo_when_asked(
    tmp_path, capsys, monkeypatch,
):
    """Without ``--repo``, gh answers about whatever repo the *working
    directory* happens to be — so one body checked from two places gets two
    verdicts and neither says which repo it answered about."""
    body = tmp_path / "body.md"
    body.write_text("Ships the fix.\n\nCloses #1433.\n")
    seen: list[list[str]] = []
    import subprocess as subprocess_module

    class _R:
        returncode, stdout, stderr = 0, '{"state":"OPEN"}', ""

    def fake(cmd, *a, **k):
        seen.append(list(cmd))
        return _R()

    monkeypatch.setattr(subprocess_module, "run", fake)

    assert main([
        "close-check", str(body), "--resolve", "--repo", "hugimuni-labs/brnrd",
    ]) == 0
    assert "Closes #1433 (OPEN)" in capsys.readouterr().out
    assert seen and seen[0][-2:] == ["--repo", "hugimuni-labs/brnrd"]


# ── The spelling the launcher earned (npx) ──────────────────────────
#
# `npx brnrd` execs a binary inside a managed venv under
# ~/.local/share/brnrd; it never puts `brnrd` on the user's PATH. The
# launcher says so with BRNRD_LAUNCHER=npx, and `cli.brnrd_cmd()` is the
# one place that reads it. Pinned on the *account connect* path (the
# second thing a new install does) and on the parser itself.
#
# Each case carries its absent twin: with the variable unset — pip, uv,
# pipx, `npm install -g` — the bare spelling must survive untouched.


class TestNpxSpelling:
    def test_brnrd_cmd_reads_the_environment_at_call_time(self, monkeypatch):
        """Not captured at import: the launcher may set it after we load."""
        from brr import cli

        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        assert cli.brnrd_cmd() == "brnrd"
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        assert cli.brnrd_cmd() == "npx brnrd"
        # An unrecognised launcher is not a licence to invent a spelling.
        monkeypatch.setenv("BRNRD_LAUNCHER", "somethingelse")
        assert cli.brnrd_cmd() == "brnrd"

    def test_usage_line_names_the_runnable_command(self, monkeypatch, capsys):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        with pytest.raises(SystemExit):
            main(["--help"])
        assert "usage: npx brnrd" in capsys.readouterr().out

    def test_usage_line_stays_bare_for_a_path_install(self, monkeypatch, capsys):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        assert "usage: brnrd" in out
        assert "npx" not in out

    def _timeout_connect(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        from brr.gates import cloud

        replies = iter([
            {"pair_code": "BR-TEST", "pair_url": "https://pair",
             "poll_secret": "secret"},
            {"status": "pending"},
        ])
        ticks = iter([0.0, 601.0])
        monkeypatch.setattr(cloud, "_request", lambda *_a, **_kw: next(replies))
        monkeypatch.setattr(cloud.time, "monotonic", lambda: next(ticks))

        with pytest.raises(SystemExit) as excinfo:
            main(["account", "connect", "https://brnrd.example"])
        return str(excinfo.value)

    def test_pairing_timeout_recovery_is_npx_spelled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        assert "re-run `npx brnrd account connect`" in self._timeout_connect(
            tmp_path, monkeypatch,
        )

    def test_pairing_timeout_recovery_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        message = self._timeout_connect(tmp_path, monkeypatch)
        assert "re-run `brnrd account connect`" in message
        assert "npx" not in message

    def _connect_no_service(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
        monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: None)
        main(["account", "connect", "--no-service"])

    def test_foreground_escape_is_npx_spelled(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        self._connect_no_service(tmp_path, monkeypatch)
        assert "Run `npx brnrd up --foreground`" in capsys.readouterr().out

    def test_foreground_escape_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        self._connect_no_service(tmp_path, monkeypatch)
        out = capsys.readouterr().out
        assert "Run `brnrd up --foreground`" in out
        assert "npx" not in out

    def test_cloud_gate_setup_pointer_is_npx_spelled(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        main(["gate", "setup", "cloud"])

        assert "Run `npx brnrd account connect`" in capsys.readouterr().out

    def test_cloud_gate_setup_pointer_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        main(["gate", "setup", "cloud"])

        out = capsys.readouterr().out
        assert "Run `brnrd account connect`" in out
        assert "npx" not in out

    def test_account_add_without_a_connected_home_is_npx_spelled(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        with pytest.raises(SystemExit) as excinfo:
            main(["account", "add", str(repo)])

        assert "run `npx brnrd account connect` first" in str(excinfo.value)

    def test_account_add_without_a_connected_home_stays_bare(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        with pytest.raises(SystemExit) as excinfo:
            main(["account", "add", str(repo)])

        message = str(excinfo.value)
        assert "run `brnrd account connect` first" in message
        assert "npx" not in message

    def test_account_status_project_home_pointer_is_npx_spelled(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        assert main(["account", "status"]) == 0

        assert "`npx brnrd account connect` links it" in capsys.readouterr().out

    def test_account_status_project_home_pointer_stays_bare(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        assert main(["account", "status"]) == 0

        out = capsys.readouterr().out
        assert "`brnrd account connect` links it" in out
        assert "npx" not in out

    def _up_before_init(self, tmp_path, monkeypatch, capsys):
        """``brnrd up`` in a repo that was never adopted.

        Driven at ``daemon.start`` rather than through ``main(["up"])``:
        the CLI verb prefers an *installed service* when one exists, so
        going through it would have the suite poke the developer's own
        systemd user manager instead of the code path under test.

        #1244 fork 1: this used to hard-exit right here (`SystemExit`,
        "run `brnrd init` first") before `daemon.start` ever wrote a
        pidfile — the crash loop #1238 could only work around downstream
        of. The daemon now boots anyway: it prints the same fact instead
        of dying on it, then falls into the normal main loop. This drives
        that loop one scan past boot and escapes it the same way every
        other loop-wiring test in this suite does — `list_pending` raises
        `StopIteration` on first call, since there is nothing pending to
        dispatch and the assertion is about boot, not dispatch.
        """
        from brr import daemon as daemon_mod

        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        monkeypatch.setattr(daemon_mod, "read_pid", lambda _b: None)
        monkeypatch.setattr(daemon_mod, "_write_pid", lambda _b: None)
        monkeypatch.setattr(daemon_mod, "_clear_pid", lambda _b: None)
        monkeypatch.setattr(daemon_mod, "_start_gates", lambda *_a: [])
        monkeypatch.setattr(daemon_mod.signal, "signal", lambda *_a: None)
        monkeypatch.setattr(daemon_mod, "publish", lambda *_a, **_k: None)
        monkeypatch.setattr(daemon_mod.conf, "load_config", lambda _r: {})

        def _stop_the_loop(*_a, **_k):
            raise StopIteration

        monkeypatch.setattr(daemon_mod.protocol, "list_pending", _stop_the_loop)

        with pytest.raises(StopIteration):
            daemon_mod.start(repo)
        return capsys.readouterr().out

    def test_up_before_init_is_npx_spelled(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        out = self._up_before_init(tmp_path, monkeypatch, capsys)
        assert "no AGENTS.md yet" in out
        assert "npx brnrd init" in out

    def test_up_before_init_stays_bare_for_a_path_install(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        out = self._up_before_init(tmp_path, monkeypatch, capsys)
        assert "no AGENTS.md yet" in out
        assert "brnrd init" in out
        assert "npx" not in out