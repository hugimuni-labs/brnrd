"""brnrd CLI — thin dispatch layer over the library modules.

The surface is **nouns first**: a handful of blessed shortcuts for the verbs
every doc already uses (``init``, ``run``, ``review``, ``up``, ``down``), then
one noun per subsystem (``daemon``, ``gate``, ``account``, …). Machine-facing
endpoints (``hook``, ``statusline``) and developer probes (``prompts``,
``worktree-hygiene``) still parse but are hidden from ``--help``: they are
called by the runner's lifecycle or by a resident that was told the spelling,
never discovered by a user reading the verb list. ``ALL_COMMANDS`` /
``PUBLIC_COMMANDS`` pin both sets so drift becomes a test failure.
"""

from __future__ import annotations

import argparse
import os
import textwrap
from pathlib import Path

from . import closekeyword
from . import gates as _gates

#: Every gate brnrd knows how to auth/bind/configure — **derived, never
#: re-listed**. ``gates.BUILTIN_GATES`` owns the set; this was a second copy
#: of the same literal until 2026-08-05, and both files carried a comment
#: calling themselves its single source of truth. Signal joining stayed
#: consistent by hand, which is the property a derived name does not need.
GATES = _gates.BUILTIN_GATES

#: Platforms that reach brnrd through a gate of a *different* name. A user who
#: types the channel they actually use is asking a real question and deserves
#: a pointer, not "unknown gate": WhatsApp is publicly listed as a supported
#: door (``support_matrix.DOORS``) while being a platform branch inside the
#: cloud gate rather than a gate of its own.
GATE_BY_PLATFORM: dict[str, tuple[str, str]] = {
    "whatsapp": (
        "cloud",
        "whatsapp is not a gate: it is a platform on the managed cloud lane, "
        "so there is nothing to configure locally. Connect the number on "
        "brnrd.dev, then run `brnrd gate setup cloud` to pair this repo.",
    ),
}

#: Top-level spellings retired by the noun consolidation (#49). Pre-release,
#: these do not survive as silent aliases — each fails with a one-line pointer
#: at the noun that absorbed it. Kept as parsers (not deleted outright) so the
#: error is a *pointer* rather than argparse's bare "invalid choice".
RETIRED_COMMANDS = {
    "auth": "brnrd gate auth <gate>",
    "bind": "brnrd gate bind <repo> <gate>",
    "setup": "brnrd gate setup <gate>",
    "add": "brnrd account add <repo>",
    "connect": "brnrd account connect [url]",
}

#: Verbs listed by ``brnrd --help`` — the user-facing surface.
PUBLIC_COMMANDS = (
    "init", "enable", "run", "review", "up", "down",
    "daemon", "gate", "account", "home",
    "kb", "docs", "portal", "runners", "bench", "agent", "ergonomics",
    "completions", "gc",
)

#: Verbs that parse but are hidden from ``--help``.
#
# ``config`` lands here, not in ``PUBLIC_COMMANDS``, on a test-suite
# constraint, not a discoverability judgment: ``test_help_stays_small_
# enough_to_read`` pins an 18-verb ceiling on the public list and it was
# already full. ``brnrd config promote`` is a rare, operator-run,
# one-time migration (issue #533) — closer in shape to ``prompts``/
# ``worktree-hygiene`` (parses, documented, not a everyday verb) than to
# the daily-use nouns the ceiling protects. It still parses, keeps its
# docstring, and is named explicitly in onboarding docs; if a future
# maintainer wants it front-and-center, retiring or folding another verb
# to make room is the tradeoff to make deliberately, not by accident here.
#
# ``relic`` is hidden for the same reason as ``emotes``: it is the
# resident's, not the operator's — it only does anything inside a live wake,
# and ``daemon-substrate.md`` / ``brnrd docs portals`` point a resident at
# the spelling directly. It also could not be public without evicting
# something from the 18-verb ceiling.
#
# ``close-check`` is hidden on the same reasoning: it is the resident's half
# of #839. ``gate: forge`` PR bodies are checked in the daemon whether anyone
# calls this or not; the verb exists for the hand-``gh pr create`` path, where
# a run gates on it deliberately and ``brnrd docs portals`` carries the
# spelling. An operator browsing ``--help`` has no use for it, and the public
# list is at its ceiling.
#
# ``mood`` (the mood seam's ergonomics ask, 2026-08-03) is the resident's
# front door onto `.mood`, same shape as ``relic``/``promise``: it only does
# anything inside a live wake, and it collapses the lookup-then-write
# round-trip ``brnrd emotes`` used to leave to the resident by hand.
#
# ``notes`` (2026-08-07) is the same shape once more: the map of the
# *resident's* own durable writing surfaces — where each lives, who parses
# it, what grammar it wants, and what the deterministic preflight found.
# An operator browsing ``--help`` has no use for it; a resident about to
# write into a half-remembered surface does, and the substrate points at
# the spelling.
#
# ``cut`` (design-the-bolt.md, 2026-08-07) is the resident's own front door
# onto the bolt — the run-completion declaration, sibling of ``do``/``await``
# in shape and reasoning: a live-wake verb, not an operator's terminal.
HIDDEN_COMMANDS = (
    "prompts", "hook", "statusline", "worktree-hygiene", "config", "emotes",
    "relic", "gate-run", "close-check", "promise", "mood", "do", "notes",
    "await", "cut", "legend", "item", "goal", "queue", "envoy",
)

#: What ``brnrd promise`` accepts, spelled here so building the parser costs
#: no import of :mod:`brr.promises` (every verb's help text is built on every
#: invocation, including ``--help``). Held equal to ``promises.PROMISABLE``
#: by a test rather than by a comment — the honest form of "keep these in
#: sync" is one that goes red.
_PROMISABLE = (
    "commit", "branch", "pr", "merge", "kb", "issue", "comment", "message",
    "file",
)

#: Promisable kinds the daemon already derives without anyone writing a
#: relic by hand: commits/branch from ``git log``, ``merge`` from a merge
#: commit's own shape, ``kb`` from the pages the knowledge capture commits
#: at closeout (see ``relics.derive_auto`` / ``knowledge.capture``). A
#: promise naming one of these is answered by work that happens anyway, so
#: ``relic`` owes no subcommand for it. Everything else in ``_PROMISABLE``
#: is the hand-attested half — the only half a promise is really for — and
#: needs one (#1060). Checked against the live ``relic`` subcommand set by
#: a test that goes red on drift, same idiom as the ``_PROMISABLE`` /
#: ``promises.PROMISABLE`` pair above.
_RELIC_AUTO_DERIVED = frozenset({"commit", "branch", "merge", "kb"})

#: Everything ``brnrd <verb>`` accepts, retired pointers included.
ALL_COMMANDS = tuple(
    sorted(PUBLIC_COMMANDS + HIDDEN_COMMANDS + tuple(RETIRED_COMMANDS))
)

#: Set by the npm launcher (``packaging/npm/bin/brnrd.js``) when the process
#: was started through ``npx brnrd``. Absent for every other install shape.
LAUNCHER_ENV = "BRNRD_LAUNCHER"


def brnrd_cmd() -> str:
    """How the user must spell a brnrd command **on this machine**.

    ``npx brnrd init`` installs into a managed virtualenv under
    ``~/.local/share/brnrd`` and execs the binary inside it; it never puts
    ``brnrd`` on the user's PATH. So every line that ends "then run ``brnrd
    up``" is a lie to that user — and the first one they meet is the last
    line of their first session. The launcher is the only component that
    knows which spelling brought it here, so it says so in the environment
    and this reads it back.

    Read at *call* time, never captured at import: the value is process
    environment, tests monkeypatch it, and a launcher that sets it after
    this module is imported still has to count.

    Compose with it rather than around it — ``f"{brnrd_cmd()} up"`` — so a
    third spelling (should one ever arrive) lands here and nowhere else.
    """
    if os.environ.get(LAUNCHER_ENV) == "npx":
        return "npx brnrd"
    return "brnrd"


