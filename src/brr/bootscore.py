"""BootScore — typed intermediate representation for the wake prompt assembly.

Introduced in ``design-native-boot-sequence.md`` Slice 1.  A ``BootScore`` is
assembled as a side-product of every prompt build.  It names every block that
enters — or was considered for — the wake, together with its owner, authority
layer, freshness marker, and location.  This makes the assembly inspectable
and testable without changing what any wake reads.

Schema version: 1

The representation contains **facts and pointers, not generated
interpretations**.  No LLM calls; no inferred intent.  Explicit commitments
may enter only when they already exist as authored structure: event ids,
resident plan/ledger entries, operator policy.

One source model feeds:

- the daemon prompt (rendered unchanged; BootScore is the inspectable middle).
  Every daemon wake persists its score to ``.brr/runs/<run-id>/boot-score.json``,
  beside the ``prompt.md`` it explains — the structured half of "what did this
  wake see?";
- the ``brnrd prompts show`` CLI;
- the boot replay test harness.

A field lands here only with a consumer that reads it.  Slice 2's orientation
kernel and Slice 4's worked depth arrive with their renderers, not before —
an unpopulated field in an IR whose selling point is *facts* is just a claim.

Slice 2 will render the compact kernel at the prompt's hot edge.
Slice 3 will extend the ``SessionStart`` capsule from the BootScore.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# ── Schema version ────────────────────────────────────────────────────────────

SCHEMA_VERSION = "1"

# ── Authority layers (ordered most → least authoritative) ─────────────────────

AUTHORITY_CONTRACT = "contract"    # product-owned operational contract (run.md, weave.md)
AUTHORITY_IDENTITY = "identity"    # product-owned resident identity
AUTHORITY_SUBSTRATE = "substrate"  # product-owned daemon mechanics (daemon-substrate.md)
AUTHORITY_POLICY = "policy"        # operator-approved runner policy
AUTHORITY_MEMORY = "memory"        # resident working memory (dominion, pitfalls)
AUTHORITY_PLAN = "plan"            # resident active inter-run plan (CS5)
AUTHORITY_LEDGER = "ledger"        # resident decision ledger (CS7)
AUTHORITY_KNOWLEDGE = "knowledge"  # project + home knowledge sources
AUTHORITY_ACTIVITY = "activity"    # daemon-live recent activity log tail
AUTHORITY_HEALTH = "health"        # daemon-live kb health scan
AUTHORITY_RUNTIME = "runtime"      # daemon-live Run Context Bundle
AUTHORITY_CONFIG = "config"        # per-repo/account config toggles (diffense, introspect)

# ── Block owners ──────────────────────────────────────────────────────────────

OWNER_PRODUCT = "product"          # brr ships it; per-repo `.brr/prompts/` overrides accepted
OWNER_RESIDENT = "resident"        # resident authors it (dominion, plan, ledger, pitfalls)
OWNER_PROJECT = "project"          # project team authors it (knowledge, AGENTS.md)
OWNER_DAEMON_LIVE = "daemon-live"  # computed fresh each wake by the daemon

# ── Depth levels ──────────────────────────────────────────────────────────────

DEPTH_COMPACT = "compact"   # hot score + source manifest + relevant injected slice (Slice 1)


# ── IR dataclasses ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContractEntry:
    """One block's entry in the prompt source manifest.

    Every block that enters — or was considered for — the rendered wake
    registers itself here.  ``present=False`` means the block is in scope for
    this prompt type but was absent this run (file missing, dominion empty,
    config toggle off, etc.).  The distinction between *silent* absence and
    *configured* silence matters for ``brnrd prompts show``.
    """

    block_key: str          # stable slug, e.g. ``"identity-core"``
    label: str              # human-readable name for display
    owner: str              # OWNER_* constant
    authority: str          # AUTHORITY_* constant
    freshness: str | None   # ISO mtime, revision marker, or ``None``
    location: str           # absolute file path or ``"computed"``
    present: bool           # ``True`` iff this block appeared in the current prompt


@dataclass(frozen=True)
class BootBody:
    """The Shell + Core + hook-capability tier for this wake."""

    shell: str | None = None   # e.g. ``"claude"`` or ``"codex"``
    core: str | None = None    # e.g. ``"claude-sonnet-4-6"``
    tier: str | None = None    # e.g. ``"Tier 2 hooks installed"``


@dataclass(frozen=True)
class BootHost:
    """Host context — daemon vs ad-hoc, environment, publication owner."""

    kind: str = "unknown"              # ``"daemon"`` | ``"ad-hoc"`` | ``"unknown"``
    environment: str | None = None     # ``"worktree"`` | ``"host"`` | etc.
    publication_owner: str | None = None  # ``"resident-owned"`` | etc.


@dataclass(frozen=True)
class BootAttention:
    """Current attention — event ids and body provenance."""

    event_ids: tuple[str, ...] = ()
    body_provenance: str | None = None   # e.g. ``"cloud/telegram"``


@dataclass(frozen=True)
class BootPosture:
    """Current operational posture snapshot.

    All fields optional — the posture is populated from whatever is cheaply
    available at prompt-build time.  Absent fields read as ``None`` (unknown),
    not as ``"absent"`` — the three-state facet model (:mod:`brr.facets`) is
    the authoritative live-surface; this is the wake-time snapshot.
    """

    pending_count: int = 0
    budget: str | None = None      # e.g. ``"120m"``
    quota: str | None = None       # e.g. ``"74% weekly"``
    branch: str | None = None      # e.g. ``"brr/my-work"``
    handoff: str | None = None     # e.g. ``"no PR recorded"``
    delivery_state: str | None = None


@dataclass(frozen=True)
class BootHook:
    """One hook's declared / installed / last-fired state.

    ``declared`` means the daemon's abstract phase contract names it.

    ``installed`` is **three-state**, and the distinction is the point:
    ``True`` (wired), ``False`` (this Shell cannot take the config), and
    ``None`` (*unknown from here* — asked outside a wake with no Shell named).
    Collapsing unknown into "not installed" is how a live hook reports itself
    dead to the one operator looking; ``absent ≠ unknown ≠ off`` is the same
    rule the credits panel learned on 2026-07-13.

    ``last_fired`` is an ISO timestamp written by :mod:`brr.hooks` when the
    phase actually fires, or ``None`` when it has not fired in this run.
    """

    name: str                        # e.g. ``"session-start"``
    declared: bool = False           # named in the daemon's abstract phase set
    installed: bool | None = False   # True | False | None (unknown from here)
    last_fired: str | None = None    # ISO timestamp, or None (never fired here)


@dataclass
class BootScore:
    """Typed intermediate representation assembled alongside the prompt.

    This is the inspectable middle between versioned prompt sources and the
    rendered text the wake sees.  Assembly and rendering are now two phases
    with an explicit intermediate; the rendered output is unchanged.

    Slice 1 produces only ``DEPTH_COMPACT``; worked depth (per-Shell execution
    traces) arrives in Slice 4, with the renderer that reads it.
    """

    schema_version: str = SCHEMA_VERSION
    depth: str = DEPTH_COMPACT

    body: BootBody = field(default_factory=BootBody)
    host: BootHost = field(default_factory=BootHost)
    attention: BootAttention = field(default_factory=BootAttention)
    posture: BootPosture = field(default_factory=BootPosture)
    contracts: list[ContractEntry] = field(default_factory=list)
    hooks: list[BootHook] = field(default_factory=list)


# ── Serialization ─────────────────────────────────────────────────────────────


def to_dict(score: BootScore) -> dict:
    """Serialize a score to a plain dict — the one shape both consumers get.

    Used by ``brnrd prompts show --json`` *and* by the per-run
    ``boot-score.json`` the daemon persists.  One function, so the operator's
    view and the run's record cannot quietly disagree about what a wake read.
    The earlier CLI-local serializer silently dropped ``attention`` and
    ``posture`` — facts the text view was printing all along.
    """
    return {
        "schema_version": score.schema_version,
        "depth": score.depth,
        "body": asdict(score.body),
        "host": asdict(score.host),
        "attention": asdict(score.attention),
        "posture": asdict(score.posture),
        "contracts": [asdict(c) for c in score.contracts],
        "hooks": [asdict(h) for h in score.hooks],
    }


# ── Rendering ─────────────────────────────────────────────────────────────────


def format_manifest(score: BootScore) -> str:
    """Render the boot source manifest as human-readable text for the CLI.

    Used by ``brnrd prompts show [--boot]``.  Deterministic and network-free.
    Groups blocks by authority layer; marks absent blocks explicitly so
    operators can see what *would* appear in a real wake.
    """
    lines: list[str] = [
        f"brnrd boot · schema v{score.schema_version} · depth {score.depth}",
        "",
    ]

    # Body / Host summary
    body = score.body
    if body.shell or body.core or body.tier:
        runner_parts = [p for p in (body.shell, body.core) if p]
        tier_str = f" · {body.tier}" if body.tier else ""
        if runner_parts:
            runner_str = " / ".join(runner_parts)
            lines.append(f"  body: {runner_str}{tier_str}")
        elif body.tier:
            lines.append(f"  body: unknown{tier_str}")

    host = score.host
    host_line = f"host: {host.kind}"
    if host.environment:
        host_line += f" · {host.environment}"
    if host.publication_owner:
        host_line += f" · {host.publication_owner}"
    lines.append(f"  {host_line}")

    posture = score.posture
    if posture.quota or posture.branch or posture.pending_count:
        p_bits = []
        if posture.quota:
            p_bits.append(posture.quota)
        if posture.branch:
            p_bits.append(posture.branch)
        if posture.pending_count:
            p_bits.append(f"{posture.pending_count} pending")
        lines.append(f"  posture: {' · '.join(p_bits)}")

    lines.append("")

    # Hooks
    if score.hooks:
        lines.append("hooks:")
        for hook in score.hooks:
            flags = ["declared" if hook.declared else "undeclared"]
            if hook.installed is None:
                flags.append("installed=unknown (ask inside a wake, or pass --runner)")
            else:
                flags.append("installed" if hook.installed else "not-installed")
            fired = hook.last_fired or "not fired here"
            lines.append(f"  {hook.name}: {', '.join(flags)}; last-fired={fired}")
        lines.append("")

    # Source manifest.  Column widths are derived from the content: a label
    # wider than its pad used to shunt every later column out of alignment.
    lines.append("source manifest:")
    label_w = max([len(e.label) for e in score.contracts] + [len("block")]) + 2
    owner_w = max([len(e.owner) for e in score.contracts] + [len("owner")]) + 2
    auth_w = max([len(e.authority) for e in score.contracts] + [len("authority")]) + 2
    header = (
        f"    {'block':<{label_w}}{'owner':<{owner_w}}{'authority':<{auth_w}}"
        "location / freshness"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for entry in score.contracts:
        # The presence mark *is* the present column — it leads the row.
        present_mark = "✓" if entry.present else "·"
        loc = entry.location
        if entry.freshness:
            loc = f"{loc}  [{entry.freshness}]"
        lines.append(
            f"  {present_mark} {entry.label:<{label_w}}{entry.owner:<{owner_w}}"
            f"{entry.authority:<{auth_w}}{loc}"
        )

    lines.append("")
    lines.append("  ✓ present in this wake · · in scope but silent (absent source or toggle off)")
    lines.append("")
    lines.append(
        f"depth: {score.depth} · "
        "refs available via `brnrd prompts show` / `brnrd kb <query>`"
    )
    return "\n".join(lines)
