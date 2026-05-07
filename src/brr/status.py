"""Status — local troubleshooting helpers.

Status is no longer the primary UX for brr; remote gates own the live
progress story. This module survives as a small set of dev-phase
diagnostics that operators run from a terminal when something looks
off:

- ``get_status``      overall daemon health and the active task (if any).
- ``inspect_task``    deep dive on a specific task — useful when a
                      remote run failed and you need traces, paths and
                      any preserved Docker container IDs.

Both renderers route through ``run_progress`` for the per-task block
so local diagnostics and remote cards stay consistent.
"""

from __future__ import annotations

from pathlib import Path

from . import conversations, run_progress
from .task import Task


# ── High-level status ───────────────────────────────────────────────


def get_status() -> str:
    """Render terse daemon health plus the active task, if any."""
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
    lines.append(
        f"daemon: {'running (pid ' + str(pid) + ')' if pid else 'stopped'}"
    )

    agents = repo_root / "AGENTS.md"
    lines.append(f"AGENTS.md: {'yes' if agents.exists() else 'missing'}")

    active = _active_run_progress(brr_dir)
    if active is not None:
        lines.append("")
        lines.append("active task:")
        for line in run_progress.render_text(active, compact=True).splitlines():
            lines.append(f"  {line}")

    return "\n".join(lines)


def _active_run_progress(brr_dir: Path) -> run_progress.RunProgressView | None:
    """Find the most recently-touched non-terminal task across conversations."""
    candidates: list[tuple[str, run_progress.RunProgressView]] = []
    for key in conversations.list_conversations(brr_dir):
        view = run_progress.project_conversation_latest(brr_dir, key)
        if view is None or view.task_id is None or view.is_terminal:
            continue
        candidates.append((view.updated_at or "", view))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


# ── Per-task troubleshooting ────────────────────────────────────────


def inspect_task(
    task_id: str,
    repo_root: Path,
    *,
    show_event_body: bool = False,
    show_prompt: bool = False,
) -> str:
    """Return a deep summary of a task and its linked artifacts.

    Useful when a remote run failed and you need to know which traces,
    response files, worktrees or preserved Docker containers to look at.
    """
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
        f"Env:      {task.env}",
    ]
    if task.source:
        lines.append(f"Source:   {task.source}")
    if task.conversation_key:
        lines.append(f"Conv:     {task.conversation_key}")
        conv_artifacts = [
            r for r in conversations.records_for_task(
                brr_dir, task.conversation_key, task.id,
            )
            if r.get("kind") == "artifact"
        ]
        if conv_artifacts:
            lines.append("  artifacts:")
            for art in conv_artifacts:
                label = art.get("label") or art.get("artifact_kind") or "artifact"
                path = art.get("path", "")
                lines.append(f"    {label} \u2192 {path}")

    event_file = brr_dir / "inbox" / f"{task.event_id}.md"
    lines.append(f"Event file: {event_file}{'' if event_file.exists() else ' (missing)'}")

    branch_name = task.meta.get("branch_name")
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
    trace_dirs: list[str] = []
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

    docker_containers = task.meta.get("docker_containers")
    if docker_containers:
        lines.append(f"Docker containers (preserved): {docker_containers}")

    extra_keys = set(task.meta) - {
        "base_branch",
        "branch_name",
        "response_path",
        "trace_dirs",
        "worktree_path",
        "docker_containers",
    }
    if extra_keys:
        lines.append("Meta:")
        for k in sorted(extra_keys):
            lines.append(f"  {k}: {task.meta[k]}")

    if task.conversation_key:
        view = run_progress.project_task(brr_dir, task.conversation_key, task.id)
        if view is not None:
            lines.append("")
            lines.append("Run progress:")
            for line in run_progress.render_text(view, compact=False).splitlines():
                lines.append(f"  {line}")

    event_body = None
    if event_file.exists():
        event_text = event_file.read_text(encoding="utf-8")
        event_body = protocol.frontmatter_body(event_text).strip()

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
    found: list[str] = []
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
    candidates: list[Path] = []
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
