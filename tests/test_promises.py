"""The blueprint: `.promises.jsonl`, its diff, and its boundary rendering.

Every test here drives the caller the defect would reach through — the CLI
verb, `promises.read` off a real file on disk, `hooks.compute_neutral` with a
real hook context — rather than a private helper. The three that guard the
*shape* of the feature (the empty blueprint is silence, the owed line is
latched, an edge opens the gate) are the ones worth neutering first: each was
confirmed red against the unfixed code before it was kept.
"""

from __future__ import annotations

import json

import pytest

from brr import cli, hooks, promises


# ── The file, and what a promise may name ────────────────────────────────


def test_promisable_matches_the_relics_vocabulary():
    """`promises.PROMISABLE` is spelled twice; a comment cannot hold that.

    Extended for #1060: the two blueprint-side sets agreeing with each
    other was never the whole contract — every promisable kind that the
    daemon does *not* already derive on its own must also have a keepable
    `brnrd relic <kind>` front door, or a promise of that kind sits owed
    forever except by hand-writing `.relics.jsonl`. Walked off the live
    parser, not a hand-listed set, so a subcommand rename or removal fails
    this test the same way a vocabulary drift would.
    """
    import argparse

    from brr import relics

    assert set(promises.PROMISABLE) <= relics._LIVE_KINDS
    assert set(cli._PROMISABLE) == set(promises.PROMISABLE)

    parser = cli.build_parser()
    relic_parser = None
    for action in parser._actions:  # noqa: SLF001 — argparse exposes no public walk
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            relic_parser = action.choices.get("relic")
            if relic_parser is not None:
                break
    assert relic_parser is not None, "brnrd relic did not parse"
    relic_names = set(cli._subcommand_names(relic_parser))
    hand_attested = set(cli._PROMISABLE) - cli._RELIC_AUTO_DERIVED
    assert hand_attested <= relic_names


def test_read_missing_file_is_an_empty_blueprint(tmp_path):
    assert promises.read(tmp_path) == []
    assert promises.read(None) == []


def test_read_skips_malformed_and_unpromisable_rows(tmp_path):
    (tmp_path / promises.CONTROL_NAME).write_text(
        '{"what":"pr","count":2}\n'
        "not json at all\n"
        "\n"
        '{"what":"banana"}\n'
        '["not","a","dict"]\n'
        '{"what":"kb_page","count":1}\n',
        encoding="utf-8",
    )
    rows = promises.read(tmp_path)
    assert [r["what"] for r in rows] == ["pr", "kb"]


# ── The diff ─────────────────────────────────────────────────────────────


def test_count_decides_and_ref_never_keys(tmp_path):
    """Shipping the right work under another name still keeps the promise."""
    rows = [{"what": "pr", "count": 1, "ref": "the rollout split"}]
    plan = promises.blueprint(rows, {"pr": 1})
    assert plan.owed == {}
    assert plan.kept


def test_released_rows_subtract_and_can_clear_the_line(tmp_path):
    rows = [
        {"what": "pr", "count": 2},
        {"what": "pr", "count": 2, "released": True, "why": "superseded"},
    ]
    plan = promises.blueprint(rows, None)
    assert plan.promised == {}
    assert promises.chip(plan) is None
    assert promises.owed_line(plan) is None


def test_empty_blueprint_renders_nothing_even_with_produce():
    """`promised 0 · shipped 3` must never read as a pass.

    A manifest is a self-report: a run that wrote no rows is byte-identical
    to a run that had nothing to promise, so there is no claim to make.
    """
    plan = promises.blueprint([], {"pr": 3, "commit": 9})
    assert not plan.any_promises
    assert plan.kept is False
    assert promises.chip(plan) is None
    assert promises.owed_line(plan) is None


def test_partial_shipment_will_not_name_which_one_is_owed():
    """Two promised, one shipped — the labels become candidates.

    Matching is on count, which cannot say *which*. Naming one would be a
    diagnostic asserting something the run cannot be proven wrong about.
    """
    rows = [
        {"what": "pr", "count": 1, "ref": "the rollout"},
        {"what": "pr", "count": 1, "ref": "the notices split"},
    ]
    line = promises.owed_line(promises.blueprint(rows, {"pr": 1}))
    assert line is not None
    assert "one of: the rollout · the notices split" in line

    whole = promises.owed_line(promises.blueprint(rows, {"pr": 0}))
    assert whole is not None
    assert "one of:" not in whole
    assert "the rollout · the notices split" in whole


