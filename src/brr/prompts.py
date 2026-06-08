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

# Soft cap on the size of the conversation-continuity block injected
# into every task prompt. Older "last N entries" cap let a single
# verbose entry blow the prompt up; bytes are what actually cost
# tokens. The entry-count cap stays as a defensive ceiling so a flood
# of one-line entries still doesn't dominate the prompt.
_MAX_LOG_ENTRIES = 10
_MAX_LOG_BYTES = 4096


def _read_recent_log(
    repo_root: Path,
    max_entries: int = _MAX_LOG_ENTRIES,
    max_bytes: int = _MAX_LOG_BYTES,
) -> str:
    """Read the most recent entries from ``kb/log.md``.

    Walks entries newest-first, including each one as long as the
    accumulated UTF-8 byte size stays at or below ``max_bytes`` and we
    haven't hit ``max_entries``. The newest entry is always included
    even if it alone exceeds the budget, so the most recent context
    never silently disappears.

    Returns the raw markdown of the included entries (oldest of the
    included set first, for natural reading order), or an empty string
    if the log is missing or has no entries.
    """
    log_path = repo_root / "kb" / "log.md"
    if not log_path.exists():
        return ""
    text = log_path.read_text(encoding="utf-8")
    parts = _LOG_ENTRY_RE.split(text)
    if len(parts) <= 1:
        return ""
    entries = [f"## [{p}".rstrip() for p in parts[1:]]
    # Walk newest → oldest, accumulate within budget.
    picked: list[str] = []
    used = 0
    sep_bytes = len(b"\n\n")
    for entry in reversed(entries):
        if len(picked) >= max_entries:
            break
        entry_bytes = len(entry.encode("utf-8"))
        projected = used + entry_bytes + (sep_bytes if picked else 0)
        if picked and projected > max_bytes:
            break
        picked.append(entry)
        used = projected
    if not picked:
        return ""
    picked.reverse()
    return "\n\n".join(picked).strip()


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


def _build_dominion_block(repo_root: Path) -> str:
    """Render the wake-time self-inject digest from the agent's dominion.

    Reads from the shared dominion worktree (``.brr/dominion/``, resolved
    via the git common dir so a per-task worktree still finds the one
    dominion). Returns ``""`` when the dominion is disabled, not yet
    materialized, or resolves to nothing — the caller drops the block.
    """
    from . import config as conf
    from . import dominion

    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return ""
    path = dominion.dominion_path(repo_root)
    if not path.is_dir():
        return ""
    budget = int(
        cfg.get(
            "dominion.inject_budget_bytes",
            cfg.get(
                "dominion_inject_budget_bytes",
                dominion.DEFAULT_INJECT_BUDGET_BYTES,
            ),
        )
    )
    digest = dominion.resolve_self_inject(path, budget_bytes=budget)
    if not digest:
        return ""
    return (
        "## Your dominion (working memory)\n\n"
        "Self-injected from `.brr/dominion/` (branch `brr-home`) per its "
        "`self-inject` index — your own durable memory, carried across "
        "wakings and yours to reshape:\n\n"
        f"{digest}"
    )


def _build_kb_health_block(repo_root: Path) -> str:
    """Render the deterministic kb-health preflight as a wake-time block.

    Runs the cheap consistency scan (:mod:`brr.kb_preflight`) plus the
    graph-stats snapshot (:mod:`brr.kb_health`) over ``kb/`` and surfaces
    any findings so the resident folds fixes into the current thought.
    Returns ``""`` when the scan is clean (a clean preflight is silent,
    not a tax on every wake) or when the inject is disabled with
    ``kb_maintenance=never`` in ``.brr/config``.

    (Earlier versions spawned a separate post-task kb-maintenance agent
    that consumed these findings; removed 2026-06-08 — the resident
    curates the shared kb as part of its own thought, with this
    deterministic signal injected on wake instead. See
    ``kb/design-agent-dominion.md`` and ``kb/subject-daemon.md``.)
    """
    from . import config as conf
    from . import kb_health, kb_preflight

    cfg = conf.load_config(repo_root)
    if str(cfg.get("kb_maintenance", "auto")).strip().lower() == "never":
        return ""
    findings = kb_preflight.scan(repo_root)
    if not findings:
        return ""
    findings_block = kb_preflight.format_findings(findings)
    stats_block = kb_health.format_graph_stats(
        kb_health.compute_graph_stats(repo_root),
    )
    body = "\n\n".join(b for b in (findings_block, stats_block) if b)
    return (
        "## kb health (deterministic preflight)\n\n"
        "The shared `kb/` has the consistency findings below. Fold fixes "
        "into your work where they fit — `kb/` is shared and governed by "
        "`AGENTS.md`; the graph stays clean when each waking leaves it no "
        "worse than it found it.\n\n"
        f"{body}"
    )


