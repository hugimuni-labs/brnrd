"""Prompts — assemble the text we hand to runner CLIs.

`brr` ships a handful of prompt templates under ``src/brr/prompts/``
and adopters can override them via ``.brr/prompts/<name>.md``.  This
module knows how to:

- read a template (with override support);
- inject conversation continuity from ``kb/log.md``;
- assemble the daemon-task **Task Context Bundle** (delivery contract,
  branch/runtime metadata, recent conversation, original event body).

It does *not* shell out — that's :mod:`brr.runner`'s job. Keeping the
assembly here means the agent-facing surface evolves independently of
subprocess plumbing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_AGENTS_PATH = Path(__file__).resolve().parent / "AGENTS.md"


# ── Template I/O ─────────────────────────────────────────────────────


def read_prompt(name: str, repo_root: Path | None = None) -> str:
    """Return a prompt template, preferring a per-repo override.

    Order: ``<repo>/.brr/prompts/<name>`` then the bundled
    ``src/brr/prompts/<name>``.  Returns ``""`` when neither exists so
    callers can detect a missing template without a ``try/except``.
    """
    if repo_root:
        from . import gitops

        override = gitops.shared_brr_dir(repo_root) / "prompts" / name
        if override.exists():
            return override.read_text(encoding="utf-8")
    bundled = _PROMPTS_DIR / name
    if bundled.exists():
        return bundled.read_text(encoding="utf-8")
    return ""


# ── Context injection ────────────────────────────────────────────────

_LOG_ENTRY_RE = re.compile(r"^## \[", re.MULTILINE)
_MAX_LOG_ENTRIES = 10


def _read_recent_log(repo_root: Path, max_entries: int = _MAX_LOG_ENTRIES) -> str:
    """Read the most recent entries from ``kb/log.md``.

    Returns the raw markdown of the last *max_entries* entries, or an
    empty string if the log is missing/empty. This is conversation
    continuity for the agent — bounded so the prompt doesn't grow
    unbounded over a long-lived repo.
    """
    log_path = repo_root / "kb" / "log.md"
    if not log_path.exists():
        return ""
    text = log_path.read_text(encoding="utf-8")
    parts = _LOG_ENTRY_RE.split(text)
    if len(parts) <= 1:
        return ""
    entries = [f"## [{p}" for p in parts[1:]]
    recent = entries[-max_entries:]
    return "\n".join(recent).strip()


def _build_context_block(repo_root: Path) -> str:
    """Render recent log entries as the conversation context block.

    The log is curated by agents (per ``AGENTS.md``) so the block stays
    proportional. Returns ``""`` when the log is empty or missing —
    the caller drops the block entirely in that case.
    """
    recent = _read_recent_log(repo_root)
    if not recent:
        return ""
    return (
        "## Recent Activity (from kb/log.md)\n\n"
        "This is your conversation context — what happened in previous sessions:\n\n"
        f"{recent}"
    )


def _join_prompt_parts(
    preamble: str,
    repo_root: Path,
    trailer: str,
) -> str:
    """Stitch preamble, optional recent-context block, and trailer."""
    parts = [preamble]
    context = _build_context_block(repo_root)
    if context:
        parts.append(context)
    parts.append(trailer)
    return "\n\n".join(parts)


# ── Top-level builders ───────────────────────────────────────────────


def build_init_prompt(repo_root: Path) -> str:
    """Build the prompt for ``brr init`` — setup.md + bundled AGENTS.md.

    brr's own ``AGENTS.md`` (bundled inside the package) is the model
    adopters' setup agent uses. Universal sections copy verbatim;
    project-specific sections (Project, Build and run, Code guidelines,
    Constraints) get rewritten for the adopter's repo.
    """
    setup = read_prompt("setup.md", repo_root)
    template = _AGENTS_PATH.read_text(encoding="utf-8") if _AGENTS_PATH.exists() else ""
    return f"{setup}\n\n{template}"


def build_run_prompt(task: str, repo_root: Path) -> str:
    """Build the prompt for ``brr run`` — run.md + recent context + task."""
    preamble = read_prompt("run.md", repo_root)
    return _join_prompt_parts(preamble, repo_root, f"---\nTask: {task}")


def build_daemon_prompt(
    task: str,
    event_id: str,
    response_path: str,
    repo_root: Path,
    *,
    task_id: str | None = None,
    branch_name: str | None = None,
    base_branch: str | None = None,
    seed_ref: str | None = None,
    auto_land_branch: str | None = None,
    branch_authority: str | None = None,
    host_context_branch: str | None = None,
    runtime_dir: str | None = None,
    context_path: str | None = None,
    recent_conversation: list[dict[str, Any]] | None = None,
    event_body: str | None = None,
) -> str:
    """Build the prompt for daemon-originated tasks.

    Same as the run prompt but with event metadata, recent conversation
    context, and an explicit delivery contract assembled into a single
    ``Task Context Bundle``.
    """
    preamble = read_prompt("run.md", repo_root)
    bundle = _build_task_context_bundle(
        event_id=event_id,
        response_path=response_path,
        repo_root=repo_root,
        task_id=task_id,
        branch_name=branch_name,
        base_branch=base_branch,
        seed_ref=seed_ref,
        auto_land_branch=auto_land_branch,
        branch_authority=branch_authority,
        host_context_branch=host_context_branch,
        runtime_dir=runtime_dir,
        context_path=context_path,
        recent_conversation=recent_conversation,
        event_body=event_body,
    )
    trailer = bundle.rstrip()
    if (event_body or "").strip() != task.strip():
        trailer = f"{trailer}\nTask: {task}"
    return _join_prompt_parts(preamble, repo_root, trailer)


def build_kb_maintenance_prompt(repo_root: Path) -> str:
    """Return the post-task KB consistency-check prompt (or empty)."""
    return read_prompt("kb-maintenance.md", repo_root)


# ── Task Context Bundle internals ────────────────────────────────────

_RECENT_CONVERSATION_MAX = 8


def _build_task_context_bundle(
    *,
    event_id: str,
    response_path: str,
    repo_root: Path,
    task_id: str | None,
    branch_name: str | None,
    base_branch: str | None,
    seed_ref: str | None,
    auto_land_branch: str | None,
    branch_authority: str | None,
    host_context_branch: str | None,
    runtime_dir: str | None,
    context_path: str | None,
    recent_conversation: list[dict[str, Any]] | None,
    event_body: str | None,
) -> str:
    """Assemble the human-readable Task Context Bundle for the daemon prompt.

    The bundle preserves the ``Key: value`` lines (Task ID:, Execution
    root:, Current branch:, etc.) under semantic headings so any tool
    grepping the prompt keeps working.
    """
    sections: list[str] = ["---", "## Task Context Bundle"]

    sections.append("")
    sections.append("### Task")
    sections.append(f"- Event: {event_id}")
    if task_id:
        sections.append(f"- Task ID: {task_id}")
    sections.append(f"- Execution root: {repo_root}")
    if seed_ref:
        sections.append(f"- Seed ref: {seed_ref}")
    if auto_land_branch:
        sections.append(f"- Auto-land branch: {auto_land_branch}")
    elif seed_ref:
        sections.append("- Auto-land branch: none (preserve task branch)")
    if branch_authority:
        sections.append(f"- Branch authority: {branch_authority}")
    if host_context_branch:
        sections.append(f"- Host context branch: {host_context_branch}")
    elif base_branch:
        sections.append(f"- Base branch: {base_branch}")
    if branch_name:
        sections.append(f"- Current branch: {branch_name}")
    if runtime_dir:
        sections.append(f"- Shared runtime dir: {runtime_dir}")
    if context_path:
        sections.append(f"- Run context file: {context_path}")

    sections.append("")
    sections.append("### Delivery contract")
    sections.append(
        "- Your stdout is the user's chat reply. Print the exact intended "
        "content as your final stdout message — no preamble, no meta "
        "acknowledgment, no commentary outside it. Stream progress, debug, "
        "and tool output to stderr."
    )
    sections.append(
        f"- brr captures stdout and stores it at {response_path}. Don't "
        "write that file yourself, and don't substitute a file path for "
        "the answer."
    )
    sections.append(
        "- If you wrote files (kb pages, code, fixtures, anything), commit "
        "them on the current branch. The diff is the receipt that the work "
        "happened — without a commit, the work disappears."
    )
    sections.append(
        "- Don't explore or modify any other files in .brr/ beyond what "
        "this task explicitly asks for."
    )
    if branch_name and seed_ref:
        if auto_land_branch:
            sections.append(
                f"- You start on `{branch_name}`, sprouted from `{seed_ref}`. "
                f"Because `{auto_land_branch}` is the resolved auto-land "
                "target, committing on the current branch lets brr "
                "fast-forward that target after the run. If the task body "
                "clearly belongs somewhere else, switch to that branch before "
                "editing; brr will preserve the branch you end up on."
            )
        else:
            sections.append(
                f"- You start on `{branch_name}`, sprouted from `{seed_ref}`. "
                "No safe auto-land target was resolved, so commit on the "
                "current task branch by default; brr will preserve that branch "
                "for human routing and publish it when a remote is configured. "
                "If the task body names a different branch, switch to it before "
                "editing."
            )
    elif branch_name and base_branch:
        sections.append(
            f"- You start on `{branch_name}`, sprouted from `{base_branch}`. "
            "If your work should land on the base branch, commit on the "
            "current branch and brr will fast-forward it back. If you want "
            "the work kept as a separate branch, run "
            "`git switch -c <meaningful-name>` first; brr will preserve "
            "whatever branch you end up on without merging."
        )

    recent_block = _format_recent_conversation(recent_conversation)
    if recent_block:
        sections.append("")
        sections.append("### Recent in this conversation")
        sections.append("")
        sections.append(recent_block)

    if event_body is not None:
        body = event_body.strip()
        if body:
            sections.append("")
            sections.append("### Original event body")
            sections.append("")
            sections.append(body)

    sections.append("")
    return "\n".join(sections) + "\n"


def _format_recent_conversation(
    records: list[dict[str, Any]] | None,
) -> str:
    """Render the last few conversation records as human-readable bullets.

    Callers pass only prior records; the current event body is rendered
    separately in the Task Context Bundle. Returns an empty string when
    nothing useful is available.
    """
    if not records:
        return ""
    bullets: list[str] = []
    for record in records[-_RECENT_CONVERSATION_MAX:]:
        kind = record.get("kind")
        ts = record.get("ts", "")
        line: str | None = None
        if kind == "event":
            summary = (record.get("summary") or "").strip()
            source = record.get("source") or ""
            line = f"- {ts} event ({source}): {summary}".rstrip()
        elif kind == "task":
            tid = record.get("task_id", "")
            status = record.get("status") or "pending"
            branch = (
                record.get("changed_branch")
                or record.get("auto_land_branch")
                or record.get("branch_name")
                or ""
            )
            line = f"- {ts} task {tid} status={status} branch={branch}"
        elif kind == "update":
            ptype = record.get("type") or ""
            tid = record.get("task_id") or ""
            stage = record.get("stage") or ""
            err = record.get("error") or ""
            bits = [f"- {ts} update {ptype}"]
            if tid:
                bits.append(f"task={tid}")
            if stage:
                bits.append(f"stage={stage}")
            if err:
                bits.append(f"error={err}")
            line = " ".join(bits)
        elif kind == "artifact":
            label = record.get("label") or record.get("artifact_kind") or ""
            path = record.get("path") or ""
            line = f"- {ts} artifact {label} {path}".rstrip()
        if line:
            bullets.append(line)
    return "\n".join(bullets)
