"""Tests for bare ``brnrd`` — the narrated front door.

What these pin, in one sentence each: the empty argv reaches the door and
*only* the empty argv does; the door announces exactly the command it then
runs; it never touches ``input()`` or a mutating verb without a terminal;
and it never redoes a step that is already standing.
"""

from __future__ import annotations

import builtins
from pathlib import Path
import subprocess

import pytest

from brr import account, front_door
from brr.cli import main

from _helpers import init_git_repo


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "proj"
    init_git_repo(root)
    monkeypatch.chdir(root)
    return root


@pytest.fixture(autouse=True)
def _no_stray_input(monkeypatch):
    """A prompt nobody asked for is the failure this suite exists to catch.

    Every test that *wants* a prompt replaces this; the rest inherit a
    hard error, so a step that slipped past its TTY gate fails loudly here
    instead of hanging a CI run on a blocking read.
    """
    def _refuse(*_args, **_kwargs):
        raise AssertionError("the front door asked a question it should not have")

    monkeypatch.setattr(builtins, "input", _refuse)


def _answering(text: str):
    """An ``input`` stub that echoes its prompt, the way a terminal does.

    ``input(prompt)`` writes the prompt to stdout itself, so a stub that
    only returns an answer makes the question invisible to ``capsys`` —
    and a test asserting on question text would then be asserting on
    nothing. This keeps the visible half visible.
    """
    def _input(prompt: str = "") -> str:
        print(prompt, end="")
        return text

    return _input


def _all_configured(monkeypatch, *, connected=True, doors=("telegram",), runner_found=True):
    """Report the machine as set up, without building real gate state.

    Fakes the three predicates the door reads for a machine's standing —
    a runner on PATH, a connected account, a configured door — so the test
    stays about orchestration rather than each gate's on-disk layout, which
    its own suite already owns.

    The runner predicate is mocked *deliberately*, not incidentally: it
    reads the real PATH (``runner.detect_all_runners``), so a suite that
    left it live passes on a dev box that happens to have ``claude`` /
    ``codex`` installed and fails on CI, which has neither — the exact
    environment-dependent green that let this door merge-red the first time.
    """
    from brr import runner
    from brr.gates import cloud
    from brr.gates import runtime as gate_runtime

    monkeypatch.setattr(
        runner, "detect_all_runners",
        lambda _repo_root: ["claude"] if runner_found else [],
    )
    monkeypatch.setattr(cloud, "is_configured", lambda _brr_dir: connected)
    monkeypatch.setattr(gate_runtime, "configured_gates", lambda _brr_dir: list(doors))


# ── Dispatch ────────────────────────────────────────────────────────


def test_bare_argv_opens_the_front_door(monkeypatch):
    calls = []
    monkeypatch.setattr(front_door, "run", lambda: calls.append(True) or 7)
    assert main([]) == 7
    assert calls == [True]


def test_a_verb_still_takes_the_ordinary_cli_path(monkeypatch):
    """`brnrd <verb>` must not be re-routed — the door is the empty argv only."""
    monkeypatch.setattr(
        front_door, "run",
        lambda: pytest.fail("a named verb reached the front door"),
    )
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


def test_a_mistyped_verb_is_still_an_argparse_error(monkeypatch):
    """The regression this guards: making the door an argparse *default*
    would turn every typo into a guided setup instead of an error."""
    monkeypatch.setattr(
        front_door, "run",
        lambda: pytest.fail("a mistyped verb reached the front door"),
    )
    with pytest.raises(SystemExit) as exc:
        main(["conect"])
    assert exc.value.code == 2


# ── The narration is not a lie ──────────────────────────────────────