def test_chip_is_a_count_not_a_ratio():
    rows = [{"what": "pr", "count": 3}]
    plan = promises.blueprint(rows, {"pr": 1, "commit": 40})
    # Not "1/3" and not "1/41": the produce count includes things nobody
    # promised, so there is no shared denominator to render a ratio over.
    assert promises.chip(plan) == "owed 2"


def test_token_moves_on_promise_and_on_fulfilment():
    rows = [{"what": "pr", "count": 1}]
    empty = promises.token(promises.blueprint([], None))
    made = promises.token(promises.blueprint(rows, None))
    kept = promises.token(promises.blueprint(rows, {"pr": 1}))
    assert empty != made != kept
    assert made != kept


# ── The front door ───────────────────────────────────────────────────────


def _run_cli(monkeypatch, outbox, argv):
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))
    return cli.main(argv)


def test_cli_writes_a_row_and_reports_the_owed_total(monkeypatch, tmp_path, capsys):
    assert _run_cli(monkeypatch, tmp_path, ["promise", "pr", "--count", "2"]) == 0
    out = capsys.readouterr().out
    assert "owed 2" in out
    rows = [
        json.loads(line)
        for line in (tmp_path / promises.CONTROL_NAME).read_text().splitlines()
    ]
    assert rows == [{"what": "pr", "count": 2}]


# ── #1060: the new relic front doors actually keep what they promise ──────


@pytest.mark.parametrize(
    "kind, relic_argv",
    [
        ("comment", ["relic", "comment", "issue #903 — stale-open sweep"]),
        ("message", ["relic", "message", "design fork answered", "--channel", "telegram"]),
        ("file", ["relic", "file", "/tmp/report.md"]),
    ],
)
def test_the_new_relic_front_doors_keep_a_promise_of_the_same_kind(
    monkeypatch, tmp_path, capsys, kind, relic_argv,
):
    """Stage a promise, keep it through the new subcommand, watch `owed`
    clear — the whole point of #1060 rather than just a parser existing."""
    from brr import relics

    assert _run_cli(monkeypatch, tmp_path, ["promise", kind]) == 0
    assert f"owed 1" in capsys.readouterr().out

    monkeypatch.setenv("BRR_OUTBOX_DIR", str(tmp_path))
    assert cli.main(relic_argv) == 0

    shipped = relics.counts_by_kind(relics.read_reported(tmp_path))
    assert shipped.get(kind) == 1
    plan = promises.blueprint(promises.read(tmp_path), shipped)
    assert plan.owed == {}
    assert plan.kept


def test_cli_refuses_an_unpromisable_word_and_writes_nothing(
    monkeypatch, tmp_path, capsys
):
    assert _run_cli(monkeypatch, tmp_path, ["promise", "banana"]) == 1
    assert not (tmp_path / promises.CONTROL_NAME).exists()
    assert "not promisable" in capsys.readouterr().err


def test_cli_release_requires_a_reason(monkeypatch, tmp_path, capsys):
    """The counter has to exist, and it has to be deliberate.

    Without `--release` the owed line is a soft nag with no counter: it fires
    at every boundary for as long as an abandoned intent sits there, and a
    nag with no counter stops being read. Without `--why`, withdrawal is a
    default rather than a decision.
    """
    assert _run_cli(monkeypatch, tmp_path, ["promise", "pr", "--release"]) == 1
    assert not (tmp_path / promises.CONTROL_NAME).exists()
    assert "--release needs --why" in capsys.readouterr().err

    assert _run_cli(
        monkeypatch, tmp_path,
        ["promise", "pr", "--release", "--why", "superseded by #1042"],
    ) == 0
    row = json.loads((tmp_path / promises.CONTROL_NAME).read_text().splitlines()[0])
    assert row["released"] is True and row["why"] == "superseded by #1042"


def test_cli_rejects_a_nonpositive_count(monkeypatch, tmp_path, capsys):
    assert _run_cli(monkeypatch, tmp_path, ["promise", "pr", "--count", "0"]) == 1
    assert not (tmp_path / promises.CONTROL_NAME).exists()


