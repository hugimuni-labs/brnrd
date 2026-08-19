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

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from . import account, card, config as conf, dev_reload, forge_state, menus, protocol


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
# The adopter template lives at ``constitution.TEMPLATE_PATH``; brr's own
# repository contract is the real ``src/brr/AGENTS.md`` (root symlink
# target). Layer 0 of ``design-init-as-a-wake.md`` split those two jobs
# apart — ``build_init_prompt`` ships the template, not brr's own playbook.


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

# Per-page byte budget inside the resident/user-authored work surface. Its
# plan and decision-ledger ancestors had no cap at all until 2026-07-09 —
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

# Named, not inline — #1061's reserve below asserts against this rather than
# a bare `48_000` repeated in two places, so bumping the default and bumping
# the reserve stay two visibly separate edits.
_DEFAULT_SURFACE_INJECT_BUDGET_BYTES = 48_000

# #1061 rec 1: two pages decide what a run may do — `workflow.md` (the
# signed two-party gating contract) and this repo's own
# `plans/<repo-slug>/active.md` (the resident's own ranked queue) — and the
# plain path-order walk below sorts both of them last (`p`, `w`). Measured
# twice losing them outright: once trimmed to 363 B of a 6,580 B page, once
# to zero when an *earlier* page's one-section floor overflowed its own
# allowance and collapsed the walk's shared `remaining` to 0 in a single
# step (see the walk's own comment on that line) — a page's mandatory
# content can crowd out everything sorted after it, however small or
# important. A reserve fixes this differently from a bigger budget: a floor
# for each page is carved out of the existing total below, before the
# alphabetical walk spends anything (#919's law — an existing total does not
# grow to make room for a new priority). The floor alone is what survives an
# earlier page's floor overflow; when the walk reaches a reserved page's own
# turn with real room still unspent it tops the floor back up, so the page
# still rides whole on a healthy budget rather than being capped at this
# size regardless of how much room the shared pool actually has (see
# `_build_work_surface_block_scored`'s topup). Sizing follows the ticket's
# own fallback shape (no fixed number was specified): min(actual page size,
# this many bytes) per page, out of the same shared total.
_SURFACE_RESERVE_PAGE_BYTES = 6 * 1024

assert 2 * _SURFACE_RESERVE_PAGE_BYTES < _DEFAULT_SURFACE_INJECT_BUDGET_BYTES, (
    "the reserve (workflow.md + plans/<repo>/active.md) must leave the "
    "alphabetical walk real room, or it only relocates #1061's starvation "
    "onto every other page instead of fixing it"
)

_H2_RE = re.compile(r"(?m)^## ")
_H2_SPLIT_RE = re.compile(r"(?m)(?=^## )")

# The date-extraction rule for a `## ` heading: the *first* `YYYY-MM-DD` in
# the heading line, nowhere else. Covers both live conventions without
# needing to know which one a given page uses — `## [2026-07-23] shipped |
# …` (kb/log.md) and `## Some title (2026-07-23, run-260723-1900-ek9s)` (the
# decision ledger). A heading with no match is undated; per
# `review-boot-prompts-2026-07.md` §P1, an undated heading is never guessed
# at or inferred from file position — it makes the whole trim it belongs to
# not-attestable (see `_entries_attestation`).
_HEADING_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# A run-id (`run-YYMMDD-HHMM-xxxx`) embedded in a heading carries a *time*,
# and the incident this whole feature is named after is same-day: an 11:31
# entry sitting below a 13:42 one, both dated 2026-07-23. Day granularity
# alone cannot see it — see `_entry_key`.
_HEADING_RUNID_RE = re.compile(r"run-(\d{2})(\d{2})(\d{2})-(\d{4})")


def _heading_date(entry: str) -> str | None:
    """First ``YYYY-MM-DD`` in a ``## `` entry's heading line, or ``None``.

    Only the heading line (up to the first newline) is searched — a date
    mentioned in an entry's *body* is not the entry's date.
    """
    heading = entry.split("\n", 1)[0]
    match = _HEADING_DATE_RE.search(heading)
    return match.group(1) if match else None


def _runid_instant(entry: str) -> tuple[str, str] | None:
    """``(YYYY-MM-DD, HHMM)`` from a run-id in the heading, or ``None``.

    A run-id is **one fact, not two**: ``run-260723-2353-4hic`` names the
    instant a run started, date and clock together, and needs nothing else
    to be unambiguous.

    That distinction is the correction #670 landed. This used to be
    ``_heading_time``, which returned the run-id's clock only when the
    run-id's *date* matched the heading's, on the ground that "a heading
    date paired with some other day's clock time is not a timestamp, it is
    two facts glued together." The premise was right and the remedy dropped
    the wrong half: it discarded a perfectly good instant to protect a
    splice nobody needed to make. Cross-midnight disagreement is not a
    legacy-discipline problem that better writing retires — a run that
    starts at 23:53 and writes its entry after midnight dates the heading
    by the day it is writing about, structurally, forever — so keying
    ordering on corroboration held ``precise`` at ``False`` on any day the
    ledger contained one such entry, which on an hourly-tick account is
    most days.

    The heading date remains what a reader is *shown* (see
    ``_EntryKey.shown_time``, which still refuses the splice); the run-id
    is what the entries are *ordered* by, and ordering wants the write
    time — which is also what the file's own append order already reflects.
    """
    match = _HEADING_RUNID_RE.search(entry.split("\n", 1)[0])
    if not match:
        return None
    yy, mm, dd, hhmm = match.groups()
    return f"20{yy}-{mm}-{dd}", hhmm


class _EntryKey(NamedTuple):
    """What one ``## `` entry contributes to ordering, and what it may show.

    Two roles the pre-#670 key conflated into a single ``(date, time)``
    pair, which is why it had to discard a time to keep the pair coherent:

    - ``order_date`` / ``order_time`` — the **write instant**, taken whole
      from the run-id when the heading carries one. ``order_time`` is
      ``None`` only when there is no run-id at all; that entry is orderable
      to the day and no finer, and it is the genuinely unrepairable legacy
      set (no clock was ever recorded, and none may be invented).
    - ``shown_date`` — the heading's own date, the **editorial** fact a
      reader wants in the trim marker. Never displaced by the run-id's
      date: a cross-midnight entry is *about* 2026-07-24 even though it was
      written at 2026-07-23 23:53.
    """

    order_date: str
    order_time: str | None
    shown_date: str

    @property
    def shown_time(self) -> str | None:
        """The clock time it is honest to render *next to* ``shown_date``.

        Only when the run-id corroborates the heading's date — otherwise
        printing "2026-07-24 23:53" for an entry headed 2026-07-24 and run
        at 2026-07-23 23:53 would glue two facts into a timestamp that
        never existed. Ordering does not need this restraint (it reads the
        run-id's own date alongside its clock); display does, because a
        rendered ``date time`` pair is read as one instant.
        """
        return self.order_time if self.order_date == self.shown_date else None


def _entry_key(entry: str) -> _EntryKey | None:
    """The ordering/display key for a ``## `` entry, or ``None`` if undated.

    ``None`` — not attestable — exactly when the *heading* carries no
    parseable date, unchanged by #670. A run-id alone does not rescue such
    an entry: the heading date is what the trim reports, and an entry with
    nothing to report is one whose date could just as well have been the
    true newest.
    """
    date = _heading_date(entry)
    if date is None:
        return None
    instant = _runid_instant(entry)
    if instant is None:
        return _EntryKey(order_date=date, order_time=None, shown_date=date)
    return _EntryKey(order_date=instant[0], order_time=instant[1], shown_date=date)


def _split_h2_entries(content: str) -> list[str]:
    """Every ``## `` entry in *content*, in document order; ``[]`` if none.

    The page's leading preamble (anything above the first ``## ``) is not an
    entry and is not returned here.
    """
    match = _H2_RE.search(content)
    if not match:
        return []
    return [e for e in _H2_SPLIT_RE.split(content[match.start() :]) if e.strip()]


def _page_is_chronological(content: str) -> bool:
    """Is this page an *accreting log*, or a *hand-authored document*?

    The two kinds want opposite treatment and the difference is **computable
    from the page itself** — no filename table, no config knob, no per-page
    declaration (#688). The predicate is the one
    :func:`_entries_attestation` already applies: every ``## `` heading
    carries a parseable date.

    - **Every heading dated ⇒ chronological.** ``ledger/decisions.md``,
      ``kb/log.md``: entries accrete at the bottom forever, so the *tail* is
      what a wake needs and the per-page cap is a real defence against a
      458 KB page eating the surface budget.
    - **Any heading undated ⇒ structural.** ``surface/workflow.md``,
      ``plans/<repo>/active.md``: the ``## `` sections are a document's
      parts, not a timeline. Their order is editorial, the *head* is what
      leads, and the page is bounded because a human maintains it by hand.

    Undated is deliberately the *structural* side rather than the unknown
    side: a dated page is a claim the page itself makes and can be checked
    against, and a page that makes no such claim is the one a human wrote.

    A page with **no ``## `` headings at all** answers ``True`` — not
    because it is a log, but because it is unclassifiable, and the accreting
    cap is the backstop for that case (it takes the flat byte-cut path in
    :func:`_trim_sectioned_page`, which has no entry structure to reason
    about).
    """
    entries = _split_h2_entries(content)
    if not entries:
        return True
    return all(_entry_key(e) is not None for e in entries)


def _heading_title(entry: str) -> str:
    """The ``## `` heading's own text, markers stripped — for naming a cut."""
    return entry.split("\n", 1)[0].lstrip("#").strip()


# #1061 rec 3: mirrors ``frontend/src/lib/backchannelPage.ts``'s ``ROW_RE`` —
# the recognized-row grammar for one backchannel/warp item. A contiguous
# block of these ``key: value`` lines immediately after the ``## `` heading
# is schema; the first line that doesn't match ends the block, and
# everything from there is the item's free markdown body. Both sides parse
# the same six keys so an item authored against one reads correctly by the
# other.
_BACKCHANNEL_ROW_RE = re.compile(r"^(kind|state|needs|refs|prompt|taken):[ \t]*")


def _backchannel_item_handle(entry: str) -> str:
    """One ``## `` backchannel/warp entry, reduced to heading + schema rows.

    The free markdown body under an item is authored for the maintainer, on
    a surface read in a browser; a wake acts on the heading and the handful
    of ``key: value`` rows, never the prose (issue #1061's own framing: one
    page, two audiences, and injection was serving the wrong one). Keeps
    every recognized row present, in document order — not just
    ``kind:``/``prompt:``/``refs:`` — so a ``state:``/``needs:`` warp item or
    a ``taken:`` back-pointer (THE WELD, #972) survives the compression too;
    the ticket names three rows as the common case, not an exhaustive list.

    Reconstructs the conventional single blank line between heading and rows
    (never more, whatever the source had) — an item with rows but no body
    then compresses to a string byte-identical to its own entry, so a
    backchannel already in handle shape shows no drop and no marker.
    """
    lines = entry.split("\n")
    heading = lines[0]
    i = 1
    if i < len(lines) and lines[i].strip() == "":
        i += 1  # the conventional blank line between heading and rows
    rows = []
    while i < len(lines) and _BACKCHANNEL_ROW_RE.match(lines[i]):
        rows.append(lines[i])
        i += 1
    return f"{heading}\n\n{chr(10).join(rows)}" if rows else heading


def _backchannel_handles_only(content: str) -> tuple[str, int]:
    """*content* reduced to one handle per ``## `` item (#1061 rec 3).

    Returns ``(rendered, dropped_bytes)``. ``dropped_bytes`` is ``0`` and
    ``rendered is content`` (the same object) when nothing changed — no
    ``## `` headings to compress (an empty or freshly-scaffolded page), or
    every item was already handle-shaped with no body to drop — so a caller
    comparing for a byte-identical "whole" injection (#628) can use either
    side of the pair without a second check.
    """
    entries = _split_h2_entries(content)
    if not entries:
        return content, 0
    preamble = content[: _H2_RE.search(content).start()].strip()
    pieces = ([preamble] if preamble else []) + [
        _backchannel_item_handle(e) for e in entries
    ]
    rendered = "\n\n".join(pieces).strip()
    if rendered == content:
        return content, 0
    dropped = len(content.encode("utf-8")) - len(rendered.encode("utf-8"))
    return rendered, max(dropped, 0)


def _entries_attestation(
    all_entries: list[str], picked_entries: list[str]
) -> tuple[str | None, str | None, str | None, bool, bool]:
    """``(newest, oldest, source_newest, stale, precise)`` for a trim.

    *all_entries* is every entry the source held (picked and dropped);
    *picked_entries* is the subset that survived the trim. Returns
    ``(None, None, None, False, False)`` — **not attestable** — the moment any
    entry in *all_entries* carries a heading with no parseable date: the
    playbook invariant this whole feature exists to satisfy is "a guard may
    only assert something the run can be proven wrong about," and a date
    skipped because it couldn't be read is a date that could just as well have
    been the true newest.

    **The staleness formula lives here and nowhere else**, and it is two-tier
    on purpose:

    - Dates differ ⇒ compare days. Sound at any precision.
    - Dates tie ⇒ compare times, but **only when every entry sharing the
      source's newest write date carries one** (*precise*). That cohort is
      exactly the set that can decide a tie; an entry on an older date is
      settled by the day comparison above and its missing time is
      irrelevant. This is the tier that catches the incident the feature is
      named after — an 11:31 entry sitting below a 13:42 one, both dated
      2026-07-23, which day granularity reports as healthy.
    - Dates tie and precision is unavailable ⇒ **not stale, and not certain**.
      The caller must not claim the tail is current; see ``_trim_marker``.
      This is the branch that matters most: the honest output there is a
      narrower claim, never a confident one.
    """
    all_keys = [_entry_key(e) for e in all_entries]
    if any(k is None for k in all_keys):
        return None, None, None, False, False
    picked_keys = [_entry_key(e) for e in picked_entries]

    # Precise only if every entry that could *decide* the comparison carries a
    # time — and that is the cohort sharing the source's newest **write** date,
    # not the whole file. Time is only ever the tie-breaker: an entry written
    # earlier than the source's newest can never be the source's newest, so its
    # missing time cannot change the verdict.
    #
    # Two narrowings, both driven, both load-bearing:
    #
    # - #610 scoped the cohort from whole-file to top-date. Measured then on
    #   this account's live ledger: 162 entries, 55 untimed, but only 1 untimed
    #   on the newest date — whole-file scope held `precise` at False
    #   permanently, since legacy headings cannot be repaired without inventing
    #   timestamps.
    # - #670 stopped requiring the run-id's date to *corroborate* the heading's
    #   before its clock counted (see `_runid_instant`). Top-date scope alone
    #   still never fired: measured 2026-07-24 on the same ledger — 177 entries,
    #   56 untimed, 14 on the top date of which exactly 1 untimed, and that one
    #   a cross-midnight entry (`## … (2026-07-24, run-260723-2353-4hic)`).
    #   That residual is structural, not legacy: every run that starts before
    #   midnight and writes after it produces another, so no amount of
    #   discipline retires it. After #670 the same ledger reports 13 entries on
    #   the top *write* date, 0 untimed, `precise` True — and 41 untimed
    #   file-wide rather than 56, the 15 recovered ones being exactly the
    #   cross-midnight entries whose clocks were previously discarded.
    #
    # A guard gated on a condition the future can no longer satisfy is a guard
    # that never fires. What remains is the genuinely unrepairable set: headings
    # carrying no run-id at all, for which no clock was ever recorded.
    top_date = max(k.order_date for k in all_keys)
    precise = all(
        k.order_time is not None for k in all_keys if k.order_date == top_date
    )

    # Normalize the missing time to "" before ordering: a set mixing timed and
    # untimed headings would otherwise compare str against None and raise.
    # Safe because staleness only consults the time component when both sides
    # sit on `top_date` (see below), where `precise` guarantees a real time —
    # so "" is never the deciding term.
    def _ord(key: _EntryKey) -> tuple[str, str]:
        return key.order_date, key.order_time or ""

    newest_key = max(picked_keys, key=_ord)
    oldest_key = min(picked_keys, key=_ord)
    source_key = max(all_keys, key=_ord)

    if source_key.order_date > newest_key.order_date:
        stale = True
    elif precise:
        stale = _ord(source_key) > _ord(newest_key)
    else:
        stale = False

    def shown(key: _EntryKey) -> str:
        """Render a key for human eyes, at the precision actually established.

        When the comparison was precise, the time *must* appear: a same-day
        alarm that renders as "newest 2026-07-23 — source has 2026-07-23" is
        correct and unreadable, and an alarm nobody can parse is not much
        better than the silence it replaced.

        ``shown_time`` is ``None`` for a cross-midnight entry even when the
        comparison ran at full precision — that entry's clock belongs to
        another day and may not be printed beside this one's date. Ordering
        used the whole instant; display shows only the half that is this
        entry's own. Each side of the range renders at the precision it
        actually has, which is why a marker can legitimately read
        "showing 2026-07-24 → 2026-07-24 00:59".
        """
        if precise and key.shown_time:
            return f"{key.shown_date} {key.shown_time[:2]}:{key.shown_time[2:]}"
        return key.shown_date

    return shown(newest_key), shown(oldest_key), shown(source_key), stale, precise


@dataclass(frozen=True)
class TrimResult:
    """What a chronological-tail trim rendered, and what it can attest to.

    ``text`` is the rendered page — the whole return value of every caller
    before this class existed. The four attestation fields are the facts
    ``_trim_sectioned_page`` and ``_read_recent_log`` already computed while
    deciding what to cut, and used to throw away
    (``review-boot-prompts-2026-07.md`` §P1): which dated entry survived as
    "newest," how many entries the budget cut, and what the *source's* true
    newest entry is — the gap between the last two is the ledger-tail
    inversion bug this class exists to make attestable.

    All four default to ``None`` — **no trim happened** (content already fit
    the budget, or entry selection needed no cut). ``dropped`` alone can be
    non-``None`` while the date fields stay ``None``: a count of entries cut
    needs no parseable heading, but a date claim does, and a heading with no
    parseable date makes the whole result **not attestable** (see
    :func:`_entries_attestation`) — never guessed, never inferred from
    position.
    """

    text: str
    newest_item: str | None = None
    oldest_item: str | None = None
    dropped: int | None = None
    source_newest: str | None = None
    stale: bool = False
    """``True`` iff the source held an entry newer than what survived the trim.

    A **stored** fact, not a re-derivation: the comparison happens once, in
    :func:`_entries_attestation`, which is the only place that still holds the
    times. The displayed ``newest_item`` / ``source_newest`` are dates, and two
    same-day entries compare equal as dates while being ordered in fact — so a
    consumer re-deriving ``source_newest > newest_item`` from those two strings
    would silently lose the same-day case that is the whole reason this feature
    exists. Every consumer (``ContractEntry.stale``, ``bootscore.attest_blocks``,
    ``_trim_marker``) reads this flag.
    """

    precise: bool = False
    """Whether same-day ordering was actually checkable for this trim.

    ``True`` only when every entry sharing the source's newest **write** date
    — the cohort that can actually decide a same-day tie — carried a run-id,
    hence a clock (see :func:`_runid_instant`). When ``False`` and the tail's
    newest shares a date with the source's
    newest, this result can say "not known to be stale" but **must not** say
    the tail is current — a distinction :func:`_trim_marker` renders and
    ``attest_blocks`` respects by staying silent rather than reassuring.
    """

    floor_overflow_section: str | None = None
    """The mandatory section that made this result exceed its byte budget.

    ``_trim_sectioned_page`` owns this fact because it alone knows that the
    overrun came from its documented one-section floor rather than from a
    caller's heading overhead or another rendering layer.  The title also
    lets callers explain which section needs attention without re-parsing
    the rendered markdown.  ``None`` means the trimmer did not invoke its
    floor past the supplied budget.
    """


def _trim_marker(
    omitted: int, oldest_item: str | None, newest_item: str | None,
    source_newest: str | None, source_hint: str,
    *, stale: bool = False, precise: bool = False,
) -> str:
    """The truncation notice embedded in a trimmed page's own rendered text.

    Pre-2026-07-23 this said only *how many* entries were cut — never *when*
    what remains is from, so a reader had no way to tell a current tail from
    a stale one (the maintainer's own refinement to P1: "if we truncate the
    log, we should also show the date when it was last modified"). When the
    trim is attestable (see :func:`_entries_attestation`) the marker now
    carries the range, and — when the source has drifted past it — says so
    in words a skimming reader cannot mistake for the healthy case. Falls
    back to the plain entry-count notice when the trim isn't attestable
    (undated headings): no date is guessed, so none is shown.
    """
    noun = "entry" if omitted == 1 else "entries"
    base = f"_({omitted} earlier {noun} cut to fit the wake budget"
    if not (oldest_item and newest_item and source_newest):
        return f"{base} — full history: {source_hint})_"
    if stale:
        return (
            f"{base} · showing {oldest_item} → {newest_item}, but the source "
            f"has a newer entry ({source_newest}) — this tail is NOT current "
            f"· full history: {source_hint})_"
        )
    if not precise and source_newest == newest_item:
        # The honest middle. Same-day ordering was not checkable, so the tail
        # is not *known* stale — and saying "the newest entry in the source"
        # here would be the exact false reassurance this feature exists to
        # abolish, asserted by the guard meant to prevent it.
        return (
            f"{base} · showing {oldest_item} → {newest_item} (day precision — "
            f"same-day ordering unchecked) · full history: {source_hint})_"
        )
    return (
        f"{base} · showing {oldest_item} → {newest_item}, the newest entry "
        f"in the source · full history: {source_hint})_"
    )


_MAX_NAMED_CUT_SECTIONS = 6


def _head_cut_at_line_boundary(text: str, limit: int) -> str:
    """*text* truncated to at most *limit* UTF-8 bytes, preferring a line break.

    Shared by every head-keeping cut in this module (:func:`_cap_turn_body`,
    the preamble charge in :func:`_trim_sectioned_page`) so the boundary rule
    is written once: prefer the last newline inside the budget so the kept
    head stays valid markdown rather than ending mid-sentence; a single
    over-long first line still gets a hard byte cut, decoded with
    ``errors="ignore"`` so a multi-byte character is never split in half.
    Returns ``""`` when *limit* leaves no room — the caller decides what to
    say about that.
    """
    if limit <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    kept = raw[:limit].decode("utf-8", errors="ignore")
    boundary = kept.rfind("\n")
    if boundary > 0:
        kept = kept[:boundary]
    return kept.rstrip()


def _structural_trim_marker(dropped: list[str], source_hint: str) -> str:
    """The truncation notice for a **structural** page, naming what it cut.

    A structural page's sections are titled parts of a document, so the
    honest report is *which parts are missing*, by name (#688). The
    driving incident: ``surface/workflow.md`` — the account's one
    two-party contract — lost ``## Autonomy``, ``## Gating and merges`` and
    ``## Delivery and ceremony`` to a tail trim, and the marker called them
    "3 earlier entries". A scheduled dispatcher tick whose own instructions
    read *"Merging follows workflow.md exactly"* was handed the
    ``## Signatures`` attestations for three sections whose text it never
    got, and nothing in its context said which three.

    ``entries`` is the dated path's noun and stays there; **sections** is
    this one's. There is no date range and no staleness verdict here on
    purpose: a structural page's order is editorial, so "newest" is not a
    fact about it — see :func:`_page_is_chronological`.

    Long cuts name the first :data:`_MAX_NAMED_CUT_SECTIONS` titles and
    count the rest. A page that flips to this path with 198 sections cut
    must not answer by pasting 198 titles into the wake it is trying to
    fit.
    """
    noun = "section" if len(dropped) == 1 else "sections"
    named = dropped[:_MAX_NAMED_CUT_SECTIONS]
    listed = " · ".join(named)
    if len(dropped) > len(named):
        listed += f" · … and {len(dropped) - len(named)} more"
    return (
        f"_({len(dropped)} {noun} cut to fit the wake budget: {listed} · "
        f"full page: {source_hint})_"
    )