def test_the_announced_command_is_the_command_that_runs(repo, capsys, monkeypatch):
    """The one promise the whole door rests on.

    Not "a command ran" — *the announced one* ran, with the options that
    argv parses to. Read the announcement back off stdout, re-parse it, and
    require the namespace that reached the command to be that namespace;
    an announcement that drifts from its own execution (a silently added
    flag, a different verb) fails here and nowhere else.
    """
    from brr import cli

    ran = []
    monkeypatch.setattr(cli, "cmd_gate_list", lambda args: ran.append(args) or 0)

    front_door._invoke(["gate", "list"])

    line = capsys.readouterr().out.strip()
    assert line.startswith("$ brnrd "), line
    announced = line.removeprefix("$ brnrd ").split()
    assert announced == ["gate", "list"]

    assert len(ran) == 1, "the announced argv did not reach its own command"
    executed = {k: v for k, v in vars(ran[0]).items() if k != "passthrough"}
    assert executed == vars(cli.build_parser().parse_args(announced))


# ── No terminal, no blocking, no mutation ───────────────────────────


def test_no_terminal_runs_nothing_and_names_every_command(repo, capsys, monkeypatch):
    monkeypatch.setattr(front_door, "interactive", lambda: False)
    monkeypatch.setattr(
        front_door, "_invoke",
        lambda argv: pytest.fail(f"ran {argv} with no terminal behind it"),
    )
    _all_configured(monkeypatch, connected=False, doors=())

    code = front_door.run()

    out = capsys.readouterr().out
    assert "not a terminal — reading state only, running nothing" in out
    assert "$ brnrd account connect" in out
    assert "$ brnrd gate setup telegram" in out
    assert code == 1


# ── Idempotence ─────────────────────────────────────────────────────


def test_a_finished_repo_is_a_receipt_not_a_setup(repo, capsys, monkeypatch):
    (repo / "AGENTS.md").write_text("# contract\n", encoding="utf-8")
    monkeypatch.setattr(front_door, "interactive", lambda: True)
    monkeypatch.setattr(
        front_door, "_invoke",
        lambda argv: pytest.fail(f"re-ran {argv} on an already-set-up repo"),
    )
    _all_configured(monkeypatch)

    code = front_door.run()

    out = capsys.readouterr().out
    assert "already connected" in out
    assert "already written" in out
    assert "all set" in out
    assert code == 0


# ── The closing offer ───────────────────────────────────────────────


def test_the_setup_offer_queues_the_first_run(repo, capsys, monkeypatch):
    from brr import connect_greeting

    monkeypatch.setattr(front_door, "interactive", lambda: True)
    monkeypatch.setattr(builtins, "input", _answering("y"))
    _all_configured(monkeypatch)

    queued = []

    def _queue(repo_root, brr_dir):
        queued.append((repo_root, brr_dir))
        return connect_greeting.GreetingOutcome(
            queued=True, event_id="evt-test", door="telegram",
        )

    monkeypatch.setattr(connect_greeting, "queue_greeting", _queue)

    code = front_door.run()

    out = capsys.readouterr().out
    assert "run setup now?" in out
    assert "queued over telegram (evt-test)" in out
    assert len(queued) == 1
    assert code == 0


def test_declining_the_offer_changes_nothing(repo, capsys, monkeypatch):
    from brr import connect_greeting

    monkeypatch.setattr(front_door, "interactive", lambda: True)
    monkeypatch.setattr(builtins, "input", _answering("n"))
    monkeypatch.setattr(
        connect_greeting, "queue_greeting",
        lambda *_a, **_k: pytest.fail("declining the offer still queued a run"),
    )
    _all_configured(monkeypatch)

    code = front_door.run()

    assert code == 1
    assert "skipped" in capsys.readouterr().out


def test_an_unreadable_answer_skips_instead_of_guessing(repo, capsys, monkeypatch):
    """Found in a live terminal, not in review: answering the *which door*
    question with ``n`` — the vocabulary of the yes/no question above it —
    used to fall back to the named default and open Telegram's token
    interview. An answer we could not read is not consent to act."""
    monkeypatch.setattr(front_door, "interactive", lambda: True)
    monkeypatch.setattr(builtins, "input", _answering("n"))
    monkeypatch.setattr(
        front_door, "_invoke",
        lambda argv: pytest.fail(f"ran {argv} on an answer it could not read"),
    )
    # ``connected=False``: a connected account now passes the doors step
    # outright (the cloud-managed door), so the which-one interview this
    # test exercises only exists on the machine that has no cloud.
    _all_configured(monkeypatch, connected=False, doors=())

    code = front_door.run()

    assert "skipping rather than guessing" in capsys.readouterr().out
    assert code == 1