def test_cli_without_an_outbox_says_so_rather_than_writing_nowhere(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
    monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)
    assert cli.main(["promise", "pr"]) == 1
    assert "no run outbox" in capsys.readouterr().err


# ── The boundary ─────────────────────────────────────────────────────────


def _ctx(tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir(exist_ok=True)
    portal = outbox / "portal-state.json"
    ctx = hooks.HookContext({
        "BRR_OUTBOX_DIR": str(outbox),
        "BRR_PORTAL_STATE": str(portal),
        "BRR_RUN_ID": "run-260803-0737-4l69",
    })
    return ctx, outbox, portal


def _portal(token: str, **extra):
    payload = {
        "change_token": token,
        "run": {"id": "run-260803-0737-4l69"},
        "budget": {"elapsed_seconds": 30, "budget_seconds": 14400},
        # Production always writes both counts (portals.write_portal_state);
        # a missing pending_event_count now reads as the honest ✉? unknown
        # (#1000) and would open the bar for the wrong reason in these tests.
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"events": []},
        "outbound": {},
        "card": {"stale": False, "state": "ok"},
        "resources": {},
        "produce": {"known": True, "counts": {}},
        "name": {"written": True},
    }
    payload.update(extra)
    return payload


def test_an_owed_promise_opens_the_gate_on_a_boundary_nothing_else_moved(
    tmp_path,
):
    """The one boundary the signal exists for.

    Writing `.promises.jsonl` changes nothing the daemon puts into
    portal-state, so a promise line gated on the portal token alone would
    render at exactly no useful moment.
    """
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")

    # Boundary 0 (w-54): a run's first bar renders everything once — burn it.
    hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})

    # Boundary 1: same token, nothing moved — nothing to say.
    first = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert first["inject"] is None

    # Boundary 2: same portal token, but a promise now exists.
    promises.append(outbox, "pr", count=2, ref="the rollout split")
    second = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert second["inject"] is not None
    assert "owed 2" in second["inject"]
    assert "still owed: 2 PRs — the rollout split" in second["inject"]


def test_the_owed_line_and_chip_both_speak_on_the_edge_only(tmp_path):
    """An obligation that repeats every boundary trains the reader to skip it.

    The line speaks on the blueprint's own delta, and since w-54 the chip is
    change-gated too: an unchanged `owed 1` is the number the reader already
    has, so a later boundary where something *else* moved carries neither.
    The standing fact's nets are the blueprint edge, the closeout line, and
    the bolt's own validation at the cut.
    """
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    promises.append(outbox, "pr", count=1)

    first = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "still owed" in (first["inject"] or "")
    assert "owed 1" in (first["inject"] or "")

    # A later boundary where something *else* moved: neither the line nor
    # the unchanged chip repeats.
    portal.write_text(
        json.dumps(_portal("t2", card={"stale": True, "state": "stale",
                                       "age_seconds": 400})),
        encoding="utf-8",
    )
    second = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "card" in (second["inject"] or "")
    assert "owed 1" not in (second["inject"] or "")
    assert "still owed" not in (second["inject"] or "")


def test_the_closeout_says_it_whether_or_not_it_was_said_mid_run(tmp_path):
    """Stop is never latched — it is the moment the feature exists for."""
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(json.dumps(_portal("t1")), encoding="utf-8")
    promises.append(outbox, "pr", count=3, ref="the rollout split")

    mid = hooks.compute_neutral(hooks.PHASE_POST_TOOL, ctx, {})
    assert "still owed" in (mid["inject"] or "")

    stop = hooks.compute_neutral(hooks.PHASE_STOP, ctx, {})
    assert "still owed: 3 PRs" in (stop["inject"] or "")


def test_the_closeout_confirms_a_blueprint_that_was_kept(tmp_path):
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(
        json.dumps(_portal("t1", produce={"known": True, "counts": {"pr": 2}})),
        encoding="utf-8",
    )
    promises.append(outbox, "pr", count=2)
    stop = hooks.compute_neutral(hooks.PHASE_STOP, ctx, {})
    inject = stop["inject"] or ""
    assert "every promise this run made is in its manifest" in inject
    assert "still owed" not in inject


