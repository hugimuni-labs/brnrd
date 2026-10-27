"""Tests for the runner hooks back channel (``brnrd hook <phase>``)."""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess


from _helpers import _says
from brr import hooks


def _portal(tmp_path, *, token="t1", pending=0, events=None, scm=None, produce=None,
            resources=None, budget=None, outbound=None, card=None,
            name=None, current_event="evt-1", current_event_replyable=True,
            notices=None, schedule=None, delivery=None, await_state=None):
    # ``current_event`` mirrors production: the daemon always writes the key,
    # set for an addressed run and None for an unaddressed one (a scheduled
    # wake). Pass ``current_event=None`` to model the unaddressed shape — the
    # fixture must be able to express both, or a guard that depends on the
    # distinction can be "green" against a portal state that cannot occur.
    #
    # ``current_event_replyable`` is the daemon's mechanical gate-ownership
    # fact (#562): a schedule wake carries a current event that no gate owns,
    # so ``current_event`` alone cannot express that shape. Pass False to
    # model it.
    payload = {
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {
            "pending_event_count": pending,
            "pending_outbox_file_count": 0,
        },
        "inbound": {
            "current_event": current_event,
            "current_event_replyable": current_event_replyable,
            "events": events or [],
        },
        "outbound": outbound or {
            "replies_current": 0,
            "replies_other": 0,
            "outbound_messages": 0,
        },
        "budget": budget or {"elapsed_seconds": 10, "budget_seconds": 3600},
        "change_token": token,
    }
    if scm is not None:
        payload["scm"] = scm
    if produce is not None:
        payload["produce"] = produce
    if resources is not None:
        payload["resources"] = resources
    if card is not None:
        payload["card"] = card
    if notices is not None:
        payload["notices"] = notices
    if name is not None:
        payload["name"] = name
    if schedule is not None:
        payload["schedule"] = schedule
    if delivery is not None:
        payload["delivery"] = delivery
    if await_state is not None:
        payload["await"] = await_state
    path = tmp_path / "portal-state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _env(tmp_path, flavour="claude"):
    return {
        "BRR_RUN_ID": "run-1",
        "BRR_EVENT_ID": "evt-1",
        "BRR_RUNNER": flavour,
        "BRR_OUTBOX_DIR": str(tmp_path),
        "BRR_PORTAL_STATE": str(tmp_path / "portal-state.json"),
    }


# ── The gate-less routing fact, all four cells (#728) ────────────────────
#
# ``gate_less`` used to be computed and then read only inside the silence
# arm, so the third delivery line below existed in one cell of a
# two-by-two table. These four pin the whole table on the axis the older
# tests above cannot speak to — they predate the line — while those older
# tests keep pinning the first two arms unchanged.
#
#                      | silent                    | delivered
#   -------------------|---------------------------|-------------------------
#   gate-less          | routing fact via the      | routing fact, its own
#                      | silence arm               | line  ← was the gap
#   gate-owned         | dispatch promise          | waking-thread nag
#
_ROUTING = "routing fact, stated once"
_DELIVERED = {"replies_current": 0, "replies_other": 0, "outbound_messages": 1}


def _stop(tmp_path, **kw):
    _portal(tmp_path, pending=0, **kw)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    return out["hookSpecificOutput"]["additionalContext"]


# ── #1142: the "no reply yet" line's consecutive-firing streak ──────────
#
# The Stop hook is a fresh subprocess every firing, so a run stuck re-firing
# Stop with no reply ever landing on its waking thread saw the exact same
# reassuring sentence every time — no way to tell "first firing, informative"
# from "fourth firing, a loop" without a persisted counter. These pin the
# counter (:data:`hooks.STOP_NO_REPLY_STREAK_KEY`) and the wording escalation
# past :data:`hooks._STOP_NO_REPLY_ESCALATE_THRESHOLD`.

_NO_REPLY_YET = "waking thread itself has no reply yet"
_VERDICT = "Probable delivery-path problem"


# ── #1296: the escalated verdict has a ceiling, independent of gate_less ─
#
# run-260810-0728-x41g, reconstructed from its own boundaries.jsonl: a
# schedule-thread continuation woken by a `spawn_completed` event, already
# delivered via `gate: telegram` twice this run (any_delivery=True), never
# accrued a reply on its own waking thread (replies_current=0). The daemon's
# `current_event_replyable` was wrong for that run shape (root cause: fixed
# in daemon.py, see `_spawn_parent_still_collecting`) and this same verdict
# line rendered 6 times in a row across Stop firings before the run ended.
# This test does not assume the root-cause fix — it drives the hooks-side
# ceiling directly, so it still passes even if some future bug makes
# `current_event_replyable` wrong again (the requirement's own "belt and
# suspenders" framing).


