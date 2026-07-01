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
        print(f"[brr runners] note: could not resolve current runner — "
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
    header = "[brr portal facets] boundary facet catalogue"
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
    print(f"[brr ergonomics] cleared {removed} record(s)")
    return 0


def _portal_state_path(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    # Inside a wake the daemon hands the resident the live portal path as
    # ``BRR_PORTAL_STATE`` (the delivery contract). Honour it first so
    # ``brr portal state`` / ``brr portal facets`` resolve on demand without a
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
