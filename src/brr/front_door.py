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


class Interrupted(Exception):
    """``^C`` at any prompt — the person wants out of the *ladder*, not past
    this one rung.

    Measured on the first live macOS onboarding (2026-08-14): ``^C`` at the
    door-choice prompt was caught per-step, so the door marched on to the
    next question over a shell that had already printed its prompt — the
    npm launcher died of the same SIGINT while this process caught it, and
    the survivor narrated into a terminal it no longer owned. Two answers,
    two meanings, split on purpose: **EOF** is "no answer *here*" (a piped
    stdin, a closed tty) and still declines one step out loud; **^C** is
    "stop asking" and ends the whole run, saying what it left standing.
    """


def interactive() -> bool:
    """Whether this invocation may ask the user anything.

    ``stdin`` only: a front door piped into ``less`` still prints, but a
    front door with no keyboard behind it must not reach ``input()``.

    ``CI`` is checked *before* the tty, because a tty is not proof of a
    typist: a CI job that allocates one (``docker run -t``, anything under
    ``script``) satisfies ``isatty()`` with nobody behind it, and the
    prompts here have no timeout by design. Measured, not theorised —
    driving this door under ``script`` blocked until the harness killed it.
    ``CI`` is the one env var every runner sets and no human shell does.
    """
    import os

    if os.environ.get("CI"):
        return False
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
    except KeyboardInterrupt:
        print()
        raise Interrupted() from None
    except EOFError:
        print()
        _note("no answer — skipping this step; nothing was changed")
        return False
    if not raw:
        return default
    return raw.lower() in ("y", "yes")


def _ask_choice(question: str, choices: tuple[str, ...], *, default: str) -> str:
    """Pick one of *choices*, or ``skip``.

    Empty takes *default* — Enter accepts the recommendation, which is the
    whole point of naming one. **Anything unrecognised skips**, and that
    asymmetry is deliberate: the answer this saw first in a live terminal
    was ``n``, a person declining a *which-one* question in the vocabulary
    of the yes/no one above it. Falling back to the default there does not
    guess helpfully — it runs a credential-entering interview nobody
    agreed to. An answer we could not read is never consent to act.
    """
    options = "/".join((*choices, "skip"))
    try:
        raw = input(
            f"  {style.qmark()} {style.bold(question)} "
            f"{style.dim('[' + options + ']')} {style.dim('(Enter for ' + default + ')')} "
        ).strip().lower()
    except KeyboardInterrupt:
        print()
        raise Interrupted() from None
    except EOFError:
        print()
        _note("no answer — skipping this step; nothing was changed")
        return "skip"
    if not raw:
        return default
    if raw in choices or raw == "skip":
        return raw
    _note(f"didn't recognise {style.accent(raw)} — skipping rather than guessing")
    return "skip"


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

    connect_args = ["account", "connect"]
    if not _ask("back up your resident home and knowledge to private GitHub repos?", default=True):
        connect_args.append("--local-memory")
    _invoke(connect_args)
    return cloud.is_configured(brr_dir)


def _tally(count: int, singular: str, plural: str) -> str:
    """``1 kb page`` / ``4,200 kb pages`` — the receipt has to read right.

    This step exists to make a home's contents legible at a glance, and a
    line that says "1 kb pages" spends a little of the credibility the
    number is there to earn. Thousands separators for the same reason: the
    difference between a resident that has lived and one that has not is
    usually four digits wide.
    """

    return f"{count:,} {singular if count == 1 else plural}"


def _step_memory(repo_root: Path, *, tty: bool) -> bool:
    """Name the resident's memory — the account home — out loud.

    After ``_step_account`` (this step needs the account actually resolved,
    or there is no home to describe) and before ``_step_doors`` (memory
    matters more than where the daemon reaches you). Never blocks and
    never fails setup — an empty home is a new resident's normal path, not
    a problem to fix — so it always returns True.

    The defect this closes, measured 2026-08-14: the ladder above narrated
    runner, account, doors, contract, and never once said where the
    resident's *memory* lives. A user set brnrd up on a second machine, the
    home was created empty and unremarked, and the kb (4,200 pages), the
    warp (55 items), the topics (6), and 8,112 run records simply were not
    there — nothing on screen said so, and the dashboard came up hollow for
    hours before anyone understood why. This step is that missing receipt,
    on every run, not only a restore.
    """
    from . import account
    from . import config as conf

    _step("your memory")
    cfg = conf.load_config(repo_root)
    # Read-only, mirroring ``cmd_account_status``: this step reports on the
    # home, it must never be the thing that creates or seeds it.
    ctx = account.resolve_context(repo_root, cfg, create=False)
    home_root = account.context_home_root(ctx)
    manifest = account.home_manifest(ctx)

    if not manifest.has_memory:
        _ok(f"{home_root} — starting with no memory yet; it fills as this resident works")
        # Informational only, deliberately never offered as `_invoke` here:
        # `home link` pushes, and the reader this note is for is the one
        # who *already has* remote-backed memory under a different name —
        # offering to push this empty home at it risks a rejected,
        # non-fast-forward push against exactly that history. `brnrd home
        # link` is the right next command; running it for them is not.
        _note("already have memory elsewhere? `brnrd home link` attaches a remote:")
        _command(["home", "link"])
        return True

    _ok(str(home_root))
    _note(" · ".join(
        _tally(n, one, many) for n, one, many in (
            (manifest.kb_pages, "kb page", "kb pages"),
            (manifest.warp_items, "warp item", "warp items"),
            (manifest.topics, "topic", "topics"),
            (manifest.run_records, "run record", "run records"),
        )
    ))

    if not manifest.fully_linked:
        _note("local-only — this memory doesn't survive the machine yet")
        if not tty:
            _command(["home", "link"])
        elif _ask("back it up to private GitHub repos now?"):
            _invoke(["home", "link"])
        else:
            _note("skipped — run it whenever you like:")
            _command(["home", "link"])

    return True


