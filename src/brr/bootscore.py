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

A field lands here only with a consumer that reads it.  Slice 4's worked depth
arrives with its renderer, not before — an unpopulated field in an IR whose
selling point is *facts* is just a claim.

Slice 2 (2026-07-13) added the two fields whose consumers now exist:

- ``orientation`` — the ordered next-actions rendered by :func:`format_kernel`
  into the *first* block of every daemon wake.  Deterministically derived from
  posture; no inferred intent.
- ``bytes`` / ``prompt_bytes`` — the cost ledger.  The score named which blocks
  were present but never what they cost, so the compact/worked depth call had
  no evidence to stand on and the resident had to shell out to ``wc -c`` to
  answer "what does this wake weigh?".  A manifest without a cost column is a
  table of contents pretending to be an invoice.

Slice 3 will extend the ``SessionStart`` capsule from the BootScore.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

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
    bytes: int | None = None
    """Rendered size of this block **in this wake**, in UTF-8 bytes.

    Measured at render time from the text that actually entered the prompt —
    not from the file on disk.  For a trimmed block (a log tail, a dominion
    digest cut to the wake budget) those two numbers differ by a lot, and the
    one that costs attention is this one.

    ``None`` means *not measured* — a score assembled without rendering, e.g.
    ``brnrd prompts show`` on a repo it is only inspecting.  It never means
    zero: an absent block is ``present=False`` with ``bytes=0``, and the
    difference between "weighs nothing" and "was never weighed" is the whole
    reason this is three-state.
    """


@dataclass(frozen=True)
class BootBody:
    """The Shell + Core + hook-capability tier for this wake.

    These are the *resolved* runner facts, not the display label the prompt
    prints.  The daemon knows them before it builds a line of prompt — it
    writes them into ``run.md`` in the same second — so a ``None`` here means
    the score was built without a body (a fixture, a replay, an ad-hoc CLI
    render), never "this wake has no Core".  A score that cannot name the body
    it is scoring is not an inspection.
    """

    name: str | None = None    # runner profile, e.g. ``"claude-fable"``
    shell: str | None = None   # e.g. ``"claude"`` or ``"codex"``
    core: str | None = None    # e.g. ``"claude-fable-5"``
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


@dataclass(frozen=True)
class OrientationStep:
    """One ordered next-action in the boot kernel.

    **Pulls, not pushes.**  A step names an action the resident performs and
    the reason it is worth performing; it does not carry the content.  Every
    executed read converts cold injected prose into a hot tool-result at a
    boundary edge — the highest-attention position the loop has — so the
    cheapest way to make orientation land is to let it be *fetched* rather
    than shipped (``design-native-boot-sequence.md`` §maintainer steer).

    Derived deterministically from posture.  A step is a fact about the wake
    ("2 events are queued", "this checkout does not publish itself"), never an
    inference about what the resident intends to do about it.
    """

    action: str
    reason: str = ""


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
    orientation: list[OrientationStep] = field(default_factory=list)
    contracts: list[ContractEntry] = field(default_factory=list)
    hooks: list[BootHook] = field(default_factory=list)

    prompt_bytes: int | None = None
    """Total UTF-8 size of the rendered wake, kernel included.

    Set after the prompt is joined — the kernel is part of what the wake pays
    for, and a ledger that excludes the auditor is not a ledger.  ``None`` on
    an unrendered score (see :attr:`ContractEntry.bytes`).
    """


def replace_bytes(entry: ContractEntry, size: int) -> ContractEntry:
    """Stamp a measured size onto a frozen manifest row.

    Two blocks can only be weighed by the renderer that produced them — the
    kernel it builds and the Run Context Bundle it computes.  Everything else
    measures itself where it is built.
    """
    return replace(entry, bytes=size)


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
        "prompt_bytes": score.prompt_bytes,
        "body": asdict(score.body),
        "host": asdict(score.host),
        "attention": asdict(score.attention),
        "posture": asdict(score.posture),
        "orientation": [asdict(o) for o in score.orientation],
        "contracts": [asdict(c) for c in score.contracts],
        "hooks": [asdict(h) for h in score.hooks],
    }


# ── The kernel: the first block of every daemon wake ───────────────────────────


