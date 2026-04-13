"""Task — the unit of work between an event and execution.

An event arrives via a gate (Telegram, Slack, Git, etc.).  A triage
step — potentially agent-assisted — converts it into a Task.  The
task carries everything the executor needs: what to do, how to branch,
where to run, and what happened.

Task files are persisted to ``.brr/tasks/`` for crash recovery and
status inspection.  The format mirrors event files: frontmatter + body.
"""

from __future__ import annotations

import time
import random
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Valid values for each field
BRANCH_STRATEGIES = ("current", "auto", "task")  # or arbitrary name / "new:<name>"
ENV_TYPES = ("local", "worktree", "docker")
STATUSES = ("pending", "running", "done", "needs_context", "error", "conflict")
_EVENT_META_FIELDS = {
    "id", "body", "source", "status", "_path", "created", "branch", "env",
}
_TASK_FIELDS = {"id", "event_id", "branch", "env", "status", "source"}


def _generate_task_id() -> str:
    ts = int(time.time())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"task-{ts}-{rand}"


@dataclass
class Task:
    """A unit of work derived from an event.

    Fields:
        id:       Unique task identifier.
        event_id: The originating event ID.
        body:     The task description / instruction for the agent.
        branch:   Branching strategy — "current", "auto", "task",
                  "<name>", or "new:<name>".
        env:      Execution environment — "local", "worktree", "docker".
        status:   Lifecycle state — pending → running → done/needs_context/error.
        source:   The gate that produced the originating event.
        meta:     Arbitrary metadata carried from the event.
    """

    id: str
    event_id: str
    body: str
    branch: str = "current"
    env: str = "local"
    status: str = "pending"
    source: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_event(cls, event: dict[str, Any], cfg: dict[str, Any] | None = None) -> Task:
        """Create a task from an event dict, applying config defaults.

        This is the mechanical conversion.  For agent-assisted triage
        (where an agent decides branch strategy, priority, etc.), the
        triage step runs first and its output feeds into this method
        or modifies the resulting Task.
        """
        cfg = cfg or {}
        task_id = _generate_task_id()
        return cls(
            id=task_id,
            event_id=event.get("id", ""),
            body=event.get("body", ""),
            branch=event.get("branch", cfg.get("default_branch", "current")),
            env=event.get("env", cfg.get("default_env", "local")),
            source=event.get("source", ""),
            meta={
                k: v for k, v in event.items()
                if k not in _EVENT_META_FIELDS
            },
        )

    @classmethod
    def from_triage_output(
        cls,
        text: str,
        event: dict[str, Any],
        cfg: dict[str, Any] | None = None,
    ) -> Task:
        """Create a task from triage-agent output plus the originating event."""
        from . import protocol

        task = cls.from_event(event, cfg)
        fm = protocol.parse_frontmatter(text)
        if not fm:
            raise ValueError("triage output is missing frontmatter")

        body = protocol.frontmatter_body(text).strip()
        if not body:
            raise ValueError("triage output is missing a task body")

        branch = str(fm.get("branch", task.branch)).strip()
        env = str(fm.get("env", task.env)).strip()
        status = str(fm.get("status", task.status)).strip()

        if not branch or branch == "new:":
            raise ValueError(f"invalid triage branch: {branch!r}")
        if env not in ENV_TYPES:
            raise ValueError(f"invalid triage env: {env!r}")
        if status not in STATUSES:
            raise ValueError(f"invalid triage status: {status!r}")

        task.body = body
        task.branch = branch
        task.env = env
        task.status = status
        task.meta.update({k: v for k, v in fm.items() if k not in _TASK_FIELDS})
        return task

    # ── Persistence ─────────────────────────────────────────────────

    def to_frontmatter(self) -> str:
        """Serialize to frontmatter + body format."""
        lines = [
            "---",
            f"id: {self.id}",
            f"event_id: {self.event_id}",
            f"branch: {self.branch}",
            f"env: {self.env}",
            f"status: {self.status}",
            f"source: {self.source}",
        ]
        for k, v in self.meta.items():
            lines.append(f"{k}: {v}")
        lines.append("---")
        lines.append(self.body)
        return "\n".join(lines) + "\n"

    @classmethod
    def from_file(cls, path: Path) -> Task | None:
        """Load a task from a persisted file. Returns None on parse failure."""
        from . import protocol

        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        fm = protocol.parse_frontmatter(text)
        if not fm.get("id"):
            return None
        body = protocol.frontmatter_body(text).strip()
        meta = {k: v for k, v in fm.items() if k not in _TASK_FIELDS}
        return cls(
            id=fm["id"],
            event_id=fm.get("event_id", ""),
            body=body,
            branch=fm.get("branch", "current"),
            env=fm.get("env", "local"),
            status=fm.get("status", "pending"),
            source=fm.get("source", ""),
            meta=meta,
        )

    def save(self, tasks_dir: Path) -> Path:
        """Persist this task to disk. Returns the file path."""
        from . import protocol

        tasks_dir.mkdir(parents=True, exist_ok=True)
        path = tasks_dir / f"{self.id}.md"
        protocol._atomic_write(path, self.to_frontmatter())
        return path

    def update_status(self, status: str, tasks_dir: Path) -> None:
        """Update status in memory and on disk."""
        self.status = status
        self.save(tasks_dir)

    # ── Branch resolution ───────────────────────────────────────────

    def resolve_branch_name(self) -> str | None:
        """Return the concrete branch name, or None for 'current'.

        Doesn't touch git — just resolves the strategy to a name.
        """
        if self.branch == "current":
            return None
        if self.branch == "auto" or self.branch == "task":
            return f"brr/{self.id}"
        if self.branch.startswith("new:"):
            return self.branch[4:]
        # Explicit branch name
        return self.branch

    # ── Convenience ─────────────────────────────────────────────────

    @property
    def needs_worktree(self) -> bool:
        return self.env == "worktree" or (
            self.branch not in ("current",) and self.env != "docker"
        )


def list_tasks(tasks_dir: Path, status: str | None = None) -> list[Task]:
    """List persisted tasks, optionally filtered by status."""
    if not tasks_dir.exists():
        return []
    tasks = []
    for entry in sorted(tasks_dir.iterdir()):
        if not entry.name.endswith(".md"):
            continue
        task = Task.from_file(entry)
        if task and (status is None or task.status == status):
            tasks.append(task)
    return tasks
