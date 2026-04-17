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

    brr_dir = repo_root / ".brr"
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
    wts = wt_mod.list_worktrees(repo_root)
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


def inspect_task(task_id: str, repo_root: Path) -> str:
    """Return a human-readable summary of a task and its linked artifacts."""
    brr_dir = repo_root / ".brr"
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

    branch_name = task.meta.get("branch_name") or task.resolve_branch_name()
    if branch_name:
        lines.append(f"Git branch: {branch_name}")

    resp = task.meta.get("response_path")
    if resp:
        exists = Path(resp).exists()
        lines.append(f"Response: {resp}{'' if exists else ' (missing)'}")
    else:
        default_resp = brr_dir / "responses" / f"{task.event_id}.md"
        if default_resp.exists():
            lines.append(f"Response: {default_resp}")

    trace_dirs_str = task.meta.get("trace_dirs", "")
    if trace_dirs_str:
        lines.append("Traces:")
        for td in trace_dirs_str.split(", "):
            full = brr_dir / td.strip()
            exists = full.exists()
            lines.append(f"  {td}{'' if exists else ' (missing)'}")
    else:
        _scan_traces(brr_dir, task, lines)

    wt = task.meta.get("worktree_path")
    if wt:
        exists = Path(wt).exists()
        lines.append(f"Worktree: {wt}{' (exists)' if exists else ' (removed)'}")
    else:
        wt_default = brr_dir / "worktrees" / task.id
        if wt_default.exists():
            lines.append(f"Worktree: {wt_default} (exists)")

    extra_keys = set(task.meta) - {
        "branch_name", "response_path", "trace_dirs", "worktree_path",
    }
    if extra_keys:
        lines.append("Meta:")
        for k in sorted(extra_keys):
            lines.append(f"  {k}: {task.meta[k]}")

    return "\n".join(lines)


def _scan_traces(brr_dir: Path, task: Task, lines: list[str]) -> None:
    """Fall back to scanning .brr/traces/ for dirs matching the event ID."""
    traces_dir = brr_dir / "traces"
    if not traces_dir.exists():
        return
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


def _recent_log(path: Path, n: int) -> list[str]:
    """Extract the last *n* log entry headings."""
    text = path.read_text(encoding="utf-8")
    entries = [
        line.strip()
        for line in text.splitlines()
        if line.startswith("## [")
    ]
    return entries[-n:]
