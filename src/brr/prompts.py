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
from typing import Any

from . import account, config as conf, dev_reload, forge_state


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


def _heading_time(entry: str, date: str) -> str | None:
    """``HHMM`` from a run-id in the heading, but only if it *corroborates*.

    A run-id embeds both a date and a time. The time is trusted only when
    the run-id's own date matches the heading's date — measured on this
    account's live ledger, 14 of 160 entries disagree (an entry written
    after midnight about the previous day's run), and a heading date paired
    with some other day's clock time is not a timestamp, it is two facts
    glued together. Returns ``None`` on any disagreement or absence, which
    downgrades the comparison to day granularity rather than guessing.
    """
    match = _HEADING_RUNID_RE.search(entry.split("\n", 1)[0])
    if not match:
        return None
    yy, mm, dd, hhmm = match.groups()
    return hhmm if f"20{yy}-{mm}-{dd}" == date else None


def _entry_key(entry: str) -> tuple[str, str | None] | None:
    """``(date, time_or_None)`` for a ``## `` entry, or ``None`` if undated."""
    date = _heading_date(entry)
    if date is None:
        return None
    return date, _heading_time(entry, date)


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
      source's newest date carries a corroborated one** (*precise*). That
      cohort is exactly the set that can decide a tie; an entry on an older
      date is settled by the day comparison above and its missing time is
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
    # corroborated time — and that is the cohort sharing the source's newest
    # date, not the whole file. Time is only ever the tie-breaker: an entry
    # dated earlier than the source's newest can never be the source's newest,
    # so its missing time cannot change the verdict. Scoping this to the whole
    # file was strictly stronger than the proof needs, and the cost was not
    # theoretical — measured on this account's live ledger (162 entries, 55
    # untimed, but only 1 untimed on the newest date), whole-file scope holds
    # `precise` at False permanently. Legacy headings cannot be repaired
    # without inventing timestamps, so the strong tier could never turn on,
    # however disciplined later writing became: a guard gated on a condition
    # the past can no longer satisfy is a guard that never fires.
    top_date = max(k[0] for k in all_keys)
    precise = all(k[1] is not None for k in all_keys if k[0] == top_date)

    # Normalize the missing time to "" before ordering: a set mixing timed and
    # untimed headings would otherwise compare str against None and raise.
    # Safe because staleness only consults the time component when both sides
    # sit on `top_date` (see below), where `precise` guarantees a real time —
    # so "" is never the deciding term.
    def _ord(key: tuple[str, str | None]) -> tuple[str, str]:
        return key[0], key[1] or ""

    newest_key = max(picked_keys, key=_ord)
    oldest_key = min(picked_keys, key=_ord)
    source_key = max(all_keys, key=_ord)

    if source_key[0] > newest_key[0]:
        stale = True
    elif precise:
        stale = source_key > newest_key
    else:
        stale = False

    def shown(key: tuple[str, str | None]) -> str:
        """Render a key for human eyes, at the precision actually established.

        When the comparison was precise, the time *must* appear: a same-day
        alarm that renders as "newest 2026-07-23 — source has 2026-07-23" is
        correct and unreadable, and an alarm nobody can parse is not much
        better than the silence it replaced.
        """
        if precise and key[1]:
            return f"{key[0]} {key[1][:2]}:{key[1][2:]}"
        return key[0]

    return shown(newest_key), shown(oldest_key), shown(source_key), stale, precise


