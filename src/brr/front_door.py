"""Bare ``brnrd`` — the narrated front door.

The shape, decided 2026-08-10 (`decision-retire-init.md` §"The front door"):
**bare ``brnrd`` in a repo starts a narrated guided setup** — the
``fly launch`` / ``vercel`` pattern. One obvious entry for the newcomer,
who runs a single word and lands set up remembering nothing; and *the
narration is the whole trick* for the skeptic, who watches it announce the
real subcommands it is about to run and sees convenience over a working
CLI, not a black box.

So this module orchestrates and narrates; it implements nothing. Every
mechanical step here is an existing verb — ``account connect``,
``gate setup <gate>`` — and it is reached through :func:`_invoke`, which
parses the very argv it just printed with the very parser ``main`` uses.
That is not a stylistic choice: a step that duplicated a command's
defaults would drift from it silently, and a step that *announced* one
argv while *running* another would make the narration a lie. ``brnrd
<verb>`` stays exactly the CLI it was; this is a second entrance to it,
not a second implementation of it.

Two invariants, both load-bearing for a command a newcomer types blind:

* **Nothing here blocks without a terminal.** Every gate's ``setup()``
  calls bare :func:`input`; the connect flow polls for pairing approval.
  Piped, redirected, or run from CI, the front door reads state, prints
  the ladder with the exact command for each rung, and exits — a status
  screen, never a stalled process.
* **Nothing here is done twice.** Each step checks first and reports
  ``already`` rather than re-running. Bare ``brnrd`` in a finished repo is
  a four-line receipt, not a setup it has to survive again.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import style

#: Doors offered when nothing is configured yet, in the order a newcomer
#: is most likely to already have one. Deliberately not ``gates.BUILTIN_GATES``:
#: ``cloud`` is not a door you pick (``account connect`` configures it, and
#: it can only reply to an inbound message, never start a conversation —
#: see ``connect_greeting.door_for_greeting``), and ``github`` is a review
#: surface rather than a place brnrd reaches *you*.
DOOR_CHOICES: tuple[str, ...] = ("telegram", "slack", "signal")


# ── Narration ───────────────────────────────────────────────────────
#
# One vocabulary, borrowed whole from ``style``: the same ✓ / · / ✗ / ?
# the init screens already speak, so the front door reads as this product
# and not as a second one. Every helper degrades to plain ASCII off a
# terminal (``style.enabled()`` is false for a pipe, ``NO_COLOR``, and
# pytest's ``capsys``), which is also why the tests can match exact text.


def _step(label: str) -> None:
    print(f"\n{style.bold('→')} {style.bold(label)}")


def _ok(text: str) -> None:
    print(f"  {style.check()} {text}")


def _note(text: str) -> None:
    print(f"  {style.dot()} {text}")


def _fail(text: str) -> None:
    print(f"  {style.cross()} {text}")


def _command(argv: list[str]) -> None:
    """Print a brnrd command the way this machine must spell it.

    Both the announcement before :func:`_invoke` runs a step and the
    "here is the rung you skipped" line on the non-interactive path go
    through here, so the two can never disagree about spelling.
    """
    from .cli import brnrd_cmd

    print(f"  {style.dim('$ ' + brnrd_cmd() + ' ' + ' '.join(argv))}")


def _invoke(argv: list[str]):
    """Announce a real brnrd subcommand, then run exactly it.

    The namespace comes from :func:`~brr.cli.build_parser` rather than a
    hand-built one so every default this command relies on is the command's
    own. ``passthrough`` is the single attribute ``main`` sets outside the
    parser (only ``do`` reads it); setting it here keeps that contract
    without teaching this module anything else about argv.
    """
    from . import cli

    _command(argv)
    args = cli.build_parser().parse_args(argv)
    args.passthrough = None
    return args.func(args)


# ── Asking ──────────────────────────────────────────────────────────


def interactive() -> bool:
    """Whether this invocation may ask the user anything.

    ``stdin`` only: a front door piped into ``less`` still prints, but a
    front door with no keyboard behind it must not reach ``input()``.
    """
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):  # a closed or exotic stdin
        return False


def _ask(question: str, *, default: bool = True) -> bool:
    """A yes/no question, with ``^C`` and EOF answering *no* out loud.

    No timeout, deliberately. ``adopt``'s timed prompts exist so an
    unattended ``init`` cannot hang; here the caller has already proven a
    terminal, and a pairing flow that silently self-answered after ten
    seconds because the user was reading the line above it is the opposite
    of the bar this door is held to.
    """
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {style.qmark()} {style.bold(question)} {style.dim('[' + hint + ']')} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        _note("no answer — skipping this step; nothing was changed")
        return False
    if not raw:
        return default
    return raw.lower() in ("y", "yes")


def _ask_choice(question: str, choices: tuple[str, ...], *, default: str) -> str:
    """Pick one of *choices*, or ``skip``. Unrecognised input takes *default*."""
    options = "/".join((*choices, "skip"))
    try:
        raw = input(
            f"  {style.qmark()} {style.bold(question)} "
            f"{style.dim('[' + options + ']')} {style.dim('(default ' + default + ')')} "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        _note("no answer — skipping this step; nothing was changed")
        return "skip"
    if not raw:
        return default
    if raw in choices or raw == "skip":
        return raw
    _note(f"unrecognised — using {style.accent(default)}")
    return default


# ── The steps ───────────────────────────────────────────────────────


def _step_runner(repo_root: Path) -> bool:
    """A coding-agent CLI on PATH. Read-only: nothing to install, nothing to ask.

    First because it is the one prerequisite brnrd cannot supply for
    itself — with no Shell there is no model process to run, so every later
    step would set up a machine that still cannot think.
    """
    from . import runner

    _step("your runner")
    found = runner.detect_all_runners(repo_root)
    if found:
        _ok("on PATH: " + ", ".join(style.accent(name) for name in found))
        return True

    _fail("no coding-agent CLI found on PATH")
    from .cli import brnrd_cmd

    print(runner.render_runner_doctor(
        runner.diagnose_runners(repo_root),
        # The way back is this same door: it is idempotent and it resumes
        # from wherever the ladder actually stands, which `init` does not.
        resume_command=brnrd_cmd(),
    ))
    return False


def _step_account(repo_root: Path, brr_dir: Path, *, tty: bool) -> bool:
    """Pair this machine to a brnrd account.

    **One step, and it is a bundle** — ``account connect`` pairs, installs
    the background service, and queues the first-wake setup event in one
    command. The narration says so rather than pretending to three rungs it
    does not have: splitting ``connect`` into separately-runnable
    ``pair`` / ``daemon install`` / ``gate configure`` verbs is a real
    change to that command, deferred out of this increment on purpose. A
    narrated step that announced ``$ brnrd daemon install`` and then ran
    something else would break the one promise this door makes.
    """
    from .gates import cloud

    _step("your account and the background daemon")
    if cloud.is_configured(brr_dir):
        _ok("already connected")
        return True

    _note("pairs this machine, installs the background service, "
          "and queues your setup run — one command")
    if not tty:
        _command(["account", "connect"])
        return False
    if not _ask("connect this repo to your brnrd account now?"):
        _note("skipped — run it whenever you like:")
        _command(["account", "connect"])
        return False

    _invoke(["account", "connect"])
    return cloud.is_configured(brr_dir)


def _step_doors(brr_dir: Path, *, tty: bool) -> bool:
    """At least one door — where brnrd reaches you, and you it."""
    from .gates import runtime as gate_runtime

    _step("your doors")
    configured = [name for name in gate_runtime.configured_gates(brr_dir)
                  if name in DOOR_CHOICES]
    if configured:
        _ok("configured: " + ", ".join(style.accent(name) for name in configured))
        return True

    _note("no door configured yet — a door is where brnrd reaches you")
    if not tty:
        _command(["gate", "setup", DOOR_CHOICES[0]])
        return False

    choice = _ask_choice("configure a door now — which one?",
                         DOOR_CHOICES, default=DOOR_CHOICES[0])
    if choice == "skip":
        _note("skipped — pick one whenever you like:")
        _command(["gate", "setup", "<gate>"])
        return False

    _invoke(["gate", "setup", choice])
    return bool([name for name in gate_runtime.configured_gates(brr_dir)
                 if name in DOOR_CHOICES])


def _step_contract(repo_root: Path, brr_dir: Path, *, tty: bool) -> bool:
    """``AGENTS.md`` — written by a run, never by this command.

    The closing offer, and the whole reason the door ends in a question
    instead of a summary. ``queue_greeting`` is the existing dispatcher for
    that run (``account connect`` already calls it) and refuses on its own
    to stack a second greeting or to write over an existing contract, so
    offering it again here costs nothing when connect already did it.
    """
    from . import connect_greeting
    from .cli import brnrd_cmd

    _step("your contract (AGENTS.md)")
    if (repo_root / "AGENTS.md").exists():
        _ok("already written")
        return True

    _note("written by your first run — a conversation, not a form")
    if not tty:
        # No standalone verb dispatches this run yet — `account connect`
        # queues it as a side effect, and this door offers it directly.
        # So the honest instruction is the door itself, from a terminal.
        _note(f"re-run `{brnrd_cmd()}` from a terminal to start it")
        return False
    if not _ask("run setup now?"):
        _note(f"skipped — `{brnrd_cmd()} account connect` offers it again")
        return False

    outcome = connect_greeting.queue_greeting(repo_root, brr_dir)
    if outcome.queued:
        _ok(f"queued over {style.accent(str(outcome.door))} ({outcome.event_id}) — "
            f"it reaches you there once `{brnrd_cmd()} up` is polling")
        return True
    _fail(f"not queued: {outcome.reason}")
    return False


# ── The door ────────────────────────────────────────────────────────


def run() -> int:
    """Narrate the setup ladder for the repo in the current directory.

    Returns ``0`` when every rung is standing and non-zero when one is
    not — so a script can read the exit code as "is this repo set up",
    while a human reads the same answer off the ✓ column.
    """
    from . import __version__, gitops
    from .cli import brnrd_cmd

    # Not a git repository raises here, and `main` turns it into one plain
    # sentence naming the cwd and both ways out (#1297). A front door owes
    # that sentence, not a traceback — and not a second copy of it either.
    repo_root = gitops.ensure_git_repo()
    brr_dir = gitops.shared_brr_dir(repo_root)
    tty = interactive()

    print(f"{style.bold('brnrd')} {style.dim(__version__)} "
          f"{style.dim('· guided setup')}")
    print(f"{style.dim('repo:')} {style.accent(str(repo_root))}")
    if not tty:
        _note("not a terminal — reading state only, running nothing")

    standing = [
        _step_runner(repo_root),
        _step_account(repo_root, brr_dir, tty=tty),
        _step_doors(brr_dir, tty=tty),
        _step_contract(repo_root, brr_dir, tty=tty),
    ]

    print()
    if all(standing):
        next_move = f'{brnrd_cmd()} run "what to do next"'
        print(f"{style.check()} all set — {style.dim(next_move)}")
        return 0
    remaining = len([ok for ok in standing if not ok])
    print(f"{style.dot()} {remaining} step(s) left — "
          f"{style.dim('run ' + brnrd_cmd() + ' again any time; it resumes here')}")
    return 1
