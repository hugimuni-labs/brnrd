"""brnrd CLI — thin dispatch layer over the library modules."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    from . import __version__
    parser = argparse.ArgumentParser(
        prog="brnrd",
        description="Resident agent runtime for local and managed repo work",
    )
    parser.add_argument("--version", action="version", version=f"brnrd {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="set up a repo for brnrd")
    p.add_argument("url", nargs="?", default=None, help="clone URL (optional)")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="ask setup questions (runner, config) with timed defaults")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("run", help="run a task through the runner")
    p.add_argument("instruction", help="what to do")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "worktree-hygiene",
        help="dry-run report for local worktree/branch hygiene",
    )
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

    p = sub.add_parser("auth", help="authenticate a gate")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("bind", help="bind a repo-local gate to this repo")
    p.add_argument("repo", help="repo path to bind")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("add", help="add a repo to the connected account home")
    p.add_argument("repo", help="repo path to add")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("connect", help="link this daemon to brnrd")
    p.add_argument("url", nargs="?", default=None,
                   help="brnrd base URL (default: $BRNRD_URL or https://brnrd.dev)")
    p.add_argument("--url", dest="url_option", default=None,
                   help="brnrd base URL (same as positional URL)")
    p.add_argument("--daemon-name", default=None,
                   help="name to register this daemon under (default: hostname)")
    p.set_defaults(func=cmd_brnrd_connect)

    p = sub.add_parser("setup", help="configure a gate in one step")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_setup)

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

    p = sub.add_parser("up", help="start the daemon")
    p.add_argument("--dev-reload", action="store_true", default=None,
                   help="developer: re-exec daemon when brnrd package files change")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("down", help="stop the daemon")
    p.set_defaults(func=cmd_down)

    daemon_p = sub.add_parser("daemon", help="daemon lifecycle")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_command", required=True)

    p = daemon_sub.add_parser("up", help="start the daemon")
    p.add_argument("--foreground", action="store_true",
                   help="run the foreground daemon instead of the installed service")
    p.add_argument("--dev-reload", action="store_true", default=None,
                   help="developer: re-exec daemon when brnrd package files change")
    p.set_defaults(func=cmd_daemon_up)

    p = daemon_sub.add_parser("down", help="stop the daemon")
    p.set_defaults(func=cmd_daemon_down)

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

    p = sub.add_parser(
        "hook",
        help="runner hooks back channel endpoint (Tier 2; called by the "
             "runner's native lifecycle hooks, not by hand)")
    p.add_argument("phase", help="abstract phase: post-tool | stop | session-start")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser(
        "statusline",
        help="Claude statusLine helper for interactive sessions; reads session "
             "JSON on stdin when wired into Claude's TUI footer")
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
        help="list declared runner profiles and the bundled Core registry")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    p.add_argument("--all", action="store_true",
                   help="include bundled Cores whose Shell is not on PATH")
    p.set_defaults(func=cmd_runners_list)

    prompts_p = sub.add_parser(
        "prompts",
        help="inspect the prompt assembly: source manifest, boot score")
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

    args = parser.parse_args(argv)
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
    """List declared runner profiles and the bundled Core registry.

    Two sections:

    - **Declared profiles** — what *runners.md* declares (bundled or
      project-owned). These are the profiles the selector and daemon
      actually invoke. Shows PATH availability, class, hooks, and
      cost_rank.
    - **Bundled Core registry** — the ``runner_cores`` module's registry of
      known Shell/Core pairs with model IDs, provider, class, and freshness.
      Filtered to PATH-available Shells by default; ``--all`` shows all.
      The registry extends the selector when a user pins ``core=`` in
      ``.brr/config`` without declaring an explicit profile entry.

    A ★ marks the currently resolved runner (the one the daemon would
    pick for the next run).
    """
    import json as _json
    import shutil
    import sys

    from . import runner as runner_mod
    from . import runner_cores, runner_select

    repo_root = _maybe_repo_root()

    # Current runner — best-effort; may fail outside a repo or without a Shell
    current_runner: str | None = None
    current_runner_err: str | None = None
    try:
        if repo_root:
            current_runner = runner_mod.resolve_runner(repo_root)
    except Exception as exc:  # noqa: BLE001
        current_runner_err = str(exc)

    # Declared profiles (from runners.md, bundled or project)
    declared_profiles: dict[str, dict] = {}
    try:
        declared_profiles = runner_mod._load_profiles(repo_root) or {}
    except Exception:
        declared_profiles = {}

    # Build declared-profile rows
    declared_rows = []
    for name, meta in declared_profiles.items():
        meta = meta or {}
        binary = str(meta.get("binary") or name).strip()
        on_path = shutil.which(binary) is not None
        is_alias = bool(meta.get("binary"))  # alias profiles have an explicit binary
        declared_rows.append({
            "name": name,
            "shell": binary,
            "model": str(meta.get("model") or "").strip() or None,
            "class": str(meta.get("class") or "").strip() or None,
            "provider": str(meta.get("provider") or "").strip() or None,
            "hooks": str(meta.get("hooks") or "").strip() or None,
            "cost_rank": meta.get("cost_rank"),
            "quota_source": str(meta.get("quota_source") or "").strip() or None,
            "owner": str(meta.get("owner") or "user").strip() or "user",
            "on_path": on_path,
            "is_alias": is_alias,
            "is_current": name == current_runner,
        })

    # Bundled Core registry rows
    show_all = getattr(args, "all", False)
    all_bundled = runner_cores.all_cores()
    bundled_rows = []
    for name, entry in all_bundled.items():
        shell = str(entry.get("shell") or "").strip()
        on_path = shutil.which(shell) is not None
        if not show_all and not on_path:
            continue
        bundled_rows.append({
            "name": name,
            "shell": shell,
            "model": str(entry.get("model") or "").strip() or None,
            "class": str(entry.get("class") or "").strip() or None,
            "provider": str(entry.get("provider") or "").strip() or None,
            "cost_rank": entry.get("cost_rank"),
            "freshness_date": str(entry.get("freshness_date") or "").strip() or None,
            "on_path": on_path,
            "is_current": name == current_runner,
        })

    if getattr(args, "json", False):
        print(_json.dumps({
            "current_runner": current_runner,
            "current_runner_error": current_runner_err,
            "declared": declared_rows,
            "bundled_cores": bundled_rows,
        }, indent=2, sort_keys=True))
        return 0

    # ── Text output ──────────────────────────────────────────────────
    if current_runner_err and not current_runner:
        print(f"[brnrd runners] note: could not resolve current runner — "
              f"{current_runner_err}", file=sys.stderr)

    def _mark(row: dict) -> str:
        return "★" if row.get("is_current") else " "

    def _avail(row: dict) -> str:
        return "✓" if row.get("on_path") else "✗"

    # Declared profiles
    print(f"declared profiles — {len(declared_rows)} profile(s), "
          f"{sum(1 for r in declared_rows if r['on_path'])} on PATH  "
          "(★ = selected by resolver, ✓ = Shell on PATH):")
    if not declared_rows:
        print("  (none)")
    else:
        for row in declared_rows:
            parts = [
                f"{_mark(row)} {_avail(row)} {row['name']:<28}",
                f"{row['shell']:<8}",
                f"{row['class'] or '—':<10}",
                f"rank={row['cost_rank'] if row['cost_rank'] is not None else '—'}",
            ]
            extras = []
            if row["model"]:
                extras.append(f"model={row['model']}")
            if row["hooks"]:
                extras.append(f"hooks={row['hooks']}")
            if row["is_alias"]:
                extras.append("alias")
            if not row["on_path"]:
                extras.append("not found")
            if extras:
                parts.append(f"  [{', '.join(extras)}]")
            print("  " + "  ".join(parts))

    # Bundled Core registry
    print()
    all_label = " (all, including unavailable)" if show_all else ""
    print(f"bundled Core registry{all_label} — {len(bundled_rows)} "
          f"core(s) shown  (add --all to include unavailable Shells):")
    if not bundled_rows:
        print("  (none on PATH — install claude, codex, or gemini)")
    else:
        for row in bundled_rows:
            avail = "✓" if row.get("on_path") else "✗ (not on PATH)"
            parts = [
                f"  {_mark(row)} {avail}  {row['name']:<20}",
                f"{row['shell']:<8}",
                f"{row['model'] or '—':<30}",
                f"{row['class'] or '—':<10}",
                f"rank={row['cost_rank'] if row['cost_rank'] is not None else '—'}",
                f"fresh={row['freshness_date'] or '—'}",
            ]
            print("  ".join(parts))

    return 0


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
        raise SystemExit("brnrd add requires a connected account home; run `brnrd connect` first")
    repo_root = _repo_root_from_arg(args.repo)
    target_cfg = conf.load_config(repo_root)
    label = account.repo_label(repo_root, target_cfg)
    account.register_repo(ctx, repo_root, label=label)
    print(f"[brnrd] added {label} to account home {ctx.dominion_repo}")


def cmd_home_link(args):
    import sys

    from . import config as conf
    from . import home_link

    repo_root = _repo_root()
    cfg = conf.load_config(repo_root)

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
            dominion_name=args.dominion_name or home_link.DEFAULT_DOMINION_NAME,
            knowledge_name=args.knowledge_name or home_link.DEFAULT_KNOWLEDGE_NAME,
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


def cmd_up(args):
    from . import daemon as daemon_mod
    root = _repo_root()
    daemon_mod.start(root, dev_reload=args.dev_reload)


def cmd_down(args):
    from . import daemon as daemon_mod
    brr = _brr_dir()
    if daemon_mod.stop(brr):
        print("[brnrd] daemon stopped")
    else:
        print("[brnrd] daemon not running")


def cmd_daemon_up(args):
    if not args.foreground:
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
    raise SystemExit(f"unknown gate: {name}")
