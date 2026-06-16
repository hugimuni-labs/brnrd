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
        "From `kb/log.md` — the shared, curated through-line of what's been "
        "done and learned. brr injects this recent tail every wake; it's what "
        "your continuity across thoughts (and other hands) rests on, and what "
        "earlier wakings chose to hand forward:\n\n"
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
    sync_note = ""
    diverged = dominion.needs_sync(path.parent)
    if diverged:
        sync_note = (
            "\n\n**Your dominion's remote has diverged** — brr's last push of "
            "`brr-home` was rejected, so another machine or session wrote it "
            "too. brr commits locally so nothing is lost, but reconciling the "
            "remote is yours (it's a merge — judgement, not a reflex): when "
            f"you're the one awake, in `{path}` fetch, merge / resolve any "
            "conflicts, and push. "
            f"(Reason on record: {diverged})"
        )
    return (
        "## Your dominion (working memory)\n\n"
        f"Your dominion is the `brr-home` branch, checked out at `{path}` — "
        "an absolute path, reachable from any working directory (your task "
        "may run in a worktree or container whose cwd is elsewhere). It's "
        "your durable memory: write notes, pain records, and your "
        "`self-inject` index there freely, and **commit what you mean to "
        "keep** — the diff is the receipt your next wake reads from. brr "
        "best-effort pushes `brr-home` after a thought so your memory reaches "
        "the remote; what it *won't* do is reconcile a **diverged** remote "
        "(another machine or session wrote `brr-home` too) — fetch / merge / "
        "resolve / push is yours to own, a merge is judgement not reflex, and "
        "you'll see a note here when it's needed."
        f"{sync_note}\n\n"
        "Self-injected below per your `self-inject` index — yours to "
        "reshape:\n\n"
        f"{digest}"
    )


def _build_pitfalls_block(repo_root: Path, task_text: str) -> str:
    """Render dominion pitfalls whose triggers fire for *task_text*.

    The affordance surface of the env-shaping loop: failure-memory the
    resident recorded in ``.brr/dominion/pitfalls.md``, injected only when
    a trigger appears in the task at hand (see
    ``kb/design-environment-shaping.md`` and ``pitfalls.py``). Returns
    ``""`` when the dominion is disabled / absent, or nothing matches.
    """
    if not task_text:
        return ""
    from . import config as conf
    from . import dominion, pitfalls

    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return ""
    path = dominion.dominion_path(repo_root)
    if not path.is_dir():
        return ""
    matched = pitfalls.match(pitfalls.parse_pitfalls(path), task_text)
    return pitfalls.format_block(matched)


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


def _build_introspection_block(repo_root: Path) -> str:
    """Render the introspection/development invitation when toggled on.

    An opt-in, co-development stance (``introspect.enabled`` in
    ``.brr/config``, **default off**): it invites the resident to turn its
    attention on the *shape of its own injected context* — the
    orientation, dominion + playbook, pitfalls, recent thread, and task
    bundle assembled into this wake — perceive how the whole connects,
    find the seams / contradictions / dead guardrails / unstated
    assumptions, and raise them with the user as a turn in the
    conversation about how the context should evolve.

    Off by default because it's an active-development aid, not a
    production wake stance (it spends tokens and attention every wake).
    The text lives in ``prompts/introspection.md`` so the tone can be
    iterated on and per-repo overridden; see
    ``kb/design-context-introspection.md``. Returns ``""`` when the toggle
    is off or the template is missing — the caller drops the block.
    """
    from . import config as conf

    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("introspect.enabled", cfg.get("introspect_enabled", False))):
        return ""
    return read_prompt("introspection.md", repo_root).strip()


