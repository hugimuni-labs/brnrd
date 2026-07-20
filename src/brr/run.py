"""Run — one runner invocation over one or more events.

An event arrives via a gate (Telegram, Slack, Git, etc.). When the
daemon wakes a runner, it creates a ``Run`` mechanically and hands it to
the env backend for execution. Branching decisions belong to the agent
at run time; the daemon just owns env preparation and cleanup.

Run manifests live at ``.brr/runs/<run-id>/run.md`` beside the prompt,
context, and grouped-history files for the same invocation. The manifest
format mirrors event files: frontmatter + body.
"""

from __future__ import annotations

import time
import random
import string
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ENV_TYPES = ("auto", "host", "worktree", "docker", "solitary", "devcontainer", "ssh")
STATUSES = ("pending", "running", "done", "error", "conflict")
_ENV_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_EVENT_META_FIELDS = {
    "id", "body", "source", "status", "_path", "created", "branch", "env",
    "environment", "conversation_key",
}
_RUN_FIELDS = {
    "id", "event_id", "branch", "env", "environment", "status", "source",
    "conversation_key",
}


def _generate_run_id() -> str:
    """Return a sortable, human-readable run ID.

    Shape: ``run-YYMMDD-HHMM-<4 random>``. The compact UTC date+minute
    keeps IDs sortable and roughly self-documenting (you can read the
    creation time at a glance) while leaving 4 random chars to
    disambiguate same-minute runs.
    """
    stamp = time.strftime("%y%m%d-%H%M", time.gmtime())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"run-{stamp}-{rand}"


def _cfg_environment_policy(cfg: dict[str, Any]) -> str:
    return str(
        cfg.get("environment", cfg.get("env", cfg.get("default_env", "auto")))
    ).strip()


def _event_environment_policy(event: dict[str, Any], cfg: dict[str, Any]) -> str:
    return str(
        event.get(
            "environment",
            event.get("env", _cfg_environment_policy(cfg)),
        )
    ).strip()


def _docker_configured(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("docker.image") or cfg.get("docker_image"))


def resolve_env(
    env_policy: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> str:
    """Resolve an env policy into the concrete backend name.

    ``auto`` (the default) prefers Docker when configured, falls back
    to ``worktree`` otherwise. ``host`` is honoured only when explicitly
    requested — the daemon assumes isolated execution by default.
    """
    cfg = cfg or {}
    requested = (env_policy or "auto").strip()
    if not requested or requested == "auto":
        return "docker" if _docker_configured(cfg) else "worktree"
    if not _ENV_NAME_RE.match(requested):
        raise ValueError(f"invalid env: {requested!r}")
    return requested


@dataclass
class Run:
    """A runner invocation derived from daemon inbox state.

    Fields:
        id:               Unique run identifier.
        event_id:         The lead event ID for the invocation.
        body:             The lead event body / instruction for the agent.
        env:              Execution environment backend — ``host``,
                          ``worktree``, ``docker``, or a future built-in.
        status:           Lifecycle state — pending → running →
                          done / error / conflict.
        source:           The gate that produced the originating event.
        conversation_key: Stable gate-thread fingerprint, when known.
        terminal_reply:   Ephemeral copy of the terminal response. The gate
                          owns and may delete the response file as soon as the
                          event becomes deliverable; closeout consumers use
                          this copy instead of racing that delivery cleanup.
                          Deliberately excluded from the run manifest.
        meta:             Arbitrary metadata carried from the event plus
                          runtime annotations (response path, branch
                          name when finalize promotes one, trace dirs,
                          etc.).
    """

    id: str
    event_id: str
    body: str
    env: str = "worktree"
    status: str = "pending"
    source: str = ""
    conversation_key: str = ""
    terminal_reply: str | None = field(default=None, repr=False)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_event(cls, event: dict[str, Any], cfg: dict[str, Any] | None = None) -> Run:
        """Create a run from an event dict, applying config defaults.

        This is a pure mechanical conversion — no LLM call, no
        external state. The agent decides any branching at run time
        from inside its env.
        """
        cfg = cfg or {}
        run_id = _generate_run_id()
        env_policy = _event_environment_policy(event, cfg)
        return cls(
            id=run_id,
            event_id=event.get("id", ""),
            body=event.get("body", ""),
            env=resolve_env(env_policy, cfg),
            source=event.get("source", ""),
            conversation_key=str(event.get("conversation_key", "") or ""),
            meta={
                k: v for k, v in event.items()
                if k not in _EVENT_META_FIELDS
            },
        )

    # ── Persistence ─────────────────────────────────────────────────

    def to_frontmatter(self) -> str:
        """Serialize to frontmatter + body format."""
        lines = [
            "---",
            f"id: {self.id}",
            f"event_id: {self.event_id}",
            f"env: {self.env}",
            f"status: {self.status}",
            f"source: {self.source}",
        ]
        if self.conversation_key:
            lines.append(f"conversation_key: {self.conversation_key}")
        for k, v in self.meta.items():
            lines.append(f"{k}: {v}")
        lines.append("---")
        lines.append(self.body)
        return "\n".join(lines) + "\n"

    @classmethod
    def from_file(cls, path: Path) -> Run | None:
        """Load a run from a persisted manifest. Returns None on parse failure."""
        from . import protocol

        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        fm = protocol.parse_frontmatter(text)
        if not fm.get("id"):
            return None
        body = protocol.frontmatter_body(text).strip()
        meta = {k: v for k, v in fm.items() if k not in _RUN_FIELDS}
        return cls(
            id=fm["id"],
            event_id=fm.get("event_id", ""),
            body=body,
            env=fm.get("env", fm.get("environment", "worktree")),
            status=fm.get("status", "pending"),
            source=fm.get("source", ""),
            conversation_key=str(fm.get("conversation_key", "") or ""),
            meta=meta,
        )

    def save(self, runs_dir: Path) -> Path:
        """Persist this run manifest to disk. Returns the file path."""
        from . import protocol

        path = run_manifest_path(runs_dir, self.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        protocol._atomic_write(path, self.to_frontmatter())
        return path

    def update_status(self, status: str, runs_dir: Path) -> None:
        """Update status in memory and on disk."""
        self.status = status
        self.save(runs_dir)


def list_runs(runs_dir: Path, status: str | None = None) -> list[Run]:
    """List persisted runs, optionally filtered by status."""
    if not runs_dir.exists():
        return []
    runs = []
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir():
            continue
        run = Run.from_file(entry / "run.md")
        if run and (status is None or run.status == status):
            runs.append(run)
    return runs


def run_manifest_path(runs_dir: Path, run_id: str) -> Path:
    """Return the canonical manifest path for *run_id* under ``runs_dir``."""
    return runs_dir / run_id / "run.md"