def test_a_run_that_promised_nothing_gets_no_blueprint_line_at_the_closeout(
    tmp_path,
):
    ctx, outbox, portal = _ctx(tmp_path)
    portal.write_text(
        json.dumps(_portal("t1", produce={"known": True, "counts": {"pr": 3}})),
        encoding="utf-8",
    )
    stop = hooks.compute_neutral(hooks.PHASE_STOP, ctx, {})
    inject = stop["inject"] or ""
    assert "blueprint" not in inject
    assert "still owed" not in inject
    assert "owed " not in inject


# ── The baseline: a promise cannot be kept by its own past ───────────────


def test_a_promise_is_not_satisfied_by_produce_that_predates_it():
    """Found by driving the feature on the run that wrote it.

    The run had already opened one PR, then promised two more, and the
    blueprint read *all kept* — counting alone cannot tell produce that
    answers a promise from produce that merely predates it. Without the
    baseline the guard fails in the **optimistic** direction: silent while a
    promise is outstanding.
    """
    rows = [{"what": "pr", "count": 2, "baseline": 1}]
    plan = promises.blueprint(rows, {"pr": 2})
    assert plan.owed == {"pr": 1}
    assert promises.chip(plan) == "owed 1"

    # And it clears the moment the promised work actually lands.
    assert promises.blueprint(rows, {"pr": 3}).owed == {}


def test_a_row_without_a_baseline_keeps_the_old_lenient_behaviour():
    """Hand-written rows, and rows from before the field existed.

    Stated rather than hidden: the fallback is the lenient one, so an
    un-baselined promise behaves exactly as it did before.
    """
    plan = promises.blueprint([{"what": "pr", "count": 2}], {"pr": 2})
    assert plan.owed == {}


def test_the_highest_baseline_of_a_kind_wins():
    """A later promise must not be answered by work that preceded it.

    Two promises for the same kind, made at different times: taking the
    first or the minimum baseline would let the second be satisfied by the
    work that landed between them — the optimistic direction again.
    """
    rows = [
        {"what": "pr", "count": 1, "baseline": 0},
        {"what": "pr", "count": 1, "baseline": 3},
    ]
    plan = promises.blueprint(rows, {"pr": 3})
    assert plan.owed == {"pr": 2}


def test_a_released_row_carries_no_baseline(monkeypatch, tmp_path, capsys):
    """Releasing subtracts a promise; it does not make a claim about produce."""
    (tmp_path / "portal-state.json").write_text(
        json.dumps({"produce": {"counts": {"pr": 7}}}), encoding="utf-8"
    )
    assert _run_cli(
        monkeypatch, tmp_path,
        ["promise", "pr", "--release", "--why", "superseded"],
    ) == 0
    row = json.loads((tmp_path / promises.CONTROL_NAME).read_text().splitlines()[0])
    assert "baseline" not in row


def test_the_front_door_stamps_the_baseline_off_the_live_snapshot(
    monkeypatch, tmp_path, capsys
):
    (tmp_path / "portal-state.json").write_text(
        json.dumps({"produce": {"counts": {"pr": 4, "kb": 1}}}), encoding="utf-8"
    )
    assert _run_cli(monkeypatch, tmp_path, ["promise", "pr"]) == 0
    row = json.loads((tmp_path / promises.CONTROL_NAME).read_text().splitlines()[0])
    assert row["baseline"] == 4
    # And the chip agrees: 4 already there, 1 promised, nothing new yet.
    plan = promises.blueprint(promises.read(tmp_path), {"pr": 4})
    assert promises.chip(plan) == "owed 1"


def test_an_unreadable_snapshot_stamps_no_baseline_rather_than_guessing(
    monkeypatch, tmp_path, capsys
):
    """No snapshot is not a baseline of zero.

    Zero would be a *pessimistic* guess and would look correct — which is
    worse than the lenient fallback, because it would report promises owed
    on runs that had kept them.
    """
    (tmp_path / "portal-state.json").write_text("{not json", encoding="utf-8")
    assert _run_cli(monkeypatch, tmp_path, ["promise", "pr"]) == 0
    row = json.loads((tmp_path / promises.CONTROL_NAME).read_text().splitlines()[0])
    assert "baseline" not in row
