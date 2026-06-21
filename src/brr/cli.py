"""brr CLI — thin dispatch layer over the library modules."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    from . import __version__
    parser = argparse.ArgumentParser(
        prog="brr",
        description="Playbook + knowledge base for AI agents, with remote execution",
    )
    parser.add_argument("--version", action="version", version=f"brr {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="set up a repo for brr")
    p.add_argument("url", nargs="?", default=None, help="clone URL (optional)")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="ask setup questions (runner, config) with timed defaults")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("run", help="run a task through the runner")
    p.add_argument("instruction", help="what to do")
    p.set_defaults(func=cmd_run)

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

    p = sub.add_parser("bind", help="bind repo to a gate channel or watch")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("setup", help="configure a gate in one step")
    p.add_argument("gate", help="gate name (telegram, slack, git)")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("up", help="start the daemon")
    p.add_argument("--dev-reload", action="store_true", default=None,
                   help="developer: re-exec daemon when brr package files change")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("down", help="stop the daemon")
    p.set_defaults(func=cmd_down)

    daemon_p = sub.add_parser("daemon", help="daemon lifecycle")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_command", required=True)

    p = daemon_sub.add_parser("up", help="start the daemon")
    p.add_argument("--foreground", action="store_true",
                   help="run the foreground daemon instead of the installed service")
    p.add_argument("--dev-reload", action="store_true", default=None,
                   help="developer: re-exec daemon when brr package files change")
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

    brnrd_p = sub.add_parser("brnrd", help="brnrd managed backend")
    brnrd_sub = brnrd_p.add_subparsers(dest="brnrd_command", required=True)

    p = brnrd_sub.add_parser(
        "connect", help="link this daemon to a brnrd project (device-flow)")
    p.add_argument("--url", default=None,
                   help="brnrd base URL (default: $BRNRD_URL or https://brnrd.dev)")
    p.add_argument("--daemon-name", default=None,
                   help="name to register this daemon under (default: hostname)")
    p.set_defaults(func=cmd_brnrd_connect)

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
        "wrap",
        help="run a command and surface live portal state when it changes")
    p.add_argument("--always", action="store_true",
                   help="print portal state after the command even when unchanged")
    p.add_argument("--path", default=None,
                   help="read this portal-state.json path instead of auto-detecting")
    p.add_argument("command", nargs=argparse.REMAINDER,
                   help="command to run; prefix with -- to stop option parsing")
    p.set_defaults(func=cmd_portal_wrap)

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

    args = parser.parse_args(argv)
    return args.func(args)


def _repo_root() -> Path:
    from . import gitops
    return gitops.ensure_git_repo()


def _brr_dir() -> Path:
    from . import gitops

    return gitops.shared_brr_dir(_repo_root())


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
        print(f"[brr] warning: daemon running (pid {pid}) — concurrent writes possible")

    from . import runner
    runner.run_task(args.instruction)


def cmd_agent_inject(args):
    import sys

    from . import prompts

    repo_root = _maybe_repo_root()
    if repo_root is None:
        print("[brr agent inject] not inside a git repo", file=sys.stderr)
        return 2
    text = prompts.build_injected_context(repo_root, task_text=args.task)
    if not text.strip():
        print("[brr agent inject] no dominion here yet — bootstrap one with "
              "`brr init` or by starting the daemon", file=sys.stderr)
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
        print(f"[brr docs] unknown topic: {args.topic}", file=sys.stderr)
        print(docs.format_listing(repo_root), file=sys.stderr)
        return 1
    print(text)
    return 0


def _latest_portal_state_path() -> Path | None:
    import os

    env_path = os.environ.get("BRR_PORTAL_STATE")
    if env_path:
        return Path(env_path)
    brr_dir = _maybe_brr_dir()
    if brr_dir is None:
        return None
    candidates = list((brr_dir / "outbox").glob("*/portal-state.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _portal_state_path(path_arg: str | None) -> Path | None:
    return Path(path_arg) if path_arg else _latest_portal_state_path()


def _read_portal_state(
    path: Path | None,
) -> tuple[dict | None, str | None, str | None]:
    import json

    if path is None or not path.exists():
        return None, None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, str(exc)
    if not isinstance(payload, dict):
        return None, None, "portal state root is not an object"
    token = payload.get("change_token")
    return payload, str(token) if token else None, None


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
    lines = [
        "[brr portal state] "
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
        f"outbound={outbound.get('outbound_messages', 0)}",
        "budget: "
        f"elapsed={_fmt_duration(budget.get('elapsed_seconds'))} "
        f"limit={_fmt_duration(budget.get('budget_seconds'))} "
        f"keepalive={(budget.get('keepalive') or {}).get('status', '-')}",
    ]
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


def cmd_portal_state(args):
    import json
    import sys

    path = _portal_state_path(args.path)
    payload, _token, error = _read_portal_state(path)
    if payload is None:
        if error and path is not None:
            print(
                f"[brr portal state] could not read {path}: {error}",
                file=sys.stderr,
            )
            return 2
        print(
            "[brr portal state] no live portal-state.json found "
            "(run inside a daemon wake or pass --path)",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_portal_state(payload))
    return 0


def cmd_portal_wrap(args):
    import subprocess
    import sys

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("[brr portal wrap] missing command", file=sys.stderr)
        return 2

    path = _portal_state_path(args.path)
    _before_payload, before_token, _before_error = _read_portal_state(path)
    code = subprocess.call(command)
    after_payload, after_token, _after_error = _read_portal_state(path)
    should_print = (
        args.always
        or (
            after_payload is not None
            and after_token is not None
            and after_token != before_token
        )
    )
    if should_print and after_payload is not None:
        print(
            "\n[brr portal update] live state after command:",
            file=sys.stderr,
        )
        print(_format_portal_state(after_payload), file=sys.stderr)
    return code


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
            print(f"[brr review] {e}")
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
        print("[brr review] pass `--check`, `--pr-title`, or `--pr-body` "
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
        print(f"[brr review] {path.name}: {n_cards} cards, "
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
    gate_mod = _load_gate(args.gate)
    gate_mod.bind(_brr_dir())


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
        print("[brr] daemon stopped")
    else:
        print("[brr] daemon not running")


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
    url = args.url or os.environ.get("BRNRD_URL", "https://brnrd.dev")
    daemon_name = args.daemon_name or socket.gethostname()
    cloud.connect(brr_dir, brnrd_url=url, daemon_name=daemon_name)
    print("[brr] Start the daemon with `brr up` to begin draining the brnrd inbox.")


def _fmt_ts(epoch: float) -> str:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%SZ"
        )
    except (OverflowError, OSError, ValueError):
        return str(epoch)


def _ergonomics_empty_hint() -> None:
    print("[brr ergonomics] no records found. This view reads the on-disk "
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

    print(f"[brr ergonomics] {len(records)} record(s) over {args.days}d, "
          f"{len(summaries)} issue(s):")
    for s in summaries:
        envs = ",".join(sorted(s.envs)) or "-"
        print(f"  {s.severity:5}  {s.count:4}x  {s.issue:22}  "
              f"last={_fmt_ts(s.last_seen)}  env={envs}")
    return 0


def cmd_ergonomics_list(args):
    import json as _json

    from . import ergonomics

    brr_dir = _brr_dir()
    records = ergonomics.read_records(brr_dir, days=args.days, issue=args.issue)
    records = records[-args.limit:] if args.limit else records

    if args.json:
        print(_json.dumps([r.__dict__ for r in records], indent=2, default=str))
        return 0

    if not records:
        _ergonomics_empty_hint()
        return 0

    for r in records:
        hint = r.detail.get("hint") if isinstance(r.detail, dict) else None
        line = (f"  {_fmt_ts(r.timestamp)}  {r.severity:5}  {r.issue:22}  "
                f"env={r.env or '-'}")
        if r.run_id:
            line += f"  run={r.run_id}"
        print(line)
        if hint:
            print(f"      {hint}")
    return 0


def cmd_ergonomics_clear(args):
    from . import ergonomics

    brr_dir = _brr_dir()
    removed = ergonomics.clear(brr_dir, before=args.before)
    scope = f"before {args.before}" if args.before else "all"
    print(f"[brr ergonomics] cleared {len(removed)} day-file(s) ({scope}).")
    return 0


def _load_gate(name: str):
    gate_map = {
        "telegram": "telegram",
        "slack": "slack",
        "github": "github",
        "cloud": "cloud",
    }
    mod_name = gate_map.get(name)
    if not mod_name:
        raise SystemExit(f"[brr] unknown gate: {name} (available: {', '.join(gate_map)})")
    from .gates import import_gate
    return import_gate(mod_name)