# ── Pending-event letter chrome ──────────────────────────────────────────
#
# The boundary-injected pending list, reworked after run-260731-1802-j6ke
# measured three live defects: truncation without accounting, verbatim
# refeed of huge bodies at every boundary, and a Stop fold-in that called a
# schedule firing "from the user".


def _aged_iso(seconds_ago: int) -> str:
    stamp = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(
        seconds=seconds_ago
    )
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


_SHORT_BODY = (
    "every word of a short message survives the boundary intact. " * 9
).strip() + " THE-LAST-WORD"

_HUGE_BODY = "## the only tick\n" + (
    "spec line about the tick cadence and its merged director duties\n" * 160
) + "DEEP-TAIL-MARKER"


# ── Burst collapse (#128: "closely-spaced fragments race separate wakes") ──
#
# A person who types three lines in twenty seconds used to produce three
# separate chrome rows — three tickets the resident had to `event:`-reply
# one and `note:` the other two. Consecutive pending events sharing a
# correspondent and a thread within `conversations.BURST_WINDOW_SECONDS`
# now collapse into one row instead.


def _burst_event(eid, *, body, aged_seconds, chat="555", user="Gurio",
                  username="StasisRush"):
    return {
        "id": eid, "source": "telegram",
        "telegram_chat_id": chat, "telegram_user": user,
        "telegram_username": username,
        "body": body, "created": _aged_iso(aged_seconds),
    }


# ── Config generation (brr-managed per-run native hook config) ───────────


# ── The rooted-write guard (#1184) ────────────────────────────────────────
#
# `daemon._child_git_pin` (#703) pins a strand's *git* to its own worktree,
# but `Edit`/`Write` never touch git — they take a raw absolute path. This
# `pre-tool` phase closes that gap: refuse a write path rooted in the host
# checkout (``BRR_HOST_ROOT``) but outside the strand's own worktree
# (``BRR_WORK_TREE``).
#
# Deliberately not ``GIT_WORK_TREE`` even though the git pin exports exactly
# that name into the same env dict: every ``brnrd hook <phase>`` invocation
# runs through ``cli.main()``, whose first act (``_drop_inherited_git_pin``)
# pops ``GIT_DIR``/``GIT_WORK_TREE`` from ``os.environ`` before anything else
# runs, so the raw variable never reaches this subprocess in production —
# only the ``BRR_``-namespaced copy the daemon arms alongside it survives.
# Found live: an early cut of this guard read ``GIT_WORK_TREE`` directly and
# passed every unit test here (which hand-build their env dict and so never
# exercise the scrub) while never actually arming in a real `brnrd hook
# pre-tool` subprocess.


# ── The closeout guard (`hooks.next_move`) ───────────────────────────────
#
# The contract `next_move` failed 0/6 across *both* arms of the drift bench —
# prose and mounted alike. Position could not fix it, because position was never
# the problem: the contract is read at wake and spent 60 turns later, at the one
# moment the model is busy ending. This is the escalation ladder's last rung —
# a contract prose cannot keep becomes code that cannot fail silently.


def _armed(tmp_path, flavour="claude"):
    env = _env(tmp_path, flavour)
    env["BRR_NEXT_MOVE_GUARD"] = "1"
    return env


def _stdin(reply=None, **extra):
    payload = dict(extra)
    if reply is not None:
        payload["last_assistant_message"] = reply
    return json.dumps(payload)


# ── Escalated artifact obligations (card) ─────────────────────────────────

_GOOD_REPLY = "wired it up.\n\n**done** — committed abc1234 on brr/x"


def _armed_obl(tmp_path, obligations="card", flavour="claude"):
    env = _armed(tmp_path, flavour)
    env["BRR_CLOSEOUT_OBLIGATIONS"] = obligations
    return env