@dataclass(frozen=True)
class TrimResult:
    """What a chronological-tail trim rendered, and what it can attest to.

    ``text`` is the rendered page — the whole return value of every caller
    before this class existed. The four attestation fields are the facts
    ``_tail_trim_entries`` and ``_read_recent_log`` already computed while
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

    ``True`` only when every entry sharing the source's newest date — the
    cohort that can actually decide a same-day tie — carried a *corroborated*
    run-id time. When ``False`` and the tail's newest shares a date with the source's
    newest, this result can say "not known to be stale" but **must not** say
    the tail is current — a distinction :func:`_trim_marker` renders and
    ``attest_blocks`` respects by staying silent rather than reassuring.
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


def _tail_trim_entries(content: str, max_bytes: int, source_hint: str) -> TrimResult:
    """Trim an append-only, chronological-ascending page to fit *max_bytes*.

    Accreting surface pages only ever grow — the resident's convention is "add an
    entry", never "prune the last one" (see ``_MAX_ACCRETING_BLOCK_BYTES``).
    Mirrors ``_read_recent_log``'s newest-first, entry-boundary-aware
    accumulation, generalized past ``kb/log.md``'s bracketed ``## [date]``
    heading to a plain ``## `` heading: keep the file's leading preamble,
    then walk ``## `` entries from the bottom (newest, since these pages
    append at the end rather than prepend) backward, keeping everything
    that fits and always keeping at least the newest entry even if it alone
    exceeds budget — the most recent decision never silently disappears.

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
    place that had them.
    """
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return TrimResult(text=content)
    match = _H2_RE.search(content)
    if not match:
        tail = encoded[-max_bytes:].decode("utf-8", errors="ignore")
        return TrimResult(text=(
            f"_(older content cut to fit the wake budget — full page: "
            f"{source_hint})_\n\n{tail}"
        ))
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

    newest_item = oldest_item = source_newest = None
    stale = precise = False
    dropped: int | None = None
    if omitted:
        dropped = omitted
        newest_item, oldest_item, source_newest, stale, precise = _entries_attestation(
            entries, picked
        )

    pieces: list[str] = []
    if preamble:
        pieces.append(preamble)
    if omitted:
        pieces.append(_trim_marker(
            omitted, oldest_item, newest_item, source_newest, source_hint,
            stale=stale, precise=precise,
        ))
    pieces.append("".join(picked).strip())
    text = "\n\n".join(p for p in pieces if p)
    return TrimResult(
        text=text,
        newest_item=newest_item,
        oldest_item=oldest_item,
        dropped=dropped,
        source_newest=source_newest,
        stale=stale,
        precise=precise,
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
    ``_tail_trim_entries``, whose "newest" is a positional assumption this
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
        if not entries:
            return ""
        findings = schedule_mod.lint_schedule(
            entries,
            now=now,
            state=schedule_mod.load_state(gitops.shared_brr_dir(repo_root)),
            forge=forge_pr_cache.read_state(repo_root, now=now),
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


def _build_work_surface_block_scored(repo_root: Path) -> TrimResult:
    """The scored implementation behind ``_build_work_surface_block``.

    Same split as ``_build_context_block`` / ``..._scored``: the plain
    function stays a ``str``-returning wrapper (unchanged signature —
    existing callers and tests are untouched); this variant also surfaces
    one representative attestation (see ``_worst_trim``) for
    ``_build_injected_blocks_with_contracts`` to copy onto the
    ``work-surface`` ``ContractEntry``.
    """
    from . import account as acc
    from . import config as conf

    cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return TrimResult(text="")
    try:
        ctx = acc.resolve_context(repo_root, cfg, create=False)
    except Exception:
        return TrimResult(text="")
    if not ctx.enabled:
        return TrimResult(text="")

    surface = acc.work_surface_path(ctx)
    budget = int(
        cfg.get(
            "dominion.surface_inject_budget_bytes",
            cfg.get("dominion_surface_inject_budget_bytes", 48_000),
        )
    )
    blocks: list[str] = []
    trims: list[TrimResult] = []
    remaining = max(0, budget)
    for path in acc.work_surface_files(ctx):
        relative = path.relative_to(surface).as_posix()
        content = path.read_text(encoding="utf-8").strip()
        if not content or remaining <= 0:
            continue
        allowance = min(remaining, _MAX_ACCRETING_BLOCK_BYTES)
        trimmed = _tail_trim_entries(content, allowance, f"`surface/{relative}`")
        block = f"### {relative}\n\n{trimmed.text}"
        size = len(block.encode("utf-8"))
        if size > remaining:
            # Heading overhead can push a budget-trimmed page just past the
            # remainder. Skip *this* page, not every page after it — the next
            # (smaller) file may still fit.
            continue
        blocks.append(block)
        trims.append(trimmed)
        remaining -= size

    if not blocks:
        text = (
            "## Work surface\n\n"
            "No authored surface yet. Start at `surface/index.md`; pages placed "
            "under `surface/` are discovered by the next wake and dashboard."
        )
        return TrimResult(text=text)
    text = (
        "## Work surface\n\n"
        "The shared user/resident orientation, discovered from one authored "
        f"root: `{surface}`. Add, move, or link Markdown there; do not create "
        "parallel orientation roots elsewhere in home. The dashboard mirrors "
        "the same discovered set.\n\n"
        + "\n\n---\n\n".join(blocks)
    )
    worst = _worst_trim(trims)
    return TrimResult(
        text=text,
        newest_item=worst.newest_item,
        oldest_item=worst.oldest_item,
        dropped=worst.dropped,
        source_newest=worst.source_newest,
        stale=worst.stale,
        precise=worst.precise,
    )


def _build_work_surface_block(repo_root: Path) -> str:
    """Render the discovered shared work surface as one orientation block.

    Membership is filesystem-authored: every non-hidden Markdown file below
    ``surface/`` rides the wake without a new prompt mount. ``index.md`` leads;
    all remaining files follow by relative path. A total budget bounds the
    surface while preserving each accreting page's newest entries.
    """
    return _build_work_surface_block_scored(repo_root).text


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
    the one place both resident and worker wakes read runner facts (the
    resident-only injected-block stack is skipped for workers, and a worker
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
    stats = kb_health.compute_graph_stats(repo_root, kb_dir)
    ownership = _kb_ownership_signal(size_pressure, stats)

    if not integrity and not ownership:
        return ""

    sections: list[str] = []
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
    orphans = len(getattr(stats, "peer_orphans", []) or [])
    if not pressure and not orphans:
        return ""
    bits = []
    if pressure:
        bits.append(f"{pressure} page(s)/log over a size threshold")
    if orphans:
        bits.append(f"{orphans} indexed page(s) no peer links to")
    return (
        "**Ownership signal** — " + "; ".join(bits) + ". Not a list of pages to "
        "trim: a byte count cannot tell a load-bearing page from bloat — you can. "
        f"The graph is {stats.total_pages} pages, log {stats.log_bytes:,} B over "
        f"{stats.log_entry_count} entries. Read this as the kb asking for a "
        "maintenance *round* — promote what's load-bearing, breadcrumb what's "
        "spent, cut what's dead, relink the orphans. Worker-delegable; worth a "
        "dedicated pass, not a per-wake reflex to shorten the longest file. Full "
        "graph shape on demand: `brnrd kb`."
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
    transcript (``boot.mount``) must take exactly those blocks *out of the
    prose*, or the wake pays for them twice and the T-vs-P experiment measures
    nothing.  An unkeyed ``list[str]`` made that subtraction impossible to state;
    a keyed one makes it a dict lookup.

    Shared by ``_build_injected_blocks``, ``build_injected_context``, and
    the scored prompt-builder variants — one computation, three consumers.
    """
    from .bootscore import (
        ContractEntry,
        OWNER_PRODUCT, OWNER_RESIDENT, OWNER_PROJECT, OWNER_DAEMON_LIVE,
        AUTHORITY_IDENTITY, AUTHORITY_MEMORY, AUTHORITY_SURFACE, AUTHORITY_POLICY,
        AUTHORITY_KNOWLEDGE, AUTHORITY_ACTIVITY, AUTHORITY_HEALTH,
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

    # 3. One discovered shared orientation root.
    work_surface_trim = _build_work_surface_block_scored(repo_root)
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

    # 5. Pitfalls matching the task
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

    return keyed, contracts


def _build_injected_blocks(
    repo_root: Path, *, task_text: str | None = None
) -> list[str]:
    """The standing, always-on context blocks brr injects into every wake.

    Returns the *base* blocks:

    1. Resident identity core — product-owned invariant contract
    2. Dominion digest (living playbook + ``self-inject``)
    3. Discovered work surface — the shared authored orientation
    4. Stored runner policy (CS6) — standing runner preferences
    5. Pitfalls matching the task
    6. Recent-activity log tail
    7. kb health note

    The ordering puts the product identity contract before the resident-owned
    state (dominion + work surface + policy), then the shared project
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
    injected blocks (identity core, dominion digest, work surface, runner
    policy, pitfalls, knowledge sources, kb health) and the
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

    # register.md — a *worked example* of the register (weave.md is the rules;
    # this is a being mid-wake, written in them). Resident path only: a bounded
    # worker gets the register contract but not the personality exemplar, which
    # is orientation for a light that has to sustain a whole run, not labour.
    # Rides right after weave.md so a mounted wake reads the rule then the hand.
    if not is_worker:
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
            action="write .card",
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


#: Cap on the orientation set (#513: "3–5 files"). The cap bounds the walk's
#: cost; the *floor* is deliberately zero — a set the derivation cannot prove
#: is a set it does not pad ("the set is 3 files, not 5 with two guesses").
_ORIENTATION_SET_MAX = 5


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
) -> list[Any]:
    """The orientation *ledger*'s file set (#513 Slice 9) — never the kernel's
    ``next:`` list (that is :func:`_build_orientation`; see
    :class:`brr.bootscore.OrientationFile` for why the two words coexist).

    Deterministic, existence-proven, capped at :data:`_ORIENTATION_SET_MAX`:

    - the repo's ``AGENTS.md`` — **unless the Shell already read it**, see
      :func:`shell_reads_agents_md_natively`;
    - the active inter-run plan (``account.active_plan_path``);
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
    """
    from .bootscore import OrientationFile

    candidates: list[Path] = []
    if not shell_reads_agents_md_natively(runner_shell):
        candidates.append(repo_root / "AGENTS.md")

    try:
        cfg = conf.load_config(repo_root)
        ctx = account.resolve_context(repo_root, cfg, create=False)
        label = account.repo_label(repo_root, cfg)
        candidates.append(account.active_plan_path(ctx, label))
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
        orientation_set=_build_orientation_set(
            effective_root, task_text=task_text, runner_shell=runner_shell
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

    Resident stack (``worker=False``, F3): the user meets the being they
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
    kwargs.setdefault("worker", False)
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
    # Order mirrors read/authority: how you write (weave), you having written
    # (register — resident only), then who drives (daemon-substrate). Kept in
    # lockstep with :func:`_collect_preamble_contracts`, which registers the same
    # blocks in the same order for the manifest and the mount.
    riders = [("weave.md", "weave")]
    if not worker:
        riders.append(("register.md", "register"))
    riders.append(("daemon-substrate.md", "daemon-substrate"))
    for name, k in riders:
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
    (identity core, dominion digest, work surface, runner policy, pitfalls,
    knowledge sources, kb health, introspection) are
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
        diffense=diffense,
    )
    trailer = bundle.rstrip()
    if (event_body or "").strip() != task.strip():
        trailer = f"{trailer}\nRun instruction: {task}"

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
    # touch the set.
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
            bits.append(f"quota={item['quota_source']}")
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
            "another worker). You share one dominion, so if one is on the "
            "same stream or files, expect its edits to land alongside yours "
            "— don't fight it. Contradictions in shared memory are normal "
            "and get reconciled by judgement, not locks (see your playbook)."
        )
        sections.append("")
        sections.append(presence_block)

    snapshot_block = _format_communication_snapshot(
        communication_snapshot, event_body=event_body
    )
    if snapshot_block:
        sections.append("")
        sections.append("### Communication snapshot")
        sections.append("")
        sections.append(snapshot_block)
    else:
        recent_block = _format_recent_conversation(
            recent_conversation, event_body=event_body
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
    *,
    event_body: str | None = None,
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

    turns = _format_recent_conversation(
        snapshot.get("recent_turns"), event_body=event_body
    )
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
    *,
    event_body: str | None = None,
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
    """
    if not records:
        return ""
    bullets: list[str] = []
    kept_schedule_turns: list[tuple[str, str]] = []
    for record in records[-RECENT_CONVERSATION_MAX:]:
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
    return "\n".join(rendered)


def _body_section_shape(body: str) -> list[str]:
    """The body's ``##`` section names, minus the ``Now`` already rendered.

    The compiled half of the wake's memory (maintainer, 2026-07-19: "maybe
    header + sections' headers, maybe just top"). A heading list is a
    remarkably high ratio of orientation to tokens: "also in that body: Arc ·
    Decisions · Open" tells a wake what kind of run that was and what it would
    find, at a cost that does not scale with how much the run actually wrote.
    """
    names: list[str] = []
    for line in body.splitlines():
        if not line.startswith("## "):
            continue
        name = line[3:].strip()
        if name and name.casefold() != "now":
            names.append(name)
    return names


def _now_projection(body: str) -> str:
    """The body's ``## Now`` section, or the whole body when it has none.

    Third implementation of one rule (``daemon._card_now_projection``,
    ``runNode.ts:nowProjection``). Kept local rather than imported from the
    daemon: prompts must not pull the daemon module into every wake's import
    graph for eighteen lines of string handling.
    """
    lines = body.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip().casefold() == "## now":
            start = index + 1
            break
    if start is None:
        return body.strip()
    projected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        projected.append(line)
    return "\n".join(projected).strip()
