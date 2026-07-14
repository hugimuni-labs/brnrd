"""Prompts — assemble the text we hand to runner CLIs.

`brr` ships a handful of prompt templates under ``src/brr/prompts/``
and adopters can override them via ``.brr/prompts/<name>.md``.  This
module knows how to:

- read a template (with override support);
- inject conversation continuity from ``kb/log.md``;
- assemble the daemon-run **Run Context Bundle** (delivery contract,
  branch/runtime metadata, recent conversation, original event body).

It does *not* shell out — that's :mod:`brr.runner`'s job. Keeping the
assembly here means the agent-facing surface evolves independently of
subprocess plumbing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import account, config as conf, dev_reload, forge_state


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_AGENTS_PATH = Path(__file__).resolve().parent / "AGENTS.md"


# ── Template I/O ─────────────────────────────────────────────────────


def effective_prompt_path(name: str, repo_root: Path | None = None) -> Path:
    """The path a prompt template *would* be read from.

    Order: ``<repo>/.brr/prompts/<name>`` then the bundled
    ``src/brr/prompts/<name>``.  Returns the bundled path when neither exists,
    so callers can report a location for an absent template.

    The single source of resolution truth: :func:`read_prompt` reads through
    it and the BootScore manifest reports through it.  A manifest that
    re-derives this itself is a manifest that lies the day the lookup order
    grows a layer.
    """
    if repo_root:
        from . import gitops

        try:
            override = gitops.shared_brr_dir(repo_root) / "prompts" / name
            if override.exists():
                return override
        except OSError:
            pass
    return _PROMPTS_DIR / name


def read_prompt(name: str, repo_root: Path | None = None) -> str:
    """Return a prompt template, preferring a per-repo override.

    Resolution lives in :func:`effective_prompt_path`.  Returns ``""`` when
    no template exists so callers can detect a missing template without a
    ``try/except``.
    """
    path = effective_prompt_path(name, repo_root)
    if path.exists():
        return path.read_text(encoding="utf-8")
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

# Byte budget for the resident-authored, append-only blocks (CS5 active
# plan, CS7 decision ledger). Both had no cap at all until 2026-07-09 —
# unlike the self-inject digest (`dominion.DEFAULT_INJECT_BUDGET_BYTES`)
# and Knowledge Sources (`knowledge._MAX_TOTAL_BYTES`), which have carried
# an enforced budget since their own introduction. "Keep it short" /
# "collapse on sight" was prose-only guidance, and prose guidance is the
# weakest rung the dominion playbook's own "Environment shaping" section
# names — it doesn't hold under normal accretion. Live proof: the decision
# ledger grew unbounded to 68KB/1110 lines over five days (2026-07-04 to
# 2026-07-09) and became the single largest block in the wake bundle,
# dwarfing the capped self-inject digest (~12KB) several times over.
# Same default for both; independently overridable per repo.
_MAX_ACCRETING_BLOCK_BYTES = 8192

_H2_RE = re.compile(r"(?m)^## ")
_H2_SPLIT_RE = re.compile(r"(?m)(?=^## )")


def _tail_trim_entries(content: str, max_bytes: int, source_hint: str) -> str:
    """Trim an append-only, chronological-ascending page to fit *max_bytes*.

    CS5/CS7 pages only ever grow — the resident's own convention is "add an
    entry", never "prune the last one" (see ``_MAX_ACCRETING_BLOCK_BYTES``).
    Mirrors ``_read_recent_log``'s newest-first, entry-boundary-aware
    accumulation, generalized past ``kb/log.md``'s bracketed ``## [date]``
    heading to a plain ``## `` heading: keep the file's leading preamble,
    then walk ``## `` entries from the bottom (newest, since these pages
    append at the end rather than prepend) backward, keeping everything
    that fits and always keeping at least the newest entry even if it alone
    exceeds budget — the most recent decision never silently disappears.

    Returns *content* unchanged when it already fits. Falls back to a flat
    tail cut when the page has no ``## `` headings to respect.
    """
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content
    match = _H2_RE.search(content)
    if not match:
        tail = encoded[-max_bytes:].decode("utf-8", errors="ignore")
        return (
            f"_(older content cut to fit the wake budget — full page: "
            f"{source_hint})_\n\n{tail}"
        )
    preamble = content[: match.start()].strip()
    entries = [e for e in _H2_SPLIT_RE.split(content[match.start() :]) if e.strip()]
    picked: list[str] = []
    used = 0
    for entry in reversed(entries):
        entry_bytes = len(entry.encode("utf-8"))
        if picked and used + entry_bytes > max_bytes:
            break
        picked.append(entry)
        used += entry_bytes
    picked.reverse()
    omitted = len(entries) - len(picked)
    pieces: list[str] = []
    if preamble:
        pieces.append(preamble)
    if omitted:
        pieces.append(
            f"_({omitted} earlier {'entry' if omitted == 1 else 'entries'} cut "
            f"to fit the wake budget — full history: {source_hint})_"
        )
    pieces.append("".join(picked).strip())
    return "\n\n".join(p for p in pieces if p)


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

    Repo ``kb/log.md`` wins when present (today's default for most
    adopters); a repo that migrated its kb out per
    ``kb/design-home-scopes-and-knowledge.md`` falls back to this repo's
    slice of home knowledge, so the recent-activity block doesn't just go
    silent the day a repo's own log moves out of the tree.
    """
    log_path = repo_root / "kb" / "log.md"
    if not log_path.exists():
        log_path = _home_knowledge_log_path(repo_root)
        if log_path is None or not log_path.exists():
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


