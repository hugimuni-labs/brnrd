"""Deterministic health checks for the resident's own note surfaces.

The pair this mirrors is :mod:`brr.kb_preflight` / :mod:`brr.kb_health`,
and the contract is identical: **silent when clean, one findings block
when not, zero model cost.** Where the kb preflight reads the shared
knowledge graph, this reads the surfaces :mod:`brr.notes` registers — the
run's control files, the dominion, the work surface.

Four checks, one per measured failure, and deliberately no speculative
fifth:

- ``inert-pitfall`` / ``unindexed-pitfall-section`` — a ``## `` section in
  ``pitfalls.md`` that the matcher will never fire on. (#985: a lesson
  spelled ``**Trigger:**`` where :func:`brr.pitfalls.parse_pitfalls` reads
  ``trigger:`` sat inert for nine days, looking perfectly filed.)
- ``eviction-preview`` — what the *current* budget will drop from an
  injected surface, **before** the wake that pays for it. (#1020: the
  dominion playbook ran 1,855 B over its 20,480 B ceiling and lost its
  bottom three sections every wake for a month; the announcement only ever
  arrived inside the wake that had already lost them.)
- ``unsigned-clause`` / ``stale-signature`` — ``workflow.md`` §Signatures
  declares a staleness predicate *and its own enforcement* ("checked the
  same way: deterministically, differentially, silent when clean") and
  nothing implemented it. This is that.
- ``never-linked`` — a dominion or knowledge repo that has never had a git
  remote, so brr's best-effort capture push has never had anywhere to go.
  (#1423: the skip was silent at every site — no marker, no banner, no
  deterministic row — so a home that never left one machine rendered
  identically to a healthy one.) Mirrors the wake-time banners
  (:func:`brr.dominion.never_linked` / :func:`brr.knowledge.never_linked`)
  rather than re-deriving the predicate.

And one that is about the *scan*, not about any surface —
``home-unresolved`` / ``surface-root-empty`` (:func:`check_roots`). It
exists because the discipline below was, on the first day it shipped,
collected on its author: ``brnrd notes check`` printed "all registered
surfaces clean" about an account it had measured at five findings minutes
earlier, because 17 of 22 surfaces had never resolved and every check had
iterated over nothing. **A clean verdict is a claim about the surfaces
that were actually read**, so it now always carries how many that was
(:class:`Scope`), and a root the scan could not read is a finding rather
than a silence.

**Two disciplines the checks are built to, not decorated with.**

*Ask the owning module what the class contains.* Every check drives the
real implementation — :func:`brr.pitfalls.parse_pitfalls`,
:func:`brr.dominion.resolve_self_inject_digest`,
``prompts._build_work_surface_block_scored`` — and diffs its answer against
the surface on disk. A check that re-implemented the parse could only ever
agree with itself.

*Every check carries its own sanity assertion.* A check parametrised over
the same list its target uses degrades, on a rename, into a no-op passing
over an empty set — and a no-op check reports clean. So each one also
reports the state where **it** has gone blind: a pitfall file with headings
and no parsed entries, a self-inject manifest with entries and no digest, a
signature scope naming a section that no longer exists.

Findings reuse :class:`brr.kb_preflight.Finding` — same three severities,
same ``render()``, so the wake block reads uniformly whichever preflight
produced a line.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .kb_preflight import Finding

#: How far back the signature staleness walk reads per section. A section
#: whose last *rewrite* is older than this many commits is reported as
#: "not determined" rather than silently clean — the walk's own edge.
_GIT_LOG_DEPTH = 60

#: Recognised in a self-inject digest to recover the section names the real
#: collapser dropped. Written by ``dominion._render_collapse_banner``; read
#: here rather than re-deriving the collapse, so the names in a finding are
#: the names the wake will actually lose.
_COLLAPSE_BANNER_RE = re.compile(r"^> collapsed bottom-up: (.+)$", re.MULTILINE)

#: Written by ``prompts._build_work_surface_block_scored`` when a page did
#: not fit. Both forms name their pages; that naming is the whole point of
#: #1020, and reading it back is how this check reports the same fact one
#: wake earlier.
#: ``(?P<page>.+)``, not ``\S+``: the assembler renders ``### {relative}``
#: from the raw home-relative path, and a page named ``zzz other.md`` is a
#: legal surface page. ``\S+`` matched the first word and then failed the
#: newline, so exactly the pages with a space in the name went unreported —
#: the silent kind of miss. ``_UNANNOUNCED_RE`` never had the bug because
#: its names arrive back-ticked; the two markers must agree.
_PAGE_OMITTED_RE = re.compile(
    r"^### (?P<page>.+)\n\n_\(page omitted — (?P<bytes>[\d,]+) B would not fit",
    re.MULTILINE,
)
_UNANNOUNCED_RE = re.compile(
    r"_\(\d+ further surface pages? omitted — the surface budget was "
    r"exhausted: (?P<pages>.+?) · read them under",
    re.MULTILINE | re.DOTALL,
)


# ── 0. The scan's own scope ──────────────────────────────────────────


def check_roots(roots, rows=None) -> list[Finding]:
    """A durable root that did not resolve — the scan's own blind spot.

    **This one is about the checks, not about a surface.** Every check
    below iterates over what a resolver found; a resolver that found
    nothing produces no findings, and no findings renders as *clean*. So
    ``brnrd notes check`` could answer "all registered surfaces clean"
    about a dominion, a work surface and a kb it never located — the exact
    shape of the failures the other three checks exist to catch, collected
    on their author one layer out. Measured 2026-08-07: a real account with
    five live findings reported clean, because a bare read command had
    resolved a freshly-scaffolded empty project home instead of the real
    one (#1193, upstream and not fixed here — the honesty is what belongs
    here, and it is the guard that would have made #1193 visible long ago).

    Silent when brnrd is not configured for this repo: there is no claim
    to fall short of, and a repo that never adopted brnrd does not want a
    line every wake saying it has no dominion.

    An **error**, ranking above every finding it invalidates, because a
    reader triaging the list below needs to know first that the list may
    be a report about the wrong directory.
    """
    from . import notes as notes_mod

    unread = notes_mod.unresolved_roots(roots, rows)
    if not unread:
        return []
    home = str(roots.home_root) if roots.home_root else "unresolved"
    if len(unread) == len(notes_mod.DURABLE_ROOTS):
        return [Finding(
            type="home-unresolved",
            target=home,
            description=(
                "**every** durable root under the home this invocation "
                "resolved is missing or empty — no dominion, no work surface, "
                "no kb. Every check below therefore ran over an empty set, and "
                "any clean verdict here is a claim about nothing. Either brnrd "
                "has not been set up for this repo yet, or this process "
                "resolved a different home than the one you mean (#1193: a "
                "bare read command can scaffold a fresh project home keyed on "
                "`sha1(repo_root)` — one per worktree — and then answer about "
                "it). Check the path in this line against `brnrd account` "
                "before believing anything below it."
            ),
            severity="error",
        )]
    return [
        Finding(
            type=(
                "surface-root-unresolved" if state == notes_mod.ROOT_MISSING
                else "surface-root-empty"
            ),
            target=root,
            description=(
                (
                    f"this root did not resolve (looked for: {where}), "
                    if state == notes_mod.ROOT_MISSING else
                    f"this root resolved to `{where}` and holds **none** of "
                    "its registered surfaces, "
                )
                + f"so every registered {root} surface was skipped and no "
                "check below covers one. Not a clean verdict — an unread "
                "one. If you expected content here, this process resolved a "
                "different home than you mean (#1193); `brnrd account` names "
                "the one it should be."
            ),
            severity="error",
        )
        for root, state, where in unread
    ]


# ── 1. The pitfall store ─────────────────────────────────────────────


_H2_RE = re.compile(r"^## (.+)$", re.MULTILINE)


def check_pitfall_store(dominion_dir: Path, label: str = "") -> list[Finding]:
    """Findings for one dominion's ``pitfalls.md``.

    Two failure shapes, both invisible on disk:

    - a section the parser *did* index but that carries no ``trigger:``
      line, so :meth:`brr.pitfalls.Pitfall.matches` returns ``False`` for
      every task forever (:func:`brr.pitfalls.inert` owns this class — it
      is asked, not re-derived);
    - a ``## `` section the parser did **not** index at all. The parse and
      the file are diffed by *title*, so a heading the parser drops for any
      reason — present or future — surfaces here without this function
      knowing why it was dropped. That is the point: a check derived from
      the implementation's own list cannot catch the list being wrong.

    The sanity assertion is the third finding: a file carrying ``## ``
    headings that yields **no** parsed entries at all. That is the state
    where this check has gone blind (a grammar change, a rename, a parser
    regression), and it is exactly the state a check written as
    "iterate over what the parser found" reports as clean.
    """
    from . import pitfalls as pitfalls_mod

    path = dominion_dir / pitfalls_mod.PITFALLS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    where = f" (dominion `{label}`)" if label else ""
    parsed = pitfalls_mod.parse_pitfalls(dominion_dir)
    headings = [m.group(1).strip() for m in _H2_RE.finditer(text)]

    out: list[Finding] = []

    # Sanity first: if this fires, the two findings below are meaningless
    # and saying so beats emitting an empty clean verdict.
    if headings and not parsed:
        return [Finding(
            type="pitfall-store-unreadable",
            target=pitfalls_mod.PITFALLS_FILE,
            description=(
                f"{len(headings)} `## ` heading(s) on disk, 0 entries parsed by "
                f"`pitfalls.parse_pitfalls`{where}. The whole store is inert — "
                "every wake matches nothing. This is the check's own blind "
                "spot reporting itself: the file's grammar and the parser have "
                "diverged, so no per-entry finding below can be trusted."
            ),
            severity="error",
        )]

    parsed_titles = {p.title for p in parsed}
    for title in headings:
        if title in parsed_titles:
            continue
        out.append(Finding(
            type="unindexed-pitfall-section",
            target=f"{pitfalls_mod.PITFALLS_FILE} § {title}",
            description=(
                "this `## ` section is in the file but "
                f"`pitfalls.parse_pitfalls` did not index it{where} — it can "
                "never be injected. Check the heading level and that it is not "
                "inside a fenced block."
            ),
            severity="warning",
        ))

    for entry in pitfalls_mod.inert(parsed):
        out.append(Finding(
            type="inert-pitfall",
            target=f"{pitfalls_mod.PITFALLS_FILE} § {entry.title}",
            description=(
                "entry has no `trigger:` line, so it matches no task and has "
                "never been injected — it reads as filed and behaves as "
                f"deleted{where}. Add a `trigger: <keyword>, <keyword>` line "
                "under the heading, or delete the entry if the lesson is spent."
            ),
            severity="info",
        ))
    return out


# ── 2. Eviction preview ──────────────────────────────────────────────


def check_self_inject_eviction(
    dominion_dir: Path,
    *,
    budget_bytes: int | None = None,
    cfg: dict[str, Any] | None = None,
    label: str = "",
) -> list[Finding]:
    """What the *current* self-inject budget will drop, before it drops it.

    Drives the real resolver —
    :func:`brr.dominion.resolve_self_inject_digest` — and reads its
    :class:`brr.dominion.InjectOverflow` accounting. Not a reimplementation
    of the budget arithmetic: the numbers in the finding are the numbers
    the wake will produce, because they came from the call the wake makes.

    Note the resolver returns a ``(digest, overflow)`` **tuple**. Comparing
    its return value against the source text passes silently on the wrong
    object; the overflow record is the thing to read, and ``None`` there is
    the only definition of "everything fit".

    **The budget comes from the same resolver the wake uses**
    (:func:`brr.dominion.inject_budget_bytes`), never from the module
    constant. ``adopt.py`` writes ``dominion.inject_budget_bytes`` into
    every fresh install, so a preview measured against the default while
    the wake spends a configured budget is the normal case, not an edge
    one — and it reports "everything fits" about a digest that is losing
    sections every wake. That is #1020 reproduced inside the check written
    to prevent it. *budget_bytes* stays available for tests that want to
    name a budget directly.
    """
    from . import dominion as dominion_mod

    manifest = dominion_dir / dominion_mod.SELF_INJECT_FILE
    try:
        manifest_text = manifest.read_text(encoding="utf-8")
    except OSError:
        return []
    entries = [
        line.strip() for line in manifest_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not entries:
        return []

    if budget_bytes is None:
        budget_bytes = dominion_mod.inject_budget_bytes(cfg)
    digest, overflow = dominion_mod.resolve_self_inject_digest(
        dominion_dir, budget_bytes=budget_bytes,
    )
    where = f" (dominion `{label}`)" if label else ""

    # Sanity assertion: the manifest names entries and the resolver
    # produced nothing. Every entry was skipped — unreadable path, unknown
    # mode, an `exec` entry that is recognised but never run. A check that
    # only inspected `overflow` would call this state clean, because
    # nothing overflowed a budget nothing was spent against.
    if entries and not digest.strip():
        return [Finding(
            type="self-inject-empty",
            target=dominion_mod.SELF_INJECT_FILE,
            description=(
                f"{len(entries)} manifest entr(ies) resolve to an empty "
                f"digest{where} — every one was skipped (missing path, unknown "
                "mode, or `exec`, which is parsed and deliberately not run). "
                "The dominion rides no self-inject material at all this wake."
            ),
            severity="error",
        )]

    if overflow is None:
        return []

    collapsed = _COLLAPSE_BANNER_RE.search(digest)
    parts = [
        f"self-inject is {overflow.total_dropped_bytes:,} B over its "
        f"{overflow.budget_bytes:,} B ceiling "
        f"({overflow.percent_dropped:.0f}% of the manifest's "
        f"{overflow.total_source_bytes:,} B dropped){where}."
    ]
    if overflow.clipped_entry:
        parts.append(
            f"`{overflow.clipped_entry}` is section-collapsed "
            f"(-{overflow.clipped_dropped_bytes:,} B)"
            + (f": {collapsed.group(1)}." if collapsed else ".")
        )
    if overflow.dropped_entries:
        named = ", ".join(
            f"`{line}` ({size:,} B)" for line, size in overflow.dropped_entries
        )
        parts.append(f"Dropped whole: {named}.")
    parts.append(
        "Eviction is bottom-up within an entry and manifest-order across "
        "entries, so the fix is to reorder the manifest or shorten the "
        "collapsed sections — not to hope."
    )
    return [Finding(
        type="eviction-preview",
        target=dominion_mod.SELF_INJECT_FILE,
        description=" ".join(parts),
        severity="warning",
    )]


def check_work_surface_eviction(repo_root: Path) -> list[Finding]:
    """Work-surface pages the *current* budget drops entirely.

    Drives ``prompts._build_work_surface_block_scored`` — the real
    assembler, the one the wake calls — and reads back the two markers it
    already renders when a page did not fit (#1020 made both name their
    pages; this reads that naming one wake earlier).

    Deliberately reports **only** whole-page losses. A page that rode
    trimmed is not a finding: ``backchannel.md`` is compressed to handles
    by design and ``ledger/decisions.md`` is capped because it accretes, so
    flagging "not carried whole" would fire every wake for a non-reason —
    which is how a guard stops being read.
    """
    try:
        from .prompts import _build_work_surface_block_scored

        trimmed, _whole = _build_work_surface_block_scored(repo_root)
    except Exception:
        return []
    text = trimmed.text or ""
    if not text:
        return []

    # One finding per evicted page, targeted at that page. A single
    # finding listing three pages is one line a reader can neither verify
    # nor act on page by page; three findings each name the file they are
    # about, so `brnrd notes` can put the verdict on that page's own row.
    dropped: list[tuple[str, str]] = []
    for match in _PAGE_OMITTED_RE.finditer(text):
        dropped.append((match.group("page"), f"{match.group('bytes')} B"))
    unannounced = _UNANNOUNCED_RE.search(text)
    if unannounced:
        for part in unannounced.group("pages").split("·"):
            page = part.strip().strip("`")
            if page:
                dropped.append((page, ""))
    if not dropped:
        return []
    out: list[Finding] = []
    for page, size in dropped:
        detail = f" ({size})" if size else ""
        out.append(Finding(
            type="eviction-preview",
            target=f"surface/{page}",
            description=(
                f"this page{detail} does not fit the wake's surface budget "
                "and is dropped **whole** — the wake carries a line naming it "
                "and nothing of its content. The walk spends the shared "
                "budget in `work_surface_files` order (index.md first, then "
                "home-relative name), so an earlier page's growth is what "
                "evicted it: shorten that page, or move this one's "
                "load-bearing material where the reserve protects it."
            ),
            severity="warning",
        ))
    return out


# ── 3. Signatures ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Signature:
    """One four-key ``signed-by`` / ``date`` / ``scope`` / ``basis`` record.

    :attr:`sections` is :attr:`scope` reduced to the section titles it
    claims to cover: the scope's head, before the em-dash that separates
    *which sections* from *what about them*, split on ``;``. That reduction
    is the parse's whole risk surface, which is why an unmatched name is
    reported rather than dropped (see :func:`check_signatures`).
    """

    signed_by: str
    date: str
    scope: str
    basis: str
    line: int
    sections: tuple[str, ...] = ()
    retracted: bool = False


_SIGNED_BY_RE = re.compile(r"^\s+signed-by:\s*(.+?)\s*$")
_KEY_RE = re.compile(r"^\s+(date|scope|basis):\s*(.*)$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalise_section(raw: str) -> str:
    """A section title reduced to its comparable core.

    Headings carry parentheticals (``## Orchestration (2026-08-01, from the
    maintainer's steer…)``) that a scope line never repeats, so both sides
    are cut at the first ``(`` and lowercased. Nothing else is stripped —
    an aggressive normaliser would start matching sections that merely
    resemble each other, and a signature attached to the wrong section is
    worse than one attached to none.
    """
    head = raw.split("(", 1)[0]
    return head.strip().strip("*_` ").lower()


def _scope_sections(scope: str) -> tuple[str, ...]:
    head = re.split(r"\s[—–-]\s", scope, maxsplit=1)[0]
    return tuple(
        _normalise_section(part) for part in head.split(";") if part.strip()
    )


def parse_signatures(text: str) -> list[Signature]:
    """Parse ``workflow.md``'s signature records.

    The section says the four keys are shaped "so a preflight can parse it
    without a model" — this takes it at its word and nothing further: an
    indented ``signed-by:`` opens a record, ``date:`` / ``scope:`` /
    ``basis:`` fill it, and a further-indented continuation line appends to
    whichever key is open. A record ends at the next ``signed-by:``, at a
    non-indented line, or at end of file.

    A ``RETRACTED`` marker inside a record marks it :attr:`retracted` and
    it stops covering anything — the 2026-07-25 retraction is kept in the
    file on purpose ("the shortest proof the block works"), so a parser
    that counted it as live coverage would report a retracted clause as
    agreed.
    """
    records: list[Signature] = []
    current: dict[str, Any] | None = None
    key: str | None = None

    def flush() -> None:
        nonlocal current, key
        if current is None:
            return
        scope = " ".join(current["scope"].split())
        records.append(Signature(
            signed_by=" ".join(current["signed-by"].split()),
            date=current["date"].strip(),
            scope=scope,
            basis=" ".join(current["basis"].split()),
            line=current["line"],
            sections=_scope_sections(scope),
            retracted=current["retracted"],
        ))
        current, key = None, None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        opener = _SIGNED_BY_RE.match(raw)
        if opener:
            flush()
            current = {
                "signed-by": opener.group(1), "date": "", "scope": "",
                "basis": "", "line": lineno, "retracted": False,
            }
            key = "signed-by"
            continue
        if current is None:
            continue
        if not raw.strip():
            flush()
            continue
        if not raw[:1].isspace():
            flush()
            continue
        if "RETRACTED" in raw:
            current["retracted"] = True
        keyed = _KEY_RE.match(raw)
        if keyed:
            key = keyed.group(1)
            current[key] = keyed.group(2)
            continue
        if key:
            current[key] = f"{current[key]} {raw.strip()}"
    flush()
    return records


def _section_ranges(text: str) -> list[tuple[str, int, int]]:
    """``(title, first_line, last_line)`` per ``## `` section, 1-indexed."""
    lines = text.splitlines()
    marks = [
        (i + 1, line[3:].strip())
        for i, line in enumerate(lines) if line.startswith("## ")
    ]
    out: list[tuple[str, int, int]] = []
    for idx, (start, title) in enumerate(marks):
        end = marks[idx + 1][0] - 1 if idx + 1 < len(marks) else len(lines)
        out.append((title, start, max(start, end)))
    return out


@dataclass(frozen=True)
class _Rewrite:
    """The newest commit that *replaced* text in a line range."""

    sha: str
    date: str
    replaced_lines: int = 0
    """How many lines that commit replaced inside the range.

    Triage, not decoration: a six-line clause rewrite and a one-line path
    correction both satisfy the predicate, and only the count tells the
    reader which one they are about to spend a re-signing round on. It is
    the difference between annotating and accusing.
    """


def _head_section_ranges(
    repo_dir: Path, rel_path: str,
) -> dict[str, tuple[int, int]] | None:
    """Section line ranges as they are **at HEAD**, keyed by normalised title.

    ``git log -L<start>,<end>:<path>`` resolves its range against HEAD, not
    against the working tree — and it *clamps* an out-of-range end instead
    of erroring, so a mismatch produces a confident wrong answer with no
    signal. Feeding it line numbers counted from the file on disk is
    therefore only correct while the file is committed, and an uncommitted
    maintainer edit to ``workflow.md`` is that file's normal state between
    capture commits: one added six-line section at the top shifts every
    range down, and the walk reports a rewrite in §Autonomy that happened
    in §Gating and merges.

    So the ranges come from HEAD's own blob. A section that exists on disk
    but not at HEAD (freshly added, uncommitted) is simply absent from this
    map, and the caller reports it as undetermined rather than clean.
    """
    from . import gitops

    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=repo_dir, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env=gitops.explicit_repo_env(),
        )
    except (OSError, ValueError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return {
        _normalise_section(title): (start, end)
        for title, start, end in _section_ranges(proc.stdout)
    }


def _last_rewrite(
    repo_dir: Path, rel_path: str, start: int, end: int,
) -> _Rewrite | None:
    """The newest commit whose diff *removed* a line inside ``start..end``.

    This is what makes the check obey §Signatures' own 2026-07-25
    amendment. The predicate as first written ("content newer than the
    claim about it") cannot tell an **append** from a **rewrite**, so one
    new clause staled every prior signature in the section — a guard that
    fires constantly for a non-reason. ``git log -L`` gives the range's own
    history with diffs, and a hunk carrying no ``-`` line added text
    without touching any: exactly the append the amendment exempts.

    Returns ``None`` when nothing in range was ever rewritten, when git is
    unavailable, or when the path is not tracked — three states this check
    reports as *undetermined*, never as clean.
    """
    from . import gitops

    try:
        proc = subprocess.run(
            [
                "git", "log", f"-L{start},{end}:{rel_path}",
                f"-n{_GIT_LOG_DEPTH}", "--date=short",
                "--format=%x00%H %ad",
            ],
            cwd=repo_dir,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=gitops.explicit_repo_env(),
        )
    except (OSError, ValueError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None

    sha = date = ""
    hit_sha = hit_date = ""
    replaced = 0
    for line in proc.stdout.splitlines():
        if line.startswith("\x00"):
            # `git log` is newest-first, so the first commit carrying a
            # deletion is the newest one that rewrote signed text — stop at
            # the *next* commit header, having counted its whole diff.
            if hit_sha:
                break
            sha, _, date = line[1:].partition(" ")
            continue
        if not sha:
            continue
        if line.startswith(("---", "+++", "@@", "diff ", "index ")):
            continue
        if line.startswith("-"):
            hit_sha, hit_date = sha, date.strip()
            replaced += 1
    if not hit_sha:
        return None
    return _Rewrite(sha=hit_sha[:8], date=hit_date, replaced_lines=replaced)


def check_signatures(
    path: Path,
    *,
    repo_dir: Path | None = None,
    rel_path: str | None = None,
) -> list[Finding]:
    """Unsigned and stale clauses in a signed two-party page.

    Three findings, in confidence order — the discipline is that remedy
    severity matches signal confidence, so an exact match states itself
    flatly and a fuzzy one annotates rather than accuses:

    - ``signature-scope-unmatched`` (**the sanity assertion**, ``error``) —
      a signature's scope names a section that no longer exists under that
      title. Nothing else in this function can be trusted while it fires:
      a renamed section silently un-covers itself and every clause under
      it would otherwise be reported as never-signed. This is the exact
      degradation a check parametrised over the implementation's own list
      cannot see.
    - ``unsigned-clause`` (``info``) — a ``## `` section no live signature
      covers. Flat and checkable: §Signatures says text no signature covers
      *is* a proposal, so this is a restatement of the file's own rule, not
      a judgement about it.
    - ``stale-signature`` (``warning``) — a signature whose covered section
      had text **replaced** after the signature date. Carries the commit
      sha so the reader can settle it with one ``git show``; an addition
      beside signed text is exempt, per the section's own amendment.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    signatures = [s for s in parse_signatures(text) if not s.retracted]
    sections = _section_ranges(text)
    if not sections:
        return []
    if not signatures:
        return [Finding(
            type="signature-scope-unmatched",
            target=path.name,
            description=(
                f"{len(sections)} `## ` section(s) and **no** parseable "
                "`signed-by:` record. Either the page carries no signatures at "
                "all, or the four-key record grammar this check parses has "
                "drifted — until that is settled, no clause here can be "
                "reported as signed or unsigned."
            ),
            severity="error",
        )]

    by_norm = {_normalise_section(title): (title, start, end)
               for title, start, end in sections}
    # The history walk's ranges are HEAD's, never the working tree's — see
    # `_head_section_ranges`. `None` here means "no history available",
    # which suppresses the staleness half entirely; the content-only
    # findings below do not depend on git at all.
    head_ranges: dict[str, tuple[int, int]] | None = None
    if repo_dir is not None and rel_path is not None:
        head_ranges = _head_section_ranges(repo_dir, rel_path)
    out: list[Finding] = []

    # ── the sanity assertion ──
    unmatched: dict[str, list[str]] = {}
    for sig in signatures:
        for name in sig.sections:
            if name not in by_norm:
                unmatched.setdefault(name, []).append(
                    f"{sig.signed_by} {sig.date}"
                )
    for name, signers in sorted(unmatched.items()):
        out.append(Finding(
            type="signature-scope-unmatched",
            target=f"{path.name} §Signatures",
            description=(
                f"signature(s) by {', '.join(signers)} scope "
                f"`{name}`, which matches no `## ` heading in this file. The "
                "section was renamed or removed and the signature no longer "
                "attaches to anything — so the clause it covered now reads as "
                "unsigned below, and this line is the reason."
            ),
            severity="error",
        ))

    covered: dict[str, list[Signature]] = {}
    for sig in signatures:
        for name in sig.sections:
            if name in by_norm:
                covered.setdefault(name, []).append(sig)

    for title, start, end in sections:
        norm = _normalise_section(title)
        if norm == "signatures":
            continue  # the block that records the signatures is not a clause
        if norm not in covered:
            out.append(Finding(
                type="unsigned-clause",
                target=f"{path.name} §{title}",
                description=(
                    "no signature scopes this section. By this file's own "
                    "rule — *text no signature covers is a proposal, not an "
                    "agreement* — nothing here binds the counterpart. Sign "
                    "it, or read it as a draft."
                ),
                severity="info",
            ))
            continue
        if repo_dir is None or rel_path is None or not head_ranges:
            continue
        head_range = head_ranges.get(norm)
        if head_range is None:
            continue  # section not at HEAD yet — undetermined, never clean
        rewrite = _last_rewrite(repo_dir, rel_path, *head_range)
        if rewrite is None or not _ISO_DATE_RE.match(rewrite.date):
            continue
        stale = [
            sig for sig in covered[norm]
            if _ISO_DATE_RE.match(sig.date) and sig.date < rewrite.date
        ]
        if not stale:
            continue
        who = ", ".join(f"{sig.signed_by} ({sig.date})" for sig in stale)
        plural = "" if rewrite.replaced_lines == 1 else "s"
        out.append(Finding(
            type="stale-signature",
            target=f"{path.name} §{title}",
            description=(
                f"{rewrite.replaced_lines} line{plural} of signed text in this "
                f"section {'was' if rewrite.replaced_lines == 1 else 'were'} "
                f"**replaced** on {rewrite.date} in `{rewrite.sha}`, after "
                f"{who} signed it. "
                "Re-sign or amend — never assume the counterpart still agrees "
                "with text they have not read. (An addition beside signed "
                "text is exempt and does not reach this line; `git show "
                f"{rewrite.sha}` is the whole evidence.)"
            ),
            severity="warning",
        ))
    return out


# ── 4. Never-linked home ─────────────────────────────────────────────


def check_never_linked(
    repo_root: Path, cfg: dict[str, Any] | None = None,
) -> list[Finding]:
    """A dominion or knowledge repo carrying no git remote at all (#1423).

    The deterministic twin of the wake-time banners
    (``prompts._build_dominion_block`` / ``_build_knowledge_sources_block``):
    same predicate, same source of truth (:func:`brr.dominion.never_linked` /
    :func:`brr.knowledge.never_linked`), read here instead of re-derived, so
    a resident that skims straight to "notes health" and never reads the
    dominion/knowledge prose still sees the fact. Same gate too — silent
    until the repo carries a commit beyond its founding one, so a fresh
    install with nothing written yet reports nothing.

    Deduplicated by ``capture_root`` the same way
    ``daemon._capture_dominion`` dedupes before committing: an
    account-scoped candidate whose ``path`` is a namespaced subdirectory of
    a *shared* dominion repo would otherwise report the same repo's
    never-linked status once per resident sharing it.
    """
    from . import account
    from . import dominion as dominion_mod
    from . import gitops
    from . import knowledge as knowledge_mod

    if cfg is None:
        try:
            from . import config as conf

            cfg = conf.load_config(repo_root)
        except Exception:
            cfg = {}

    out: list[Finding] = []

    try:
        seen: set[Path] = set()
        for candidate in dominion_mod.resident_dominion_candidates(repo_root, cfg):
            root = candidate.capture_root
            if not root.is_dir():
                continue
            try:
                key = root.resolve()
            except OSError:
                key = root
            if key in seen:
                continue
            seen.add(key)
            reason = dominion_mod.never_linked(root.parent, root)
            if not reason:
                continue
            where = f" (dominion `{candidate.label}`)" if candidate.label else ""
            out.append(Finding(
                type="never-linked",
                target=str(root),
                description=f"{reason}{where}.",
                severity="warning",
            ))
    except Exception:
        pass

    try:
        ctx = account.resolve_context(repo_root, cfg, create=False)
        home_knowledge = account.knowledge_path(ctx)
        if home_knowledge.is_dir():
            reason = knowledge_mod.never_linked(
                gitops.shared_brr_dir(repo_root), home_knowledge,
            )
            if reason:
                out.append(Finding(
                    type="never-linked",
                    target=str(home_knowledge),
                    description=f"{reason} (account knowledge repo).",
                    severity="warning",
                ))
    except Exception:
        pass

    return out


# ── The scan ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Scope:
    """What a scan's verdict is a claim *about*.

    A findings list with no denominator is unreadable: an empty one means
    "everything I read was healthy", and only this says how much that was.
    Returned beside the findings by :func:`scan_scoped` so no caller has to
    reconstruct it, and so "clean" can never be printed without it.
    """

    located: int
    registered: int
    unresolved_roots: tuple[str, ...] = ()

    @property
    def whole(self) -> bool:
        return not self.unresolved_roots

    def line(self) -> str:
        """One line naming the scope, for a clean verdict or a block header."""
        head = f"{self.located} of {self.registered} registered surfaces located"
        if self.unresolved_roots:
            head += f" · unresolved roots: {', '.join(self.unresolved_roots)}"
        return head


def scan(repo_root: Path, cfg: dict[str, Any] | None = None) -> list[Finding]:
    """Every notes-surface finding for *repo_root*, ordered for rendering.

    Silent — an empty list — when every registered surface is healthy.
    **Not** silent when a durable root failed to resolve: see
    :func:`check_roots`. A scan that cannot see a surface says so instead
    of counting it clean.

    Ordered ``error`` → ``warning`` → ``info``, then by type and target,
    matching :func:`brr.kb_preflight.scan` so a reader triages one list the
    same way in either block.
    """
    return scan_scoped(repo_root, cfg)[0]


def scan_scoped(
    repo_root: Path, cfg: dict[str, Any] | None = None,
) -> tuple[list[Finding], Scope]:
    """:func:`scan`, plus the :class:`Scope` its verdict is a claim about."""
    from . import notes as notes_mod

    if cfg is None:
        try:
            from . import config as conf

            cfg = conf.load_config(repo_root)
        except Exception:
            cfg = {}

    out: list[Finding] = []

    # Resolve once, and keep the roots: they are what turns "no findings"
    # from a verdict into a *scoped* verdict. Everything below runs over
    # what these located.
    rows, roots = notes_mod.resolve_with_roots(repo_root, cfg)
    out.extend(check_roots(roots, rows))
    located, registered = notes_mod.located_counts(rows)
    scope = Scope(
        located=located,
        registered=registered,
        unresolved_roots=tuple(
            f"{root} ({state})"
            for root, state, _where in notes_mod.unresolved_roots(roots, rows)
        ),
    )

    try:
        from . import dominion as dominion_mod

        for candidate in dominion_mod.resident_dominion_candidates(repo_root, cfg):
            if not candidate.path.is_dir():
                continue
            out.extend(check_pitfall_store(candidate.path, candidate.label))
            out.extend(check_self_inject_eviction(
                candidate.path, cfg=cfg, label=candidate.label,
            ))
    except Exception:
        pass

    out.extend(check_work_surface_eviction(repo_root))

    # `signatures` is a registry trait, not a filename: a second signed
    # page enrols itself in this check by declaring the trait, with no
    # edit here. Today the trait selects exactly `workflow.md`.
    try:
        signed = {s.key for s in notes_mod.with_trait("signatures")}
        for resolved in rows:
            if resolved.surface.key not in signed:
                continue
            for path in resolved.paths:
                repo_dir, rel = _git_location(path)
                out.extend(check_signatures(
                    path, repo_dir=repo_dir, rel_path=rel,
                ))
    except Exception:
        pass

    out.extend(check_never_linked(repo_root, cfg))

    from .kb_preflight import _SEVERITY_RANK

    out.sort(key=lambda f: (
        _SEVERITY_RANK.get(f.severity, 99), f.type, f.target,
    ))
    return out, scope


def _git_location(path: Path) -> tuple[Path | None, str | None]:
    """``(repo_root, repo-relative path)`` for *path*, or ``(None, None)``.

    The work surface lives in the account home's **own** git repo, not the
    project's, so this asks git where the file actually is rather than
    assuming. The environment scrub is not optional: under a brnrd wake
    ``GIT_DIR``/``GIT_WORK_TREE`` are pinned to the run's worktree and would
    silently answer for the wrong repository.
    """
    from . import gitops

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path.parent,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=gitops.explicit_repo_env(),
        )
    except (OSError, ValueError):
        return None, None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None, None
    root = Path(proc.stdout.strip())
    try:
        return root, path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None, None


def format_findings(findings: list[Finding]) -> str:
    """Render *findings* as the wake block's findings list, or ``""``."""
    if not findings:
        return ""
    bullets = "\n".join(f.render() for f in findings)
    return f"## Findings (deterministic preflight)\n\n{bullets}\n"