def _preamble_cut_marker(cut_bytes: int, source_hint: str) -> str:
    """The notice for a preamble that did not fit its page's own allowance.

    The defect this pairs with is that the preamble used to be appended
    *outside* the budget walk with ``used`` starting at 0, so a
    preamble-heavy page rendered past its allowance (#688 measured
    ``plans/<repo>/active.md`` at 193%, and ``ledger/decisions.md`` at 111%
    live). Charging it is the fix; **truncating it silently would only move
    the defect**, so a charged-and-cut preamble always says so, in bytes.
    """
    return (
        f"_({cut_bytes:,} B of this page's opening cut to fit the wake budget "
        f"· full page: {source_hint})_"
    )


def _handles_only_marker(dropped_bytes: int, source_hint: str) -> str:
    """The notice for a page injected as schema-only handles (#1061 rec 3).

    Same honesty discipline as every other trim marker in this module: a
    page that reaches the wake in a shape that differs from what's on disk
    says so, in bytes, with a path back to the full text.
    """
    return (
        f"_(injected as handles only — heading + kind/state/needs/refs/prompt "
        f"rows per item, free text dropped: {dropped_bytes:,} B · full page: "
        f"{source_hint})_"
    )


def _trim_sectioned_page(content: str, max_bytes: int, source_hint: str) -> TrimResult:
    """Trim a ``## ``-sectioned page to fit *max_bytes*, keeping the right half.

    **Which half is right is derived, never declared** (#688). The page's
    own headings say which kind of page it is — see
    :func:`_page_is_chronological`, which both this function and
    ``_build_work_surface_block_scored`` consult, so the shape is decided in
    one place:

    - **Chronological** (every ``## `` heading dated) ⇒ keep the **tail**.
      Accreting pages only ever grow — the resident's convention is "add an
      entry", never "prune the last one" (see
      ``_MAX_ACCRETING_BLOCK_BYTES``) — so the newest entries live at the
      bottom and are what a wake needs. Mirrors ``_read_recent_log``'s
      newest-first accumulation, generalized past ``kb/log.md``'s bracketed
      ``## [date]`` heading to a plain ``## `` heading.
    - **Structural** (any heading undated) ⇒ keep from the **head**, in
      document order. A hand-authored page's sections are parts of a
      document, not a timeline; its first sections are what lead.

    This function shipped tail-only, and the docstring's "newest is at the
    bottom" was true for ``ledger/decisions.md`` and false for
    ``surface/workflow.md``. The cost was not hypothetical: a scheduled
    dispatcher tick instructed to *"follow workflow.md exactly"* ran with
    ``## Gating and merges`` cut out of its context and ``## Signatures``
    — the tail — still present, so it held attestations for three sections
    whose text it never received. It was called ``_tail_trim_entries`` until
    2026-07-31 (#765): #688 made direction a derived property and left the
    name asserting one of the two branches, so the identifier every reader
    met first contradicted the paragraph above it. Both nouns were wrong by
    then — a structural page's parts are sections, not entries.

    Either way **at least one entry always survives**, even if it alone
    exceeds the budget: the newest decision, or the leading section, never
    silently disappears. That floor is also the *only* way the rendered
    text can exceed *max_bytes* — everything else here is charged, and the
    overshoot in that one case is bounded by the notices, never by the
    page.

    The preamble is **charged against the allowance**, not appended outside
    it. It used to be appended with ``used`` starting at 0, which is how a
    preamble-heavy page rendered past its own allowance. When the preamble
    cannot fit alongside the one mandatory entry it is cut at a line
    boundary and :func:`_preamble_cut_marker` says how many bytes went —
    an uncharged preamble is the defect, and a silently truncated one would
    only move it.

    Returns *content* unchanged when it already fits — no trim, so the
    returned :class:`TrimResult` carries no attestation (all four extra
    fields ``None``); a block that fits whole is untouched, not "attested
    healthy." Falls back to a flat tail cut when the page has no ``## ``
    headings to respect — also not attestable, for the same reason: there
    are no entry-dated headings to compare.

    When entries are cut, the returned ``dropped`` / ``newest_item`` /
    ``oldest_item`` / ``source_newest`` are the facts this function already
    computes while deciding what to keep (see ``_entries_attestation``) —
    P1's whole point is that these used to be thrown away here, in the one
    place that had them. A structural trim reports ``dropped`` and nothing
    else: it is not-attestable by construction (undated headings), and
    "newest" is not a fact about a page whose order is editorial.
    """
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return TrimResult(text=content)
    match = _H2_RE.search(content)
    entries = _split_h2_entries(content)
    if match is None or not entries:
        tail = encoded[-max_bytes:].decode("utf-8", errors="ignore")
        return TrimResult(text=(
            f"_(older content cut to fit the wake budget — full page: "
            f"{source_hint})_\n\n{tail}"
        ))
    preamble = content[: match.start()].strip()
    chronological = _page_is_chronological(content)

    # `keep` counts entries taken from the keep side — the bottom when
    # chronological, the top when structural. `_picked` turns that count
    # back into document order, which is what everything downstream wants.
    def _picked(keep: int) -> list[str]:
        return entries[len(entries) - keep:] if chronological else entries[:keep]

    def _cut(keep: int) -> list[str]:
        return entries[: len(entries) - keep] if chronological else entries[keep:]

    def render(keep: int) -> tuple[str, str]:
        """``(marker, body)`` for a candidate pick of *keep* entries."""
        picked = _picked(keep)
        cut = _cut(keep)
        body = "".join(picked).strip()
        if not cut:
            return "", body
        if chronological:
            newest, oldest, src_newest, is_stale, is_precise = _entries_attestation(
                entries, picked
            )
            return _trim_marker(
                len(cut), oldest, newest, src_newest, source_hint,
                stale=is_stale, precise=is_precise,
            ), body
        return _structural_trim_marker(
            [_heading_title(e) for e in cut], source_hint
        ), body

    def assemble(pre: str, pre_marker: str, marker: str, body: str) -> str:
        # A chronological cut happens at the *top* (older entries above the
        # kept tail) and a structural one at the *bottom* (later sections
        # below the kept head): the marker sits where the content went.
        pieces = [pre, pre_marker]
        pieces += [marker, body] if chronological else [body, marker]
        return "\n\n".join(p for p in pieces if p)

    # Charge the preamble — but never let it crowd out the one entry that
    # must survive, or the marker that explains the cut. Both are reserved
    # before the preamble gets its room; the marker reserve uses the
    # worst-case (maximal) cut, so the walk below can only overshoot by the
    # difference between two marker strings, which the fit loop settles.
    mandatory_bytes = len(_picked(1)[0].encode("utf-8"))
    worst_marker = render(1)[0]
    pre_marker = ""
    if preamble:
        room = max_bytes - mandatory_bytes - len(worst_marker.encode("utf-8")) - 4
        preamble_bytes = len(preamble.encode("utf-8"))
        if preamble_bytes > room:
            # Only now does the preamble's own notice exist, so only now does
            # it cost anything. Its byte count can never exceed the preamble's
            # own size, so formatting the probe with that count bounds its
            # width.
            probe = _preamble_cut_marker(preamble_bytes, source_hint)
            room -= len(probe.encode("utf-8")) + 2
            kept_preamble = _head_cut_at_line_boundary(preamble, max(0, room))
            cut_bytes = preamble_bytes - len(kept_preamble.encode("utf-8"))
            pre_marker = _preamble_cut_marker(cut_bytes, source_hint)
            preamble = kept_preamble

    fixed = len(preamble.encode("utf-8")) + len(pre_marker.encode("utf-8"))
    fixed += len(worst_marker.encode("utf-8")) + 8  # inter-piece separators
    # Walk outward from the keep side: bottom-up when chronological (newest
    # first), top-down when structural (leading section first).
    from_keep_side = list(reversed(entries)) if chronological else entries
    keep = 0
    used = 0
    for entry in from_keep_side:
        entry_bytes = len(entry.encode("utf-8"))
        if keep and fixed + used + entry_bytes > max_bytes:
            break
        keep += 1
        used += entry_bytes

    # The walk reserved the worst-case marker; the real one is usually
    # shorter, but a structural cut names *different* titles depending on
    # where the boundary fell, so settle the fit against the text actually
    # rendered. Terminates: the mandatory entry is never dropped.
    while True:
        marker, body = render(keep)
        text = assemble(preamble, pre_marker, marker, body)
        if len(text.encode("utf-8")) <= max_bytes or keep <= 1:
            break
        keep -= 1

    omitted = len(entries) - keep
    newest_item = oldest_item = source_newest = None
    stale = precise = False
    dropped: int | None = None
    if omitted:
        dropped = omitted
        if chronological:
            newest_item, oldest_item, source_newest, stale, precise = (
                _entries_attestation(entries, _picked(keep))
            )
    return TrimResult(
        text=text,
        newest_item=newest_item,
        oldest_item=oldest_item,
        dropped=dropped,
        source_newest=source_newest,
        stale=stale,
        precise=precise,
        floor_overflow_section=(
            _heading_title(_picked(1)[0])
            if len(text.encode("utf-8")) > max_bytes
            else None
        ),
    )