def _home_knowledge_log_path(repo_root: Path) -> Path | None:
    """Return this repo's ``log.md`` inside home knowledge, if any.

    Mirrors ``knowledge.sources()``'s own home-knowledge resolution
    (repo-scoped bucket for a split account home, flat bucket otherwise)
    without importing :mod:`brr.knowledge` here — that module renders
    injection *blocks*, not raw paths, and pulling it in just for a path
    lookup would be the wrong direction of dependency for a one-file check.
    """
    try:
        cfg = conf.load_config(repo_root)
        ctx = account.resolve_context(repo_root, cfg, create=False)
        if ctx.kind == "account" and account.knowledge_split_mode(cfg) == "per-repo":
            label = account.repo_label(repo_root, cfg)
            return account.repo_knowledge_path(ctx, label) / "log.md"
        return account.knowledge_path(ctx) / "log.md"
    except Exception:
        return None


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

    Reads from the account-scoped resident dominion when present, falling back
    to the legacy repo-local dominion for partially migrated installs. Returns
    ``""`` when the dominion is disabled, not yet materialized, or resolves to
    nothing — the caller drops the block.
    """
    from . import config as conf
    from . import dominion

    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
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
    chosen = None
    digest = ""
    for candidate in dominion.resident_dominion_candidates(repo_root, cfg):
        if not candidate.path.is_dir():
            continue
        digest = dominion.resolve_self_inject(candidate.path, budget_bytes=budget)
        if digest:
            chosen = candidate
            break
    if chosen is None or not digest:
        return ""
    path = chosen.path
    sync_note = ""
    diverged = dominion.needs_sync(chosen.capture_root.parent)
    if diverged:
        sync_note = (
            "\n\n**Your dominion remote has diverged** — brr's last push of "
            "the account dominion repo was rejected, so another machine or "
            "session wrote it too. brr commits locally so nothing is lost, but "
            "reconciling the remote is yours (it's a merge — judgement, not a "
            f"reflex): when you're the one awake, in `{chosen.capture_root}` "
            "fetch, merge / resolve any conflicts, and push. "
            f"(Reason on record: {diverged})"
        )
    if chosen.legacy:
        location = (
            f"Your dominion is the legacy repo-local working memory at `{path}`. "
            "This install has not moved that memory into the account dominion "
            "repo yet."
        )
        remote = (
            "When its git branch has a remote, brr best-effort pushes it after "
            "a thought; reconciling a diverged remote stays yours."
        )
    else:
        location = (
            f"Your dominion is the resident-owned working memory at `{path}` "
            f"inside the local account dominion repo `{chosen.capture_root}`."
        )
        remote = (
            "The account dominion repo is local-first: it can stay only on this "
            "machine, or you can opt into durability by adding a git remote. "
            "When a remote is configured, brr best-effort pushes it after a "
            "thought; reconciling a diverged remote stays yours."
        )
    return (
        "## Your dominion (working memory)\n\n"
        f"{location} It is an absolute path, reachable from any working "
        "directory (your task may run in a worktree or container whose cwd is "
        "elsewhere). It's your durable memory: write notes, pain records, and "
        "your `self-inject` index there freely, and **commit what you mean to "
        f"keep** — the diff is the receipt your next wake reads from. {remote}"
        f"{sync_note}\n\n"
        "Self-injected below per your `self-inject` index — yours to "
        "reshape:\n\n"
        f"{digest}"
    )


def _build_identity_core_block(_repo_root: Path) -> str:
    """Render the product-owned resident identity contract.

    The dominion playbook is resident-owned memory and can drift by design.
    The identity core is the product-owned invariant layer that rides before
    that memory, so a resident can rewrite its workshop without silently
    rewriting brr's loyalty, fallibility, and perception/action contract. This
    is intentionally not a normal per-repo prompt override: appearance should
    move through typed settings, not runtime prose overrides of the core.
    """
    path = _PROMPTS_DIR / "identity-core.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _build_pitfalls_block(repo_root: Path, task_text: str) -> str:
    """Render dominion pitfalls whose triggers fire for *task_text*.

    The affordance surface of the env-shaping loop: failure-memory the
    resident recorded in its account-scoped dominion (legacy repo-local
    fallback supported), injected only when a trigger appears in the task at
    hand (see ``kb/design-environment-shaping.md`` and ``pitfalls.py``).
    Returns ``""`` when the dominion is disabled / absent, or nothing matches.
    """
    if not task_text:
        return ""
    from . import config as conf
    from . import dominion, pitfalls

    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return ""
    matched = []
    for candidate in dominion.resident_dominion_candidates(repo_root, cfg):
        if not candidate.path.is_dir():
            continue
        matched = pitfalls.match(pitfalls.parse_pitfalls(candidate.path), task_text)
        if matched:
            break
    return pitfalls.format_block(matched)


def _build_inter_run_plan_block(repo_root: Path) -> str:
    """Render the active inter-run plan when one exists in the account dominion.

    CS5: the resident leaves a plan in ``plans/<repo-slug>/active.md`` (or
    ``plans/_cross-repo/active.md`` for cross-repo work) and the daemon
    injects it at the top of the next wake — perception=injection, no poll
    needed. The resident updates or retires the plan as the work evolves.
    Returns ``""`` when no plan file exists or when the dominion is off.
    """
    from . import account as acc
    from . import config as conf

    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return ""
    try:
        ctx = acc.resolve_context(repo_root, cfg, create=False)
    except Exception:
        return ""
    if not ctx.enabled:
        return ""

    label = acc.repo_label(repo_root, cfg)
    plan_path = acc.active_plan_path(ctx, label)
    cross_path = acc.cross_repo_plans_path(ctx) / "active.md"
    budget = int(
        cfg.get(
            "dominion.plan_inject_budget_bytes",
            cfg.get("dominion_plan_inject_budget_bytes", _MAX_ACCRETING_BLOCK_BYTES),
        )
    )

    blocks: list[str] = []
    for path, hint in (
        (plan_path, f"`plans/{label}/active.md`"),
        (cross_path, "`plans/_cross-repo/active.md`"),
    ):
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                blocks.append(_tail_trim_entries(content, budget, hint))

    if not blocks:
        return ""

    body = "\n\n---\n\n".join(blocks)
    return (
        "## Active inter-run plan\n\n"
        "Persisted between wakes in the account dominion — the plan you left "
        f"yourself. Update `{plan_path}` (absolute — not relative to your "
        "dominion directory; a copy left under the dominion's own `plans/` is "
        "never injected) or retire it by emptying the file as the work "
        "evolves.\n\n"
        f"{body}"
    )


def _build_runner_policy_block(repo_root: Path) -> str:
    """Render stored runner policy preferences when present in the account dominion.

    CS6: standing runner preferences live in
    ``runner-policy/<repo-slug>/policy.md`` (or ``runner-policy/_account/policy.md``
    for account-wide defaults). Operators can edit them directly; resident-originated
    changes flow through the daemon-owned proposal/approval path. The daemon injects
    them so the resident can reference them when selecting a runner or emitting a
    respawn request.
    Repo-level policy is listed first; account-wide policy follows.
    Returns ``""`` when no policy file exists.
    """
    from . import account as acc
    from . import config as conf

    cfg = conf.load_config(repo_root)
    try:
        ctx = acc.resolve_context(repo_root, cfg, create=False)
    except Exception:
        return ""
    if not ctx.enabled:
        return ""

    label = acc.repo_label(repo_root, cfg)
    repo_policy = acc.runner_policy_path(ctx, label)
    acct_policy = acc.account_runner_policy_path(ctx)

    blocks: list[str] = []
    for path in (repo_policy, acct_policy):
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                blocks.append(content)

    if not blocks:
        return ""

    return (
        "## Stored runner policy\n\n"
        "Standing runner preferences from the account dominion. The daemon "
        "applies these; do not silently rewrite them. To propose a change, "
        "emit an outbox file with `runner_policy: propose` frontmatter and the "
        "new policy body. The daemon parks it for operator approval before "
        "mutating `runner-policy/.../policy.md`.\n\n"
        + "\n\n".join(blocks)
    )


def _build_decision_ledger_block(repo_root: Path) -> str:
    """Render the resident-maintained decision ledger when present.

    CS7: the resident creates and maintains ``ledger/decisions.md`` in the
    account dominion — a user-facing through-line of recent decisions and
    current plan-position in plain language. Complements ``kb/log.md``
    (technical, resident-facing) with something a user can read directly.
    Web-visible via the account dominion remote when one is configured.
    Returns ``""`` when the ledger file does not exist.
    """
    from . import account as acc
    from . import config as conf

    cfg = conf.load_config(repo_root)
    try:
        ctx = acc.resolve_context(repo_root, cfg, create=False)
    except Exception:
        return ""
    if not ctx.enabled:
        return ""

    ledger_path = acc.decisions_ledger_path(ctx)
    if not ledger_path.is_file():
        return ""
    content = ledger_path.read_text(encoding="utf-8").strip()
    if not content:
        return ""
    budget = int(
        cfg.get(
            "dominion.ledger_inject_budget_bytes",
            cfg.get(
                "dominion_ledger_inject_budget_bytes", _MAX_ACCRETING_BLOCK_BYTES
            ),
        )
    )
    content = _tail_trim_entries(content, budget, "`ledger/decisions.md`")

    return (
        "## Decision ledger\n\n"
        "Resident-maintained cross-run decisions and plan-position "
        "(account dominion `ledger/decisions.md`) — the user-facing "
        "through-line alongside `kb/log.md`.\n\n"
        f"{content}"
    )


def _build_kb_health_block(repo_root: Path) -> str:
    """Render the deterministic kb-health preflight as a wake-time block.

    Runs the cheap consistency scan (:mod:`brr.kb_preflight`) plus the
    graph-stats snapshot (:mod:`brr.kb_health`) over whichever directory
    ``knowledge.active_kb_dir`` resolves as this repo's kb (repo-committed
    ``kb/``, or home knowledge for a repo that dogfoods that shape) and
    surfaces any findings so the resident folds fixes into the current
    thought.
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
    from . import kb_health, kb_preflight, knowledge

    cfg = conf.load_config(repo_root)
    if str(cfg.get("kb_maintenance", "auto")).strip().lower() == "never":
        return ""
    kb_dir = knowledge.active_kb_dir(repo_root, cfg)
    findings = kb_preflight.scan(repo_root, kb_dir)
    if not findings:
        return ""
    findings_block = kb_preflight.format_findings(findings)
    stats_block = kb_health.format_graph_stats(
        kb_health.compute_graph_stats(repo_root, kb_dir),
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


def _build_knowledge_sources_block(repo_root: Path) -> str:
    """Render the compact home→repo→docs knowledge slice.

    Leads with the knowledge chain's divergence warning when brr's last push
    of the knowledge repo was rejected. A marker nothing surfaces is a
    guardrail that doesn't guard: the whole point of not swallowing a
    rejected push is that the next resident awake sees it and reconciles.
    """

    from . import config as conf
    from . import gitops
    from . import knowledge

    cfg = conf.load_config(repo_root)
    block = knowledge.render_injection(repo_root, cfg)
    diverged = knowledge.needs_sync(gitops.shared_brr_dir(repo_root))
    if not diverged:
        return block
    warning = (
        "**The knowledge remote has diverged** — brr's last push of the "
        "knowledge repo was rejected, so another machine or session wrote it "
        "too. Nothing is lost (it's committed locally), but reconciling is "
        "yours: fetch, merge / resolve, push. Until then the kb pages this "
        "run writes will not reach the archive, and they will not be "
        f"linkable. (Reason on record: {diverged})"
    )
    return f"{warning}\n\n{block}" if block else warning


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


def _mtime_iso(path: Path) -> str | None:
    """Return the file's mtime as a compact ISO date, or ``None`` if missing."""
    try:
        import datetime
        ts = path.stat().st_mtime
        return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except OSError:
        return None


def _rendered_bytes(block: str) -> int:
    """UTF-8 size of a block **as rendered into this wake**.

    Not the file on disk: a dominion digest or a log tail is trimmed to the
    wake budget before it enters, and the trimmed size is the one that costs
    attention.  An empty block measures 0 — present=False, bytes=0, which is
    a measurement, not the ``None`` that means *never weighed*.
    """
    return len(block.encode("utf-8"))


def _build_injected_blocks_with_contracts(
    repo_root: Path, *, task_text: str | None = None
) -> tuple[list[tuple[str, str]], list["ContractEntry"]]:
    """The scored implementation behind ``_build_injected_blocks``.

    Returns the rendered blocks **keyed** — ``(block_key, text)`` pairs, in
    prompt order — plus a :class:`ContractEntry` list, the source manifest for
    every block considered.  Blocks that are absent this run (empty file, nothing
    to inject) still appear in the manifest with ``present=False`` so ``brnrd
    prompts show`` can report the full picture.

    The keys are not decoration.  A caller that mounts some blocks as a resumed
    transcript (``boot.transcript``) must take exactly those blocks *out of the
    prose*, or the wake pays for them twice and the T-vs-P experiment measures
    nothing.  An unkeyed ``list[str]`` made that subtraction impossible to state;
    a keyed one makes it a dict lookup.

    Shared by ``_build_injected_blocks``, ``build_injected_context``, and
    the scored prompt-builder variants — one computation, three consumers.
    """
    from .bootscore import (
        ContractEntry,
        OWNER_PRODUCT, OWNER_RESIDENT, OWNER_PROJECT, OWNER_DAEMON_LIVE,
        AUTHORITY_IDENTITY, AUTHORITY_MEMORY, AUTHORITY_PLAN, AUTHORITY_POLICY,
        AUTHORITY_LEDGER, AUTHORITY_KNOWLEDGE, AUTHORITY_ACTIVITY, AUTHORITY_HEALTH,
    )

    keyed: list[tuple[str, str]] = []
    contracts: list[ContractEntry] = []

    # 1. Resident identity core
    ic_path = effective_prompt_path("identity-core.md", repo_root)
    identity_core = _build_identity_core_block(repo_root)
    contracts.append(ContractEntry(
        block_key="identity-core",
        label="Resident identity core",
        owner=OWNER_PRODUCT,
        authority=AUTHORITY_IDENTITY,
        freshness=_mtime_iso(ic_path),
        location=str(ic_path),
        present=bool(identity_core),
        bytes=_rendered_bytes(identity_core),
    ))
    if identity_core:
        keyed.append(("identity-core", identity_core))

    # 2. Dominion digest (living playbook + self-inject)
    dominion_block = _build_dominion_block(repo_root)
    contracts.append(ContractEntry(
        block_key="dominion",
        label="Dominion digest (self-inject)",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_MEMORY,
        freshness=None,
        location="computed",
        present=bool(dominion_block),
        bytes=_rendered_bytes(dominion_block),
    ))
    if dominion_block:
        keyed.append(("dominion", dominion_block))

    # 3. CS5 — active inter-run plan
    inter_run_plan = _build_inter_run_plan_block(repo_root)
    contracts.append(ContractEntry(
        block_key="inter-run-plan",
        label="Active inter-run plan (CS5)",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_PLAN,
        freshness=None,
        location="computed",
        present=bool(inter_run_plan),
        bytes=_rendered_bytes(inter_run_plan),
    ))
    if inter_run_plan:
        keyed.append(("inter-run-plan", inter_run_plan))

    # 4. CS6 — stored runner policy
    runner_policy = _build_runner_policy_block(repo_root)
    contracts.append(ContractEntry(
        block_key="runner-policy",
        label="Stored runner policy (CS6)",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_POLICY,
        freshness=None,
        location="computed",
        present=bool(runner_policy),
        bytes=_rendered_bytes(runner_policy),
    ))
    if runner_policy:
        keyed.append(("runner-policy", runner_policy))

    # 5. CS7 — decision ledger
    decision_ledger = _build_decision_ledger_block(repo_root)
    contracts.append(ContractEntry(
        block_key="decision-ledger",
        label="Decision ledger (CS7)",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_LEDGER,
        freshness=None,
        location="computed",
        present=bool(decision_ledger),
        bytes=_rendered_bytes(decision_ledger),
    ))
    if decision_ledger:
        keyed.append(("decision-ledger", decision_ledger))

    # 6. Pitfalls matching the task
    pitfalls_block = _build_pitfalls_block(repo_root, task_text) if task_text else ""
    contracts.append(ContractEntry(
        block_key="pitfalls",
        label="Task-matched pitfalls",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_MEMORY,
        freshness=None,
        location="computed",
        present=bool(pitfalls_block),
        bytes=_rendered_bytes(pitfalls_block),
    ))
    if pitfalls_block:
        keyed.append(("pitfalls", pitfalls_block))

    # 7. Knowledge sources
    knowledge_block = _build_knowledge_sources_block(repo_root)
    contracts.append(ContractEntry(
        block_key="knowledge-sources",
        label="Knowledge sources (home+repo+docs)",
        owner=OWNER_PROJECT,
        authority=AUTHORITY_KNOWLEDGE,
        freshness=None,
        location="computed",
        present=bool(knowledge_block),
        bytes=_rendered_bytes(knowledge_block),
    ))
    if knowledge_block:
        keyed.append(("knowledge-sources", knowledge_block))

    # 8. Recent activity log tail
    context = _build_context_block(repo_root)
    contracts.append(ContractEntry(
        block_key="recent-activity",
        label="Recent activity (kb/log.md tail)",
        owner=OWNER_DAEMON_LIVE,
        authority=AUTHORITY_ACTIVITY,
        freshness=None,
        location="computed",
        present=bool(context),
        bytes=_rendered_bytes(context),
    ))
    if context:
        keyed.append(("recent-activity", context))

    # 9. kb health findings
    kb_health_block = _build_kb_health_block(repo_root)
    contracts.append(ContractEntry(
        block_key="kb-health",
        label="kb health (deterministic preflight)",
        owner=OWNER_DAEMON_LIVE,
        authority=AUTHORITY_HEALTH,
        freshness=None,
        location="computed",
        present=bool(kb_health_block),
        bytes=_rendered_bytes(kb_health_block),
    ))
    if kb_health_block:
        keyed.append(("kb-health", kb_health_block))

    return keyed, contracts


def _build_injected_blocks(
    repo_root: Path, *, task_text: str | None = None
) -> list[str]:
    """The standing, always-on context blocks brr injects into every wake.

    Returns the *base* blocks:

    1. Resident identity core — product-owned invariant contract
    2. Dominion digest (living playbook + ``self-inject``)
    3. Active inter-run plan (CS5) — the plan the resident left itself
    4. Stored runner policy (CS6) — standing runner preferences
    5. Decision ledger (CS7) — user-facing through-line of recent decisions
    6. Pitfalls matching the task
    7. Recent-activity log tail
    8. kb health note

    Each CS5/CS6/CS7 block is silent when no file exists — never a
    constant tax, only present when the resident wrote something.  The
    ordering puts the product identity contract before the resident-owned
    state (dominion + active plan + policy + ledger), then the shared project
    history, so a waking can distinguish authority layers in read order.

    Shared by ``_join_prompt_parts`` and ``build_injected_context``; whatever
    block is added here surfaces in both paths with no drift.  Mode-toggle
    blocks (diffense, introspection) sit on top of these; they are added by
    ``_join_prompt_parts`` (for the full runner prompt) and by
    ``build_injected_context`` (for the faithful inject-tool view).

    Delegates to ``_build_injected_blocks_with_contracts`` and discards the
    contracts list and the keys — the scored variant is the single implementation.
    """
    keyed, _ = _build_injected_blocks_with_contracts(repo_root, task_text=task_text)
    return [text for _, text in keyed]


def build_injected_context(repo_root: Path, *, task_text: str | None = None) -> str:
    """brr's assembled wake-context, for ``brnrd agent inject`` and agent wrappers.

    Returns the **full** injected context a daemon task wake receives: the
    base blocks (dominion digest, pitfalls, recent-activity log, kb health)
    **plus** the mode-toggle blocks (diffense review-pack prompt,
    introspection invitation) when their config toggles are on.  The result
    mirrors what ``_join_prompt_parts`` embeds minus the preamble (AGENTS.md
    / runner template) and the trailing task bundle, giving a faithful
    "what did this wake see?" answer via ``brnrd agent inject``.

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
    kernel: str | None = None,
    task_text: str | None = None,
    diffense: bool = False,
    inject_blocks: bool = True,
    prepared_injected_blocks: list[str] | None = None,
    prepared_introspection_block: str | None = None,
) -> str:
    """Stitch preamble, optional recent-context block, and trailer.

    ``inject_blocks=False`` skips the resident stack entirely — the base
    injected blocks (identity core, dominion digest, inter-run plan, runner
    policy, decision ledger, pitfalls, knowledge sources, kb health) and the
    introspection dev-mode block. That's the B4 worker trim: a bounded
    worker wake gets its task and files, not the standing resident context.
    The ``diffense`` review-pack step is independent of that trim (a worker
    wake asking for diffense is out of scope for now; whatever the caller
    passes is honored as-is).
    """
    # The kernel leads.  Everything after it is reference the wake may consult;
    # the kernel is the wake's own first move (``bootscore.format_kernel``).
    parts = [kernel, preamble] if kernel else [preamble]
    if inject_blocks:
        # The scored builder supplies this pair from one source read.  The
        # ordinary path stays lazy, but a replay/inspection run must not
        # build the prompt and its manifest from two independently-read
        # views of dominion and knowledge state.
        parts.extend(
            prepared_injected_blocks
            if prepared_injected_blocks is not None
            else _build_injected_blocks(repo_root, task_text=task_text)
        )
    if diffense:
        pack_step = read_prompt("diffense.md", repo_root)
        if pack_step:
            parts.append(pack_step)
    if inject_blocks:
        # Last framing before the task: invite the resident to look at the
        # whole shape it has just read (opt-in dev mode). Placed here so it
        # can refer to everything above and sit fresh against the task
        # bundle.
        introspection_block = (
            prepared_introspection_block
            if prepared_introspection_block is not None
            else _build_introspection_block(repo_root)
        )
        if introspection_block:
            parts.append(introspection_block)
    parts.append(trailer)
    return "\n\n".join(parts)


def _collect_preamble_contracts(
    repo_root: Path,
    *,
    is_worker: bool = False,
    is_daemon: bool = True,
    has_diffense: bool = False,
    has_introspection: bool = False,
) -> list[Any]:
    """Compute ContractEntry items for the preamble + substrate + config-toggle blocks.

    These are the blocks that live *outside* ``_build_injected_blocks`` — the
    prompt frame before and after the inject stack.  Returns the list in the
    order they appear in a rendered prompt.
    """
    from .bootscore import (
        ContractEntry,
        OWNER_PRODUCT, AUTHORITY_CONTRACT, AUTHORITY_SUBSTRATE, AUTHORITY_CONFIG,
    )

    entries: list[Any] = []

    def _file_entry(
        name: str, *, block_key: str, label: str, authority: str, present: bool | None = None
    ) -> Any:
        """One manifest row for a file-backed prompt block.

        Location comes from :func:`effective_prompt_path` — the same resolution
        the reader uses — so an override reports as the override.
        """
        path = effective_prompt_path(name, repo_root)
        exists = path.exists()
        is_present = exists if present is None else (present and exists)
        # The rendered block, not the file: every reader of these templates
        # strips them before joining.  A toggle-off block measures 0 — it did
        # not enter this wake, whatever its file weighs.
        text = read_prompt(name, repo_root).strip() if is_present else ""
        return ContractEntry(
            block_key=block_key,
            label=label,
            owner=OWNER_PRODUCT,
            authority=authority,
            freshness=_mtime_iso(path),
            location=str(path),
            present=is_present,
            bytes=_rendered_bytes(text),
        )

    # Preamble: run.md / worker.md
    entries.append(_file_entry(
        "worker.md" if is_worker else "run.md",
        block_key="worker-preamble" if is_worker else "run-preamble",
        label="Worker preamble (worker.md)" if is_worker
              else "Operational preamble (run.md)",
        authority=AUTHORITY_CONTRACT,
    ))

    # weave.md — rides every runner path
    entries.append(_file_entry(
        "weave.md",
        block_key="weave",
        label="Working register (weave.md)",
        authority=AUTHORITY_CONTRACT,
    ))

    # daemon-substrate.md — daemon paths only
    if is_daemon:
        entries.append(_file_entry(
            "daemon-substrate.md",
            block_key="daemon-substrate",
            label="Daemon mechanics (daemon-substrate.md)",
            authority=AUTHORITY_SUBSTRATE,
        ))

    # Config-toggle blocks — present only when the toggle is on *and* the
    # template exists.
    entries.append(_file_entry(
        "diffense.md",
        block_key="diffense",
        label="diffense review-pack prompt",
        authority=AUTHORITY_CONFIG,
        present=has_diffense,
    ))
    entries.append(_file_entry(
        "introspection.md",
        block_key="introspection",
        label="Introspection dev-mode invitation",
        authority=AUTHORITY_CONFIG,
        present=has_introspection,
    ))

    # Run Context Bundle — daemon-live runtime trailer.  ``bytes`` stays None
    # here: this function is also the CLI's path, where no bundle is rendered
    # and its size is genuinely *unknown*, not zero.  The daemon stamps the
    # real figure in :func:`build_daemon_prompt_with_score`.
    if is_daemon:
        from .bootscore import OWNER_DAEMON_LIVE, AUTHORITY_RUNTIME
        entries.append(ContractEntry(
            block_key="run-context-bundle",
            label="Run Context Bundle (runtime facts)",
            owner=OWNER_DAEMON_LIVE,
            authority=AUTHORITY_RUNTIME,
            freshness=None,
            location="computed",
            present=is_daemon,
        ))

    return entries


def _build_orientation(
    *,
    is_daemon: bool,
    is_worker: bool,
    environment: str | None,
    pending_count: int,
    has_event_body: bool,
) -> list[Any]:
    """The kernel's ``next:`` list — ordered actions, derived from posture.

    Deterministic.  Every step is a *fact about this wake* plus the action it
    obliges; none of them is an inference about what the resident intends.
    That boundary is the whole reason the daemon is allowed to write this list
    at all (``design-native-boot-sequence.md`` §1: facts and pointers, not
    generated interpretations).

    Ordering is execution order, not authority order: what is being asked →
    make yourself visible → the constraint that will bite → the queue → go.
    """
    from .bootscore import OrientationStep

    steps: list[Any] = []

    if has_event_body:
        steps.append(OrientationStep(
            action="read the task",
            reason="the verbatim event body is the last block below",
        ))

    if is_daemon and not is_worker:
        steps.append(OrientationStep(
            action="write .card + .task-classification",
            reason="the card is the surface the user watches while you think",
        ))

    # The queue belongs to the *resident*, and only to the resident.
    #
    # This was gated on ``pending_count`` alone, and it caused a live incident on
    # 2026-07-13. ``pending_count`` is the **parent's** queue — events addressed
    # to the resident, in the resident's gate thread. A spawned worker inherited
    # it and was handed, at position 1, in the imperative:
    #
    #     next:
    #       2. answer 12 queued events — one outbox file each, `event: <id>`
    #
    # Two workers (claude-haiku, codex-mini) did exactly that: they answered
    # twelve of the user's messages to the resident, in the resident's thread,
    # with no context for any of them.
    #
    # ``worker.md`` states plainly that the spawning conversation "is not yours
    # to hold or extend" — and it states it in *prose*, *below* this list. The
    # kernel overrode it. That is the whole thesis of the boot work confirmed
    # from the wrong end: **the imperative action-list at the hot slot is what
    # gets acted on; the prose contract beneath it is what gets skimmed.** The
    # kernel did not misfire. It worked perfectly, and carried a wrong
    # instruction with total authority.
    #
    # A worker has no gate authority, no `event:` disposition to make, and no
    # standing in that thread. It must never see this step.
    if pending_count and not is_worker:
        plural = "s" if pending_count != 1 else ""
        steps.append(OrientationStep(
            action=f"answer {pending_count} queued event{plural}",
            reason="one outbox file each, `event: <id>`; nothing else clears them",
        ))

    if (environment or "").strip() == "host":
        steps.append(OrientationStep(
            action="branch before you edit",
            reason="host checkout — your push, or the work never leaves this machine",
        ))

    steps.append(OrientationStep(
        action="act",
        reason="deltas arrive at every tool boundary; never poll",
    ))
    return steps


def probe_shell_hook_capability(shell: str | None) -> bool | None:
    """Can *shell* actually take brr's hook config here?  ``None`` = unknown.

    The real prechecks (:func:`brr.hooks.hook_capability` for file-config
    Shells, :func:`brr.hooks.codex_hook_capability` for argv-config codex) —
    not a guess from an environment variable.  No Shell named ⇒ ``None``:
    *unknown from here* is a legitimate answer and the honest one.
    """
    from . import hooks as _hooks

    if not shell or not shell.strip():
        return None
    base = shell.split()[0].strip()
    if base == "codex":
        return _hooks.codex_hook_capability()
    return _hooks.hook_capability(base or None, Path.cwd())


def read_hook_stamps(state_dir: Path | None) -> dict[str, str]:
    """Per-phase last-fired stamps from a run's ``.hook-state.json``.

    Explicit argument, never an ambient environment read: a score built for a
    *fixture* or for a run that has not started yet must not absorb whatever
    wake happens to be firing hooks in the surrounding process.  (The boot
    replay harness caught exactly that leak — a live wall-clock stamp landing
    in a versioned snapshot.)
    """
    if state_dir is None:
        return {}
    import json

    from . import hooks as _hooks

    path = Path(state_dir)
    if path.suffix == ".json":
        path = path.parent
    state_file = path / _hooks.HOOK_STATE_NAME
    try:
        if not state_file.exists():
            return {}
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    stamps = state.get(_hooks.FIRED_KEY)
    return {str(k): str(v) for k, v in stamps.items()} if isinstance(stamps, dict) else {}


def _collect_hooks_info(
    *,
    installed: bool | None = None,
    hook_stamps: dict[str, str] | None = None,
) -> list[Any]:
    """Return a :class:`BootHook` list for the abstract phase set.

    A pure function of its arguments — every caller supplies what it actually
    knows, and nothing is inferred from ambient process state:

    - ``declared`` is always ``True``: the three abstract phases are the
      daemon's back-channel contract.
    - ``installed`` is three-state — ``True`` (wired), ``False`` (this Shell
      cannot take the config), ``None`` (*unknown from here*).  The daemon
      passes the fact it holds; the CLI probes; nobody guesses.  Reporting
      "not-installed" for "I cannot see from here" is how a live hook told the
      only operator looking that it was dead.
    - ``last_fired`` is per phase.  A post-tool hook firing says nothing about
      session-start, so a single stamp is never copied across all three.
    """
    from . import hooks as _hooks
    from .bootscore import BootHook

    stamps = hook_stamps or {}
    return [
        BootHook(
            name=phase,
            declared=True,
            installed=installed,
            last_fired=str(stamps[phase]) if stamps.get(phase) else None,
        )
        for phase in _hooks.PHASES  # ("post-tool", "stop", "session-start")
    ]


def build_boot_score(
    repo_root: Path | None = None,
    *,
    is_daemon: bool = True,
    is_worker: bool = False,
    runner_name: str | None = None,
    runner_shell: str | None = None,
    runner_core: str | None = None,
    environment: str | None = None,
    event_ids: tuple[str, ...] = (),
    body_provenance: str | None = None,
    source_gate: str | None = None,
    continuity: "BootContinuity | None" = None,
    pending_count: int = 0,
    budget: str | None = None,
    quota: str | None = None,
    branch: str | None = None,
    task_text: str | None = None,
    has_event_body: bool = False,
    has_diffense: bool = False,
    has_introspection: bool = False,
    contracts: list[Any] | None = None,
    hooks_installed: bool | None = None,
    hook_stamps: dict[str, str] | None = None,
    mounted: bool = False,
) -> "BootScore":
    """Assemble a :class:`BootScore` for inspection without building the full prompt.

    Used by the daemon (every wake), ``brnrd prompts show``, and the replay
    test harness.  Deterministic and network-free.  When ``repo_root`` is
    ``None`` the inject-blocks contracts reflect only the bundled product
    templates (no dominion, no plan, no knowledge sources).

    Hook facts are **passed in, never sniffed**: ``hooks_installed`` is the
    caller's known answer (the daemon installed the config, so it reports it;
    the CLI probes with :func:`probe_shell_hook_capability`), and
    ``hook_stamps`` are per-phase last-fired times from an explicitly named
    run (:func:`read_hook_stamps`).  Both default to "unknown / none", which
    is what keeps this deterministic — a score built for a fixture cannot
    absorb the wall clock of whatever wake is firing hooks around it.

    The returned score carries:

    - ``contracts``: every block considered for the given prompt type,
      with ``present`` reflecting whether the source exists today.
    - ``hooks``: the abstract phase set with per-phase installed/fired state.
    """
    from .bootscore import (
        BootScore, BootBody, BootHost, BootAttention, BootContinuity, BootPosture,
        DEPTH_COMPACT, SCHEMA_VERSION,
    )

    effective_root = repo_root if repo_root is not None else Path.cwd()

    if contracts is None:
        # Preamble + substrate + toggle blocks
        preamble_contracts = _collect_preamble_contracts(
            effective_root,
            is_worker=is_worker,
            is_daemon=is_daemon,
            has_diffense=has_diffense,
            has_introspection=has_introspection,
        )

        # Inject-stack blocks (skipped for workers)
        if not is_worker:
            _, inject_contracts = _build_injected_blocks_with_contracts(
                effective_root, task_text=task_text
            )
        else:
            inject_contracts = []

        # Ordered: preamble blocks first, then inject stack (mirrors prompt
        # order). The runtime trailer comes after the inject stack.
        pre_inject = [c for c in preamble_contracts if c.block_key != "run-context-bundle"]
        runtime_entries = [c for c in preamble_contracts if c.block_key == "run-context-bundle"]
        all_contracts = pre_inject + inject_contracts + runtime_entries
    else:
        all_contracts = contracts

    # Host kind
    kind = "daemon" if is_daemon else "ad-hoc"
    pub_owner = "resident-owned" if not is_worker else "worker"

    hooks_info = _collect_hooks_info(
        installed=hooks_installed, hook_stamps=hook_stamps
    )

    # tier is a *reading*, not a label: it reports what the hook contract
    # actually says, including that it cannot be known from here.
    installed = hooks_info[0].installed if hooks_info else None
    if installed is None:
        tier = None
    elif installed:
        tier = "Tier 2 hooks installed"
    else:
        tier = "Tier 1 heartbeat-polled (no hooks)"

    return BootScore(
        schema_version=SCHEMA_VERSION,
        depth=DEPTH_COMPACT,
        body=BootBody(
            name=runner_name,
            shell=runner_shell,
            core=runner_core,
            tier=tier,
            mounted=mounted,
            # Why this body — *not* where the attention came from. These were
            # one field until 2026-07-13; see BootBody.provenance.
            provenance=body_provenance,
        ),
        host=BootHost(
            kind=kind,
            environment=environment,
            publication_owner=pub_owner,
            # Asked here rather than threaded down from the loop: staleness is a
            # property of *the process doing the assembling*, and this is where
            # the assembling happens.  Inert outside a live daemon (no captured
            # fingerprint ⇒ False), so ad-hoc runs and tests never see it.
            image_stale=dev_reload.image_is_stale(),
        ),
        continuity=continuity if continuity is not None else BootContinuity(),
        attention=BootAttention(event_ids=event_ids, source_gate=source_gate),
        posture=BootPosture(
            pending_count=pending_count,
            budget=budget,
            quota=quota,
            branch=branch,
        ),
        orientation=_build_orientation(
            is_daemon=is_daemon,
            is_worker=is_worker,
            environment=environment,
            pending_count=pending_count,
            has_event_body=has_event_body,
        ),
        contracts=all_contracts,
        hooks=hooks_info,
    )


def build_daemon_prompt_with_score(
    task: str,
    event_id: str,
    response_path: str,
    repo_root: Path,
    **kwargs: Any,
) -> "tuple[str, BootScore]":
    """Build the daemon prompt and return it together with the BootScore.

    Accepts the same keyword arguments as :func:`build_daemon_prompt`.  The
    returned ``BootScore`` is the source manifest for the assembled prompt —
    the inspectable middle between the versioned sources and the rendered text.

    This is the daemon's path: every wake builds its score here, and the
    daemon persists it to ``.brr/runs/<run-id>/boot-score.json``.  For the
    prompt text alone use :func:`build_daemon_prompt`.

    ``hooks_installed`` (keyword) is the run's own hook-config decision; the
    daemon knows it because it installed the config, and the score should not
    re-guess it from a process that is not the runner.
    """
    # Resolved runner facts. Read, not popped: since Slice 2 the *prompt* needs
    # them too — the kernel names the body the wake is running in, where the
    # Mode line only prints the display label (what was *requested*). Those two
    # have diverged in production; the wake should be able to see it.
    runner_name = kwargs.get("runner_name")
    runner_shell = kwargs.get("runner_shell")
    runner_core = kwargs.get("runner_core")
    body_provenance = kwargs.get("body_provenance")
    source_gate = kwargs.get("source_gate")
    continuity = kwargs.get("continuity")
    environment = kwargs.get("environment")
    worker = bool(kwargs.get("worker", False))
    diffense = bool(kwargs.get("diffense", False))
    event_body = kwargs.get("event_body", "")
    pending_events = kwargs.get("pending_events") or []
    budget_seconds = kwargs.get("budget_seconds")
    runner_quota = kwargs.get("runner_quota")
    branch_name = kwargs.get("branch_name")
    hooks_installed = kwargs.get("hooks_installed")

    pitfall_text = "\n".join(t for t in (task, event_body or "") if t)

    # The introspection toggle is read inside _build_introspection_block (it
    # returns "" when off), so its rendered emptiness *is* the toggle state —
    # no second config read needed to know whether the block is present.
    has_diff = diffense

    mount_sink: dict[str, str] | None = kwargs.pop("_mount_sink", None)

    if worker:
        injected_keyed: list[tuple[str, str]] = []
        inject_contracts: list[Any] = []
        introspection_block = ""
    else:
        injected_keyed, inject_contracts = _build_injected_blocks_with_contracts(
            repo_root, task_text=pitfall_text or None
        )
        introspection_block = _build_introspection_block(repo_root)

    preamble_contracts = _collect_preamble_contracts(
        repo_root,
        is_worker=worker,
        is_daemon=True,
        has_diffense=has_diff,
        has_introspection=bool(introspection_block),
    )
    pre_inject = [c for c in preamble_contracts if c.block_key != "run-context-bundle"]
    runtime_entries = [c for c in preamble_contracts if c.block_key == "run-context-bundle"]

    from .bootscore import (
        ContractEntry, OWNER_DAEMON_LIVE, AUTHORITY_RUNTIME, replace_bytes,
    )

    # The kernel is a block of the wake and pays rent like every other one.
    # A ledger that omits the auditor is not a ledger.
    kernel_entry = ContractEntry(
        block_key="boot-kernel",
        label="Boot kernel (action-first score)",
        owner=OWNER_DAEMON_LIVE,
        authority=AUTHORITY_RUNTIME,
        freshness=None,
        location="computed",
        present=True,
    )
    contracts = [kernel_entry] + pre_inject + inject_contracts + runtime_entries

    # Which blocks *could* be mounted as seeded perceptions rather than prose:
    # exactly the ones backed by a real file. A block at ``location == "computed"``
    # (the kernel, the run bundle, live portal posture) has no honest ``Read`` —
    # it is not on disk — so it stays prose, and this is the same test
    # ``transcript.build_orientation_transcript`` applies. Deciding it here, from
    # the contracts, is what stops a computed block from being subtracted from the
    # prose and then silently not mounted: dropped from the wake entirely, by a
    # boot that was trying to be clever.
    from .transcript import COMPUTED

    mountable = frozenset(
        c.block_key
        for c in (preamble_contracts + inject_contracts)
        if c.present and c.location and c.location != COMPUTED
    ) if mount_sink is not None else frozenset()

    # The prompt and its inspection score now share the same injected blocks
    # and manifest.  A changing dominion/kb cannot make the CLI explain a
    # different wake than the one the runner actually received.
    sizes: dict[str, int] = {}
    prompt = build_daemon_prompt(
        task, event_id, response_path, repo_root, **kwargs,
        _prepared_injected_keyed=injected_keyed,
        _prepared_introspection_block=introspection_block,
        _size_sink=sizes,
        _mountable=mountable,
        _mount_sink=mount_sink,
    )

    # Stamp the two blocks only the renderer could weigh (the kernel it built
    # and the bundle it computed); the rest measured themselves at build time.
    contracts = [
        replace_bytes(c, sizes[c.block_key]) if c.block_key in sizes else c
        for c in contracts
    ]

    score = build_boot_score(
        repo_root,
        is_daemon=True,
        is_worker=worker,
        runner_name=str(runner_name) if runner_name else None,
        runner_shell=str(runner_shell) if runner_shell else None,
        runner_core=str(runner_core) if runner_core else None,
        body_provenance=str(body_provenance) if body_provenance else None,
        source_gate=str(source_gate) if source_gate else None,
        continuity=continuity,
        environment=str(environment) if environment else None,
        event_ids=(event_id,),
        pending_count=len(pending_events),
        budget=f"{budget_seconds // 60}m" if budget_seconds else None,
        quota=str(runner_quota) if runner_quota else None,
        branch=str(branch_name) if branch_name else None,
        task_text=pitfall_text or None,
        has_event_body=bool((event_body or task or "").strip()),
        has_diffense=has_diff,
        has_introspection=bool(introspection_block),
        contracts=contracts,
        hooks_installed=hooks_installed,
        # Same derivation the kernel used, from the same `mountable` set — so the
        # block the wake *reads* and the score the daemon *persists* cannot disagree
        # about which boot it got. (They already did, for one commit: the kernel said
        # "mounted", the score said `false`. An inspection that describes a wake
        # nobody had is the failure this module's docstring already names.)
        mounted=bool(mountable),
    )
    score.prompt_bytes = sizes.get("_prompt")

    return prompt, score


def diffense_emit_enabled(cfg: dict[str, Any] | None) -> bool:
    """Return whether runner prompts should ask for a diffense review pack.

    Off by default because the prompt fragment and follow-on review-pack
    work are not free: a chat-only turn, a tiny fix, or a user who did not
    ask for PR ceremony should not pay that token and attention tax. Opt in
    per repo with ``diffense.emit_pack=true`` in ``.brr/config`` when the
    richer review surface is worth the cost.
    """
    cfg = cfg or {}
    return bool(cfg.get("diffense.emit_pack", cfg.get("diffense_emit_pack", False)))


# ── Top-level builders ───────────────────────────────────────────────


def build_init_prompt(repo_root: Path) -> str:
    """Build the prompt for ``brnrd init`` — setup.md + bundled AGENTS.md.

    brr's own ``AGENTS.md`` (bundled inside the package) is the model
    adopters' setup agent uses. Universal sections copy verbatim;
    project-specific sections (Project, Build and run, Code guidelines,
    Constraints) get rewritten for the adopter's repo.
    """
    setup = read_prompt("setup.md", repo_root)
    template = _AGENTS_PATH.read_text(encoding="utf-8") if _AGENTS_PATH.exists() else ""
    return f"{setup}\n\n{template}"


def _read_preamble_with_weave(repo_root: Path) -> str:
    """Read ``run.md`` plus the working-register contract (``weave.md``).

    The weave rides every runner path — one-shot and daemon alike — because
    it governs the resident's *own* working surfaces (card notes, stderr
    narration, dominion scratch), which exist under any host. It sits right
    after the host-agnostic operational preamble and before any host-specific
    machinery so read order mirrors authority: how you operate, how you
    write while operating, then who is driving.
    """
    preamble = read_prompt("run.md", repo_root)
    weave = read_prompt("weave.md", repo_root)
    if weave.strip():
        preamble = f"{preamble.rstrip()}\n\n{weave.strip()}"
    return preamble


def _preamble_parts(repo_root: Path, *, worker: bool) -> list[tuple[str, str]]:
    """The preamble as ``(block_key, text)`` parts, in read order.

    Same bytes as ``_read_preamble_with_weave`` + ``daemon-substrate.md`` glued
    together (:func:`_glue_preamble` re-joins them identically) — but *keyed*, so
    a wake that mounts a block as a seeded perception can take it out of the prose
    instead of paying for it twice.

    These are the blocks that carry the wake's obligations (write the card, branch
    before you edit, own the pending event). They are therefore the blocks the
    transcript experiment most needs to be able to move, and an unkeyed preamble
    string is precisely what made that impossible.
    """
    key = "worker-preamble" if worker else "run-preamble"
    parts = [(key, read_prompt("worker.md" if worker else "run.md", repo_root))]
    for name, k in (("weave.md", "weave"), ("daemon-substrate.md", "daemon-substrate")):
        text = read_prompt(name, repo_root)
        if text.strip():
            parts.append((k, text.strip()))
    return parts


def _glue_preamble(parts: list[str]) -> str:
    """Re-join preamble parts exactly as the unkeyed path did."""
    if not parts:
        return ""
    out = parts[0]
    for part in parts[1:]:
        out = f"{out.rstrip()}\n\n{part}"
    return out


def _build_worker_preamble(repo_root: Path) -> str:
    """Read ``worker.md`` plus the working-register contract (``weave.md``).

    The slim counterpart to :func:`_read_preamble_with_weave`: a worker wake
    (B4, ``kb/design-director-loop.md`` §orchestrator/worker) gets the bounded
    task preamble instead of the resident's ``run.md`` — no dominion write,
    no kb governance, no "reconsider intent" stewardship framing, none of
    which apply to a bounded handoff. ``weave.md`` still rides: it governs
    *how* any wake writes to its working surfaces, resident or worker alike.
    """
    preamble = read_prompt("worker.md", repo_root)
    weave = read_prompt("weave.md", repo_root)
    if weave.strip():
        preamble = f"{preamble.rstrip()}\n\n{weave.strip()}"
    return preamble


def build_run_prompt(task: str, repo_root: Path) -> str:
    """Build the prompt for ``brnrd run`` — run.md + weave + context + task."""
    preamble = _read_preamble_with_weave(repo_root)
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
    run_id: str | None = None,
    source: str | None = None,
    environment: str | None = None,
    branch_name: str | None = None,
    repo_label: str | None = None,
    seed_ref: str | None = None,
    branch_source: str | None = None,
    branch_setup_notice: str | None = None,
    host_context_branch: str | None = None,
    runtime_dir: str | None = None,
    context_path: str | None = None,
    recent_conversation: list[dict[str, Any]] | None = None,
    communication_snapshot: dict[str, Any] | None = None,
    kb_base_url: str | None = None,
    pending_events: list[dict[str, Any]] | None = None,
    present: list[dict[str, Any]] | None = None,
    event_body: str | None = None,
    event_attachments: list[Path] | None = None,
    budget_seconds: int | None = None,
    runner_medium: str | None = None,
    runner_quota: str | None = None,
    runner_catalog: list[dict[str, Any]] | None = None,
    runner_name: str | None = None,
    runner_shell: str | None = None,
    runner_core: str | None = None,
    body_provenance: str | None = None,
    source_gate: str | None = None,
    continuity: Any | None = None,
    hooks_installed: bool | None = None,
    diffense: bool = False,
    worker: bool = False,
    _prepared_injected_keyed: list[tuple[str, str]] | None = None,
    _mountable: frozenset[str] = frozenset(),
    _mount_sink: dict[str, str] | None = None,
    _prepared_introspection_block: str | None = None,
    _size_sink: dict[str, int] | None = None,
) -> str:
    """Build the prompt for daemon-originated runs.

    Same as the run prompt but with event metadata, recent conversation
    context, and an explicit delivery contract assembled into a single
    ``Run Context Bundle``.

    The daemon path also injects ``daemon-substrate.md`` — brr's driver's
    manual for the daemon-specific machinery (single-flight, capture net,
    self-scheduled wakes, the outbox/keepalive contract) that the
    host-agnostic playbook deliberately leaves out. ``brnrd run`` skips it:
    a one-shot has no daemon to fire schedules or drain an outbox.

    ``worker=True`` (B4, ``kb/design-director-loop.md`` §orchestrator/worker)
    swaps in the slim worker stack: ``worker.md`` + ``weave.md`` instead of
    the resident's ``run.md``, and the resident-only injected blocks
    (identity core, dominion digest, inter-run plan, runner policy, decision
    ledger, pitfalls, knowledge sources, kb health, introspection) are
    skipped entirely — a worker wake still gets ``daemon-substrate.md`` (it
    still runs under the daemon and needs the delivery/portal mechanics) and
    the full Run Context Bundle (its actual task). Default ``False`` is
    byte-identical to the prior behavior.
    """
    # A mounted block leaves the prose. It is not dropped — it arrives as a seeded
    # `Read` and its result (`transcript.py`), so the wake receives the same bytes
    # in a different grammatical position. Paying for it in *both* places would
    # double the wake and, worse, would make the T-vs-P experiment measure nothing:
    # both arms would carry the prose.
    def _take(key: str, text: str) -> str | None:
        if _mount_sink is None or key not in _mountable:
            return text
        _mount_sink[key] = text
        return None

    preamble = _glue_preamble([
        kept
        for key, text in _preamble_parts(repo_root, worker=worker)
        if (kept := _take(key, text)) is not None
    ])
    bundle = _build_run_context_bundle(
        event_id=event_id,
        response_path=response_path,
        outbox_path=outbox_path,
        budget_seconds=budget_seconds,
        runner_medium=runner_medium,
        runner_quota=runner_quota,
        runner_catalog=runner_catalog,
        repo_root=repo_root,
        run_id=run_id,
        source=source,
        environment=environment,
        branch_name=branch_name,
        repo_label=repo_label,
        seed_ref=seed_ref,
        branch_source=branch_source,
        branch_setup_notice=branch_setup_notice,
        host_context_branch=host_context_branch,
        runtime_dir=runtime_dir,
        context_path=context_path,
        recent_conversation=recent_conversation,
        communication_snapshot=communication_snapshot,
        kb_base_url=kb_base_url,
        pending_events=pending_events,
        present=present,
        event_body=event_body,
        event_attachments=event_attachments,
        diffense=diffense,
    )
    trailer = bundle.rstrip()
    if (event_body or "").strip() != task.strip():
        trailer = f"{trailer}\nRun instruction: {task}"

    # The action-first kernel (Slice 2).  Built from the same
    # :func:`build_boot_score` the daemon persists, so the block the wake reads
    # and the block the score describes cannot drift — ``contracts=[]`` because
    # the kernel names the *move*, not the map, and skipping the manifest scan
    # keeps this path as cheap as it was.
    from .bootscore import format_kernel

    kernel = format_kernel(build_boot_score(
        repo_root,
        is_daemon=True,
        is_worker=worker,
        runner_name=runner_name,
        runner_shell=runner_shell,
        runner_core=runner_core,
        body_provenance=body_provenance,
        source_gate=source_gate,
        continuity=continuity,
        environment=environment,
        event_ids=(event_id,) if event_id else (),
        pending_count=len(pending_events or []),
        budget=f"{budget_seconds // 60}m" if budget_seconds else None,
        quota=runner_quota,
        branch=branch_name,
        has_event_body=bool((event_body or task or "").strip()),
        contracts=[],
        hooks_installed=hooks_installed,
        # Derived from the *render*: `_mountable` is exactly the set of blocks
        # about to be subtracted from this prose and seeded as perceptions. Not
        # `cfg["boot.transcript"]` — a config key is a request, and the request can
        # be refused (Shell has no renderer, nothing to seed). When the mount fails,
        # the daemon rebuilds this whole prompt with no sink, `_mountable` is empty,
        # and the kernel silently tells the truth again.
        mounted=bool(_mountable),
    ))

    # Match pitfalls against the run instruction and the original event text — the
    # triggers the resident recorded tend to echo how a request is phrased.
    pitfall_text = "\n".join(t for t in (task, event_body) if t)
    prepared_blocks = (
        None
        if _prepared_injected_keyed is None
        else [
            kept
            for key, text in _prepared_injected_keyed
            if (kept := _take(key, text)) is not None
        ]
    )
    prompt = _join_prompt_parts(
        preamble, repo_root, trailer, kernel=kernel,
        task_text=pitfall_text, diffense=diffense,
        inject_blocks=not worker,
        prepared_injected_blocks=prepared_blocks,
        prepared_introspection_block=_prepared_introspection_block,
    )
    if _size_sink is not None:
        # Only what this function alone can measure: the bundle is computed
        # here and nowhere else, and the total must include the kernel.
        _size_sink["boot-kernel"] = _rendered_bytes(kernel)
        _size_sink["run-context-bundle"] = _rendered_bytes(trailer)
        _size_sink["_prompt"] = _rendered_bytes(prompt)
    return prompt


# ── Run Context Bundle internals ─────────────────────────────────────

# How many prior conversation records the prompt renders. The daemon reads
# a slightly larger window from the log so that records belonging to the
# in-flight event/run (filtered out before formatting) don't starve the
# tail. Keep the daemon's read cap = RECENT_CONVERSATION_MAX + headroom.
RECENT_CONVERSATION_MAX = 8


def _render_runner_catalog(
    catalog: list[dict[str, Any]] | None,
) -> list[str]:
    """Compact prompt rendering for the selectable Runner/Core mandate."""
    lines: list[str] = []
    for item in catalog or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        selected = bool(item.get("selected"))
        prefix = "selected " if selected else ""
        bits = [
            f"shell={item.get('shell') or 'unknown'}",
            f"core={item.get('model') or 'default'}",
        ]
        if item.get("class"):
            bits.append(f"class={item['class']}")
        if item.get("cost_rank") is not None:
            bits.append(f"cost_rank={item['cost_rank']}")
        if item.get("quota_source"):
            bits.append(f"quota={item['quota_source']}")
        if item.get("auth_variant"):
            bits.append(f"auth={item['auth_variant']}")
        # The catalog is pre-filtered to invokable profiles, so
        # ``availability=available`` on every line was pure noise; surface
        # the field only when it says something unusual.
        availability = str(item.get("availability") or "available")
        if availability != "available":
            bits.append(f"availability={availability}")
        lines.append(f"- {prefix}{name}: " + ", ".join(str(bit) for bit in bits))
    return lines


def _build_run_context_bundle(
    *,
    event_id: str,
    response_path: str,
    outbox_path: str | None = None,
    budget_seconds: int | None = None,
    runner_medium: str | None = None,
    runner_quota: str | None = None,
    runner_catalog: list[dict[str, Any]] | None = None,
    repo_root: Path,
    run_id: str | None,
    source: str | None,
    environment: str | None,
    branch_name: str | None,
    repo_label: str | None,
    seed_ref: str | None,
    branch_source: str | None,
    branch_setup_notice: str | None,
    host_context_branch: str | None,
    runtime_dir: str | None,
    context_path: str | None,
    recent_conversation: list[dict[str, Any]] | None,
    communication_snapshot: dict[str, Any] | None = None,
    kb_base_url: str | None = None,
    pending_events: list[dict[str, Any]] | None = None,
    present: list[dict[str, Any]] | None = None,
    event_body: str | None,
    event_attachments: list[Path] | None = None,
    diffense: bool = False,
) -> str:
    """Assemble the human-readable Run Context Bundle for the daemon prompt.

    The product model is a runner wake: one run can read and respond to
    more than one event, so this bundle frames the unit as a run.
    """
    sections: list[str] = ["---", "## Run Context Bundle"]
    sections.append("")
    sections.append(
        "_From the brnrd daemon: the runtime facts for *this* thought — run "
        "metadata, environment, and the delivery contract. Operational and "
        "per-thought, not durable memory (that's your dominion)._"
    )

    sections.append("")
    sections.append("### Mode")
    sections.append("- Stage: brnrd daemon run")
    if source:
        sections.append(f"- Source: {source}")
    if environment:
        environment_line = f"- Environment: {environment}"
        if environment == "host":
            environment_line += (
                " — shared checkout; host finalization does not publish "
                "commits. For work that must leave this machine, switch off "
                "the default branch and own the push / PR handoff."
            )
        sections.append(environment_line)
    if runner_medium:
        sections.append(
            f"- Runner: {runner_medium} — the Shell+Core this thought runs "
            "in. A failure here (quota exhausted, provider error) ⇒ the user "
            "pays a manual reroute, so chunk work and commit early when the "
            "budget is tight."
        )
        if runner_quota:
            sections.append(f"- Quota: {runner_quota}")
    sections.append(
        "- Delivery: situational outputs captured by brr "
        "(see Delivery contract below)"
    )
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
    mandate_lines = _render_runner_catalog(runner_catalog)
    if mandate_lines:
        sections.append("")
        sections.append("### Runner catalog")
        sections.append(
            "Selectable local Shell+Core profiles from the same catalog brr "
            "uses for cost-aware selection and respawn decisions:"
        )
        sections.extend(mandate_lines)

    sections.append("")
    sections.append("### Run")
    sections.append(f"- Event: {event_id}")
    if run_id:
        sections.append(f"- Run ID: {run_id}")
    sections.append(f"- Execution root: {repo_root}")
    if repo_label:
        sections.append(f"- Repo: {repo_label}")
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
    if diffense and run_id:
        # An absolute path in the *shared* runtime dir, not a cwd-relative
        # `.brr/...`: the runner works in a worktree whose own `.brr/` is
        # torn down at finalize, so a relative pack would die before the
        # resident can validate, project, and publish it through a forge
        # gate send.
        from . import gitops

        base = Path(runtime_dir) if runtime_dir else gitops.shared_brr_dir(repo_root)
        pack_path = base / "diffense" / run_id / "pack.json"
        sections.append(f"- Review pack path: {pack_path}")
    if context_path:
        sections.append(f"- Run context file: {context_path}")

    sections.append("")
    sections.append("### Delivery contract")
    sections.append(
        "Live values for this run's portals. Standing rules: §How the daemon "
        "drives you → delivery portals; full choreography: "
        "`brnrd docs portals`."
    )
    sections.append(
        f"- stdout capture: {response_path} (brnrd-written; final stdout = the "
        "one plain current-thread reply)"
    )
    if outbox_path:
        sections.append(
            f"- outbox: `{outbox_path}/` — one file = one mid-thought chat "
            "message; frontmatter routes (`event:` / `gate:` / `respawn:` / `spawn:`)"
        )
        sections.append(
            f"- inbox: `{outbox_path}/inbox.json` — re-read at plan / todo "
            "boundaries, and immediately before a terminal closeout"
        )
        sections.append(
            f"- portal state: `{outbox_path}/portal-state.json` (env "
            "`BRR_PORTAL_STATE`) — pending events, posture, `change_token`"
        )
        if kb_base_url:
            sections.append(
                f"- kb page URL base: {kb_base_url} — append the page path; "
                "link only after the knowledge commit is pushed"
            )
        if runner_medium == "codex":
            sections.append(
                "- codex Shell: native progress/final channels are "
                "runner-local under brr — user-visible mid-run communication "
                "goes through `.card` / outbox / `gate:`; stdout stays the "
                "plain current-thread fallback"
            )
        if budget_seconds:
            sections.append(
                f"- keepalive: `{outbox_path}/.keepalive` — first line "
                "ISO-8601 or `+<duration>` (`+30m`); rewrite to extend"
            )
        sections.append(
            f"- card: `{outbox_path}/.card` — note body only; rewrite as "
            "context shifts"
        )
    if branch_name and seed_ref:
        branch_line = (
            f"- branch: `{branch_name}` ⇐ `{seed_ref}` — commit here; brr "
            "publishes the branch you end on"
        )
        if branch_name.startswith("brr/"):
            branch_line += (
                "; themed work ⇒ rename to a descriptive `brr/<short-slug>` "
                "before committing"
            )
        sections.append(branch_line)

    inbox_block = _format_pending_events(pending_events)
    if inbox_block:
        sections.append("")
        sections.append("### Inbox — other pending events")
        sections.append(
            "Other events were waiting when you woke. Every listed event is "
            "yours to disposition: fold small/related work now, dispatch "
            "bounded independent work with `spawn:`, or explicitly defer for "
            "a resource, priority, dependency, or authority reason. Answer "
            "each original event via the outbox `event: <id>` route after "
            "the work or reviewed child result is ready. For the current "
            "list and surrounding run posture, "
            "read the live `portal-state.json` in your outbox at plan / todo "
            "boundaries; `inbox.json` remains the focused pending-event list."
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

    body = event_body.strip() if event_body is not None else ""
    if body or event_attachments:
        sections.append("")
        sections.append("### Original event body")
        sections.append("")
        if body:
            sections.append(body)
        if event_attachments:
            sections.append("")
            sections.append(
                "Attachments (local image files — open them with Read):"
            )
            sections.extend(f"- {p}" for p in event_attachments)

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
        tid = str(e.get("run_id") or "").strip()
        where = f" on `{stream}`" if stream else ""
        tag = f" (run {tid})" if tid else ""
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
    commitment = str(snapshot.get("user_commitment") or "").strip()
    if commitment == "full":
        lines.append(
            "- Reader model: `user_commitment: full` — this reader asked "
            "for the weave; replies may keep the register's density "
            "(coordinates, deltas, marks). Unfold only where meaning needs it."
        )
    elif commitment:
        lines.append(
            f"- Reader model: `user_commitment: {commitment}` — unfold "
            "replies into plain prose."
        )

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


def _format_pr_state(pr_state: Any) -> list[str]:
    """Lines for the PR-state cache: its trustworthiness, then homeless PRs.

    Reads the facet only — the cache behind it is filled by the daemon tick
    (:mod:`brr.forge_pr_cache`), so nothing here touches the network. An absent
    or failed cache says *unknown* out loud rather than rendering as "no PRs".
    """
    lines: list[str] = []
    note = forge_state.pr_state_note(pr_state)
    if note:
        lines.append(f"- {note}")
    if not isinstance(pr_state, dict):
        return lines
    standalone, omitted = forge_state.standalone_prs(pr_state)
    if standalone:
        lines.append("- PRs in flight or just resolved (no local worktree):")
        for pr in standalone:
            marker = forge_state.format_pr(pr)
            if not marker:
                continue
            branch = str(pr.get("branch") or "").strip()
            branch_bit = f" (`{branch}`)" if branch else ""
            # Link the open ones only: those are the actionable queue. A merged
            # PR's number and age already carry everything the wake needs.
            url = str(pr.get("url") or "").strip()
            link = (
                f" — {url}"
                if url and str(pr.get("state") or "").upper() == "OPEN"
                else ""
            )
            lines.append(f"  - {marker}{branch_bit}{link}")
        if omitted:
            noun = "resolution" if omitted == 1 else "resolutions"
            lines.append(f"  - {omitted} older {noun} in the last 24h omitted")
    return lines


def _format_forge_state(forge: Any) -> str:
    """Render the forge-state facet: in-flight worktrees + issues/PRs in play.

    Network-free local picture (co-maintainer §5): the resident's worktrees
    and unpushed work, the PR state cached beside each branch, and the GitHub
    threads its conversations are about. A branch's PR marker is the point of
    the block — a wake that *sees* ``#382 MERGED`` cannot go on claiming #382
    awaits review. Returns an empty string when the facet is absent or empty.
    """
    if not isinstance(forge, dict) or not forge:
        return ""
    lines: list[str] = ["Forge state (local, network-free):"]

    worktrees = forge.get("worktrees")
    worktree_summary = forge_state.summarize_worktrees(worktrees)
    if worktree_summary["total"]:
        bits = [f"{worktree_summary['total']} total"]
        if worktree_summary["unpushed_branches"]:
            branches = worktree_summary["unpushed_branches"]
            commits = worktree_summary["unpushed_commits"]
            commit_noun = "commit" if commits == 1 else "commits"
            bits.append(
                f"{branches} with unpushed commits ({commits} {commit_noun})"
            )
        if worktree_summary["dirty_branches"]:
            bits.append(f"{worktree_summary['dirty_branches']} dirty")
        if worktree_summary["current_branches"]:
            bits.append(f"{worktree_summary['current_branches']} current")
        lines.append(f"- Worktrees / branches: {'; '.join(bits)}")
        for wt in worktree_summary["attention"]:
            branch = str(wt.get("branch") or "").strip() or "(detached)"
            tid = str(wt.get("run_id") or "").strip()
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
            pr = forge_state.format_pr(wt.get("pr"))
            pr_marker = f" → {pr}" if pr else ""
            lines.append(f"  - `{branch}`{tag}{detail}{pr_marker}{link}")
        omitted = worktree_summary["omitted"]
        if omitted:
            noun = "branch" if omitted == 1 else "branches"
            lines.append(f"  - {omitted} clean pushed {noun} omitted")

    threads = forge.get("threads")
    has_threads = isinstance(threads, list) and bool(threads)
    if worktree_summary["total"] or has_threads:
        # Only speak about PR state when the block has a body at all — an
        # empty facet still renders as nothing.
        lines.extend(_format_pr_state(forge.get("pr_state")))

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
    path = None
    for candidate in dominion.resident_dominion_candidates(repo_root, cfg):
        if candidate.path.is_dir():
            path = candidate.path
            break
    if path is None:
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
    separately in the Run Context Bundle. Returns an empty string when
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
        elif kind == "run":
            tid = record.get("run_id", "")
            status = record.get("status") or "pending"
            branch = (
                record.get("publish_branch")
                or record.get("target_branch")
                or record.get("branch_name")
                or ""
            )
            line = f"- {ts} run {tid} status={status} branch={branch}"
        elif kind == "update":
            ptype = record.get("type") or ""
            tid = record.get("run_id") or ""
            stage = record.get("stage") or ""
            err = record.get("error") or ""
            bits = [f"- {ts} update {ptype}"]
            if tid:
                bits.append(f"run={tid}")
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