def format_kernel(score: BootScore) -> str:
    """Render the compact, action-first boot kernel.

    This is Slice 2's core move, and it is a move about *position* before it is
    a move about content.  The most-attended real estate in a 73 KB wake is its
    opening, and until now that slot held a paragraph of standing prose that is
    byte-identical in every wake a resident has ever had.  The kernel takes it:
    what body, what room, what is being asked, what is owed, what to do first.

    Modelled on the one part of the wake that demonstrably *moves* a resident —
    the ~400-byte post-tool portal capsule (:func:`brr.hooks.format_delta`).
    Three properties, copied deliberately:

    - **differential** — it says what is true *now*, not what is always true;
    - **imperative, with a required disposition** — ``next:`` is a list of
      actions, not a list of facts;
    - **at the choice point** — first thing read, last thing before the scroll
      it indexes.

    The verbatim event body is *not* duplicated here.  It stays exactly where
    it is, once, at the prompt's other hot edge (the end).  The kernel points
    at it; a wake that reads the pointer and the body twice has been taxed, not
    oriented.

    Facts and pointers only — no LLM, no inferred intent, deterministic.
    """
    lines: list[str] = [f"brnrd boot · score v{score.schema_version} · depth {score.depth}"]

    body = score.body
    runner = " / ".join(p for p in (body.shell, body.core) if p)
    body_bits = [b for b in (body.name, f"({runner})" if runner else "", body.tier) if b]
    if body_bits:
        lines.append(f"body: {' '.join(body_bits)}")

    host = score.host
    host_bits = [host.kind] + [b for b in (host.environment, host.publication_owner) if b]
    lines.append(f"host: {' · '.join(host_bits)}")

    att = score.attention
    if att.event_ids:
        att_line = "attention: " + ", ".join(att.event_ids)
        if att.body_provenance:
            att_line += f" · {att.body_provenance}"
        lines.append(att_line)

    posture = score.posture
    p_bits = [
        b for b in (
            posture.branch,
            posture.quota,
            f"budget {posture.budget}" if posture.budget else None,
            f"{posture.pending_count} pending" if posture.pending_count else None,
            posture.handoff,
        ) if b
    ]
    if p_bits:
        lines.append(f"posture: {' · '.join(p_bits)}")

    if score.orientation:
        lines.append("next:")
        for i, step in enumerate(score.orientation, 1):
            suffix = f" — {step.reason}" if step.reason else ""
            lines.append(f"  {i}. {step.action}{suffix}")

    lines.append(
        "below: reference · `brnrd prompts show` names every block and its cost"
    )
    return "\n".join(lines)


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
    if body.name or body.shell or body.core or body.tier:
        # Name the resolved Shell+Core, not just the profile label: the label
        # is what was *asked for*, the shell/core pair is what was *issued*.
        # Those two have diverged in production before (a core pin silently
        # dropped, the wake running a stronger body than the one requested),
        # and the divergence is invisible unless both are printed.
        runner_parts = [p for p in (body.shell, body.core) if p]
        detail = f" ({' / '.join(runner_parts)})" if runner_parts else ""
        tier_str = f" · {body.tier}" if body.tier else ""
        label = body.name or (" / ".join(runner_parts) if runner_parts else None)
        if label and body.name and detail:
            lines.append(f"  body: {label}{detail}{tier_str}")
        elif label:
            lines.append(f"  body: {label}{tier_str}")
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
        f"{'bytes':>9}  location / freshness"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for entry in score.contracts:
        # The presence mark *is* the present column — it leads the row.
        present_mark = "✓" if entry.present else "·"
        loc = entry.location
        if entry.freshness:
            loc = f"{loc}  [{entry.freshness}]"
        # "—" is *not measured*; "0" is measured and empty.  Same three-state
        # rule the rest of this module lives by.
        size = "—" if entry.bytes is None else f"{entry.bytes:,}"
        lines.append(
            f"  {present_mark} {entry.label:<{label_w}}{entry.owner:<{owner_w}}"
            f"{entry.authority:<{auth_w}}{size:>9}  {loc}"
        )

    lines.append("")
    lines.append("  ✓ present in this wake · · in scope but silent (absent source or toggle off)")

    # The cost ledger.  Without it the compact/worked depth call has no
    # evidence and the operator is left running `wc -c` by hand.
    ledger = _cost_ledger(score)
    if ledger:
        lines.append("")
        lines.extend(ledger)

    lines.append("")
    lines.append(
        f"depth: {score.depth} · "
        "refs available via `brnrd prompts show` / `brnrd kb <query>`"
    )
    return "\n".join(lines)


def _cost_ledger(score: BootScore) -> list[str]:
    """Bytes by authority layer, plus the share of the wake each one takes.

    Silent when nothing was measured — an unrendered score should not print a
    table of dashes and call it an invoice.
    """
    measured = [e for e in score.contracts if e.present and e.bytes]
    if not measured:
        return []

    total = score.prompt_bytes or sum(e.bytes or 0 for e in measured)
    by_authority: dict[str, int] = {}
    for entry in measured:
        by_authority[entry.authority] = by_authority.get(entry.authority, 0) + (entry.bytes or 0)

    lines = ["cost ledger:"]
    for authority, size in sorted(by_authority.items(), key=lambda kv: -kv[1]):
        share = f"{100 * size / total:4.1f}%" if total else "   ?"
        lines.append(f"  {authority:<12}{size:>9,} B  {share}")
    accounted = sum(by_authority.values())
    if score.prompt_bytes:
        # Joins, the kernel, and anything the manifest does not itemize.  Named
        # rather than silently absorbed: a ledger whose columns don't add up to
        # the bill is how you learn to stop trusting the ledger.
        lines.append(f"  {'unattributed':<12}{total - accounted:>9,} B")
        lines.append(f"  {'wake total':<12}{total:>9,} B")
    return lines