def test_a_connected_account_is_a_door_and_no_token_is_demanded(repo, capsys, monkeypatch):
    """The first live macOS onboarding, pinned: ``✓ already connected`` two
    lines above a BotFather-token interview is the product contradicting
    itself. A connected account IS a door — cloud-managed, no credential to
    type — and the self-managed interview is offered as a command, not run.
    The autouse ``_no_stray_input`` fixture is the other half of this test:
    the whole ladder must complete without one question."""
    monkeypatch.setattr(front_door, "interactive", lambda: True)
    monkeypatch.setattr(
        front_door, "_invoke",
        lambda argv: pytest.fail(f"ran {argv} on a machine already reachable"),
    )
    _all_configured(monkeypatch, connected=True, doors=())

    code = front_door.run()

    out = capsys.readouterr().out
    assert "cloud-managed — your brnrd.dev account is the door" in out
    assert "optional: a direct, self-managed door" in out
    # The contract rung stays honest: the cloud wire is reply-shaped, so
    # the next move is the human's first message, not a doomed queue.
    assert "message your account's bot about this repo" in out
    assert code == 1


def test_ci_is_never_treated_as_a_typist(monkeypatch):
    """A tty is not proof of a human: CI runners that allocate one
    (`docker run -t`, anything under `script`) would sit on a prompt with
    no timeout until the harness killed them. Measured, then guarded."""
    monkeypatch.setattr("sys.stdin", type("_TTY", (), {"isatty": lambda self: True})())
    monkeypatch.delenv("CI", raising=False)
    assert front_door.interactive() is True
    monkeypatch.setenv("CI", "true")
    assert front_door.interactive() is False


# ── Memory (front_door._step_memory) ───────────────────────────────
#
# The missing step this closes (measured 2026-08-14): the ladder said
# nothing about where a resident's memory lives, so a second-machine setup
# with an empty, silently-scaffolded home rendered identically to one
# holding years of kb pages and run history. Never blocks setup — it only
# names the home and, honestly, what it holds.


def test_step_memory_renders_the_manifest_line_for_a_populated_home(repo, capsys):
    ctx = account.resolve_context(repo, {})
    (account.work_surface_path(ctx) / "warp").mkdir(parents=True, exist_ok=True)
    (account.work_surface_path(ctx) / "warp" / "w-1.md").write_text(
        "# Ship\n\ntype: action\n", encoding="utf-8",
    )
    kb = account.knowledge_path(ctx)
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "design.md").write_text("# Design", encoding="utf-8")

    result = front_door._step_memory(repo, tty=False)

    out = capsys.readouterr().out
    assert str(account.context_home_root(ctx)) in out
    assert "1 kb page · 1 warp item · 0 topics · 0 run records" in out
    assert result is True


def test_the_manifest_line_agrees_with_itself_about_number(repo, capsys):
    """One page is not "1 kb pages".

    This step's whole job is making a home legible at a glance; a count
    that disagrees with its own noun spends some of the credibility the
    number is there to earn. Pins both sides of the boundary at once so a
    future edit cannot fix one and break the other.
    """
    ctx = account.resolve_context(repo, {})
    topics = account.work_surface_path(ctx) / "topics"
    topics.mkdir(parents=True, exist_ok=True)
    for name in ("a.md", "b.md"):
        (topics / name).write_text("# t", encoding="utf-8")
    kb = account.knowledge_path(ctx)
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "design.md").write_text("# Design", encoding="utf-8")

    front_door._step_memory(repo, tty=False)

    out = capsys.readouterr().out
    assert "1 kb page ·" in out and "1 kb pages" not in out
    assert "2 topics" in out
    assert "0 warp items" in out  # zero takes the plural, as English does


def test_step_memory_renders_the_new_resident_line_for_an_empty_home(repo, capsys):
    # create=False by construction (front_door never seeds a home to
    # report on it) — nothing on disk, so this is the "genuinely new
    # resident" path even though resolve_context happily names a path.
    result = front_door._step_memory(repo, tty=False)

    out = capsys.readouterr().out
    assert "starting with no memory yet" in out
    assert "brnrd home link" in out
    assert "kb pages" not in out  # no manifest line on the empty path
    assert result is True


