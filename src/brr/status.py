"""Status — show project state at a glance."""

from __future__ import annotations

from pathlib import Path

from .task import Task


def get_status() -> str:
    from . import gitops
    from . import config as conf
    from . import daemon as daemon_mod

    try:
        repo_root = gitops.ensure_git_repo()
    except (RuntimeError, SystemExit):
        return "[brr] not in a git repo"

    brr_dir = gitops.shared_brr_dir(repo_root)
    lines = [f"repo: {repo_root}"]

    cfg = conf.load_config(repo_root)
    runner_name = cfg.get("runner", "auto")
    lines.append(f"runner: {runner_name}")

    pid = daemon_mod.read_pid(brr_dir)
    lines.append(f"daemon: {'running (pid ' + str(pid) + ')' if pid else 'stopped'}")

    agents = repo_root / "AGENTS.md"
    lines.append(f"AGENTS.md: {'yes' if agents.exists() else 'missing'}")

    kb_dir = repo_root / "kb"
    if kb_dir.exists():
        pages = [f.name for f in kb_dir.iterdir() if f.suffix == ".md"]
        lines.append(f"kb/: {len(pages)} page(s)")
    else:
        lines.append("kb/: missing")

    from . import worktree as wt_mod
    wts = wt_mod.list_worktrees(brr_dir.parent)
    if wts:
        lines.append(f"worktrees: {len(wts)} active")
        for w in wts:
            lines.append(f"  {w.task_id} ({w.branch})")
    else:
        lines.append("worktrees: none")

    log = repo_root / "kb" / "log.md"
    if log.exists():
        recent = _recent_log(log, 5)
        if recent:
            lines.append("")
            lines.append("recent activity:")
            lines.extend(f"  {r}" for r in recent)

    return "\n".join(lines)


def inspect_task(
    task_id: str,
    repo_root: Path,
    *,
    show_event_body: bool = False,
    show_prompt: bool = False,
) -> str:
    """Return a human-readable summary of a task and its linked artifacts."""
    from . import gitops
    from . import protocol

    brr_dir = gitops.shared_brr_dir(repo_root)
    tasks_dir = brr_dir / "tasks"

    task_file = tasks_dir / f"{task_id}.md"
    if not task_file.exists():
        candidates = sorted(tasks_dir.glob(f"*{task_id}*.md")) if tasks_dir.exists() else []
        if len(candidates) == 1:
            task_file = candidates[0]
        elif candidates:
            return (
                f"Ambiguous task ID '{task_id}'. Matches:\n"
                + "\n".join(f"  {c.stem}" for c in candidates)
            )
        else:
            return f"No task found matching '{task_id}'"

    task = Task.from_file(task_file)
    if task is None:
        return f"Failed to parse task file: {task_file}"

    lines = [
        f"Task:     {task.id}",
        f"Event:    {task.event_id}",
        f"Status:   {task.status}",
        f"Branch:   {task.branch}",
        f"Env:      {task.env}",
    ]
    if task.source:
        lines.append(f"Source:   {task.source}")

    event_file = brr_dir / "inbox" / f"{task.event_id}.md"
    lines.append(f"Event file: {event_file}{'' if event_file.exists() else ' (missing)'}")

    branch_name = task.meta.get("branch_name") or task.resolve_branch_name()
    if branch_name:
        lines.append(f"Git branch: {branch_name}")
    base_branch = task.meta.get("base_branch")
    if base_branch:
        lines.append(f"Base branch: {base_branch}")

    resp = task.meta.get("response_path")
    if resp:
        exists = Path(resp).exists()
        lines.append(f"Response: {resp}{'' if exists else ' (missing)'}")
    else:
        default_resp = brr_dir / "responses" / f"{task.event_id}.md"
        if default_resp.exists():
            lines.append(f"Response: {default_resp}")

    trace_dirs_str = task.meta.get("trace_dirs", "")
    trace_dirs = []
    if trace_dirs_str:
        lines.append("Traces:")
        for td in trace_dirs_str.split(", "):
            trace_dirs.append(td.strip())
            full = brr_dir / td.strip()
            exists = full.exists()
            lines.append(f"  {td}{'' if exists else ' (missing)'}")
    else:
        trace_dirs = _scan_traces(brr_dir, task, lines)

    latest_prompt = _latest_prompt_path(brr_dir, trace_dirs)
    if latest_prompt:
        lines.append(f"Latest prompt: {latest_prompt}")

    wt = task.meta.get("worktree_path")
    if wt:
        exists = Path(wt).exists()
        lines.append(f"Worktree: {wt}{' (exists)' if exists else ' (removed)'}")
    else:
        wt_default = brr_dir / "worktrees" / task.id
        if wt_default.exists():
            lines.append(f"Worktree: {wt_default} (exists)")

    extra_keys = set(task.meta) - {
        "base_branch", "branch_name", "response_path", "trace_dirs", "worktree_path",
    }
    if extra_keys:
        lines.append("Meta:")
        for k in sorted(extra_keys):
            lines.append(f"  {k}: {task.meta[k]}")

    event_body = None
    if event_file.exists():
        event_text = event_file.read_text(encoding="utf-8")
        event_body = protocol.frontmatter_body(event_text).strip()
    elif show_event_body:
        event_body = _event_body_from_trace_prompts(brr_dir, trace_dirs, task.event_id)

    if show_event_body and event_body:
        lines.append("")
        lines.append("Event body:")
        lines.append(event_body)

    if show_prompt and latest_prompt and latest_prompt.exists():
        lines.append("")
        lines.append("Latest prompt:")
        lines.append(latest_prompt.read_text(encoding="utf-8").strip())

    return "\n".join(lines)