class _OrderedAppend(argparse.Action):
    """Append ``(dest, value)`` to one shared, command-line-ordered list.

    ``brnrd do``'s ``--reply``/``--gate`` verbs each need the *next*
    ``--body-file``/``--body`` on the command line, not any later one — and
    ``--body-file`` is legitimately reused by both verbs. Plain
    ``action="append"`` gives four independent lists with the cross-verb
    ordering thrown away; routing all four options through this one action
    (into ``namespace._do_ops``) preserves command-line order so
    ``cli._reconstruct_do_ops`` can pair each body to the verb that
    immediately preceded it, the same way a shell reads the flags left to
    right.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        items = getattr(namespace, "_do_ops", None)
        if items is None:
            items = []
            namespace._do_ops = items
        items.append((self.dest, values))


def build_parser() -> argparse.ArgumentParser:
    """Build the full argparse tree.

    Split out of ``main`` so the CLI surface is inspectable without running a
    command — the completions generator walks this tree, and the surface test
    pins it.
    """
    from . import __version__
    parser = argparse.ArgumentParser(
        prog=brnrd_cmd(),
        description="Resident agent runtime for local and managed repo work",
    )
    parser.add_argument("--version", action="version", version=f"brnrd {__version__}")

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("init", help="set up a repo for brnrd")
    p.add_argument("url", nargs="?", default=None, help="clone URL (optional)")
    # #507: `brnrd init` is one verb. The interview wake is what init *is*
    # on a TTY with a working Runner; everything else degrades to the
    # mechanical install automatically, with one line saying why. No flag —
    # a mode switch here would ask the user to choose between two things
    # they have no way to tell apart yet (maintainer decision, 2026-07-22).
    # `-i` survives only as a no-op alias so muscle memory still lands on
    # the friendlier path instead of an argparse error.
    p.add_argument("-i", "--interactive", action="store_true",
                   help="deprecated no-op — the interview is the default")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser(
        "enable", help="make a project agent-ready and register it locally")
    p.add_argument(
        "path", nargs="?", default=".",
        help="git project to enable (default: current repository)",
    )
    p.add_argument(
        "--borrowed", action="store_true",
        help="keep every seeded addition local to this checkout",
    )
    p.add_argument("--label", default=None, help="household project label")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("run", help="run a task through the runner")
    p.add_argument("instruction", help="what to do")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "gc", help="prune daemon-accumulated state per retention windows")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be deleted (counts + bytes per store) without deleting")
    p.set_defaults(func=cmd_gc)

    # Hidden: the resident's spelling for satisfying the `hooks.gate_command`
    # Stop-hook obligation, not a verb a human browsing `--help` picks —
    # same shape as `hook`/`worktree-hygiene` (parses, documented directly
    # at the point of use, no --help line spent).
    p = sub.add_parser("gate-run")
    p.add_argument(
        "--override-command", default=None,
        help="run this instead of the configured hooks.gate_command",
    )
    p.set_defaults(func=cmd_gate_run)

    # Omitting `help=` is what hides a subparser: argparse only adds it to the
    # help listing when the kwarg is present (`help=argparse.SUPPRESS` renders a
    # literal "==SUPPRESS==" line instead). Developer probe, not an operator verb.
    p = sub.add_parser("worktree-hygiene")
    p.set_defaults(func=cmd_worktree_hygiene)

    p = sub.add_parser("review", help="work with diffense review packs")
    p.add_argument("pack", help="path to a review pack JSON file")
    p.add_argument("--check", action="store_true",
                   help="validate the pack's schema, card graph, and locators")
    p.add_argument("--pr-body", action="store_true",
                   help="project the pack into a Markdown pull-request body")
    p.add_argument("--pr-title", action="store_true",
                   help="print the pull-request title derived from the pack")
    p.add_argument("--fallback-title", default=None,
                   help="fallback title when the pack has no better title")
    p.add_argument("--render-url", default=None,
                   help="interactive review URL to include in the PR body")
    p.add_argument("--render-base-url", default=None,
                   help="renderer shell base URL for gist-backed review links")
    p.add_argument("--relay", action="store_true",
                   help="publish a rich review link: secret gist first, brnrd relay fallback")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.set_defaults(func=cmd_review)

    # The opt-in half of #839. `gate: forge` PR bodies are checked at the
    # outbox drain, where refusal is enforced; a PR opened by hand with
    # `gh pr create` passes through no brnrd code at all, so the honest
    # coverage there is a verb a run can call on the body file before it
    # shells out — not a wrapper pretending to be a chokepoint.
    p = sub.add_parser("close-check")
    p.add_argument(
        "path", nargs="?", default="-",
        help="file to check; '-' or omitted reads stdin",
    )
    p.add_argument(
        "--channel", default="pr-body", choices=sorted(closekeyword.CHANNELS),
        help="which surface this text is headed for (default: pr-body)",
    )
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.add_argument("--resolve", action="store_true",
                   help="look up the current state of each close ref on the forge")
    p.add_argument("--repo", metavar="OWNER/NAME",
                   help="repo --resolve looks the refs up in (default: whatever "
                        "gh resolves from the working directory)")
    p.set_defaults(func=cmd_close_check)

    gate_help = f"gate name ({', '.join(GATES)})"

    gate_p = sub.add_parser("gate", help="configure the gates brnrd speaks through")
    gate_sub = gate_p.add_subparsers(dest="gate_command", required=True)

    p = gate_sub.add_parser("setup", help="configure a gate in one step (auth + bind)")
    p.add_argument("gate", help=gate_help)
    p.set_defaults(func=cmd_setup)

    p = gate_sub.add_parser("auth", help="authenticate a gate")
    p.add_argument("gate", help=gate_help)
    p.set_defaults(func=cmd_auth)

    p = gate_sub.add_parser("bind", help="bind a repo-local gate to this repo")
    p.add_argument("repo", help="repo path to bind")
    p.add_argument("gate", help=gate_help)
    p.set_defaults(func=cmd_bind)

    p = gate_sub.add_parser("list", help="show which gates are configured here")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.set_defaults(func=cmd_gate_list)

    account_p = sub.add_parser(
        "account", help="the connected account home and the repos under it")
    account_sub = account_p.add_subparsers(dest="account_command", required=True)

    p = account_sub.add_parser("add", help="add a repo to the connected account home")
    p.add_argument("repo", help="repo path to add")
    p.set_defaults(func=cmd_add)

    p = account_sub.add_parser("connect", help="link this daemon to brnrd")
    p.add_argument("url", nargs="?", default=None,
                   help="brnrd base URL (default: $BRNRD_URL or https://brnrd.dev)")
    p.add_argument("--url", dest="url_option", default=None,
                   help="brnrd base URL (same as positional URL)")
    p.add_argument("--daemon-name", default=None,
                   help="name to register this daemon under (default: hostname)")
    p.add_argument(
        "--no-service",
        action="store_true",
        help="pair only; do not install or start the systemd/launchd service",
    )
    p.add_argument(
        "--defaults",
        action="store_true",
        help=(
            "skip the conversational setup interview on an uninitialized "
            "repo; write today's `brnrd init` defaults directly instead"
        ),
    )
    linger = p.add_mutually_exclusive_group()
    linger.add_argument(
        "--yes-linger",
        action="store_true",
        help="linux: enable systemd linger without prompting",
    )
    linger.add_argument(
        "--no-linger",
        action="store_true",
        help="linux: skip the linger prompt",
    )
    p.set_defaults(func=cmd_brnrd_connect)

    p = account_sub.add_parser(
        "disconnect", help="unlink this daemon from brnrd")
    p.set_defaults(func=cmd_brnrd_disconnect)

    p = account_sub.add_parser(
        "relabel",
        help="follow a repo that changed address, carrying its memory with it")
    p.add_argument("old_label", metavar="<old>", help="current label, e.g. Gurio/brr")
    p.add_argument("new_label", metavar="<new>",
                   help="new label, e.g. hugimuni-labs/brnrd")
    p.add_argument("--dry-run", action="store_true",
                   help="print the moves without performing them")
    p.add_argument("--yes", action="store_true",
                   help="skip the confirmation prompt (required when not on a TTY)")
    p.set_defaults(func=cmd_account_relabel)

    p = account_sub.add_parser(
        "status", help="show the resolved home, its kind, and the repos under it")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.set_defaults(func=cmd_account_status)

    home_p = sub.add_parser("home", help="manage the resolved brnrd home")
    home_sub = home_p.add_subparsers(dest="home_command", required=True)
    p = home_sub.add_parser(
        "link",
        help="back up the agent's memory + knowledge base to private GitHub repos",
    )
    p.add_argument("--yes", action="store_true",
                   help="skip the confirmation prompt (required when not on a TTY)")
    p.add_argument("--owner", default=None,
                   help="GitHub owner/org for the backup repos (default: `gh api user` login)")
    p.add_argument("--dominion-name", default=None,
                   help="repo name for the memory backup (default: brnrd-home)")
    p.add_argument("--knowledge-name", default=None,
                   help="repo name for the knowledge backup (default: brnrd-knowledge)")
    p.set_defaults(func=cmd_home_link)

    p = home_sub.add_parser(
        "manifest",
        help="count what the resolved home actually holds — kb pages, "
             "warp items, topics, run records, surface pages, git state",
    )
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.set_defaults(func=cmd_home_manifest)

    p = home_sub.add_parser(
        "sweep-orphans",
        help="list (and, with --delete, remove) project homes holding "
             "nothing but default scaffold — #1193 rec 4",
    )
    p.add_argument("--delete", action="store_true",
                   help="actually remove the orphaned homes found (default: dry run, lists only)")
    p.add_argument("--yes", action="store_true",
                   help="skip the confirmation prompt (required with --delete when not on a TTY)")
    p.set_defaults(func=cmd_home_sweep_orphans)

    # Hidden per HIDDEN_COMMANDS above (help ceiling, not obscurity) — omit
    # `help=` here too, or it leaks into the listing despite the constant.
    config_p = sub.add_parser("config")
    config_sub = config_p.add_subparsers(dest="config_command", required=True)

    p = config_sub.add_parser(
        "promote",
        help="move security-defining keys (runner_cmd, trust.*, docker.*, "
             "solitary.*, environment/env/default_env) and the runner "
             "profile catalog (.brr/runners.md) out of the repo-writable "
             ".brr/ into the daemon-owned home",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan; change nothing")
    p.add_argument("--force", action="store_true",
                   help="overwrite a security.config value that differs "
                        "from .brr/config's, instead of refusing")
    p.set_defaults(func=cmd_config_promote)

    # `up` / `down` are blessed shortcuts — the muscle-memory verbs every doc
    # uses — but they are *thin aliases*, not a second implementation. Before
    # #49 the top-level pair called `daemon.start`/`stop` directly and silently
    # skipped the installed service, so `brnrd up` and `brnrd daemon up` did
    # different things under the same name. Both spellings now build the same
    # parser and land on the same function.
    def _add_up(target, name: str = "up"):
        q = target.add_parser(name, help="start the daemon")
        q.add_argument("--foreground", action="store_true",
                       help="run the foreground daemon instead of the installed service")
        q.add_argument("--dev-reload", action="store_true", default=None,
                       help="developer: re-exec daemon when brnrd package files change")
        q.set_defaults(func=cmd_daemon_up)
        return q

    def _add_down(target, name: str = "down"):
        q = target.add_parser(name, help="stop the daemon")
        q.set_defaults(func=cmd_daemon_down)
        return q

    _add_up(sub)
    _add_down(sub)

    daemon_p = sub.add_parser("daemon", help="daemon lifecycle")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_command", required=True)

    _add_up(daemon_sub)
    _add_down(daemon_sub)

    p = daemon_sub.add_parser("status", help="show daemon status")
    p.set_defaults(func=cmd_daemon_status)

    p = daemon_sub.add_parser("install", help="install the native user service")
    p.add_argument("--no-start", action="store_true",
                   help="write the service file without starting it now")
    linger = p.add_mutually_exclusive_group()
    linger.add_argument("--yes-linger", action="store_true",
                        help="linux: enable systemd linger without prompting")
    linger.add_argument("--no-linger", action="store_true",
                        help="linux: skip the linger prompt")
    p.set_defaults(func=cmd_daemon_install)

    p = daemon_sub.add_parser("uninstall", help="remove the native user service")
    disable_linger = p.add_mutually_exclusive_group()
    disable_linger.add_argument("--yes-disable-linger", action="store_true",
                                help="linux: disable linger if brr enabled it earlier")
    disable_linger.add_argument("--no-disable-linger", action="store_true",
                                help="linux: leave linger enabled without prompting")
    p.set_defaults(func=cmd_daemon_uninstall)

    p = daemon_sub.add_parser("logs", help="tail daemon service logs")
    p.add_argument("-n", "--lines", type=int, default=80,
                   help="number of existing log lines to show first")
    p.add_argument("--no-follow", action="store_true",
                   help="print existing log lines and exit")
    p.set_defaults(func=cmd_daemon_logs)

    erg_p = sub.add_parser(
        "ergonomics", help="inspect locally captured agent-ergonomics records")
    erg_sub = erg_p.add_subparsers(dest="ergonomics_command", required=True)

    p = erg_sub.add_parser("summary", help="top issues with counts over a window")
    p.add_argument("--days", type=int, default=7,
                   help="window in days (default: 7)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.set_defaults(func=cmd_ergonomics_summary)

    p = erg_sub.add_parser("list", help="raw records, newest last")
    p.add_argument("--issue", default=None, help="filter to one issue identifier")
    p.add_argument("--days", type=int, default=None, help="window in days")
    p.add_argument("--limit", type=int, default=50,
                   help="max records to show (default: 50)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.set_defaults(func=cmd_ergonomics_list)

    p = erg_sub.add_parser("clear", help="delete locally stored records")
    p.add_argument("--before", default=None,
                   help="YYYY-MM-DD; delete days strictly before this (default: all)")
    p.set_defaults(func=cmd_ergonomics_clear)

    p = sub.add_parser(
        "docs", help="read bundled tool docs (omit topic to list)")
    p.add_argument("topic", nargs="?", default=None,
                   help="doc topic to print (e.g. portals, execution-map)")
    p.set_defaults(func=cmd_docs)

    # No `help=`: hidden commands stay off `--help` (pinned by
    # `test_hidden_commands_parse_but_are_not_listed`), which is the whole
    # point — this one is the resident's, and `daemon-substrate.md` points
    # it there directly.
    p = sub.add_parser("emotes")
    p.add_argument("query", nargs="*", help="a feeling, a handle, or words from a trigger")
    p.add_argument("--all", action="store_true", help="every face, not the top matches")
    p.add_argument("--telemetry", action="store_true", help="the daemon's derived set too")
    p.set_defaults(func=cmd_emotes)

    # Hidden per HIDDEN_COMMANDS, same reasoning as `emotes`: the boot-time
    # legend question design-the-live-loop.md §Round 2026-08-07 left open
    # ("render it once at boot… or purely on-demand") answered on the
    # cheap, pull-not-push side — a resident opaque on what a chip means
    # calls this instead of grepping `hooks.py` for `BAR_SEGMENTS`.
    p = sub.add_parser("legend")
    p.set_defaults(func=cmd_legend)

    # Hidden per HIDDEN_COMMANDS — the resident's front door onto the
    # `.relics.jsonl` produce manifest, the same "control file with a command
    # in front of it" shape as `.card` / `.keepalive`.
    relic_p = sub.add_parser("relic")
    relic_sub = relic_p.add_subparsers(dest="relic_command", required=True)

    p = relic_sub.add_parser(
        "issue", help="record an issue this run opened or closed")
    p.add_argument("number", help="issue number (686 or #686)")
    action_flags = p.add_mutually_exclusive_group()
    action_flags.add_argument(
        "--opened", dest="action", action="store_const", const="opened",
        help="this run filed the issue")
    action_flags.add_argument(
        "--closed", dest="action", action="store_const", const="closed",
        help="this run closed the issue")
    p.add_argument(
        "--repo", default=None, metavar="owner/name",
        help="the issue's project, when it is not this checkout's origin")
    p.set_defaults(func=cmd_relic_issue, action=None)

    # The second PR self-report front door (#317 follow-up): the ``.pr``
    # control holds exactly one PR per run, so a run that opens more than
    # one had no legal way to record the rest until now — this mirrors
    # `relic issue`'s shape onto the same `{"kind": "pr", "number": N}`
    # grammar `collect()` already parses (and, after the numberless-pr fix,
    # normalises from a bare ref/URL).
    p = relic_sub.add_parser(
        "pr", help="record a PR this run opened, beyond the `.pr` control")
    p.add_argument("number", help="PR number, #N, or a full forge URL")
    p.add_argument(
        "--repo", default=None, metavar="owner/name",
        help="the PR's project, when it is not this checkout's origin — "
             "inferred from a full URL's own owner/repo when omitted")
    p.add_argument(
        "--summary", default=None, metavar="TEXT",
        help="one line describing the PR")
    p.set_defaults(func=cmd_relic_pr)

    # The blueprint's front door — `.promises.jsonl`, the opposite tense of
    # the relics manifest (#1008). Its own top-level verb rather than a
    # `relic` subcommand: a promise is not produce, the two live in different
    # files for that reason, and the whole feature turns on writing a promise
    # being cheaper than breaking one.
    promise_p = sub.add_parser("promise")
    promise_p.add_argument(
        "what",
        help="what this run is promising to make: " + ", ".join(_PROMISABLE))
    promise_p.add_argument(
        "--count", type=int, default=1, metavar="N",
        help="how many (default 1)")
    promise_p.add_argument(
        "--ref", default=None, metavar="LABEL",
        help="what to call it when the boundary says it is still owed "
             "(e.g. --ref 'the rollout split'). A label, never a key: "
             "matching is on count, so shipping the same work under another "
             "name still keeps the promise")
    promise_p.add_argument(
        "--release", action="store_true",
        help="release --count units of this kind (default 1), not the "
             "whole row — a kind promised 4 times and released once is "
             "still owed 3; requires --why")
    promise_p.add_argument(
        "--why", default=None, metavar="REASON",
        help="why these units are being withdrawn (required with "
             "--release; rides the row for the units this call releases, "
             "not a statement about every promise of this kind)")
    promise_p.set_defaults(func=cmd_promise)

    # The mood seam's front door: collapses the lookup-then-write round trip
    # `brnrd emotes <query>` then a hand-written `.mood` used to leave to the
    # resident. Hidden per HIDDEN_COMMANDS, same reasoning as `emotes` and
    # `relic` — it is the resident's, and `daemon-substrate.md` names the
    # spelling directly.
    mood_p = sub.add_parser("mood")
    mood_p.add_argument(
        "feeling",
        help="a feeling, or a handle (`brnrd emotes <query>` finds one)")
    mood_p.add_argument(
        "narration", nargs="*",
        help="optional narration lines, written after the resolved handle")
    mood_p.add_argument(
        "--outbox", default=None, metavar="DIR",
        help="the run's outbox dir, when the environment does not name one "
             "(BRR_OUTBOX_DIR / BRR_PORTAL_STATE)")
    mood_p.set_defaults(func=cmd_mood)

    p = relic_sub.add_parser(
        "item", help="record the warp item this run ignited from")
    p.add_argument(
        "address",
        help="item id (the file's basename in surface/warp/, e.g. w-42)")
    p.set_defaults(func=cmd_relic_item)

    # The remaining three fronts onto `_PROMISABLE`'s hand-attested half
    # (#1060): `comment`/`message`/`file` were promisable and rendered fine
    # (`relics.label`) but had no subcommand at all — only a hand-written
    # `.relics.jsonl` line could keep a promise of one of these kinds, and
    # the CLI never said that was the escape hatch. Same shape as
    # `issue`/`item`: one positional for the field the grammar already
    # reads, the same outside-a-run and failed-append refusals.
    p = relic_sub.add_parser(
        "comment", help="record a comment this run left on an issue, PR, or thread")
    p.add_argument(
        "on",
        help="what the comment was on, e.g. 'issue #903 — stale-open sweep'")
    p.set_defaults(func=cmd_relic_comment)

    p = relic_sub.add_parser(
        "message",
        help="record an outbound message this run sent, outside its own reply")
    p.add_argument("note", help="what the message said or was about")
    p.add_argument(
        "--channel", default=None, metavar="NAME",
        help="where it went, e.g. telegram, slack")
    p.set_defaults(func=cmd_relic_message)

    p = relic_sub.add_parser(
        "file", help="record a file this run produced, outside a commit")
    p.add_argument("path", help="the file's path")
    p.set_defaults(func=cmd_relic_file)

    # The warp item space (2026-08-11): `brnrd item` are the maintenance
    # verbs over `surface/warp/` — writes are verbs, not prose, so a weak
    # core can only be asked to do what the system can check it did
    # (design-decision-surface.md §Maintenance contract).
    # `item` is hidden for the same reason as `relic`: it is the resident's
    # maintenance verb over the account's warp (`surface/warp/`), pointed at
    # from the wake's own warp-index block, not an operator's daily noun.
    item_p = sub.add_parser("item")
    item_sub = item_p.add_subparsers(dest="item_cmd", required=True)
    p = item_sub.add_parser("list", help="the open-items index (--all for everything)")
    p.add_argument("--all", action="store_true", help="include done/retired items")
    p.set_defaults(func=cmd_item_list)
    p = item_sub.add_parser("new", help="mint a new item file, id allocated")
    p.add_argument("headline", help="the item's one-line headline")
    p.add_argument(
        "--type", required=True,
        choices=("decision", "preparation", "action", "goal"),
        dest="item_type")
    p.add_argument("--topics", default=None, help="topic ids, space-separated")
    p.add_argument("--needs", default=None, help="blocking item ids, space-separated")
    p.add_argument(
        "--advances", default=None,
        help="goal ids this item advances, space-separated (legal on any "
        "type, including a goal itself for sub-goals)")
    p.add_argument(
        "--metric", default=None, help="goal-only: the number this goal moves")
    p.add_argument(
        "--target", default=None, help="goal-only: the finish line")
    p.add_argument(
        "--horizon", default=None, help="goal-only: the timeframe")
    p.add_argument("--prompt", default=None, help="the dispatch mandate, one line")
    p.add_argument("--refs", default=None, help="refs row, `·`-separated")
    p.add_argument("--body", default=None, help="free markdown body")
    p.set_defaults(func=cmd_item_new)
    p = item_sub.add_parser("done", help="stamp an item's completion receipt")
    p.add_argument("id", help="item id, or a unique fragment of its headline")
    p.add_argument("--run", default=None, help="run id for the receipt "
                   "(default: this run, via BRR_RUN_ID)")
    p.set_defaults(func=cmd_item_done)
    p = item_sub.add_parser("retire", help="retire an item without completing it")
    p.add_argument("id", help="item id, or a unique fragment of its headline")
    p.add_argument("--why", default=None, help="one line on why")
    p.set_defaults(func=cmd_item_retire)

    # `brnrd goal` (design-goal-oriented-engineering.md §"a metrics block in
    # the wake"): the readings store's maintenance verbs, mirroring `item`'s
    # shape — same `_item_context`/id-resolution helpers, same read/refuse
    # style. Hidden for the same reason as `item`: the resident's own
    # measuring hand, recorded on its own schedule (collectors are explicitly
    # out of scope — this CLI is the one grammar both a human and a resident
    # write through).
    goal_p = sub.add_parser("goal")
    goal_sub = goal_p.add_subparsers(dest="goal_cmd", required=True)
    p = goal_sub.add_parser("record", help="append a reading + echo the latest")
    p.add_argument("id", help="goal id, or a unique fragment of its headline")
    p.add_argument("key", help="the metric key this sample is for")
    p.add_argument("value", type=float, help="the sample's numeric value")
    p.add_argument("--source", default=None, help="where the number came from")
    p.add_argument("--note", default=None, help="optional free-text note")
    p.set_defaults(func=cmd_goal_record)
    p = goal_sub.add_parser(
        "show", help="metric/target/horizon header, then latest reading per key")
    p.add_argument("id", help="goal id, or a unique fragment of its headline")
    p.set_defaults(func=cmd_goal_show)

    # The public queue (`envoys.py`): mail that arrived at envoy standing —
    # it can never ignite a run; a sweep on the resident's own clock closes
    # each item `answered` / `noted` / `dropped`.
    # Hidden per HIDDEN_COMMANDS (same reasoning as `item`/`goal`: the
    # resident's own verbs, pointed at from the wake and the sweep contract,
    # not an operator's daily noun — the dashboard drawer is the operator
    # read surface). No `help=` on the top-level parsers, so they stay off
    # `--help` (see the comment on `do` below).
    queue_p = sub.add_parser("queue")
    queue_sub = queue_p.add_subparsers(dest="queue_cmd")
    p = queue_sub.add_parser("list", help="queue items, oldest first (default verb)")
    p.add_argument("--status", default=None, help="filter: arrived/answered/noted/dropped")
    p.set_defaults(func=cmd_queue_list)
    p = queue_sub.add_parser("show", help="one item, whole")
    p.add_argument("id", help="queue item id")
    p.set_defaults(func=cmd_queue_show)
    p = queue_sub.add_parser(
        "record", help="file one arrived item (sweep scripts' write verb)")
    p.add_argument("--channel", required=True, help="medium it arrived on (x, github, ...)")
    p.add_argument("--body", default=None, help="the item's text")
    p.add_argument("--body-file", default=None, help="read the text from a file (- for stdin)")
    p.add_argument(
        "--meta", action="append", default=[], metavar="KEY=VALUE",
        help="context fields (author=..., ref=..., envoy=...), repeatable")
    p.set_defaults(func=cmd_queue_record)
    p = queue_sub.add_parser("close", help="close one item with a verb")
    p.add_argument("id", help="queue item id")
    p.add_argument(
        "--as", required=True, dest="verb", choices=("answered", "noted", "dropped"),
        help="the close verb")
    p.add_argument("--why", default=None, help="one line on why (required for dropped)")
    p.set_defaults(func=cmd_queue_close)
    queue_p.set_defaults(func=cmd_queue_list, status=None)

    envoy_p = sub.add_parser("envoy")
    envoy_sub = envoy_p.add_subparsers(dest="envoy_cmd")
    p = envoy_sub.add_parser("list", help="registry rows (default verb)")
    p.set_defaults(func=cmd_envoy_list)
    envoy_p.set_defaults(func=cmd_envoy_list)

    # Hidden per HIDDEN_COMMANDS — porcelain over the outbox verb grammar
    # (`docs/portals.md`), meant for the resident's own shell inside a live
    # wake, not an operator's terminal. `-- <command> [args...]` is split out
    # of argv before this parser ever sees it (see `main`), so it never
    # appears as an argparse-level option here.
    # No `help=`: hidden commands stay off `--help` (see the comment on
    # `emotes` above) — passing one is exactly what made `do` show up in
    # `_choices_actions` and fail `test_hidden_commands_parse_but_are_not_listed`.
    do_p = sub.add_parser("do")
    do_p.add_argument(
        "--outbox", default=None, metavar="DIR",
        help="outbox dir to act on (default: this run's own, via "
             "BRR_OUTBOX_DIR / BRR_PORTAL_STATE)")
    do_p.add_argument(
        "--timeout", type=float, default=None, metavar="SECONDS",
        help="how long to wait for a staged directive to drain "
             "(default 30s)")
    do_p.add_argument(
        "--mood", default=None, metavar="FEELING-OR-HANDLE",
        help="resolve a feeling or handle through the emotes index and "
             "write .mood")
    do_p.add_argument(
        "--mood-note", default=None, metavar="TEXT",
        help="narration after the resolved mood handle (only with --mood)")
    do_p.add_argument(
        "--note", dest="note", action="append", default=None,
        metavar="EVENT-ID",
        help="retire a pending event deliberately, no message goes out "
             "(repeatable)")
    do_p.add_argument(
        "--reply", dest="reply", action=_OrderedAppend, default=None,
        metavar="EVENT-ID",
        help="reply to a pending event; pair with --body-file/--body "
             "(repeatable)")
    do_p.add_argument(
        "--gate", dest="gate", action=_OrderedAppend, default=None,
        metavar="NAME",
        help="send to a destination with no waiting event; pair with "
             "--body-file (repeatable)")
    do_p.add_argument(
        "--body-file", dest="body_file", action=_OrderedAppend, default=None,
        metavar="FILE",
        help="body for the immediately preceding --reply or --gate")
    do_p.add_argument(
        "--body", dest="body", action=_OrderedAppend, default=None,
        metavar="TEXT",
        help="inline body for the immediately preceding --reply "
             "(--gate takes --body-file only)")
    do_p.add_argument(
        "--card", default=None, metavar="FILE",
        help="overwrite .card with this file's contents")
    do_p.set_defaults(func=cmd_do)

    # Hidden per HIDDEN_COMMANDS, same reason as `do`: a resident's own verb
    # inside a live wake, not an operator's terminal. The whole surface is
    # `brnrd await` with nothing after it — every flag below is optional, and
    # omitting all of them is the shape the prose teaches (#959, #1187).
    await_p = sub.add_parser("await")
    await_p.add_argument(
        "--timeout", default=None, metavar="DURATION",
        help="ceiling on the wait (30m, 1h30m, or seconds); default: this "
             "run's own remaining budget")
    await_p.add_argument(
        "--file", default=None, metavar="PATH",
        help="also resolve when this path appears — an extra trigger for "
             "what the daemon cannot see; never narrows the wait")
    await_p.add_argument(
        "--json", action="store_true", help="emit the outcome as JSON")
    await_p.add_argument(
        "--outbox", default=None, metavar="DIR",
        help="outbox dir to act on (default: this run's own, via "
             "BRR_OUTBOX_DIR / BRR_PORTAL_STATE)")
    await_p.set_defaults(func=cmd_await)

    # Hidden per HIDDEN_COMMANDS, same shape as `do`/`await`: the resident's
    # own front door onto the bolt (design-the-bolt.md), not an operator's
    # terminal. Positional FILE is the resident-authored declaration —
    # frontmatter (`asks:`/`produce:`/`owed:`/...) plus a woven body.
    cut_p = sub.add_parser("cut")
    cut_p.add_argument(
        "file", metavar="FILE",
        help="the declaration to stage: frontmatter + woven body")
    cut_p.add_argument(
        "--outbox", default=None, metavar="DIR",
        help="outbox dir to act on (default: this run's own, via "
             "BRR_OUTBOX_DIR / BRR_PORTAL_STATE)")
    cut_p.add_argument(
        "--timeout", type=float, default=None, metavar="SECONDS",
        help="how long to wait for the daemon's verdict (default 30s)")
    cut_p.set_defaults(func=cmd_cut)

    p = sub.add_parser("kb", help="search home/repo knowledge; omit query to print graph shape")
    p.add_argument("query", nargs="?", default=None,
                   help="search term (omit to print the kb graph shape)")
    p.add_argument("--limit", type=int, default=20,
                   help="maximum matching lines to print")
    p.set_defaults(func=cmd_kb)

    # Hidden, like `relic` / `promise` / `mood`, and for the same reason:
    # it is the *resident's* front door onto its own note surfaces, not the
    # operator's. `daemon-substrate.md` and `brnrd docs portals` point a
    # resident at the spelling. The public list is also at its 19-verb
    # ceiling, and this verb has not earned a slot off another one.
    p = sub.add_parser("notes")
    p.add_argument(
        "surface", nargs="?", default=None,
        help="a surface key (see `brnrd notes`) for its grammar, readers and "
             "findings; or `check` to run the checks with full detail")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.set_defaults(func=cmd_notes)

    portal_p = sub.add_parser("portal", help="inspect daemon portal state")
    portal_sub = portal_p.add_subparsers(dest="portal_command", required=True)

    p = portal_sub.add_parser(
        "state", help="show the live daemon-state portal for a running wake")
    p.add_argument("--json", action="store_true",
                   help="emit raw portal JSON")
    p.add_argument("--path", default=None,
                   help="read this portal-state.json path instead of auto-detecting")
    p.set_defaults(func=cmd_portal_state)

    p = portal_sub.add_parser(
        "facets",
        help="list the boundary facet catalogue — what the implemented facets "
             "are, and (inside a wake) which are populated right now")
    p.add_argument("--json", action="store_true",
                   help="emit the facet catalogue as JSON")
    p.add_argument("--path", default=None,
                   help="read this portal-state.json path for live status")
    p.set_defaults(func=cmd_portal_facets)

    # Machine-facing endpoints: called by the runner's native lifecycle hooks
    # and by Claude's TUI footer, never typed. They stay parseable and keep
    # their docstrings; they just don't spend a line of the operator's --help.
    p = sub.add_parser("hook")
    p.add_argument(
        "phase",
        help="abstract phase: post-tool | stop | session-start | pre-tool",
    )
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("statusline")
    p.set_defaults(func=cmd_statusline)

    agent_p = sub.add_parser(
        "agent", help="resident-agent helpers (wake-context, dominion)")
    agent_sub = agent_p.add_subparsers(dest="agent_command", required=True)

    p = agent_sub.add_parser(
        "inject",
        help="print the full wake-context a daemon task receives — dominion "
             "digest + pitfalls + recent kb/log + mode-toggle blocks "
             "(diffense, introspection) when their config toggles are on")
    p.add_argument(
        "--task", default=None,
        help="task text to match pitfalls against (a pitfall's triggers key "
             "off how a request is phrased)")
    p.set_defaults(func=cmd_agent_inject)

    runners_p = sub.add_parser(
        "runners", help="inspect configured Shell/Core runner profiles")
    runners_sub = runners_p.add_subparsers(dest="runners_command", required=True)

    p = runners_sub.add_parser(
        "list",
        help="list runner profiles from the unified catalog projection")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.add_argument("--all", action="store_true",
                   help="include profiles whose Shell is not on PATH (shown by default with ✗)")
    p.set_defaults(func=cmd_runners_list)

    p = runners_sub.add_parser(
        "doctor",
        help="check runner catalog health: stale cores, missing shells, auth issues")
    p.set_defaults(func=cmd_runners_doctor)

    # Resident-facing introspection: the boot text tells a wake the spelling
    # (`brnrd prompts show`), so it needs no discovery slot in the operator's
    # verb list. Hidden, not retired — the surface is load-bearing.
    prompts_p = sub.add_parser("prompts")
    prompts_sub = prompts_p.add_subparsers(dest="prompts_command", required=True)

    p = prompts_sub.add_parser(
        "show",
        help="print the boot source manifest — every block considered for a "
             "wake, with owner, authority, freshness, and location. "
             "Deterministic and network-free.")
    p.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of human-readable text")
    p.add_argument(
        "--runner", default=None,
        help="runner profile to score for (e.g. claude-sonnet, codex) — also "
             "resolves that Shell's real hook capability")
    p.set_defaults(func=cmd_prompts_show)

    p = prompts_sub.add_parser(
        "transcript",
        help="materialize the wake as a resumable session — the boot as "
             "evidence of having oriented, instead of prose telling you to. "
             "Prints the exact command to resume it.")
    p.add_argument(
        "--runner", default=None,
        help="runner profile to build for (e.g. claude-haiku) — the floor is "
             "the instrument for boot work, so name a weak core deliberately. "
             "Only claude-Shell profiles have a mount; codex is refused, loudly")
    p.add_argument(
        "--write", action="store_true",
        help="write the session file where the Shell looks for it")
    p.set_defaults(func=cmd_prompts_transcript)

    p = prompts_sub.add_parser(
        "wake",
        help="print a past run's context as the runner received it — the boot "
             "prompt plus every hook boundary injection after it, in order. "
             "Defaults to the most recent run.")
    p.add_argument(
        "run_id", nargs="?", default=None,
        help="run id (default: the most recent run directory)")
    p.add_argument(
        "--boundaries", type=int, default=None,
        help="show only the first N boundaries (default: all)")
    p.add_argument(
        "--no-boot", action="store_true",
        help="skip the boot prompt and print only the boundaries")
    p.set_defaults(func=cmd_prompts_wake)

    p = prompts_sub.add_parser(
        "replay",
        help="rebuild a captured run's prompt under modified prompt files "
             "and report which blocks would have changed — w-56 rung 1. "
             "Substitutes only file-backed blocks (run.md, weave.md, "
             "register.md, daemon-substrate.md, identity-core.md, "
             "diffense.md, introspection.md, the portals.md verb-grammar "
             "extract); every other byte of the captured wake is held "
             "identical. Refuses rather than guessing when the captured "
             "run's block layout cannot be verified (e.g. a boot.mount "
             "run, whose file-backed blocks never entered prompt.md's own "
             "text).")
    p.add_argument("run_id", help="run id to replay (must have a captured prompt.md + boot-score.json)")
    p.add_argument(
        "--prompts", required=True, metavar="DIR",
        help="directory of replacement prompt files, named like the "
             "bundled originals (weave.md, daemon-substrate.md, ...)")
    p.add_argument(
        "--block", action="append", default=None, metavar="BLOCK_KEY",
        help="restrict substitution to this block_key (repeatable); every "
             "other file-backed block still reports as unchanged")
    p.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of a human diff")
    p.set_defaults(func=cmd_prompts_replay)

    bench_p = sub.add_parser(
        "bench",
        help="probe daemon/runner seams with a scripted lesser-light run")
    bench_sub = bench_p.add_subparsers(dest="bench_command", required=True)

    p = bench_sub.add_parser("scenarios", help="list bench scenarios")
    p.set_defaults(func=cmd_bench_scenarios)

    p = bench_sub.add_parser(
        "run",
        help="run one scenario in a sandbox (spends real runner quota)")
    p.add_argument("--scenario", default="simple-ask",
                   help="scenario name (see `brnrd bench scenarios`)")
    p.add_argument("--shell", default="claude-haiku",
                   help="runner profile to pin in the sandbox")
    p.add_argument("--root", default=None,
                   help="sandbox root directory (default: ~/.cache/brr/bench/<stamp>)")
    p.add_argument("--timeout", type=int, default=None,
                   help="override the scenario timeout in seconds")
    p.add_argument("--config", action="append", default=[], metavar="KEY=VALUE",
                   help="extra .brr/config line for the sandbox (repeatable) — "
                        "this is how an A/B arm is expressed, e.g. "
                        "--config boot.mount=true")
    p.set_defaults(func=cmd_bench_run)

    p = sub.add_parser(
        "completions",
        help="print a shell completion script (bash, zsh, fish)")
    p.add_argument("shell", choices=("bash", "zsh", "fish"),
                   help="shell to generate completions for")
    p.set_defaults(func=cmd_completions)

    # Retired top-level spellings — parsed only to fail with a pointer.
    for retired, replacement in RETIRED_COMMANDS.items():
        p = sub.add_parser(retired, add_help=False)
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=_retired_command(retired, replacement))

    return parser


def _retired_command(name: str, replacement: str):
    def _fail(args):
        del args
        import sys

        print(
            f"brnrd: `{name}` moved — use `{replacement}`",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return _fail


def _drop_inherited_git_pin() -> None:
    """Drop ``GIT_DIR`` / ``GIT_WORK_TREE`` from this process's environment.

    #703 pins both into a strand run's environment so a bare ``git commit``
    from a drifted cwd cannot reach the shared host checkout. Those two
    variables outrank *every* cwd-based discovery mechanism — ``cwd=``,
    ``-C <path>``, an absolute pathspec — so any tool that inherits them
    addresses the pinned worktree no matter which repository it names.

    ``brnrd`` is such a tool, and it is invoked from inside a pinned run on
    two live paths: the runner hook endpoint (``brnrd hook <phase>``, see
    ``hooks.hook_command``) and any ``brnrd`` command the resident itself
    types. Every git call brnrd makes names the repository it means, so the
    pin can only make brnrd report the wrong tree — confidently, exit 0.
    Dropping it here is the single floor for the whole package: it covers
    modules that call ``subprocess.run`` directly, and modules added later
    that never hear about this. The per-wrapper scrub in
    ``gitops.explicit_repo_env`` is the same invariant stated locally, for
    library importers that never come through this entrypoint.

    Not a behaviour change for a normal invocation: brnrd resolves its
    repository from cwd and config, never from ``GIT_DIR``.
    """
    for var in ("GIT_DIR", "GIT_WORK_TREE"):
        os.environ.pop(var, None)


def main(argv: list[str] | None = None) -> None:
    _drop_inherited_git_pin()
    import sys

    raw = list(sys.argv[1:] if argv is None else argv)
    # `brnrd do [verbs…] -- <command> [args…]` — split the passthrough command
    # out of argv before argparse ever sees it, rather than fighting
    # argparse's own (subparser-inconsistent) `--` handling with
    # `nargs=REMAINDER`. Only "do" gets this treatment; every other
    # subcommand's own `--` (if it ever has one) is untouched. `args.passthrough`
    # is always set on the parsed namespace, `None` when there was no `--`, so
    # `cmd_do` never has to special-case a missing attribute.
    passthrough: list[str] | None = None
    if raw[:1] == ["do"] and "--" in raw:
        idx = raw.index("--")
        passthrough = raw[idx + 1:]
        raw = raw[:idx]
    from . import gitops

    try:
        if not raw:
            # Bare `brnrd` — the narrated front door
            # (`decision-retire-init.md` §"The front door"). Handled here
            # rather than as an argparse default because the subparsers are
            # `required=True` and must stay that way: `brnrd <verb>` is the
            # CLI it has always been, and a mistyped verb must still be an
            # argparse error, not a guided setup. The only argv this
            # intercepts is the empty one, which argparse could previously
            # only answer with "the following arguments are required".
            from . import front_door

            return front_door.run()
        args = build_parser().parse_args(raw)
        args.passthrough = passthrough
        return args.func(args)
    except gitops.RepoTreeUnusable as exc:
        # #1108: the one failure where a traceback actively misleads. It
        # names a path the user never typed, points at `subprocess.py`, and
        # says nothing about the git config that caused it — so the operator
        # reads "brnrd is broken" instead of "one line in .git/config is".
        # Every frame between here and there is machinery; the message is
        # the whole product.
        raise SystemExit(f"[brnrd] {exc}") from None
    except gitops.NotAGitRepository as exc:
        # The sibling of the above: a fresh user runs `brnrd account connect`
        # from a folder that is not a git checkout and, before this, got the
        # full subprocess/RuntimeError traceback as the product's first
        # impression. The message names the cwd and both ways out; a front
        # door owes a plain sentence, not a stack.
        raise SystemExit(f"[brnrd] {exc}") from None


def _repo_root() -> Path:
    from . import gitops
    return gitops.ensure_git_repo()


def _repo_root_from_arg(raw: str) -> Path:
    import subprocess

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"not a git repository: {raw}")
    return Path(result.stdout.strip())


def _brr_dir() -> Path:
    from . import gitops

    return gitops.shared_brr_dir(_repo_root())


def _brr_dir_for_repo(repo_root: Path) -> Path:
    from . import gitops

    return gitops.shared_brr_dir(repo_root)


def _maybe_brr_dir() -> Path | None:
    try:
        return _brr_dir()
    except (RuntimeError, SystemExit):
        return None


def _maybe_repo_root() -> Path | None:
    try:
        return _repo_root()
    except (RuntimeError, SystemExit):
        return None


def cmd_init(args):
    from . import adopt
    adopt.init_repo(args.url, interactive=getattr(args, "interactive", False))


def cmd_enable(args):
    from . import enable

    result = enable.enable_project(
        _repo_root_from_arg(args.path),
        borrowed=bool(args.borrowed),
        label=args.label,
    )
    agents = (
        "AGENTS.md created"
        if result.agents_md == "created"
        else "AGENTS.md existing"
    )
    bridges = ", ".join(result.bridges) if result.bridges else "none needed"
    print(f"[brnrd] seeded: {agents}; bridges: {bridges}")
    print(f"[brnrd] mode: {result.seeding}")
    print(f"[brnrd] registry: {result.registry_path}")
    print(
        f"[brnrd] household link: {result.household_link} "
        f"({result.household_path})"
    )


def cmd_gc(args):
    """``brnrd gc [--dry-run]`` — retention sweep over daemon state (#501).

    Same code path the daemon's periodic pass uses, so the dry run prints
    exactly the counts and bytes a real run deletes.
    """
    from . import account as account_mod
    from . import config as conf
    from . import retention

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    windows = retention.Windows.from_config(cfg)
    try:
        ctx = account_mod.resolve_context(repo_root, cfg, create=False)
    except Exception:
        ctx = None
    _plan, reports = retention.gc(
        repo_root, ctx, windows, dry_run=bool(args.dry_run))
    print(retention.render_report(reports, windows, dry_run=bool(args.dry_run)))


def cmd_gate_run(args):
    """``brnrd gate-run`` — run this repo's declared ``hooks.gate_command``
    and write the receipt ``hooks._gate_closeout_clause`` checks for (the
    obligation nothing could satisfy: only this repo's own unshipped
    ``scripts/gate.py`` ever wrote one; every other adopter's
    ``hooks.gate_command`` was first-run poison).

    Only writes anything from inside a brnrd run — ``BRR_OUTBOX_DIR`` is the
    daemon's own signal for "there is a Stop-hook obligation watching this",
    the same env var ``scripts/gate.py`` keys off. A bare shell invocation
    with nothing watching writes no receipt, on purpose: there is nothing for
    one to satisfy.
    """
    from . import config as conf
    from . import gate_receipt

    repo_root = _repo_root()
    command = args.override_command or conf.load_config(repo_root).get("hooks.gate_command")
    if not command:
        raise SystemExit(
            "[brnrd] gate-run: no command to run — set hooks.gate_command in "
            ".brr/config, or pass one: brnrd gate-run --override-command '<cmd>'"
        )
    command = str(command)

    outbox = os.environ.get("BRR_OUTBOX_DIR")
    if not outbox:
        raise SystemExit(
            "[brnrd] gate-run: BRR_OUTBOX_DIR is unset — this only writes a "
            "receipt from inside a brnrd run (the Stop-hook obligation reads "
            "one per run, never a standing global fact)"
        )
    run_id = os.environ.get("BRR_RUN_ID", "")
    rc = gate_receipt.run_and_write_receipt(
        repo_root, Path(outbox), command, run_id=run_id)
    raise SystemExit(rc)


def cmd_run(args):
    from . import daemon as daemon_mod
    brr = _brr_dir()
    pid = daemon_mod.read_pid(brr)
    if pid:
        print(f"[brnrd] warning: daemon running (pid {pid}) — concurrent writes possible")

    from . import runner
    runner.run_task(args.instruction)


def cmd_worktree_hygiene(args):
    del args

    from . import worktree

    return worktree.main_worktree_hygiene()


def cmd_prompts_show(args):
    """``brnrd prompts show [--json] [--runner PROFILE]``.

    Prints the boot source manifest: every block that would enter a wake here,
    with owner, authority, freshness/revision, location, and whether it is
    currently present or silent.  Deterministic and network-free.
    """
    import json
    import sys

    from . import bootscore, prompts, runner

    repo_root = _maybe_repo_root()

    # Resolve an optional runner profile to its Shell + Core.  The catalog is
    # the public surface that already answers this (it is what the wake's own
    # Runner block is built from) — the earlier code reached into runner_select
    # for a `.shell` attribute RunnerProfile does not have, and a bare
    # `except Exception` swallowed the AttributeError into a wrong answer.
    runner_medium: str | None = None
    runner_core: str | None = None
    if getattr(args, "runner", None):
        name = str(args.runner)
        catalog = runner.available_runner_catalog(repo_root, selected=name)
        match = next((r for r in catalog if r.get("name") == name), None)
        if match is None:
            known = ", ".join(sorted(str(r.get("name")) for r in catalog)) or "none"
            print(
                f"brnrd: unknown runner profile {name!r}. Known profiles: {known}",
                file=sys.stderr,
            )
            return 1
        runner_medium = match.get("shell") or name
        runner_core = match.get("model")

    # Hook facts, in order of authority: inside a wake the hooks are provably
    # wired (and their fired stamps are readable); with a Shell named, probe
    # its real capability; otherwise the honest answer is "unknown from here".
    import os

    wake_outbox = os.environ.get("BRR_OUTBOX_DIR") or os.environ.get("BRR_PORTAL_STATE")
    in_wake = bool(os.environ.get("BRR_RUNNER") and wake_outbox)
    if in_wake:
        hooks_installed: bool | None = True
        hook_stamps = prompts.read_hook_stamps(Path(wake_outbox))
    else:
        hooks_installed = prompts.probe_shell_hook_capability(runner_medium)
        hook_stamps = {}

    score = prompts.build_boot_score(
        repo_root,
        is_daemon=True,
        is_strand=False,
        runner_shell=runner_medium,
        runner_core=runner_core,
        hooks_installed=hooks_installed,
        hook_stamps=hook_stamps,
    )

    if getattr(args, "json", False):
        print(json.dumps(bootscore.to_dict(score), indent=2))
    else:
        print(bootscore.format_manifest(score))
    return 0


def _wake_dump(run_dir: Path, *, boot: bool, limit: int | None) -> str:
    """Render one run's whole received context as readable Markdown.

    Pure apart from the two file reads, so the tests drive it directly. The
    ordering is the run's own: the boot prompt the daemon assembled, then each
    hook boundary in the order it fired. That order *is* the point — reading
    the wake alone answers "what was it told", and only the boundaries answer
    "what did it keep being told".
    """
    import json

    parts: list[str] = [f"# Received context — `{run_dir.name}`\n"]
    prompt_path = run_dir / "prompt.md"
    boundaries_path = run_dir / "boundaries.jsonl"

    if boot:
        if prompt_path.exists():
            body = prompt_path.read_text(encoding="utf-8", errors="replace")
            parts.append(
                f"## Boot — the assembled wake ({len(body.encode())} B)\n\n"
                f"{body.rstrip()}\n"
            )
        else:
            parts.append(
                "## Boot — the assembled wake\n\n"
                "_absent: no `prompt.md` in this run directory._\n"
            )

    # Absent and empty are different answers, and the boundary transcript is
    # young enough that "no file" usually means "this run predates it" rather
    # than "this run had no boundaries" — say which.
    if not boundaries_path.exists():
        parts.append(
            "## Boundaries\n\n"
            "_no `boundaries.jsonl` — this run predates the boundary "
            "transcript, or ran with no boot-score path armed._\n"
        )
        return "\n".join(parts)

    records = []
    for line in boundaries_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"phase": "?", "inject": line, "malformed": True})

    total = len(records)
    shown = records if limit is None else records[:limit]
    header = f"## Boundaries — {total} hook fire(s)"
    if len(shown) != total:
        header += f", showing the first {len(shown)}"
    parts.append(header + "\n")
    for index, record in enumerate(shown, start=1):
        phase = record.get("phase", "?")
        at = record.get("at", "?")
        inject = record.get("inject")
        blocked = " · **BLOCKED**" if record.get("block") else ""
        parts.append(f"### {index}. `{phase}` · {at}{blocked}\n")
        if inject:
            parts.append("```\n" + str(inject).rstrip() + "\n```\n")
        else:
            # A fired-but-silent boundary is a result, not a gap: it is the
            # hook deciding the runner already has this text. Rendering it
            # keeps the count honest against the fire count.
            parts.append("_silent — nothing injected at this boundary._\n")
        if record.get("block_reason"):
            parts.append(
                "block reason:\n\n```\n"
                + str(record["block_reason"]).rstrip()
                + "\n```\n"
            )
    return "\n".join(parts)


def _default_wake_run(runs_dir: Path) -> Path | None:
    """The run to print when the caller named none.

    ``BRR_RUN_ID`` first, most-recent-by-mtime second — and the order matters
    more than it looks. Called from *inside* a run that has spawned a strand,
    newest-directory-wins resolves to the **child's** run, silently: the
    command answers a different question than the one asked and nothing about
    the output says so. A run asking for "the wake" means its own.
    """
    current = (os.environ.get("BRR_RUN_ID") or "").strip()
    if current:
        mine = runs_dir / current
        if mine.is_dir():
            return mine
    candidates = [d for d in runs_dir.iterdir() if d.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime)


def cmd_prompts_wake(args):
    """``brnrd prompts wake [RUN_ID]`` — a run's context, both halves.

    The daemon has always written `prompt.md` per run; nothing wrote what the
    hooks injected afterwards, so the only inspectable record of a runner's
    environment ended at t=0. `hooks.record_boundary` writes the other half
    and this prints them together, which is the whole of what a run was ever
    told.
    """
    import sys

    repo_root = _maybe_repo_root()
    if repo_root is None:
        print("brnrd: not inside a git repository", file=sys.stderr)
        return 1
    runs_dir = repo_root / ".brr" / "runs"
    if not runs_dir.is_dir():
        print(f"brnrd: no run directory at {runs_dir}", file=sys.stderr)
        return 1

    if args.run_id:
        run_dir = runs_dir / args.run_id
        if not run_dir.is_dir():
            print(f"brnrd: unknown run {args.run_id!r}", file=sys.stderr)
            return 1
    else:
        run_dir = _default_wake_run(runs_dir)
        if run_dir is None:
            print(f"brnrd: no runs under {runs_dir}", file=sys.stderr)
            return 1

    sys.stdout.write(
        _wake_dump(
            run_dir,
            boot=not getattr(args, "no_boot", False),
            limit=getattr(args, "boundaries", None),
        )
    )
    return 0


def cmd_prompts_replay(args):
    """``brnrd replay <run-id> --prompts <dir> [--block NAME]... [--json]``.

    w-56 rung 1 — rebuild a captured run's ``prompt.md`` with its
    file-backed blocks substituted from ``--prompts <dir>``, print the
    substitution roster and diff, hold every other byte identical. See
    :mod:`brr.replay` for the locate mechanism and why it refuses rather
    than guesses on a run it cannot verify.
    """
    import json
    import sys

    from . import replay as replay_mod

    repo_root = _maybe_repo_root()
    if repo_root is None:
        print("brnrd: not inside a git repository", file=sys.stderr)
        return 1
    # `shared_brr_dir`, not a bare `repo_root / ".brr"`: inside a linked
    # worktree (where a strand's own run always executes) the runtime dir
    # — including `runs/` — lives beside the *host* checkout's common git
    # dir, not under the worktree itself. `cmd_prompts_wake` above uses the
    # bare form and is consequently unable to find its own run's directory
    # from inside a worktree; noted in the rung-1 report rather than fixed
    # here (out of this change's scope — `_wake_dump`'s caller, not the
    # function itself, and a pre-existing behavior this task didn't ask
    # for).
    runs_dir = _brr_dir_for_repo(repo_root) / "runs"
    run_dir = runs_dir / args.run_id
    if not run_dir.is_dir():
        print(f"brnrd: unknown run {args.run_id!r} (looked under {runs_dir})", file=sys.stderr)
        return 1

    prompts_dir = Path(args.prompts)
    if not prompts_dir.is_dir():
        print(f"brnrd: --prompts {args.prompts!r} is not a directory", file=sys.stderr)
        return 1

    try:
        result = replay_mod.plan_replacement(
            run_dir, prompts_dir, block_filter=getattr(args, "block", None)
        )
    except replay_mod.ReplayLocateError as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(replay_mod.to_dict(result), indent=2))
    else:
        sys.stdout.write(replay_mod.format_human(result))
    return 0


def cmd_prompts_transcript(args):
    """Materialize the wake as a session the Shell can resume.

    The verification surface for Slice 4, and it exists because the resident that
    built Slice 4 **could not verify it**: a nested ``claude`` is inert inside a
    ``claude`` session, so the one thing that matters — does the Shell actually
    resume a session brnrd forged — is not answerable from inside a wake. This
    prints the artifact and the exact command, so a human shell can answer it.
    """
    import subprocess
    import sys

    from . import prompts, runner, transcript as tx

    repo_root = _maybe_repo_root()

    runner_medium: str | None = None
    runner_core: str | None = None
    if getattr(args, "runner", None):
        name = str(args.runner)
        catalog = runner.available_runner_catalog(repo_root, selected=name)
        match = next((r for r in catalog if r.get("name") == name), None)
        if match is None:
            known = ", ".join(sorted(str(r.get("name")) for r in catalog)) or "none"
            print(
                f"brnrd: unknown runner profile {name!r}. Known profiles: {known}",
                file=sys.stderr,
            )
            return 1
        runner_medium = match.get("shell") or name
        runner_core = match.get("model")

        # The IR is Shell-agnostic; the mount is not. Without this, `--runner
        # codex` scored the wake for codex, stamped a codex core on the seeded
        # turns, rendered them in *claude's* JSONL, wrote them to *claude's*
        # session directory, and printed a `claude --resume` command — while
        # reporting `body: codex / default` the whole way. A tool that cannot
        # distinguish "mounted for codex" from "mounted for claude wearing a
        # codex label" is this week's bug in a fourth costume. Refuse instead.
        if runner_medium not in tx.MOUNTED_SHELLS:
            have = ", ".join(sorted(tx.MOUNTED_SHELLS))
            print(
                f"brnrd: no transcript mount for shell {runner_medium!r} — only "
                f"{have} can resume a session brnrd forged.\n"
                f"  The IR is Shell-agnostic; the mount is not, and only "
                f"render_claude_jsonl() exists today.\n"
                f"  This is a missing renderer, not a safety wall: `Perceive` "
                f"carries a path, and each Shell's renderer spells it in its own "
                f"verb\n"
                f"  (claude: Read; codex: `cat` through exec, authored by the "
                f"renderer, never inspected).\n"
                f"  Not built yet because the boot's benefit is unmeasured — see "
                f"transcript.MOUNTED_SHELLS.",
                file=sys.stderr,
            )
            return 1

    score = prompts.build_boot_score(
        repo_root,
        is_daemon=True,
        is_strand=False,
        runner_shell=runner_medium,
        runner_core=runner_core,
        hooks_installed=prompts.probe_shell_hook_capability(runner_medium),
        hook_stamps={},
    )

    # Reconstruct each file-backed block from disk via `prompts.mountable_block_text`
    # — a raw file read for most blocks (exactly what the wake received; a trimmed
    # one gets `_trim_note`), or a block's own extractor for the few blocks that
    # mount a curated slice of a larger file rather than the whole thing. Blocks at
    # `location == "computed"` are live state and stay prose — they are not on
    # disk and a Read returning them would be fiction.
    block_text: dict[str, str] = {}
    for entry in score.contracts:
        if not entry.present or entry.location == tx.COMPUTED:
            continue
        try:
            block_text[entry.block_key] = prompts.mountable_block_text(
                entry, repo_root
            )
        except OSError:
            continue

    branch = ""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        pass

    t = tx.build_orientation_transcript(
        score,
        block_text=block_text,
        cwd=str(repo_root),
        git_branch=branch,
        model=runner_core or "",
    )

    seen = list(t.perceptions())
    if not seen:
        print("[brnrd] no file-backed blocks in this wake — nothing to mount.")
        return 1

    body = tx.render_claude_jsonl(t)
    print(f"seeded turns : {len(seen)} perception{'s' if len(seen) != 1 else ''}, "
          f"each with its result")
    for c in seen:
        print(f"  {tx.CLAUDE_READ_TOOL} {c.location}  → {len(c.result):,} B")
    print(f"session      : {t.session_id}")
    print(f"body         : {runner_medium} / {runner_core or 'default'}")

    if not args.write:
        print("\n(dry run — pass --write to place the session file)")
        return 0

    path = tx.claude_session_path(t.cwd, t.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    print(f"written      : {path} ({len(body.encode()):,} B)")
    print(
        f"\nresume it (from a plain shell, NOT inside a claude session):\n"
        f"  claude --resume {t.session_id} --fork-session --print \\\n"
        f"    'Without using any tools: what did you just read, and what is it "
        f"asking of you?'"
    )
    return 0


def cmd_agent_inject(args):
    import sys

    from . import prompts

    repo_root = _maybe_repo_root()
    if repo_root is None:
        print("[brnrd agent inject] not inside a git repo", file=sys.stderr)
        return 2
    text = prompts.build_injected_context(repo_root, task_text=args.task)
    if not text.strip():
        print("[brnrd agent inject] no dominion here yet — bootstrap one with "
              "`brnrd init` or by starting the daemon", file=sys.stderr)
        return 1
    print(text)
    return 0


def cmd_docs(args):
    import sys

    from . import docs

    repo_root = _maybe_repo_root()
    if args.topic is None:
        print(docs.format_listing(repo_root))
        return 0
    text = docs.read_topic(args.topic, repo_root=repo_root)
    if text is None:
        print(f"[brnrd docs] unknown topic: {args.topic}", file=sys.stderr)
        print(docs.format_listing(repo_root), file=sys.stderr)
        return 1
    print(text)
    return 0


def cmd_emotes(args):
    """Print matching faces — the palette's index (#566).

    A resident writes one handle into `.mood` and until now had no way to
    learn a second one: the boot names `brr.emotes` and gives a single
    example. Pull-not-push on purpose (see `emotes.search`), so this costs
    a wake nothing until it wants a face.

    One line per face: handle, the frames it plays, its resting frame when
    that differs, the body-axis pitch, and the trigger — which is the part
    that actually matters, because the honesty bar is "wear it only when
    the trigger line is true right now", and a handle without its trigger
    is an invitation to lie politely.
    """
    from . import emotes as emo

    query = " ".join(args.query or [])
    if args.all:
        rows = [
            e for e in emo.EMOTES.values()
            if args.telemetry or e.kind == "situational"
        ]
    else:
        rows = emo.search(query, limit=200 if args.telemetry else 12)
        if not args.telemetry:
            rows = [e for e in rows if e.kind == "situational"] or rows

    if not rows:
        # #1117: a miss is a bridge, not a scold. The old line said "try a
        # feeling, not a handle" to someone who had just typed `confused`,
        # which *is* a feeling — it told them to do the thing they did, and
        # the docs meanwhile promised a miss would name near faces.
        #
        # Two honest answers, in order of how likely they are to be the
        # one wanted. A typo gets the face it was reaching for. A word that
        # is simply not ours — `confused`, where the family is `puzzled` —
        # gets the vocabulary, because there is no thesaurus here and a
        # bridge built by string distance would either miss it or, tuned
        # looser, confidently offer something unrelated.
        did_you_mean = emo.nearest(query)
        if did_you_mean:
            names = " · ".join(e.name for e in did_you_mean)
            print(f"[brnrd emotes] no face named {query!r}. Did you mean: {names}")
        else:
            print(f"[brnrd emotes] no face named {query!r}.")
        vocabulary = emo.families()
        if vocabulary:
            print(
                "[brnrd emotes] the palette is organised by these feelings — "
                "any of them finds its faces:"
            )
            for line in textwrap.wrap(" · ".join(vocabulary), width=72):
                print(f"    {line}")
        return 1

    for e in rows:
        cycles = " / ".join(" ".join(seq) for seq in e.sequences)
        rest = "" if e.rest is None else f"  rest {e.rest}"
        # The family is printed because it is the word the *next* search
        # should use: a resident that found `fine_` by typing "satisfied"
        # learns the handle, and one that found it by typing "clean diff"
        # learns the family it belongs to. Handles are marks; families are
        # the way back in.
        family = f"  {e.family}" if e.family else ""
        print(f"{e.name:<10} {cycles}{rest}  pitch {e.pitch:.2f}  [{e.kind}]{family}")
        print(f"           {e.trigger}")
    if not args.all and len(rows) >= 12:
        print("[brnrd emotes] top matches only — narrow the query, or --all")
    return 0


#: The hand-declared row for the one chip `BAR_SEGMENTS` cannot carry itself
#: (design-the-live-loop.md §Round 2026-08-07): `pending_unknown` renders
#: inline in `_render_bar` when the pending count is unreadable, so it has a
#: `SEGMENT_CLASS` entry but no `_BarSegment` vocabulary row to read a
#: `meaning` off. Declared once, here, rather than left for `cmd_legend` to
#: invent prose at print time.
_PENDING_UNKNOWN_MEANING = (
    "the pending-event count is unreadable this boundary — the portal "
    "capsule did not provide one. Never rendered as a false zero: this "
    "chip stands in for the unknown until a real count comes back."
)


def cmd_legend(args):
    """Print the boundary bar's fixed chip vocabulary — `brnrd legend`.

    One source of truth, read out rather than restated: every row here is a
    live field off :data:`brr.hooks.BAR_SEGMENTS`, so the command can never
    drift from what the bar actually renders (design-the-live-loop.md's
    "#1200 is not new design work — it is a specific gap inside a shape
    already built" — `BAR_SEGMENTS.meaning` was always ready to print, only
    the wire didn't carry it). Read-only, no options, no state: the cheapest
    of the three shapes that round left open (full-at-boot /
    first-seen-only / on-demand) — a resident opaque on `!N` or `mood?`
    calls this instead of grepping `hooks.py`.
    """
    from . import hooks

    for segment in hooks.BAR_SEGMENTS:
        print(f"{segment.glyph} · {segment.key} · {segment.klass} — {segment.meaning}")
    print(f"✉? · pending_unknown · {hooks.OBLIGATION} — {_PENDING_UNKNOWN_MEANING}")
    return 0


def _wake_outbox_dir() -> Path | None:
    """This run's outbox directory, or ``None`` outside a wake.

    Deliberately not a second resolution path: ``hooks.HookContext`` is the
    one place that reads ``BRR_OUTBOX_DIR`` and falls back to the portal
    file's parent, and it is what every other control-file consumer already
    trusts. Constructing it is pure environment parsing — no I/O.
    """
    from . import hooks

    return hooks.HookContext(dict(os.environ)).outbox_dir


def _resolve_explicit_outbox(raw: str) -> tuple[Path | None, str | None]:
    """Absolutize and validate an *explicit* ``--outbox`` argument.

    Call this only for a caller-typed ``--outbox`` value — never for the
    env-derived fallback (``_wake_outbox_dir()``), which must stay lenient:
    an absent live run there is a legitimate "nothing to report", and
    ``do.read_portal_state`` is deliberately built to read that case as
    absence rather than error. An *explicit* path that isn't a real
    directory is unambiguously a caller mistake, so this fails loud, naming
    the path it tried, instead of a relative ``Path`` surviving unresolved
    into I/O and surfacing as a raw ``OSError`` traceback deep inside
    ``tempfile.mkstemp`` (issue #1337).

    Resolution base is deliberately left as plain ``Path.resolve()``
    (process cwd) — *which* anchor a relative ``--outbox`` should resolve
    against (cwd vs. a repo-root env var) is a design call out of scope for
    this fix; this only turns a late, ugly crash into an early, named one
    at the same resolved path.

    Returns ``(path, None)`` on success, or ``(None, message)`` naming the
    resolved path that failed the ``is_dir()`` check.
    """
    resolved = Path(raw).expanduser().resolve()
    if not resolved.is_dir():
        return None, f"--outbox {raw!r} resolved to {resolved} — no such directory"
    return resolved, None


def cmd_mood(args):
    """Resolve a feeling to a face and write `.mood` in one shot.

    Collapses the round trip `brnrd emotes <query>` then a hand-written
    `.mood` used to leave to the resident: resolve the query through
    `brr.emotes`'s own resolver — the one `lookup` every other reader of
    `.mood` (the boundary chip, the dashboard) already trusts, per that
    module's own history (#601/#603 shipped two resolvers for one question
    and the tolerant one never reached the wire) — write the resolved
    handle, and echo the face straight back so the resident sees and owns
    it in the same boundary instead of trusting a write it cannot see
    confirmed.

    **No match writes nothing.** Same honesty bar `emotes.lookup` already
    keeps: a family word (`satisfied` is four faces) or an invented handle
    is a real miss, and guessing between candidates would be a face the
    resident didn't mean. `emotes.near_misses` names the nearest faces —
    the same line `brnrd emotes`'s own no-match case prints — so the
    resident can retry with the actual handle instead of staring at
    silence.

    `--outbox` overrides the environment for anything driving this outside
    a live wake's own process (a script, a test); environment resolution
    (`BRR_OUTBOX_DIR` / `BRR_PORTAL_STATE`, `_wake_outbox_dir`'s job) is the
    default and applies whenever the flag is not given — the same
    precedence the daemon itself uses to find a run's outbox.
    """
    import sys

    from . import emotes as emo
    from . import hooks

    explicit = str(getattr(args, "outbox", "") or "").strip()
    if explicit:
        outbox_dir, outbox_error = _resolve_explicit_outbox(explicit)
        if outbox_error:
            print(f"[brnrd mood] {outbox_error}. Nothing was written.", file=sys.stderr)
            return 1
    else:
        outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd mood] no run outbox in this environment — `brnrd mood` "
            "sets the `.mood` control file for a live brnrd run; point it "
            "at one with BRR_OUTBOX_DIR / BRR_PORTAL_STATE, or --outbox. "
            "Nothing was written.",
            file=sys.stderr,
        )
        return 1

    feeling = str(getattr(args, "feeling", "") or "").strip()
    emote = emo.lookup(feeling) if feeling else None
    if emote is None:
        near = emo.near_misses(feeling, limit=4) if feeling else []
        if near:
            names = " · ".join(e.name for e in near)
            print(
                f"[brnrd mood] no face matches {feeling!r} — nearest: "
                f"{names}. Nothing was written.",
                file=sys.stderr,
            )
        else:
            print(
                f"[brnrd mood] no face matches {feeling!r} — try a "
                "feeling, not a handle. Nothing was written.",
                file=sys.stderr,
            )
        return 1

    narration = " ".join(getattr(args, "narration", None) or []).strip()
    text = emote.name + ("\n" + narration if narration else "") + "\n"

    mood_path = outbox_dir / hooks.MOOD_NAME
    try:
        mood_path.parent.mkdir(parents=True, exist_ok=True)
        mood_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(
            f"[brnrd mood] could not write {mood_path}: {exc}. Nothing "
            "was written.",
            file=sys.stderr,
        )
        return 1

    face = emo.glyph(emote.name)
    prefix = f"{face} " if face else ""
    note = f" — {narration}" if narration else ""
    print(f"[brnrd mood] {prefix}{emote.name}{note}")
    return 0


def cmd_relic_issue(args):
    """Append an ``issue`` relic to this run's produce manifest (#686).

    The grammar always accepted ``{"kind": "issue", "number": N, "action":
    "closed"}`` — end to end, rendered and linked and counted. What it never
    had was a front door: the record only existed if the resident remembered
    the JSON shape *and* remembered that closing an issue is produce at all.
    Filing feels like output; closing feels like tidying up, so the misses
    skewed one way and a produce block reading "3 issues" was as likely to be
    a run whose closes went unrecorded as a run that filed three.

    The action flag is **required**, and that is the judgement call this
    command makes. Defaulting the bare form to ``opened`` would manufacture
    exactly the asymmetry #686 exists to remove — a resident recording a
    close in a hurry would silently file the opposite fact — and defaulting
    to *no* action would write a record that :func:`relics.issue_actions`
    counts in neither bucket, i.e. a front door onto the room the resident
    was already standing in. One retype teaches the vocabulary once; a wrong
    fact in the manifest outlives the run.

    Nothing here talks to the forge. Whether issue #686 is really closed is
    the forge's knowledge; what this run *did* is the resident's, and the
    manifest records the second.
    """
    import sys

    from . import relics

    outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd relic] no run outbox in this environment — `brnrd relic` "
            "records produce for a live brnrd run, and the daemon names the "
            "outbox through BRR_OUTBOX_DIR / BRR_PORTAL_STATE. Nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1

    raw = str(getattr(args, "number", "") or "").strip()
    number_text = raw[1:] if raw.startswith("#") else raw
    if not number_text.isdigit() or int(number_text) <= 0:
        print(
            f"[brnrd relic] not an issue number: {raw!r} — want a positive "
            "integer, e.g. `brnrd relic issue 686 --closed` (a leading # is "
            "fine). Nothing was written.",
            file=sys.stderr,
        )
        return 1
    number = int(number_text)

    action = getattr(args, "action", None)
    if action is None:
        print(
            "[brnrd relic] say which: --opened or --closed. An issue relic "
            "with no action counts as neither created nor completed, so the "
            "flag is the record, not decoration on it. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    repo = str(getattr(args, "repo", None) or "").strip().strip("/")
    if repo and repo.count("/") != 1:
        print(
            f"[brnrd relic] not a repo: {repo!r} — want owner/name, e.g. "
            "`--repo hugimuni-labs/brnrd`. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    # ``relics.append`` is the same writer the daemon's own auto-derivation
    # uses: append-only, one JSON line, and it drops the record rather than
    # corrupting a file the resident may have hand-written into. That
    # best-effort posture is right on the closeout path and wrong at a
    # prompt — a silent drop there is a resident who believes the close is
    # recorded, which is the exact failure #686 is about — so confirm the
    # file actually grew rather than reporting the intent.
    control = outbox_dir / relics.CONTROL_NAME
    try:
        before = control.stat().st_size
    except OSError:
        before = 0
    relics.append(outbox_dir, "issue", number=number, action=action,
                  repo=repo or None)
    try:
        after = control.stat().st_size
    except OSError:
        after = before
    if after <= before:
        print(
            f"[brnrd relic] could not append to {control} — nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1
    where = f" in {repo}" if repo else ""
    print(f"[brnrd relic] issue #{number} {action}{where}")
    return 0


def cmd_relic_pr(args):
    """Append a ``pr`` relic to this run's produce manifest.

    ``.pr`` (the daemon's own auto-derivation control) holds exactly one
    PR — the shape assumes a run makes at most one. A run that opens a
    second PR had no legal front door onto the same
    ``{"kind": "pr", "number": N}`` grammar :func:`relics.collect` already
    parses, so it silently under-counted against the promise blueprint.
    This is that front door, the same shape as :func:`cmd_relic_issue`:
    parse the number (bare, ``#N``, or a full forge URL, via
    :func:`forges.parse_pull_request_ref`), refuse with the shape it
    wanted on failure, and confirm the file actually grew rather than
    reporting the intent — ``relics.append`` is best-effort by design,
    right at closeout and wrong at a prompt.

    #1461: a full PR URL names its own ``owner/repo`` — the repo a sibling
    strand (or a resident recording a PR on a project other than this
    checkout's origin) actually opened the PR in. Reducing that to a bare
    number, the way :func:`forges.parse_pull_request_number` used to be the
    only option, threw the one fact away that a multi-repo run needs kept.
    ``--repo`` mirrors :func:`cmd_relic_issue`'s flag — explicit and wins
    over a URL's own reading, the same "declaration outranks inference"
    order :func:`relics._ForgeLinks._thread_repo` already uses; omitted, a
    URL's own ``owner/repo`` is kept rather than discarded, and a bare
    number/``#N`` still means "this checkout's origin", unchanged.
    """
    import sys

    from . import forges
    from . import relics

    outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd relic] no run outbox in this environment — `brnrd relic` "
            "records produce for a live brnrd run, and the daemon names the "
            "outbox through BRR_OUTBOX_DIR / BRR_PORTAL_STATE. Nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1

    raw = str(getattr(args, "number", "") or "").strip()
    parsed = forges.parse_pull_request_ref(raw)
    if not parsed:
        print(
            f"[brnrd relic] not a PR number or URL: {raw!r} — want a "
            "positive integer, `#N`, or a full forge PR URL, e.g. "
            "`brnrd relic pr 1175` or "
            "`brnrd relic pr https://github.com/o/r/pull/1175`. Nothing "
            "was written.",
            file=sys.stderr,
        )
        return 1
    url_repo, number_text = parsed
    number = int(number_text)

    repo = str(getattr(args, "repo", None) or "").strip().strip("/")
    if repo and repo.count("/") != 1:
        print(
            f"[brnrd relic] not a repo: {repo!r} — want owner/name, e.g. "
            "`--repo hugimuni-labs/brnrd`. Nothing was written.",
            file=sys.stderr,
        )
        return 1
    if not repo and url_repo:
        repo = url_repo

    summary = str(getattr(args, "summary", None) or "").strip() or None

    control = outbox_dir / relics.CONTROL_NAME
    try:
        before = control.stat().st_size
    except OSError:
        before = 0
    relics.append(outbox_dir, "pr", number=number, summary=summary,
                  repo=repo or None)
    try:
        after = control.stat().st_size
    except OSError:
        after = before
    if after <= before:
        print(
            f"[brnrd relic] could not append to {control} — nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1
    where = f" in {repo}" if repo else ""
    print(f"[brnrd relic] pr #{number}{where}")
    return 0


def cmd_promise(args):
    """Append one row to this run's blueprint — ``.promises.jsonl`` (#1008).

    The opposite tense of ``brnrd relic``: what this run *said it would
    make*, so the boundary can say what is still owed and the closeout can
    say what was not. The whole feature turns on one economics — **it works
    only if writing the promise is cheaper than breaking it** — so this verb
    is short, takes one required word, and validates nothing it does not
    have to.

    ``--release`` is the counter, and it requires ``--why``. Without a way
    out, an abandoned intent sits owed forever and the line becomes a soft
    nag with no counter — which fires at every boundary for a non-reason and
    stops being read, the death this whole family is written against.
    Requiring the reason is what keeps a withdrawal a decision rather than a
    default, and the reason rides the row so the record of the abandonment
    lives where the abandonment happened.

    **Release semantics, the call made explicit (#1060).** ``--release``
    drops ``--count`` units of *this kind* (default 1) — ``blueprint``
    subtracts the count from the kind's running total, same arithmetic as a
    positive promise, just negative. It does **not** withdraw the whole
    row: ``promise pr --count 4`` then ``promise pr --release --why …``
    (default count 1) leaves 3 still promised, even though ``--why`` reads
    like a statement about the row as a whole. Left as-is rather than
    changed to "withdraw everything of this kind" — that reading would make
    ``--why`` swallow a wider undertaking than the caller may have meant to
    take back, silently, on a single flag most callers reach for after
    shipping *some* but not all of a batch. The behavior was already
    correct; only the help text and this docstring were silent on it.
    """
    import sys

    from . import promises

    outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd promise] no run outbox in this environment — `brnrd "
            "promise` records the blueprint of a live brnrd run, and the "
            "daemon names the outbox through BRR_OUTBOX_DIR / "
            "BRR_PORTAL_STATE. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    what = str(getattr(args, "what", "") or "").strip().lower()
    if what == "kb_page":
        what = "kb"
    if what not in promises.PROMISABLE:
        print(
            f"[brnrd promise] not promisable: {what!r} — want one of "
            + ", ".join(promises.PROMISABLE)
            + ". A promise names something the run's own manifest can attest;"
            " anything else would sit owed forever. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    raw_count = getattr(args, "count", 1)
    # Not `or 1`: `0 or 1` is 1, so the falsy-default idiom silently turns
    # the one value this check exists to reject into the default. Caught by
    # `test_cli_rejects_a_nonpositive_count`, which is why it is there.
    if raw_count is None:
        raw_count = 1
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        print(
            f"[brnrd promise] --count must be a positive integer, got "
            f"{getattr(args, 'count', None)!r}. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    release = bool(getattr(args, "release", False))
    why = str(getattr(args, "why", "") or "").strip()
    if release and not why:
        print(
            "[brnrd promise] --release needs --why. Withdrawing a promise is "
            "a decision, not a default; the reason rides the row so the "
            "record lives where the abandonment happened. Nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1
    if why and not release:
        print(
            "[brnrd promise] --why only applies to --release. Use --ref to "
            "label a promise you are making. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    ref = str(getattr(args, "ref", "") or "").strip() or None

    # How much of this kind the run had *already* produced when the claim
    # was made. Without it a promise is satisfiable by its own past — driven
    # and caught on the run that wrote this feature (see
    # `promises.blueprint`). Read off the live portal snapshot the daemon
    # already maintains; unreadable snapshot ⇒ no baseline, which falls back
    # to the lenient old behaviour rather than guessing a number.
    baseline: int | None = None
    if not release:
        try:
            import json as _json

            state = _json.loads(
                (outbox_dir / "portal-state.json").read_text(encoding="utf-8")
            )
            counts = state.get("produce", {}).get("counts", {})
            if isinstance(counts, dict):
                baseline = int(counts.get(what, 0) or 0)
        except Exception:
            baseline = None

    # Same confirmed-append posture as `cmd_relic_issue` / `cmd_relic_item`:
    # `promises.append` is best-effort by design, which is right inside a
    # closeout and wrong at a prompt — verify the file grew rather than
    # report the intent. A promise that silently failed to be written is the
    # one failure this feature cannot tolerate: it would report a clean
    # blueprint for a run that made a claim.
    control = outbox_dir / promises.CONTROL_NAME
    try:
        before = control.stat().st_size
    except OSError:
        before = 0
    promises.append(
        outbox_dir, what, count=count, ref=ref,
        released=release, why=why or None, baseline=baseline,
    )
    try:
        after = control.stat().st_size
    except OSError:
        after = before
    if after <= before:
        print(
            f"[brnrd promise] could not append to {control} — nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1

    plan = promises.blueprint(promises.read(outbox_dir), None)
    owed = sum(plan.owed.values())
    verb = "released" if release else "promised"
    label = f" ({ref})" if ref else ""
    reason = f" — {why}" if why else ""
    print(f"[brnrd promise] {verb} {count} {what}{label}{reason} · owed {owed}")
    return 0


def cmd_relic_item(args):
    """Append an ``item`` relic — the warp item this run serves (#972).

    THE WELD's manifest half: the run's produce manifest carries the
    id of the warp item that ignited it, so the item and
    the run's cloth line can point at each other through resolver addresses
    instead of re-listing each other's content. The daemon writes this relic
    itself when the dispatching event body names the address
    (``weld.annotate_ignition``); this front door is for the run that only
    learns mid-flight which item it is serving.

    Validation is grammar-only and strict: an id outside the slug grammar
    (lowercase ``[a-z0-9-]``, starting alphanumeric) is refused with the
    shape it wanted and **never written** — a malformed id in the manifest
    would be an unresolvable claim every downstream renderer has to carry.
    Whether the id *resolves* (the item file exists in the account home) is
    deliberately not checked here: the account surface is the daemon's
    knowledge, not the run environment's, and capture skips an unresolvable
    id with a log line rather than guessing.
    """
    import sys

    from . import relics
    from . import weld

    outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd relic] no run outbox in this environment — `brnrd relic` "
            "records produce for a live brnrd run, and the daemon names the "
            "outbox through BRR_OUTBOX_DIR / BRR_PORTAL_STATE. Nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1

    address = str(getattr(args, "address", "") or "").strip()
    if not weld.is_item_address(address):
        print(
            f"[brnrd relic] not an item id: {address!r} — want the item "
            "file's basename in surface/warp/, lowercase [a-z0-9-], e.g. "
            "`brnrd relic item w-42`. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    # Same confirmed-append posture as `cmd_relic_issue`: `relics.append` is
    # best-effort by design, which is right at closeout and wrong at a
    # prompt — verify the file grew rather than report the intent.
    control = outbox_dir / relics.CONTROL_NAME
    try:
        before = control.stat().st_size
    except OSError:
        before = 0
    relics.append(outbox_dir, "item", address=address)
    try:
        after = control.stat().st_size
    except OSError:
        after = before
    if after <= before:
        print(
            f"[brnrd relic] could not append to {control} — nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1
    print(f"[brnrd relic] item {address}")
    return 0


def cmd_relic_comment(args):
    """Append a ``comment`` relic to this run's produce manifest (#1060).

    Issue produce got its own front door in #686 because filing/closing an
    issue is the one relic kind the daemon cannot see happen (``gh`` runs
    inside the resident's shell). A comment left without an issue action
    attached — a review comment, a reply on someone else's thread — is the
    same species of invisible-to-the-daemon fact, and until now had no
    subcommand at all: ``relics.label`` already renders
    ``{"kind": "comment", "on": …}`` for the ``## Produce`` block (the
    ``on`` field names what the comment was posted on), but reaching it
    meant hand-writing the JSONL line.
    """
    import sys

    from . import relics

    outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd relic] no run outbox in this environment — `brnrd relic` "
            "records produce for a live brnrd run, and the daemon names the "
            "outbox through BRR_OUTBOX_DIR / BRR_PORTAL_STATE. Nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1

    on = str(getattr(args, "on", "") or "").strip()
    if not on:
        print(
            "[brnrd relic] say what the comment was on, e.g. `brnrd relic "
            "comment 'issue #903 — stale-open sweep'`. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    control = outbox_dir / relics.CONTROL_NAME
    try:
        before = control.stat().st_size
    except OSError:
        before = 0
    relics.append(outbox_dir, "comment", on=on)
    try:
        after = control.stat().st_size
    except OSError:
        after = before
    if after <= before:
        print(
            f"[brnrd relic] could not append to {control} — nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1
    print(f"[brnrd relic] comment on {on}")
    return 0


def cmd_relic_message(args):
    """Append a ``message`` relic to this run's produce manifest (#1060).

    The manifest's catch-all for outbound communication that is neither the
    run's own terminal reply (auto-reported as a ``reply`` relic at
    closeout) nor a forge comment (``brnrd relic comment``) — a message sent
    by hand through a tool outside brnrd's own delivery portals. ``note`` is
    what it said or was about; ``--channel`` is where it went, the same
    grammar ``relics.label`` and ``docs/portals.md`` already document:
    ``{"kind": "message", "channel": "telegram", "note": "design fork
    answered"}``.
    """
    import sys

    from . import relics

    outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd relic] no run outbox in this environment — `brnrd relic` "
            "records produce for a live brnrd run, and the daemon names the "
            "outbox through BRR_OUTBOX_DIR / BRR_PORTAL_STATE. Nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1

    note = str(getattr(args, "note", "") or "").strip()
    if not note:
        print(
            "[brnrd relic] say what the message was, e.g. `brnrd relic "
            "message 'design fork answered' --channel telegram`. Nothing "
            "was written.",
            file=sys.stderr,
        )
        return 1
    channel = str(getattr(args, "channel", None) or "").strip() or None

    control = outbox_dir / relics.CONTROL_NAME
    try:
        before = control.stat().st_size
    except OSError:
        before = 0
    relics.append(outbox_dir, "message", note=note, channel=channel)
    try:
        after = control.stat().st_size
    except OSError:
        after = before
    if after <= before:
        print(
            f"[brnrd relic] could not append to {control} — nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1
    where = f" via {channel}" if channel else ""
    print(f"[brnrd relic] message{where}: {note}")
    return 0


def cmd_relic_file(args):
    """Append a ``file`` relic to this run's produce manifest (#1060).

    For a file this run produced outside a commit on the tracked branch — a
    report handed off by path, an artifact written to a share. ``relics.
    label`` already renders ``{"kind": "file", "path": …}`` for the
    ``## Produce`` block; this is the front door onto that grammar, same
    shape as ``brnrd relic item``'s single-field address.
    """
    import sys

    from . import relics

    outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd relic] no run outbox in this environment — `brnrd relic` "
            "records produce for a live brnrd run, and the daemon names the "
            "outbox through BRR_OUTBOX_DIR / BRR_PORTAL_STATE. Nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1

    path = str(getattr(args, "path", "") or "").strip()
    if not path:
        print(
            "[brnrd relic] say which file, e.g. `brnrd relic file "
            "/tmp/report.md`. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    control = outbox_dir / relics.CONTROL_NAME
    try:
        before = control.stat().st_size
    except OSError:
        before = 0
    relics.append(outbox_dir, "file", path=path)
    try:
        after = control.stat().st_size
    except OSError:
        after = before
    if after <= before:
        print(
            f"[brnrd relic] could not append to {control} — nothing was "
            "written.",
            file=sys.stderr,
        )
        return 1
    print(f"[brnrd relic] file {path}")
    return 0


def _reconstruct_do_ops(ordered_ops):
    """Pair ``--reply``/``--gate`` with the ``--body-file``/``--body`` that
    immediately follows them in ``ordered_ops`` (command-line order, from
    ``_OrderedAppend`` — only ``reply``/``gate``/``body_file``/``body``
    entries, ``--note`` is not routed through this list).

    Returns ``(replies, gates, error)``: ``replies``/``gates`` are lists of
    ``(target, body_text)``; ``error`` is a human string naming the first
    unpaired flag, or ``None``. A ``--body-file`` reads its file eagerly here
    so a bad path fails before anything is staged, not mid-batch.
    """
    replies: list[tuple[str, str]] = []
    gates: list[tuple[str, str]] = []
    pending: tuple[str, str] | None = None
    for dest, value in ordered_ops:
        if dest in ("reply", "gate"):
            if pending is not None:
                kind, target = pending
                return replies, gates, (
                    f"--{kind} {target} has no --body-file/--body before "
                    f"the next --{dest}"
                )
            pending = (dest, value)
            continue
        # dest in ("body_file", "body")
        if pending is None:
            flag = "--body-file" if dest == "body_file" else "--body"
            return replies, gates, f"{flag} given with no preceding --reply/--gate"
        kind, target = pending
        if dest == "body_file":
            try:
                text = Path(value).read_text(encoding="utf-8")
            except OSError as exc:
                return replies, gates, f"could not read {value!r}: {exc}"
        else:
            if kind == "gate":
                return replies, gates, "--gate only pairs with --body-file, not --body"
            text = value
        (replies if kind == "reply" else gates).append((target, text))
        pending = None
    if pending is not None:
        kind, target = pending
        return replies, gates, f"--{kind} {target} has no --body-file/--body"
    return replies, gates, None


def _do_render(verb: str, label: str, status: str, detail: str) -> tuple[str, bool]:
    from . import do as do_mod

    if status == do_mod.OK:
        return f"{verb} {label} ✓", True
    if status == do_mod.QUEUED:
        return f"{verb} {label} ? {detail or 'still queued'}", False
    return f"{verb} {label} ✗ {detail}", False


def _do_mood(do_mod, emo, outbox_dir: Path, feeling: str, note: str | None) -> tuple[str, bool]:
    resolved = emo.lookup(feeling)
    if resolved is None:
        misses = emo.near_misses(feeling)
        tail = (
            " — try: " + ", ".join(e.name for e in misses)
            if misses else ""
        )
        return f"mood {feeling} ✗ no match{tail}", False
    do_mod.write_mood(outbox_dir, resolved.name, note)
    glyph = resolved.frames[0] if resolved.frames else resolved.name
    return f"mood {glyph} {resolved.name} ✓", True


def _do_note(do_mod, outbox_dir: Path, event_id: str, index: int, timeout: float) -> tuple[str, bool]:
    from . import hooks as hooks_mod

    short = hooks_mod._short_event_id(event_id)
    before = do_mod.notices_of(do_mod.read_portal_state(outbox_dir))
    path = do_mod.stage_note(outbox_dir, event_id, index=index)
    status, detail = do_mod.await_verdict(
        outbox_dir, path, before, ("note", event_id), timeout_seconds=timeout,
    )
    return _do_render("note", short, status, detail)


def _do_reply(
    do_mod, outbox_dir: Path, event_id: str, body: str, index: int, timeout: float,
) -> tuple[str, bool]:
    from . import hooks as hooks_mod

    short = hooks_mod._short_event_id(event_id)
    before = do_mod.notices_of(do_mod.read_portal_state(outbox_dir))
    path = do_mod.stage_reply(outbox_dir, event_id, body, index=index)
    status, detail = do_mod.await_verdict(
        outbox_dir, path, before, ("reply", event_id), timeout_seconds=timeout,
    )
    return _do_render("reply", short, status, detail)


def _do_gate(
    do_mod, outbox_dir: Path, gate_name: str, body: str, index: int, timeout: float,
) -> tuple[str, bool]:
    before = do_mod.notices_of(do_mod.read_portal_state(outbox_dir))
    path = do_mod.stage_gate(outbox_dir, gate_name, body, index=index)
    status, detail = do_mod.await_verdict(
        outbox_dir, path, before, ("gate", gate_name), timeout_seconds=timeout,
    )
    return _do_render("gate", gate_name, status, detail)


def _do_card(do_mod, outbox_dir: Path, filename: str) -> tuple[str, bool]:
    try:
        text = Path(filename).read_text(encoding="utf-8")
    except OSError as exc:
        return f"card ✗ could not read {filename}: {exc}", False
    do_mod.write_card(outbox_dir, text)
    return "card ✓", True


def cmd_do(args):
    """``brnrd do`` — stage outbox verbs, read the daemon's verdict back in
    the same boundary as the act (`kb/design-...`, evts dt2m/khiw/nkq5).

    Porcelain over the existing outbox grammar (``docs/portals.md``), not a
    new channel: every verb here stages exactly the file a resident would
    stage by hand, then waits for the daemon's own drain to consume it and
    diffs ``portal-state.json`` -> ``notices`` for a refusal it names. See
    ``brr.do``'s module docstring for the verdict-observation contract per
    verb and its one named daemon-side gap (a notice carries no
    per-directive source id, so correlation is a text-substring heuristic).

    ``-- <command> [args…]`` (split out of argv in ``main`` before this
    parser ever sees it) runs after the verbs are staged: verdict lines move
    to stderr (so the command's own stdout stays pipeable) and the command
    replaces this process via ``os.execvp`` — argv passthrough, execvp
    semantics, never ``sh -c`` — so its stdout/stderr/exit code become
    ``brnrd do``'s own. No ``--`` -> the verdict lines are the only output,
    same as before this existed.
    """
    import os
    import sys

    from . import do as do_mod
    from . import emotes as emo

    passthrough = getattr(args, "passthrough", None)
    out = sys.stderr if passthrough else sys.stdout
    timeout = args.timeout if args.timeout is not None else do_mod.DEFAULT_TIMEOUT_SECONDS

    if args.mood_note and not args.mood:
        print("[brnrd do] --mood-note only applies with --mood", file=sys.stderr)
        return 1

    ordered_ops = getattr(args, "_do_ops", None) or []
    replies, gates, pairing_error = _reconstruct_do_ops(ordered_ops)
    if pairing_error:
        print(f"[brnrd do] {pairing_error}. Nothing was staged.", file=sys.stderr)
        return 1

    notes = args.note or []
    has_verbs = bool(args.mood or notes or replies or gates or args.card)

    explicit_outbox = str(getattr(args, "outbox", "") or "").strip()
    if explicit_outbox:
        outbox_dir, outbox_error = _resolve_explicit_outbox(explicit_outbox)
        if outbox_error:
            print(f"[brnrd do] {outbox_error}. Nothing was staged.", file=sys.stderr)
            return 1
    else:
        outbox_dir = _wake_outbox_dir()

    if not has_verbs:
        if outbox_dir is None:
            print(
                "[brnrd do] no run outbox in this environment — pass "
                "--outbox, or run inside a daemon wake.",
                file=sys.stderr,
            )
            return 1
        payload = do_mod.read_portal_state(outbox_dir)
        if not payload:
            print(
                f"[brnrd do] no live portal-state.json under {outbox_dir}",
                file=sys.stderr,
            )
            return 1
        print(do_mod.format_snapshot(payload), file=out)
        if not passthrough:
            return 0
    else:
        if outbox_dir is None:
            print(
                "[brnrd do] no run outbox in this environment — `brnrd do` "
                "stages directives into a live run's outbox; pass "
                "--outbox, or run inside a daemon wake. Nothing was "
                "written.",
                file=sys.stderr,
            )
            return 1

        segments: list[str] = []
        any_failed = False

        if args.mood:
            seg, ok = _do_mood(do_mod, emo, outbox_dir, args.mood, args.mood_note)
            segments.append(seg)
            any_failed = any_failed or not ok

        for i, event_id in enumerate(notes):
            seg, ok = _do_note(do_mod, outbox_dir, event_id, i, timeout)
            segments.append(seg)
            any_failed = any_failed or not ok

        for i, (event_id, body) in enumerate(replies):
            seg, ok = _do_reply(do_mod, outbox_dir, event_id, body, i, timeout)
            segments.append(seg)
            any_failed = any_failed or not ok

        for i, (gate_name, body) in enumerate(gates):
            seg, ok = _do_gate(do_mod, outbox_dir, gate_name, body, i, timeout)
            segments.append(seg)
            any_failed = any_failed or not ok

        if args.card:
            seg, ok = _do_card(do_mod, outbox_dir, args.card)
            segments.append(seg)
            any_failed = any_failed or not ok

        print(" · ".join(segments), file=out)

        if not passthrough:
            return 1 if any_failed else 0

    # passthrough present (verbs or not): argv replacement, not a subprocess
    # spawn — this process becomes the command, so its stdout/stderr/exit
    # code are structurally "brnrd do's own" rather than something this
    # function has to relay.
    out.flush()
    try:
        os.execvp(passthrough[0], passthrough)
    except OSError as exc:
        print(
            f"[brnrd do] passthrough command not found: {passthrough[0]!r} "
            f"({exc})",
            file=sys.stderr,
        )
        return 127
    return 0  # pragma: no cover — unreachable: execvp never returns on success


def _item_context(*, create_dir: bool = False):
    """Resolve the account's warp directory from the current repo.

    Returns ``(warp_root, error)`` — exactly one is non-None. Read verbs
    pass ``create_dir=False`` and get an error when no warp exists;
    ``brnrd item new`` scaffolds the directory on first use (a mint is an
    act, and the first item is what brings the warp into being).
    """
    from . import account as account_mod
    from . import config as conf

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    ctx = account_mod.resolve_context(repo_root, cfg, create=False)
    if ctx is None or not getattr(ctx, "enabled", False):
        return None, "no enabled account home for this repo — the warp lives there"
    surface = account_mod.work_surface_path(ctx)
    from .items import WARP_DIRNAME

    warp_root = surface / WARP_DIRNAME
    if not warp_root.is_dir():
        if not create_dir:
            return None, (
                f"no warp yet — nothing under {warp_root}. "
                "`brnrd item new` mints the first item."
            )
        warp_root.mkdir(parents=True, exist_ok=True)
    return warp_root, None


def _resolve_item_arg(warp_root, raw: str):
    """An id, or a unique case-insensitive fragment of an open item's
    headline. Returns ``(item, error)`` — ambiguity is an error, never a
    coin flip."""
    from . import items as items_mod

    raw = (raw or "").strip()
    path = items_mod.resolve_item(warp_root, raw)
    if path is not None:
        return items_mod.parse_item(path), None
    candidates = [
        item
        for item in items_mod.load_items(warp_root)
        if item.state == "open" and raw.lower() in item.headline.lower()
    ]
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, f"nothing matches {raw!r} — not an id, not an open headline"
    names = " · ".join(f"{item.id} ({item.headline})" for item in candidates[:6])
    return None, f"{raw!r} is ambiguous: {names}"


def _resolve_goal_arg(warp_root, raw: str):
    """Same resolution as ``_resolve_item_arg``, plus the goal-only gate:
    an id/headline that resolves to a non-goal item is refused, the same
    way an unknown id is — "goal record" and "goal show" only ever touch
    ``type: goal`` files."""
    from . import items as items_mod

    item, err = _resolve_item_arg(warp_root, raw)
    if err:
        return None, err
    if item.type != items_mod.GOAL_TYPE:
        return None, f"{item.id} is a {item.type or 'untyped'} item, not a goal"
    return item, None


def cmd_item_list(args):
    from . import items as items_mod

    warp_root, err = _item_context()
    if err:
        print(f"[brnrd item] {err}")
        return 0
    if getattr(args, "all", False):
        for item in items_mod.load_items(warp_root):
            mark = {"open": "·", "done": "✓", "retired": "✕"}[item.state]
            topics = (" [" + " ".join(item.topics) + "]") if item.topics else ""
            print(f"{mark} {item.id} {item.type or 'untyped'}{topics} — {item.headline}")
        return 0
    # `render_index` already puts goals in their own leading band, ahead of
    # ready/held — this list verb and the wake's composed index are the
    # same function, so they never drift (design-goal-oriented-
    # engineering.md §"the wake's composed open-items index").
    index = items_mod.render_index(warp_root)
    print(index if index else "[brnrd item] the warp is bare")
    return 0


def cmd_item_new(args):
    import sys

    from . import items as items_mod

    warp_root, err = _item_context(create_dir=True)
    if err:
        print(f"[brnrd item] {err}", file=sys.stderr)
        return 1
    item_id = items_mod.allocate_id(warp_root, args.item_type)
    text = items_mod.new_item_text(
        args.headline.strip(),
        item_type=args.item_type,
        topics=(args.topics or "").split() or None,
        needs=(args.needs or "").split() or None,
        advances=(args.advances or "").split() or None,
        metric=(args.metric or None),
        target=(args.target or None),
        horizon=(args.horizon or None),
        prompt=(args.prompt or None),
        refs=(args.refs or None),
        body=(args.body or None),
    )
    path = warp_root / f"{item_id}.md"
    path.write_text(text, encoding="utf-8")
    print(f"{item_id} — {path}")
    return 0


def _item_receipt_date() -> str:
    import datetime as _dt

    return _dt.date.today().isoformat()


def cmd_item_done(args):
    import os
    import sys

    from . import items as items_mod

    warp_root, err = _item_context()
    if err:
        print(f"[brnrd item] {err}", file=sys.stderr)
        return 1
    item, err = _resolve_item_arg(warp_root, args.id)
    if err:
        print(f"[brnrd item] {err}", file=sys.stderr)
        return 1
    run_id = (args.run or os.environ.get("BRR_RUN_ID") or "").strip() or None
    if not items_mod.mark_done(item.path, date=_item_receipt_date(), run_id=run_id):
        print(
            f"[brnrd item] {item.id} is already {item.state} — a second "
            "receipt would rewrite history. Nothing was written.",
            file=sys.stderr,
        )
        return 1
    print(f"{item.id} done — {item.headline}")
    return 0


def cmd_item_retire(args):
    import sys

    from . import items as items_mod

    warp_root, err = _item_context()
    if err:
        print(f"[brnrd item] {err}", file=sys.stderr)
        return 1
    item, err = _resolve_item_arg(warp_root, args.id)
    if err:
        print(f"[brnrd item] {err}", file=sys.stderr)
        return 1
    if not items_mod.mark_retired(
        item.path, date=_item_receipt_date(), why=(args.why or None)
    ):
        print(
            f"[brnrd item] {item.id} is already {item.state}. Nothing was written.",
            file=sys.stderr,
        )
        return 1
    print(f"{item.id} retired — {item.headline}")
    return 0


def cmd_goal_record(args):
    import sys

    from . import items as items_mod

    warp_root, err = _item_context()
    if err:
        print(f"[brnrd goal] {err}", file=sys.stderr)
        return 1
    goal, err = _resolve_goal_arg(warp_root, args.id)
    if err:
        print(f"[brnrd goal] {err}", file=sys.stderr)
        return 1
    key = args.key.strip()
    if not key:
        print("[brnrd goal] key must not be empty", file=sys.stderr)
        return 1
    reading = items_mod.append_reading(
        warp_root,
        goal.id,
        key,
        args.value,
        source=(args.source or "").strip(),
        note=(args.note or None),
    )
    source_note = f" via {reading.source}" if reading.source else ""
    print(f"{goal.id} {reading.key} = {items_mod.format_value(reading.value)}{source_note} ({reading.ts})")
    return 0


def cmd_goal_show(args):
    import sys

    from . import items as items_mod

    warp_root, err = _item_context()
    if err:
        print(f"[brnrd goal] {err}", file=sys.stderr)
        return 1
    goal, err = _resolve_goal_arg(warp_root, args.id)
    if err:
        print(f"[brnrd goal] {err}", file=sys.stderr)
        return 1
    print(f"{goal.id} — {goal.headline}")
    spine = " ".join(
        f"{label}: {value}"
        for label, value in (
            ("metric", goal.metric), ("target", goal.target), ("horizon", goal.horizon)
        )
        if value
    )
    if spine:
        print(spine)
    readings = items_mod.load_readings(warp_root, goal.id)
    if not readings:
        print("no readings yet")
        return 0
    summary = items_mod.reading_summary(readings)
    for key in sorted(summary):
        info = summary[key]
        delta = (
            f" (Δ{items_mod.format_delta(info.delta)} vs previous)"
            if info.previous is not None
            else ""
        )
        plural = "" if info.count == 1 else "s"
        print(
            f"{key}: {items_mod.format_value(info.latest.value)}{delta} "
            f"· {info.count} sample{plural} "
            f"· min {items_mod.format_value(info.min)} · max {items_mod.format_value(info.max)}"
        )
    return 0


def _home_root_context():
    """Resolve the account home root from the current repo.

    Returns ``(home_root, error)`` — exactly one is non-None. Mirrors
    ``_item_context``: the queue and the envoy registry are account
    organs, so no enabled account home means neither exists here.
    """
    from . import account as account_mod
    from . import config as conf

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    ctx = account_mod.resolve_context(repo_root, cfg, create=False)
    if ctx is None or not getattr(ctx, "enabled", False):
        return None, "no enabled account home for this repo"
    return account_mod.context_home_root(ctx), None


def _queue_item_line(item) -> str:
    status = str(item.get("status") or "?")
    mark = {"arrived": "✉", "answered": "✓", "noted": "·", "dropped": "✕"}.get(
        status, "?"
    )
    source = str(item.get("source") or "?")
    author = str(item.get("author") or "")
    who = f" {author}" if author else ""
    kind = str(item.get("kind") or "")
    kind_s = f" [{kind}]" if kind else ""
    body = " ".join(str(item.get("body") or "").split())
    if len(body) > 80:
        body = body[:77] + "…"
    return f"{mark} {item.get('id')} {source}{who}{kind_s} {status} — {body}"


def cmd_queue_list(args):
    import sys

    from . import envoys as envoys_mod

    home_root, err = _home_root_context()
    if err:
        print(f"[brnrd queue] {err}", file=sys.stderr)
        return 1
    items = envoys_mod.list_items(home_root, status=getattr(args, "status", None))
    if not items:
        arrived = envoys_mod.list_items(home_root, status=envoys_mod.QUEUE_OPEN_STATUS)
        scope = f"status={args.status} " if getattr(args, "status", None) else ""
        print(f"[brnrd queue] no {scope}items ({len(arrived)} arrived in the drawer)")
        return 0
    for item in items:
        print(_queue_item_line(item))
    return 0


def cmd_queue_show(args):
    import sys

    from . import envoys as envoys_mod

    home_root, err = _home_root_context()
    if err:
        print(f"[brnrd queue] {err}", file=sys.stderr)
        return 1
    path = envoys_mod.queue_dir(home_root) / f"{args.id}.md"
    if not path.is_file():
        print(f"[brnrd queue] no item {args.id}", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_queue_record(args):
    import sys

    from . import envoys as envoys_mod

    home_root, err = _home_root_context()
    if err:
        print(f"[brnrd queue] {err}", file=sys.stderr)
        return 1
    if bool(args.body) == bool(args.body_file):
        print("[brnrd queue] exactly one of --body / --body-file", file=sys.stderr)
        return 2
    if args.body_file:
        body = (
            sys.stdin.read()
            if args.body_file == "-"
            else Path(args.body_file).read_text(encoding="utf-8")
        )
    else:
        body = args.body
    meta: dict[str, object] = {}
    for pair in args.meta:
        if "=" not in pair:
            print(f"[brnrd queue] --meta wants KEY=VALUE, got {pair!r}", file=sys.stderr)
            return 2
        key, value = pair.split("=", 1)
        meta[key.strip()] = value.strip()
    try:
        path = envoys_mod.record(home_root, args.channel, body, **meta)
    except ValueError as exc:
        print(f"[brnrd queue] {exc}", file=sys.stderr)
        return 2
    print(f"[brnrd queue] recorded {path.stem}")
    return 0


def cmd_queue_close(args):
    import sys

    from . import envoys as envoys_mod

    home_root, err = _home_root_context()
    if err:
        print(f"[brnrd queue] {err}", file=sys.stderr)
        return 1
    if args.verb == "dropped" and not args.why:
        print("[brnrd queue] dropped needs --why", file=sys.stderr)
        return 2
    try:
        envoys_mod.close(home_root, args.id, args.verb, why=args.why)
    except ValueError as exc:
        print(f"[brnrd queue] {exc}", file=sys.stderr)
        return 1
    print(f"[brnrd queue] {args.id} → {args.verb}")
    return 0


def cmd_envoy_list(args):
    import sys

    from . import envoys as envoys_mod

    home_root, err = _home_root_context()
    if err:
        print(f"[brnrd envoy] {err}", file=sys.stderr)
        return 1
    rows = envoys_mod.list_envoys(home_root)
    if not rows:
        print(
            "[brnrd envoy] no envoys — the registry is "
            f"{envoys_mod.envoys_dir(home_root)}"
        )
        return 0
    for row in rows:
        state = "on " if row.get("enabled") else "off"
        platform = str(row.get("platform") or "?")
        handle = str(row.get("handle") or "?")
        policy = str(row.get("policy") or envoys_mod.DEFAULT_POLICY)
        print(f"{state} {row['slug']} · {platform} {handle} · policy: {policy}")
    return 0


def cmd_kb(args):
    from . import account as account_mod
    from . import config as conf
    from . import knowledge

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    # Resolve read-only first: `brnrd kb` is a read, never an act that means
    # to create a home. `create=False` never scaffolds a project/account
    # home (nor the `.brnrd-kb` checkout below), only reports which one
    # `resolve_context` would pick and why (#1193) — a read that used to
    # silently mint an empty project home and then report cleanly on it,
    # indistinguishable from a genuinely empty, real kb.
    resolved_ctx = account_mod.resolve_context(repo_root, cfg, create=False)
    reason = account_mod.resolution_reason(resolved_ctx, repo_root)
    checkout = knowledge.ensure_checkout(repo_root, cfg, create=False)

    if not args.query:
        from . import kb_health
        kb_dir = knowledge.active_kb_dir(repo_root, cfg)
        # `active_kb_dir` documents None as a *finding* — "this repo has no kb
        # at all yet" — but `compute_graph_stats(root, None)` reads the same
        # value as "unspecified" and quietly defaults to ``repo_root / "kb"``
        # (kb_health.py, `kb_dir.resolve() if kb_dir is not None else ...`).
        # One value, two meanings, meeting at this call site: passing the None
        # straight through converts "there is no kb here" into a confident
        # report about a different directory. Refuse instead.
        if kb_dir is None:
            print("[brnrd kb] no kb resolved for this root — nothing to report")
            print(f"[brnrd kb] repo root: {repo_root} — {reason}")
            print(f"[brnrd kb] checkout: {checkout}")
            return 1
        stats = kb_health.compute_graph_stats(repo_root, kb_dir)
        report = kb_health.format_graph_stats(stats)
        # A zero-page kb renders as the empty string. Printing that and
        # returning 0 is a silent success — strictly worse than the `exit 2`
        # this command replaced, which at least said something was wrong.
        if not report.strip():
            print(f"[brnrd kb] kb dir holds no pages: {kb_dir} — {reason}")
            return 1
        # Name the directory that was walked. There are several plausible
        # knowledge roots for one repo — the account-scoped home, a
        # project-scoped home, the `.brnrd-kb` checkout clone, a committed
        # `kb/` — and which one wins depends on where this command was run
        # from. A wake comparing this report against the page counts in its own
        # kb-health block cannot reconcile the two unless the report says which
        # corpus it walked. One line, and the ambiguity stops being a
        # twenty-minute investigation. The reason (account-linked vs. project
        # fallback) says *why* that corpus, not just which (#1193).
        print(f"[brnrd kb] graph for: {kb_dir} — {reason}")
        print(report)
        return 0

    hits = knowledge.search(repo_root, args.query, cfg, limit=args.limit)
    if not hits:
        print(f"[brnrd kb] no matches for {args.query!r}")
        print(f"[brnrd kb] checkout: {checkout} — {reason}")
        return 1
    for hit in hits:
        rel = hit.path
        try:
            rel = hit.path.relative_to(repo_root)
        except ValueError:
            pass
        print(f"{hit.source}: {rel}:{hit.line_no}: {hit.line}")
    return 0


def _notes_age(mtime: float | None) -> str:
    """``mtime`` as a coarse "how long since anyone wrote here"."""
    import time

    if not mtime:
        return "—"
    delta = max(0.0, time.time() - mtime)
    for size, unit in ((86400.0, "d"), (3600.0, "h"), (60.0, "m")):
        if delta >= size:
            return f"{int(delta // size)}{unit} ago"
    return "just now"


def _notes_verdicts(findings: list) -> dict[str, list]:
    """Group findings by the surface filename they name.

    A finding's ``target`` leads with the file it is about
    (``pitfalls.md § …``, ``workflow.md §Autonomy``,
    ``surface/ledger/decisions.md``), so the map can put a verdict on the
    row it belongs to without the checks having to know the registry's key
    names. Anything that matches no row is still printed — under
    ``unattributed``, never dropped, because a finding filtered out of a
    map reads exactly like a clean surface.

    The key is the target's **path suffix**, not its basename. Two
    registered surfaces are called ``index.md`` (``surface/index.md`` and
    the kb's), so a basename key paints one surface's finding onto the
    other's row and makes a healthy surface exit non-zero.
    """
    out: dict[str, list] = {}
    for finding in findings:
        head = str(finding.target).split(" ")[0].split("§")[0].strip()
        out.setdefault(head.strip("/"), []).append(finding)
    return out


def _notes_match_verdicts(resolved, verdicts: dict[str, list]) -> list:
    """The findings belonging to one resolved surface row.

    A target matches when a resolved path *ends with* it as a whole path
    segment — ``surface/index.md`` matches
    ``…/home/surface/index.md`` and not ``…/knowledge/repos/x/index.md``.
    """
    candidates = [p.as_posix() for p in resolved.paths]
    candidates.append(resolved.surface.path_hint)
    hits: list = []
    for head, findings in verdicts.items():
        if not head:
            continue
        if any(c == head or c.endswith("/" + head) for c in candidates):
            hits.extend(findings)
    return hits


def cmd_notes(args):
    """``brnrd notes`` — the map, one surface, or the checks in full.

    Three shapes, one verb, matching the three questions a resident
    actually asks about its own note surfaces:

    - bare — **the map**: every registered surface, what is on disk for
      it, how it rides a wake, and its check verdict. The answer to
      "which of these am I even maintaining, and is any of it broken?"
    - ``<surface>`` — that surface's grammar, its readers, its budget
      rule, and its current findings. The answer to "what shape does this
      file want *before* I write into it" — which is the whole problem
      this verb exists for: a resident that writes the wrong key gets
      silence, not an error.
    - ``check`` — the same checks the wake preflight runs, printed in
      full rather than wake-sized.

    ``--json`` on any of the three. The map's per-block byte costs come
    from the same contract manifest the wake builds, so a cost printed
    here is the cost a wake pays, not an estimate of one.
    """
    import json as json_mod

    from . import account as account_mod
    from . import config as conf
    from . import notes, notes_preflight

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    target = args.surface

    if target == "check":
        # Read-only resolution, same contract as `brnrd kb` (#1193):
        # `create=False` never scaffolds a home, and the reason names
        # whether the surfaces below are an account-linked home's or a
        # project-path fallback's — the two render identically otherwise,
        # and a freshly-would-be-created fallback answering "no findings"
        # is not the same claim as an account's "no findings".
        resolved_ctx = account_mod.resolve_context(repo_root, cfg, create=False)
        reason = account_mod.resolution_reason(resolved_ctx, repo_root)
        findings, scope = notes_preflight.scan_scoped(repo_root, cfg)
        if args.json:
            # The scope rides the JSON too. A consumer reading a bare `[]`
            # cannot tell "healthy" from "read nothing", and that is the
            # whole defect this shape exists to close.
            print(json_mod.dumps({
                "scope": {
                    "located": scope.located,
                    "registered": scope.registered,
                    "unresolved_roots": list(scope.unresolved_roots),
                },
                "resolution": reason,
                "findings": [
                    {"type": f.type, "target": f.target,
                     "severity": f.severity, "description": f.description}
                    for f in findings
                ],
            }, indent=2))
            return 0 if not findings else 1
        # **Never a bare "clean".** A clean verdict is a claim about the
        # surfaces that were actually read, so it always carries how many
        # that was — and it is not clean at all when a root went missing,
        # which `check_roots` has already turned into a finding above.
        print(f"[brnrd notes] {scope.line()} — {reason}")
        if not findings:
            print("[brnrd notes] no findings on the surfaces above")
            return 0
        for finding in findings:
            print(finding.render())
        return 1

    resolved, _roots = notes.resolve_with_roots(repo_root, cfg)
    findings, scope = notes_preflight.scan_scoped(repo_root, cfg)
    verdicts = _notes_verdicts(findings)

    if target:
        row = next((r for r in resolved if r.surface.key == target), None)
        if row is None:
            keys = ", ".join(r.surface.key for r in resolved)
            print(f"[brnrd notes] no surface named {target!r}")
            print(f"[brnrd notes] known: {keys}")
            return 1
        return _print_one_surface(row, _notes_match_verdicts(row, verdicts),
                                  json_mod, as_json=args.json)

    return _print_notes_map(
        repo_root, resolved, verdicts, findings, scope, json_mod,
        as_json=args.json,
    )


def _print_one_surface(row, hits, json_mod, *, as_json: bool) -> int:
    surface = row.surface
    if as_json:
        print(json_mod.dumps({
            "key": surface.key, "root": surface.root,
            "role": surface.role, "grammar": surface.grammar,
            "parser": surface.parser, "readers": list(surface.readers),
            "lifetime": surface.lifetime, "budget": surface.budget,
            "rides": surface.rides, "traits": list(surface.traits),
            "paths": [str(p) for p in row.paths], "bytes": row.bytes,
            "findings": [
                {"type": f.type, "severity": f.severity,
                 "target": f.target, "description": f.description}
                for f in hits
            ],
        }, indent=2))
        return 1 if hits else 0

    print(f"{surface.key} — {surface.role}")
    print(f"  root      {surface.root} ({surface.lifetime})")
    print(f"  path      {surface.path_hint}")
    for path in row.paths:
        print(f"            {path}")
    if row.note:
        print(f"  note      {row.note}")
    if not row.paths:
        print("            (nothing on disk here — not itself a finding)")
    print(f"  bytes     {row.bytes:,}   last write {_notes_age(row.mtime)}")
    print(f"  grammar   {surface.grammar}")
    if surface.parser:
        print(f"  parsed by {surface.parser}")
    print(f"  readers   {', '.join(surface.readers)}")
    print(f"  budget    {surface.budget or '—  (no ceiling)'}")
    print(f"  rides     {surface.rides or '—  (read on demand, never injected)'}")
    if surface.traits:
        print(f"  traits    {', '.join(surface.traits)}")
    print()
    if not hits:
        print("  verdict   clean")
        return 0
    print("  verdict   " + f"{len(hits)} finding(s)")
    for finding in hits:
        print(f"  {finding.render()}")
    return 1


def _print_notes_map(
    repo_root, resolved, verdicts, findings, scope, json_mod, *, as_json: bool,
) -> int:
    from . import notes

    if as_json:
        print(json_mod.dumps({
            "scope": {
                "located": scope.located,
                "registered": scope.registered,
                "unresolved_roots": list(scope.unresolved_roots),
            },
            "surfaces": [
                {
                    "key": r.surface.key, "root": r.surface.root,
                    "path_hint": r.surface.path_hint,
                    "paths": [str(p) for p in r.paths],
                    "bytes": r.bytes, "mtime": r.mtime,
                    "rides": r.surface.rides,
                    "grammar": r.surface.grammar,
                    "parser": r.surface.parser,
                    "findings": len(_notes_match_verdicts(r, verdicts)),
                }
                for r in resolved
            ],
            "findings": [
                {"type": f.type, "severity": f.severity, "target": f.target}
                for f in findings
            ],
        }, indent=2))
        return 1 if findings else 0

    # The block costs, measured once from the manifest the wake itself
    # builds. Reported per *block* rather than smeared across the rows that
    # feed it — several surfaces share one budget, and splitting a shared
    # number between them would invent precision nobody measured.
    costs: dict[str, int] = {}
    try:
        from . import prompts

        _keyed, contracts, _whole = prompts._build_injected_blocks_with_contracts(
            repo_root
        )
        costs = {c.block_key: c.bytes for c in contracts if c.present}
    except Exception:
        pass

    unresolved = notes.unresolvable_keys()
    if unresolved:
        print(f"[brnrd notes] registry entries with no resolver: "
              f"{', '.join(unresolved)}")

    attributed: set[int] = set()
    header = f"{'surface':<18} {'bytes':>9}  {'last write':<11} {'rides':<18} {'?':<3} grammar"
    for root in notes.ROOT_ORDER:
        rows = [r for r in resolved if r.surface.root == root]
        if not rows:
            continue
        print(f"\n── {root} — {notes.ROOT_BLURBS.get(root, '')}")
        print(header)
        for row in rows:
            hits = _notes_match_verdicts(row, verdicts)
            attributed.update(id(f) for f in hits)
            worst = ""
            if hits:
                rank = {"error": "!!", "warning": "!", "info": "?"}
                worst = min(
                    (rank.get(f.severity, "?") for f in hits),
                    key=lambda mark: {"!!": 0, "!": 1, "?": 2}[mark],
                )
            size = f"{row.bytes:,}" if row.paths else "—"
            print(
                f"{row.surface.key:<18} {size:>9}  "
                f"{_notes_age(row.mtime):<11} {row.surface.rides or '—':<18} "
                f"{worst:<3} {row.surface.grammar.splitlines()[0][:70]}"
            )

    # A finding that matched no row is printed here rather than dropped.
    # Filtered out, it would read exactly like a clean surface — which is
    # the failure this whole verb exists to end.
    orphans = [f for f in findings if id(f) not in attributed]
    if orphans:
        print("\n── findings not attributable to a registered surface")
        for finding in orphans:
            print(f"  {finding.render()}")

    if costs:
        print("\n── what those blocks cost this wake (shared per block, "
              "not per surface)")
        for key in ("dominion", "work-surface", "recent-activity",
                    "knowledge-sources", "kb-health", "notes-health"):
            if key in costs:
                print(f"  {key:<20} {costs[key]:>9,} B")

    # The denominator, always. A row rendered `—` is a surface that did not
    # resolve, and a reader who takes the absence of a mark for health has
    # been misled by a table that looked complete.
    print(
        f"\n{scope.line()} · {len(findings)} finding(s) — "
        "`brnrd notes check` for detail, `brnrd notes <surface>` for one "
        "surface's grammar and readers"
    )
    return 1 if findings else 0


def _fmt_duration(seconds: object) -> str:
    try:
        secs = int(float(seconds))
    except (TypeError, ValueError):
        return "-"
    mins, sec = divmod(secs, 60)
    if mins:
        return f"{mins}m{sec:02d}s"
    return f"{sec}s"


def _format_portal_state(payload: dict) -> str:
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    attention = (
        payload.get("attention")
        if isinstance(payload.get("attention"), dict) else {}
    )
    inbound = payload.get("inbound") if isinstance(payload.get("inbound"), dict) else {}
    outbound = (
        payload.get("outbound")
        if isinstance(payload.get("outbound"), dict) else {}
    )
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
    card = payload.get("card") if isinstance(payload.get("card"), dict) else {}
    resources = (
        payload.get("resources")
        if isinstance(payload.get("resources"), dict) else {}
    )
    lines = [
        "[brnrd portal state] "
        f"run={run.get('id') or '-'} "
        f"event={run.get('event_id') or '-'} "
        f"phase={run.get('phase') or '-'} "
        f"attempt={run.get('attempt') or '-'} "
        f"token={payload.get('change_token') or '-'}",
        "attention: "
        f"{attention.get('pending_event_count', 0)} pending event(s), "
        f"{attention.get('pending_outbox_file_count', 0)} pending outbox file(s)",
        "delivery: "
        f"current={outbound.get('replies_current', 0)} "
        f"other={outbound.get('replies_other', 0)} "
        f"outbound={outbound.get('outbound_messages', 0)}"
        + ("" if outbound.get("any_sent") else "  ⚠ nothing sent yet"),
        "budget: "
        f"elapsed={_fmt_duration(budget.get('elapsed_seconds'))} "
        f"limit={_fmt_duration(budget.get('budget_seconds'))} "
        f"keepalive={(budget.get('keepalive') or {}).get('status', '-')}"
        + ("  ⚠ running long" if budget.get("long_running") else ""),
    ]
    if resources:
        # Three-state honesty: a 'known' facet shows its value; an 'absent' or
        # 'unimplemented' one names the state and its reason so the gaps read as
        # data, not as a flat "unavailable". Projects from the shared facet
        # schema so this view can never drift from the woven line / JSON.
        from . import facets

        lines.append(
            "resources: "
            + " | ".join(
                f"{spec.label}={facets.facet_value(resources.get(spec.key))}"
                for spec in facets.FACETS
            )
        )
    card_text = str(card.get("text") or "").strip()
    if card_text:
        lines.append(f"card: {card_text.splitlines()[0][:160]}")
    events = inbound.get("events") if isinstance(inbound.get("events"), list) else []
    if events:
        lines.append("pending events:")
        for ev in events:
            if not isinstance(ev, dict):
                continue
            summary = str(ev.get("summary") or "").strip()
            lines.append(
                f"- {ev.get('id') or '-'} {ev.get('source') or '-'}: {summary[:200]}"
            )
    pending_files = outbound.get("pending_outbox_files")
    if isinstance(pending_files, list) and pending_files:
        lines.append("pending outbox files: " + ", ".join(map(str, pending_files)))
    return "\n".join(lines)


def cmd_runners_list(args):
    """List the unified runner catalog — the one projection for all consumers.

    Uses ``runner.available_runner_catalog()`` as the authoritative source,
    the same projection the wake prompt and dashboard spool rack consume.
    Every profile is shown; unavailable ones (Shell not on PATH, or auth env
    missing) are marked with ✗.  Stale entries (freshness_date > 30 days)
    are flagged.  A ★ marks the currently resolved runner.

    ``--all`` is accepted for backwards-compat but is now a no-op: unavailable
    profiles are always included (with marks) by the unified projection.
    """
    import json as _json
    import sys

    from . import runner as runner_mod

    repo_root = _maybe_repo_root()

    current_runner: str | None = None
    current_runner_err: str | None = None
    try:
        if repo_root:
            current_runner = runner_mod.resolve_runner(repo_root)
    except Exception as exc:  # noqa: BLE001
        current_runner_err = str(exc)

    catalog = runner_mod.available_runner_catalog(repo_root, selected=current_runner)

    if getattr(args, "json", False):
        print(_json.dumps({
            "current_runner": current_runner,
            "current_runner_error": current_runner_err,
            "profiles": catalog,
        }, indent=2, sort_keys=True))
        return 0

    # ── Text output ──────────────────────────────────────────────────
    if current_runner_err and not current_runner:
        print(f"[brnrd runners] note: could not resolve current runner — "
              f"{current_runner_err}", file=sys.stderr)

    available_count = sum(1 for r in catalog if r.get("available"))
    stale_count = sum(1 for r in catalog if r.get("stale"))
    stale_note = f", {stale_count} stale" if stale_count else ""
    print(
        f"runner catalog — {len(catalog)} profile(s), "
        f"{available_count} available{stale_note}  "
        "(★ = selected, ✓ = available, ✗ = unavailable, ⚠ = stale):"
    )

    if not catalog:
        print("  (none — install claude or codex, or declare runners.md profiles)")
        return 0

    for row in catalog:
        is_current = row.get("selected") or row.get("name") == current_runner
        sel_mark = "★" if is_current else " "
        avail = "✓" if row.get("available") else "✗"
        stale_mark = " ⚠" if row.get("stale") else ""
        name = str(row.get("name") or "")
        shell = str(row.get("shell") or "")
        model = str(row.get("model") or "—")
        if row.get("pin"):
            model = f"{model} (pin:{row['pin']})"
        cls = str(row.get("class") or "—")
        cost = row.get("cost_rank")
        cost_str = f"rank={cost}" if cost is not None else "rank=—"
        parts = [
            f"{sel_mark} {avail} {name:<28}",
            f"{shell:<8}",
            f"{model:<28}",
            f"{cls:<10}",
            cost_str,
        ]
        extras = []
        if row.get("freshness_date"):
            extras.append(f"fresh={row['freshness_date']}{stale_mark.strip()}")
        if row.get("hooks"):
            extras.append(f"hooks={row['hooks']}")
        if row.get("quota_source"):
            extras.append(f"quota={row['quota_source']}")
        if row.get("availability") not in (None, "available"):
            extras.append(row["availability"])
        if extras:
            parts.append(f"  [{', '.join(extras)}]")
        print("  " + "  ".join(parts))

    if stale_count:
        print(f"\n  ⚠ {stale_count} stale profile(s) — run `brnrd runners doctor` for details")

    return 0


def cmd_runners_doctor(args):
    """Check runner catalog health: stale cores, missing shells, auth issues.

    Prints a summary of health issues found in the catalog.  Exit code 0 when
    clean; 1 when warnings are present.
    """
    import sys

    from . import runner as runner_mod

    repo_root = _maybe_repo_root()
    catalog = runner_mod.available_runner_catalog(repo_root)

    issues: list[str] = []

    stale = [r for r in catalog if r.get("stale")]
    if stale:
        issues.append(f"stale cores ({len(stale)}):")
        for r in stale:
            issues.append(
                f"  {r['name']} — fresh={r.get('freshness_date', '?')} "
                f"(shell={r.get('shell')}, model={r.get('model')})"
            )

    unavail = [r for r in catalog if not r.get("available")]
    if unavail:
        issues.append(f"unavailable profiles ({len(unavail)}):")
        for r in unavail:
            issues.append(
                f"  {r['name']} — {r.get('availability', 'unknown')} "
                f"(shell={r.get('shell')})"
            )

    if not issues:
        print("brnrd runners doctor: catalog is healthy ✓")
        return 0

    print("brnrd runners doctor: issues found", file=sys.stderr)
    for line in issues:
        print(f"  {line}", file=sys.stderr)
    return 1


def cmd_bench_scenarios(args):
    from . import bench

    for scenario in bench.SCENARIOS.values():
        followups = f", {len(scenario.followups)} follow-up(s)" if scenario.followups else ""
        print(f"{scenario.name:<16} probes: {', '.join(scenario.probes)}{followups}")
        print(f"{'':<16} {scenario.description}")
    return 0


def cmd_bench_run(args):
    import dataclasses

    from . import bench

    scenario = bench.SCENARIOS.get(args.scenario)
    if scenario is None:
        print(f"[brnrd] unknown scenario '{args.scenario}' — see `brnrd bench scenarios`")
        return 2
    if args.timeout:
        scenario = dataclasses.replace(scenario, timeout_seconds=args.timeout)
    if args.config:
        overrides: dict[str, str] = {}
        for item in args.config:
            key, sep, value = item.partition("=")
            if not sep:
                print(f"[brnrd] bad --config {item!r} — expected KEY=VALUE")
                return 2
            overrides[key.strip()] = value.strip()
        scenario = dataclasses.replace(
            scenario, config={**scenario.config, **overrides},
        )
    root = (
        Path(args.root).expanduser().resolve()
        if args.root
        else bench.default_root(scenario.name, args.shell)
    )
    print(f"[brnrd] bench: {scenario.name} @ {args.shell} → {root}")
    print("[brnrd] bench: spawning sandbox daemon (spends real runner quota)…")
    transcript, results = bench.run_scenario(scenario, shell=args.shell, root=root)
    passed = sum(1 for r in results if r.passed)
    for r in results:
        mark = "✓" if r.passed else "✗"
        print(f"  {mark} {r.name}: {r.detail}")
    status = "TIMED OUT — " if transcript.timed_out else ""
    print(f"[brnrd] bench: {status}{passed}/{len(results)} probes ✓")
    print(f"[brnrd] bench: report → {root / 'report.md'}")
    print(f"[brnrd] bench: transcript → {root / 'transcript.md'}")
    return 0 if passed == len(results) else 1


def cmd_portal_state(args):
    import json
    import sys

    path = _portal_state_path(args.path)
    payload, _token, error = _read_portal_state(path)
    if payload is None:
        if error and path is not None:
            print(
                f"[brnrd portal state] could not read {path}: {error}",
                file=sys.stderr,
            )
            return 2
        print(
            "[brnrd portal state] no live portal-state.json found "
            "(run inside a daemon wake or pass --path)",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_portal_state(payload))
    return 0


def cmd_portal_facets(args):
    """List the boundary facet catalogue for an operator.

    The schema is always printable (it is defined in code, not in a run), so
    this works outside a wake and answers "what are the implemented facets?".
    Inside a wake — or with ``--path`` — it also folds in the live status of
    each facet from ``portal-state.json``, answering "which are populated now?".
    """
    import json
    import sys

    from . import facets

    resources = None
    path = _portal_state_path(args.path)
    if path is not None:
        payload, _token, _error = _read_portal_state(path)
        if isinstance(payload, dict):
            res = payload.get("resources")
            resources = res if isinstance(res, dict) else None

    rows = facets.describe_facets(resources)
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0

    live = resources is not None
    header = "[brnrd portal facets] boundary facet catalogue"
    print(header + (" (with live status)" if live else " (schema only)"))
    for row in rows:
        flag = "required" if row["required"] else "optional"
        head = f"  {row['label']} [{row['kind']}, {flag}]"
        if live:
            status = row.get("status") or "unimplemented"
            value = row.get("value") or status
            head += f" — {status}: {value}"
        print(head)
        print(f"      {row['fills']}")
    if not live:
        print(
            "\n  no live run detected — run inside a daemon wake or pass "
            "--path to also see which facets are populated right now."
        )
    return 0


#: How long one ``brnrd await`` call may block before it returns
#: ``pending`` on its own terms. **Not a brnrd protocol number** — the older
#: 15s ceiling was justified by "a long blocking call is a blind stretch",
#: and that argument dies with the collapsed verb: the only things that
#: would want to interrupt this run are the very things that *resolve* the
#: wait, so a blocking call returns the moment one arrives. What actually
#: bounds a call is the Shell's own per-tool-call cap (claude's Bash tool
#: ends at 10 minutes; codex differs), which is the CLI's problem, not
#: something a resident should reason about. This sits under the tightest of
#: those with margin so the call ends by returning an answer rather than by
#: being killed mid-wait.
_AWAIT_SLICE_CEILING_SECONDS = 480.0

#: How often the slice re-reads ``portal-state.json``. Cheap: a local file
#: read. The daemon's own evaluation tick runs independently, so a
#: resolution landing mid-sleep is reported on the next read with no extra
#: round trip.
_AWAIT_POLL_INTERVAL_SECONDS = 1.0

#: The ceiling used when the run's remaining budget can't be read at all
#: (an ad-hoc ``--outbox`` caller, a portal-state file without a budget
#: block). Deliberately finite: a wait with no ceiling is a hang.
_AWAIT_FALLBACK_TIMEOUT_SECONDS = 1800.0

#: Floor for the budget-derived default, so a run in its last seconds still
#: arms something the daemon can evaluate at least once.
_AWAIT_MIN_TIMEOUT_SECONDS = 30.0


def _await_default_timeout(payload: dict) -> float:
    """The run's own remaining budget, as the default ceiling.

    The daemon already knows how long this run has left; asking the resident
    to restate it is the same mistake ``spawn:<id>`` was — enumerating what
    the daemon already tracks. The daemon caps the arming again on its side
    against the hard budget ceiling (and says so via an ``advisory`` notice),
    so this is a default, not a promise.
    """
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
    total = budget.get("budget_seconds")
    elapsed = budget.get("elapsed_seconds")
    try:
        remaining = float(total) - float(elapsed)
    except (TypeError, ValueError):
        return _AWAIT_FALLBACK_TIMEOUT_SECONDS
    return max(_AWAIT_MIN_TIMEOUT_SECONDS, remaining)


def _await_continued_timeout(previous: dict) -> float | None:
    """Seconds left on a *standing* arming, or ``None`` when there isn't one.

    ``pending`` is this **call's** ceiling, never the wait's: the daemon's
    arming is still up, with its own deadline, and *call again* continues the
    same vigil. Re-deriving the default from the run's remaining budget on
    each re-call silently re-arms it longer every time — a deliberate
    12-minute hold becomes hours by the third call, with nothing on any
    surface saying so.

    Inheriting is not a convenience; it is the verb's own design rule applied
    to its own re-call. ``spawn:<id>`` died because it asked the caller to
    restate what the daemon already tracks (#1187), and the deadline is
    tracked — it is in ``portal-state.json`` before this process starts. An
    explicit ``--timeout`` still wins: that is the caller deliberately
    re-arming, which is a different act from continuing.
    """
    if not previous.get("armed") or previous.get("resolved"):
        return None
    raw = previous.get("deadline")
    if not isinstance(raw, str):
        return None
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    import time as _time

    remaining = parsed.timestamp() - _time.time()
    return remaining if remaining > 0 else None


def _await_parse_timeout(raw: str) -> float | None:
    """``30m`` / ``1h30m`` / a bare number of seconds → seconds, or ``None``."""
    from . import schedule as schedule_mod

    parsed = schedule_mod.parse_duration(raw)
    if parsed is not None:
        return parsed
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def cmd_await(args):
    """``brnrd await`` — block until the daemon has something for this run.

    The whole surface, and deliberately: no positional arguments, no
    condition flags, nothing to forget and nothing to typo. "Wake me when
    something arrives" is the entire meaning — a message, a dispatched child
    finishing, a schedule firing; all of them reach a run as pending events.
    ``--file`` is a footnote for the one thing the daemon genuinely cannot
    observe (an external CI run, a human dropping a file); it *adds* a
    resolution trigger and can never narrow the wait, so omitting it gives
    the correct default rather than a broken wait. That asymmetry is why it
    survives where ``spawn:<id>`` did not (#959, #1187).

    One call does three things in one boundary:

    1. stages the ``await:`` directive — porcelain over the same outbox
       grammar ``brnrd do`` already wraps, not a second channel;
    2. **reports its own arming verdict**, by diffing ``notices`` exactly the
       way ``brnrd do`` does. That is what kills #1187 by construction: a
       directive that fails to arm fails in the call that made it, instead of
       leaving a stale ``resolved: true`` in place looking like an answer;
    3. slice-polls ``portal-state.json`` until the daemon resolves the wait
       (``event`` / ``condition`` / ``timeout``) or the call hits its own
       Shell-safe ceiling, in which case the outcome is ``pending`` and
       *call again* is the entire instruction.

    The daemon does the evaluating, on its own heartbeat, whether or not
    this command is running — that is what makes this a listening wait
    rather than a sleeping one.
    """
    import json
    import sys
    import time

    from . import do as do_mod

    explicit_outbox = str(getattr(args, "outbox", "") or "").strip()
    if explicit_outbox:
        outbox_dir, outbox_error = _resolve_explicit_outbox(explicit_outbox)
        if outbox_error:
            print(f"[brnrd await] {outbox_error}", file=sys.stderr)
            return 1
    else:
        outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd await] no run outbox in this environment — `brnrd await` "
            "holds a live run; pass --outbox, or run inside a daemon wake.",
            file=sys.stderr,
        )
        return 1

    payload = do_mod.read_portal_state(outbox_dir)
    if not payload:
        print(
            f"[brnrd await] no live portal-state.json under {outbox_dir}",
            file=sys.stderr,
        )
        return 1

    previous = payload.get("await") if isinstance(payload.get("await"), dict) else {}
    previous_generation = previous.get("generation")

    if args.timeout is not None:
        timeout_seconds = _await_parse_timeout(str(args.timeout))
        if timeout_seconds is None or timeout_seconds <= 0:
            print(
                f"[brnrd await] --timeout {args.timeout!r} is not a positive "
                "duration (e.g. 30m, 1h30m, 90s, or a bare number of seconds)",
                file=sys.stderr,
            )
            return 1
    else:
        # A bare re-call *continues* the standing vigil at its own deadline;
        # only a first call falls through to the budget-derived default.
        timeout_seconds = (
            _await_continued_timeout(previous) or _await_default_timeout(payload)
        )

    before = do_mod.notices_of(payload)
    staged = do_mod.stage_await(
        outbox_dir, timeout_seconds=timeout_seconds, file_path=args.file,
    )
    status, detail = do_mod.await_verdict(
        outbox_dir, staged, before, ("await",),
        timeout_seconds=do_mod.DEFAULT_TIMEOUT_SECONDS,
    )
    if status != do_mod.OK:
        # The arming verdict, in the call that armed it. `failed` = the
        # daemon refused/dropped the directive and named it in a notice;
        # `unarmed` = the drain never consumed the file, so nothing is
        # waiting on anything. Neither is a wait, and neither is reported as
        # one.
        outcome = "failed" if status == do_mod.FAILED else "unarmed"
        result = {"outcome": outcome, "detail": detail or "still queued"}
        if args.json:
            print(json.dumps(result))
        else:
            print(f"[brnrd await] ✗ not armed — {result['detail']}", file=sys.stderr)
        return 1

    def _emit(state: dict, outcome: str) -> int:
        result = {
            "outcome": outcome,
            "which": state.get("which"),
            "deadline": state.get("deadline"),
            "capped": bool(state.get("capped")),
        }
        if args.json:
            print(json.dumps(result))
        else:
            tail = f" ({result['which']})" if result["which"] else ""
            note = " — call again" if outcome == "pending" else ""
            print(f"[brnrd await] {outcome}{tail}{note}")
        return 0

    deadline = time.monotonic() + _AWAIT_SLICE_CEILING_SECONDS
    while True:
        state = do_mod.read_portal_state(outbox_dir).get("await")
        if not isinstance(state, dict):
            state = {}
        # Generation-gated: a portal-state file written *before* this tick's
        # drain still carries the previous call's sticky-resolved outcome,
        # and reporting that as this wait's answer is the very stale-answer
        # failure this command exists to end.
        fresh = state.get("generation") != previous_generation
        if fresh and state.get("resolved"):
            return _emit(state, str(state.get("outcome") or "event"))
        if time.monotonic() >= deadline:
            return _emit(state, "pending")
        time.sleep(_AWAIT_POLL_INTERVAL_SECONDS)


def cmd_cut(args):
    """``brnrd cut FILE`` — stage the bolt, read the daemon's verdict back.

    Sibling of ``do``/``await`` in shape: stage the declaration, wait for
    the daemon's own drain to consume it, and report the verdict in the
    same call — ``accepted`` / ``bounced — <named diff>`` / ``queued``.
    Exit 0 only on ``accepted``, matching the porcelain contract in
    ``docs/portals.md``.
    """
    import sys

    from . import do as do_mod

    explicit_outbox = str(getattr(args, "outbox", "") or "").strip()
    if explicit_outbox:
        outbox_dir, outbox_error = _resolve_explicit_outbox(explicit_outbox)
        if outbox_error:
            print(f"[brnrd cut] {outbox_error}. Nothing was written.", file=sys.stderr)
            return 1
    else:
        outbox_dir = _wake_outbox_dir()
    if outbox_dir is None:
        print(
            "[brnrd cut] no run outbox in this environment — `brnrd cut` "
            "stages this run's completion declaration; pass --outbox, or "
            "run inside a daemon wake. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    file_path = Path(args.file)
    if not file_path.is_file():
        print(f"[brnrd cut] {args.file!r} is not a file", file=sys.stderr)
        return 1

    timeout = args.timeout if args.timeout is not None else do_mod.DEFAULT_TIMEOUT_SECONDS
    before = do_mod.notices_of(do_mod.read_portal_state(outbox_dir))
    staged, stage_error = do_mod.stage_cut(outbox_dir, file_path)
    if staged is None:
        print(f"[brnrd cut] ✗ {stage_error}", file=sys.stderr)
        return 1

    status, detail = do_mod.await_verdict(
        outbox_dir, staged, before, ("cut",),
        timeout_seconds=timeout, source_file=staged.name,
    )
    if status == do_mod.OK:
        # An OK drain verdict only means the directive was consumed with no
        # refusal notice naming it — it is not proof the accept branch's own
        # `task.meta["bolt"]` write has reached this run's portal-state.json
        # yet (the same intra-tick race `await_verdict` already grace-polls
        # for, applied to a different facet — #1221). Confirm the bolt
        # facet actually landed before ever printing "accepted".
        bolt_facet = do_mod.await_bolt_facet(outbox_dir)
        if bolt_facet is None:
            print(
                "[brnrd cut] ? unconfirmed — no bolt facet after "
                f"{int(do_mod.BOLT_GRACE_SECONDS)}s; check notices",
                file=sys.stderr,
            )
            return 1
        # An annotated accept (cap-3 forced) exits 0 like a clean one — the
        # bolt stands either way — but the daemon's dissent must be visible
        # in the same call, not only in the delivered body.
        annotated = int(bolt_facet.get("annotated") or 0)
        # Name where the bolt lands (his 2026-08-08 ask: the interface must
        # explain itself) — a resident reading "accepted" otherwise has no
        # way to know what surface should change, and the first bolt night
        # produced a misdiagnosis for exactly that reason.
        where = (
            "bolt rides state.md + the run ledger; the dashboard's summons "
            "strip and cloth lane pick it up on the next mirror tick"
        )
        if annotated:
            print(
                f"[brnrd cut] accepted, annotated — {annotated} check(s) "
                f"unresolved; the daemon's dissent rides the delivered body. {where}"
            )
        else:
            print(f"[brnrd cut] accepted — {where}")
        return 0
    if status == do_mod.QUEUED:
        print(f"[brnrd cut] ? {detail or 'still queued'}", file=sys.stderr)
        return 1
    print(f"[brnrd cut] bounced — {detail}", file=sys.stderr)
    return 1


def cmd_hook(args):
    import sys

    from . import hooks

    phase = str(args.phase or "").strip()
    if phase not in hooks.PHASES:
        print("{}", end="")
        return 0
    return hooks.main(phase)


def cmd_statusline(args):
    from . import statusline

    return statusline.main()


def cmd_close_check(args):
    """Report close keywords GitHub would act on. Exit 1 when any fire.

    The exit code is the point: a run about to `gh pr create --body-file x.md`
    can gate on `brnrd close-check x.md` in the same command line. Coverage
    here is opt-in — nothing forces a hand-`gh` path through it — which is
    stated plainly rather than dressed up as enforcement (#839).
    """
    import json as _json
    import sys as _sys
    import subprocess as _subprocess

    if args.path in ("-", ""):
        text = _sys.stdin.read()
        label = "<stdin>"
    else:
        path = Path(args.path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[brnrd close-check] {e}")
            return 2
        label = str(path)

    findings = closekeyword.check(text, channel=args.channel)
    refs = closekeyword.extract_close_refs(text, channel=args.channel)

    # When --resolve is requested, look up the state of each ref.
    #
    # #1433 — the whole point of this command is that a verdict must not read
    # as a safety clearance it did not earn, so the lookup's *failure* wording
    # is as load-bearing as its success wording. `NOT_FOUND` says "this ref
    # does not exist", which a reader takes as *harmless*. An unauthenticated
    # `gh`, a missing `gh`, the wrong working directory, a rate limit or a dead
    # network all exit non-zero too — and answering `NOT_FOUND` to any of them
    # is a confident lie in the optimistic direction about a live open issue.
    # Driven, 2026-08-17, against two genuinely OPEN issues: an empty
    # `GH_CONFIG_DIR` reported `NOT_FOUND` for both, and so did running from a
    # directory outside the repo.
    #
    # So `NOT_FOUND` is claimed only when gh says *that specific thing*
    # ("Could not resolve to an issue or pull request with the number of N");
    # every other non-zero exit is `UNKNOWN`. A remedy is part of a
    # diagnostic's truth claim: cannot tell which case ⇒ name the ambiguity,
    # never the confident branch.
    ref_states = {}
    if args.resolve and refs:
        for ref in refs:
            cmd = ["gh", "issue", "view", ref.ref, "--json", "state"]
            if args.repo:
                # Without this, gh resolves against the *working directory's*
                # repo — so the same body checked from two places can get two
                # answers, and neither says which repo it answered about.
                cmd += ["--repo", args.repo]
            try:
                result = _subprocess.run(
                    cmd, capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    state_data = _json.loads(result.stdout)
                    ref_states[ref.ref] = state_data.get("state", "UNKNOWN")
                elif "could not resolve to an issue" in (result.stderr or "").lower():
                    ref_states[ref.ref] = "NOT_FOUND"
                else:
                    ref_states[ref.ref] = "UNKNOWN"
            except Exception:
                ref_states[ref.ref] = "UNKNOWN"

    if args.json:
        refs_data = [
            {
                "ref": r.ref,
                "line_number": r.line_number,
                **({"state": ref_states.get(r.ref)} if args.resolve else {}),
            }
            for r in refs
        ]
        print(_json.dumps({
            "ok": not findings,
            "source": label,
            "channel": args.channel,
            "findings": [
                {
                    "rule": f.rule,
                    "line_number": f.line_number,
                    "line": f.line,
                    "headline": f.headline,
                    "remedies": list(f.remedies),
                }
                for f in findings
            ],
            "close_refs": refs_data,
        }))
        return 1 if findings else 0

    if findings:
        print(closekeyword.render(findings, channel=args.channel))
        return 1

    # No findings: display what will be closed
    if refs:
        print(f"[brnrd close-check] {label}: will close {len(refs)} issue(s) ({args.channel})")
        for ref in refs:
            state_suffix = ""
            if args.resolve:
                state = ref_states.get(ref.ref, "UNKNOWN")
                state_suffix = f" ({state})"
            print(f"  Closes #{ref.ref}{state_suffix}")
    else:
        print(f"[brnrd close-check] {label}: no close keywords ({args.channel})")
    return 0


def cmd_review(args):
    import json as _json

    from .diffense import pack as pack_mod
    from .diffense import prbody

    path = Path(args.pack)
    try:
        loaded = pack_mod.load_pack(path)
    except pack_mod.PackError as e:
        if args.json:
            print(_json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"[brnrd review] {e}")
        return 2

    if args.pr_title:
        print(prbody.pr_title(loaded, fallback=args.fallback_title or path.stem))
        return 0

    if args.pr_body:
        render_url = args.render_url
        pack_url = None
        if args.relay and not render_url:
            from .diffense import gist
            render_base_url = args.render_base_url or _diffense_render_base_url()
            if gist.renderer_shell_available(render_base_url):
                published = gist.create_pack_gist(
                    loaded, repo=_diffense_current_repo()
                )
                if published is not None:
                    render_url = gist.render_url(
                        published.raw_url,
                        base_url=render_base_url,
                    )
                    pack_url = published.html_url
            if not render_url:
                brr_dir = _maybe_brr_dir()
                if brr_dir is not None:
                    from .gates import cloud
                    if cloud.is_configured(brr_dir):
                        candidate = cloud.relay_pack(brr_dir, loaded)
                        if candidate and gist.review_url_available(candidate):
                            render_url = candidate
        print(prbody.project_pr_body(loaded, render_url=render_url, pack_url=pack_url))
        return 0

    if not args.check:
        print("[brnrd review] pass `--check`, `--pr-title`, or `--pr-body` "
              "(the local render/serve surface is a follow-up)")
        return 0

    repo_root = _maybe_repo_root()
    issues = pack_mod.check_pack(loaded, repo_root=repo_root)
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    if args.json:
        print(_json.dumps(
            {
                "ok": not errors,
                "errors": len(errors),
                "warnings": len(warnings),
                "issues": [i.__dict__ for i in issues],
            },
            indent=2,
        ))
    else:
        for issue in issues:
            print(f"  {issue.format()}")
        n_cards = len(loaded.get("cards") or [])
        scope = "against repo" if repo_root else "structure-only (no repo)"
        print(f"[brnrd review] {path.name}: {n_cards} cards, "
              f"{len(errors)} error(s), {len(warnings)} warning(s) — {scope}")
    return 1 if errors else 0


def _diffense_render_base_url() -> str:
    from .diffense import gist

    repo_root = _maybe_repo_root()
    if repo_root is not None:
        from . import config as conf

        cfg = conf.load_config(repo_root)
        value = cfg.get("diffense.render_base_url", cfg.get("diffense_render_base_url"))
        if isinstance(value, str) and value.strip():
            return value.strip()
    return gist.DEFAULT_RENDER_BASE_URL


def _diffense_current_repo() -> str | None:
    repo_root = _maybe_repo_root()
    if repo_root is None:
        return None
    from . import gitops
    from .gates.github import parse_origin_url

    remote = gitops.default_remote(repo_root)
    if not remote:
        return None
    url = gitops.remote_url(repo_root, remote)
    return parse_origin_url(url or "")


def cmd_auth(args):
    gate_mod = _load_gate(args.gate)
    gate_mod.auth(_brr_dir())


def cmd_bind(args):
    from . import account
    from . import config as conf

    repo_root = _repo_root_from_arg(args.repo)
    cfg = dict(conf.load_config(repo_root))
    cfg["home.kind"] = "project"
    ctx = account.resolve_context(repo_root, cfg)
    gate_mod = _load_gate(args.gate)
    gate_mod.bind(_brr_dir_for_repo(repo_root))
    print(f"[brnrd] bound {args.gate} for {account.repo_label(repo_root, cfg)}")
    print(f"[brnrd] project home: {ctx.dominion_repo}")


def cmd_add(args):
    from . import account
    from . import config as conf

    account_repo_root = _repo_root()
    cfg = conf.load_config(account_repo_root)
    ctx = account.resolve_context(account_repo_root, cfg)
    if ctx.kind != "account":
        raise SystemExit(
            f"{brnrd_cmd()} account add requires a connected account home; "
            f"run `{brnrd_cmd()} account connect` first"
        )
    repo_root = _repo_root_from_arg(args.repo)
    target_cfg = conf.load_config(repo_root)
    label = account.repo_label(repo_root, target_cfg)
    account.register_repo(ctx, repo_root, label=label)
    print(f"[brnrd] added {label} to account home {ctx.dominion_repo}")


def _print_link_ceremony(owner: str, dominion_name: str, knowledge_name: str) -> None:
    """Name the moment `home link` is: two repos, founded for the user.

    Everything printed here is a fact `link_home` acts on anyway — the
    resolved owner, the names, the private-only invariant, what each slot
    pushes. The ceremony is saying them *before* acting, at the one seam
    where the user is standing (design-repo-birth-ceremony.md)."""
    print()
    print("[brnrd] home link — putting your resident's two repos in your hands:")
    print()
    print(f"  memory     {owner}/{dominion_name}")
    print("             the dominion: the agent's working memory — notes, plans,")
    print("             run records; the daemon commits here after every thought")
    print(f"  knowledge  {owner}/{knowledge_name}")
    print("             the pages your projects taught it — designs, decisions,")
    print("             pitfalls")
    print()
    print("  · created under your GitHub login, with your credentials —")
    print("    brnrd's App owns nothing here")
    print("  · always private: an existing public repo with one of these names")
    print("    is refused, never pushed to")
    print("  · these names are brnrd's defaults, not yours — rename with")
    print("    --dominion-name / --knowledge-name")
    print("  · each repo carries a README deed: what it is, who writes it,")
    print("    where it lives, and how to leave (plain git)")


def cmd_home_link(args):
    import sys

    from . import config as conf
    from . import home_link

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    dominion_name = args.dominion_name or home_link.DEFAULT_DOMINION_NAME
    knowledge_name = args.knowledge_name or home_link.DEFAULT_KNOWLEDGE_NAME

    # Best-effort owner resolution for the ceremony text only — link_home
    # re-resolves lazily for the actual work, so a failure here degrades the
    # display, never the link.
    owner = args.owner or ""
    if not owner and home_link.gh_available():
        try:
            owner = home_link.resolve_owner(None)
        except home_link.HomeLinkError:
            owner = ""
    _print_link_ceremony(owner or "<your GitHub login>", dominion_name, knowledge_name)

    if not args.yes:
        if not sys.stdin.isatty():
            raise SystemExit(
                "[brnrd] `brnrd home link` needs --yes when not running interactively"
            )
        from .adopt import _confirm

        print()
        if not _confirm(
            "Back up the agent's memory and knowledge base to private GitHub repos?",
            default=True,
        ):
            print("[brnrd] cancelled — nothing changed")
            return

    def _report(result: "home_link.RepoLinkResult") -> None:
        state = "pushed" if result.pushed else "already up to date"
        print(f"[brnrd] {result.slot}: {result.action} → {result.remote_url} ({state})")

    try:
        home_link.link_home(
            repo_root,
            cfg,
            owner=args.owner,
            dominion_name=dominion_name,
            knowledge_name=knowledge_name,
            on_result=_report,
        )
    except home_link.HomeLinkError as exc:
        raise SystemExit(f"[brnrd] {exc}")


def cmd_home_manifest(args):
    """``brnrd home manifest [--json]`` — what the resolved home actually holds.

    The reusable half of the front door's missing memory step
    (``front_door._step_memory``): read-only by construction
    (``resolve_context(create=False)``, mirroring ``cmd_account_status``),
    so running this command never materializes or seeds the thing it
    reports on.
    """
    import json as _json

    from . import account
    from . import config as conf

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    ctx = account.resolve_context(repo_root, cfg, create=False)
    home_root = account.context_home_root(ctx)
    manifest = account.home_manifest(ctx)

    if getattr(args, "json", False):
        print(_json.dumps({
            "home": str(home_root),
            "kb_pages": manifest.kb_pages,
            "warp_items": manifest.warp_items,
            "topics": manifest.topics,
            "run_records": manifest.run_records,
            "surface_pages": manifest.surface_pages,
            "commit_count": manifest.commit_count,
            "origin_url": manifest.origin_url,
            "knowledge_origin_url": manifest.knowledge_origin_url,
            "has_memory": manifest.has_memory,
        }, indent=2, sort_keys=True))
        return 0

    print(f"[brnrd home manifest] {home_root}")
    print(f"  kb pages         : {manifest.kb_pages:,}")
    print(f"  warp items       : {manifest.warp_items:,}")
    print(f"  topics           : {manifest.topics:,}")
    print(f"  run records      : {manifest.run_records:,}")
    print(f"  surface pages    : {manifest.surface_pages:,}")
    print(f"  git commits      : {manifest.commit_count:,}")
    print(f"  memory origin    : {manifest.origin_url or '(none — local only)'}")
    print(f"  knowledge origin : {manifest.knowledge_origin_url or '(none — local only)'}")
    if not manifest.has_memory:
        print("\n  no memory yet — this resident is starting fresh")
    return 0


def cmd_home_sweep_orphans(args):
    """``brnrd home sweep-orphans`` — #1193 rec 4.

    Rec 3 keyed the fallback project home on repo identity instead of raw
    checkout path, so a worktree, a scratch clone, or a container mount no
    longer mints its own empty home. It does nothing about the ones the old
    keying already minted before that fix landed — this lists them
    (:func:`account.survey_project_home` does the actual classification,
    read-only) and only deletes on an explicit ``--delete``, itself gated
    by ``--yes`` off a TTY exactly like ``brnrd home link``.
    """
    from . import account

    homes = account.list_project_homes()
    if not homes:
        print("[brnrd home sweep-orphans] no project homes found under "
              "~/.local/state/brnrd/projects/ (or $XDG_STATE_HOME/brnrd/projects/)")
        return 0

    surveys = [account.survey_project_home(home) for home in homes]
    orphans = [s for s in surveys if s.default_scaffold]
    kept = [s for s in surveys if not s.default_scaffold]

    print(f"[brnrd home sweep-orphans] {len(homes)} project home(s) scanned: "
          f"{len(orphans)} default-scaffold, {len(kept)} carrying real content")
    for s in orphans:
        print(f"  orphan  {s.home}")
    for s in kept:
        print(f"  keep    {s.home}  ({'; '.join(s.reasons)})")

    if not orphans:
        return 0

    if not args.delete:
        print()
        print(f"[brnrd home sweep-orphans] dry run — nothing deleted. "
              f"Re-run with --delete to remove the {len(orphans)} orphan(s) above.")
        return 0

    if not args.yes:
        import sys

        if not sys.stdin.isatty():
            raise SystemExit(
                "[brnrd home sweep-orphans] --delete needs --yes when not running interactively"
            )
        from .adopt import _confirm

        print()
        if not _confirm(
            f"Delete {len(orphans)} default-scaffold project home(s)?",
            default=False,
        ):
            print("[brnrd home sweep-orphans] cancelled — nothing changed")
            return 0

    import shutil

    for s in orphans:
        # Remove the whole `projects/<name>/` directory, not just its
        # `home/` child — an emptied `<name>/` left behind is the same
        # noise this tool exists to clear.
        shutil.rmtree(s.home.parent)
        print(f"  deleted {s.home.parent}")
    return 0


def cmd_config_promote(args):
    """``brnrd config promote`` — move security keys out of ``.brr/config``.

    Issue #533: ``runner_cmd`` / ``trust.*`` / ``docker.*`` / ``solitary.*``
    / ``environment``/``env``/``default_env`` are security-defining —
    ``config.load_config`` has already stopped honouring them from the
    repo-writable ``.brr/config``. This is the operator-run migration that
    carries any already sitting there into the daemon-owned
    ``security.config``, once. Always prints the plan before touching
    anything; ``--dry-run`` stops there.

    Issue #693 added the *file* half: a repo-side ``.brr/runners.md``
    declares runner profiles, and a profile carries ``cmd:`` — the argv
    brnrd executes — so it joined the same domain and moves in the same
    command. One verb, because the two are one migration from the
    operator's side: "the things my repo used to decide about execution,
    moved to where only the daemon can write them."
    """
    from . import config as conf

    repo_root = _repo_root()
    plan = conf.plan_promote(repo_root)

    if plan.is_empty:
        print(
            "[brnrd config promote] no security-defining keys or runner "
            "profile file in .brr/ — nothing to do"
        )
        return 0

    if plan.security_path is None:
        print(
            "[brnrd config promote] could not resolve the daemon-owned "
            "home for this repo — nothing to promote into"
        )
        return 2

    if plan.moves:
        print(
            f"[brnrd config promote] moving {len(plan.moves)} key(s) from "
            f".brr/config to {plan.security_path}:"
        )
    for key in sorted(plan.moves):
        if key in plan.conflicts:
            old, new = plan.conflicts[key]
            tag = (
                f"  (--force: overwrites existing security.config value {old!r})"
                if args.force
                else f"  (CONFLICTS with existing security.config value {old!r} — needs --force)"
            )
        else:
            tag = ""
        print(f"  {key}={plan.moves[key]!r}{tag}")

    if plan.profiles_move is not None:
        source, dest = plan.profiles_move
        if plan.profiles_conflict:
            tag = (
                "  (--force: replaces the existing home copy)"
                if args.force
                else "  (CONFLICTS with the existing home copy — needs --force)"
            )
        else:
            tag = ""
        print("[brnrd config promote] moving runner profiles:")
        print(f"  {source} -> {dest}{tag}")

    if (plan.conflicts or plan.profiles_conflict) and not args.force:
        print(
            "[brnrd config promote] refusing to overwrite differing "
            "security.config value(s) or an existing home runners.md "
            "without --force"
        )
        return 2

    if args.dry_run:
        print("[brnrd config promote] --dry-run: nothing changed")
        return 0

    try:
        conf.apply_promote(repo_root, plan, force=args.force)
    except conf.ConfigPromoteError as exc:
        print(f"[brnrd config promote] {exc}")
        return 2

    done = []
    if plan.moves:
        done.append(f"{len(plan.moves)} promoted key(s)")
    if plan.profiles_move is not None:
        done.append("the runner profile catalog")
    print(
        f"[brnrd config promote] done — {plan.security_path.parent} holds "
        f"{' and '.join(done)}, mode 0600"
    )
    return 0


def cmd_setup(args):
    gate_mod = _load_gate(args.gate)
    brr_dir = _brr_dir()
    setup = getattr(gate_mod, "setup", None)
    if setup is not None:
        setup(brr_dir)
        return
    gate_mod.auth(brr_dir)
    gate_mod.bind(brr_dir)


def cmd_gate_list(args):
    """``brnrd gate list [--json]`` — which gates are configured in this repo.

    Every gate module owns an ``is_configured`` predicate; this reads them
    rather than second-guessing each gate's on-disk state layout. Outside a
    repo there is no ``.brr`` to inspect, so the catalogue still prints and
    every gate reports unknown — the honest answer, not a false "no".
    """
    import json as _json

    brr_dir = _maybe_brr_dir()
    rows = []
    for name in GATES:
        configured: bool | None = None
        if brr_dir is not None:
            try:
                configured = bool(_load_gate(name).is_configured(brr_dir))
            except Exception:  # noqa: BLE001 — a broken gate is "unknown", not a crash
                configured = None
        rows.append({"name": name, "configured": configured})

    if getattr(args, "json", False):
        print(_json.dumps(
            {"brr_dir": str(brr_dir) if brr_dir else None, "gates": rows},
            indent=2, sort_keys=True,
        ))
        return 0

    if brr_dir is None:
        print("[brnrd gate list] not inside a brnrd repo — showing the catalogue only")
    for row in rows:
        mark = {True: "✓", False: "·", None: "?"}[row["configured"]]
        state = {True: "configured", False: "not configured", None: "unknown"}[
            row["configured"]
        ]
        print(f"  {mark} {row['name']:<10} {state}")
    if brr_dir is not None:
        print("\nconfigure one with `brnrd gate setup <gate>`")
    return 0


def cmd_account_relabel(args):
    """``brnrd account relabel <old> <new>`` — move a repo's memory to a new address.

    A repo's resident memory — knowledge, dominion, plans, runner policy, run
    history, archived replies — is keyed by a slug derived from the origin
    remote. Move the repo (``Gurio/brr`` → ``hugimuni-labs/brnrd``) and every
    one of those scopes silently re-keys: nothing errors, nothing warns, and
    the next wake starts from zero on a mature project. This carries them over.

    Order doesn't matter: run it before or after ``git remote set-url``. The
    labels are explicit precisely so the command never has to guess from a
    remote that may already have moved.
    """
    import sys

    from . import account
    from . import config as conf
    from . import gitops

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    ctx = account.resolve_context(repo_root, cfg, create=False)

    if ctx.kind != "account":
        print(f"[brnrd account relabel] home kind is {ctx.kind!r}, not 'account'.")
        print("  A project home is keyed by repo slug *and* path hash, so a")
        print("  relabel alone would not find it. Connect an account first")
        print("  (`brnrd account connect`), or move the home directory by hand.")
        return 2

    try:
        moves = account.plan_relabel(ctx, args.old_label, args.new_label)
    except account.RelabelError as exc:
        print(f"[brnrd account relabel] {exc}")
        return 2

    # Gate identity is project-side state (.brr/gates/*.json): a cloud gate
    # still carrying the old repo_full_name keeps stamping events with the
    # old label, re-splitting the memory this command just unified (#546).
    try:
        brr_dir = gitops.shared_brr_dir(repo_root)
    except Exception:
        brr_dir = None
    gate_rewrites = (
        account.plan_relabel_gates(brr_dir, args.old_label, args.new_label)
        if brr_dir is not None
        else []
    )

    if not moves and not any(rw.fields for rw in gate_rewrites):
        print(f"[brnrd account relabel] no memory found under {args.old_label!r}.")
        print("  Nothing to move. (Already relabelled? Check `brnrd account status`.)")
        for rw in gate_rewrites:
            for field in rw.warnings:
                print(f"  ⚠ gate {rw.gate} still says {args.old_label} in {field}")
        return 0

    print(f"[brnrd account relabel] {args.old_label} → {args.new_label}")
    for move in moves:
        print(f"  {move.scope:<14} {move.src}")
        print(f"  {'':<14}   → {move.dst}")
    for rw in gate_rewrites:
        if not rw.fields:
            continue
        print(f"  {'gate-config':<14} {rw.path}")
        for field, old, new in rw.fields:
            print(f"  {'':<14}   {field}: {old} → {new}")
    print(f"  registry       account/repos.json: rekey entry"
          + (" + default_repo" if ctx.default_repo.label == args.old_label else ""))
    for rw in gate_rewrites:
        for field in rw.warnings:
            print(f"  ⚠ gate {rw.gate} still says {args.old_label} in {field}")

    if args.dry_run:
        print("\n  --dry-run: nothing moved.")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print("\n[brnrd account relabel] refusing to move without --yes "
                  "on a non-TTY.")
            return 2
        answer = input("\nMove these? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("  aborted; nothing moved.")
            return 1

    account.relabel_repo(ctx, args.old_label, args.new_label)
    account.relabel_gates(gate_rewrites, args.old_label)

    # Commit both homes. The knowledge dir is a nested repo the dominion
    # gitignores, so it needs its own commit — a relabel that lands in only
    # one of them is exactly the half-migration this command exists to avoid.
    message = f"relabel: {args.old_label} -> {args.new_label}"
    home_root = account.context_home_root(ctx)
    knowledge_root = account.knowledge_path(ctx)
    for label, path in (("home", home_root), ("knowledge", knowledge_root)):
        if not (path / ".git").exists():
            continue
        if not gitops.worktree_dirty(path):
            continue
        if gitops.commit_all(path, message):
            print(f"  committed {label}: {path}")
        else:
            print(f"  ⚠ could not commit {label} ({path}) — commit it by hand.")

    rewritten_gates = sum(1 for rw in gate_rewrites if rw.fields)
    if rewritten_gates:
        print(f"  rewrote gate identity in {rewritten_gates} gate config(s).")
    print(f"\n  done. {len(moves)} scope(s) moved; the next wake reads them "
          f"under {args.new_label}.")
    print("  Remaining: point the repo's origin remote at the new address if "
          "you haven't yet.")
    return 0


def cmd_account_status(args):
    """``brnrd account status [--json]`` — the resolved home and its repos.

    Read-only by construction: ``resolve_context(create=False)`` inspects
    without materializing a home on disk. A status command that created the
    thing it reports would be lying about the state it found.
    """
    import json as _json

    from . import account
    from . import config as conf

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    ctx = account.resolve_context(repo_root, cfg, create=False)
    roots = account.selectable_roots(ctx)

    if getattr(args, "json", False):
        print(_json.dumps({
            "kind": ctx.kind,
            "account_id": ctx.account_id or None,
            "home_id": ctx.home_id or None,
            "dominion_repo": str(ctx.dominion_repo),
            "enabled": ctx.enabled,
            "default_repo": ctx.default_repo.label,
            "repos": [
                {
                    "label": root.label,
                    "root": str(root.root),
                    "kind": root.kind,
                    "default": root.default,
                }
                for root in roots
            ],
        }, indent=2, sort_keys=True))
        return 0

    print(f"[brnrd account status] home kind: {ctx.kind}")
    if ctx.account_id:
        print(f"  account id   : {ctx.account_id}")
    print(f"  home         : {ctx.dominion_repo}")
    print(f"  enabled      : {'yes' if ctx.enabled else 'no'}")
    print(f"  roots        : {len(roots)}")
    for root in roots:
        star = "★" if root.default else " "
        kind = f"[{root.kind}]"
        print(f"    {star} {root.label:<24} {kind:<8} {root.root}")
    if ctx.kind != "account":
        print(
            f"\n  this is a project home — `{brnrd_cmd()} account connect` "
            "links it to brnrd."
        )
    return 0


_COMPLETION_PREAMBLE = (
    "# brnrd shell completions — generated by `brnrd completions {shell}`.\n"
    "# Regenerate after upgrading brnrd; the verb list is baked in.\n"
)


def _subcommand_names(parser: argparse.ArgumentParser) -> list[str]:
    """Subcommand names directly under *parser* (empty for a leaf verb)."""
    names: list[str] = []
    for action in parser._actions:  # noqa: SLF001 — argparse exposes no public walk
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            names.extend(action.choices)
    return names


def _completion_tree() -> dict[str, list[str]]:
    """Map each public verb to its subcommands, walked off the live parser.

    Walked, not hand-listed: a hand-maintained completion table would be a
    second source of truth for the surface this slice spent its whole diff
    unifying, and it would drift the first time someone adds a subcommand.
    Hidden and retired verbs are skipped — completing a spelling that answers
    with "use the other one" is worse than not completing it.
    """
    parser = build_parser()
    tree: dict[str, list[str]] = {}
    for action in parser._actions:  # noqa: SLF001
        if not isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            continue
        for name, subparser in action.choices.items():
            if name in PUBLIC_COMMANDS:
                tree[name] = sorted(_subcommand_names(subparser))
    return tree


def cmd_completions(args):
    shell = args.shell
    tree = _completion_tree()
    verbs = " ".join(sorted(tree))
    out = [_COMPLETION_PREAMBLE.format(shell=shell)]

    if shell == "bash":
        out.append("_brnrd_completions() {\n"
                   '  local cur prev\n'
                   '  cur="${COMP_WORDS[COMP_CWORD]}"\n'
                   '  prev="${COMP_WORDS[COMP_CWORD-1]}"\n'
                   f'  if [ "$COMP_CWORD" -eq 1 ]; then\n'
                   f'    COMPREPLY=( $(compgen -W "{verbs}" -- "$cur") )\n'
                   "    return\n"
                   "  fi\n"
                   "  case \"${COMP_WORDS[1]}\" in\n")
        for verb, subs in sorted(tree.items()):
            if subs:
                out.append(f'    {verb}) COMPREPLY=( $(compgen -W '
                           f'"{" ".join(subs)}" -- "$cur") ) ;;\n')
        out.append("  esac\n"
                   "}\n"
                   "complete -F _brnrd_completions brnrd\n")
    elif shell == "zsh":
        out.append("#compdef brnrd\n_brnrd() {\n"
                   "  local -a verbs\n"
                   f'  verbs=({verbs})\n'
                   "  if (( CURRENT == 2 )); then\n"
                   '    _describe "brnrd command" verbs\n'
                   "    return\n"
                   "  fi\n"
                   '  case "${words[2]}" in\n')
        for verb, subs in sorted(tree.items()):
            if subs:
                out.append(f'    {verb}) _values "{verb} subcommand" '
                           f'{" ".join(subs)} ;;\n')
        out.append("  esac\n"
                   "}\n"
                   "compdef _brnrd brnrd\n")
    else:  # fish
        for verb in sorted(tree):
            out.append(f'complete -c brnrd -n "__fish_use_subcommand" '
                       f'-a "{verb}"\n')
        for verb, subs in sorted(tree.items()):
            for s in subs:
                out.append(f'complete -c brnrd -n "__fish_seen_subcommand_from '
                           f'{verb}" -a "{s}"\n')

    print("".join(out), end="")
    return 0


def cmd_up(args):
    """Start the foreground daemon directly, bypassing any installed service.

    Not bound to a parser: this is the ``--foreground`` half of
    ``cmd_daemon_up``, which both ``brnrd up`` and ``brnrd daemon up`` reach.
    """
    from . import daemon as daemon_mod
    from . import gitops

    # #1108: the daemon is the one caller allowed to *repair* rather than
    # only diagnose, and only for brnrd's own garbage — a `core.worktree`
    # naming a deleted `.brr/worktrees/<run-id>`. Two reasons the line sits
    # exactly here. Without it the boot cannot proceed at all: this pin
    # crash-looped the service 312 times in 27 minutes on the very next
    # statement, so "report and exit" is a loop with better prose. And the
    # daemon owns those worktrees — it created them and it tore them down,
    # which is what makes the value provably dead rather than merely
    # suspicious. A read-only verb like `daemon status` still gets the
    # diagnosis and leaves the operator's config alone; being asked a
    # question is not consent to edit git config.
    gitops.heal_stale_brnrd_worktree_pin(Path.cwd())
    try:
        root = _repo_root()
    except RuntimeError:
        # Under an installed service this cwd comes from the unit's
        # WorkingDirectory pin; a raw traceback in the journal helps nobody.
        raise SystemExit(
            "[brnrd] `daemon up` must run from inside a project repository "
            f"(cwd: {Path.cwd()}) — under a service, re-run "
            "`brnrd daemon install` from the repo to refresh the pinned "
            "working directory"
        )
    daemon_mod.start(root, dev_reload=args.dev_reload)


def cmd_down(args):
    """Stop a directly-started daemon. The fallback half of ``cmd_daemon_down``."""
    from . import daemon as daemon_mod
    brr = _brr_dir()
    if daemon_mod.stop(brr):
        print("[brnrd] daemon stopped")
    else:
        print("[brnrd] daemon not running")


def cmd_daemon_up(args):
    """The one implementation behind both ``brnrd up`` and ``brnrd daemon up``.

    Prefers the installed user service; falls back to a direct foreground start
    when no service is installed (``start_service`` returns ``None``), or when
    ``--foreground`` or ``--dev-reload`` asks for one explicitly —
    ``--dev-reload`` is a foreground concept the service cannot carry, and
    delegating would silently drop it.
    """
    if not args.foreground and args.dev_reload is None:
        from . import daemon_install
        code = daemon_install.start_service()
        if code is not None:
            if code == 0:
                from . import release_availability
                observation = release_availability.refresh_if_stale(Path.cwd())
                if observation and (fact := observation.render()):
                    print(f"[brnrd] {fact}")
            return code
    return cmd_up(args)


def cmd_daemon_down(args):
    from . import daemon_install
    code = daemon_install.stop_service()
    if code is not None:
        return code
    return cmd_down(args)


def cmd_daemon_status(args):
    from . import daemon_install
    return daemon_install.status(direct_brr_dir=_maybe_brr_dir())


def cmd_daemon_install(args):
    from . import daemon_install
    return daemon_install.install(
        no_start=args.no_start,
        prompt_linger=not args.no_linger,
        assume_yes_linger=args.yes_linger,
    )


def cmd_daemon_uninstall(args):
    from . import daemon_install
    return daemon_install.uninstall(
        prompt_linger=not args.no_disable_linger,
        assume_yes_disable_linger=args.yes_disable_linger,
    )


def cmd_daemon_logs(args):
    from . import daemon_install
    return daemon_install.logs(follow=not args.no_follow, lines=args.lines)


def _connect_interrupted(step: str) -> "SystemExit":
    """#1244 fork 2, signed rider: a single ^C here must say what happened,
    not abandon the terminal to a raw traceback. Same contract
    ``init_wake.py``'s own ``_on_sigint`` already gives the (now-retired)
    terminal interview: nothing is corrupted, name the step that was cut
    off, name the exact command that resumes it. No double-press — a single
    ^C during a blocking wait (the pairing-approval poll, the linger
    prompt) is unambiguous; it is the *silence* about what survived that
    this rider objects to, not the single keystroke.
    """
    return SystemExit(
        f"\n[brnrd] interrupted during {step} — nothing was left half-done; "
        f"re-run `{brnrd_cmd()} account connect` to continue."
    )


def cmd_brnrd_connect(args):
    import os
    import socket

    from .gates import cloud

    repo_root = _repo_root()
    brr_dir = _brr_dir_for_repo(repo_root)
    url = args.url_option or args.url or os.environ.get("BRNRD_URL", "https://brnrd.dev")
    daemon_name = args.daemon_name or socket.gethostname()
    try:
        cloud.connect(brr_dir, brnrd_url=url, daemon_name=daemon_name)
    except (cloud.CloudUnavailableError, TimeoutError) as exc:
        raise SystemExit(f"[brnrd] {exc}") from None
    except KeyboardInterrupt:
        # Nothing is written to disk until the server reports `paired`
        # (`cloud.connect` only calls `_save_state` after that) — a ^C
        # during the pairing-approval poll leaves the pending pair code to
        # expire server-side on its own TTL and this machine untouched.
        raise _connect_interrupted("pairing approval") from None
    if args.no_service:
        print(
            "[brnrd] Paired without a background service. "
            f"Run `{brnrd_cmd()} up --foreground` to begin draining the brnrd inbox."
        )
        return

    # Used to skip the service install entirely here when `AGENTS.md` was
    # missing: `daemon.start` hard-exited before it ever wrote a pidfile
    # (daemon.py, the `run brnrd init first` guard), so installing anyway
    # handed launchd/systemd a job whose first line was that exit — a
    # throttled crash loop under `KeepAlive`/`Restart=on-failure`, "loaded"
    # forever, never a pidfile (#1238). #1244 fork 1 made that boot path
    # itself safe (`daemon.start` now boots, pairs, and polls with no
    # `AGENTS.md` — it prints and continues instead of exiting), so the
    # premise for skipping is gone: install proceeds unconditionally below,
    # same as an initialized repo.

    from . import daemon_install

    try:
        result = daemon_install.install(
            no_start=False,
            prompt_linger=not args.no_linger,
            assume_yes_linger=args.yes_linger,
        )
    except KeyboardInterrupt:
        # The one remaining interactive terminal moment in this command:
        # `maybe_enable_linger`'s confirm prompt (linux only; reached unless
        # ``--yes-linger``/``--no-linger`` opted out already). Pairing above
        # already landed and was reported — a ^C here only cuts off the
        # service install, which is independently re-runnable.
        raise _connect_interrupted("service install") from None
    if result == 0:
        print("[brnrd] Connected and listening in the background.")
    else:
        print(
            "[brnrd] Paired, but the background service did not come up — "
            "see the diagnosis above."
        )

    _connect_finish_setup(repo_root, brr_dir, defaults=bool(args.defaults))


def _connect_finish_setup(repo_root: Path, brr_dir: Path, *, defaults: bool) -> None:
    """#1244 fork 2 — what `connect` does about an uninitialized repo.

    ``--defaults`` (the signed opt-out rider): skip the conversational
    interview and write today's `brnrd init` defaults directly — the exact
    same headless path `brnrd init` already takes with no TTY, so this is
    not a second init implementation, only a second caller of the first
    one. Otherwise: queue the first-wake greeting event (see
    :mod:`connect_greeting`) so the resident's next dispatch is the
    interview, conducted over whichever door just proved live — never both.
    """
    if (repo_root / "AGENTS.md").exists():
        return
    if defaults:
        from . import adopt

        print("[brnrd] --defaults: writing brnrd init defaults, no interview")
        adopt.init_repo(defaults=True)
        return

    from . import connect_greeting

    outcome = connect_greeting.queue_greeting(repo_root, brr_dir)
    if outcome.queued:
        print(
            f"[brnrd] no AGENTS.md yet — queued the setup interview as the "
            f"resident's first wake over {outcome.door} ({outcome.event_id}). "
            f"It reaches you there once `{brnrd_cmd()} up` is polling; "
            f"`{brnrd_cmd()} account connect --defaults` writes plain "
            "defaults instead, any time."
        )
    else:
        print(f"[brnrd] no AGENTS.md yet, and no interview queued: {outcome.reason}.")
        print(
            f"[brnrd] run `{brnrd_cmd()} init` at the terminal, or "
            f"`{brnrd_cmd()} account connect --defaults` for plain defaults."
        )


def cmd_brnrd_disconnect(args):
    from .gates import cloud

    removed = cloud.disconnect(_brr_dir())
    if removed:
        print(
            "[brnrd] Disconnected this daemon from brnrd. "
            "Local home, knowledge, and repo registration were kept."
        )
    else:
        print("[brnrd] This daemon is not connected to brnrd.")


def _fmt_ts(epoch: float) -> str:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%SZ"
        )
    except (OverflowError, OSError, ValueError):
        return str(epoch)


def _ergonomics_empty_hint() -> None:
    print("[brnrd ergonomics] no records found. This view reads the on-disk "
          "store, which only `ergonomics=local` writes to. The default "
          "(`ergonomics=log`) surfaces findings on the daemon log instead; "
          "set `ergonomics=local` in .brr/config to persist them here.")


def cmd_ergonomics_summary(args):
    import json as _json

    from . import ergonomics

    brr_dir = _brr_dir()
    records = ergonomics.read_records(brr_dir, days=args.days)
    summaries = ergonomics.summarize(records)

    if args.json:
        print(_json.dumps(
            {"days": args.days, "total": len(records),
             "issues": [s.as_dict() for s in summaries]},
            indent=2,
        ))
        return 0

    if not summaries:
        _ergonomics_empty_hint()
        return 0

    print(f"[brnrd ergonomics] {len(records)} record(s) over {args.days}d, "
          f"{len(summaries)} issue(s):")
    for s in summaries:
        print(
            f"- {s.issue_id}: {s.count}×, first { _fmt_ts(s.first_seen) }, "
            f"last { _fmt_ts(s.last_seen) }"
        )
        for msg in s.examples:
            print(f"    · {msg}")
    return 0


def cmd_ergonomics_list(args):
    import json as _json

    from . import ergonomics

    brr_dir = _brr_dir()
    records = ergonomics.read_records(brr_dir, days=args.days, limit=args.limit)
    if args.issue:
        records = [r for r in records if r.issue_id == args.issue]

    if args.json:
        print(_json.dumps([r.as_dict() for r in records], indent=2))
        return 0

    if not records:
        _ergonomics_empty_hint()
        return 0

    for r in records:
        print(f"{ _fmt_ts(r.ts) } {r.issue_id} {r.message}")
    return 0


def cmd_ergonomics_clear(args):
    from datetime import datetime, timezone

    from . import ergonomics

    before_ts = None
    if args.before:
        before_ts = datetime.fromisoformat(args.before).replace(tzinfo=timezone.utc).timestamp()
    removed = ergonomics.clear_records(_brr_dir(), before_ts=before_ts)
    print(f"[brnrd ergonomics] cleared {removed} record(s)")
    return 0


def _portal_state_path(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    # Inside a wake the daemon hands the resident the live portal path as
    # ``BRR_PORTAL_STATE`` (the delivery contract). Honour it first so
    # ``brnrd portal state`` / ``brnrd portal facets`` resolve on demand without a
    # ``--path``, which is the whole point of "see them on demand".
    import os

    env_path = os.environ.get("BRR_PORTAL_STATE")
    if env_path:
        return Path(env_path)
    brr_dir = _maybe_brr_dir()
    if brr_dir is None:
        return None
    for candidate in (brr_dir / "portal-state.json", brr_dir / "state" / "portal-state.json"):
        if candidate.exists():
            return candidate
    return None


def _read_portal_state(path: Path | None):
    if path is None:
        return None, None, None
    import json
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw), None, None
    except Exception as e:
        return None, None, e


def _load_gate(name: str):
    """Import a gate module by name, or exit with a pointer.

    Was an if-chain naming each gate twice (once in the test, once in the
    import) beside a ``GATES`` literal that was itself a copy of
    ``gates.BUILTIN_GATES``. Three spellings of one list, and a new gate had
    to touch all three to exist. Now the owning module answers.
    """
    if name in GATES:
        return _gates.import_gate(name)
    redirect = GATE_BY_PLATFORM.get(name)
    if redirect is not None:
        raise SystemExit(redirect[1])
    raise SystemExit(f"unknown gate: {name} (known: {', '.join(GATES)})")
