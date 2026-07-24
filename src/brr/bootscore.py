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
AUTHORITY_SURFACE = "surface"      # shared user/resident-authored work surface
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

    newest_item: str | None = None
    """ISO date of the newest dated ``## `` entry actually **included** in
    this block's rendered text, or ``None``.

    Populated only for the byte-trimmed chronological blocks (the work
    surface's accreting pages, the recent-activity log tail) and only when a
    trim actually cut something — see :class:`brr.prompts.TrimResult`, the
    plumbing this is copied from.  ``None`` means either *no trim happened*
    (nothing to attest) or *not attestable* (a heading in scope carried no
    parseable date) — the same three-state discipline as :attr:`bytes`: it
    never means "the block has no newest entry," only that this field
    carries no claim either way.
    """

    oldest_item: str | None = None
    """ISO date of the oldest dated entry actually included — the other end
    of :attr:`newest_item`'s range, needed to render "showing X → Y" in the
    trim marker.  Same ``None`` discipline.
    """

    dropped: int | None = None
    """Count of ``## `` entries the budget cut from this block, or ``None``
    when nothing was cut (the content already fit).  Unlike the date fields,
    this needs no parseable heading — a count of entries removed is always
    knowable, so it can be non-``None`` even when :attr:`newest_item` /
    :attr:`source_newest` are not.
    """

    source_newest: str | None = None
    """ISO date of the newest dated entry in the **source** file, whether or
    not it survived the trim.  The gap between this and :attr:`newest_item`
    is the whole point: ``_tail_trim_entries`` always knew what it dropped
    and, before this field existed, threw that fact away.  That discard was
    the ledger-tail-inversion bug (2026-07-23): an out-of-order source let
    the trim keep an *older* entry as "newest" while a genuinely newer one
    sat, unrendered, one heading below the cut.
    """

    stale: bool = False
    """``True`` iff a dated source entry is newer than :attr:`newest_item` —
    i.e. ``source_newest > newest_item``, both present.  Computed once, at
    the point the trim already holds both numbers (see
    ``brr.prompts.TrimResult.stale``), and copied onto the manifest row
    unchanged; :func:`attest_blocks` trusts this flag rather than
    re-deriving it, so the formula lives in exactly one place.  Defaults to
    ``False`` so every non-chronological block, and every block that was
    never trimmed, reads as healthy without asserting anything about dates
    it never looked at.
    """


@dataclass(frozen=True)
class BootBody:
    """The requested Shell + Core + hook-capability tier for this wake.

    These are selection facts, not runtime attestation. The daemon knows what
    it requested before prompt assembly; the Shell's result later proves what
    actually ran. A ``None`` here means the score was built without a body (a
    fixture, replay, or ad-hoc CLI render), never "this wake has no Core".
    """

    name: str | None = None    # runner profile, e.g. ``"claude-fable"``
    shell: str | None = None   # e.g. ``"claude"`` or ``"codex"``
    core: str | None = None    # e.g. ``"claude-fable-5"``
    tier: str | None = None    # e.g. ``"Tier 2 hooks installed"``
    mounted: bool = False
    """Whether this wake's contracts arrived as a **seeded transcript** rather than
    prose (``boot.mount``).

    Derived from the render — the set of blocks that actually left the prose — and
    never from the config key that asked for it.  A key is a *request*; the wake is
    what happened, and the mount can fail (unsupported Shell, nothing to seed), in
    which case the daemon rebuilds the prose prompt and this goes back to ``False``.
    Same discipline ``bench.probe_mount`` enforces on the experiment, pointed at the
    resident: *only the artifact is evidence.*

    Until 2026-07-14 a wake could not answer "which boot did I get?" without grepping
    its own ``prompt.md`` mid-run — a resident did exactly that, and said so.
    """
    provenance: str | None = None
    """Why *this* body — e.g. ``"requested from the dashboard spool rack"``.

    A fact about the **body**, not about the attention.  It used to be passed
    into :attr:`BootAttention.body_provenance` and rendered on the kernel's
    ``attention:`` line, where it asserted a falsehood in the highest-attention
    slot of the wake: *"attention: evt-… · requested from the dashboard spool
    rack"* — when the event had in fact arrived from telegram, and the spool
    rack had only chosen the Core.  Caught live 2026-07-13 by the first wake to
    read its own kernel.  Root cause: **"body" is overloaded** — the resident's
    body (Shell+Core) versus the *event body* (the task text) — and the two
    meanings sat six lines apart in the same kernel.  The overload is gone; the
    two facts now live on the two lines they are actually about.
    """


