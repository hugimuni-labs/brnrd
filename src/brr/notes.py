"""The registry of durable writing surfaces a resident maintains.

A resident writes into ~18 distinct surfaces across four roots — the
run's own control files, its dominion, the account work surface, the
knowledge base. **Every one of them has a grammar, and until this module
every grammar was enforced nowhere and described in whichever page
happened to use it.** A resident that writes the wrong key gets silence:
the surface narrows and renders as if it hadn't. Three failures on
record, all from that one class:

- a pitfall spelled ``**Trigger:**`` where :func:`brr.pitfalls.parse_pitfalls`
  reads ``trigger:`` — 62 minutes of authoring, nine days inert, filed
  perfectly (#985);
- an injected surface silently over its byte ceiling, losing its bottom
  sections *every wake for a month*, announced only inside the wake that
  already paid for it (#1020);
- ``workflow.md`` §Signatures declaring a staleness predicate and its own
  enforcement, with nothing implementing it.

This module is the answer to "self-descriptive": **the grammar of a
surface becomes a fact code can hand you, not prose you must find.** It
is deliberately a *pointer* table, not a second copy of the prose — each
entry names the module that actually parses the surface, so the registry
cannot drift into being a prettier lie about the same file. Where
``src/brr/prompts/daemon-substrate.md``'s control-file table already
states a grammar for a wake reader, the registry points at it and the
code beside it rather than restating it.

Two consumers, one table: :mod:`brr.notes_preflight` (the deterministic
checks that ride the wake beside ``kb health``) and ``brnrd notes`` (the
drill-down map). Nothing here reads a model or writes a file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


# ── Roots ────────────────────────────────────────────────────────────
#
# Four physical places a durable surface can live, in the order a
# resident meets them: the run it is inside, the memory that outlives the
# run, the two-party work surface, the shared knowledge base. The order
# is the reporting order — ``brnrd notes`` groups by it.

ROOT_INTRA_RUN = "intra-run"
ROOT_DOMINION = "dominion"
ROOT_SURFACE = "work-surface"
ROOT_KNOWLEDGE = "knowledge"

ROOT_ORDER: tuple[str, ...] = (
    ROOT_INTRA_RUN, ROOT_DOMINION, ROOT_SURFACE, ROOT_KNOWLEDGE,
)

ROOT_BLURBS: dict[str, str] = {
    ROOT_INTRA_RUN: (
        "the run's own outbox — written this wake, captured at closeout, "
        "gone as a live surface when the run ends"
    ),
    ROOT_DOMINION: (
        "resident memory — survives every run, injected on wake, "
        "committed by the capture net"
    ),
    ROOT_SURFACE: (
        "the account work surface — authored by resident *and* maintainer, "
        "injected under a shared byte budget"
    ),
    ROOT_KNOWLEDGE: (
        "the shared knowledge base — many readers, long shelf life, "
        "governed by AGENTS.md"
    ),
}


# ── Lifetimes ────────────────────────────────────────────────────────

LIFETIME_RUN = "run"          # dies with the thought (captured, not live)
LIFETIME_DURABLE = "durable"  # outlives every run


@dataclass(frozen=True)
class Surface:
    """One durable writing surface, and everything code knows about it.

    The fields answer the four questions a resident actually has when it
    is about to write into a surface it half-remembers:

    - *where does this live* — :attr:`root` plus the resolver behind
      :attr:`key`;
    - *what is it for* — :attr:`role`, one line;
    - **who reads it** — :attr:`readers`, as code coordinates
      (``module.function``) or a named human, never "the system";
    - *what shape must I write* — :attr:`grammar`, one line, with
      :attr:`parser` naming the code that will actually read the bytes.

    :attr:`budget` is the one field that is usually ``None``: most
    surfaces have no ceiling. When it is set, it names the ceiling *and*
    the rule, because a byte count with no eviction order is not
    something a resident can act on before writing (#1020).
    """

    key: str
    root: str
    path_hint: str
    role: str
    readers: tuple[str, ...]
    grammar: str
    parser: str | None = None
    lifetime: str = LIFETIME_DURABLE
    budget: str | None = None
    #: The wake block this surface's content rides in, when it rides at all
    #: (``None`` = read on demand, never injected). A *block* key, not a
    #: per-surface byte cost: several surfaces share one block and one
    #: budget, so ``brnrd notes`` reports the block's measured cost once
    #: rather than repeating it against every row that contributed to it.
    rides: str | None = None
    #: Grammar families a checker keys off — ``signatures``,
    #: ``trigger-keyed``, ``h2-sections``, … A check asks for the
    #: surfaces carrying its trait rather than hard-coding a filename,
    #: so adding a second signed page enrols it in the signature check
    #: with no edit to the check.
    traits: tuple[str, ...] = ()


# ── The table ────────────────────────────────────────────────────────
#
# Ordered within each root the way a resident meets the surfaces, not
# alphabetically. `path_hint` is the *shape* of the path (the resolver
# below turns it into a real one for this account/repo); it is what
# `brnrd notes` prints when nothing on disk resolves, so it stays
# readable on its own.

_REGISTRY: tuple[Surface, ...] = (
    # ── intra-run: the outbox control files ──
    Surface(
        key="card",
        root=ROOT_INTRA_RUN,
        path_hint="<outbox>/.card",
        role="the run-body write-head — live projection plus the run's arc",
        readers=("brr.card.now_projection", "brr.course", "dashboard", "maintainer"),
        grammar=(
            "Markdown. `## Now` is the compact live projection; a "
            "`## Plan` / `## Course` checkbox section is the course"
        ),
        parser="brr.card",
        lifetime=LIFETIME_RUN,
        budget="`card.CARD_TEXT_MAX_CHARS` chars for the projected text",
        traits=("h2-sections",),
    ),
    Surface(
        key="mood",
        root=ROOT_INTRA_RUN,
        path_hint="<outbox>/.mood",
        role="emote chip plus private narration; rides statusline, run node, dashboard",
        readers=("brr.emotes.lookup", "brr.statusline", "dashboard"),
        grammar="first line an emote handle, lines after are narration",
        parser="brr.emotes",
        lifetime=LIFETIME_RUN,
    ),
    Surface(
        key="name",
        root=ROOT_INTRA_RUN,
        path_hint="<outbox>/.name",
        role="the run's short resident-authored name",
        readers=("brr.daemon", "dashboard"),
        grammar="first line, <=60 chars",
        lifetime=LIFETIME_RUN,
    ),
    Surface(
        key="pr",
        root=ROOT_INTRA_RUN,
        path_hint="<outbox>/.pr",
        role="the PR this run created",
        readers=("brr.relics", "brr.forge_state"),
        grammar="the PR **URL** (without it `remote_scm` reads `absent`)",
        parser="brr.relics",
        lifetime=LIFETIME_RUN,
    ),
    Surface(
        key="keepalive",
        root=ROOT_INTRA_RUN,
        path_hint="<outbox>/.keepalive",
        role="outlast the run budget",
        readers=("brr.portals.keepalive_until",),
        grammar="first line ISO-8601 or `+<duration>` (`+30m`)",
        parser="brr.portals.keepalive_until",
        lifetime=LIFETIME_RUN,
    ),
    Surface(
        key="promises",
        root=ROOT_INTRA_RUN,
        path_hint="<outbox>/.promises.jsonl",
        role="the blueprint — what this run said it would make",
        readers=("brr.promises.read", "brr.promises.blueprint"),
        grammar="one JSON object per line; write via `brnrd promise`",
        parser="brr.promises",
        lifetime=LIFETIME_RUN,
    ),
    Surface(
        key="relics",
        root=ROOT_INTRA_RUN,
        path_hint="<outbox>/.relics.jsonl",
        role="the produce manifest — what this run actually made",
        readers=("brr.relics", "dashboard", "maintainer"),
        grammar="one JSON object per line; write via `brnrd relic`",
        parser="brr.relics",
        lifetime=LIFETIME_RUN,
    ),
    Surface(
        key="menu",
        root=ROOT_INTRA_RUN,
        path_hint="<outbox>/menu.json",
        role="the live menu the correspondent answers by handle",
        readers=("brr.menus.validate_menu", "brr.menus.promote_menu"),
        grammar=(
            "one composed generation, written atomically: `menu_id` "
            "(immutable per generation), `thread`, `options[]`"
        ),
        parser="brr.menus.validate_menu",
        lifetime=LIFETIME_RUN,
    ),
    # ── dominion: resident memory ──
    Surface(
        key="playbook",
        rides="dominion",
        root=ROOT_DOMINION,
        path_hint="<dominion>/playbook.md",
        role="the resident's standing self-orientation, injected every wake",
        readers=("brr.dominion.resolve_self_inject_digest", "the resident, on wake"),
        grammar="Markdown, **ordered most-important-first** — invariants at the top",
        parser="brr.dominion",
        budget=(
            "shares `dominion.DEFAULT_INJECT_BUDGET_BYTES` "
            "with every other self-inject entry; over budget, `## ` sections "
            "collapse **bottom-up** (`dominion._collapse_markdown_to_budget`)"
        ),
        traits=("h2-sections", "budgeted-inject"),
    ),
    Surface(
        key="pitfalls",
        rides="dominion",
        root=ROOT_DOMINION,
        path_hint="<dominion>/pitfalls.md",
        role="trigger-indexed failure memory, matched against the waking text",
        readers=("brr.pitfalls.parse_pitfalls", "brr.pitfalls.match"),
        grammar=(
            "one `## ` heading per pitfall; a `trigger: a, b` line "
            "**at line start, lowercase key, plain colon** anywhere in the "
            "block. No trigger line => parses, renders, and matches nothing"
        ),
        parser="brr.pitfalls.parse_pitfalls",
        traits=("trigger-keyed", "h2-sections"),
    ),
    Surface(
        key="schedule",
        root=ROOT_DOMINION,
        path_hint="<dominion>/schedule.md",
        role="self-wake entries — future thoughts the daemon wakes instead of a user",
        readers=("brr.schedule", "brr.daemon"),
        grammar=(
            "one entry per `## ` heading with `at:` / `every:` and optional "
            "`shell:` / `core:` keys"
        ),
        parser="brr.schedule",
        traits=("h2-sections",),
    ),
    Surface(
        key="self-inject",
        rides="dominion",
        root=ROOT_DOMINION,
        path_hint="<dominion>/self-inject",
        role="the manifest deciding what dominion material rides every wake",
        readers=("brr.dominion.resolve_self_inject_digest",),
        grammar=(
            "one `<mode> <path>` entry per line, ordered by importance; "
            "modes `full` | `head:N` | `tail:N` | `grep:<pattern>`; "
            "`#` comments and blank lines ignored"
        ),
        parser="brr.dominion.resolve_self_inject_digest",
        budget=(
            "`dominion.DEFAULT_INJECT_BUDGET_BYTES` total; entries past it "
            "are accounted, not silently dropped — order by importance"
        ),
        traits=("budgeted-inject", "manifest"),
    ),
    Surface(
        key="thread-of-record",
        rides="dominion",
        root=ROOT_DOMINION,
        path_hint="<dominion>/thread-of-record.md",
        role="durable project-level narrative that survives across channels",
        readers=("the resident", "the maintainer"),
        grammar="free Markdown; brnrd points at the slot and never mutates it",
    ),
    # ── work surface: the two-party pages ──
    Surface(
        key="workflow",
        rides="work-surface",
        root=ROOT_SURFACE,
        path_hint="<surface>/workflow.md",
        role="the account's one genuine two-party contract",
        readers=("brr.prompts._build_work_surface_block_scored", "the maintainer"),
        grammar=(
            "`## ` sections; §Signatures carries four-key records — "
            "`signed-by:` / `date:` / `scope:` / `basis:` — shaped so a "
            "preflight can parse them without a model. Text no signature "
            "covers is a proposal, not an agreement"
        ),
        parser="brr.notes_preflight.parse_signatures",
        budget=(
            "reserved floor `prompts._SURFACE_RESERVE_PAGE_BYTES` out of the "
            "shared `dominion.surface_inject_budget_bytes`"
        ),
        traits=("signatures", "h2-sections", "budgeted-inject"),
    ),
    Surface(
        key="surface-index",
        rides="work-surface",
        root=ROOT_SURFACE,
        path_hint="<surface>/index.md",
        role="the work surface's entry point; leads the orientation order",
        readers=("brr.account.work_surface_files", "dashboard"),
        grammar="Markdown; `index.md` sorts first, everything else by name",
        traits=("budgeted-inject",),
    ),
    Surface(
        key="warp-items",
        rides="work-surface",
        root=ROOT_SURFACE,
        path_hint="<surface>/warp/*.md",
        role="the work-item graph: one item per file, needs: rows as edges",
        readers=(
            "brr.items.render_index",
            "brr.weld",
            "frontend warpGraph.ts",
        ),
        grammar=(
            "one `# ` title, a recognized-row block (type/topics/needs/"
            "done/retired/refs/prompt/taken), then free body; lifecycle "
            "derives from the receipt rows"
        ),
        parser="brr.items.parse_item",
        budget=(
            "never enters the surface page walk — the wake carries the "
            "composed open-items index (`items.render_index`) instead"
        ),
        traits=(),
    ),
    Surface(
        key="topics",
        rides="work-surface",
        root=ROOT_SURFACE,
        path_hint="<surface>/topics/*.md",
        role="the filter axis: topic definitions with alias id sets",
        readers=("frontend warpGraph.ts",),
        grammar="one `# ` title, `ids:`/`split-into:` rows, definition body",
        budget="excluded from the surface page walk, same as warp items",
        traits=(),
    ),
    Surface(
        key="active-plan",
        rides="work-surface",
        root=ROOT_SURFACE,
        path_hint="<surface>/plans/<repo-slug>/active.md",
        role="the resident's own ranked queue for this repo",
        readers=("brr.account.active_plan_path", "brr.prompts"),
        grammar="`## ` sections, structural (undated headings) — not a log",
        budget=(
            "reserved floor `prompts._SURFACE_RESERVE_PAGE_BYTES`, same "
            "reserve as `workflow.md`"
        ),
        traits=("h2-sections", "budgeted-inject"),
    ),
    Surface(
        key="decisions-ledger",
        rides="work-surface",
        root=ROOT_SURFACE,
        path_hint="<surface>/ledger/decisions.md",
        role="the account's decision ledger",
        readers=("brr.account.decisions_ledger_path", "brr.prompts"),
        grammar=(
            "**every heading dated** => read as chronological; newest matters, "
            "so the wake carries a tail, not the head"
        ),
        parser="brr.prompts._page_is_chronological",
        budget=(
            "capped at `prompts._MAX_ACCRETING_BLOCK_BYTES` because it "
            "accretes without bound"
        ),
        traits=("chronological", "budgeted-inject"),
    ),
    # ── knowledge: the shared kb ──
    Surface(
        key="kb-index",
        rides="knowledge-sources",
        root=ROOT_KNOWLEDGE,
        path_hint="<kb>/index.md",
        role="the kb graph's entry point, organised by subject hub",
        readers=("brr.kb_preflight.scan", "brr.knowledge.render_injection"),
        grammar="Markdown sections of links; every page should be reachable from here",
        parser="brr.kb_preflight",
        traits=("h2-sections",),
    ),
    Surface(
        key="kb-log",
        rides="recent-activity",
        root=ROOT_KNOWLEDGE,
        path_hint="<kb>/log.md",
        role="the curated chronological narrative",
        readers=("brr.kb_preflight._check_log_ordering", "brr.prompts._read_recent_log"),
        grammar="`## [YYYY-MM-DD] <type> | <title>`, newest appended at the **bottom**",
        parser="brr.kb_preflight",
        budget=(
            "the wake carries a fixed-byte tail (`prompts._MAX_LOG_BYTES`), so "
            "fat entries silently narrow how many entries of continuity a "
            "reader gets — aim <=1,500 B per entry"
        ),
        traits=("chronological",),
    ),
    Surface(
        key="kb-pages",
        rides="knowledge-sources",
        root=ROOT_KNOWLEDGE,
        path_hint="<kb>/{subject,design,decision,plan,research,review}-*.md",
        role="current-state synthesis — the semantic and decisional layer",
        readers=("brr.kb_preflight.scan", "brr.kb_health.compute_graph_stats", "brnrd kb"),
        grammar=(
            "type prefix in the filename; `plan-` / `design-` / `decision-` "
            "carry a top-of-page `Status:` line; pages are **rewritten** to "
            "current state, never grown as running diffs"
        ),
        parser="brr.kb_preflight",
        traits=("lifecycle-marker",),
    ),
)


def registry() -> tuple[Surface, ...]:
    """Every registered surface, in reporting order."""
    return _REGISTRY


def get(key: str) -> Surface | None:
    """The surface registered under *key*, or ``None``."""
    for surface in _REGISTRY:
        if surface.key == key:
            return surface
    return None


def with_trait(trait: str) -> tuple[Surface, ...]:
    """Surfaces carrying *trait*.

    This is the join a check should use instead of naming a file. A
    second signed page enrols itself in the signature check by declaring
    ``signatures`` here — no edit to the check, and no silent divergence
    between "what the check covers" and "what the class contains".
    """
    return tuple(s for s in _REGISTRY if trait in s.traits)


def traits() -> tuple[str, ...]:
    """Every trait any registered surface declares, sorted."""
    seen: set[str] = set()
    for surface in _REGISTRY:
        seen.update(surface.traits)
    return tuple(sorted(seen))


# ── Resolution ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Resolved:
    """A registry entry joined with what is actually on disk right now.

    ``paths`` is a list because three entries are globs by nature
    (``warp/*.md``, the kb page types, and — in principle — a
    multi-dominion install). ``bytes`` and ``mtime`` aggregate over it;
    ``exists`` is ``False`` when the glob matched nothing, which is a
    legitimate state for most surfaces and never on its own a finding.
    """

    surface: Surface
    paths: tuple[Path, ...] = ()
    bytes: int = 0
    mtime: float | None = None
    note: str = ""

    @property
    def exists(self) -> bool:
        return bool(self.paths)


def _outbox_dir(explicit: Path | None = None) -> Path | None:
    """This run's outbox directory, or ``None`` outside a wake.

    ``BRR_PORTAL_STATE`` names ``<outbox>/portal-state.json`` and is the
    one variable present in every daemon-hosted wake, so its parent is
    the outbox. Outside a wake there is no outbox and the intra-run
    surfaces resolve to nothing — which is the honest answer, not a
    failure.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get("BRR_PORTAL_STATE")
    if not raw:
        return None
    parent = Path(raw).parent
    return parent if parent.is_dir() else None


def _glob(root: Path | None, pattern: str) -> list[Path]:
    if root is None or not root.is_dir():
        return []
    return sorted(p for p in root.glob(pattern) if p.is_file())


def _one(root: Path | None, name: str) -> list[Path]:
    if root is None:
        return []
    path = root / name
    return [path] if path.is_file() else []


@dataclass
class _Roots:
    """The four resolved roots, each ``None`` when not available here.

    ``None`` is load-bearing, not a shrug. A check that runs over a root
    it never located reports *clean* about surfaces it never read — and a
    clean verdict about an empty set is the exact silent-narrowing this
    module exists to end, one layer out. :func:`unresolved_roots` is how
    that state gets said; :attr:`account_enabled` and :attr:`home_root`
    are what let it be said *precisely*, naming the home this invocation
    actually resolved rather than the one the reader assumes.
    """

    outbox: Path | None = None
    dominion: Path | None = None
    dominion_label: str = ""
    surface: Path | None = None
    kb: Path | None = None
    repo_label: str = ""
    account_enabled: bool = False
    home_root: Path | None = None


#: The roots whose absence is worth reporting. ``intra-run`` is excluded on
#: purpose: it resolves from ``BRR_PORTAL_STATE``, which exists only inside
#: a wake, so a CLI invocation legitimately has no outbox and saying so
#: every time would be a guard firing for a non-reason.
DURABLE_ROOTS: tuple[str, ...] = (ROOT_DOMINION, ROOT_SURFACE, ROOT_KNOWLEDGE)


#: A durable root reported by :func:`unresolved_roots`.
ROOT_MISSING = "missing"   # the directory itself did not resolve
ROOT_EMPTY = "empty"       # it resolved, and holds none of its surfaces


def unresolved_roots(
    roots: _Roots, rows: list["Resolved"] | None = None,
) -> list[tuple[str, str, str]]:
    """``(root, state, where it looked)`` for each durable root the scan
    could not actually read.

    Two states, because a root fails to be readable in two ways and only
    one of them looks like an error from the filesystem:

    - :data:`ROOT_MISSING` — the directory is not there.
    - :data:`ROOT_EMPTY` — it *is* there and holds **none** of its
      registered surfaces. This is the one that matters, and it is the
      fingerprint of #1193: a bare read command scaffolds a fresh project
      home keyed on ``sha1(repo_root)``, every root resolves inside it, and
      every check then runs over an empty directory and reports clean.
      Measured 2026-08-07 — a run inside a worktree resolved
      ``projects/…-fc376f3ff6/home`` instead of the account home, located 6
      of 22 surfaces, and said nothing about it.

    Zero, not a threshold: "this root holds none of the five things it is
    defined by" is provable, and a resident can check it with one ``ls``.
    A ratio would be a guess about how empty is too empty, and a guard
    built on a guess is one that fires for a non-reason.

    Empty when brnrd is not configured for this repo at all — there is no
    claim to fall short of, and a repo that never adopted brnrd does not
    want a line about its missing dominion. *rows* is optional so a caller
    that only has roots can still get the ``missing`` half.
    """
    if not roots.account_enabled:
        return []
    home = str(roots.home_root) if roots.home_root else "<unresolved home>"
    resolved_dir = {
        ROOT_DOMINION: roots.dominion,
        ROOT_SURFACE: roots.surface,
        ROOT_KNOWLEDGE: roots.kb,
    }
    populated: set[str] = set()
    if rows is not None:
        populated = {r.surface.root for r in rows if r.exists}
        # **Nothing written anywhere yet is not a blind spot.** A fresh
        # adopter's first wake has no dominion, no surface, no kb — because
        # it has not written one, not because this scan failed to find one
        # — and an `error` on day one is a guard firing for a non-reason on
        # the one wake least able to judge it. The honest report in that
        # state is the *denominator* (``N of M surfaces located``), which
        # `Scope` carries unconditionally, not an accusation.
        #
        # What discriminates #1193 is that the account is demonstrably in
        # use — at least one durable root holds real material — while
        # another comes back empty. That is the account whose clean
        # verdict is a lie.
        if not (populated & set(DURABLE_ROOTS)):
            return []

    out: list[tuple[str, str, str]] = []
    for root in DURABLE_ROOTS:
        where = resolved_dir[root]
        if where is None:
            expected = {
                ROOT_DOMINION: f"no resident dominion directory under {home}",
                ROOT_SURFACE: f"{home}/surface",
                ROOT_KNOWLEDGE: f"no kb resolved for this repo (home: {home})",
            }[root]
            out.append((root, ROOT_MISSING, expected))
        elif rows is not None and root not in populated:
            out.append((root, ROOT_EMPTY, str(where)))
    return out


def _dominion_filenames() -> tuple[str, ...]:
    """The basenames of every registered ``dominion`` surface.

    Derived from the table rather than listed twice, so a surface added
    there enrols in the dominion-candidate preference automatically —
    a second list would drift, and a drifted list here means resolving
    the wrong dominion silently.
    """
    return tuple(
        s.path_hint.rsplit("/", 1)[-1]
        for s in _REGISTRY if s.root == ROOT_DOMINION
    )


def located_counts(resolved: list["Resolved"]) -> tuple[int, int]:
    """``(surfaces with something on disk, surfaces registered)``.

    The denominator behind every clean verdict. A "clean" that does not
    carry it is a claim about surfaces that may never have been read.
    """
    return sum(1 for r in resolved if r.exists), len(resolved)


def resolve_roots(
    repo_root: Path,
    cfg: dict[str, Any] | None = None,
    *,
    outbox_dir: Path | None = None,
) -> _Roots:
    """Resolve the four physical roots for *repo_root*.

    Every lookup is best-effort and independently failable: a repo with
    no account home still resolves an outbox and a repo-committed kb, and
    a plain editor session with no wake still resolves the dominion. A
    root that cannot be resolved is ``None``, never a raise — this
    function is called from the wake path, where an exception would cost
    the whole block.
    """
    roots = _Roots(outbox=_outbox_dir(outbox_dir))
    if cfg is None:
        try:
            from . import config as conf

            cfg = conf.load_config(repo_root)
        except Exception:
            cfg = {}

    try:
        from . import dominion as dominion_mod

        # The first candidate that holds a **registered dominion surface**,
        # falling back to the first that merely exists. The readers this
        # mirrors do the same: the wake's `_build_dominion_block` walks
        # candidates until one yields a digest — not until one is a
        # directory — and the pitfall block walks all of them. Stopping at
        # the first directory points this registry at an empty
        # account-root (which is a real candidate, and on a live account
        # holds `repos/` and `surface/` without holding a single dominion
        # file) while the store the wake actually reads sits one candidate
        # down: a map of the wrong dominion, which is the failure it
        # exists to report.
        #
        # The filenames come from the registry itself, so a surface added
        # to the table enrols in this preference with no edit here.
        wanted = _dominion_filenames()
        fallback = None
        for candidate in dominion_mod.resident_dominion_candidates(repo_root, cfg):
            if not candidate.path.is_dir():
                continue
            if fallback is None:
                fallback = candidate
            if any((candidate.path / name).exists() for name in wanted):
                fallback = candidate
                break
        if fallback is not None:
            roots.dominion = fallback.path
            roots.dominion_label = fallback.label
    except Exception:
        pass

    try:
        from . import account as acc

        roots.repo_label = acc.repo_label(repo_root, cfg)
        ctx = acc.resolve_context(repo_root, cfg, create=False)
        roots.account_enabled = bool(ctx.enabled)
        if ctx.enabled:
            roots.home_root = acc.context_home_root(ctx)
            surface = acc.work_surface_path(ctx)
            if surface.is_dir():
                roots.surface = surface
    except Exception:
        pass

    try:
        from . import knowledge

        kb = knowledge.active_kb_dir(repo_root, cfg)
        if kb is not None and Path(kb).is_dir():
            roots.kb = Path(kb)
    except Exception:
        pass

    return roots


#: How each registry key finds its files, given the resolved roots. Kept
#: beside the table rather than inside :class:`Surface` so the table stays
#: a *declaration* — readable end to end without following a callable.
_RESOLVERS: dict[str, Callable[[_Roots], list[Path]]] = {
    "card": lambda r: _one(r.outbox, ".card"),
    "mood": lambda r: _one(r.outbox, ".mood"),
    "name": lambda r: _one(r.outbox, ".name"),
    "pr": lambda r: _one(r.outbox, ".pr"),
    "keepalive": lambda r: _one(r.outbox, ".keepalive"),
    "promises": lambda r: _one(r.outbox, ".promises.jsonl"),
    "relics": lambda r: _one(r.outbox, ".relics.jsonl"),
    "menu": lambda r: _one(r.outbox, "menu.json"),
    "playbook": lambda r: _one(r.dominion, "playbook.md"),
    "pitfalls": lambda r: _one(r.dominion, "pitfalls.md"),
    "schedule": lambda r: _one(r.dominion, "schedule.md"),
    "self-inject": lambda r: _one(r.dominion, "self-inject"),
    "thread-of-record": lambda r: _one(r.dominion, "thread-of-record.md"),
    "workflow": lambda r: _one(r.surface, "workflow.md"),
    "surface-index": lambda r: _one(r.surface, "index.md"),
    "warp-items": lambda r: _glob(r.surface, "warp/*.md"),
    "topics": lambda r: _glob(r.surface, "topics/*.md"),
    "active-plan": lambda r: _active_plan(r),
    "decisions-ledger": lambda r: _one(r.surface, "ledger/decisions.md"),
    "kb-index": lambda r: _one(r.kb, "index.md"),
    "kb-log": lambda r: _one(r.kb, "log.md"),
    "kb-pages": lambda r: _kb_pages(r),
}

_KB_PAGE_PREFIXES = (
    "subject-", "design-", "decision-", "plan-", "research-", "review-",
)


def _active_plan(roots: _Roots) -> list[Path]:
    if roots.surface is None or not roots.repo_label:
        return []
    from . import account as acc

    slug = acc.slug_repo_label(roots.repo_label)
    return _one(roots.surface, f"plans/{slug}/active.md")


def _kb_pages(roots: _Roots) -> list[Path]:
    if roots.kb is None:
        return []
    return sorted(
        p for p in roots.kb.rglob("*.md")
        if p.is_file() and p.name.startswith(_KB_PAGE_PREFIXES)
    )


def resolve(
    repo_root: Path,
    cfg: dict[str, Any] | None = None,
    *,
    outbox_dir: Path | None = None,
    keys: Iterable[str] | None = None,
) -> list[Resolved]:
    """Join the registry against this account/repo's actual filesystem.

    Returns one :class:`Resolved` per registry entry, in registry order,
    whether or not anything is on disk for it. Restrict with *keys* when
    you only want a few.

    **Every registry key must have a resolver.** A key with none is a
    registry entry nothing can ever locate — the same silent-narrowing
    class this module exists to end — so it resolves to a ``Resolved``
    carrying an explicit ``note`` rather than an empty path list that
    reads identically to "the file isn't there".
    """
    return resolve_with_roots(
        repo_root, cfg, outbox_dir=outbox_dir, keys=keys,
    )[0]


def resolve_with_roots(
    repo_root: Path,
    cfg: dict[str, Any] | None = None,
    *,
    outbox_dir: Path | None = None,
    keys: Iterable[str] | None = None,
) -> tuple[list[Resolved], _Roots]:
    """:func:`resolve`, plus the roots it resolved against.

    Callers that need to say *what the rows are a claim about* — a clean
    verdict's denominator, an unresolved root — need the roots, not just
    the rows. Handing back only the rows is how "nothing on disk" and
    "nowhere to look" become the same empty answer.
    """
    roots = resolve_roots(repo_root, cfg, outbox_dir=outbox_dir)
    wanted = set(keys) if keys is not None else None
    out: list[Resolved] = []
    for surface in _REGISTRY:
        if wanted is not None and surface.key not in wanted:
            continue
        resolver = _RESOLVERS.get(surface.key)
        if resolver is None:
            out.append(Resolved(
                surface=surface,
                note="no resolver registered — this entry can never be located",
            ))
            continue
        try:
            paths = tuple(resolver(roots))
        except Exception as exc:  # a broken root must not cost the block
            out.append(Resolved(surface=surface, note=f"unresolvable: {exc}"))
            continue
        total = 0
        newest: float | None = None
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            newest = stat.st_mtime if newest is None else max(newest, stat.st_mtime)
        out.append(Resolved(
            surface=surface, paths=paths, bytes=total, mtime=newest,
        ))
    return out, roots


def unresolvable_keys() -> tuple[str, ...]:
    """Registry keys with no resolver — always empty in a healthy tree.

    The registry's own sanity assertion, exposed so a test can hold it
    and ``brnrd notes`` can say so out loud instead of rendering a row
    that looks like a missing file.
    """
    return tuple(s.key for s in _REGISTRY if s.key not in _RESOLVERS)