# ── The SCM closeout obligation (host work-loss block) ────────────────────


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def _seeded_repo(tmp_path):
    """A git repo on `main` with one seed commit. Returns (repo_dir)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo


# ── The `gate` closeout obligation (ran-the-project's-own-CI block) ───────


def _gate_receipt(tmp_path, repo, **overrides):
    """Write the receipt `scripts/gate.py` would write for `repo`'s current
    tree, into `repo`'s own slot of the outbox's receipts map (#820) — the
    same map `hooks._gate_closeout_clause` reads via
    `gate_receipt.read_receipt(outbox_dir, ctx.repo_dir)`."""
    from brr import gate_receipt

    def out(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
        ).stdout

    payload = {
        "head": out("rev-parse", "HEAD").strip(),
        "status": out("status", "--porcelain"),
        "diff_digest": hashlib.sha256(
            out("diff", "HEAD").encode("utf-8", "replace")
        ).hexdigest(),
        # Agreement between this and `scripts/gate.py`'s own writer is pinned
        # in `test_gate_runner.py`, against a real repo — not assumed here.
        "untracked_digest": hooks._untracked_digest(repo),
        "verdict": "GREEN",
        "legs": [],
    }
    payload.update(overrides)
    receipt_path = tmp_path / hooks.GATE_RECEIPT_NAME
    data = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
    data[gate_receipt.tree_key(repo)] = payload
    receipt_path.write_text(json.dumps(data), encoding="utf-8")
    return payload


def _armed_gate(tmp_path, repo, command="python scripts/gate.py"):
    """Arm only the `gate` obligation, card already satisfied, so a block can
    come from nowhere else."""
    (tmp_path / hooks.CARD_NAME).write_text("progress", encoding="utf-8")
    env = _armed_obl(tmp_path, obligations="gate")
    env["BRR_REPO_DIR"] = str(repo)
    env["BRR_SEED_REF"] = "main"
    env["BRR_GATE_COMMAND"] = command
    return env


def test_gate_silent_when_the_receipt_matches_the_tree(tmp_path):
    """Ran, on this tree ⇒ nothing owed. The receipt is the whole interface."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _gate_receipt(tmp_path, repo)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


def test_gate_silent_on_a_red_receipt_for_this_tree(tmp_path):
    """The obligation is *the gate ran*, never *the gate was green*. A run may
    end red and report it; a run that never looked is the defect."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _gate_receipt(tmp_path, repo, verdict="RED")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


def test_gate_blocks_when_the_tree_moved_after_the_receipt(tmp_path):
    """A green verdict for a tree you have since edited is a claim about code
    nobody ran — the exact shape of the failure this guard exists for."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _gate_receipt(tmp_path, repo)
    (repo / "feature.py").write_text("x\nthe one-line fix nobody re-ran\n", encoding="utf-8")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "a different tree" in out["reason"]


def test_gate_blocks_when_a_commit_landed_after_the_receipt(tmp_path):
    """Committing does not change `status --porcelain` or `diff HEAD` — both go
    back to empty — so HEAD is the referent that has to catch this. Without it
    a receipt taken on a dirty tree would validate the commit of that tree,
    which is *almost* right and therefore the dangerous kind of wrong."""
    repo = _seeded_repo(tmp_path)
    _git(repo, "switch", "-qc", "brr/work")
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    _gate_receipt(tmp_path, repo)
    (repo / "feature.py").write_text("y\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "two")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "a different tree" in out["reason"]


def test_gate_blocks_when_the_tree_moved_while_the_gate_was_running(tmp_path):
    """#917, the third state: the receipt describes exactly the tree you are
    ending on — so every check above it is satisfied — and it still does not
    certify that tree, because a file landed *during* the legs. Before this,
    the guard was silent here: both writers sampled only after the last leg,
    and this clause recomputed that same end state and found it matching."""
    from brr import gate_receipt

    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    before = gate_receipt.tree_referents(repo)          # the gate starts
    (repo / "written-mid-gate.py").write_text("no leg saw this\n", encoding="utf-8")
    tree = gate_receipt.tree_fields(repo, before)       # the gate finishes
    assert tree["tree_moved_during_gate"] is True

    _gate_receipt(tmp_path, repo, **tree)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    # It names the actual cause and the actual file. A remedy aimed at the
    # wrong cause is worse than none, so the two older sentences must not
    # appear — neither is true here.
    assert "written-mid-gate.py" in out["reason"]
    assert not _says(out["reason"], "the gate never ran")
    assert not _says(out["reason"], "a different tree than the one you are ending on")
    # Composition, not just content: the clause is placed as a whole sentence
    # and the next one starts cleanly after it.
    assert _says(out["reason"], "written-mid-gate.py changed during `python scripts/gate.py`, so no "
        "leg ever saw it. The receipt's GREEN is about the tree")


def test_gate_silent_on_a_receipt_that_records_a_still_tree(tmp_path):
    """The honest path with the new field present: the writer checked, the
    tree held, nothing owed."""
    from brr import gate_receipt

    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    tree = gate_receipt.tree_fields(repo, gate_receipt.tree_referents(repo))
    assert tree["tree_moved_during_gate"] is False

    _gate_receipt(tmp_path, repo, **tree)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


def test_gate_silent_on_a_receipt_too_old_to_carry_the_field(tmp_path):
    """Absence is unassertable, not guilty. Every receipt written before this
    shape existed lacks the field, and a guard that read absence as a moved
    tree would fire on all of them — which is how a guard stops being read."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    payload = _gate_receipt(tmp_path, repo)
    assert "tree_moved_during_gate" not in payload
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


def test_gate_names_a_file_removed_while_the_gate_was_running(tmp_path):
    """The pair is diffed *both* ways. A file created during the gate appears
    only in the after capture; one removed during the gate appears only in the
    before capture, and looking one way named the first while silently missing
    the second — falling through to a sentence about "a file that was already
    dirty", which a deletion is not."""
    from brr import gate_receipt

    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    (repo / "scratch.py").write_text("deleted before the gate ended\n", encoding="utf-8")
    before = gate_receipt.tree_referents(repo)
    (repo / "scratch.py").unlink()
    tree = gate_receipt.tree_fields(repo, before)
    assert tree["tree_moved_during_gate"] is True

    _gate_receipt(tmp_path, repo, **tree)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "scratch.py" in out["reason"]
    assert "already dirty" not in out["reason"]


def test_gate_moved_tree_message_falls_back_to_the_referent_it_cannot_name(tmp_path):
    """A *second* edit to a file that was already ` M` when the gate started
    leaves its `status --porcelain` line byte-identical, so no filename is
    derivable from the pair. The sentence names the referent that moved
    rather than inventing a path.

    This branch is pinned **through the hook**, not just against the helper,
    because the defect it caught lived in neither half: `moved_sentence`'s
    ancestor returned a bare noun when it could name files and a whole clause
    when it could not, and the hook interpolated it mid-sentence as though it
    were always a noun. The filename branch read fine and this one produced
    word salad. A helper tested alone would have passed."""
    from brr import gate_receipt

    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature")
    (repo / "feature.py").write_text("x\ndirty before the gate\n", encoding="utf-8")
    before = gate_receipt.tree_referents(repo)
    (repo / "feature.py").write_text("x\nand again, under the gate\n", encoding="utf-8")
    tree = gate_receipt.tree_fields(repo, before)
    assert tree["tree_moved_during_gate"] is True

    _gate_receipt(tmp_path, repo, **tree)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "diff_digest" in out["reason"]
    assert _says(out["reason"], "no path in `git status` changed during `python scripts/gate.py`, but "
        "diff_digest did — content moved under a path that was already dirty "
        "when the gate started. The receipt's GREEN is about the tree")
    # The garble this shape exists to prevent, spelled out so a future edit
    # that reintroduces noun-vs-clause has to walk past it.
    assert "started changed" not in out["reason"]


def test_gate_a_receipt_for_a_different_tree_does_not_satisfy_this_one(tmp_path):
    """#820: one run that gates two trees under one outbox — this repo's own
    documented `host` pattern, a scratch `git worktree add` plus the checkout
    — must not have the second, correct gate's receipt read as covering the
    first tree, or vice versa. A receipt keyed to another tree is a different
    question's answer, neither a pass nor a failure for this guard."""
    repo = _seeded_repo(tmp_path)
    (tmp_path / "other-tree").mkdir()
    other = _seeded_repo(tmp_path / "other-tree")
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    # Gate `other`'s tree — a real, matching, GREEN receipt, just not for the
    # tree this guard is asking about.
    _gate_receipt(tmp_path, other)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert _says(out["reason"], "the gate never ran")
    # And once `repo` gets its own entry beside `other`'s, both survive and
    # this guard reads only its own.
    _gate_receipt(tmp_path, repo)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


# ── The `vigil` closeout obligation (#947: a claimed vigil must be armed) ─
#
# Two live instances on 2026-08-01: a run ended on `Holding for the integration
# gate.` and another on `continuing — … vigil armed (keepalive live…)` with no
# `.keepalive` on disk. Both then exited, and both times the maintainer waited
# on a run that was already `done`.


_HOLDING = "Merged #948 and #950.\n\nHolding for the integration gate."
_CONTINUING = (
    "Pushed the branch.\n\n"
    "continuing — combined gate (bp4w3plg6) still running; vigil armed"
)


# ── Slice 8 (#513): the agnoster mid-run status bar ──────────────────────


def _bar_payload(**overrides):
    """A fully-laden post-tool payload — every bar segment has something to
    show. Individual tests knock pieces out via ``overrides`` to exercise the
    quiet/partial shapes."""
    until = (
        datetime.datetime.now(tz=datetime.timezone.utc)
        + datetime.timedelta(hours=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "run": {"id": "run-260723-1241-3jy8"},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"events": []},
        "budget": {
            "elapsed_seconds": 16 * 60, "budget_seconds": 120 * 60,
            "keepalive": {"status": "active", "until": until},
        },
        "outbound": {"replies_current": 2, "replies_other": 3,
                     "outbound_messages": 0},
        "produce": {"known": True, "counts": {"commit": 3, "kb": 1}},
        "card": {"active": True, "stale": False},
        "resources": {
            "quota": {
                "status": "known",
                "summary": (
                    "session 57% left (resets 8:30pm (Europe/Berlin)); "
                    "week 50% left (resets Jul 24, 12am (Europe/Berlin)); "
                    "Fable week 27% left"
                ),
            },
            "coexisting_runs": {
                "status": "known",
                "siblings": [{"run_id": "run-x"}],
            },
        },
    }
    for key, value in overrides.items():
        payload[key] = value
    return payload


# ── Armed dated-letters block (#904) ──────────────────────────────────


# ── Armed-letter obligation classification (#1209) ────────────────────────
#
# `_has_post_tool_obligations`'s armed-letters clause used to treat mere
# *presence* of an armed `at:` entry as an obligation — so a letter hours
# out rendered the OBLIGATION-classed block at every boundary of the whole
# run, defeating the quiet-boundary economy (#1116) until it fired. The fix
# scopes the clause to overdue-or-imminent entries only; the always-shown
# render (`_render_armed_rows`, exercised above) is untouched.


# ── The `.mood` control channel (#566 layer 2) ───────────────────────────


def test_emote_glyph_degrades_to_none_for_an_unresolvable_name():
    """A handle with no library entry degrades silently — never raises.

    This test used to read "`brr.emotes` (#566) does not exist in this tree
    yet" and assert no glyph for **`bo_Od`** — which is a real entry in the
    shipped library. So once #601 landed it was no longer testing degradation
    at all: it was pinning the broken resolver's output as correct. A fixture
    that quietly becomes valid input is how a negative test turns into a lock
    on the bug. The handle below is invented on purpose and stays invented.
    """
    from brr import emotes

    name = "not_an_emote_xyz"
    assert name not in emotes.EMOTES, "fixture must stay unresolvable"
    assert emotes.lookup(name) is None, "fixture must stay unresolvable"
    assert hooks._emote_glyph(name) is None
    # Degrades, but no longer *silently* — and no longer *graded*: the word
    # renders plain, with the honest "no face" the design page asks for
    # (rule 1), never a `✗` grading the resident's own claimed mood.
    assert hooks._mood_chip(name) == f"{name} · no face"


def test_a_family_word_renders_its_default_face():
    """A synonym is wearable via the word it now resolves to.

    Pre-rework, ``family`` was a distinct grouping name a synonym could
    equal; post-rework every situational face's ``family`` equals its own
    ``name`` (``test_every_situational_face_declares_its_family``), so a
    synonym's ``family`` is the *target* word's name, not the synonym
    itself. ``satisfied`` resolves to ``pleased``.
    """
    from brr import emotes

    name = "satisfied"
    resolved = emotes.lookup(name)
    assert resolved is not None
    assert resolved.name == "pleased"
    assert resolved.family == "pleased"
    chip = hooks._mood_chip(name)
    assert chip == f"{resolved.resting_frame} satisfied"


def test_the_word_for_the_feeling_renders_the_same_chip_as_the_handle():
    """``focused`` is what a run writes; ``fo.cus`` is what the file calls it.

    This is the whole reported bug in one assertion. ``.mood`` is a
    machine-parsed channel, the handles were minted as weave marks, and the
    parser matched them byte for byte — so the obvious spelling published
    nothing and the dashboard printed the raw string. Both spellings are one
    face now, and the chip proves it end to end rather than at the library
    boundary where the mismatch was invisible.
    """
    from brr import emotes

    assert emotes.lookup("focused") is emotes.lookup("fo.cus")
    assert hooks._mood_chip("focused") == f"{emotes.glyph('fo.cus')} focused"


def test_a_real_emote_handle_renders_its_face_in_the_chip():
    """The seam between the statusline (#603) and the emote library (#601).

    Every other mood test in this file names an *invented* handle — "stoked",
    "curious", "bo_Od" — so "no glyph" was the expected output everywhere and
    a broken resolver was indistinguishable from a green suite. It was broken:
    ``hooks._emote_glyph`` called ``emotes.glyph``, a function the shipped
    library never had, and the caller's deliberately broad ``except`` ate the
    ``AttributeError`` on every boundary. The face never rendered, for anyone,
    ever — which is exactly what a guard swallowing its own signal buys you.

    So this test uses a handle that really is in ``EMOTES``. It is the only
    one here that can fail if the seam breaks again.
    """
    from brr import emotes

    name = "fo.cus"
    assert emotes.lookup(name) is not None, "fixture handle must be a real emote"

    expected = emotes.glyph(name)
    assert expected, "a resolvable handle must yield a resting-frame glyph"
    resolved_name = emotes.lookup(name).name
    assert expected == emotes.EMOTES[resolved_name].resting_frame, (
        "resting glyph is resting_frame, not the shared animation base"
    )

    assert hooks._emote_glyph(name) == expected
    assert hooks._mood_chip(name) == f"{expected} {name}"


# ── the resident seat's own standing allowance (design-the-allowance.md
# §2, slice 2): same facet shape, `scope: "resident"` changes the render ──


# ── draws — attribution of the shared quota gauge (brnrd#1810, design-
# the-seat-that-never-quits.md §"The measurement") ────────────────────────


# ── hold (design-the-seat-that-never-quits.md §"The machinery, in
# slices" #3) ────────────────────────────────────────────────────────────


# ── spawn_refused — a refused admission, named beside `▷` (2026-09-14, his
# ask: "you need visibility" — a dispatch he could not check `notices`
# against was reported "dispatched" 2026-09-09) ─────────────────────────


# ── context_window: live before the final envelope, per-Shell (brnrd#1810,
# design-the-seat-that-never-quits.md §slice 4) ──────────────────────────


# ── Mood asks on the edge, not on the tick (2026-07-23) ──────────────────


# ── #616: notices segment and spawn_completed closeout rendering ─────────────


# ── #1002: a notice carries a `kind`, and only `refused`/`dropped` count ─────


# ── #716: `lifetime` splits standing environmental notices out of `!N` ───────


# ── notices: the seed/stop briefing spells the text, not just the count ──────
#
# The count already had a home (the ``!N`` bar segment, #616). What did not:
# ``notices`` was read on the seed/stop path and then never rendered, so the
# two verbose boundaries — including Stop, the last one at which a dropped
# reply can still be re-routed — said nothing about a refusal at all. A
# refused outbox file is deleted exactly like an accepted one, so the notice
# is the only trace there is.


# ── The wake census (#739, the reading half of #683) ─────────────────────
#
# Same fixture discipline as the orientation ledger above (#611): every
# assertion of absence sits beside a positive twin on the same input shape,
# so "no census rendered" can never be green against an input that could not
# have produced one.


_CENSUS_BLOCKS = [
    {"block_key": "work-surface", "label": "Discovered work surface",
     "bytes": 24716, "present": True, "oldest_item": "2026-07-25 21:18"},
    {"block_key": "dominion", "label": "Dominion digest",
     "bytes": 21521, "present": True, "oldest_item": None},
    {"block_key": "weave", "label": "Working register",
     "bytes": 5524, "present": True, "oldest_item": "2026-07-26 09:00"},
    # Present in scope, silent in this wake: must not win `top` on a stale
    # byte count from a block that never rendered.
    {"block_key": "diffense", "label": "Diffense pack",
     "bytes": 99999, "present": False, "oldest_item": None},
]


_CENSUS_BLOCKS_WITH_AUTHORITY = [
    {"block_key": "work-surface", "label": "Discovered work surface",
     "bytes": 51_200, "present": True, "authority": "surface"},
    {"block_key": "notes-health", "label": "notes health",
     "bytes": 28_672, "present": True, "authority": "health"},
    {"block_key": "kb-health", "label": "kb health",
     "bytes": 1_024, "present": True, "authority": "health"},
    {"block_key": "identity-core", "label": "Resident identity core",
     "bytes": 6_079, "present": True, "authority": "identity"},
]


# ── Boundary transcript ──────────────────────────────────────────────────
#
# The wake capture (`prompt.md`) has always been written and the boundaries
# never were, so the only inspectable record of a run's environment stopped at
# t=0. These pin the other half.


def _transcript_env(tmp_path):
    """An env whose run directory is *not* the outbox directory.

    Deliberately distinct: the transcript is anchored on ``BRR_BOOT_SCORE``'s
    parent so it lands beside ``prompt.md``, and a fixture that collapses the
    two paths cannot tell a correct implementation from one that writes into
    the outbox (where the daemon's drain and the closeout capture would both
    see a file they know nothing about).
    """
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    env = _env(tmp_path)
    env["BRR_BOOT_SCORE"] = str(run_dir / "boot-score.json")
    return env, run_dir


def _transcript(run_dir):
    path = run_dir / "boundaries.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── Tool detail, redaction, and out_bytes ─────────────────────────────────
#
# Tests for the ``detail`` and ``out_bytes`` fields added to boundary records.
# Driven through the real hook entry point so the full write path is covered.


# ── The bead and its coordinates (ctx / spend / quota / place) ────────────


def test_boundary_row_keeps_a_codex_runs_own_buckets(tmp_path):
    """The row used to be seeded `{"S","W","F"}` and filled only from that
    set, so a *codex* run recorded three null claude slots while holding a
    perfectly good `5h`/`7d` reading (measured 2026-09-19 across every
    `codex-gpt-6-astra` row on this account). The summary here is built by
    codex's own parser from a `token_count` payload in the shape the Shell
    emits — not a hand-written string that could drift from production's."""
    from brr import codex_status, facets

    levels = codex_status.parse_token_count({
        "type": "token_count",
        "rate_limits": {
            "primary": {
                "used_percent": 100.0, "window_minutes": 300,
                "resets_in_seconds": 9000,
            },
            "secondary": {
                "used_percent": 16.0, "window_minutes": 10080,
                "resets_in_seconds": 600000,
            },
        },
        "info": {
            "total_token_usage": {"input_tokens": 140090, "output_tokens": 504},
            "model_context_window": 272000,
        },
    }, "2026-09-19T10:09:26Z")
    resources = facets.build(
        levels=levels,
        levels_collector=codex_status.COLLECTED_SLOTS,
        runner_name="codex-gpt-6-astra",
        allowance={"tokens": 5_000_000, "spent": 495_141, "scope": "strand"},
    )
    assert resources["quota"]["status"] == "known"

    env, run_dir = _transcript_env(tmp_path)
    _portal(tmp_path, token="t1", resources=resources)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    row = _transcript(run_dir)[-1]["quota"]
    # H = the 5h window, D = the 7d one — the chip's own letters, and the
    # 0 is the number four codex strands died without ever being shown.
    assert row == {"H": 0, "D": 84}
    assert "S" not in row and "W" not in row and "F" not in row


def test_redact_detail_masks_authorization_header():
    """Authorization header values are masked regardless of case."""
    result = hooks.redact_detail("curl -H 'Authorization: Bearer mytoken123'")
    assert "mytoken123" not in result
    assert "<redacted>" in result


def test_redact_detail_masks_token_assignment():
    """``token=VALUE`` and ``secret=VALUE`` assignments are masked."""
    assert "<redacted>" in hooks.redact_detail("export TOKEN=super_secret_value")
    assert "<redacted>" in hooks.redact_detail("secret=topsecret cmd")


def test_redact_detail_masks_known_api_key_prefix():
    """Values starting with known API-key prefixes (sk-ant-, ghp_) are masked."""
    assert "<redacted>" in hooks.redact_detail("--key sk-ant-abc123xyz456")
    assert "<redacted>" in hooks.redact_detail("echo ghp_PAT1234567890")


def test_redact_detail_preserves_innocent_text():
    """Normal command lines that contain no secrets are returned unchanged."""
    cmd = "pytest tests/ -q --tb=short"
    assert hooks.redact_detail(cmd) == cmd


# ── Boundary summary (`boundaries.json`) ──────────────────────────────────
#
# `derive_boundaries_summary` reads the exact transcript `record_boundary`
# writes, so these fixtures are driven through the real hook entry point
# (`hooks.run_hook`) rather than hand-assembled — the same discipline the
# transcript tests above already keep. Only the malformed-line case appends
# synthetic damage on top of a real file, because that damage cannot be
# produced any other way.


# ── #1113: a row that asks for a reply nobody receives ──────────────────────


# ── #1116: the typed channel ────────────────────────────────────────────────


def test_every_bar_segment_declares_its_class():
    """The class is structural, not a list (#1118's lesson, applied).

    `_BarSegment.klass` has no default, so a segment added later has to
    answer *"what turns this off?"* rather than inherit an answer — and the
    cheap inherited answer would be AMBIENT, which is exactly how a new
    obligation would go silent on the boundary it exists for.
    """
    for segment in hooks.BAR_SEGMENTS:
        assert segment.klass in hooks._SEGMENT_CLASSES, segment.key
    # And every key the renderer can emit is classified, including the
    # inline `✉?` chip that has no vocabulary entry.
    assert set(hooks.SEGMENT_CLASS) >= {s.key for s in hooks.BAR_SEGMENTS}
    assert hooks.SEGMENT_CLASS["pending_unknown"] == hooks.WAITING


# ── w-34 (2026-09-11): the renderer obeys the class ─────────────────────────


# ── The in-process subagent boundary (#1095) ─────────────────────────────
#
# The payloads below are *captured*, not invented: dumped from live claude
# `PostToolBatch` hook fires on 2026-08-05 (run-260805-1041-c1b3), one from
# the resident's own boundary and one from an `Agent`-tool subagent's, with
# the ids shortened. They are the evidence the discriminator rests on — every
# other field, `session_id` and `transcript_path` included, was byte-identical
# across the two, because the child shares the parent's session.

_PARENT_PAYLOAD = {
    "cwd": "/home/gurio/src/misc/brr",
    "effort": {"level": "high"},
    "hook_event_name": "PostToolBatch",
    "permission_mode": "bypassPermissions",
    "prompt_id": "e738ebed-c29b-4e74-9397-fb9d3a28f305",
    "session_id": "46435b5d-b82e-4fb6-9a2c-b3f382c0bd44",
    "tool_calls": [],
    "transcript_path": "/home/gurio/.claude/projects/x/46435b5d.jsonl",
}

_SUBAGENT_PAYLOAD = {
    "agent_id": "aaa08adde62eae409",
    "agent_type": "Explore",
    "cwd": "/home/gurio/src/misc/brr",
    "hook_event_name": "PostToolBatch",
    "permission_mode": "bypassPermissions",
    "prompt_id": "e738ebed-c29b-4e74-9397-fb9d3a28f305",
    "session_id": "46435b5d-b82e-4fb6-9a2c-b3f382c0bd44",
    "tool_calls": [],
    "transcript_path": "/home/gurio/.claude/projects/x/46435b5d.jsonl",
}


# ── The bolt (design-the-bolt.md §Accretion): the boundary gauge ─────────────


# ── Compression-on-repeat (#1116 residue, design-the-live-loop.md §1) ────────


# ── w-54: the line that learned silence ──────────────────────────────────
#
# The 2026-08-19 statusline redesign (evt-…-xw3c, signed "lets build this
# shape"): a static `⌁[<mood>]:` preamble, every chip change-gated, a
# healthy card silent, no run-id chip, the ticker limitless by default, and
# the course drift trigger — the reminder keyed to evidence of divergence
# (work moved, route didn't) instead of a clock.


def test_bar_preamble_forms():
    # The one static element: the bolt, then the resident's own face.
    # Never a face-shaped error (design-the-pre-attentive-channel.md rule 2):
    # no mood, and an unresolved one, both render the brand at rest —
    # `⌁[✗]:` is gone, on purpose.
    assert hooks._bar_preamble(None) == "⌁[b·_·d]:"
    assert hooks._bar_preamble("") == "⌁[b·_·d]:"
    # A real handle renders its own still (#601 seam, resting-frame rework):
    # `bo_Od` is a legacy alias for `curious`, whose still is `b·o·d`.
    assert hooks._bar_preamble("bo_Od") == "⌁[b·o·d]:"
    # An unresolved handle is the same neutral face, not an error mark — the
    # word itself (never the ornament) is where the miss is visible, and
    # only when `named=True` fires it.
    assert hooks._bar_preamble("no-such-face") == "⌁[b·_·d]:"
    # A handle whose still differs from the rest glyph proves the bracket
    # really does carry the resolved face, not always the same placeholder.
    from brr import emotes

    assert emotes.glyph("ugh_") == "b¬_¬d"
    assert hooks._bar_preamble("ugh_") == "⌁[b¬_¬d]:"


# ── 2026-08-23: render-on-change (boundary dedup) ─────────────────────


# ── the wait-by-returning trap (design-the-seat-that-never-quits.md §The tool trap) ──


# ── the seat that never quits: a turn end is a park, not a close ──────────


# ── A seat is an entity, not a conversation (2026-09-12) ────────────────
#
# `linger` used to be armed by the daemon only for a `source: cloud` wake, so
# the one shape it could never catch was a schedule tick that acquired a live
# human halfway through its turn: `run-260912-0824-cicv` sent the maintainer
# four chat replies and then closed on an honest `done — clean handoff` with
# nothing armed to stop it.
#
# The first fix read the *room* at Stop instead (an attached correspondent, a
# reply sent, a replyable event). He named that as the same mistake one layer
# down — "the seat is not how it was spawned, but rather whether there is an
# entity here (or just workers); the seat should never quit, no matter how it
# was spawned" — so the arming is unconditional for a non-strand seat and the
# clause asks every one of them the same question: is a wait armed?


# ── a strand never waits by returning (2026-09-06, three deaths) ───────────


# ── The correspondent's presence on the bar (design-the-continuous-seat
# §Presence) ─────────────────────────────────────────────────────────


# ── #1200: notes-health findings fire on the boundary they become new ────