@dataclass(frozen=True)
class BootHost:
    """Host context — daemon vs ad-hoc, environment, publication owner."""

    kind: str = "unknown"              # ``"daemon"`` | ``"ad-hoc"`` | ``"unknown"``
    environment: str | None = None     # ``"worktree"`` | ``"host"`` | etc.
    publication_owner: str | None = None  # ``"resident-owned"`` | etc.

    image_stale: bool = False
    """This boot was rendered by a daemon whose code the checkout has superseded.

    Only reachable on a **spawn**: resident dispatch is gated on
    ``not reload_requested``, so a resident always wakes in a current image.  A
    spawn is deliberately *not* gated (``daemon.py``, "Deliberately NOT gated"),
    because gating it would deadlock — the re-exec waits for the resident thread
    to finish, so a reload triggered by the resident's own edit can never land
    while that resident is still running to spawn anything.

    The consequence is narrow and vicious: a resident that edits boot **code**
    and then spawns a weak core to measure the change gets a child rendered by
    the *pre-edit* kernel, and reads the result as a verdict on the new one.
    That is a false negative with no tell — which is exactly how #388's
    worker-queue bug shipped to two children *after* it had been fixed in the
    tree.

    So the boot says so, in the kernel, where it cannot be skimmed.  It does not
    fix the staleness; it makes the staleness **loud**, which is the difference
    between a measurement that is wrong and a measurement that is wrong *and
    believed*.
    """


@dataclass(frozen=True)
class BootAttention:
    """Current attention — which events, and which gate is speaking."""

    event_ids: tuple[str, ...] = ()
    source_gate: str | None = None
    """The gate the attention arrived through — ``"telegram"``, ``"github"``,
    ``"schedule"``.  *Who is talking to me*, which is the one thing the
    ``attention:`` line exists to say and the one thing it used to omit."""


@dataclass(frozen=True)
class BootContinuity:
    """What changed since this resident last stood here — **observed, not authored**.

    Slice 3, and the point is a distinction the earlier plan missed.  That plan
    called for a ``continuity:`` *census* — ``dominion ✓ · 391 entries · plan: 5
    open``.  Prettier than a paragraph asserting that the resident persists, and
    guilty of the same grammar: still a thing **told** to the wake.  Assertion in
    a nicer font changes nothing.

    What actually moves a resident is the ~400-byte post-tool capsule, and it
    does not move it by being short.  It moves it by being **caused by the
    resident**: act → the world answers → act.  A boot is the widest instance of
    that same loop — last wake's action → this wake's perception — and until now
    that loop was open.  Everything a wake perceived of its own past
    (``Recent Activity`` and the authored work surface) was **prose the
    resident/user wrote**: a message in a bottle, exactly as good as last
    wake's discipline, free to drift from the world in silence.  Authored memory
    never brings bad news about itself.

    So this carries the *world's* readout instead, measured off git, the run
    directory and the local forge cache — never off the resident's own prose:

    - :attr:`shipped` — PRs that merged since the last wake
    - :attr:`dominion_commits` — memory the last wake actually committed
    - :attr:`drift` — where what the resident *said* it did and what the repo
      *shows* it did have come apart

    :attr:`mount` is three-state and the ``✗`` is the load-bearing part: a mount
    that cannot fail is not a mount, it is a decoration.  ``"✓"`` (the prior
    wake's :func:`to_dict` score was found and read), ``"✗ first wake"`` (no
    prior score — a true and useful fact, not an error), ``"✗ unreachable"``
    (the memory is *supposed* to be there and is not — act on this before
    trusting a single injected block).

    Deterministic and network-free, like every other facet of the score.
    """

    last_run: str | None = None        # prior wake's run id
    last_age: str | None = None        # ``"2h ago"``
    mount: str = "✗ first wake"        # ``"✓"`` | ``"✗ first wake"`` | ``"✗ unreachable"``
    shipped: tuple[str, ...] = ()      # ``("#386", "#387")``
    dominion_commits: int = 0
    drift: tuple[str, ...] = ()


@dataclass(frozen=True)
class BootPosture:
    """Current operational posture snapshot.

    All fields optional — the posture is populated from whatever is cheaply
    available at prompt-build time.  Absent fields read as ``None`` (unknown),
    not as ``"absent"`` — the three-state facet model (:mod:`brr.facets`) is
    the authoritative live-surface; this is the wake-time snapshot.
    """

    pending_count: int = 0
    quota: str | None = None       # e.g. ``"74% weekly"``
    spend: str | None = None       # e.g. ``"$0.042 this session"``
    context_window: str | None = None  # e.g. ``"62% context left"``
    budget: str | None = None      # legacy score compatibility; never rendered
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