def _step_doors(brr_dir: Path, *, tty: bool) -> bool:
    """At least one door — where brnrd reaches you, and you it.

    Two kinds, named the way the 2026-08-14 steer named them: a
    **cloud-managed** door rides the account pairing — brnrd.dev runs the
    bot, and a connected account is already reachable with no credential
    to type here; a **self-managed** door is a token the user brings
    (BotFather, a Slack app, signal-cli) for a direct, no-cloud-in-the-path
    wire. The first live macOS onboarding hit the old shape of this step:
    ``✓ already connected`` two lines above, and then an interview
    demanding a bot token the cloud path exists to make unnecessary. So a
    connected account *passes* this step, and the self-managed interview
    becomes what it always was in practice — the power move, offered, not
    required.
    """
    from .gates import cloud
    from .gates import runtime as gate_runtime

    _step("your doors")
    configured = [name for name in gate_runtime.configured_gates(brr_dir)
                  if name in DOOR_CHOICES]
    if configured:
        _ok("self-managed: " + ", ".join(style.accent(name) for name in configured))
        return True
    if cloud.is_configured(brr_dir):
        _ok("cloud-managed — your brnrd.dev account is the door; chat rides the account wire")
        _note("optional: a direct, self-managed door with your own bot token:")
        _command(["gate", "setup", "<gate>"])
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

    # A question whose *yes* is known to fail is not a question — it is a
    # refusal wearing one. `queue_greeting` needs a door that can say the
    # first word, and the cloud wire is reply-shaped (it answers, it does
    # not open — `door_for_greeting`'s contract). So with no direct door
    # at all, don't ask: say the true next move instead. On the cloud-only
    # machine that is the human's — the conversation starts from their
    # side, and the first inbound message wakes the run that writes this
    # file. (A direct door that *is* configured but can't be addressed
    # still gets `queue_greeting`'s own refusal, which names the gap.)
    from .gates import cloud
    from .gates import runtime as gate_runtime

    direct = [name for name in gate_runtime.configured_gates(brr_dir)
              if name in DOOR_CHOICES]
    if not direct:
        if cloud.is_configured(brr_dir):
            _note("your cloud door can reply but not start a conversation —")
            _note("message your account's bot about this repo, and the first run takes it from there")
        else:
            _note("needs a door first (the step above) — then this offer works")
        return False

    if not tty:
        # No standalone verb dispatches this run yet — `account connect`
        # queues it as a side effect, and this door offers it directly.
        # So the honest instruction is the door itself, from a terminal.
        _note(f"re-run `{brnrd_cmd()}` from a terminal to start it")
        return False
    if not _ask("run setup now?"):
        # The way back is this same door — it resumes from this rung. The
        # line used to send people to `account connect`, a verb whose job
        # here was already done; a resume instruction that renames the
        # entrance is a resume instruction that gets pasted and half-works.
        _note(f"skipped — run `{brnrd_cmd()}` again whenever you like")
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
    # Where brnrd keeps things, said up front. The first live macOS
    # onboarding spent its reset attempt grepping `~/brnrd`, `/home/<user>`,
    # `~/local` — the answer (`$XDG_STATE_HOME` shape, macOS included) is
    # nowhere a newcomer would look, and a tool that hides where it lives
    # cannot be cleanly uninstalled, reset, or trusted.
    from . import account

    print(f"{style.dim('state:')} {style.accent(str(account.state_root()))}"
          f"{style.dim(' · per-repo: ' + str(brr_dir))}")
    if not tty:
        _note("not a terminal — reading state only, running nothing")

    try:
        standing = [
            _step_runner(repo_root),
            _step_account(repo_root, brr_dir, tty=tty),
            _step_memory(repo_root, tty=tty),
            _step_doors(brr_dir, tty=tty),
            _step_contract(repo_root, brr_dir, tty=tty),
        ]
    except Interrupted:
        # 128+SIGINT, the exit code the shell would have minted had nothing
        # caught it — a script watching this door reads ^C as ^C.
        print()
        print(f"{style.dot()} stopped — nothing else was changed; "
              f"{style.dim('run ' + brnrd_cmd() + ' again any time; it resumes here')}")
        return 130

    print()
    if all(standing):
        next_move = f'{brnrd_cmd()} run "what to do next"'
        print(f"{style.check()} all set — {style.dim(next_move)}")
        return 0
    remaining = len([ok for ok in standing if not ok])
    print(f"{style.dot()} {remaining} step(s) left — "
          f"{style.dim('run ' + brnrd_cmd() + ' again any time; it resumes here')}")
    return 1
