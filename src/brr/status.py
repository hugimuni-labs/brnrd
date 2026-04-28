"""Status — show project state at a glance."""

from __future__ import annotations

from pathlib import Path

from . import stream as stream_mod
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

    streams = [s for s in stream_mod.list_streams(brr_dir) if s.status == "active"]
    if streams:
        lines.append(f"streams: {len(streams)} active")
        for s in streams[:5]:
            title = s.title or "(untitled)"
            lines.append(f"  {s.id} — {title}")
        if len(streams) > 5:
            lines.append(f"  … and {len(streams) - 5} more")
    else:
        lines.append("streams: none")

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
    if task.stream_id:
        lines.append(f"Stream:   {task.stream_id}")
        manifest = stream_mod.load_manifest(brr_dir, task.stream_id)
        if manifest:
            if manifest.title:
                lines.append(f"  title:   {manifest.title}")
            if manifest.intent:
                lines.append(f"  intent:  {manifest.intent}")
            artifacts = stream_mod.read_artifacts(brr_dir, task.stream_id)
            task_artifacts = [a for a in artifacts if a.get("task_id") == task.id]
            if task_artifacts:
                lines.append("  artifacts:")
                for art in task_artifacts:
                    label = art.get("label") or art.get("kind", "artifact")
                    path = art.get("path", "")
                    lines.append(f"    {label} → {path}")

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


# ── Streams ─────────────────────────────────────────────────────────


def list_streams() -> str:
    """Render `brr streams` — all known streams with high-level status."""
    from . import gitops

    try:
        repo_root = gitops.ensure_git_repo()
    except (RuntimeError, SystemExit):
        return "[brr] not in a git repo"

    brr_dir = gitops.shared_brr_dir(repo_root)
    streams = stream_mod.list_streams(brr_dir)
    if not streams:
        return "No streams yet."

    lines: list[str] = []
    for manifest in streams:
        tasks = stream_mod.read_tasks(brr_dir, manifest.id)
        artifacts = stream_mod.read_artifacts(brr_dir, manifest.id)
        title = manifest.title or "(untitled)"
        lines.append(
            f"{manifest.id}  [{manifest.status}]  {title}"
        )
        if manifest.intent:
            lines.append(f"  intent: {manifest.intent}")
        lines.append(
            f"  tasks: {len(tasks)}  artifacts: {len(artifacts)}"
        )
        if manifest.updated:
            lines.append(f"  updated: {manifest.updated}")
        lines.append("")
    return "\n".join(lines).rstrip()


def show_stream(stream_id: str) -> str:
    """Render `brr stream show <id>` — manifest, tasks, artifacts, events."""
    from . import gitops

    try:
        repo_root = gitops.ensure_git_repo()
    except (RuntimeError, SystemExit):
        return "[brr] not in a git repo"

    brr_dir = gitops.shared_brr_dir(repo_root)
    manifest = stream_mod.load_manifest(brr_dir, stream_id)
    if manifest is None:
        candidates = [m for m in stream_mod.list_streams(brr_dir) if stream_id in m.id]
        if len(candidates) == 1:
            manifest = candidates[0]
            stream_id = manifest.id
        elif candidates:
            return (
                f"Ambiguous stream ID '{stream_id}'. Matches:\n"
                + "\n".join(f"  {m.id}" for m in candidates)
            )
        else:
            return f"No stream matching '{stream_id}'"

    lines = [
        f"Stream:   {manifest.id}",
        f"Title:    {manifest.title or '(untitled)'}",
        f"Status:   {manifest.status}",
    ]
    if manifest.intent:
        lines.append(f"Intent:   {manifest.intent}")
    if manifest.created:
        lines.append(f"Created:  {manifest.created}")
    if manifest.updated:
        lines.append(f"Updated:  {manifest.updated}")
    if manifest.gate_context:
        ctx_bits = [f"{k}={v}" for k, v in sorted(manifest.gate_context.items())]
        lines.append(f"Gate:     {' '.join(ctx_bits)}")
    if manifest.reply_route:
        rr = manifest.reply_route
        lines.append(
            f"Reply:    preferred={rr.get('preferred')} "
            f"selected={rr.get('selected')}"
        )
    if manifest.summary:
        lines.append("")
        lines.append("Current summary:")
        for line in manifest.summary.splitlines():
            lines.append(f"  {line}")
    if manifest.open_questions:
        lines.append("")
        lines.append("Open questions:")
        for line in manifest.open_questions.splitlines():
            lines.append(f"  {line}")

    tasks = stream_mod.read_tasks(brr_dir, stream_id)
    if tasks:
        lines.append("")
        lines.append(f"Tasks ({len(tasks)}):")
        for task in tasks[-10:]:
            task_status = _current_task_status(brr_dir, task)
            lines.append(
                f"  {task.get('task_id')} [{task_status}] "
                f"{task.get('branch')}/{task.get('env')}"
            )

    artifacts = stream_mod.read_artifacts(brr_dir, stream_id)
    if artifacts:
        lines.append("")
        lines.append(f"Artifacts ({len(artifacts)}):")
        for art in artifacts[-10:]:
            label = art.get("label") or art.get("kind", "artifact")
            lines.append(f"  {label} → {art.get('path', '')}")

    events = stream_mod.read_events(brr_dir, stream_id)
    if events:
        lines.append("")
        lines.append(f"Events ({len(events)}):")
        for ev in events[-10:]:
            kind = ev.get("type") or ev.get("source", "event")
            summary = ev.get("summary") or ev.get("event_id", "")
            lines.append(f"  {ev.get('ts', '')} {kind} {summary}".rstrip())

    return "\n".join(lines)


def _current_task_status(brr_dir: Path, stream_task: dict) -> str:
    task_id = stream_task.get("task_id")
    if task_id:
        task = Task.from_file(brr_dir / "tasks" / f"{task_id}.md")
        if task is not None:
            return task.status
    return str(stream_task.get("status", ""))