def _join_prompt_parts(
    preamble: str,
    repo_root: Path,
    trailer: str,
    *,
    reflection: bool = False,
    diffense: bool = False,
) -> str:
    """Stitch preamble, optional recent-context block, and trailer."""
    parts = [preamble]
    dominion_block = _build_dominion_block(repo_root)
    if dominion_block:
        parts.append(dominion_block)
    context = _build_context_block(repo_root)
    if context:
        parts.append(context)
    kb_health_block = _build_kb_health_block(repo_root)
    if kb_health_block:
        parts.append(kb_health_block)
    if diffense:
        pack_step = read_prompt("diffense.md", repo_root)
        if pack_step:
            parts.append(pack_step)
    if reflection:
        nudge = read_prompt("self-review.md", repo_root)
        if nudge:
            parts.append(nudge)
    parts.append(trailer)
    return "\n\n".join(parts)


def reflection_enabled(cfg: dict[str, Any] | None, owner: str = "user") -> bool:
    """Return whether to inject the skippable reflection nudge and leave it visible.

    True only for ``ergonomics=response`` on a **user-owned** run: a
    self-hoster's explicit opt-in to see their own agent's ergonomics
    notes in their own reply. Operator-owned runs never inject it (the
    "no ergonomics in a managed reply, ever" invariant), regardless of
    config.
    """
    if owner != "user":
        return False
    from .ergonomics.proxy import ergonomics_mode

    return ergonomics_mode(cfg) == "response"


def diffense_emit_enabled(cfg: dict[str, Any] | None) -> bool:
    """Return whether runner prompts should ask for a diffense review pack.

    On by default now that the consuming surface ships: the publish kernel
    projects the pack into the PR body (``diffense_create_pr_enabled``), so
    a review-worthy change produces a richer PR for free. Opt out per repo
    with ``diffense.emit_pack=false`` in ``.brr/config``. (Default was off
    through slices 1–2, before the projection consumed the pack.)
    """
    cfg = cfg or {}
    return bool(cfg.get("diffense.emit_pack", cfg.get("diffense_emit_pack", True)))


def diffense_create_pr_enabled(cfg: dict[str, Any] | None) -> bool:
    """Return whether the publish kernel should open/refresh a forge PR.

    On by default (GitHub only for now): when a run leaves a review-worthy
    pack, brr opens a PR whose body *is* the pack projection. It no-ops
    naturally when no pack was emitted, so ``diffense.emit_pack=false``
    also turns PR creation off. Opt out independently with
    ``diffense.create_pr=false`` to keep packs local (review by hand).
    """
    cfg = cfg or {}
    return bool(cfg.get("diffense.create_pr", cfg.get("diffense_create_pr", True)))


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
    source: str | None = None,
    environment: str | None = None,
    branch_name: str | None = None,
    seed_ref: str | None = None,
    branch_source: str | None = None,
    host_context_branch: str | None = None,
    runtime_dir: str | None = None,
    context_path: str | None = None,
    recent_conversation: list[dict[str, Any]] | None = None,
    event_body: str | None = None,
    reflection: bool = False,
    diffense: bool = False,
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
        source=source,
        environment=environment,
        branch_name=branch_name,
        seed_ref=seed_ref,
        branch_source=branch_source,
        host_context_branch=host_context_branch,
        runtime_dir=runtime_dir,
        context_path=context_path,
        recent_conversation=recent_conversation,
        event_body=event_body,
        diffense=diffense,
    )
    trailer = bundle.rstrip()
    if (event_body or "").strip() != task.strip():
        trailer = f"{trailer}\nTask: {task}"
    return _join_prompt_parts(
        preamble, repo_root, trailer,
        reflection=reflection, diffense=diffense,
    )


# ── Task Context Bundle internals ────────────────────────────────────