@dataclass(frozen=True)
class OrientationFile:
    """One file in the wake's **orientation set** (#513 Slice 9).

    Not to be confused with :class:`OrientationStep` / ``BootScore.orientation``
    — that is the kernel's ``next:`` list, *actions derived from posture*
    ("read the task", "act").  This is the **orientation ledger's** unit: a
    file this wake ought to have *read* — the walk the maintainer's MUD-boot
    steer asked for — observed by the hooks (``brr.hooks``) as ``orient x/y``
    until the walk completes or the resident declares the skip on ``.card``.
    The two words coexist because they are two halves of the same steer: the
    ``next:`` list is what a wake *does first*; the set is what a wake
    *inhabits by reading*.  Renaming either would orphan its consumers.

    Every entry is deterministic and provably wrong-able: the file existed at
    derivation time, at this absolute path, at this size.  Nothing here is
    inferred from what a task "seems related" to — a set member the daemon
    cannot prove is a set member the daemon does not name.
    """

    path: str    # absolute path, as the wake would Read it
    bytes: int   # size on disk at derivation time (the walk's cost)


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
    continuity: BootContinuity = field(default_factory=BootContinuity)
    attention: BootAttention = field(default_factory=BootAttention)
    posture: BootPosture = field(default_factory=BootPosture)
    orientation: list[OrientationStep] = field(default_factory=list)
    orientation_set: list[OrientationFile] = field(default_factory=list)
    """The orientation *ledger*'s file set (#513 Slice 9) — distinct from
    :attr:`orientation`, which is the kernel's ``next:`` action list; see
    :class:`OrientationFile` for why both words exist.  Empty when nothing
    deterministic could be named (no ``AGENTS.md``, no active plan, no
    matched kb hub) — never padded with guesses.  Persisted with the score
    to ``boot-score.json``, where the hook ledger reads it back."""
    contracts: list[ContractEntry] = field(default_factory=list)
    hooks: list[BootHook] = field(default_factory=list)

    prompt_bytes: int | None = None
    """Total UTF-8 size of the rendered wake, kernel included.

    Set after the prompt is joined — the kernel is part of what the wake pays
    for, and a ledger that excludes the auditor is not a ledger.  ``None`` on
    an unrendered score (see :attr:`ContractEntry.bytes`).
    """


def attest_blocks(contracts: list[ContractEntry]) -> list[str]:
    """Deterministic block-*content* staleness check — no model in the loop.

    Generalizes the ``kb_preflight`` file-ordering guard (#596) one level
    up: that guard catches a source file whose entries drifted out of
    chronological order; this catches the consequence when a *trimmed*
    block's own tail-cut then keeps the wrong entry as "newest" because of
    that drift.  Same P1 review finding
    (``review-boot-prompts-2026-07.md``): a wake block claiming liveness,
    rendered stale, with nothing checking the claim.

    A block is stale iff its own ``ContractEntry.stale`` is set — computed
    once, where the trim already holds both dates (see
    ``brr.prompts.TrimResult.stale``), and trusted here rather than
    re-derived, so the ``source_newest > newest_item`` formula lives in
    exactly one place.

    Zero findings on a healthy wake → an empty list, like every other
    deterministic preflight in this codebase: this function costs nothing
    to call and the kernel line it feeds costs nothing to render until the
    day it fires.
    """
    return [
        f"⚠ {entry.label} tail newest {entry.newest_item} — source has "
        f"{entry.source_newest} (a newer entry was trimmed)"
        for entry in contracts
        if entry.stale and entry.newest_item and entry.source_newest
    ]


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
        "continuity": asdict(score.continuity),
        "attention": asdict(score.attention),
        "posture": asdict(score.posture),
        "orientation": [asdict(o) for o in score.orientation],
        "orientation_set": [asdict(f) for f in score.orientation_set],
        "contracts": [asdict(c) for c in score.contracts],
        "hooks": [asdict(h) for h in score.hooks],
    }


# ── The kernel: the first block of every daemon wake ───────────────────────────