def _build_injected_blocks(
    repo_root: Path, *, task_text: str | None = None
) -> list[str]:
    """The standing, always-on context blocks brr injects into every wake.

    Returns the *base* blocks: dominion digest (playbook + ``self-inject``),
    pitfalls matching the task, recent-activity log tail, and kb health note.
    These are the blocks that appear regardless of mode toggles.

    Shared by ``_join_prompt_parts`` and ``build_injected_context``; whatever
    block is added here surfaces in both paths with no drift.  Mode-toggle
    blocks (diffense, introspection) sit on top of these; they are added by
    ``_join_prompt_parts`` (for the full runner prompt) and by
    ``build_injected_context`` (for the faithful inject-tool view).
    """
    blocks: list[str] = []
    dominion_block = _build_dominion_block(repo_root)
    if dominion_block:
        blocks.append(dominion_block)
    if task_text:
        pitfalls_block = _build_pitfalls_block(repo_root, task_text)
        if pitfalls_block:
            blocks.append(pitfalls_block)
    context = _build_context_block(repo_root)
    if context:
        blocks.append(context)
    kb_health_block = _build_kb_health_block(repo_root)
    if kb_health_block:
        blocks.append(kb_health_block)
    return blocks


def build_injected_context(repo_root: Path, *, task_text: str | None = None) -> str:
    """brr's assembled wake-context, for ``brr agent inject`` and agent wrappers.

    Returns the **full** injected context a daemon task wake receives: the
    base blocks (dominion digest, pitfalls, recent-activity log, kb health)
    **plus** the mode-toggle blocks (diffense review-pack prompt,
    introspection invitation) when their config toggles are on.  The result
    mirrors what ``_join_prompt_parts`` embeds minus the preamble (AGENTS.md
    / runner template) and the trailing task bundle, giving a faithful
    "what did this wake see?" answer via ``brr agent inject``.

    ``task_text`` lets the caller pull in pitfalls whose triggers match the
    work at hand.

    Wrappers that want *only* the base blocks (e.g. ``build_run_prompt`` for
    ad-hoc tasks, or test helpers asserting block content) call
    ``_build_injected_blocks`` directly.
    """
    from . import config as conf

    cfg = conf.load_config(repo_root)
    parts = list(_build_injected_blocks(repo_root, task_text=task_text))
    if diffense_emit_enabled(cfg):
        pack_step = read_prompt("diffense.md", repo_root)
        if pack_step:
            parts.append(pack_step)  # keep as-is to match _join_prompt_parts
    introspection = _build_introspection_block(repo_root)
    if introspection:
        parts.append(introspection)
    return "\n\n".join(parts)


def _join_prompt_parts(
    preamble: str,
    repo_root: Path,
    trailer: str,
    *,
    task_text: str | None = None,
    diffense: bool = False,
) -> str:
    """Stitch preamble, optional recent-context block, and trailer."""
    parts = [preamble]
    parts.extend(_build_injected_blocks(repo_root, task_text=task_text))
    if diffense:
        pack_step = read_prompt("diffense.md", repo_root)
        if pack_step:
            parts.append(pack_step)
    # Last framing before the task: invite the resident to look at the whole
    # shape it has just read (opt-in dev mode). Placed here so it can refer to
    # everything above and sit fresh against the task bundle.
    introspection_block = _build_introspection_block(repo_root)
    if introspection_block:
        parts.append(introspection_block)
    parts.append(trailer)
    return "\n\n".join(parts)


def diffense_emit_enabled(cfg: dict[str, Any] | None) -> bool:
    """Return whether runner prompts should ask for a diffense review pack.

    On by default now that the consuming surface ships: the resident
    projects the pack into the PR body and sends it through the forge gate,
    so a review-worthy change can produce a richer PR. Opt out per repo
    with ``diffense.emit_pack=false`` in ``.brr/config``. (Default was off
    through slices 1–2, before the projection consumed the pack.)
    """
    cfg = cfg or {}
    return bool(cfg.get("diffense.emit_pack", cfg.get("diffense_emit_pack", True)))