def test_step_memory_notes_local_only_on_a_non_tty(repo, capsys):
    ctx = account.resolve_context(repo, {})
    (account.knowledge_path(ctx) / "index.md").parent.mkdir(parents=True, exist_ok=True)
    (account.knowledge_path(ctx) / "index.md").write_text("# kb", encoding="utf-8")

    result = front_door._step_memory(repo, tty=False)

    out = capsys.readouterr().out
    assert "local-only" in out
    assert "brnrd home link" in out
    assert result is True


def test_step_memory_skips_the_local_only_note_when_fully_linked(repo, capsys):
    ctx = account.resolve_context(repo, {})
    (account.knowledge_path(ctx)).mkdir(parents=True, exist_ok=True)
    (account.knowledge_path(ctx) / "index.md").write_text("# kb", encoding="utf-8")
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:someone/my-brain.git"],
        cwd=account.context_home_root(ctx), check=True,
    )
    # Both repos need an origin — the dominion above, and the (separate,
    # independently-linked) knowledge repo here.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=account.knowledge_path(ctx), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:someone/my-brain-knowledge.git"],
        cwd=account.knowledge_path(ctx), check=True,
    )

    result = front_door._step_memory(repo, tty=False)

    out = capsys.readouterr().out
    assert "local-only" not in out
    assert result is True


def test_step_memory_offers_to_link_on_a_tty(repo, capsys, monkeypatch):
    """Mirrors ``_step_account``'s own ``_ask`` then ``_invoke`` shape:
    ``home_link.link_home`` already does the whole idempotent job in one
    call, so a local-only home on a real terminal gets offered the fix
    instead of only being told the command to type."""
    ctx = account.resolve_context(repo, {})
    kb = account.knowledge_path(ctx)
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "design.md").write_text("# Design", encoding="utf-8")
    monkeypatch.setattr(builtins, "input", _answering("y"))
    invoked = []
    monkeypatch.setattr(front_door, "_invoke", lambda argv: invoked.append(argv))

    result = front_door._step_memory(repo, tty=True)

    out = capsys.readouterr().out
    assert "back it up to private GitHub repos now?" in out
    assert invoked == [["home", "link"]]
    assert result is True


def test_declining_the_link_offer_changes_nothing(repo, capsys, monkeypatch):
    ctx = account.resolve_context(repo, {})
    kb = account.knowledge_path(ctx)
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "design.md").write_text("# Design", encoding="utf-8")
    monkeypatch.setattr(builtins, "input", _answering("n"))
    monkeypatch.setattr(
        front_door, "_invoke",
        lambda argv: pytest.fail(f"ran {argv} on a declined offer"),
    )

    result = front_door._step_memory(repo, tty=True)

    out = capsys.readouterr().out
    assert "skipped" in out
    assert result is True


def test_an_interrupted_question_stops_the_whole_ladder(repo, capsys, monkeypatch):
    """^C means *stop asking*, not *next question*. Measured live
    (2026-08-14, the first macOS onboarding): the old per-step catch kept
    narrating after the launcher had already died of the same SIGINT, so
    the door interviewed a shell prompt. One interrupt now ends the run —
    out loud, resumable, exit 128+SIGINT — and asks nothing further; a
    second ``input`` call would trip this stub's counter."""
    asked = []

    def _interrupt(_prompt=""):
        asked.append(True)
        raise KeyboardInterrupt

    monkeypatch.setattr(front_door, "interactive", lambda: True)
    monkeypatch.setattr(builtins, "input", _interrupt)
    monkeypatch.setattr(
        front_door, "_invoke",
        lambda argv: pytest.fail(f"ran {argv} after an interrupted question"),
    )
    _all_configured(monkeypatch, connected=False, doors=())

    code = front_door.run()

    out = capsys.readouterr().out
    assert "stopped — nothing else was changed" in out
    assert len(asked) == 1, "the door kept asking past a ^C"
    assert code == 130
