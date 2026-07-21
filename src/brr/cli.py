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
from pathlib import Path

#: Every gate brnrd knows how to auth/bind/configure. Single source of truth
#: for ``_load_gate``, ``brnrd gate list``, and the gate argument help.
GATES = ("telegram", "slack", "github", "cloud")

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
    "init", "run", "review", "up", "down",
    "daemon", "gate", "account", "home",
    "kb", "docs", "portal", "runners", "bench", "agent", "ergonomics",
    "completions",
)

#: Verbs that parse but are hidden from ``--help``.
HIDDEN_COMMANDS = ("prompts", "hook", "statusline", "worktree-hygiene")

#: Everything ``brnrd <verb>`` accepts, retired pointers included.
ALL_COMMANDS = tuple(
    sorted(PUBLIC_COMMANDS + HIDDEN_COMMANDS + tuple(RETIRED_COMMANDS))
)


def build_parser() -> argparse.ArgumentParser:
    """Build the full argparse tree.

    Split out of ``main`` so the CLI surface is inspectable without running a
    command — the completions generator walks this tree, and the surface test
    pins it.
    """
    from . import __version__
    parser = argparse.ArgumentParser(
        prog="brnrd",
        description="Resident agent runtime for local and managed repo work",
    )
    parser.add_argument("--version", action="version", version=f"brnrd {__version__}")

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("init", help="set up a repo for brnrd")
    p.add_argument("url", nargs="?", default=None, help="clone URL (optional)")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="ask setup questions (runner, config) with timed defaults")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("run", help="run a task through the runner")
    p.add_argument("instruction", help="what to do")
    p.set_defaults(func=cmd_run)

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
    p.set_defaults(func=cmd_brnrd_connect)

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

    p = sub.add_parser("kb", help="search home/repo knowledge")
    p.add_argument("query", help="search term")
    p.add_argument("--limit", type=int, default=20,
                   help="maximum matching lines to print")
    p.set_defaults(func=cmd_kb)

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
    p.add_argument("phase", help="abstract phase: post-tool | stop | session-start")
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


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    return args.func(args)


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
    adopt.init_repo(args.url, interactive=args.interactive)


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
        is_worker=False,
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
        is_worker=False,
        runner_shell=runner_medium,
        runner_core=runner_core,
        hooks_installed=prompts.probe_shell_hook_capability(runner_medium),
        hook_stamps={},
    )

    # Read each file-backed block from disk. For an untrimmed block that is
    # exactly what the wake received; a trimmed one gets `_trim_note`. Blocks at
    # `location == "computed"` are live state and stay prose — they are not on
    # disk and a Read returning them would be fiction.
    block_text: dict[str, str] = {}
    for entry in score.contracts:
        if not entry.present or entry.location == tx.COMPUTED:
            continue
        try:
            block_text[entry.block_key] = Path(entry.location).read_text(
                encoding="utf-8"
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


def cmd_kb(args):
    from . import config as conf
    from . import knowledge

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)
    checkout = knowledge.ensure_checkout(repo_root, cfg)
    hits = knowledge.search(repo_root, args.query, cfg, limit=args.limit)
    if not hits:
        print(f"[brnrd kb] no matches for {args.query!r}")
        print(f"[brnrd kb] checkout: {checkout}")
        return 1
    for hit in hits:
        rel = hit.path
        try:
            rel = hit.path.relative_to(repo_root)
        except ValueError:
            pass
        print(f"{hit.source}: {rel}:{hit.line_no}: {hit.line}")
    return 0


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
            "brnrd account add requires a connected account home; "
            "run `brnrd account connect` first"
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

    if not moves:
        print(f"[brnrd account relabel] no memory found under {args.old_label!r}.")
        print("  Nothing to move. (Already relabelled? Check `brnrd account status`.)")
        return 0

    print(f"[brnrd account relabel] {args.old_label} → {args.new_label}")
    for move in moves:
        print(f"  {move.scope:<14} {move.src}")
        print(f"  {'':<14}   → {move.dst}")
    print(f"  registry       account/repos.json: rekey entry"
          + (" + default_repo" if ctx.default_repo.label == args.old_label else ""))

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
    repos = sorted(ctx.repos.values(), key=lambda r: r.label)

    if getattr(args, "json", False):
        print(_json.dumps({
            "kind": ctx.kind,
            "account_id": ctx.account_id or None,
            "home_id": ctx.home_id or None,
            "dominion_repo": str(ctx.dominion_repo),
            "enabled": ctx.enabled,
            "default_repo": ctx.default_repo.label,
            "repos": [
                {"label": r.label, "root": str(r.root), "default":
                 r.label == ctx.default_repo.label}
                for r in repos
            ],
        }, indent=2, sort_keys=True))
        return 0

    print(f"[brnrd account status] home kind: {ctx.kind}")
    if ctx.account_id:
        print(f"  account id   : {ctx.account_id}")
    print(f"  home         : {ctx.dominion_repo}")
    print(f"  enabled      : {'yes' if ctx.enabled else 'no'}")
    print(f"  repos        : {len(repos)}")
    for r in repos:
        star = "★" if r.label == ctx.default_repo.label else " "
        print(f"    {star} {r.label:<24} {r.root}")
    if ctx.kind != "account":
        print("\n  this is a project home — `brnrd account connect` links it to brnrd.")
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


def cmd_brnrd_connect(args):
    import os
    import socket

    from .gates import cloud

    brr_dir = _brr_dir()
    url = args.url_option or args.url or os.environ.get("BRNRD_URL", "https://brnrd.dev")
    daemon_name = args.daemon_name or socket.gethostname()
    cloud.connect(brr_dir, brnrd_url=url, daemon_name=daemon_name)
    print("[brnrd] Start the daemon with `brnrd up` to begin draining the brnrd inbox.")


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
    if name == "github":
        from .gates import github
        return github
    if name == "cloud":
        from .gates import cloud
        return cloud
    if name == "telegram":
        from .gates import telegram
        return telegram
    if name == "slack":
        from .gates import slack
        return slack
    raise SystemExit(f"unknown gate: {name} (known: {', '.join(GATES)})")