def _read_recent_log(
    repo_root: Path,
    max_entries: int = _MAX_LOG_ENTRIES,
    max_bytes: int = _MAX_LOG_BYTES,
) -> TrimResult:
    """Read the most recent entries from ``kb/log.md``.

    Walks entries newest-first, including each one as long as the
    accumulated UTF-8 byte size stays at or below ``max_bytes`` and we
    haven't hit ``max_entries``. The newest entry is always included
    even if it alone exceeds the budget, so the most recent context
    never silently disappears — which also means this trim can never itself
    go stale-by-trim (``newest_item`` always equals ``source_newest`` when
    attestable): the residual risk P1 guards is the *other* trim,
    ``_trim_sectioned_page``, whose "newest" is a positional assumption this
    function's explicit newest-first walk doesn't share.

    Returns a :class:`TrimResult` whose ``text`` is the raw markdown of the
    included entries (oldest of the included set first, for natural reading
    order), or ``""`` if the log is missing or has no entries. Attestation
    fields are populated exactly when something was actually cut — see
    ``TrimResult`` / ``_entries_attestation``.

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
            return TrimResult(text="")
    text = log_path.read_text(encoding="utf-8")
    parts = _LOG_ENTRY_RE.split(text)
    if len(parts) <= 1:
        return TrimResult(text="")
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
        return TrimResult(text="")
    picked.reverse()
    rendered = "\n\n".join(picked).strip()

    omitted = len(entries) - len(picked)
    if not omitted:
        return TrimResult(text=rendered)
    newest_item, oldest_item, source_newest, stale, precise = _entries_attestation(
        entries, picked
    )
    return TrimResult(
        text=rendered,
        newest_item=newest_item,
        oldest_item=oldest_item,
        dropped=omitted,
        source_newest=source_newest,
        stale=stale,
        precise=precise,
    )


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


def _build_context_block_scored(repo_root: Path) -> TrimResult:
    """The scored implementation behind ``_build_context_block``.

    Same split as ``_build_work_surface_block`` / ``..._scored``: the plain
    function stays a ``str``-returning wrapper (unchanged signature, so its
    own tests and every other caller are untouched); this variant also
    surfaces the attestation ``_read_recent_log`` computed, for
    ``_build_injected_blocks_with_contracts`` to copy onto the
    ``recent-activity`` ``ContractEntry``. No extra trimming happens at this
    layer — the attestation is ``_read_recent_log``'s, passed straight
    through.
    """
    recent = _read_recent_log(repo_root)
    if not recent.text:
        return TrimResult(text="")
    text = (
        "## Recent Activity (from kb/log.md)\n\n"
        "From `kb/log.md` — the shared, curated through-line of what's been "
        "done and learned. brr injects this recent tail every wake; it's what "
        "your continuity across thoughts (and other hands) rests on, and what "
        "earlier wakings chose to hand forward:\n\n"
        f"{recent.text}"
    )
    return TrimResult(
        text=text,
        newest_item=recent.newest_item,
        oldest_item=recent.oldest_item,
        dropped=recent.dropped,
        source_newest=recent.source_newest,
        stale=recent.stale,
        precise=recent.precise,
    )


def _build_context_block(repo_root: Path) -> str:
    """Render recent log entries as the conversation context block.

    The log is curated by agents (per ``AGENTS.md``) so the block stays
    proportional. Returns ``""`` when the log is empty or missing —
    the caller drops the block entirely in that case.
    """
    return _build_context_block_scored(repo_root).text


def _build_relabelled_repo_block(repo_root: Path) -> str:
    """Warn a wake that its memory is stranded under this repo's old address.

    Injected rather than left to be discovered, because it is the one gap a
    resident structurally cannot notice from the inside: when a repo changes
    address every memory scope re-keys, the dominion and knowledge blocks
    render empty, and an amputated home looks exactly like a fresh project.
    There is no absence to observe — only a smaller wake that reads as normal.

    So the warning has to arrive as perception, not as something to go and
    check. Returns ``""`` in every ordinary case (see
    ``account.detect_relabelled_repo``), so the block costs nothing until the
    day it matters.
    """
    from . import account
    from . import config as conf

    try:
        cfg = conf.load_config(repo_root)
        ctx = account.resolve_context(repo_root, cfg, create=False)
        current = account.repo_label(repo_root, cfg)
        stale = account.detect_relabelled_repo(ctx, repo_root, current)
    except Exception:  # noqa: BLE001 — orientation must never fail a wake
        return ""
    if not stale:
        return ""

    return (
        "## ⚠ Your memory is under this repo's previous address\n\n"
        f"This repo is registered as `{stale}`, but its remote now says "
        f"`{current}`. Every resident-memory scope is keyed by the repo "
        "label, so the knowledge, dominion, work surface, runner policy and run "
        "history you would normally wake into are **on disk but not being "
        "read** — filed under the old label.\n\n"
        "This is not a fresh project. Do not re-derive it, and do not start "
        "writing a second memory beside the first: the migration exists.\n\n"
        f"    brnrd account relabel {stale} {current} --dry-run\n"
        f"    brnrd account relabel {stale} {current}\n\n"
        "It moves every scope, rekeys the registry, and commits both homes. "
        "If you are mid-task, say this to the user first — it is almost "
        "certainly more urgent than what you were woken for."
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
    # One resolver, shared with the eviction preview — see
    # `dominion.inject_budget_bytes` for why two readers of this number
    # must not be allowed to drift.
    budget = dominion.inject_budget_bytes(cfg)
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
        sync_note = "\n\n" + _sync_marker_banner(
            status=dominion.needs_sync_status(chosen.capture_root.parent),
            reason=diverged,
            subject="your dominion remote",
            repo_path=str(chosen.capture_root),
            stakes=(
                "Until it lands, this memory lives on one machine only."
            ),
        )
    else:
        # Mutually exclusive with `diverged` by construction — the marker
        # this reads is only ever set while `needs_sync`'s is unset (see
        # `dominion.commit`) — but the `else` still says so rather than
        # relying on that invariant silently: a remote that has diverged
        # keeps its own banner and this one never doubles up on it.
        unlinked = dominion.never_linked(
            chosen.capture_root.parent, chosen.capture_root,
        )
        if unlinked:
            sync_note = "\n\n" + _never_linked_banner(
                subject="your dominion", repo_path=str(chosen.capture_root),
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
        f"{_schedule_lint_note(repo_root, path)}"
    )


def _schedule_lint_note(repo_root: Path, dominion_dir: Path) -> str:
    """The mechanical schedule-lint addendum for this wake, or ``""`` (#579).

    Deliberately **not** tied to the self-inject manifest. The first shape of
    this hooked the block onto whichever manifest entry rendered
    ``schedule.md`` — which reads sensibly and ships dark: `self-inject` is
    opt-in per dominion, the seed lists only the playbook, and this account's
    own production manifest lists only the playbook too. A linter wired
    exclusively to an opt-in surface is a linter that never runs, and its
    tests pass only because the fixture opts in.

    So it rides the dominion block itself, which is always assembled when a
    dominion exists. That is affordable precisely because zero findings render
    zero bytes: the common case costs nothing, and the rare case is the whole
    point. Every input is a local read (parsed ``schedule.md``, the firing
    state cache, the network-free PR cache) — no network joins the wake path.
    Never raises: a lint pass is a bonus, not a wake-blocking dependency.
    """
    import time

    from . import forge_pr_cache
    from . import gitops
    from . import schedule as schedule_mod

    try:
        now = time.time()
        entries = schedule_mod.parse_schedule(dominion_dir)
        try:
            text = (dominion_dir / schedule_mod.SCHEDULE_FILE).read_text(
                encoding="utf-8",
            )
        except OSError:
            text = None
        if not entries and not text:
            return ""
        findings = schedule_mod.lint_schedule(
            entries,
            now=now,
            state=schedule_mod.load_state(gitops.shared_brr_dir(repo_root)),
            forge=forge_pr_cache.read_state(repo_root, now=now),
            text=text,
        )
        block = schedule_mod.render_lint_block(findings)
    except Exception:  # noqa: BLE001 - a lint pass never blocks a wake
        return ""
    return f"\n\n{block}" if block else ""


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


def _build_strand_pitfalls_contract(
    repo_root: Path, task_text: str | None
) -> "tuple[list[tuple[str, str]], ContractEntry]":
    """The one inject-stack block a strand wake receives (#1185).

    Every other block in the inject stack (identity-core, dominion digest,
    work surface, knowledge sources, recent activity) is standing-half
    material that belongs only to the dispatching resident (see
    ``prompts/strand.md``'s own framing) — a strand's boot score keeps all
    of those empty. Pitfalls are the one exception: they are the account's
    failure-memory lookup, matched fresh against each run's own task text,
    and a strand is single-shot — the one run shape that cannot learn from
    its own patterns and so benefits most per byte from being handed this
    list.

    Mirrors block 5 of ``_build_injected_blocks_with_contracts``
    (~L2401-2416) byte for byte, including its never-weighed (``bytes=None``)
    vs weighed-and-empty (``bytes=0``) distinction (#1181) — same
    ``block_key="pitfalls"`` so ``brnrd prompts show`` and any caller keying
    off ``injected_keyed`` by name find it in the same slot for a strand as
    for a resident wake.

    Returns the keyed block (empty list when nothing rendered — no task
    text, dominion disabled/absent, or no trigger matched) alongside the
    single :class:`ContractEntry` describing it, so a caller building either
    the prompt text or a contracts-only manifest can take just the half it
    needs.
    """
    from .bootscore import ContractEntry, OWNER_RESIDENT, AUTHORITY_MEMORY

    if task_text:
        pitfalls_block = _build_pitfalls_block(repo_root, task_text)
        pitfalls_bytes = _rendered_bytes(pitfalls_block)
    else:
        pitfalls_block = ""
        pitfalls_bytes = None
    contract = ContractEntry(
        block_key="pitfalls",
        label="Task-matched pitfalls",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_MEMORY,
        freshness=None,
        location="computed",
        present=bool(pitfalls_block),
        bytes=pitfalls_bytes,
    )
    keyed = [("pitfalls", pitfalls_block)] if pitfalls_block else []
    return keyed, contract


class _ReserveFloor(NamedTuple):
    """One reserved page's pre-charged floor render (#1061 rec 1).

    ``content`` is cached alongside the render so the topup in
    :func:`_build_work_surface_block_scored` can re-trim against a bigger
    allowance without a second disk read; ``size`` is what was actually
    carved out of the shared budget for this floor, in bytes.
    """

    content: str
    block: str
    trimmed: TrimResult
    size: int


def _worst_trim(results: list[TrimResult]) -> TrimResult:
    """Pick one ``TrimResult`` to represent a block made of several trimmed pages.

    ``work-surface`` is one ``ContractEntry`` aggregating many independently
    trimmed pages (the ledger, the plan, ...); the kernel alarm (P1 4a) needs
    one representative newest/oldest/dropped/source-newest per block, not
    per page. Priority: a **stale** page outranks a merely-trimmed one — the
    alarm exists to catch exactly that class, and reporting an arbitrary
    healthy page while a stale one sits unreported would defeat the point —
    and among several stale pages, the one whose source has drifted furthest
    (``source_newest`` compares as an ISO date, so max is latest) wins.
    Failing any stale page, the page with the most entries dropped
    represents the block, so a healthy-but-trimmed block still attests
    something rather than nothing. Returns an empty, all-``None``
    ``TrimResult`` when no page was trimmed at all.
    """
    trimmed = [r for r in results if r.dropped]
    if not trimmed:
        return TrimResult(text="")
    stale = [r for r in trimmed if r.stale]
    if stale:
        return max(stale, key=lambda r: r.source_newest)
    return max(trimmed, key=lambda r: r.dropped)


# Per-page budget for the heading gist attached to a surface page the
# budget dropped whole (#1111). Deliberately tiny — a coordinate for a page
# a reader cannot open anyway is a nice-to-have next to the pages that did
# render, and #1061 already starves the surface budget without this feature
# adding to the bill. Measured against this account's own pages: a
# structural page's headings run long (`workflow.md`'s six average 32 B,
# but one alone is 90 B; `active.md`'s five average 64 B) — a handful of
# titles routinely exceeds this budget, which is why the walk below falls
# back to a bare count rather than stretching it.
_OMITTED_HEADING_GIST_BUDGET_BYTES = 40


def _page_heading_gist(content: str, max_bytes: int = _OMITTED_HEADING_GIST_BUDGET_BYTES) -> str:
    """A budget-capped list of *content*'s own ``## `` heading titles.

    For a page the surface budget drops whole (placeholder or fully
    unannounced), this is the only coordinate left to offer — the page's
    rendered text carries none of its own headings anymore. Walks titles in
    document order, keeping whichever fit inside *max_bytes* together with a
    ``· +N more`` tail when some were left out.

    Never truncates an individual title to make it fit: a clipped title is
    a fabricated anchor (mid-sentence text nobody wrote as a heading), which
    is the same class of lie #1111 exists to end for URLs. When not even the
    first title fits whole, the return value falls back to a bare count —
    true, cheap, and never worse than silence. ``""`` only when the page has
    no ``## `` headings to report (see :func:`_split_h2_entries`).

    Accounts against the *rendered* bytes, not the raw title: each kept
    title ships as ``§{title}`` (the ``§`` is 2 bytes UTF-8) joined by
    ``" · "`` (4 bytes UTF-8, the middle dot is not ASCII) — measuring
    ``len(title)`` alone would silently overshoot *max_bytes*, the "bytes,
    not len()" trap, in the one function whose whole job is a byte budget
    (caught and fixed before this ever shipped past its own worker; see
    the second stranded commit this was reconciled from).
    """
    titles = [_heading_title(e) for e in _split_h2_entries(content)]
    if not titles:
        return ""
    prefix_bytes = len("§".encode("utf-8"))
    sep_bytes_unit = len(" · ".encode("utf-8"))
    kept: list[str] = []
    used = 0
    for title in titles:
        token_bytes = prefix_bytes + len(title.encode("utf-8"))
        sep_bytes = sep_bytes_unit if kept else 0
        if kept and used + sep_bytes + token_bytes > max_bytes:
            break
        if not kept and token_bytes > max_bytes:
            break
        kept.append(title)
        used += sep_bytes + token_bytes
    if not kept:
        noun = "heading" if len(titles) == 1 else "headings"
        return f"{len(titles)} {noun}"
    rest = len(titles) - len(kept)
    gist = " · ".join(f"§{t}" for t in kept)
    if rest:
        gist += f" · +{rest} more"
    return gist


def _reserved_surface_page_paths(repo_root: Path, ctx: Any, cfg: dict) -> list[Path]:
    """The two pages :data:`_SURFACE_RESERVE_PAGE_BYTES` reserves room for.

    ``workflow.md`` and this repo's own ``plans/<repo-slug>/active.md`` — the
    same two paths ``account.workflow_doc_path`` / ``account.active_plan_path``
    already resolve for ``_build_orientation_set``'s fallback pointer, reused
    here rather than re-deriving the surface-relative path by hand.
    """
    from . import account as acc

    label = acc.repo_label(repo_root, cfg)
    return [
        acc.workflow_doc_path(ctx),
        acc.active_plan_path(ctx, label),
    ]


_REFS_ROW_VALUE_RE = re.compile(r"^refs:[ \t]*(.*)$")

# The stale-refs annotation mark (#1137). A flag, not a strike: unlike a
# live-menu option (#957), a work-surface item can legitimately outlive the
# PR it references, so this only draws a human's eye rather than claiming
# the item is done.
_STALE_REF_MARK = "⚑"


def _item_refs_text(entry: str) -> str | None:
    """The value of a ``## `` item's own ``refs:`` row, or ``None``.

    Walks the same recognized-row block :func:`_backchannel_item_handle`
    already parses (``kind``/``state``/``needs``/``refs``/``prompt``/
    ``taken``, contiguous, directly under the heading, after the
    conventional single blank line) — the schema backchannel items, warp
    items (THE WELD, ``weld.py``), and plan-page entries are all authored
    against, so one walk reads a ``refs:`` row the same way regardless of
    which surface page it lives on. ``None`` when the item carries no
    ``refs:`` row at all (most items — the annotation below only applies to
    the ones that name a PR).
    """
    lines = entry.split("\n")
    i = 1
    if i < len(lines) and lines[i].strip() == "":
        i += 1
    while i < len(lines) and _BACKCHANNEL_ROW_RE.match(lines[i]):
        match = _REFS_ROW_VALUE_RE.match(lines[i])
        if match:
            return match.group(1).strip()
        i += 1
    return None


def _annotate_stale_refs(content: str, resolved_prs: dict[int, str] | None) -> str:
    """Flag work-surface items whose ``refs:`` row names only PR(s) this
    wake's forge-state snapshot already reports MERGED/CLOSED (#1137).

    The join #957 built for the live menu (``menus._referenced_pr_numbers`` /
    ``forge_state.resolved_pr_lookup``) never reached work-surface pages —
    a plan/backchannel/layer item's own ``refs:`` row could sit unmarked for
    days after the PR it names has merged, with no mechanism to catch it
    (the incident that opened #1137). This is the render-side join for that
    row, parallel to the live-menu one.

    Deliberately an **annotation** appended to the item's own heading line,
    never a suppression: a work-surface item can legitimately outlive the
    PR it references (follow-up work, a design page the PR only partially
    executed) — this flags staleness for a human to judge, it never drops
    or strikes the item.

    Reuses :func:`menus._strike_reason` outright rather than re-deriving its
    join — it already implements exactly the semantic this annotation wants
    to match: **all-or-nothing**. An item naming one resolved PR and one
    still-open PR (or a number this wake's forge state has no opinion on)
    renders unchanged, same as the live menu — the item is still live work
    until *every* PR it names is done (#957's own precedent, quoted in its
    docstring).

    Returns *content* unchanged — same object — when there is nothing to
    annotate (no ``resolved_prs`` snapshot, no ``## `` items, or no item's
    refs fully resolve): the common case, and it keeps an unannotated page
    byte-identical to its own disk copy for the #628 "whole" accounting
    below.
    """
    if not resolved_prs:
        return content
    entries = _split_h2_entries(content)
    if not entries:
        return content
    heading_match = _H2_RE.search(content)
    preamble = content[: heading_match.start()] if heading_match else ""
    changed = False
    pieces: list[str] = []
    for entry in entries:
        refs_text = _item_refs_text(entry)
        reason = menus._strike_reason(refs_text, resolved_prs) if refs_text else None
        if reason:
            heading, sep, rest = entry.partition("\n")
            entry = f"{heading}    {_STALE_REF_MARK} refs {reason}{sep}{rest}"
            changed = True
        pieces.append(entry)
    if not changed:
        return content
    return preamble + "".join(pieces)


#: ~1.5 KB — a small standing block, index-shaped on purpose (#1332): one
#: line per hearth page, never the page body. The hearth's whole point is
#: that its pages are never mirrored anywhere, so a byte-budget mirroring
#: the work surface's tens of KB would be the wrong shape even before the
#: privacy question — this block is a table of contents, not orientation.
_HEARTH_INDEX_BUDGET_BYTES = 1536

#: The confidences reminder every hearth block carries, bare or populated.
#: Read literally by the resident's own delivery discipline (kb capture,
#: commit messages, issue/PR bodies, chat replies) — this is the line that
#: makes "never quoted into a public surface" a standing instruction
#: instead of something only ``account.HEARTH_PATH``'s placement enforces
#: structurally.
_HEARTH_CONFIDENCES_MARKER = (
    "Confidences, not context: nothing under `hearth/` is ever quoted into "
    "a kb page, an issue, a PR, a commit message, or any other surface "
    "that leaves this room. Read a page when it matters to the moment; "
    "leave its words behind you when you act."
)


def _first_heading(content: str) -> str:
    """The page's own first ``# `` line, title only — ``""`` if it has none.

    Deliberately the *first* H1, not the first ``## ``: a hearth page is
    one free-form document, not a sectioned page like a surface hub, so its
    own title is the whole orientation a one-line index needs (#1332).
    """

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _build_hearth_block(repo_root: Path) -> str:
    """The hearth's index-shaped standing block (#1332) — never the pages.

    ``<account home>/hearth/`` is the shared human<->resident *personal*
    space — confidences, story, the relationship — distinct from
    ``surface/`` (the work), the rest of the home (the resident's own
    workshop), and ``knowledge/`` (shared project facts). A wake perceives
    one line per page (filename + the page's own first ``# `` heading),
    never the page body: the room is real to every wake without flooding
    the budget or, worse, riding the same unredacted wires the work
    surface and knowledge corpus do. A page's full text is one ``Read``
    away by the path this index names, exactly like the warp item index
    inside the work-surface block just below this function.

    Placed with the identity/continuity blocks, ahead of the work surface
    (see ``_build_injected_blocks_with_contracts``): who-we-are reads
    before what-we-do, the same ordering rationale ``_build_identity_core_block``
    already uses.

    A hearth with no pages beyond its own README states that plainly
    (``the mantel is bare``) rather than omitting the block — an unnamed
    absence reads as "no such room," and the room exists the moment
    ``resolve_context`` first ran (see ``account._seed_hearth``). Returns
    ``""`` only when dominion injection itself is off or no home resolves —
    the same two guards ``_build_work_surface_block_scored`` uses.
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

    hearth = acc.hearth_path(ctx)
    header = "## The hearth — your personal space with the human\n\n" + _HEARTH_CONFIDENCES_MARKER

    pages = [
        path
        for path in acc.hearth_files(ctx)
        if path.relative_to(hearth).as_posix() != acc.HEARTH_README_NAME
    ]
    if not pages:
        return header + "\n\nthe mantel is bare — no pages beyond the README yet."

    lines: list[str] = []
    omitted = 0
    remaining = max(0, _HEARTH_INDEX_BUDGET_BYTES - len(header.encode("utf-8")))
    for path in pages:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        relative = path.relative_to(hearth).as_posix()
        heading = _first_heading(content)
        line = f"- `{relative}`" + (f" — {heading}" if heading else "")
        size = len(line.encode("utf-8")) + 1  # +1 for the joining newline
        if size > remaining:
            omitted += 1
            continue
        lines.append(line)
        remaining -= size

    body = "\n".join(lines)
    if omitted:
        noun = "page" if omitted == 1 else "pages"
        body += (
            f"\n\n_({omitted} further hearth {noun} omitted — the index "
            f"budget was exhausted: read them under `hearth/`)_"
        )
    return f"{header}\n\n{body}"


def _build_work_surface_block_scored(
    repo_root: Path,
    *,
    resolved_prs: dict[int, str] | None = None,
) -> tuple[TrimResult, "frozenset[Path]"]:
    """The scored implementation behind ``_build_work_surface_block``.

    Same split as ``_build_context_block`` / ``..._scored``: the plain
    function stays a ``str``-returning wrapper (unchanged signature —
    existing callers and tests are untouched); this variant also surfaces
    one representative attestation (see ``_worst_trim``) for
    ``_build_injected_blocks_with_contracts`` to copy onto the
    ``work-surface`` ``ContractEntry``.

    The second return value is the set of resolved surface-page paths this
    call emitted **whole** — byte-identical to the page on disk, no tail
    trim and no budget skip (see ``_build_orientation_set``'s
    ``injected_whole``: a page handed over whole here must not also be
    billed as a walk entry there; #628). A page's content compares
    byte-identical to ``trimmed.text`` exactly when ``_trim_sectioned_page``
    took its own early-return ("already fits") — the one condition true
    regardless of whether the page has dated ``## `` headings to attest
    (a headingless page that got tail-cut has no ``dropped`` count to key
    off, so comparing rendered text, not just the attestation fields, is
    what a headingless page's cut cannot hide from). A reserved page (see
    below) counts as whole under the same rule when its final render matched
    the page on disk exactly, same as any other page.

    Two refinements over the plain alphabetical walk, both from #1061:

    - **A named reserve** (rec 1, :func:`_reserved_surface_page_paths`) —
      ``workflow.md`` and this repo's ``plans/<repo-slug>/active.md`` each get
      a small guaranteed **floor** — ``min(page size, `` :data:`_SURFACE_RESERVE_PAGE_BYTES`
      ``)`` — carved out of the shared budget *before* the alphabetical walk
      spends anything, so an unrelated page's mandatory-floor overflow later
      in the walk (the ``remaining = 0`` branch below) can never zero them
      out just because they happen to sort last. The floor is a guarantee,
      not a ceiling: when the walk reaches a reserved page's own path-order
      turn with real room still unspent, it re-renders using that page's
      floor *plus* whatever is still left — so it still rides whole on a
      healthy budget (#688's invariant), and only falls back to the bare
      floor when an earlier page has already spent everything else.
    - **Backchannel as handles** (rec 3, :func:`_backchannel_handles_only`) —
      ``backchannel.md`` is compressed to one heading + schema-row handle per
      item before its size enters the budget walk at all; the free-text body
      under each item is dropped (it's authored for the dashboard reader, not
      the wake) and the page says so in place, same as any other trim marker.

    ``resolved_prs`` (#1137) is the same ``{pr_number: "merged 3h ago"}`` join
    #957 built for the live menu (:func:`brr.forge_state.resolved_pr_lookup`),
    applied here to every page's own ``## `` items via
    :func:`_annotate_stale_refs` — an item whose ``refs:`` row names only PR(s)
    this wake's forge state already reports resolved gets a short annotation on
    its heading line, never a suppression. Applied *before* a page's content
    enters :func:`_trim_sectioned_page`, so an annotated page is charged for
    the annotation's bytes like any other content and — because the annotation
    changes the rendered text — no longer counts as "whole" under the
    byte-identical-to-disk rule above, same as a backchannel page that lost
    its free-text body. Omit ``resolved_prs`` (the default) to render exactly
    as today, with no forge-state input.
    """
    from . import account as acc
    from . import config as conf

    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return TrimResult(text=""), frozenset()
    try:
        ctx = acc.resolve_context(repo_root, cfg, create=False)
    except Exception:
        return TrimResult(text=""), frozenset()
    if not ctx.enabled:
        return TrimResult(text=""), frozenset()

    surface = acc.work_surface_path(ctx)
    budget = int(
        cfg.get(
            "dominion.surface_inject_budget_bytes",
            cfg.get(
                "dominion_surface_inject_budget_bytes",
                _DEFAULT_SURFACE_INJECT_BUDGET_BYTES,
            ),
        )
    )
    blocks: list[str] = []
    trims: list[TrimResult] = []
    whole_paths: set[Path] = set()
    remaining = max(0, budget)
    # The *names* of the pages no placeholder could be afforded for, not a
    # count of them. A count says a page is missing; only the name says which,
    # and "go read it" is not an instruction until the reader knows what to
    # open. This list is the last thing standing between a dropped page and
    # silence, so it holds paths (#1020). Paired with each page's own
    # content so the closing line can still offer a heading gist (#1111) —
    # the one coordinate left for a page that rendered nothing at all.
    unannounced: list[tuple[str, str]] = []

    # The warp index (2026-08-11): `surface/warp/` and `surface/topics/`
    # are the item space — dozens of small files whose *graph*, not whose
    # pages, is what a wake needs. Injecting them as pages would flood the
    # walk (and evict every page after them), so both directories are
    # excluded from the per-page loop below and the item space rides as one
    # composed index: one line per open item — id · type · topics ·
    # blocked-by · headline — ready before held, decisions first, plus a
    # short done-tail. An item's full body is one `Read` away by the id the
    # index carries.
    try:
        from . import items as items_mod

        warp_index = items_mod.render_index(items_mod.warp_dir(ctx))
    except Exception:
        warp_index = None
    if warp_index:
        block = (
            "### the warp — open items\n\n"
            "The account's work-item graph (`surface/warp/<id>.md`, topics "
            "under `surface/topics/`). Address an item by its id in any "
            "event body to take it; verbs: `brnrd item list|new|done|retire`. "
            "decisions/preparations wait on the user; actions are "
            "dispatchable. Full body: read the item's file.\n\n"
            + warp_index
        )
        size = len(block.encode("utf-8"))
        if size <= remaining:
            blocks.append(block)
            remaining -= size

    # #1061 rec 1 — the named reserve, floor pre-pass. For each load-bearing
    # page, render once against `min(page size, _SURFACE_RESERVE_PAGE_BYTES)`
    # and carve that rendered size out of `remaining` *before* the
    # alphabetical walk below spends anything. This is a **guarantee**, not
    # where the page ends up: the walk still gives it a fair shot at more
    # room when it reaches that page's own path-order turn (see the topup
    # below) — this pre-pass only fixes the worst case, where an *earlier*
    # page's floor overflow has already zeroed `remaining` by the time the
    # walk gets there (the `remaining = 0` branch two screens down).
    reserve_floor: dict[Path, _ReserveFloor] = {}
    for path in _reserved_surface_page_paths(repo_root, ctx, cfg):
        if not path.is_file() or path.is_symlink():
            continue
        resolved = path.resolve()
        if resolved in reserve_floor:
            continue  # workflow.md and a repo's active.md never collide,
            # but a degenerate config could point both at the same file
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        relative = path.relative_to(surface).as_posix()
        allowance = min(_SURFACE_RESERVE_PAGE_BYTES, remaining)
        annotated = _annotate_stale_refs(content, resolved_prs)
        trimmed = _trim_sectioned_page(annotated, allowance, f"`surface/{relative}`")
        block = f"### {relative}\n\n{trimmed.text}"
        if trimmed.floor_overflow_section is not None:
            trimmed_bytes = len(trimmed.text.encode("utf-8"))
            notice = (
                "_(mandatory section floor exceeded this page's reserved "
                f"allocation — trimmed page: {trimmed_bytes:,} B · reserve: "
                f"{allowance:,} B · overflowing section: "
                f"`{trimmed.floor_overflow_section}` · full page: "
                f"`surface/{relative}`)_"
            )
            block = f"{block}\n\n{notice}"
        size = len(block.encode("utf-8"))
        remaining = max(0, remaining - size)
        reserve_floor[resolved] = _ReserveFloor(content, block, trimmed, size)

    for path in acc.work_surface_files(ctx):
        resolved = path.resolve()
        relative = path.relative_to(surface).as_posix()
        if relative.startswith(("warp/", "topics/")):
            # The item space rides as the composed index above, never as
            # pages — see the warp-index block.
            continue
        floor = reserve_floor.get(resolved)
        if floor is not None:
            # The topup: this page's own floor, spent above, plus whatever
            # of the shared pool is still unclaimed *now* — mathematically
            # the same allowance this page would see if it had never been
            # pre-charged at all, unless an earlier page's floor overflow
            # already zeroed `remaining`, in which case topup == floor and
            # the pre-rendered block below is exactly what re-rendering
            # would produce anyway.
            topup_allowance = remaining + floor.size
            if topup_allowance <= floor.size:
                block, trimmed = floor.block, floor.trimmed
            else:
                annotated = _annotate_stale_refs(floor.content, resolved_prs)
                trimmed = _trim_sectioned_page(
                    annotated, topup_allowance, f"`surface/{relative}`"
                )
                block = f"### {relative}\n\n{trimmed.text}"
                if trimmed.floor_overflow_section is not None:
                    trimmed_bytes = len(trimmed.text.encode("utf-8"))
                    notice = (
                        "_(mandatory section floor exceeded this page's "
                        f"budget — trimmed page: {trimmed_bytes:,} B · "
                        f"budget: {topup_allowance:,} B · overflowing "
                        f"section: `{trimmed.floor_overflow_section}` · "
                        f"full page: `surface/{relative}`)_"
                    )
                    block = f"{block}\n\n{notice}"
            size = len(block.encode("utf-8"))
            remaining = max(0, topup_allowance - size)
            blocks.append(block)
            trims.append(trimmed)
            if trimmed.text == floor.content:
                whole_paths.add(resolved)
            continue
        raw_content = path.read_text(encoding="utf-8").strip()
        if not raw_content:
            continue
        content = raw_content
        handles_dropped = 0
        if relative == _BACKCHANNEL_PAGE:
            # #1061 rec 3 — the backchannel's free-text body is written for
            # the maintainer reading it in a browser; a wake needs the
            # heading and the item's schema rows. Compress *before* the
            # budget arithmetic below, same as any other page's real size —
            # this is what shrank 68% of a live budget to a fraction of it.
            content, handles_dropped = _backchannel_handles_only(raw_content)
        content = _annotate_stale_refs(content, resolved_prs)
        page_bytes = len(content.encode("utf-8"))
        if remaining <= 0:
            unannounced.append((relative, content))
            continue
        # The per-page cap is a defence against *accreting* pages — see
        # `_MAX_ACCRETING_BLOCK_BYTES`, and `ledger/decisions.md`, 458 KB
        # today and larger next week. A hand-authored page is not that: it
        # is bounded because a human maintains it, so capping it deletes
        # content while the shared budget sits idle: #688 measured
        # `workflow.md` losing 5 KB of a signed two-party contract with
        # roughly half of the 48,000 B budget still unspent. `remaining`
        # still bounds it, so a runaway authored page cannot exceed it —
        # it can only crowd pages after it, and `work_surface_files` order
        # is stable, so that is deterministic and, via the skip placeholder
        # below, visible.
        if _page_is_chronological(content):
            allowance = min(remaining, _MAX_ACCRETING_BLOCK_BYTES)
        else:
            allowance = remaining
        trimmed = _trim_sectioned_page(content, allowance, f"`surface/{relative}`")
        block_body = trimmed.text
        if handles_dropped:
            block_body = (
                f"{block_body}\n\n"
                f"{_handles_only_marker(handles_dropped, f'`surface/{relative}`')}"
            )
        block = f"### {relative}\n\n{block_body}"
        size = len(block.encode("utf-8"))
        if size > remaining:
            if trimmed.floor_overflow_section is not None:
                # The trimmer deliberately exceeded its allowance to honour
                # the one-section floor.  That stored fact distinguishes this
                # from heading overhead or a headingless flat cut without
                # making the renderer reverse-engineer the trimmed markdown.
                trimmed_bytes = len(trimmed.text.encode("utf-8"))
                notice = (
                    "_(mandatory section floor exceeded this page's budget — "
                    f"trimmed page: {trimmed_bytes:,} B · budget: {allowance:,} B "
                    f"· overflowing section: `{trimmed.floor_overflow_section}` "
                    f"· full page: `surface/{relative}`)_"
                )
                block = f"{block}\n\n{notice}"
                blocks.append(block)
                trims.append(trimmed)
                # A floor overflow may crowd out later pages, but the shared
                # arithmetic remains total and deterministic.  Those pages
                # flow through `unannounced` below because not even a
                # placeholder can fit in a zero remainder — so the closing
                # line names them, which is the only reason a hard zero is
                # survivable. The reserve above is exactly the escape hatch
                # for the two pages that must not depend on that survival.
                remaining = 0
                continue
            # Heading overhead can push a budget-trimmed page just past the
            # remainder. Skip *this* page, not every page after it — the next
            # (smaller) file may still fit. **Say so**: a page dropped from a
            # wake with nothing naming it is the same class of silent loss
            # this whole change exists to end, and a reader who cannot see
            # that `workflow.md` is absent cannot know to go read it. The
            # heading gist (#1111) rides the same placeholder budget check
            # below, so a gist too big to afford falls through to
            # `unannounced` exactly like the bare placeholder always did.
            gist = _page_heading_gist(content)
            gist_suffix = f" · {gist}" if gist else ""
            placeholder = (
                f"### {relative}\n\n_(page omitted — {page_bytes:,} B would not "
                f"fit the {remaining:,} B left of the surface budget{gist_suffix} "
                f"· full page: `surface/{relative}`)_"
            )
            placeholder_size = len(placeholder.encode("utf-8"))
            if placeholder_size <= remaining:
                blocks.append(placeholder)
                remaining -= placeholder_size
            else:
                unannounced.append((relative, content))
            continue
        blocks.append(block)
        trims.append(trimmed)
        remaining -= size
        if trimmed.text == raw_content:
            whole_paths.add(resolved)

    if unannounced:
        # The budget ran out before even a placeholder fit. One line, not
        # one per page, and it is not charged — the alternative is silence
        # about pages the wake never saw. Emitted even when it is the *only*
        # block: "no authored surface yet" would be a lie about a surface
        # whose pages were all skipped.
        #
        # The line names each page (#1020). It cost a signed contract to learn
        # why: this wake dropped `workflow.md` — the agreement governing
        # gating and merges, quoted by the schedule entry that woke the run —
        # and reported `2 further surface pages omitted`, a sentence from
        # which no reader can recover what to open. Naming is tens of bytes
        # against a budget of tens of thousands, and it is charged to nothing:
        # the count was already being rendered, and the count was the part
        # that carried no information.
        #
        # Each name also carries a heading gist (#1111) — a page dropped
        # whole leaves nothing else a reply could point at. Same reasoning
        # as the name itself: uncharged, because the alternative is a
        # coordinate that silently stops existing the moment a page misses
        # even the placeholder.
        noun = "page" if len(unannounced) == 1 else "pages"
        named = " · ".join(
            f"`{relative}`" + (f" ({gist})" if (gist := _page_heading_gist(content)) else "")
            for relative, content in unannounced
        )
        blocks.append(
            f"_({len(unannounced)} further surface {noun} omitted — the "
            f"surface budget was exhausted: {named} · read them under "
            f"`{surface}`)_"
        )

    if not blocks:
        text = (
            "## Work surface\n\n"
            "No authored surface yet. Start at `surface/index.md`; pages placed "
            "under `surface/` are discovered by the next wake and dashboard."
        )
        return TrimResult(text=text), frozenset()
    text = (
        "## Work surface\n\n"
        "The shared user/resident orientation, discovered from one authored "
        f"root: `{surface}`. Add, move, or link Markdown there; do not create "
        "parallel orientation roots elsewhere in home. The dashboard mirrors "
        "the same discovered set. Cite a page as `path §Heading` — the path "
        "is each block's own heading below, the heading is one of its own "
        "`## ` lines. Unlike kb pages, no page URL exists for these.\n\n"
        + "\n\n---\n\n".join(blocks)
    )
    worst = _worst_trim(trims)
    return (
        TrimResult(
            text=text,
            newest_item=worst.newest_item,
            oldest_item=worst.oldest_item,
            dropped=worst.dropped,
            source_newest=worst.source_newest,
            stale=worst.stale,
            precise=worst.precise,
        ),
        frozenset(whole_paths),
    )


def _build_work_surface_block(repo_root: Path) -> str:
    """Render the discovered shared work surface as one orientation block.

    Membership is filesystem-authored: every non-hidden Markdown file below
    ``surface/`` rides the wake without a new prompt mount. ``index.md`` leads;
    all remaining files follow by relative path. A total budget bounds the
    surface while preserving each accreting page's newest entries.
    """
    return _build_work_surface_block_scored(repo_root)[0].text


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


def _build_web_capability_block(runner_shell: str | None) -> str:
    """Declare this wake's native web-research capability (issue #411 L0).

    Renders 1–2 ``- Web research:`` lines for the bundle's Mode section —
    the one place both resident and strand wakes read runner facts (the
    resident-only injected-block stack is skipped for strands, and a strand
    needs this fact just as much).  The declaration itself lives in the
    packaged capabilities data (:mod:`brr.runner_capabilities`), keyed by
    Shell: whether a wake can verify a changing fact is a property of the
    CLI's tool surface, not of the Core inside it.

    Declared Shell → name the native tools and that search executes
    server-side (rides the model API, so it survives the solitary egress
    boundary).  Unknown/custom Shell → say web verification is undeclared
    and to verify from repo/local sources or state the limit — never guess.
    """
    from .runner_capabilities import web_research_for_shell

    cap = web_research_for_shell(runner_shell)
    if cap is None:
        return (
            "- Web research: not declared for this Shell — verify changing "
            "facts from repo/local sources, or state the limit in your "
            "reply rather than guessing."
        )
    tools = "/".join(cap.tools)
    execution = cap.execution or "server-side"
    default_note = ", default-on" if cap.default_on else ""
    return (
        f"- Web research: native via {tools}{default_note} — search executes "
        f"{execution} (rides the model API, so it is available even under "
        "the solitary egress boundary); use it to verify changing facts "
        "before asserting them."
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

    # Two kinds, handled differently (2026-07-15). *Integrity* findings are
    # specific inconsistencies with a specific fix — fold them in. *Size*
    # findings are not: a byte count cannot tell a load-bearing page from
    # bloat, and a per-page nag every wake trained the wrong reflex (compress
    # the longest page — often the one whose length is the point). The
    # reasonable idea underneath — own the kb, don't let it silt into a
    # long-tail cemetery — survives as one derived *ownership* signal that a
    # maintenance round is due, not a list of pages to trim.
    integrity = [f for f in findings if f.type not in _KB_SIZE_FINDINGS]
    size_pressure = [f for f in findings if f.type in _KB_SIZE_FINDINGS]
    # Dominion pitfall findings used to ride this block (#985) under an
    # explicit apology — "they are not kb pages, but this is the one
    # deterministic notice channel a resident already reads on wake." They
    # now have their own: `_build_notes_health_block`, which owns every
    # *note surface* the resident writes, the pitfall store included. This
    # block is about the shared kb again, and only that.
    stats = kb_health.compute_graph_stats(repo_root, kb_dir)
    ownership = _kb_ownership_signal(size_pressure, stats)
    # `cfg` rather than a bare `repo_root`: the mirror's identity check asks
    # which account knowledge repo *this wake* reads, and the answer has to be
    # the one every other line in this block was computed from. Letting
    # `mirror_state` load its own config would make the check disagree with its
    # own caller for a reason no reader could see (#676).
    mirror = _kb_mirror_signal(knowledge.mirror_state(repo_root, cfg))

    if not integrity and not ownership and not mirror:
        return ""

    sections: list[str] = []
    # Mirror first: it is not a page to fix but a statement about whether the
    # pages below are the whole set. A findings list read against a mirror
    # three commits behind is a report about yesterday's kb.
    if mirror:
        sections.append(mirror)
    if integrity:
        sections.append(
            "**Integrity** — specific inconsistencies with a specific fix; "
            "fold these into your work where they touch it:\n\n"
            + kb_preflight.format_findings(integrity)
        )
    if ownership:
        sections.append(ownership)

    return (
        "## kb health (deterministic preflight)\n\n"
        "The shared `kb/` is yours to keep coherent (governed by `AGENTS.md`); "
        "leave it no worse than you found it.\n\n"
        + "\n\n".join(sections)
    )


# Size findings are a maintenance *signal*, not per-page work — see
# :func:`_build_kb_health_block` and :func:`_kb_ownership_signal`.
_KB_SIZE_FINDINGS = frozenset({"oversized-page", "recent-log-budget-exceeded"})


def _build_notes_health_block(repo_root: Path) -> str:
    """Render the note-surface preflight as a wake-time block.

    The sibling of :func:`_build_kb_health_block`, and deliberately its
    sibling rather than a section inside it. That block is about the shared
    ``kb/``, which many readers own; this one is about the surfaces the
    *resident* writes and the *resident* is narrowed by — the dominion's
    pitfall store and self-inject manifest, the account work surface, the
    two-party ``workflow.md``. #985's inert-pitfall notice used to ride the
    kb block under an explicit apology for being there; it rides here now,
    with the two checks that had nowhere to live at all.

    Same contract as every deterministic preflight on this path:
    **silent when clean** (returns ``""``), one findings block when not,
    zero model cost. Silenced by ``kb_maintenance=never`` too — one switch
    for "no deterministic maintenance nagging on this repo", rather than a
    second knob a reader has to discover after turning the first one off.

    See :mod:`brr.notes` for the registry these checks read, and
    :mod:`brr.notes_preflight` for what each check can and cannot prove.
    """
    from . import config as conf
    from . import notes_preflight

    cfg = conf.load_config(repo_root)
    if str(cfg.get("kb_maintenance", "auto")).strip().lower() == "never":
        return ""
    findings, scope = notes_preflight.scan_scoped(repo_root, cfg)
    if not findings:
        return ""
    # The scope line rides *above* the findings, not below them. A findings
    # list read without its denominator is a report of unknown coverage,
    # and the one thing a reader must know before triaging the list is how
    # much of the registry it is a claim about.
    return (
        "## notes health (deterministic preflight)\n\n"
        "Your own note surfaces — dominion, work surface, run controls. Each "
        "line is a place where what you wrote and what the reader parses have "
        "come apart, so the surface renders as filed and behaves as absent. "
        "Every finding names the code that decided it; check it before you "
        "act on it.\n\n"
        f"_Scope: {scope.line()}._\n\n"
        + notes_preflight.format_findings(findings)
    )


def _kb_mirror_signal(state) -> str:
    """One line when the ``.brnrd-kb/`` mirror is behind its origin — else "".

    Follows ``_build_knowledge_sources_block``'s precedent (*"a marker nothing
    surfaces is a guardrail that doesn't guard"*) for the same class of fact:
    #659 gave every mirror skip a reason and sent it to the daemon log, one
    layer short of the resident who reads a wake prompt. This renders the
    *state* instead of replaying the event — read fresh each wake by
    :func:`knowledge.mirror_state`, so it is never a note about something that
    was true an hour ago.

    Silent in two of four cases, for two different reasons:

    - **current** — the #623 discipline. A guard that fires every wake for a
      non-reason stops being read, and takes the wakes where it *is* a reason
      down with it.
    - **absent** — no checkout, detached HEAD, no ``origin/<branch>``. A repo
      with no mirror has no mirror to be stale, and does not want a line every
      wake saying so. Silence here is *not* the same value as silence above:
      :class:`knowledge.MirrorState` keeps them apart as distinct statuses, so
      nothing downstream can read "absent" as "0 behind" and call it healthy.
      The rendering collapses them; the model does not.

    The sentence names ``git status`` explicitly, because the reader's likely
    next move is to check it, and it will read clean — that clean status is
    precisely the defect (a mirror's whole problem is that it has no way to
    say it is stale) rather than evidence against this line.

    The fourth case, ``elsewhere`` (#676), speaks *instead of* the counts: a
    checkout cloned from some other repository is current with respect to the
    wrong origin, so "0 behind" is true and worthless. Its sentence names both
    paths, because the reader cannot check this one by hand without them —
    ``git status`` reads clean there too, and so does the count.
    """
    from . import knowledge

    if state.status == knowledge.MIRROR_ELSEWHERE:
        return (
            f"**Mirror** — `{knowledge.CHECKOUT_DIRNAME}/` is a clone of "
            f"`{state.origin}`, but this repo's account knowledge repo is "
            f"`{state.expected_origin}`. It is a mirror of the **wrong "
            "repository**: it can be perfectly up to date and still never "
            "carry this account's pages, so treat `brnrd kb` results and "
            f"anything under `{knowledge.CHECKOUT_DIRNAME}/` as somebody "
            "else's until this is fixed. Nothing on the wake path repairs it "
            "— running `brnrd kb` does, by re-cloning on exactly this "
            "mismatch. Check for unpushed work there first; a re-clone "
            "discards the directory."
        )
    if state.status != knowledge.MIRROR_BEHIND:
        return ""
    upstream = f"origin/{state.branch}" if state.branch else "origin"
    plural = "" if state.behind == 1 else "s"
    head = (
        f"**Mirror** — `{knowledge.CHECKOUT_DIRNAME}/` is {state.behind} "
        f"commit{plural} behind `{upstream}`"
    )
    # Three tails, three next moves. Ordered most-actionable first: a diverged
    # mirror will never fast-forward on its own, a dirty one is waiting on you,
    # a clean one resolves itself and only needs to be known about.
    if state.ahead:
        tail = (
            f", and {state.ahead} ahead of it — the histories have **diverged**, so "
            "no read path will reconcile this for you (`--ff-only` correctly "
            "refuses). Fetch and rebase the checkout by hand before you trust "
            "`brnrd kb` to be complete."
        )
    elif state.dirty:
        tail = (
            ". The checkout has uncommitted work, which is *why* it was left "
            "behind — the fast-forward skipped rather than clobber an in-flight "
            "edit. Commit or discard it and the next capture catches the mirror up."
        )
    else:
        tail = (
            " — and `git status` there reads clean, which is why nothing else "
            "told you. The next capture fast-forwards it; until then treat "
            "`brnrd kb` results as possibly missing recent pages."
        )
    return head + tail


def _kb_ownership_signal(size_findings: list, stats) -> str:
    """One derived line when the graph is asking for a maintenance round.

    Replaces the per-page size nag (2026-07-15). Fires on accumulated size
    pressure or orphaned pages and says *own a round* — promote / breadcrumb /
    cut / relink — rather than *trim page X*, which is the judgment a byte count
    cannot make and the resident can.

    A follow-up worth its own change: gate this on *staleness* (wakes since the
    last ownership round) rather than absolute size, so a kb that is legitimately
    large-and-tended stops signalling. That needs a piece of state this does not
    yet carry; until then the signal is at least a single line, not one per page.
    """
    pressure = len(size_findings)
    orphan_list = getattr(stats, "peer_orphans", []) or []
    if not pressure and not orphan_list:
        return ""
    bits = []
    if pressure:
        bits.append(f"{pressure} page(s)/log over a size threshold")
    if orphan_list:
        names = [Path(p).name for p in orphan_list]
        if len(names) <= 3:
            label = ", ".join(names)
        else:
            label = ", ".join(names[:3]) + f" … and {len(names) - 3} more"
        bits.append(f"{len(orphan_list)} indexed page(s) no peer links to ({label})")
    return (
        "**Ownership signal** — " + "; ".join(bits) + ". Not a list of pages to "
        "trim: a byte count cannot tell a load-bearing page from bloat — you can. "
        f"The graph is {stats.total_pages} pages, log {stats.log_bytes:,} B over "
        f"{stats.log_entry_count} entries. Read this as the kb asking for a "
        "maintenance *round* — promote what's load-bearing, breadcrumb what's "
        "spent, cut what's dead, relink the orphans. Strand-delegable; worth a "
        "dedicated pass, not a per-wake reflex to shorten the longest file. Full "
        "graph shape on demand: `brnrd kb`."
    )


def _build_knowledge_sources_block(repo_root: Path) -> str:
    """Render the compact home→repo→docs knowledge slice.

    Leads with the knowledge chain's divergence warning when brr's last push
    of the knowledge repo was rejected. A marker nothing surfaces is a
    guardrail that doesn't guard: the whole point of not swallowing a
    rejected push is that the next resident awake sees it and reconciles.

    Falls back to the never-linked banner when there was no push to fail —
    the account knowledge repo has no remote at all (#1423). The two
    warnings are mutually exclusive by construction (the never-linked
    marker is only set while the repo has no remote; `needs_sync` is only
    set while it has one and a push just failed), so at most one renders.
    """

    from . import account
    from . import config as conf
    from . import gitops
    from . import knowledge

    cfg = conf.load_config(repo_root)
    block = knowledge.render_injection(repo_root, cfg)
    brr_dir = gitops.shared_brr_dir(repo_root)
    diverged = knowledge.needs_sync(brr_dir)
    if diverged:
        warning = _sync_marker_banner(
            status=knowledge.needs_sync_status(brr_dir),
            reason=diverged,
            subject="the knowledge remote",
            repo_path=str(brr_dir),
            stakes=(
                "Until it lands, the kb pages this run writes will not reach "
                "the archive, and they will not be linkable."
            ),
        )
        return f"{warning}\n\n{block}" if block else warning
    try:
        home_knowledge = account.knowledge_path(
            account.resolve_context(repo_root, cfg, create=False),
        )
    except Exception:  # noqa: BLE001 - a wake block must never break on this
        home_knowledge = None
    unlinked = (
        knowledge.never_linked(brr_dir, home_knowledge)
        if home_knowledge is not None else None
    )
    if not unlinked:
        return block
    warning = _never_linked_banner(
        subject="the knowledge remote", repo_path=str(home_knowledge),
    )
    return f"{warning}\n\n{block}" if block else warning


def _never_linked_banner(*, subject: str, repo_path: str) -> str:
    """One standing status line for a repo that has never had a remote (#1423).

    Deliberately **not** built from :func:`_sync_marker_banner` — that one
    renders a *push failure*: an attempt was made and it didn't land, so it
    carries a classified reason and a stakes clause about what's pending.
    This one renders a standing *state*: no attempt was ever possible,
    because there has never been anywhere to push to. Different fact,
    different remedy (`brnrd home link`, not "reconcile the remote"), so it
    reads differently rather than being squeezed into the same shape with a
    placeholder reason.

    Byte-stable for a given *subject* / *repo_path* pair (no timestamp, no
    run-scoped detail) so the line reads as the same ambient fact on every
    wake it renders on, not a fresh event each time.
    """
    return (
        f"**{subject} has never been linked** — `{repo_path}` carries no git "
        "remote, so brr has never had anywhere to push this memory; it has "
        "lived on this machine only since it began. Not a push failure to "
        "reconcile — there is no remote to reconcile against. "
        "`brnrd home link` is the whole remedy."
    )


def _sync_marker_banner(
    *,
    status: str | None,
    reason: str,
    subject: str,
    repo_path: str,
    stakes: str,
) -> str:
    """Render a capture-push failure for the wake, keyed on its class.

    The marker carries its own classification (#786); this reads it rather
    than re-deriving one from the sentence. Before that, both banners
    hard-coded *"has diverged … reconcile by hand"* around whatever reason
    the marker held — so an auth failure arrived wearing a merge
    prescription, and two consecutive wakes went looking for a divergence
    that did not exist while the repo sat 22 commits ahead and 0 behind.

    An unknown class renders as unknown. That is the point: the old
    behaviour's defect was not a wrong label, it was a *default* label.
    """
    from . import gitops

    if status == gitops.PushStatus.REJECTED_NON_FAST_FORWARD.value:
        head = f"**{subject} has diverged**"
        body = (
            "brr's last push was rejected because the remote moved — another "
            "machine or session wrote it too. Nothing is lost (it is "
            "committed locally), but reconciling is yours (a merge is "
            f"judgement, not a reflex): in `{repo_path}` fetch, merge / "
            "resolve any conflicts, and push."
        )
    elif status in (
        gitops.PushStatus.AUTH_FAILED.value,
        gitops.PushStatus.UNREACHABLE.value,
    ):
        verb = (
            "could not authenticate to"
            if status == gitops.PushStatus.AUTH_FAILED.value
            else "could not reach"
        )
        head = f"**brnrd {verb} {subject}**"
        body = (
            "Nothing is lost and **nothing has diverged** — there is no merge "
            "to do. The local history is intact and ahead; the push simply "
            "never landed. Fix the credential or the network, or say so and "
            "move on: a repair you cannot make is a report, not a blocker."
        )
    else:
        head = f"**brnrd could not push {subject}**"
        body = (
            "The failure did not match a known class, so it is reported "
            "unclassified rather than guessed at. Nothing is lost; do not "
            "assume a merge is needed until the reason below says so."
        )
    return f"{head} — {body} {stakes} (Reason on record: {reason})"


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
    repo_root: Path,
    *,
    task_text: str | None = None,
    resolved_prs: dict[int, str] | None = None,
) -> tuple[list[tuple[str, str]], list["ContractEntry"], "frozenset[Path]"]:
    """The scored implementation behind ``_build_injected_blocks``.

    Returns the rendered blocks **keyed** — ``(block_key, text)`` pairs, in
    prompt order — plus a :class:`ContractEntry` list, the source manifest for
    every block considered.  Blocks that are absent this run (empty file, nothing
    to inject) still appear in the manifest with ``present=False`` so ``brnrd
    prompts show`` can report the full picture.  Third: the set of work-surface
    page paths this call's ``## Work surface`` block emitted **whole** (#628) —
    see ``_build_work_surface_block_scored``. ``_build_orientation_set`` reads
    it as ``injected_whole`` so the orientation walk never re-bills a Read the
    wake was already handed.

    The keys are not decoration.  A caller that mounts some blocks as a resumed
    transcript (``boot.mount``) must take exactly those blocks *out of the
    prose*, or the wake pays for them twice and the T-vs-P experiment measures
    nothing.  An unkeyed ``list[str]`` made that subtraction impossible to state;
    a keyed one makes it a dict lookup.

    ``resolved_prs`` (#1137) passes straight through to
    ``_build_work_surface_block_scored`` — the caller's own join of this
    wake's forge-state snapshot, when one is available (see
    ``build_daemon_prompt_with_score``); ``None`` renders the work-surface
    block exactly as before, with no stale-refs annotation.

    Shared by ``_build_injected_blocks``, ``build_injected_context``, and
    the scored prompt-builder variants — one computation, three consumers.
    """
    from .bootscore import (
        ContractEntry,
        OWNER_PRODUCT, OWNER_RESIDENT, OWNER_PROJECT, OWNER_DAEMON_LIVE,
        AUTHORITY_IDENTITY, AUTHORITY_MEMORY, AUTHORITY_SURFACE, AUTHORITY_POLICY,
        AUTHORITY_KNOWLEDGE, AUTHORITY_ACTIVITY, AUTHORITY_HEALTH, AUTHORITY_HEARTH,
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

    # 1b. Stranded memory after a repo move. Ahead of the dominion digest on
    # purpose: it explains why that block is about to be empty, and a wake that
    # reads the emptiness first has already started re-deriving.
    relabelled_block = _build_relabelled_repo_block(repo_root)
    contracts.append(ContractEntry(
        block_key="relabelled-repo",
        label="Stranded-memory warning (repo moved)",
        owner=OWNER_DAEMON_LIVE,
        authority=AUTHORITY_HEALTH,
        freshness=None,
        location="computed",
        present=bool(relabelled_block),
        bytes=_rendered_bytes(relabelled_block),
    ))
    if relabelled_block:
        keyed.append(("relabelled-repo", relabelled_block))

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

    # 2c. The hearth — shared human/resident personal space, index-shaped
    # (#1332). Grouped with identity/continuity, ahead of the work surface:
    # who-we-are before what-we-do. Never mirrored — see AUTHORITY_HEARTH
    # and `_build_hearth_block`'s own docstring for the privacy contract.
    hearth_block = _build_hearth_block(repo_root)
    contracts.append(ContractEntry(
        block_key="hearth",
        label="The hearth (personal space, index-shaped)",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_HEARTH,
        freshness=None,
        location="computed",
        present=bool(hearth_block),
        bytes=_rendered_bytes(hearth_block),
    ))
    if hearth_block:
        keyed.append(("hearth", hearth_block))

    # 3. One discovered shared orientation root.
    work_surface_trim, work_surface_whole = _build_work_surface_block_scored(
        repo_root, resolved_prs=resolved_prs
    )
    work_surface = work_surface_trim.text
    contracts.append(ContractEntry(
        block_key="work-surface",
        label="Discovered work surface",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_SURFACE,
        freshness=None,
        location="computed",
        present=bool(work_surface),
        bytes=_rendered_bytes(work_surface),
        newest_item=work_surface_trim.newest_item,
        oldest_item=work_surface_trim.oldest_item,
        dropped=work_surface_trim.dropped,
        source_newest=work_surface_trim.source_newest,
        stale=work_surface_trim.stale,
    ))
    if work_surface:
        keyed.append(("work-surface", work_surface))

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

    # 5. Pitfalls matching the task — trigger-gated on task_text. No task_text
    # means the block was never weighed (bytes=None), not weighed-and-empty
    # (bytes=0): those are different facts about the *invocation*, and
    # ContractEntry.bytes is documented as three-state precisely so this
    # distinction survives. See #1181.
    if task_text:
        pitfalls_block = _build_pitfalls_block(repo_root, task_text)
        pitfalls_bytes = _rendered_bytes(pitfalls_block)
    else:
        pitfalls_block = ""
        pitfalls_bytes = None
    contracts.append(ContractEntry(
        block_key="pitfalls",
        label="Task-matched pitfalls",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_MEMORY,
        freshness=None,
        location="computed",
        present=bool(pitfalls_block),
        bytes=pitfalls_bytes,
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
    context_trim = _build_context_block_scored(repo_root)
    context = context_trim.text
    contracts.append(ContractEntry(
        block_key="recent-activity",
        label="Recent activity (kb/log.md tail)",
        owner=OWNER_DAEMON_LIVE,
        authority=AUTHORITY_ACTIVITY,
        freshness=None,
        location="computed",
        present=bool(context),
        bytes=_rendered_bytes(context),
        newest_item=context_trim.newest_item,
        oldest_item=context_trim.oldest_item,
        dropped=context_trim.dropped,
        source_newest=context_trim.source_newest,
        stale=context_trim.stale,
    ))
    if context:
        keyed.append(("recent-activity", context))

    # 8b. The resident's own last run node (wyrd §5)
    prior_run = _build_prior_run_block(repo_root)
    contracts.append(ContractEntry(
        block_key="prior-run",
        label="Your last run (node frame + Now + shape)",
        owner=OWNER_RESIDENT,
        authority=AUTHORITY_MEMORY,
        freshness=None,
        location="computed",
        present=bool(prior_run),
        bytes=_rendered_bytes(prior_run),
    ))
    if prior_run:
        keyed.append(("prior-run", prior_run))

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

    # 9b. notes health — the resident's own surfaces, beside the shared kb's
    notes_health_block = _build_notes_health_block(repo_root)
    contracts.append(ContractEntry(
        block_key="notes-health",
        label="notes health (deterministic preflight)",
        owner=OWNER_DAEMON_LIVE,
        authority=AUTHORITY_HEALTH,
        freshness=None,
        location="computed",
        present=bool(notes_health_block),
        bytes=_rendered_bytes(notes_health_block),
    ))
    if notes_health_block:
        keyed.append(("notes-health", notes_health_block))

    return keyed, contracts, work_surface_whole


def _build_injected_blocks(
    repo_root: Path,
    *,
    task_text: str | None = None,
    resolved_prs: dict[int, str] | None = None,
) -> list[str]:
    """The standing, always-on context blocks brr injects into every wake.

    Returns the *base* blocks:

    1. Resident identity core — product-owned invariant contract
    2. Dominion digest (living playbook + ``self-inject``)
    3. The hearth — personal space, index-shaped (#1332)
    4. Discovered work surface — the shared authored orientation
    5. Stored runner policy (CS6) — standing runner preferences
    6. Pitfalls matching the task
    7. Recent-activity log tail
    8. kb health note
    9. notes health note — the resident's own surfaces (:mod:`brr.notes`)

    The ordering puts the product identity contract before the resident-owned
    state (dominion + hearth + work surface + policy), then the shared
    project history, so a waking can distinguish authority layers in read
    order.

    Shared by ``_join_prompt_parts`` and ``build_injected_context``; whatever
    block is added here surfaces in both paths with no drift.  Mode-toggle
    blocks (diffense, introspection) sit on top of these; they are added by
    ``_join_prompt_parts`` (for the full runner prompt) and by
    ``build_injected_context`` (for the faithful inject-tool view).

    Delegates to ``_build_injected_blocks_with_contracts`` and discards the
    contracts list and the keys — the scored variant is the single implementation.
    """
    keyed, _, _ = _build_injected_blocks_with_contracts(
        repo_root, task_text=task_text, resolved_prs=resolved_prs
    )
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
    strand: bool = False,
    prepared_injected_blocks: list[str] | None = None,
    prepared_introspection_block: str | None = None,
    resolved_prs: dict[int, str] | None = None,
) -> str:
    """Stitch preamble, optional recent-context block, and trailer.

    ``inject_blocks=False`` skips the resident stack entirely — the base
    injected blocks (identity core, dominion digest, work surface, runner
    policy, pitfalls, knowledge sources, kb health) and the
    introspection dev-mode block.

    ``strand=True`` *narrows* ``inject_blocks`` rather than defeating it —
    that's the B4 strand trim: a bounded strand wake gets its task and
    files, not the standing resident context, with one exception (#1185).
    Pitfalls are the account's failure-memory lookup, matched fresh against
    each run's own task text, and a strand is single-shot — the one run
    shape that cannot learn from its own patterns and so benefits most per
    byte from being handed this list. The fallback build (used when no
    scored variant pre-built the blocks — ``prepared_injected_blocks`` is
    ``None``) picks the pitfalls-only slice for a strand and the full stack
    otherwise; ``prepared_injected_blocks``, when supplied, is honored
    as-is (the caller — :func:`build_daemon_prompt_with_score` — already
    scoped it the same way). The introspection dev-mode invitation stays
    resident-only regardless: it invites a look at "the whole shape ... just
    read", a shape a strand was never given. The ``diffense`` review-pack
    step is independent of the trim (a strand wake asking for diffense is
    out of scope for now; whatever the caller passes is honored as-is).
    """
    # The kernel leads.  Everything after it is reference the wake may consult;
    # the kernel is the wake's own first move (``bootscore.format_kernel``).
    parts = [kernel, preamble] if kernel else [preamble]
    if inject_blocks:
        # The scored builder supplies this pair from one source read.  The
        # ordinary path stays lazy, but a replay/inspection run must not
        # build the prompt and its manifest from two independently-read
        # views of dominion and knowledge state.
        if prepared_injected_blocks is not None:
            parts.extend(prepared_injected_blocks)
        elif strand:
            strand_keyed, _ = _build_strand_pitfalls_contract(repo_root, task_text)
            parts.extend(text for _, text in strand_keyed)
        else:
            parts.extend(
                _build_injected_blocks(
                    repo_root, task_text=task_text, resolved_prs=resolved_prs
                )
            )
    if diffense:
        pack_step = read_prompt("diffense.md", repo_root)
        if pack_step:
            parts.append(pack_step)
    if inject_blocks and not strand:
        # Last framing before the task: invite the resident to look at the
        # whole shape it has just read (opt-in dev mode). Placed here so it
        # can refer to everything above and sit fresh against the task
        # bundle. Never for a strand — see docstring.
        introspection_block = (
            prepared_introspection_block
            if prepared_introspection_block is not None
            else _build_introspection_block(repo_root)
        )
        if introspection_block:
            parts.append(introspection_block)
    parts.append(trailer)
    return "\n\n".join(parts)


def _extract_markdown_sections(text: str, headings: list[str]) -> str:
    """Pull named ``##``/``###`` sections out of *text*, verbatim, in source order.

    A *section* runs from a heading line (matched by an exact, stripped string in
    *headings*) to the line before the next ``## `` / ``### `` heading, or end of
    text. Deeper headings (``#### ``) don't count as boundaries — they stay inside
    whichever section they're nested in. A heading not found is silently skipped:
    the same "smaller honest set over a padded one" instinct :func:`_build_orientation_set`
    already applies — a doc that renames a section should drop it from this extract
    rather than raise mid-wake.

    Nothing here is invented text: every byte returned is a literal substring of
    *text*, so mounting this as a seeded ``Read`` result of the file it came from
    stays honest even though the result skips the material between sections.
    """
    lines = text.splitlines()
    heading_idxs = [
        i for i, ln in enumerate(lines)
        if ln.startswith("## ") or ln.startswith("### ")
    ]
    wanted = set(headings)
    found: list[tuple[int, str]] = []
    for pos, idx in enumerate(heading_idxs):
        if lines[idx].strip() not in wanted:
            continue
        end = heading_idxs[pos + 1] if pos + 1 < len(heading_idxs) else len(lines)
        found.append((idx, "\n".join(lines[idx:end]).rstrip()))
    found.sort(key=lambda pair: pair[0])
    return "\n\n".join(body for _, body in found)


#: The two `docs/portals.md` sections a resident needs to actually use the
#: verb grammar instead of re-deriving it from an eight-row frontmatter table.
#: Exact heading text, kept here (not re-derived) so a rename in the manual
#: makes this extract quietly empty rather than silently wrong — a diff a
#: reviewer sees, not a drift nobody notices.
_PORTAL_VERB_GRAMMAR_HEADINGS = [
    "### `brnrd do` — the verdict rides the act",
    "### `brnrd await` — the wait with nothing to forget (#959, #1187)",
]


def _build_portal_verb_grammar_block(repo_root: Path) -> str:
    """The `brnrd await` and `brnrd do` sections of the portals manual, live.

    The gap this closes: `daemon-substrate.md`'s frontmatter verb table names
    eight rows and points at `brnrd docs portals` for "the reasoning behind each
    pin" — a sentence about *reasoning*, at the bottom, after the table already
    reads as the complete grammar. Both `brnrd await` (#959) and `brnrd do` are fully
    documented there and were invisible to a wake that read the table and
    stopped, because nothing in the table said *there is more*. A run discovered
    `brnrd do` by accident.

    Live extract, not a static copy: this calls :func:`brr.docs.read_topic`
    (the same function `brnrd docs portals` prints from, override-aware) and
    slices out the two sections at mount time, so an edit to the manual is
    live here on the next wake instead of drifting from a pasted-in copy that
    needs its own commit to stay honest — the exact trap `_trim_note`'s own
    docstring names for `kb/log.md`. The cost is one file read already paid by
    every doc-topic lookup; there is no subprocess here to measure.

    Returns ``""`` when the manual is absent or has been restructured past
    recognition (heading text changed) — the same "absent block" shape every
    other preamble rider already has, not a wake-breaking failure.
    """
    from . import docs

    text = docs.read_topic("portals", repo_root)
    if not text:
        return ""
    return _extract_markdown_sections(text, _PORTAL_VERB_GRAMMAR_HEADINGS)


#: Block keys whose mountable text is *not* simply "read the file at
#: ``entry.location``" — a curated extract, not the whole file. The offline
#: inspection path (``brnrd prompts transcript``, :func:`mountable_block_text`)
#: has no other way to learn that, so it consults this registry instead of
#: reading `entry.location` raw; the alternative is that command handing a
#: resident 754 lines when the live daemon mount only ever seeded ~120 of them.
_MOUNTABLE_TEXT_BUILDERS: dict[str, Any] = {
    "portal-verb-grammar": _build_portal_verb_grammar_block,
}


def mountable_block_text(entry: "ContractEntry", repo_root: Path) -> str:
    """Reconstruct the text a mounted block would carry, straight from disk.

    Most mountable blocks *are* their file's content — ``Path(entry.location).
    read_text()`` reproduces exactly what a live daemon wake seeded. The one
    exception so far is a block built from a curated slice of a larger file
    (see :data:`_MOUNTABLE_TEXT_BUILDERS`); this is the single place that
    distinction is applied, so the offline reconstruction path
    (``brnrd prompts transcript``) and the live daemon mount cannot disagree
    about what a given block_key means.
    """
    builder = _MOUNTABLE_TEXT_BUILDERS.get(entry.block_key)
    if builder is not None:
        return builder(repo_root)
    return Path(entry.location).read_text(encoding="utf-8")


def _collect_preamble_contracts(
    repo_root: Path,
    *,
    is_strand: bool = False,
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

    # Preamble: run.md / strand.md
    entries.append(_file_entry(
        "strand.md" if is_strand else "run.md",
        block_key="strand-preamble" if is_strand else "run-preamble",
        label="Strand preamble (strand.md)" if is_strand
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

    # register.md — a *worked example* of the register (weave.md is the rules;
    # this is a being mid-wake, written in them). Resident path only: a bounded
    # strand gets the register contract but not the personality exemplar, which
    # is orientation for a light that has to sustain a whole run, not labour.
    # Rides right after weave.md so a mounted wake reads the rule then the hand.
    if not is_strand:
        entries.append(_file_entry(
            "register.md",
            block_key="register",
            label="Working register, worked example (register.md)",
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

    # Portal verb grammar — a curated extract of `docs/portals.md`, riding
    # right after daemon-substrate.md on purpose: that file's own frontmatter
    # verb table is where a resident learns the portal grammar, and the table
    # read as a *closed* set — `brnrd await` (#959) and `brnrd do` are both real,
    # fully documented in the manual, and invisible to a wake that read the
    # table and stopped. See `_build_portal_verb_grammar_block` for why this
    # is a live extract rather than a static copy.
    if is_daemon:
        from . import docs as _docs

        portal_grammar_path = _docs.effective_topic_path("portals", repo_root)
        portal_grammar_text = _build_portal_verb_grammar_block(repo_root)
        entries.append(ContractEntry(
            block_key="portal-verb-grammar",
            label="Portal verb grammar (`brnrd await` / `brnrd do`, docs portals)",
            owner=OWNER_PRODUCT,
            authority=AUTHORITY_SUBSTRATE,
            freshness=_mtime_iso(portal_grammar_path),
            location=str(portal_grammar_path),
            present=bool(portal_grammar_text),
            bytes=_rendered_bytes(portal_grammar_text),
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
    is_strand: bool,
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

    if is_daemon and not is_strand:
        steps.append(OrientationStep(
            action="write .card",
            reason="the card is the surface the user watches while you think",
        ))

    # The queue belongs to the *resident*, and only to the resident.
    #
    # This was gated on ``pending_count`` alone, and it caused a live incident on
    # 2026-07-13. ``pending_count`` is the **parent's** queue — events addressed
    # to the resident, in the resident's gate thread. A spawned strand inherited
    # it and was handed, at position 1, in the imperative:
    #
    #     next:
    #       2. answer 12 queued events — one outbox file each, `event: <id>`
    #
    # Two strands (claude-haiku, codex-mini) did exactly that: they answered
    # twelve of the user's messages to the resident, in the resident's thread,
    # with no context for any of them.
    #
    # ``strand.md`` states plainly that the dispatching conversation "is not yours
    # to hold or extend" — and it states it in *prose*, *below* this list. The
    # kernel overrode it. That is the whole thesis of the boot work confirmed
    # from the wrong end: **the imperative action-list at the hot slot is what
    # gets acted on; the prose contract beneath it is what gets skimmed.** The
    # kernel did not misfire. It worked perfectly, and carried a wrong
    # instruction with total authority.
    #
    # A strand has no gate authority, no `event:` disposition to make, and no
    # standing in that thread. It must never see this step.
    if pending_count and not is_strand:
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


#: Cap on the orientation set (#513: "3–5 files"). The cap bounds the walk's
#: cost; the *floor* is deliberately zero — a set the derivation cannot prove
#: is a set it does not pad ("the set is 3 files, not 5 with two guesses").
_ORIENTATION_SET_MAX = 5

#: The two-party contract page on the work surface — the third structural
#: role in the orientation set, beside the repo's ``AGENTS.md`` (the project's
#: contract) and ``plans/<repo>/active.md`` (the resident's own queue).  Not a
#: member of a list of nice-to-read pages: it is the file that says what a run
#: may merge, what it owes the maintainer, and what shape a reply takes, so a
#: wake that cannot cite it is a wake acting on remembered permissions.
#:
#: It earns a named slot because the work-surface block cannot be relied on to
#: carry it.  That block walks ``account.work_surface_files`` in **path
#: order** and stops at the byte budget (#1020/#1061), and ``workflow.md``
#: sorts last under ``surface/`` — so on a busy surface it is the first page
#: dropped and the wake never learns it went missing.  Naming it here is not a
#: second copy: ``injected_whole`` (#628) removes it from the walk on every
#: wake the surface block *did* hand it over whole, so the slot is spent only
#: on the wakes that would otherwise have had nothing.
_WORKFLOW_PAGE = "workflow.md"

#: The account-wide backchannel queue's home-relative surface path (#1061
#: rec 3) — the one page ``_build_work_surface_block_scored`` compresses to
#: handles-only before it enters the budget walk.
_BACKCHANNEL_PAGE = "backchannel.md"


def _kb_hub_matches(slug: str, task_text: str) -> bool:
    """Deterministic touched-subject test: every token of *slug* in the task.

    A ``subject-<slug>.md`` hub is "touched" iff **all** of the slug's
    hyphen-separated tokens appear as substrings of the lowercased task text.
    Deliberately strict — a one-token overlap ("boot" in a task about boots
    *and* a hub about boot-sequences) is how a guess would sneak in wearing a
    match's clothes.  Provably wrong-able either way: given the task text and
    the slug, anyone can recompute the answer.
    """
    tokens = [t for t in slug.lower().split("-") if t]
    if not tokens:
        return False
    haystack = task_text.lower()
    return all(t in haystack for t in tokens)


def shell_reads_agents_md_natively(shell: str | None) -> bool:
    """Does *shell* put ``AGENTS.md`` in the model's context without being asked?

    One named fact with one home, because two surfaces depend on it and they
    were drifting apart: ``prompts/run.md`` tells the resident *"Shell-dependent:
    some Shells read it natively (codex), others don't (claude)"*, while
    :func:`_build_orientation_set` used to list the file for every Shell alike.

    ``None``/unknown answers ``False`` — the conservative direction. A walk
    entry for a file already in context costs one redundant Read; a missing
    entry for a file nobody read costs the orientation.
    """
    if not shell or not shell.strip():
        return False
    return shell.split()[0].strip() == "codex"


def _build_orientation_set(
    repo_root: Path,
    *,
    task_text: str | None = None,
    runner_shell: str | None = None,
    injected_whole: "frozenset[Path] | set[Path] | None" = None,
) -> list[Any]:
    """The orientation *ledger*'s file set (#513 Slice 9) — never the kernel's
    ``next:`` list (that is :func:`_build_orientation`; see
    :class:`brr.bootscore.OrientationFile` for why the two words coexist).

    Deterministic, existence-proven, capped at :data:`_ORIENTATION_SET_MAX`:

    - the repo's ``AGENTS.md`` — **unless the Shell already read it**, see
      :func:`shell_reads_agents_md_natively`;
    - the active inter-run plan (``account.active_plan_path``);
    - the work surface's two-party contract page (:data:`_WORKFLOW_PAGE`),
      which sorts last in the surface block's path-order walk and is
      therefore the page a busy surface drops first;
    - every ``subject-*.md`` kb hub whose slug the task text provably touches
      (:func:`_kb_hub_matches`), from the same home-knowledge dir the recent-
      activity tail reads (:func:`_home_knowledge_log_path`), in sorted-name
      order.

    These files a wake ought to read **in addition to** what it was handed —
    the set never justifies removing a block from injection (#513's guard
    rail: what must be *known* stays injected; what builds *ownership*
    becomes the walk).  Anything unresolvable is simply absent: a smaller
    honest set over a padded one, every time.

    The Shell conditional serves that same guard rail rather than bending it.
    On codex, ``AGENTS.md`` is the set's largest entry by far and is already in
    the model's context, so listing it asked the wake to spend a Read on a file
    it was holding — the polling tax the identity core names, charged by the
    meter that exists to make orientation honest. What must be known is still
    known; only the walk stops claiming credit for it.

    ``injected_whole`` (#628) applies that same rule to the active plan (and,
    in principle, any future candidate under the work-surface root): a
    resolved candidate path present in this set was handed over **whole** by
    the ``## Work surface`` block earlier in the same prompt, so naming it
    again here would bill the wake for a Read of a file it is already
    holding — the exact defect ``AGENTS.md``'s Shell conditional fixed for one
    file, generalized. A candidate the surface block **trimmed or skipped**
    (budget-exhausted, oversized) is not in this set and stays in the walk —
    the one case dropping the plan unconditionally would have broken.
    """
    from .bootscore import OrientationFile

    whole = injected_whole or frozenset()

    candidates: list[Path] = []
    if not shell_reads_agents_md_natively(runner_shell):
        candidates.append(repo_root / "AGENTS.md")

    try:
        cfg = conf.load_config(repo_root)
        ctx = account.resolve_context(repo_root, cfg, create=False)
        label = account.repo_label(repo_root, cfg)
        candidates.append(account.active_plan_path(ctx, label))
        candidates.append(account.work_surface_path(ctx) / _WORKFLOW_PAGE)
    except Exception:  # noqa: BLE001 — orientation must never fail a wake
        pass

    if task_text and task_text.strip():
        log_path = _home_knowledge_log_path(repo_root)
        if log_path is not None:
            try:
                hubs = sorted(log_path.parent.glob("subject-*.md"))
            except OSError:
                hubs = []
            for hub in hubs:
                if _kb_hub_matches(hub.stem[len("subject-"):], task_text):
                    candidates.append(hub)

    entries: list[Any] = []
    for path in candidates:
        if len(entries) >= _ORIENTATION_SET_MAX:
            break
        try:
            resolved = path.resolve()
            size = resolved.stat().st_size
        except OSError:
            continue
        if not resolved.is_file() or size == 0:
            # An empty file orients nobody; a meter counting it would be
            # asking for a Read with no reading.
            continue
        if resolved in whole:
            # Already handed over whole by the work-surface block — the walk
            # names only what the wake was NOT already given (#628).
            continue
        entries.append(OrientationFile(path=str(resolved), bytes=size))
    return entries


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
    runner_shell: str | None = None,
) -> list[Any]:
    """Return a :class:`BootHook` list for the abstract phase set.

    A pure function of its arguments — every caller supplies what it actually
    knows, and nothing is inferred from ambient process state:

    - ``declared`` is always ``True``: the abstract phases are the daemon's
      back-channel contract.
    - ``installed`` is three-state — ``True`` (wired), ``False`` (this Shell
      cannot take the config), ``None`` (*unknown from here*).  The daemon
      passes the fact it holds; the CLI probes; nobody guesses.  Reporting
      "not-installed" for "I cannot see from here" is how a live hook told the
      only operator looking that it was dead.
    - ``last_fired`` is per phase.  A post-tool hook firing says nothing about
      session-start, so a single stamp is never copied across all four.
    - ``pre-tool`` (#1184) is the one phase not every flavour installs even
      when ``installed`` is otherwise true: codex's own hooks docs verify
      only PostToolUse/Stop/SessionStart (``hooks.codex_hook_args`` carries
      no ``PreToolUse`` override), so a codex run reporting it installed
      alongside the other three would be exactly the unbacked claim this
      function otherwise refuses to make. ``runner_shell`` — the same Shell
      identity the Mode block already names — downgrades just that one cell
      to ``False`` for codex; every other phase keeps the caller's single
      fact unchanged. A profile whose declared ``hooks:`` flavour diverges
      from its ``shell:`` is the one shape this narrower check cannot see —
      out of scope here, same as it was before this phase existed.
    """
    from . import hooks as _hooks
    from .bootscore import BootHook

    stamps = hook_stamps or {}

    def _phase_installed(phase: str) -> bool | None:
        if phase == _hooks.PHASE_PRE_TOOL and runner_shell == "codex":
            return False
        return installed

    return [
        BootHook(
            name=phase,
            declared=True,
            installed=_phase_installed(phase),
            last_fired=str(stamps[phase]) if stamps.get(phase) else None,
        )
        for phase in _hooks.PHASES  # ("post-tool", "stop", "session-start", "pre-tool")
    ]


def build_boot_score(
    repo_root: Path | None = None,
    *,
    is_daemon: bool = True,
    is_strand: bool = False,
    runner_name: str | None = None,
    runner_shell: str | None = None,
    runner_core: str | None = None,
    environment: str | None = None,
    event_ids: tuple[str, ...] = (),
    event_created: str | None = None,
    event_retry_of: str | None = None,
    event_retry_failure_kind: str | None = None,
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
    injected_whole: "frozenset[Path] | None" = None,
    hooks_installed: bool | None = None,
    hook_stamps: dict[str, str] | None = None,
    mounted: bool = False,
) -> "BootScore":
    """Assemble a :class:`BootScore` for inspection without building the full prompt.

    Used by the daemon (every wake), ``brnrd prompts show``, and the replay
    test harness.  Deterministic and network-free.  When ``repo_root`` is
    ``None`` the inject-blocks contracts reflect only the bundled product
    templates (no dominion, no plan, no knowledge sources).

    ``injected_whole`` (#628) is the set of work-surface page paths the
    ``## Work surface`` block handed over **whole** this wake — the fact
    ``_build_orientation_set`` subtracts so the walk never re-bills a Read
    the wake already has. A caller that already built the real inject stack
    (and so already knows this set, byte for byte) should pass it; a caller
    that supplied ``contracts`` pre-built without it (the kernel's own cheap
    path in :func:`build_daemon_prompt`, which skips the manifest scan for
    cost) leaves it ``None`` and this function derives it independently, from
    the same :func:`_build_work_surface_block_scored` the real render calls —
    deterministic, so the two calls cannot disagree. This is the fix for the
    exact drift class the comment above ``build_daemon_prompt``'s kernel call
    names: *every argument that feeds* ``orientation_set`` *must be supplied
    (or independently derivable) at every call site that builds one.*

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
        DEPTH_COMPACT, SCHEMA_VERSION, event_age_seconds,
    )

    effective_root = repo_root if repo_root is not None else Path.cwd()

    if contracts is None:
        # Preamble + substrate + toggle blocks
        preamble_contracts = _collect_preamble_contracts(
            effective_root,
            is_strand=is_strand,
            is_daemon=is_daemon,
            has_diffense=has_diffense,
            has_introspection=has_introspection,
        )

        # Inject-stack blocks (skipped for strands, except pitfalls — #1185)
        if not is_strand:
            _, inject_contracts, block_whole = _build_injected_blocks_with_contracts(
                effective_root, task_text=task_text
            )
        else:
            _, pitfalls_contract = _build_strand_pitfalls_contract(
                effective_root, task_text
            )
            inject_contracts = [pitfalls_contract]
            block_whole = frozenset()

        if injected_whole is None:
            injected_whole = block_whole

        # Ordered: preamble blocks first, then inject stack (mirrors prompt
        # order). The runtime trailer comes after the inject stack.
        pre_inject = [c for c in preamble_contracts if c.block_key != "run-context-bundle"]
        runtime_entries = [c for c in preamble_contracts if c.block_key == "run-context-bundle"]
        all_contracts = pre_inject + inject_contracts + runtime_entries
    else:
        all_contracts = contracts
        if injected_whole is None:
            # ``contracts`` arrived pre-built (the kernel's cheap path, or a
            # caller replaying a stamped manifest) without the fact
            # ``_build_orientation_set`` needs. Derive it independently
            # rather than skip it — the work-surface pass alone, not the
            # full inject-stack scan ``contracts=`` was chosen to avoid.
            injected_whole = (
                frozenset()
                if is_strand
                else _build_work_surface_block_scored(effective_root)[1]
            )

    # Host kind
    kind = "daemon" if is_daemon else "ad-hoc"
    pub_owner = "resident-owned" if not is_strand else "strand"

    # #1244 fork 1: name a missing AGENTS.md/kb in the kernel rather than
    # let the resident infer it from a failed Read or a kb write that
    # quietly never lands. Existence check only — never reads AGENTS.md's
    # content, so this costs nothing on the common (initialized) path.
    agents_md_missing = not (effective_root / "AGENTS.md").exists()
    kb_missing = False
    try:
        from . import knowledge

        kb_missing = knowledge.active_kb_dir(
            effective_root, conf.load_config(effective_root)
        ) is None
    except Exception:  # noqa: BLE001 — a boot score must never fail a wake;
        # conservative direction on failure, same call `shell_reads_agents_md_natively`
        # makes: an unknown answer reports "present" rather than a possibly-wrong "missing".
        kb_missing = False

    hooks_info = _collect_hooks_info(
        installed=hooks_installed, hook_stamps=hook_stamps, runner_shell=runner_shell
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
            # Same "asked where the assembling happens" reasoning as
            # `image_stale` above — and the same module-global state, so the
            # two can never name a different image. `None` outside a live
            # daemon (see `dev_reload.image_fingerprint_digest`).
            image_digest=dev_reload.image_fingerprint_digest(),
            image_captured_at=dev_reload.image_captured_at(),
            agents_md_missing=agents_md_missing,
            kb_missing=kb_missing,
        ),
        continuity=continuity if continuity is not None else BootContinuity(),
        attention=BootAttention(
            event_ids=event_ids,
            source_gate=source_gate,
            created=event_created,
            age_seconds=event_age_seconds(event_created),
            retry_of=event_retry_of,
            retry_failure_kind=event_retry_failure_kind,
        ),
        posture=BootPosture(
            pending_count=pending_count,
            budget=budget,
            quota=quota,
            branch=branch,
        ),
        orientation=_build_orientation(
            is_daemon=is_daemon,
            is_strand=is_strand,
            environment=environment,
            pending_count=pending_count,
            has_event_body=has_event_body,
        ),
        orientation_set=_build_orientation_set(
            effective_root,
            task_text=task_text,
            runner_shell=runner_shell,
            injected_whole=injected_whole,
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
    event_created = kwargs.get("event_created")
    event_retry_of = kwargs.get("event_retry_of")
    event_retry_failure_kind = kwargs.get("event_retry_failure_kind")
    continuity = kwargs.get("continuity")
    environment = kwargs.get("environment")
    strand = bool(kwargs.get("strand", False))
    diffense = bool(kwargs.get("diffense", False))
    event_body = kwargs.get("event_body", "")
    pending_events = kwargs.get("pending_events") or []
    budget_seconds = kwargs.get("budget_seconds")
    runner_quota = kwargs.get("runner_quota")
    branch_name = kwargs.get("branch_name")
    hooks_installed = kwargs.get("hooks_installed")

    # #1137: the same forge-state join #957 built for the live menu
    # (`resolved_prs` — a `{pr_number: "merged 3h ago"}` map), reused here so
    # a work-surface item's own `refs:` row can be annotated the same way.
    # `communication_snapshot["forge"]` is this wake's own already-built
    # facet (`daemon.py` sets it network-free before this function ever
    # runs) — never re-fetched here, and absent (ad-hoc/setup callers, a
    # strand's isolated snapshot) simply renders with no annotation, same as
    # today.
    communication_snapshot = kwargs.get("communication_snapshot")
    resolved_prs = (
        forge_state.resolved_pr_lookup(communication_snapshot.get("forge"))
        if isinstance(communication_snapshot, dict)
        else None
    )

    pitfall_text = "\n".join(t for t in (task, event_body or "") if t)

    # The introspection toggle is read inside _build_introspection_block (it
    # returns "" when off), so its rendered emptiness *is* the toggle state —
    # no second config read needed to know whether the block is present.
    has_diff = diffense

    mount_sink: dict[str, str] | None = kwargs.pop("_mount_sink", None)

    if strand:
        # #1185: a strand is single-shot — it cannot learn from its own
        # patterns the way a resident's later wake would — so the pitfalls
        # block is the one exception to "no inject stack for strands".
        injected_keyed, pitfalls_contract = _build_strand_pitfalls_contract(
            repo_root, pitfall_text
        )
        inject_contracts = [pitfalls_contract]
        injected_whole: frozenset[Path] = frozenset()
        introspection_block = ""
    else:
        injected_keyed, inject_contracts, injected_whole = _build_injected_blocks_with_contracts(
            repo_root, task_text=pitfall_text or None, resolved_prs=resolved_prs
        )
        introspection_block = _build_introspection_block(repo_root)

    preamble_contracts = _collect_preamble_contracts(
        repo_root,
        is_strand=strand,
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
        is_strand=strand,
        runner_name=str(runner_name) if runner_name else None,
        runner_shell=str(runner_shell) if runner_shell else None,
        runner_core=str(runner_core) if runner_core else None,
        body_provenance=str(body_provenance) if body_provenance else None,
        source_gate=str(source_gate) if source_gate else None,
        continuity=continuity,
        environment=str(environment) if environment else None,
        event_ids=(event_id,),
        event_created=str(event_created) if event_created else None,
        event_retry_of=str(event_retry_of) if event_retry_of else None,
        event_retry_failure_kind=str(event_retry_failure_kind) if event_retry_failure_kind else None,
        pending_count=len(pending_events),
        budget=f"{budget_seconds // 60}m" if budget_seconds else None,
        quota=str(runner_quota) if runner_quota else None,
        branch=str(branch_name) if branch_name else None,
        task_text=pitfall_text or None,
        has_event_body=bool((event_body or task or "").strip()),
        has_diffense=has_diff,
        has_introspection=bool(introspection_block),
        contracts=contracts,
        # The exact set the real inject stack (built above, same call) handed
        # over whole — not a second, independent derivation. Passing it here
        # is what keeps this persisted score in lockstep with the kernel's own
        # `build_boot_score` call in `build_daemon_prompt` (which has no
        # `contracts` built to read this from and so derives it fresh); see
        # that call site's comment for the drift class this closes (#628,
        # same shape as #638's `task_text` omission).
        injected_whole=injected_whole,
        hooks_installed=hooks_installed,
        # Same derivation the kernel used, from the same `mountable` set — so the
        # block the wake *reads* and the score the daemon *persists* cannot disagree
        # about which boot it got. (They already did, for one commit: the kernel said
        # "mounted", the score said `false`. An inspection that describes a wake
        # nobody had is the failure this module's docstring already names.)
        mounted=bool(mountable),
    )
    # A mounted block leaves the prose but never the wake: `_take` diverts it
    # into `mount_sink` and `transcript.py` re-delivers it as a seeded `Read`
    # result, so the runner pays for those bytes in a different grammatical
    # position. Counting only the prose made this field a *subtotal* wearing
    # the name "total". On a real Tier-2 wake five files mount (identity core,
    # run preamble, weave, register, daemon-substrate) and the shortfall is not
    # marginal: measured on run-260725-1136-pyck, prose 88,835 B against
    # 117,459 B of censused blocks, so `_cost_ledger` printed
    # `unattributed  -28,624 B` with per-authority shares summing to 132% —
    # 24% of the wake invisible to the one number that claims to size it, and
    # a negative "unattributed" line right under a comment saying a ledger
    # whose columns don't add up is how you learn to stop trusting the ledger.
    #
    # `mount_sink`, not `mountable`: the latter is the set that *could* mount,
    # and `introspection` sits in it while never passing through `_take` (it is
    # joined via `_prepared_introspection_block` and stays prose). Correcting
    # by `mountable` would double-count it. The sink is the only record of what
    # actually left.
    prose_bytes = sizes.get("_prompt")
    score.prompt_bytes = None if prose_bytes is None else prose_bytes + sum(
        len(text.encode("utf-8")) for text in (mount_sink or {}).values()
    )

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

#: The Stage line an init wake's Run Context Bundle carries. One constant
#: because three places must agree on it: the bundle renderer (which hangs
#: the bootstrap-commit carveout off it), ``init_wake`` (which passes it),
#: and the tests that pin both.
INIT_WAKE_STAGE = "brnrd init wake"

#: The playbook the init wake receives as its task. A separate name because
#: the file is a *prompt contract* (maintainer-owned, ``prompts/``) while
#: everything that reads it is runtime.
INIT_PLAYBOOK_NAME = "init-playbook.md"


def init_playbook_available(repo_root: Path | None = None) -> bool:
    """Whether the init playbook prompt exists on this install.

    The wake path is only offered when it does. A brnrd built without the
    playbook (or with it removed by a per-repo override that emptied it)
    falls back to the mechanical install rather than dispatching
    a wake with no task — a wake whose contract is an empty string would
    improvise the product's first impression.
    """
    return bool(read_prompt(INIT_PLAYBOOK_NAME, repo_root).strip())


def build_init_wake_facts(facts: dict[str, Any]) -> str:
    """Render the init wake's facts block — what code already knows.

    Everything here is something the wake would otherwise have to ask the
    user or shell out for, and getting it wrong costs an interview beat.
    Notably the *detection report*: a Runner necessarily exists (the
    mechanical doctor handles zero-runner before any wake), so a missing
    alternative is a resilience note, never a blocker.
    """
    lines = ["### Init facts", ""]
    lines.append(
        "_What brnrd already established mechanically. Treat as ground "
        "truth; don't re-derive it, and don't send the user back through "
        "installation for a Runner that is visibly working._"
    )
    lines.append("")
    for label, key in (
        ("Repo root", "repo_root"),
        ("Selected runner", "runner_name"),
        ("Detected runners", "detected_runners"),
        ("Detected shells", "detected_shells"),
        ("Shell families not on PATH", "missing_shells"),
        ("Configured gates", "configured_gates"),
        ("gh CLI", "gh_available"),
        ("GitHub identity (via gh)", "github_identity"),
        ("git remotes", "git_remotes"),
        ("Existing AGENTS.md", "agents_md"),
        ("Knowledge shape (if already chosen)", "knowledge_shape"),
    ):
        if key not in facts:
            continue
        value = facts[key]
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value) or "(none)"
        elif isinstance(value, bool):
            value = "yes" if value else "no"
        elif value in (None, ""):
            value = "(none)"
        lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def build_init_wake_prompt(
    repo_root: Path,
    *,
    event_id: str,
    response_path: str,
    outbox_path: str,
    facts: dict[str, Any] | None = None,
    **kwargs: Any,
) -> "tuple[str, Any]":
    """Assemble the init wake's prompt (spec §3.3). Returns ``(prompt, score)``.

    A thin wrapper over :func:`build_daemon_prompt_with_score` — the boot
    score, keyed preamble, injected blocks, and the Run Context Bundle are
    the daemon's, unchanged, because the entire point of #507 is that init
    is *not* a special mode. What init supplies is only what is genuinely
    different: the Stage line, the playbook as the task, and a facts block.

    Resident stack (``strand=False``, F3): the user meets the being they
    will be working with, not a bounded thought that opens by disclaiming
    residency. The injected resident blocks must therefore degrade on a
    repo with no connected account — the normal state at minute zero.
    """
    task_parts = [read_prompt(INIT_PLAYBOOK_NAME, repo_root).strip()]
    if facts:
        task_parts.append(build_init_wake_facts(facts))
    from . import constitution

    tpl_path = constitution.TEMPLATE_PATH
    if tpl_path.exists():
        task_parts.append(
            "---\n\n## Adopter template (author `AGENTS.md` from this)\n\n"
            + tpl_path.read_text(encoding="utf-8")
        )
    task = "\n\n".join(p for p in task_parts if p)

    kwargs.setdefault("stage", INIT_WAKE_STAGE)
    kwargs.setdefault("source", "init")
    kwargs.setdefault("environment", "host")
    kwargs.setdefault("strand", False)
    kwargs.setdefault("outbox_path", outbox_path)
    return build_daemon_prompt_with_score(
        task, event_id, response_path, repo_root, **kwargs,
    )


def build_init_prompt(repo_root: Path, knowledge_shape: str = "repo") -> str:
    """Build the prompt for ``brnrd init`` — setup.md + adopter template.

    The setup agent works from ``templates/constitution.md`` — the
    host-agnostic adopter template, *not* brr's own 667-line internal
    playbook (Layer 0 split these apart; before it, brr-specific truth
    leaked into adopter repos because one file served both jobs). The
    template's universal sections are versioned blocks copied verbatim;
    the project-specific sections (Project, Build and run, Code guidelines,
    Constraints) get rewritten for the adopter's repo.

    ``knowledge_shape`` (``"repo"`` | ``"home"``) is the adopter's chosen kb
    architecture — asked, not defaulted (D2). It selects whether the setup
    agent scaffolds a committed ``kb/`` or leaves knowledge to the brnrd
    account home; the shell bridges are written by brnrd itself, not the
    model.
    """
    from . import constitution

    setup = read_prompt("setup.md", repo_root)
    tpl_path = constitution.TEMPLATE_PATH
    template = tpl_path.read_text(encoding="utf-8") if tpl_path.exists() else ""
    if knowledge_shape == "home":
        directive = (
            "\n\n---\n\n## Knowledge shape for this adopter: **home**\n\n"
            "This repo is connected to a brnrd account, so its knowledge base "
            "lives in the account's home knowledge, not a committed `kb/`. Do "
            "**not** create `kb/index.md` or `kb/log.md`. In the rendered "
            "`Knowledge base` section, keep the logical contract but drop the "
            "committed-`kb/` specifics."
        )
    else:
        directive = (
            "\n\n---\n\n## Knowledge shape for this adopter: **repo**\n\n"
            "Scaffold a committed `kb/`: create `kb/index.md` and `kb/log.md` "
            "if absent (seeds below), and add `kb/log.md merge=union` to "
            "`.gitattributes`."
        )
    return f"{setup}\n\n{template}{directive}"


#: The two places ``init-playbook.md`` still assumes a literal terminal,
#: reconciled once for every door-carried first wake (the connect greeting
#: and the uninitialized-repo fold below). The playbook file itself cannot
#: be edited from a run (prompt-contract, maintainer-merged), so the
#: reconciliation rides the code-composed preamble.
_DOOR_WAKE_CAVEATS = (
    "Two places in the playbook below don't apply here; read past them, "
    "keep everything else:\n\n"
    "- \"You have no stdin here… the person types at the terminal brnrd "
    "owns\" — there is no separate terminal; you already have this "
    "conversation. The mechanism it describes is otherwise exactly right: "
    "a reply lands as a new pending event, so ask, then wait for it, under "
    "the standing portal rules your own playbook already gives you.\n"
    "- The gate walk's `control: gate-setup <name>` outbox verb does not "
    "exist on this run — that is the terminal wake's own machinery, "
    "unavailable here. If the user wants a door wired up mid-conversation, "
    "tell them the command to run themselves (e.g. `brnrd gate setup "
    "telegram`), or note it as a follow-up; do not emit `control:`."
)


def _first_wake_task_parts(
    repo_root: Path, preamble: str, facts: dict[str, Any] | None,
) -> str:
    """Assemble preamble + init playbook + facts + adopter template.

    The shared tail of every door-carried first wake — one assembly, so the
    connect greeting and the uninitialized-repo fold cannot drift apart in
    what they hand the run.
    """
    task_parts = [preamble + read_prompt(INIT_PLAYBOOK_NAME, repo_root).strip()]
    if facts:
        task_parts.append(build_init_wake_facts(facts))
    from . import constitution

    tpl_path = constitution.TEMPLATE_PATH
    if tpl_path.exists():
        task_parts.append(
            "---\n\n## Adopter template (author `AGENTS.md` from this)\n\n"
            + tpl_path.read_text(encoding="utf-8")
        )
    return "\n\n".join(p for p in task_parts if p)


def collect_daemon_wake_init_facts(repo_root: Path) -> dict[str, Any]:
    """A trimmed ``init_wake.collect_facts`` — repo/gh/gate facts only.

    Runner and shell detection are dropped: a daemon-dispatched run already
    gets its own Runner catalog and Mode block every wake — restating a
    subset of that here would drift from, or duplicate, the standard
    surface rather than add anything. Shared by the connect greeting and
    the dispatch-time uninitialized-repo fold.
    """
    from . import init_wake

    facts = init_wake.collect_facts(repo_root, runner_name="")
    for key in ("runner_name", "detected_runners", "detected_shells", "missing_shells"):
        facts.pop(key, None)
    return facts


def build_uninitialized_wake_task(
    repo_root: Path, *, facts: dict[str, Any] | None = None,
) -> str:
    """The task a human-addressed run gets on a repo with no ``AGENTS.md``.

    The lane the connect greeting cannot cover: a cloud-only pairing has no
    door that can *originate* a message, so ``front_door`` truthfully says
    "message your account's bot about this repo, and the first run takes it
    from there" — and until 2026-08-19 nothing made that true. The first
    inbound message woke a plain run whose wake never mentioned setup; the
    live repro (a fresh macOS install, ``StrayUnicorn/tgtldr``) answered
    "The room is lit; I'm here" on the cheapest core and "nothing appears
    to need intervention" on the strongest one — the instructions were
    missing, not the capability. The daemon now folds this task around any
    owner-trusted, correspondent-addressed run while ``AGENTS.md`` is
    absent; the state that triggers it ends the moment the contract is
    committed.
    """
    preamble = (
        "# First wake on an uninitialized repo\n\n"
        "`AGENTS.md` does not exist here yet — nobody has run `brnrd init` "
        "on this repo and no connect-time greeting reached it, so **this "
        "run is the setup run**, whatever the message that woke you asked "
        "for. The person on the other end is the person to interview. This "
        "is a normal resident-operated run over an already-paired channel, "
        "not a proxied terminal session. "
        + _DOOR_WAKE_CAVEATS
        + "\n\n"
        "Open by answering the message that woke you — it is quoted under "
        "`### Original event body` in your Run Context Bundle — then fold "
        "the interview into that same conversation: survey the repo first, "
        "take the beats, author `AGENTS.md` from the adopter template that "
        "follows, seed the knowledge base, commit, and only then close. "
        "Answering the message while skipping the setup is the measured "
        "failure this task exists to end: every later wake would keep "
        "waking into an unfurnished room, and the person who was promised "
        "\"the first run takes it from there\" would be right to wonder "
        "where it went. **The setup is the task; the message rides along "
        "with it.**\n\n"
        "---\n"
    )
    return _first_wake_task_parts(repo_root, preamble, facts)


def build_connect_greeting_task(
    repo_root: Path, *, facts: dict[str, Any] | None = None,
) -> str:
    """The event body `connect_greeting.queue_greeting` mints (#1244 fork 2).

    Reuses the same two ingredients :func:`build_init_wake_prompt` feeds the
    terminal init wake — ``init-playbook.md`` (the interview + authoring
    playbook) and the adopter template — so this run authors ``AGENTS.md``
    and the kb seed exactly the way ``brnrd init`` always has, no second
    implementation. Unlike that function, this does **not** call
    :func:`build_daemon_prompt_with_score`: this text becomes a plain inbox
    event ``body``, and the *normal* daemon dispatch path wraps it in the
    ordinary Run Context Bundle on its own — that is the whole point of
    fork 2 ("a normal resident-operated run over an established door", the
    maintainer's own words, not a bespoke wake session).

    The one addition is a short preamble reconciling the two places
    ``init-playbook.md`` still assumes a literal terminal (it cannot be
    edited here — ``src/brr/prompts/`` changes affect every user and need a
    maintainer merge; see this task's own report for the two-line change
    that would let a future edit fold this preamble into the template
    itself).
    """
    preamble = (
        "# First wake over a door, not a terminal (#1244 fork 2)\n\n"
        "`AGENTS.md` is missing — nobody has run `brnrd init` here yet, and "
        "this repo just connected a chat door. You are waking for the first "
        "time on this repo, and — unlike the playbook right below assumes — "
        "this is a **normal resident-operated run over an already-paired "
        "door**, not a proxied terminal session (that terminal shape was "
        "tried and rejected). "
        + _DOOR_WAKE_CAVEATS
        + "\n\n"
        "Everything else below — tone, survey-before-speaking, the interview "
        "beats, authoring `AGENTS.md` from the adopter template that "
        "follows, the kb seed, the closeout — applies unchanged.\n\n"
        "---\n"
    )
    return _first_wake_task_parts(repo_root, preamble, facts)


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
    register = read_prompt("register.md", repo_root)
    if register.strip():
        preamble = f"{preamble.rstrip()}\n\n{register.strip()}"
    return preamble


def _preamble_parts(repo_root: Path, *, strand: bool) -> list[tuple[str, str]]:
    """The preamble as ``(block_key, text)`` parts, in read order.

    Same bytes as ``_read_preamble_with_weave`` + ``daemon-substrate.md`` +
    the portal verb grammar extract, glued together (:func:`_glue_preamble`
    re-joins them identically) — but *keyed*, so a wake that mounts a block
    as a seeded perception can take it out of the prose instead of paying
    for it twice.

    These are the blocks that carry the wake's obligations (write the card, branch
    before you edit, own the pending event). They are therefore the blocks the
    transcript experiment most needs to be able to move, and an unkeyed preamble
    string is precisely what made that impossible.
    """
    key = "strand-preamble" if strand else "run-preamble"
    parts = [(key, read_prompt("strand.md" if strand else "run.md", repo_root))]
    # Order mirrors read/authority: how you write (weave), you having written
    # (register — resident only), then who drives (daemon-substrate), then the
    # verb grammar that section's own frontmatter table only gestures at. Kept
    # in lockstep with :func:`_collect_preamble_contracts`, which registers the
    # same blocks in the same order for the manifest and the mount.
    riders = [("weave.md", "weave")]
    if not strand:
        riders.append(("register.md", "register"))
    riders.append(("daemon-substrate.md", "daemon-substrate"))
    for name, k in riders:
        text = read_prompt(name, repo_root)
        if text.strip():
            parts.append((k, text.strip()))
    portal_grammar = _build_portal_verb_grammar_block(repo_root)
    if portal_grammar.strip():
        parts.append(("portal-verb-grammar", portal_grammar.strip()))
    return parts


def _glue_preamble(parts: list[str]) -> str:
    """Re-join preamble parts exactly as the unkeyed path did."""
    if not parts:
        return ""
    out = parts[0]
    for part in parts[1:]:
        out = f"{out.rstrip()}\n\n{part}"
    return out


def _build_strand_preamble(repo_root: Path) -> str:
    """Read ``strand.md`` plus the working-register contract (``weave.md``).

    The slim counterpart to :func:`_read_preamble_with_weave`: a strand wake
    (B4, ``kb/design-director-loop.md`` §orchestrator/worker) gets the bounded
    task preamble instead of the resident's ``run.md`` — no dominion write,
    no kb governance, no "reconsider intent" stewardship framing, none of
    which apply to a bounded handoff. ``weave.md`` still rides: it governs
    *how* any wake writes to its working surfaces, resident or strand alike.
    """
    preamble = read_prompt("strand.md", repo_root)
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
    stage: str = "brnrd daemon run",
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
    event_created: str | None = None,
    event_retry_of: str | None = None,
    event_retry_failure_kind: str | None = None,
    budget_seconds: int | None = None,
    runner_medium: str | None = None,
    runner_quota: str | None = None,
    update_available: str | None = None,
    runner_catalog: list[dict[str, Any]] | None = None,
    runner_name: str | None = None,
    runner_shell: str | None = None,
    runner_core: str | None = None,
    body_provenance: str | None = None,
    source_gate: str | None = None,
    continuity: Any | None = None,
    hooks_installed: bool | None = None,
    diffense: bool = False,
    strand: bool = False,
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

    ``strand=True`` (B4, ``kb/design-director-loop.md`` §orchestrator/worker)
    swaps in the slim child stack: ``strand.md`` + ``weave.md`` instead of
    the resident's ``run.md``, and the resident-only injected blocks
    (identity core, dominion digest, work surface, runner policy,
    knowledge sources, kb health, introspection) are skipped entirely — a
    strand wake still gets ``daemon-substrate.md`` (it still runs under the
    daemon and needs the delivery/portal mechanics) and the full Run
    Context Bundle (its actual task). Pitfalls are the one exception
    (#1185): a strand is single-shot and cannot learn from its own
    patterns the way a resident's later wake would, so the account's
    failure-memory lookup — matched fresh against this run's own task text
    — is handed over same as a resident wake, and nothing else. Default
    ``False`` is byte-identical to the prior behavior.
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
        for key, text in _preamble_parts(repo_root, strand=strand)
        if (kept := _take(key, text)) is not None
    ])
    bundle = _build_run_context_bundle(
        event_id=event_id,
        response_path=response_path,
        stage=stage,
        outbox_path=outbox_path,
        budget_seconds=budget_seconds,
        runner_medium=runner_medium,
        runner_quota=runner_quota,
        update_available=update_available,
        runner_shell=runner_shell,
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
        event_created=event_created,
        event_retry_of=event_retry_of,
        event_retry_failure_kind=event_retry_failure_kind,
        diffense=diffense,
    )
    trailer = bundle.rstrip()
    if (event_body or "").strip() != task.strip():
        trailer = f"{trailer}\nRun instruction: {task}"

    # #1137: same forge-state join as `build_daemon_prompt_with_score` — see
    # that function's own comment. This bare-`build_daemon_prompt` path
    # (direct calls, `brnrd run` one-shot mode) has `communication_snapshot`
    # as its own parameter rather than a kwarg, so the lookup happens here
    # too rather than only in the scored variant.
    resolved_prs = (
        forge_state.resolved_pr_lookup(communication_snapshot.get("forge"))
        if isinstance(communication_snapshot, dict)
        else None
    )

    # Match pitfalls against the run instruction and the original event text — the
    # triggers the resident recorded tend to echo how a request is phrased. The
    # same text selects the orientation set's task-touched kb hubs, so it is
    # computed *before* the kernel; see the kernel's own note below.
    pitfall_text = "\n".join(t for t in (task, event_body) if t)

    # The action-first kernel (Slice 2).  Built from the same
    # :func:`build_boot_score` the daemon persists, so the block the wake reads
    # and the block the score describes cannot drift — ``contracts=[]`` because
    # the kernel names the *move*, not the map, and skipping the manifest scan
    # keeps this path as cheap as it was.
    #
    # "Same function" is not "same value", and that gap shipped: this call
    # omitted ``task_text`` while ``build_daemon_prompt_with_score`` passed it,
    # so the *persisted* score's ``orientation_set`` carried the task-touched
    # ``subject-*.md`` hubs (:func:`_kb_hub_matches`) and the *rendered* kernel
    # never named them. Two costs, both silent: the hub-matching branch was
    # unreachable from the only surface that asks for a Read, and the hooks'
    # ``orient x/y`` meter — which counts against the persisted set — could
    # never complete, because no listed file would ever close the gap. A meter
    # that cannot leave is the skimming trainer its own docstring warns about
    # (`hooks._orientation_progress`). Found 2026-07-24 from a live wake whose
    # kernel said 2 files and whose ``boot-score.json`` said 4.
    #
    # So every argument that feeds ``orientation_set`` must be passed here too.
    # ``contracts=[]`` stays the one deliberate divergence, and it does not
    # touch the set. ``injected_whole`` (#628 — the orientation walk must not
    # re-bill a Read the work-surface block already handed over whole) is the
    # one exception to "must be passed": this call leaves it unset on purpose.
    # Unlike ``task_text`` above, that is not a second copy of the #638 bug —
    # ``build_boot_score`` derives it itself from :func:`_build_work_surface_block_scored`
    # whenever ``contracts`` arrives pre-built without it, so this call and
    # ``build_daemon_prompt_with_score``'s (which passes the value its own
    # already-built inject stack produced) land on the same deterministic
    # answer either way — see that function's docstring.
    from .bootscore import format_kernel

    kernel = format_kernel(build_boot_score(
        repo_root,
        is_daemon=True,
        is_strand=strand,
        runner_name=runner_name,
        runner_shell=runner_shell,
        runner_core=runner_core,
        body_provenance=body_provenance,
        source_gate=source_gate,
        continuity=continuity,
        environment=environment,
        event_ids=(event_id,) if event_id else (),
        event_created=event_created,
        event_retry_of=event_retry_of,
        event_retry_failure_kind=event_retry_failure_kind,
        pending_count=len(pending_events or []),
        budget=f"{budget_seconds // 60}m" if budget_seconds else None,
        quota=runner_quota,
        branch=branch_name,
        has_event_body=bool((event_body or task or "").strip()),
        contracts=[],
        task_text=pitfall_text or None,
        hooks_installed=hooks_installed,
        # Derived from the *render*: `_mountable` is exactly the set of blocks
        # about to be subtracted from this prose and seeded as perceptions. Not
        # `cfg["boot.mount"]` — a config key is a request, and the request can
        # be refused (Shell has no renderer, nothing to seed). When the mount fails,
        # the daemon rebuilds this whole prompt with no sink, `_mountable` is empty,
        # and the kernel silently tells the truth again.
        mounted=bool(_mountable),
    ))

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
        strand=strand,
        prepared_injected_blocks=prepared_blocks,
        prepared_introspection_block=_prepared_introspection_block,
        resolved_prs=resolved_prs,
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

# Issue #576: a recurring schedule.md entry re-enters the conversation store
# as a `source: schedule` user turn every time it fires. Those turns weave
# into "Recent turns" on every later wake, *and* the current firing renders
# again under "### Original event body" — the same multi-thousand-token
# document counted two or three times in one wake. conversations.py already
# collapses byte-identical repeat firings at the store layer
# (`_collapse_schedule_repeats`), but a live entry's body drifts slightly
# firing to firing (timestamps, a line of accreted rationale), so exact
# matching misses the near-duplicates that make up most of the waste
# (measured 0.971 similarity between two real firings). SequenceMatcher.ratio
# is a cheap, dependency-free way to catch those near-misses.
SCHEDULE_TURN_DEDUP_RATIO = 0.9
SCHEDULE_TURN_DEDUP_EVENT_STUB = (
    "[schedule entry, identical to this run's event body — not repeated]"
)
SCHEDULE_TURN_DEDUP_TURN_STUB = (
    "[schedule entry, identical to the {ts} firing above — not repeated]"
)

# Issue #736: `RECENT_CONVERSATION_MAX` caps the *count* of woven turns and
# nothing caps their *size*, so one turn can eat the wake. Measured on
# `run-260725-1056-u1y3`: a single 2026-07-21 `schedule` firing rendered at
# 12,438 B — 9.9% of a 125,044 B wake — for a `schedule.md` entry that had
# been rewritten two days later and deleted two days after that. A fired
# schedule body is an immutable conversation record, so every edit to the
# live entry forks it from its ghosts and the ghosts keep full weight
# forever.
#
# Deliberately *additive* to the #576 dedup above, not a replacement: that
# collapse fires only when a schedule turn resembles another schedule turn
# or the current event body. The 07-21 ghost resembled neither (its
# successor had been rewritten), so it sailed through a mechanism that was
# working exactly as designed. Size is a separate axis from similarity.
#
# 2,000 B is the sizing from the issue: all six live turns on the measured
# wake were already under it, so a normal wake renders byte-identically to
# before the cap existed and only the pathological turn is ever touched.
# Set `conversation.recent_turn_max_bytes` in `.brr/config` to retune it;
# <= 0 disables the cap entirely.
RECENT_TURN_MAX_BYTES = 2_000
RECENT_TURN_MAX_BYTES_KEY = "conversation.recent_turn_max_bytes"

# The elision has to be *visible* or the trim reads as if nothing was
# dropped (#660's complaint, #688's failure). Both halves are load-bearing:
# the byte count says how much is missing, the pointer says where to read
# it. Never "…" alone, never "truncated".
RECENT_TURN_ELISION_MARKER = "…{dropped:,} B elided · {pointer}"

# Issue #755: measured on the wake audit, the recent-turns block was 15,836 B
# of a 122,542 B wake and **71% of it was the resident's own previously-sent
# messages**, replayed at full text every boot. That is not context, it is a
# feedback loop: the voice is demonstrably learned from examples rather than
# rules (#711 — `fluency: weave` declared once was out-argued by ~70 KB of the
# resident's own prose, and the introspection reflex decayed 67-100% → 0-20%
# with its instruction text unchanged), so re-feeding yesterday's outbound
# verbatim re-teaches yesterday's voice on every wake.
#
# Inbound user turns are the half that must never be lost — they are the only
# record of what someone actually asked. Own outbound is *recoverable*: the
# per-run history JSONL holds the full text and `_turn_store_pointer` already
# names the file. So it needs a receipt, not a replay.
#
# A third axis, deliberately independent of the two above it. The #576 dedup
# asks *is this a repeat?*, the #736 cap asks *is this too big?*, and this asks
# **whose text is this?** — a question neither of the others can answer, and
# one that a 300 B outbound turn fails just as squarely as a 12 KB one. So it
# is not gated on `turn_max_bytes`: a caller that opted out of the size cap did
# not thereby ask to be fed its own voice back.
OWN_OUTBOUND_RECEIPT_HEAD_CHARS = 200

# The first line is the scene-verdict line under the turn contract (weave.md
# → "The turn"), so it is the right summary handle; for older messages
# predating that contract it is still the best available single line.
OWN_OUTBOUND_RECEIPT_MARKER = "{head} · {size:,} B · {pointer}"

# The newest own-outbound record keeps today's full-body rendering: the
# resident has to know what it just replied in order to hold a live exchange,
# and everything older is exactly what the receipt line is for. Flip this to
# ``False`` for the all-receipts variant — that is the one knob between the two
# shapes, kept explicit so the choice stays visible rather than buried in a
# loop condition.
OWN_OUTBOUND_KEEP_NEWEST_IN_FULL = True


def _render_runner_catalog(
    catalog: list[dict[str, Any]] | None,
) -> list[str]:
    """Compact prompt rendering for the Runner/Core catalog.

    Includes unavailable profiles (marked with ✗) and stale entries (marked
    ``stale``).  All three consumers — wake prompt, ``brnrd runners list``,
    and the dashboard publish — derive their rows from the same
    ``runner.available_runner_catalog()`` projection; this renderer is the
    compact form for the wake prompt only.
    """
    lines: list[str] = []
    for item in catalog or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        selected = bool(item.get("selected"))
        prefix = "selected " if selected else ""

        # Availability mark: ✗ when unavailable; omit when available (noise).
        availability = str(item.get("availability") or "available")
        unavail_prefix = "✗ " if availability != "available" else ""

        core_label = item.get("model") or "default"
        if item.get("pin"):
            core_label = f"{core_label} (pinned: {item['pin']})"

        bits = [
            f"shell={item.get('shell') or 'unknown'}",
            f"core={core_label}",
        ]
        if item.get("class"):
            bits.append(f"class={item['class']}")
        if item.get("cost_rank") is not None:
            bits.append(f"cost_rank={item['cost_rank']}")
        if item.get("quota_source"):
            quota_str = f"quota={item['quota_source']}"
            level = str(item.get("quota_level") or "").strip()
            if level:
                quota_str += f" ({level})"
            bits.append(quota_str)
        if item.get("auth_variant"):
            bits.append(f"auth={item['auth_variant']}")
        if availability != "available":
            bits.append(f"availability={availability}")
        if item.get("stale"):
            bits.append("stale")
        lines.append(
            f"- {prefix}{unavail_prefix}{name}: " + ", ".join(str(bit) for bit in bits)
        )
    return lines


def _build_run_context_bundle(
    *,
    event_id: str,
    response_path: str,
    stage: str = "brnrd daemon run",
    outbox_path: str | None = None,
    budget_seconds: int | None = None,
    runner_medium: str | None = None,
    runner_quota: str | None = None,
    update_available: str | None = None,
    runner_shell: str | None = None,
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
    event_created: str | None = None,
    event_retry_of: str | None = None,
    event_retry_failure_kind: str | None = None,
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
    # Parametrized, not hard-coded: the Stage line is what licenses a wake's
    # stage-specific deltas. The init wake commits on the *current* branch
    # (spec §5 / F4) — the exact opposite of the host-environment receipts
    # pin below — and it is this line that tells the wake which of the two
    # it is living under, instead of leaving it to fight its training.
    sections.append(f"- Stage: {stage}")
    if stage == INIT_WAKE_STAGE:
        sections.append(
            "- Bootstrap exception: this is the repository's first wake. "
            "Commit what you author on the **current branch** — the user "
            "just asked for these files in the checkout they are standing "
            "in. No branch ceremony, no PR handoff."
        )
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
            f"- Requested Runner: {runner_medium} — the Shell+Core selected "
            "for this thought; the actual Core is attested from the Shell "
            "result. A failure here (quota exhausted, provider error, "
            "substitution) ⇒ the user pays a manual reroute, so chunk work "
            "and commit early when the budget is tight."
        )
        if runner_quota:
            sections.append(f"- Quota: {runner_quota}")
    if update_available:
        sections.append(f"- {update_available}")
    # Native web-research declaration (issue #411 L0): always present, so a
    # waking resident never has to guess whether its Shell can verify a
    # changing fact — a missing declaration is itself the "undeclared" answer.
    sections.append(_build_web_capability_block(runner_shell))
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
    else:
        sections.append(
            "- Budget: no time limit — nothing configured "
            "`runner.timeout_seconds`, so brr will not kill this thought on "
            "a clock. Still bound uncertain long-running commands yourself "
            "(own timeout, or background + poll); a hung shell now holds "
            "the single-flight slot until stopped by hand."
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
    # #1491: the age travels with the event past the boot kernel's own
    # trimming — the kernel line is the first thing read, but it is not the
    # only thing a wake reads, and a run that skims past it must still be
    # able to find the same fact here. Same helpers as `format_kernel`'s
    # attention line, so the two surfaces cannot report a different age for
    # the same event.
    from .bootscore import event_age_seconds, format_event_age, format_retry_note

    age_seconds = event_age_seconds(event_created)
    age_note = format_event_age(event_created, age_seconds)
    if age_note:
        sections.append(f"- Event {age_note}")
    retry_note = format_retry_note(event_retry_of, event_retry_failure_kind)
    if retry_note:
        sections.append(f"- Event {retry_note}")
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
        sections.append(
            f"- live menu: `{outbox_path}/menu.json` — write one composed "
            "generation atomically (`menu_id`, `thread`, `options[]` with "
            "`handle` / `label` / optional `detail` / `rec`); the daemon "
            "validates and renders it at the gate and the next resident "
            "boundary. **`menu_id` names an immutable generation** — changed "
            "options need a *new* id, or the write is refused to `notices` "
            "and the live menu silently stays the old one. An empty "
            "`options` list is legal — it stands the live menu down (no "
            "controls render) without ending the conversation"
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
        sections.append(
            f"- keepalive: `{outbox_path}/.keepalive` — first line "
            "ISO-8601 or `+<duration>` (`+30m`); rewrite to extend"
            + ("" if budget_seconds else " an `await:` wait (no runtime "
               "budget is configured, so there is no deadline to outlast)")
        )
        sections.append(
            f"- card/run body: `{outbox_path}/.card` — resident-owned Markdown "
            "write-head; keep `## Now` current for the live projection, preserve "
            "the full run story below it; closeout captures it as `body.md`"
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
            "another strand). You share one dominion, so if one is on the "
            "same stream or files, expect its edits to land alongside yours "
            "— don't fight it. Contradictions in shared memory are normal "
            "and get reconciled by judgement, not locks (see your playbook)."
        )
        sections.append("")
        sections.append(presence_block)

    # #736: the per-turn byte cap and the store root it points at are
    # resolved once here — the two renderers below are pure formatters and
    # neither should be reaching for config or the filesystem on its own.
    # `runtime_dir` is the daemon's own value for the *shared* `.brr`; the
    # `gitops` fallback keeps hand-built callers (tests, `brnrd agent
    # inject`) pointing at the same store rather than at a worktree copy
    # that gets torn down at finalize.
    from . import gitops

    turn_max_bytes = _recent_turn_byte_cap(repo_root)
    try:
        turn_store_root = (
            Path(runtime_dir) if runtime_dir else gitops.shared_brr_dir(repo_root)
        )
    except Exception:
        # A pointer we cannot resolve degrades the marker; it never fails
        # the wake. Assembling the bundle is not the place to die.
        turn_store_root = None

    snapshot_block = _format_communication_snapshot(
        communication_snapshot,
        event_body=event_body,
        turn_max_bytes=turn_max_bytes,
        brr_dir=turn_store_root,
    )
    if snapshot_block:
        sections.append("")
        sections.append("### Communication snapshot")
        sections.append("")
        sections.append(snapshot_block)
    else:
        recent_block = _format_recent_conversation(
            recent_conversation,
            event_body=event_body,
            turn_max_bytes=turn_max_bytes,
            brr_dir=turn_store_root,
        )
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


#: 1,203 pending events once rendered 165.9 KB into a 252.7 KB wake — the
#: replay ate the thought that was supposed to handle it. This caps the
#: rendered window; the true omitted count always appears on the elision
#: line below rather than being silently dropped (that silence was the bug).
_PENDING_EVENTS_RENDER_MAX = 40


def _format_pending_events(
    events: list[dict[str, Any]] | None,
) -> str:
    """Render other pending inbox events as bullets for the bundle.

    Each entry shows the event id (the handle the resident names in the
    outbox ``event:`` frontmatter to fold it in), its source, and a
    one-line summary. Returns an empty string when nothing is waiting.

    #1156: a folded-in event's attachment bytes are already on disk
    (``daemon._pending_event_record`` resolves them), so a sub-bullet names
    the openable path directly rather than leaving the resident to re-derive
    it from the bare ``attachments:`` filename. An event that announced
    attachments but resolved none — the bytes never arrived, or were swept
    by retention — gets a sub-bullet that says so explicitly: *announced,
    not fetched* is a different fact than *no attachment*, and rendering
    neither is how that distinction went missing the first time (#1154).

    Capped at :data:`_PENDING_EVENTS_RENDER_MAX` — existing order is
    preserved (never re-sorted), just truncated, and a fitting list gets no
    elision line at all.
    """
    if not events:
        return ""
    total = len(events)
    rendered_events = events[:_PENDING_EVENTS_RENDER_MAX]
    # Counted from what the loop *kept*, not from the slice: entries without
    # an id are skipped below, and an omitted count taken from the slice
    # would then under-report. A truncation that misstates its own size is
    # the same lie as one that says nothing.
    from .bootscore import (
        EVENT_AGE_STALE_SECONDS, event_age_seconds, format_age_short,
    )

    rendered = 0
    bullets: list[str] = []
    for ev in rendered_events:
        eid = str(ev.get("id") or "").strip()
        if not eid:
            continue
        source = str(ev.get("source") or "").strip()
        summary = " ".join(str(ev.get("summary") or "").split())
        if len(summary) > 140:
            summary = summary[:137].rstrip() + "..."
        # #1491: `created` already rides in every pending-event record
        # (`daemon._pending_event_record` copies it) — this was purely a
        # dropped field read. Compact by design (this can be a list of many
        # events): only past the same soft threshold `format_kernel` uses
        # for the waking event, and just the elapsed half — the id already
        # anchors the reader to `brnrd do --reply <id>` if they want the
        # full stamp.
        age_seconds = event_age_seconds(ev.get("created"))
        age = ""
        if age_seconds is not None and age_seconds >= EVENT_AGE_STALE_SECONDS:
            age = f", {format_age_short(age_seconds)} old"
        src = f" ({source}{age})" if source else (f" ({age.lstrip(', ')})" if age else "")
        sep = f": {summary}" if summary else ""
        rendered += 1
        bullets.append(f"- {eid}{src}{sep}")
        if ev.get("orphaned"):
            # #1496 ("the event nobody could see"): daemon._pending_events_
            # for_agent only lets a `status: processing` event through this
            # filter when it proved the run that was dispatched to answer it
            # is dead. Marked here, explicitly — mixing it silently into
            # fresh mail is the exact failure this fix exists to end; a
            # resident reading this needs to know it's a survivor, not a
            # new arrival someone is waiting on for the first time.
            bullets.append(
                "  - orphaned: the run this event was dispatched to died "
                "mid-flight (interrupted/crashed) before answering it — "
                "this is a survivor being re-offered, not a fresh arrival"
            )
        paths = ev.get("attachment_paths")
        if isinstance(paths, list) and paths:
            bullets.extend(f"  - attachment: {p}" for p in paths)
        else:
            unfetched = ev.get("attachment_unfetched")
            if isinstance(unfetched, list) and unfetched:
                names = ", ".join(str(n) for n in unfetched)
                bullets.append(
                    f"  - attachment announced, not fetched ({names}) — "
                    "the bytes never reached this machine or were already "
                    "swept by retention; this is not the same as no attachment"
                )
    omitted = total - rendered
    if omitted > 0:
        bullets.append(
            f"- … +{omitted:,} more pending events not rendered here — read "
            "the live portal-state.json / inbox.json for the full list"
        )
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
    *,
    event_body: str | None = None,
    turn_max_bytes: int = 0,
    brr_dir: Path | None = None,
) -> str:
    """Render the curated cross-channel wake snapshot.

    This is the prompt-facing tier in the co-maintainer continuity model:
    compact enough to ride every wake, with a bounded recent-tail of
    grouped history one file read away when the resident needs more, and
    a pointer to the permanent, untruncated base store for anything a
    truncated tail dropped.
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
    # Reader fluency (#217): which *language* the reader reads, never how
    # much the reply says. The line deliberately re-states the non-licence —
    # the field's predecessor (`user_commitment: full`) read as a volume knob
    # and produced arc-retelling replies (2026-07-23, maintainer).
    fluency = str(snapshot.get("fluency") or "").strip()
    if fluency == "weave":
        lines.append(
            "- Reader fluency: `fluency: weave` — this reader reads the "
            "register; replies may keep its density (coordinates, deltas, "
            "marks). Density, not extra length: the reply is still the delta."
        )
    elif fluency:
        lines.append(
            f"- Reader fluency: `fluency: {fluency}` — unfold replies into "
            "plain language. Deeper where meaning needs it, never longer."
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
        any_truncated = False
        for group in groups:
            if not isinstance(group, dict):
                continue
            label = str(group.get("label") or group.get("id") or "").strip()
            path = str(group.get("path") or "").strip()
            if not label or not path:
                continue
            count = group.get("record_count", 0)
            if group.get("truncated"):
                any_truncated = True
                total = group.get("total_record_count", count)
                store_path = str(group.get("store_path") or "").strip()
                where = f" — full history: `{store_path}`" if store_path else ""
                lines.append(
                    f"  - {label}: `{path}` (latest {count} of {total} "
                    f"records{where})"
                )
            else:
                lines.append(f"  - {label}: `{path}` ({count} records)")
        note = (
            "  Read these JSONL files only when the snapshot is too thin; "
            "they are runtime records grouped by gate/forge thread"
        )
        note += (
            ", truncated to the latest per group where noted above — the "
            "full history for a truncated thread lives at its store path."
            if any_truncated else "."
        )
        lines.append(note)

    forge_block = _format_forge_state(snapshot.get("forge"))
    if forge_block:
        if lines:
            lines.append("")
        lines.append(forge_block)

    live_menu = snapshot.get("live_menu")
    if isinstance(live_menu, dict):
        resolved_prs = forge_state.resolved_pr_lookup(snapshot.get("forge"))
        rendered_menu = menus.render_numbered(live_menu, resolved_prs=resolved_prs)
        if lines:
            lines.append("")
        lines.append(
            "Live menu — the same validated generation rendered at the gate; "
            "free text always overrides it:"
        )
        lines.append(rendered_menu or "(no standing options)")

    turns = _format_recent_conversation(
        snapshot.get("recent_turns"),
        event_body=event_body,
        turn_max_bytes=turn_max_bytes,
        brr_dir=brr_dir,
    )
    if turns:
        if lines:
            lines.append("")
        lines.append("Recent turns (woven, oldest first):")
        lines.append(turns)
    return "\n".join(lines)


def _format_pr_state(pr_state: Any, *, default_branch: str | None = None) -> list[str]:
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
            marker = forge_state.format_pr(pr, default_branch=default_branch)
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
    default_branch = forge.get("default_branch")
    lines: list[str] = ["Forge state (local, network-free):"]
    lines.append(f"- {forge_state.render_prod_line(forge.get('prod'))}")

    worktrees = forge.get("worktrees")
    worktree_summary = forge_state.summarize_worktrees(worktrees)
    if worktree_summary["total"]:
        bits = [f"{worktree_summary['total']} total"]
        external_note = forge_state.external_worktree_note(worktree_summary)
        if external_note:
            bits.append(external_note)
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
            tid = forge_state.worktree_label(wt)
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
            pr = forge_state.format_pr(wt.get("pr"), default_branch=default_branch)
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
        lines.extend(
            _format_pr_state(forge.get("pr_state"), default_branch=default_branch)
        )

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
    *,
    event_body: str | None = None,
    turn_max_bytes: int = 0,
    brr_dir: Path | None = None,
) -> str:
    """Render the last few conversation records as human-readable bullets.

    Callers pass only prior records; the current event body is rendered
    separately in the Run Context Bundle (or passed as ``event_body`` here
    so a `source: schedule` turn can be checked against it). Returns an
    empty string when nothing useful is available.

    Issue #576: a `source: schedule` turn whose body is a near-duplicate
    (>= ``SCHEDULE_TURN_DEDUP_RATIO``) of either the current event body or
    an earlier schedule turn already kept in full here collapses to a
    one-line stub instead of repeating the whole body. A user repeating
    themselves is signal and is left alone; only `schedule`-sourced turns
    are ever collapsed.

    Issue #736: independently of that, any turn body over
    ``turn_max_bytes`` keeps its head and states what it dropped —
    ``…N B elided · full turn: <path>`` (see :func:`_cap_turn_body`). The
    two mechanisms answer different questions (*is this a repeat?* vs *is
    this too big?*) and a turn can pass one and fail the other. The cap
    applies to every kind that renders a body, not only `schedule`: a
    forge gate can carry a whole PR description just as easily. Callers
    that pass no ``turn_max_bytes`` get the uncapped, pre-#736 rendering,
    which is what the bare-helper tests and any non-daemon caller want.

    Issue #755: a third mechanism, on a third axis. Every `artifact` record
    carrying a body is the resident's *own* prior outbound, and replaying it
    verbatim feeds the voice back to itself; all but the newest collapse to a
    one-line receipt — first line, byte count, pointer to the full turn (see
    :func:`_own_outbound_receipt`). Where the other two ask *is this a
    repeat?* and *is this too big?*, this asks *whose text is this?*, so
    unlike the cap it applies regardless of ``turn_max_bytes``: opting out of
    a size limit is not a request to be fed your own voice back. Inbound
    `event` records and the `run` / `update` kinds are untouched.
    """
    if not records:
        return ""
    bullets: list[str] = []
    kept_schedule_turns: list[tuple[str, str]] = []
    window = records[-RECENT_CONVERSATION_MAX:]
    newest_own_outbound = _newest_own_outbound_index(window)
    for index, record in enumerate(window):
        kind = record.get("kind")
        ts = record.get("ts", "")
        line: str | None = None
        if kind == "event":
            body = _conversation_body(record)
            summary = body or (record.get("summary") or "").strip()
            source = _conversation_source_label(record)
            if str(record.get("source") or "").strip() == "schedule" and summary:
                stub = _schedule_turn_dedup_stub(
                    summary, event_body=event_body, kept=kept_schedule_turns
                )
                if stub is not None:
                    summary = stub
                else:
                    kept_schedule_turns.append((ts, summary))
            summary = _cap_turn_body(
                summary, record, limit=turn_max_bytes, brr_dir=brr_dir
            )
            marker = _attachment_marker(record, brr_dir=brr_dir)
            if marker:
                summary = f"{summary} {marker}".strip() if summary else marker
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
                keeps_full_body = (
                    OWN_OUTBOUND_KEEP_NEWEST_IN_FULL
                    and index == newest_own_outbound
                )
                if keeps_full_body:
                    body = _cap_turn_body(
                        body, record, limit=turn_max_bytes, brr_dir=brr_dir
                    )
                else:
                    body = _own_outbound_receipt(body, record, brr_dir=brr_dir)
                line = _format_turn(f"{ts} agent ({label})", body)
            else:
                path = record.get("path") or ""
                line = f"- {ts} artifact {label} {path}".rstrip()
        if line:
            bullets.append(line)
    return "\n".join(bullets)


def _newest_own_outbound_index(window: list[dict[str, Any]]) -> int:
    """Index of the last own-outbound record in *window*, or ``-1``.

    "Own outbound" = an `artifact` record that carries a body: every producer
    of one (`conversations.append_artifact`, called for responses, interim
    replies, gate messages, spawn/respawn requests, policy proposals) is
    writing text the resident itself authored. A bodiless artifact record is
    a bare path line and has nothing to collapse.

    Deliberately computed over the *rendered window* rather than all
    ``records``: the exception exists so the resident can see what it just
    replied, and a record trimmed off by ``RECENT_CONVERSATION_MAX`` is not
    on screen to be the newest of anything.
    """
    for index in range(len(window) - 1, -1, -1):
        record = window[index]
        if record.get("kind") == "artifact" and _conversation_body(record):
            return index
    return -1


def _own_outbound_receipt(
    body: str,
    record: dict[str, Any],
    *,
    brr_dir: Path | None,
) -> str:
    """Collapse one own-outbound turn to a receipt line (#755).

    ``<first non-empty line, ~200 chars> · <NNN> B · full turn: <path>`` —
    single-line by construction, so :func:`_format_turn` renders it inline
    after the ``ts agent (label)`` prefix rather than as an indented block.

    All three parts load-bear, the same way #736's elision marker does: the
    head says which message this was, the byte count says how much text the
    receipt stands in for, and the pointer says where to read it. The pointer
    is :func:`_turn_store_pointer` — the same existence-checked derivation the
    cap uses, so a receipt never names a path that does not resolve, and the
    two mechanisms cannot drift apart on where a full turn lives.
    """
    head = ""
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped:
            head = stripped
            break
    if len(head) > OWN_OUTBOUND_RECEIPT_HEAD_CHARS:
        head = head[: OWN_OUTBOUND_RECEIPT_HEAD_CHARS - 1].rstrip() + "…"
    return OWN_OUTBOUND_RECEIPT_MARKER.format(
        head=head,
        size=len(body.encode("utf-8")),
        pointer=_turn_store_pointer(record, brr_dir),
    )


def _schedule_turn_dedup_stub(
    body: str,
    *,
    event_body: str | None,
    kept: list[tuple[str, str]],
) -> str | None:
    """Return a collapse stub for a near-duplicate `schedule` turn body.

    Checks the current run's event body first — that is the largest,
    guaranteed-duplicate case (the firing that woke this run, rendered a
    second time as a recent turn) — then earlier schedule turns already
    kept in full during this same render. Returns ``None`` when ``body``
    is novel enough to render in full, in which case the caller is
    responsible for adding it to ``kept``.
    """
    if event_body:
        ratio = difflib.SequenceMatcher(None, body, event_body).ratio()
        if ratio >= SCHEDULE_TURN_DEDUP_RATIO:
            return SCHEDULE_TURN_DEDUP_EVENT_STUB
    for kept_ts, kept_body in kept:
        ratio = difflib.SequenceMatcher(None, body, kept_body).ratio()
        if ratio >= SCHEDULE_TURN_DEDUP_RATIO:
            return SCHEDULE_TURN_DEDUP_TURN_STUB.format(ts=kept_ts)
    return None


def _recent_turn_byte_cap(repo_root: Path) -> int:
    """Resolve the per-turn byte cap for the recent-turns weave (#736).

    Returns 0 only when the cap is *deliberately* disabled — a configured
    value <= 0 — which the renderer treats as "render every turn in full",
    the pre-#736 behaviour.

    An unreadable or unparseable config **falls back to the default cap, not
    to 0**: a config this function cannot read is not a config saying "no
    limit", and reading silence as consent to an unbounded turn is how the
    12 KB ghost got in. Fail closed. (Reviewed 2026-07-25: the first draft of
    this docstring claimed the opposite of the code beneath it, which is the
    exact class #736's own sibling tickets are filed under — a comment
    claiming more, or other, than the code can do, in the place a reader goes
    to check.)
    """
    try:
        cfg = conf.load_config(repo_root)
        raw = cfg.get(RECENT_TURN_MAX_BYTES_KEY, RECENT_TURN_MAX_BYTES)
        limit = int(raw)
    except Exception:
        return RECENT_TURN_MAX_BYTES
    return limit if limit > 0 else 0


def _turn_store_pointer(record: dict[str, Any], brr_dir: Path | None) -> str:
    """Where the *full* text of one elided turn is readable, or what's known.

    Every conversation record lands in exactly one `<event-id>.jsonl` under
    its own thread's store directory (`conversations.event_log_path`), and
    woven records carry `conversation_key` (tagged by
    `conversations._tag_record`) plus `event_id` — so the pointer is the
    turn's own file, not the thread's tail copy under
    `.brr/runs/<run>/history/`, which is bounded and can itself have
    dropped the record.

    **Existence-checked on purpose.** A marker naming a path that does not
    resolve is worse than no marker, so every rung here either stats a real
    location or degrades to saying what it can offer. The degradations are
    reachable for a record with no `conversation_key` (never produced by
    the daemon's append path, but tests and hand-built snapshots do it) and
    for any record whose store file has since been pruned.
    """
    if brr_dir is None:
        return "full turn: conversation store not resolvable on this wake"
    from . import conversations

    key = str(record.get("conversation_key") or "").strip()
    if not key:
        return "full turn: this turn carries no thread key to resolve"
    event_id = str(record.get("event_id") or "").strip()
    try:
        path = conversations.event_log_path(brr_dir, key, event_id)
        if path.is_file():
            return f"full turn: `{path}`"
        store = conversations.conversation_path(brr_dir, key)
        if store.is_dir():
            return f"full turn: not in one file — thread store: `{store}`"
    except OSError:
        pass
    return f"full turn: no stored record found for thread `{key}`"


def _cap_turn_body(
    body: str,
    record: dict[str, Any],
    *,
    limit: int,
    brr_dir: Path | None,
) -> str:
    """Return *body* truncated to *limit* bytes with the elision stated.

    **Keeps the head, drops the tail** (#736). A turn's first lines carry
    who said what and why they woke someone; the tail is accreted
    rationale. Note for the next reader: this repo's two other trimmers
    each chose a *different* direction for their own reasons —
    `dominion._collapse_markdown_to_budget` drops bottom-up, and
    `prompts._trim_sectioned_page` picks its direction per page from the
    page's own headings (`_page_is_chronological`: dated ⇒ keep the tail,
    undated ⇒ keep the head). Neither governs here; a conversation turn has
    no `## ` structure to classify, so this is a third call made on this
    block's own content, not a convention to generalise.

    Truncation prefers the last line boundary inside the budget so the
    kept head stays valid markdown rather than ending mid-sentence; a
    single over-long first line still gets a hard byte cut (decoded with
    `errors="ignore"` so a multi-byte character is never split in half).
    """
    if limit <= 0:
        return body
    raw = body.encode("utf-8")
    if len(raw) <= limit:
        return body
    kept = _head_cut_at_line_boundary(body, limit)
    dropped = len(raw) - len(kept.encode("utf-8"))
    marker = RECENT_TURN_ELISION_MARKER.format(
        dropped=dropped, pointer=_turn_store_pointer(record, brr_dir)
    )
    return f"{kept}\n{marker}" if kept else marker


def _conversation_body(record: dict[str, Any]) -> str:
    body = record.get("body")
    return body.strip() if isinstance(body, str) else ""


def _record_attachment_paths(
    record: dict[str, Any], brr_dir: Path | None,
) -> list[Path]:
    """Resolve a woven conversation record's attachments to local paths.

    #1156: the record only ever carried ``{"kind", "filename"}`` facts (see
    ``conversations._attachment_facts``), never a path — so a folded-in
    turn's marker could name a count but never something ``Read`` could
    open. The record has no ``_path`` of its own (unlike an event dict), so
    this derives the same ``<inbox>/<event-id>.attachments/`` location
    ``protocol.event_attachment_paths`` uses, rooted at this run's own
    ``<brr_dir>/inbox`` drawer. A record from a different drawer (the
    account dispatch inbox, a sibling repo) or one whose bytes retention
    already swept resolves to no paths here, same as *genuinely gone* —
    the caller's job is to fall back to the bare marker in that case, not
    to claim a path this reader cannot see.
    """
    if not brr_dir:
        return []
    event_id = str(record.get("event_id") or "").strip()
    attachments = record.get("attachments")
    if not event_id or not isinstance(attachments, list) or not attachments:
        return []
    names = [
        str(item.get("filename") or "").strip()
        for item in attachments
        if isinstance(item, dict)
    ]
    names = [n for n in names if n]
    if not names:
        return []
    adir = protocol.attachments_dir_for_event(Path(brr_dir) / "inbox", event_id)
    return [p for p in (adir / n for n in names) if p.is_file()]


def _attachment_marker(
    record: dict[str, Any], *, brr_dir: Path | None = None,
) -> str:
    """``[photo ×2]``-style marker for a woven turn carrying attachments.

    Issue #943: a captionless inbound photo/document used to render as a
    blank turn in "Recent turns" — the fact of the attachment existed
    nowhere the weave looked. ``conversations.append_event`` mints the fact
    (``record["attachments"]``, a list of ``{"kind", "filename"}`` dicts);
    this is the cheap render half — a marker cheap enough to always include
    rather than a full describe-the-image pass. One bracket group per kind,
    in first-appearance order, so ``[photo ×2] [document ×1]`` reads left
    to right in the order the kinds arrived on the message.

    #1156: the marker used to be the whole answer — a count with no path,
    even while the bytes it counted sat on disk in this run's own inbox.
    When :func:`_record_attachment_paths` resolves at least one local file
    for this record, the marker now names them too. When it resolves none
    (a different drawer, retention already swept them, or the attachment
    was announced but never fetched — #1154's class of drop), the bracket
    count is still rendered — the announced fact survives even when the
    bytes do not.
    """
    attachments = record.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return ""
    counts: dict[str, int] = {}
    for item in attachments:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "attachment").strip() or "attachment"
        counts[kind] = counts.get(kind, 0) + 1
    marker = " ".join(f"[{kind} ×{n}]" for kind, n in counts.items())
    paths = _record_attachment_paths(record, brr_dir)
    if paths:
        marker += " (local: " + ", ".join(str(p) for p in paths) + ")"
    return marker


def _conversation_source_label(record: dict[str, Any]) -> str:
    parts = [str(record.get("source") or "").strip()]
    correspondent = str(record.get("correspondent_key") or "").strip()
    if correspondent:
        parts.append(f"correspondent={correspondent}")
    thread = str(record.get("conversation_key") or "").strip()
    if thread:
        parts.append(f"thread={thread}")
    # Correspondent-weave dedup (conversations._dedupe_woven_records)
    # collapses an exchange mirrored onto a sibling gate (cloud mirrors
    # telegram) into one turn on the earliest-arriving thread; this
    # names the sibling pipe(s) it also arrived on so the collapse never
    # silently erases which gates carried it.
    duplicates = record.get("duplicate_conversation_keys")
    if isinstance(duplicates, list):
        also = ", ".join(str(d).strip() for d in duplicates if str(d).strip())
        if also:
            parts.append(f"also-on={also}")
    return "; ".join(p for p in parts if p)


def _format_turn(prefix: str, body: str) -> str:
    if "\n" not in body:
        return f"- {prefix}: {body}".rstrip()
    indented = "\n".join(f"  {line}" if line else "" for line in body.splitlines())
    return f"- {prefix}:\n{indented}".rstrip()


# Wyrd §5, closing the resident half (maintainer, 2026-07-19: "not the whole
# thing, now + one line is fine"). The runs layer is the corpus's largest — the
# user can open any node on the dashboard — but nothing carried it back to the
# resident, who therefore maintained the run body strictly forward-blind: it
# wrote `.card` every wake and never once saw what the last one said. §5 claims
# the two faces are the same object at two unfoldings; this is the block that
# made that true rather than aspirational.
#
# Deliberately not the whole node. The frame's one line answers "how did it
# end", the body's `## Now` answers "what was I doing" — and the full node
# stays one `Read` away for the rare wake that needs the middle.
_PRIOR_RUN_FRAME_KEYS = ("status", "stage", "runner_name", "publish_status", "branch_name")


def _prior_run_node(repo_root: Path) -> tuple[Path, Path] | None:
    """Locate the newest run node that actually wrote a body, or ``None``.

    Newest-body rather than newest-node is what keeps the *current* run out of
    its own wake: at prompt-build time this run's frame already exists (the
    daemon writes it at dispatch) but its body cannot — the body is mirrored
    from a card the resident has not written yet. The selection rule is the
    exclusion rule, with no run id to thread through.
    """
    from . import account as account_mod
    from . import config as conf

    try:
        cfg = conf.load_config(repo_root)
        ctx = account_mod.resolve_context(repo_root, cfg, create=False)
    except Exception:
        return None
    if not ctx.enabled or not ctx.runs_dir.is_dir():
        return None
    # Scoped to *this* repo's runs. Falling back to the whole account would
    # hand a wake the last run of a neighbouring repo, which is worse than
    # handing it nothing: a plausible, wrong memory is harder to catch than
    # an absent one.
    from . import daemon as daemon_mod

    label = daemon_mod._repo_label(repo_root, None, cfg)
    if not label:
        return None
    root = ctx.runs_dir / account_mod.slug_repo_label(label)
    if not root.is_dir():
        return None
    newest: tuple[float, Path] | None = None
    for body in root.glob("*/body.md"):
        try:
            stamp = body.stat().st_mtime
        except OSError:
            continue
        if newest is None or stamp > newest[0]:
            newest = (stamp, body)
    if newest is None:
        return None
    return newest[1].parent / "state.md", newest[1]


def _build_prior_run_block(repo_root: Path) -> str:
    """Hand the resident its own last run: one attestation line + that run's Now."""
    from . import protocol

    located = _prior_run_node(repo_root)
    if located is None:
        return ""
    state_path, body_path = located
    try:
        body = body_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    now = _now_projection(body)
    if not now:
        return ""
    fields: dict[str, str] = {}
    try:
        fields = protocol.parse_frontmatter(state_path.read_text(encoding="utf-8"))
    except OSError:
        pass
    run_id = str(fields.get("run_id") or body_path.parent.name)
    parts = [run_id]
    parts.extend(
        str(fields[key]) for key in _PRIOR_RUN_FRAME_KEYS
        if str(fields.get(key) or "").strip()
    )
    shape = _body_section_shape(body)
    rendered = [
        "## Your last run\n",
        "The node you wrote last wake, from `runs/<repo>/<run>/`: the attested "
        "frame in one line, the `## Now` you left on the card, and the shape "
        "of the rest. Section names, not their contents — the map of what that "
        "run recorded, so a wake knows whether the territory is worth opening. "
        "The full body and the run's message traffic live on the node.\n",
        f"`{' · '.join(parts)}`\n",
        now,
    ]
    if shape:
        rendered.append("\nalso in that body: " + " · ".join(shape))
    guard_line = _guard_line(body_path.parent)
    if guard_line:
        rendered.append("\n" + guard_line)
    return "\n".join(rendered)


def _guard_line(node_dir: Path) -> str:
    """``guard: 16 stops · blocked ×1 · final stop clear`` — or ``""``.

    The closeout guard's own verdict, projected from ``boundaries.json``
    (``hooks.derive_boundaries_summary``, written beside this node's
    ``body.md``/``state.md`` by ``daemon._persist_boundaries_summary``). The
    frame line above carries the reply and its status; until this, nothing
    on the node said whether the guard *agreed* with that reply — a run that
    ended on a false ``continuing`` claim (the guard fired, and the run ended
    anyway) read identical to a clean close. ``BLOCKED`` (uppercase, the one
    case this line exists for) means the run's last Stop was still live under
    a block when it ended; ``clear`` means the guard had nothing outstanding.

    Absent — not ``"guard: unknown"`` — when the summary itself is: an older
    node, one from a run whose transcript never parsed. A placeholder here
    would read as a clean bill of health for a run this feature cannot
    actually vouch for.
    """
    import json

    path = node_dir / "boundaries.json"
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(summary, dict):
        return ""
    stops = summary.get("stops")
    if not isinstance(stops, int):
        return ""
    parts = [f"{stops} stop{'s' if stops != 1 else ''}"]
    fire_count = summary.get("guard_fire_count")
    if isinstance(fire_count, int) and fire_count > 0:
        parts.append(f"blocked ×{fire_count}")
    verdict = "BLOCKED" if summary.get("final_stop_block") else "clear"
    parts.append(f"final stop {verdict}")
    return "guard: " + " · ".join(parts)


def _body_section_shape(body: str) -> list[str]:
    """The body's top-level section names, minus the ``Now`` already rendered.

    The compiled half of the wake's memory (maintainer, 2026-07-19: "maybe
    header + sections' headers, maybe just top"). A heading list is a
    remarkably high ratio of orientation to tokens: "also in that body: Arc ·
    Decisions · Open" tells a wake what kind of run that was and what it would
    find, at a cost that does not scale with how much the run actually wrote.

    Depth-agnostic since #722: an H1-sectioned card has a shape, and reporting
    it as shapeless was the same defect as projecting it whole — the wake was
    told "also in that body: " and nothing, about a body full of sections.
    """
    return card.section_names(body)


def _now_projection(body: str) -> str:
    """The body's ``Now`` section, or the whole body when it has none.

    One rule, shared with ``daemon._card_now_projection`` and the hooks
    boundary meter since #722; the earlier local copy was the second of three
    that drifted. Its stated reason for staying local — that prompts must not
    pull ``daemon`` into every wake's import graph — measured true (that
    direction costs ~46 ms), which is why the rule moved *outward* to the leaf
    module :mod:`brr.card` rather than into either caller. See that module for
    the numbers and for what they did not justify.

    No *limit* here. This projection is read by the next wake, not published
    to a transport, so bounding it would cost the resident its own memory to
    solve a problem this path does not have.
    """
    return card.now_projection(body)