def diffense_create_pr_enabled(cfg: dict[str, Any] | None) -> bool:
    """Return whether the GitHub delivery gate should open/refresh a PR.

    On by default (GitHub only for now): when the resident sends a checked
    diffense projection through ``gate: forge``, the GitHub gate opens or
    refreshes the PR. Opt out independently with ``diffense.create_pr=false``
    to keep packs local (review by hand).
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
    return _join_prompt_parts(
        preamble, repo_root, f"---\nTask: {task}", task_text=task,
    )


def build_daemon_prompt(
    task: str,
    event_id: str,
    response_path: str,
    repo_root: Path,
    *,
    outbox_path: str | None = None,
    task_id: str | None = None,
    source: str | None = None,
    environment: str | None = None,
    branch_name: str | None = None,
    seed_ref: str | None = None,
    branch_source: str | None = None,
    branch_setup_notice: str | None = None,
    host_context_branch: str | None = None,
    runtime_dir: str | None = None,
    context_path: str | None = None,
    recent_conversation: list[dict[str, Any]] | None = None,
    communication_snapshot: dict[str, Any] | None = None,
    pending_events: list[dict[str, Any]] | None = None,
    present: list[dict[str, Any]] | None = None,
    event_body: str | None = None,
    budget_seconds: int | None = None,
    diffense: bool = False,
) -> str:
    """Build the prompt for daemon-originated tasks.

    Same as the run prompt but with event metadata, recent conversation
    context, and an explicit delivery contract assembled into a single
    ``Task Context Bundle``.

    The daemon path also injects ``daemon-substrate.md`` — brr's driver's
    manual for the daemon-specific machinery (single-flight, capture net,
    self-scheduled wakes, the outbox/keepalive contract) that the
    host-agnostic playbook deliberately leaves out. ``brr run`` skips it:
    a one-shot has no daemon to fire schedules or drain an outbox.
    """
    preamble = read_prompt("run.md", repo_root)
    substrate = read_prompt("daemon-substrate.md", repo_root)
    if substrate.strip():
        preamble = f"{preamble.rstrip()}\n\n{substrate.strip()}"
    bundle = _build_task_context_bundle(
        event_id=event_id,
        response_path=response_path,
        outbox_path=outbox_path,
        budget_seconds=budget_seconds,
        repo_root=repo_root,
        task_id=task_id,
        source=source,
        environment=environment,
        branch_name=branch_name,
        seed_ref=seed_ref,
        branch_source=branch_source,
        branch_setup_notice=branch_setup_notice,
        host_context_branch=host_context_branch,
        runtime_dir=runtime_dir,
        context_path=context_path,
        recent_conversation=recent_conversation,
        communication_snapshot=communication_snapshot,
        pending_events=pending_events,
        present=present,
        event_body=event_body,
        diffense=diffense,
    )
    trailer = bundle.rstrip()
    if (event_body or "").strip() != task.strip():
        trailer = f"{trailer}\nTask: {task}"
    # Match pitfalls against the task and the original event text — the
    # triggers the resident recorded tend to echo how a request is phrased.
    pitfall_text = "\n".join(t for t in (task, event_body) if t)
    return _join_prompt_parts(
        preamble, repo_root, trailer, task_text=pitfall_text, diffense=diffense,
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
    outbox_path: str | None = None,
    budget_seconds: int | None = None,
    repo_root: Path,
    task_id: str | None,
    source: str | None,
    environment: str | None,
    branch_name: str | None,
    seed_ref: str | None,
    branch_source: str | None,
    branch_setup_notice: str | None,
    host_context_branch: str | None,
    runtime_dir: str | None,
    context_path: str | None,
    recent_conversation: list[dict[str, Any]] | None,
    communication_snapshot: dict[str, Any] | None = None,
    pending_events: list[dict[str, Any]] | None = None,
    present: list[dict[str, Any]] | None = None,
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
    sections.append(
        "_From the brr daemon: the runtime facts for *this* thought — task "
        "metadata, environment, and the delivery contract. Operational and "
        "per-thought, not durable memory (that's your dominion)._"
    )

    sections.append("")
    sections.append("### Mode")
    sections.append("- Stage: brr daemon task")
    if source:
        sections.append(f"- Source: {source}")
    if environment:
        sections.append(f"- Environment: {environment}")
    sections.append("- Delivery: stdout captured by brr (see Delivery contract below)")
    if budget_seconds:
        sections.append(
            f"- Budget: ~{budget_seconds // 60}m of wall-clock runtime before "
            "brr kills this thought to reclaim the single-flight slot. Bound "
            "uncertain long-running commands yourself (own timeout, or "
            "background + poll); extend the deadline if you genuinely need "
            "longer (see Delivery contract)."
        )
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
    if branch_setup_notice:
        sections.append(f"- Branch setup: {branch_setup_notice}")
    if runtime_dir:
        sections.append(f"- Shared runtime dir: {runtime_dir}")
    if diffense and task_id:
        # An absolute path in the *shared* runtime dir, not a cwd-relative
        # `.brr/...`: the runner works in a worktree whose own `.brr/` is
        # torn down at finalize, so a relative pack would die before the
        # resident can validate, project, and publish it through a forge
        # gate send.
        from . import gitops

        base = Path(runtime_dir) if runtime_dir else gitops.shared_brr_dir(repo_root)
        pack_path = base / "diffense" / task_id / "pack.json"
        sections.append(f"- Review pack path: {pack_path}")
    if context_path:
        sections.append(f"- Run context file: {context_path}")

    sections.append("")
    sections.append("### Delivery contract")
    sections.append(
        "- Stdout is the default terminal reply for the current thread. "
        "Print the exact intended content as your final stdout message — "
        "no preamble, no meta acknowledgment, no commentary outside it. "
        "Stream progress, debug, and tool output to stderr."
    )
    sections.append(
        f"- brr captures stdout and stores it at {response_path}. Don't "
        "write that file yourself, and don't substitute a file path for "
        "the answer. If the runner fails or stays silent on an addressed "
        "event, brr sends an explicit failure note instead of dropping the "
        "thread."
    )
    if outbox_path:
        sections.append(
            "- You can also send the user a reply *mid-thought* — before a "
            "long stretch, to share trajectory, or to answer a quick thing "
            "right away — by writing a markdown file into your outbox "
            f"directory `{outbox_path}`. brr delivers each file as its own "
            "chat message, in order, while you keep working. A wake may "
            "produce zero, one, or many deliveries; for an addressed event, "
            "make sure the current thread receives either stdout, an outbox "
            "reply, or an honest noop/failure explanation. One file is one "
            "message; write the complete reply (stage as `*.tmp` and rename "
            "if you want an atomic write). This is optional: a single final "
            "stdout is still a complete, healthy run."
        )
        sections.append(
            "- To answer a *different* pending event inline (see Inbox "
            "below), start the outbox file with a frontmatter `event: <id>` "
            "naming that event. brr delivers it to that event's thread and "
            "marks the event handled, so it won't wake again. Use one "
            "complete reply per folded-in event; prefer letting anything "
            "that wants its own branch wake as its own thought."
        )
        sections.append(
            "- brr also refreshes a live inbox view at "
            f"`{outbox_path}/inbox.json` on each heartbeat. At natural "
            "plan / todo boundaries, re-read it before deciding whether to "
            "continue, fold in a quick event via `event: <id>`, or leave "
            "waiting work for its own wake. This file is daemon-owned "
            "control state, not an outbox message to edit or remove."
        )
        sections.append(
            "- To send a message to a destination with *no* waiting event "
            "— ping a chat, post an out-of-bound note, deliver from a "
            "scheduled thought — start the outbox file with a frontmatter "
            "`gate: <name>` (e.g. `gate: telegram`) plus any target fields "
            "that gate needs (omit them to use its configured default). The "
            "body is the message; brr delivers it once to that destination. "
            "It's a send, not a reply to this thread, and an unconfigured "
            "gate is dropped. For forge publishing, `gate: forge` uses the "
            "GitHub gate and expects `head`, `base`, and `title` frontmatter; "
            "the body is the pull-request body."
        )
        if budget_seconds:
            sections.append(
                "- Running something that will outlast your budget? Don't get "
                f"killed mid-run: write `{outbox_path}/.keepalive` whose first "
                "line is either an ISO-8601 time (\"busy until T\") or "
                "`+<duration>` like `+30m` (\"busy this much longer\", measured "
                "from when you write it). brr honours it on its next heartbeat "
                "and holds the slot until then; rewrite it to extend again. "
                "It's a control file, not a message — brr never delivers it."
            )
        sections.append(
            "- Want to narrate what the live progress card says? Write a "
            f"line or two into `{outbox_path}/.card` and the gate's card "
            "re-renders with your text as a `note:` line under the live "
            "phase. Rewrite it as your context shifts; deleting or "
            "emptying the file withdraws the note. The daemon still owns "
            "the lifecycle scaffolding (header, sync line, phase log, "
            "terminal state) — this is the seam where the resident gets to "
            "say what's actually happening, not just which packet last "
            "fired. It's a control file, not a message — brr never "
            "delivers it as a chat reply."
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

    inbox_block = _format_pending_events(pending_events)
    if inbox_block:
        sections.append("")
        sections.append("### Inbox — other pending events")
        sections.append(
            "Other events were waiting when you woke. You can fold a quick, "
            "related one in now (answer it via the outbox `event: <id>` "
            "contract above) instead of leaving it for its own spawn — your "
            "call. For the current list, read the live `inbox.json` in your "
            "outbox at plan / todo boundaries."
        )
        sections.append("")
        sections.append(inbox_block)

    presence_block = _format_presence(present)
    if presence_block:
        sections.append("")
        sections.append("### Also awake right now")
        sections.append(
            "Other thoughts are active in this repo (ad-hoc sessions, or "
            "another worker). You share one dominion, so if one is on the "
            "same stream or files, expect its edits to land alongside yours "
            "— don't fight it. Contradictions in shared memory are normal "
            "and get reconciled by judgement, not locks (see your playbook)."
        )
        sections.append("")
        sections.append(presence_block)

    snapshot_block = _format_communication_snapshot(communication_snapshot)
    if snapshot_block:
        sections.append("")
        sections.append("### Communication snapshot")
        sections.append("")
        sections.append(snapshot_block)
    else:
        recent_block = _format_recent_conversation(recent_conversation)
        if recent_block:
            sections.append("")
            sections.append("### Recent in this conversation")
            sections.append("")
            sections.append(recent_block)

    thread_record_block = _format_thread_of_record(repo_root)
    if thread_record_block:
        sections.append("")
        sections.append("### Thread of record")
        sections.append("")
        sections.append(thread_record_block)

    if event_body is not None:
        body = event_body.strip()
        if body:
            sections.append("")
            sections.append("### Original event body")
            sections.append("")
            sections.append(body)

    sections.append("")
    return "\n".join(sections) + "\n"


def _format_pending_events(
    events: list[dict[str, Any]] | None,
) -> str:
    """Render other pending inbox events as bullets for the bundle.

    Each entry shows the event id (the handle the resident names in the
    outbox ``event:`` frontmatter to fold it in), its source, and a
    one-line summary. Returns an empty string when nothing is waiting.
    """
    if not events:
        return ""
    bullets: list[str] = []
    for ev in events:
        eid = str(ev.get("id") or "").strip()
        if not eid:
            continue
        source = str(ev.get("source") or "").strip()
        summary = " ".join(str(ev.get("summary") or "").split())
        if len(summary) > 140:
            summary = summary[:137].rstrip() + "..."
        src = f" ({source})" if source else ""
        sep = f": {summary}" if summary else ""
        bullets.append(f"- {eid}{src}{sep}")
    return "\n".join(bullets)


def _format_presence(
    entries: list[dict[str, Any]] | None,
) -> str:
    """Render other active thoughts (the presence registry) as bullets.

    Each entry shows the participant kind and the stream it's on, so the
    resident can tell whether another thought might touch the same work.
    Returns an empty string when nobody else is awake — the common case
    under single-flight, so the section drops out entirely.
    """
    if not entries:
        return ""
    bullets: list[str] = []
    for e in entries:
        kind = str(e.get("kind") or "thought").strip()
        stream = str(e.get("stream") or "").strip()
        tid = str(e.get("task_id") or "").strip()
        where = f" on `{stream}`" if stream else ""
        tag = f" (task {tid})" if tid else ""
        bullets.append(f"- {kind}{where}{tag}")
    return "\n".join(bullets)


def _format_communication_snapshot(
    snapshot: dict[str, Any] | None,
) -> str:
    """Render the curated cross-channel wake snapshot.

    This is the prompt-facing tier in the co-maintainer continuity model:
    compact enough to ride every wake, with untruncated grouped history
    one file read away when the resident needs more.
    """
    if not snapshot:
        return ""
    lines: list[str] = []
    current = str(snapshot.get("current_thread") or "").strip()
    if current:
        lines.append(f"- Current thread: `{current}`")
    correspondent = str(snapshot.get("correspondent_key") or "").strip()
    if correspondent:
        lines.append(f"- Correspondent: `{correspondent}`")

    failure = snapshot.get("prior_failure")
    if isinstance(failure, dict) and failure:
        lines.append(_format_prior_failure(failure))

    related = snapshot.get("related_threads")
    if isinstance(related, list) and related:
        lines.append("- Related input threads:")
        for thread in related:
            if not isinstance(thread, dict):
                continue
            key = str(thread.get("conversation_key") or "").strip()
            if not key:
                continue
            source = str(thread.get("source") or "").strip()
            kind = str(thread.get("kind") or "").replace("_", " ").strip()
            records = thread.get("record_count", 0)
            dialogue = thread.get("dialogue_count", 0)
            latest = str(thread.get("latest_ts") or "").strip()
            detail = f"{dialogue} dialogue / {records} records"
            if source:
                detail = f"{source}; {detail}"
            if kind:
                detail = f"{kind}; {detail}"
            if latest:
                detail = f"{detail}; latest {latest}"
            lines.append(f"  - `{key}` ({detail})")

    groups = snapshot.get("history_groups")
    if isinstance(groups, list) and groups:
        lines.append("- On-demand grouped history:")
        for group in groups:
            if not isinstance(group, dict):
                continue
            label = str(group.get("label") or group.get("id") or "").strip()
            path = str(group.get("path") or "").strip()
            if not label or not path:
                continue
            count = group.get("record_count", 0)
            lines.append(f"  - {label}: `{path}` ({count} records)")
        lines.append(
            "  Read these JSONL files only when the snapshot is too thin; "
            "they are untruncated runtime records grouped by gate/forge "
            "thread."
        )

    forge_block = _format_forge_state(snapshot.get("forge"))
    if forge_block:
        if lines:
            lines.append("")
        lines.append(forge_block)

    turns = _format_recent_conversation(snapshot.get("recent_turns"))
    if turns:
        if lines:
            lines.append("")
        lines.append("Recent turns (woven, oldest first):")
        lines.append(turns)
    return "\n".join(lines)


def _format_forge_state(forge: Any) -> str:
    """Render the forge-state facet: in-flight worktrees + issues/PRs in play.

    Network-free local picture (co-maintainer §5): the resident's worktrees
    and unpushed work, and the GitHub threads its conversations are about.
    Returns an empty string when the facet is absent or empty.
    """
    if not isinstance(forge, dict) or not forge:
        return ""
    lines: list[str] = ["Forge state (local, network-free):"]

    worktrees = forge.get("worktrees")
    if isinstance(worktrees, list) and worktrees:
        lines.append("- Worktrees / branches:")
        for wt in worktrees:
            if not isinstance(wt, dict):
                continue
            branch = str(wt.get("branch") or "").strip() or "(detached)"
            tid = str(wt.get("task_id") or "").strip()
            bits: list[str] = []
            unpushed = wt.get("unpushed", 0)
            if isinstance(unpushed, int) and unpushed > 0:
                bits.append(f"{unpushed} unpushed")
            if wt.get("dirty"):
                bits.append("uncommitted changes")
            if wt.get("current"):
                bits.append("this run")
            url = str(wt.get("branch_url") or "").strip()
            detail = f" ({'; '.join(bits)})" if bits else ""
            tag = f" [{tid}]" if tid else ""
            link = f" — {url}" if url else ""
            lines.append(f"  - `{branch}`{tag}{detail}{link}")

    threads = forge.get("threads")
    if isinstance(threads, list) and threads:
        lines.append("- Issues / PRs in play:")
        for th in threads:
            if not isinstance(th, dict):
                continue
            repo = str(th.get("repo") or "").strip()
            number = th.get("number")
            ref = f"{repo}#{number}" if repo and number is not None else ""
            if not ref:
                continue
            bits = []
            kind = str(th.get("kind") or "").strip()
            if kind:
                bits.append(kind)
            branch_target = str(th.get("branch_target") or "").strip()
            if branch_target:
                bits.append(f"branch {branch_target}")
            if th.get("current"):
                bits.append("this thread")
            url = str(th.get("url") or "").strip()
            detail = f" ({'; '.join(bits)})" if bits else ""
            link = f" — {url}" if url else ""
            lines.append(f"  - {ref}{detail}{link}")

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _format_prior_failure(facet: dict[str, Any]) -> str:
    """Render the prior-run-failure facet as one prominent bundle line.

    Surfaced near the top of the snapshot so a wake landing after an
    interrupted run opens knowing the last run on this thread failed
    operationally, rather than reconstructing it from the woven turns.
    """
    reason = str(facet.get("reason") or "").strip() or "no reply produced"
    detail_bits: list[str] = []
    stage = str(facet.get("stage") or "").strip()
    if stage:
        detail_bits.append(f"stage={stage}")
    attempts = facet.get("attempts")
    if isinstance(attempts, int):
        detail_bits.append(f"{attempts} attempt(s)")
    if facet.get("timed_out"):
        detail_bits.append("timed out")
    exit_code = facet.get("exit_code")
    if isinstance(exit_code, int):
        detail_bits.append(f"exit {exit_code}")
    ts = str(facet.get("ts") or "").strip()
    if ts:
        detail_bits.append(ts)
    detail = f" [{'; '.join(detail_bits)}]" if detail_bits else ""
    return (
        f"- ⚠ Prior run on this thread failed (operational): "
        f"{reason}{detail}. This wake lands after that interruption."
    )


def _format_thread_of_record(repo_root: Path) -> str:
    """Return the dominion thread-of-record hint, when a dominion exists."""
    from . import config as conf
    from . import dominion

    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return ""
    path = dominion.dominion_path(repo_root)
    if not path.is_dir():
        return ""
    record_path = path / "thread-of-record.md"
    state = "exists" if record_path.exists() else "not created yet"
    return (
        f"- Resident-maintained note: `{record_path}` ({state}).\n"
        "- Use it only for durable project-level narrative that should "
        "survive across channels; brr points at the slot but does not "
        "synthesize or mutate it for you."
    )


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
            body = _conversation_body(record)
            summary = body or (record.get("summary") or "").strip()
            source = _conversation_source_label(record)
            line = _format_turn(f"{ts} user ({source})", summary)
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
            body = _conversation_body(record)
            if body:
                line = _format_turn(f"{ts} agent ({label})", body)
            else:
                path = record.get("path") or ""
                line = f"- {ts} artifact {label} {path}".rstrip()
        if line:
            bullets.append(line)
    return "\n".join(bullets)


def _conversation_body(record: dict[str, Any]) -> str:
    body = record.get("body")
    return body.strip() if isinstance(body, str) else ""


def _conversation_source_label(record: dict[str, Any]) -> str:
    parts = [str(record.get("source") or "").strip()]
    correspondent = str(record.get("correspondent_key") or "").strip()
    if correspondent:
        parts.append(f"correspondent={correspondent}")
    thread = str(record.get("conversation_key") or "").strip()
    if thread:
        parts.append(f"thread={thread}")
    return "; ".join(p for p in parts if p)


def _format_turn(prefix: str, body: str) -> str:
    if "\n" not in body:
        return f"- {prefix}: {body}".rstrip()
    indented = "\n".join(f"  {line}" if line else "" for line in body.splitlines())
    return f"- {prefix}:\n{indented}".rstrip()