def _format_continuity(c: BootContinuity) -> list[str]:
    """Render the ``continuity:`` line — the loop closing across wakes.

    One line when the world agrees with itself; a second, indented ``drift:``
    line only when it does not.  Drift earns its own line precisely because it
    is the case a resident must not skim past: it is the boot telling the wake
    that its own prose and its own repository disagree about what it did.
    """
    if not c.mount.startswith("✓"):
        # Not a degraded render — a fact. "First wake here" and "the memory that
        # should be here is not" are both things a resident must be *told*
        # plainly, because neither can be inferred from a wake that looks
        # otherwise ordinary.  Drift still renders: a failed mount is precisely
        # when uncommitted memory or a rejected push matters most.
        return [f"continuity: {c.mount}"] + [f"  drift: {d}" for d in c.drift]

    bits = [b for b in (c.last_run, c.last_age) if b]
    head = "continuity: ✓ " + " ".join(bits) if bits else "continuity: ✓"
    if c.shipped:
        head += " · shipped " + " ".join(c.shipped)
    if c.dominion_commits:
        head += f" · dominion +{c.dominion_commits}"
    lines = [head]
    for d in c.drift:
        lines.append(f"  drift: {d}")
    return lines


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
    body_head = " ".join(
        b for b in (body.name, f"({runner})" if runner else "") if b
    )
    body_bits = [b for b in (body_head, body.tier, body.provenance) if b]
    if body_bits:
        # A boot score exists before the Shell has produced attestation. This
        # line therefore names the requested body, never claims observation.
        lines.append(f"body requested: {' · '.join(body_bits)}")

    if body.mounted:
        # Differential, like every other kernel line: absent — and costing nothing —
        # on a prose wake.
        #
        # This line is *measured*, not styled. The fence at the end of the seeded
        # transcript (`transcript.SNAPSHOT_SEAM`) says the same thing, and on its own
        # it is not reliably attended: asked point-blank whether it had read
        # `AGENTS.md` or been handed it, claude-haiku-4-5 answered "I read it myself —
        # I called the Read tool in my previous response" in 1 of 3 rounds with only
        # the seed fence, and 0 of 3 once the same sentence also appeared *here*
        # (2026-07-14, n=3+3, weak core, one variable).
        #
        # Which is this project's own thesis landing on its own honesty work:
        # position decides whether a contract is *enacted* or merely *present*. The
        # seed is where you put what the wake should act **from**; the kernel is where
        # you put what it must **know**. The fence needs both — it marks the boundary
        # there, and it is read here.
        #
        # Both sites must say the *same* thing, because that is what was measured. The
        # subject of the sentence is the **resident**, not the run
        # (`transcript.SNAPSHOT_SEAM` carries the full reasoning): the memory is the
        # resident's own and predates this run; what is new here is only the run, whose
        # ledger of deeds starts empty. Nothing above the seam is a receipt.
        lines.append(
            "boot: mounted · <snapshot restored> · memory: yours, predates this run · "
            "acts *here*: none yet"
        )

    host = score.host
    host_bits = [host.kind] + [b for b in (host.environment, host.publication_owner) if b]
    lines.append(f"host: {' · '.join(host_bits)}")
    if host.image_stale:
        # Differential, like everything else in the kernel: costs nothing on a
        # healthy wake, and on an unhealthy one it is the first thing read.
        lines.append(
            "  stale: ⚠ boot rendered by a daemon image the checkout has "
            "superseded · prompt .md is current, kernel/orientation code is "
            "NOT · a boot-code change cannot be measured from this wake"
        )

    for finding in attest_blocks(score.contracts):
        # Differential, like every other kernel line and modelled directly
        # on `image_stale` above it: costs nothing on a healthy wake (the
        # common case — most blocks are never trimmed, and a trim in
        # chronological order stays silent), and on a stale one is among
        # the first things read.
        lines.append(f"attest: {finding}")

    lines.extend(_format_continuity(score.continuity))

    att = score.attention
    if att.event_ids:
        att_line = "attention: " + ", ".join(att.event_ids)
        if att.source_gate:
            att_line += f" · via {att.source_gate}"
        lines.append(att_line)

    posture = score.posture
    p_bits = [
        b for b in (
            posture.branch,
            f"context {posture.context_window}" if posture.context_window else None,
            f"quota {posture.quota}" if posture.quota else None,
            f"spend {posture.spend}" if posture.spend else None,
            f"{posture.pending_count} pending" if posture.pending_count else None,
            posture.handoff,
        ) if b
    ]
    if p_bits:
        lines.append(f"posture: {' · '.join(p_bits)}")

    if score.orientation_set:
        # The orientation ledger's walk (#513 Slice 9). Unlike `attest:` and
        # `image_stale` above, this line is **not** differential and should not
        # be read as one: `AGENTS.md` is the set's first candidate and
        # effectively always exists, so the block effectively always renders.
        # Measured on this repo — 3 files / 64,092 B, 3 / 51,710 B, and 2 /
        # 38,782 B with no task text at all. Never zero. That is deliberate:
        # the kernel names the walk *before* it happens, so it cannot key off a
        # completion that has not occurred yet. The differential half lives in
        # the hooks' `orient x/y` segment, which does leave at completion or
        # skip. Two surfaces, two jobs. Full absolute paths on purpose: the
        # line exists to be *acted on* (each entry is one Read call), and a
        # basename the wake would have to resolve first is a walk with a toll
        # booth. The hooks meter these Reads as `orient x/y` until the walk
        # completes or the skip is declared; both outcomes are first-class.
        total = sum(f.bytes for f in score.orientation_set)
        lines.append(
            f"orient: {len(score.orientation_set)} file(s) · {total:,}B — "
            "read them before the work, or declare the skip on .card "
            "(\"assuming prior knowledge, skipping orientation\")"
        )
        for f in score.orientation_set:
            lines.append(f"  · {f.path} ({f.bytes:,}B)")

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