# How many prior conversation records the prompt renders. The daemon reads
# a slightly larger window from the log so that records belonging to the
# in-flight event/task (filtered out before formatting) don't starve the
# tail. Keep the daemon's read cap = RECENT_CONVERSATION_MAX + headroom.
RECENT_CONVERSATION_MAX = 8


def _build_task_context_bundle(
    *,
    event_id: str,
    response_path: str,
    repo_root: Path,
    task_id: str | None,
    source: str | None,
    environment: str | None,
    branch_name: str | None,
    seed_ref: str | None,
    branch_source: str | None,
    host_context_branch: str | None,
    runtime_dir: str | None,
    context_path: str | None,
    recent_conversation: list[dict[str, Any]] | None,
    event_body: str | None,
    diffense: bool = False,
) -> str:
    """Assemble the human-readable Task Context Bundle for the daemon prompt.

    The bundle preserves the ``Key: value`` lines (Task ID:, Execution
    root:, Current branch:, etc.) under semantic headings so any tool
    grepping the prompt keeps working.
    """
    sections: list[str] = ["---", "## Task Context Bundle"]

    sections.append("")
    sections.append("### Mode")
    sections.append("- Stage: brr daemon task")
    if source:
        sections.append(f"- Source: {source}")
    if environment:
        sections.append(f"- Environment: {environment}")
    sections.append("- Delivery: stdout captured by brr (see Delivery contract below)")
    if context_path:
        sections.append(
            f"- Runtime recovery: {context_path} "
            "(open only if a detail you need isn't in this bundle)"
        )

    sections.append("")
    sections.append("### Task")
    sections.append(f"- Event: {event_id}")
    if task_id:
        sections.append(f"- Task ID: {task_id}")
    sections.append(f"- Execution root: {repo_root}")
    if seed_ref:
        sections.append(f"- Seed ref: {seed_ref}")
    if branch_source:
        sections.append(f"- Branch source: {branch_source}")
    if host_context_branch:
        sections.append(f"- Host context branch: {host_context_branch}")
    if branch_name:
        sections.append(f"- Current branch: {branch_name}")
    if runtime_dir:
        sections.append(f"- Shared runtime dir: {runtime_dir}")
    if diffense and task_id:
        # An absolute path in the *shared* runtime dir, not a cwd-relative
        # `.brr/...`: the runner works in a worktree whose own `.brr/` is
        # torn down at finalize, so a relative pack would die before the
        # publish kernel could read it. This path is the one place the
        # daemon looks for the emitted pack.
        from . import gitops

        base = Path(runtime_dir) if runtime_dir else gitops.shared_brr_dir(repo_root)
        pack_path = base / "diffense" / task_id / "pack.json"
        sections.append(f"- Review pack path: {pack_path}")
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
        "- The user reads your reply remotely (Telegram / Slack / etc.). "
        "Refer to files by basename only — `subject-envs.md`, "
        "`run_progress.py` — never with absolute or worktree-relative "
        "paths like `/home/.../.brr/worktrees/task-.../kb/foo.md` or "
        "`.brr/worktrees/task-.../kb/foo.md`. Those paths exist on the "
        "host running brr, not on the user's machine, and chat clients "
        "won't render or link them. brr already appends a "
        "forge-hosted branch URL to the response card when one is "
        "available; you don't need to fabricate a link."
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
        sections.append(
            f"- You start on `{branch_name}`, sprouted from `{seed_ref}`. "
            "Commit here by default; brr publishes whichever branch you "
            "end on after the run."
        )
        if branch_name.startswith("brr/"):
            sections.append(
                f"- The placeholder branch name `{branch_name}` is opaque on "
                "a forge branch list. If your work has a clear theme — a "
                "feature, a fix, a refactor — rename the branch before "
                "committing to something descriptive like "
                "`brr/<short-slug>` (e.g. `brr/remove-status-module`, "
                "`brr/forge-url-inference`). Keep the `brr/` prefix so the "
                "branch is recognisable as brr-originated. Read-only, "
                "research, or pure-discussion tasks can keep the "
                "placeholder name."
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
    for record in records[-RECENT_CONVERSATION_MAX:]:
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
                record.get("publish_branch")
                or record.get("target_branch")
                or record.get("expected_publish_branch")  # compat: old records
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