def _scan_traces(brr_dir: Path, task: Task, lines: list[str]) -> list[str]:
    """Fall back to scanning .brr/traces/ for dirs matching the event ID."""
    traces_dir = brr_dir / "traces"
    if not traces_dir.exists():
        return []
    found = []
    for kind_dir in sorted(traces_dir.iterdir()):
        if not kind_dir.is_dir():
            continue
        for td in sorted(kind_dir.iterdir()):
            if task.event_id in td.name or task.id in td.name:
                found.append(str(td.relative_to(brr_dir)))
    if found:
        lines.append("Traces:")
        for f in found:
            lines.append(f"  {f}")
    return found


def _latest_prompt_path(brr_dir: Path, trace_dirs: list[str]) -> Path | None:
    """Return the most useful prompt path from linked traces, if present."""
    candidates = []
    for td in trace_dirs:
        prompt = brr_dir / td / "prompt.md"
        if prompt.exists():
            candidates.append(prompt)
    daemon_prompts = [p for p in candidates if "/daemon-run/" in p.as_posix()]
    if daemon_prompts:
        return daemon_prompts[-1]
    if candidates:
        return candidates[-1]
    return None


def _event_body_from_trace_prompts(
    brr_dir: Path,
    trace_dirs: list[str],
    event_id: str,
) -> str | None:
    """Recover event body from the linked triage prompt when inbox was pruned."""
    marker = f"---\nEvent ID: {event_id}\n\n"
    for td in trace_dirs:
        if not td.startswith("traces/triage/"):
            continue
        prompt = brr_dir / td / "prompt.md"
        if not prompt.exists():
            continue
        text = prompt.read_text(encoding="utf-8")
        if marker in text:
            return text.split(marker, 1)[1].strip()
    return None


def _recent_log(path: Path, n: int) -> list[str]:
    """Extract the last *n* log entry headings."""
    text = path.read_text(encoding="utf-8")
    entries = [
        line.strip()
        for line in text.splitlines()
        if line.startswith("## [")
    ]
    return entries[-n:]
