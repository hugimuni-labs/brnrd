"""Slice 1 of ``design-the-allowance.md`` — a strand's own token budget.

The maintainer's ask (signed 2026-09-05, evt-…-t5pk/r033): a strand carries a
token allowance instead of reading the shared, lagging quota percentage —
"the percentage doesn't mean anything and is hard to derive" from inside a
concurrent child. This module is the metering half: parsing/formatting the
unit, and reading each Shell's own on-disk record for a live, per-run
cumulative token count.

**Step zero's finding** (design-the-allowance.md's own dispatch: "find how
the daemon already reads a run's token usage ... name which give a per-run
token count today and which only the provider's shared gauge"):

Neither existing level collector gives what this slice needs.

- **claude** (:mod:`brr.claude_status`) only has something to report once the
  whole headless invocation has produced its final ``--output-format json``
  result — mid-run (which is most of a strand's life) ``claude_status.
  load_snapshot`` reads either nothing yet or a *different, earlier* run's
  stale reading (see ``daemon._collect_levels``'s own docstring on the
  cross-run fallback, #1027). Even that final envelope's ``modelUsage``
  totals are cumulative for the whole *resumed* session, not scoped to one
  run, in the general case — safe here only because a strand's session is
  never resumed across daemon wakes (it dies with its stream, #996).
- **codex** (:mod:`brr.codex_status`) reads live, but only
  ``info.last_token_usage`` — the *last request's* size, the right number
  for context-window occupancy and the wrong one for a running spend total.
  The rollout's own ``info.total_token_usage.total_tokens`` is genuinely
  cumulative per thread and already present in every real payload
  (``tests/test_codex_status.py`` fixtures carry it); it was simply never
  read into ``levels["tokens"]``.

So this module reads *live*, from each Shell's own on-disk record, rather
than reusing either existing collector:

- **claude** — sums every assistant turn's own ``usage`` in the session
  transcript. Each turn's own ``{input,output,cache_read,cache_creation}``
  counts are that call's real billed cost — the same arithmetic
  ``claude_status._model_usage_tokens`` already trusts for spend/volume
  accounting ("the right shape for spend/volume accounting"), read live off
  the transcript instead of waiting for the final envelope that only exists
  once the whole process exits. The transcript is located by the newest
  ``.jsonl`` under the *cwd*'s own projects slug — safe for a strand
  specifically (not a general Claude reader) because every strand runs in
  its own isolated worktree (``daemon-substrate.md``'s ``spawn:`` row:
  worktree is the isolation floor), so its cwd is unique and cannot cross-
  read a sibling's session the way a bare newest-mtime scan over the whole
  projects root would (the exact hazard ``codex_status.
  _latest_rollout_fallback`` documents and defends against for codex's
  *shared* sessions root).
- **codex** — ``info.total_token_usage.total_tokens`` off the last
  ``token_count`` event, via the same exact ``thread_id`` correlation
  :func:`brr.codex_status.load_levels` already uses.

**The unit is cost-weighted tokens** (input-equivalent), not the raw sum.
Measured at review on a real strand transcript (run-260904-2331-9e3x, 521
assistant turns): raw sum 146,204,793 of which 144,941,516 were cache
*re-reads* of the same context — a raw count is ~100x the bill and would
fire the ≥100% directive on a strand's third boundary against any sane
ceiling. Weighted with the providers' own published price ratios
(:data:`TOKEN_WEIGHTS`: cache-read 0.1, cache-write 1.25, output 5, fresh
input 1) that strand reads ~17.4m — the number a ceiling can be set against.
The per-provider *quota* exchange rate (percent per weighted token) is still
slice 3's learned rate; this weighting only makes the unit proportional to
cost across turns so that rate can exist.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from . import claude_status, codex_status

#: ``spawn.allowance_tokens`` config default (design-the-allowance.md §2)
#: when a ``spawn:`` directive names no ``allowance:`` of its own.
DEFAULT_ALLOWANCE_TOKENS = 20_000_000

#: ``resident.allowance_tokens`` config default (design-the-allowance.md §2,
#: slice 2) for the seat's own standing allowance. Same magnitude as the
#: strand default above — until real usage data justifies a different
#: number, inventing a second constant would be a guess dressed as a
#: measurement (the 2026-09-05 review's objection to a borrowed exchange
#: rate applies just as well to a borrowed *ceiling*). A config override
#: exists precisely so an operator, not this module, can size the seat's
#: window once evidence exists.
DEFAULT_RESIDENT_ALLOWANCE_TOKENS = DEFAULT_ALLOWANCE_TOKENS

#: Cost weights per token class, in fresh-input-token equivalents — the
#: providers' own price ratios (Anthropic: cache read 0.1x, cache write 1.25x,
#: output 5x; OpenAI: cached input 0.1x, output ~8x — the same table is used
#: for both, slice 3's learned rate absorbs the residual per-provider scale).
TOKEN_WEIGHTS: dict[str, float] = {
    "input": 1.0,
    "output": 5.0,
    "cache_read": 0.1,
    "cache_creation": 1.25,
}


def weighted_tokens(
    *, input: "int | float" = 0, output: "int | float" = 0,
    cache_read: "int | float" = 0, cache_creation: "int | float" = 0,
) -> int:
    """Fold one usage record into cost-weighted tokens (:data:`TOKEN_WEIGHTS`)."""
    w = TOKEN_WEIGHTS
    return int(round(
        float(input) * w["input"] + float(output) * w["output"]
        + float(cache_read) * w["cache_read"]
        + float(cache_creation) * w["cache_creation"]
    ))

_TOKENS_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([km]?)\s*$", re.IGNORECASE)
_SUFFIX_MULTIPLIER = {"": 1, "k": 1_000, "m": 1_000_000}


def parse_tokens(raw: Any) -> int | None:
    """Parse an unsigned token count: a plain integer, or ``k``/``m``-suffixed
    (``120k`` -> 120000, ``1.2m`` -> 1200000).

    ``None`` on anything that doesn't parse, or a non-positive count — never
    raises, and never guesses a default; the caller decides what "unset"
    means.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    match = _TOKENS_RE.match(text)
    if not match:
        return None
    number, suffix = match.group(1), match.group(2).lower()
    try:
        value = float(number) * _SUFFIX_MULTIPLIER[suffix]
    except ValueError:
        return None
    tokens = int(round(value))
    return tokens if tokens > 0 else None


def parse_signed_tokens(raw: Any) -> int | None:
    """Parse a *signed* token delta (``+50k``, ``-10000``) for a grant/ask.

    Unlike :func:`parse_tokens`, the magnitude may be any positive number —
    the sign is what this adds; zero is refused (nothing to grant/ask for).
    """
    text = str(raw or "").strip()
    if not text:
        return None
    sign = 1
    if text[0] in "+-":
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    magnitude = parse_tokens(text)
    if magnitude is None:
        return None
    return sign * magnitude


def format_tokens(n: "int | float | None") -> str:
    """Render a token count the way the bar/prose want it: ``38k``, ``1.2m``."""
    if n is None:
        return "?"
    n = int(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        value = n / 1_000_000
        return sign + f"{value:.1f}".rstrip("0").rstrip(".") + "m"
    if n >= 1_000:
        value = n / 1_000
        return sign + f"{value:.1f}".rstrip("0").rstrip(".") + "k"
    return sign + str(n)


def spend_pct(
    spent: "int | float | None", tokens: "int | float | None"
) -> "float | None":
    """``100 * spent / tokens``, or ``None`` when either side is unknown/zero."""
    if spent is None or not tokens:
        return None
    return round(100.0 * float(spent) / float(tokens), 1)


def _camel_or_snake(data: dict[str, Any], camel: str, snake: str) -> Any:
    return data.get(camel) if camel in data else data.get(snake)


def claude_transcript_tokens(path: "Path | str | None") -> "int | None":
    """Sum every assistant turn's usage in a Claude session transcript,
    cost-weighted (:func:`weighted_tokens`).

    ``None`` when *path* is falsy, unreadable, or carries no assistant
    ``usage`` row at all — "no reading yet", never a fabricated zero.
    """
    if not path:
        return None
    total = 0
    found = False
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict) or row.get("type") != "assistant":
                    continue
                message = row.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                parts: dict[str, float] = {}
                for name, camel, snake in (
                    ("input", "inputTokens", "input_tokens"),
                    ("output", "outputTokens", "output_tokens"),
                    ("cache_read", "cacheReadInputTokens", "cache_read_input_tokens"),
                    ("cache_creation", "cacheCreationInputTokens",
                     "cache_creation_input_tokens"),
                ):
                    value = _camel_or_snake(usage, camel, snake)
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    parts[name] = float(value)
                    found = True
                total += weighted_tokens(**parts)
    except OSError:
        return None
    return total if found else None


def claude_first_turn_boot_tokens(path: "Path | str | None") -> "int | None":
    """The first assistant turn's own boot cost, cost-weighted.

    "Boot cost" (design-the-seat-that-never-quits.md §"The measurement" —
    brnrd#1810's slice 3 needs a number to compare a held seat's per-
    boundary cost against) is what it took to *establish* context — the
    wake bundle, the system prompt, the dominion files — before any work
    happened: the first turn's own ``cache_creation`` + fresh ``input``
    tokens, deliberately excluding ``output`` (that turn's own reply is
    work, not setup) and ``cache_read`` (there is nothing to re-read yet on
    turn one). Same weights as :func:`weighted_tokens`, same transcript
    format as :func:`claude_transcript_tokens` — this just stops at the
    first matching row instead of summing every one.

    ``None`` when *path* is falsy, unreadable, or the transcript's first
    assistant row carries neither field — "no reading yet", never a
    fabricated zero.
    """
    if not path:
        return None
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict) or row.get("type") != "assistant":
                    continue
                message = row.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                input_tokens = _camel_or_snake(usage, "inputTokens", "input_tokens")
                cache_creation = _camel_or_snake(
                    usage, "cacheCreationInputTokens", "cache_creation_input_tokens"
                )
                parts: dict[str, float] = {}
                if isinstance(input_tokens, (int, float)) and not isinstance(
                    input_tokens, bool
                ):
                    parts["input"] = float(input_tokens)
                if isinstance(cache_creation, (int, float)) and not isinstance(
                    cache_creation, bool
                ):
                    parts["cache_creation"] = float(cache_creation)
                if not parts:
                    return None
                return weighted_tokens(**parts)
    except OSError:
        return None
    return None


def claude_last_turn_context_tokens(path: "Path | str | None") -> "int | None":
    """The transcript's most recent assistant turn's own context footprint.

    Raw ``input + cache_read + cache_creation`` tokens — unweighted, unlike
    :func:`claude_transcript_tokens`/:func:`claude_first_turn_boot_tokens`:
    this is an occupancy reading (design-the-seat-that-never-quits.md
    §slice 4, brnrd#1810's context-drift park), not a cost estimate, so the
    provider's cache-discount price ratios (:data:`TOKEN_WEIGHTS`) do not
    apply — a cached token still occupies a token's worth of window.
    ``output_tokens`` is excluded deliberately, same reasoning as the boot
    reading: a reply's own tokens don't occupy the *input* side of the
    window the next turn pays for.

    Mirrors :func:`brr.claude_status._instantaneous_context_used_percent`'s
    numerator (the last assistant row's usage — "instantaneous", not the
    cumulative-per-*resumed*-session total ``modelUsage`` carries, #1178),
    computed the same file-path-first way :func:`claude_first_turn_boot_tokens`
    already reads a growing transcript before any final envelope or
    ``session_id`` exists — the last matching row wins instead of the first.

    ``None`` when *path* is falsy, unreadable, or the transcript carries no
    assistant ``usage`` row at all — "no reading yet", never a fabricated
    zero.
    """
    if not path:
        return None
    last_total: int | None = None
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict) or row.get("type") != "assistant":
                    continue
                message = row.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                total = 0.0
                found = False
                for camel, snake in (
                    ("inputTokens", "input_tokens"),
                    ("cacheReadInputTokens", "cache_read_input_tokens"),
                    ("cacheCreationInputTokens", "cache_creation_input_tokens"),
                ):
                    value = _camel_or_snake(usage, camel, snake)
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    total += float(value)
                    found = True
                if found:
                    last_total = int(round(total))
    except OSError:
        return None
    return last_total


def latest_claude_transcript(
    cwd: "str | Path | None", projects_root: "str | Path | None" = None,
) -> "Path | None":
    """The newest session transcript under *cwd*'s own projects slug.

    Mirrors :func:`brr.transcript.claude_session_path`'s slugging. Safe for a
    strand's own cwd (see the module docstring) — never call this against a
    shared/resident cwd multiple runs share, for the same reason
    :func:`brr.codex_status._latest_rollout_fallback` names for codex's
    shared sessions root.
    """
    if not cwd:
        return None
    root = (
        Path(projects_root) if projects_root else Path.home() / ".claude" / "projects"
    )
    slug = str(cwd).rstrip("/").replace("/", "-").replace(".", "-")
    directory = root / slug
    try:
        if not directory.is_dir():
            return None
        candidates = list(directory.glob("*.jsonl"))
    except OSError:
        return None
    if not candidates:
        return None
    newest: Path | None = None
    newest_mtime = -1.0
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = candidate, mtime
    return newest


def collect_spent(
    runner_name: "str | None",
    work_dir: "str | Path | None",
    *,
    codex_thread_id: "str | None" = None,
) -> "int | None":
    """A strand's own live cumulative token spend, per-Shell (step zero).

    ``None`` when the Shell has no reader wired, or the reader found nothing
    yet (a run's very first boundary, before either Shell has written
    anything to read) — never a fabricated zero.
    """
    if codex_status.supported(runner_name):
        return codex_status.total_tokens_used(thread_id=codex_thread_id)
    if claude_status.supported(runner_name):
        return claude_transcript_tokens(latest_claude_transcript(work_dir))
    return None


#: The boundary directive's fixed wording (design-the-allowance.md §2, step
#: 3): "never a kill, never a second nag until the number changes" — the
#: caller (`hooks.format_delta`) gates repetition; this only names the text.
def directive_line(spent: "int | None", tokens: "int | None") -> str:
    return (
        f"- allowance spent ({format_tokens(spent)}/{format_tokens(tokens)}) — "
        "park — `submit: true` then `brnrd await` — or ask: "
        "`ask: allowance +<tokens>` with one line why."
    )


def resident_ceiling_tokens(cfg: "Mapping[str, Any] | None") -> int:
    """The resident seat's own standing-allowance ceiling, config-first.

    Reads ``resident.allowance_tokens`` (same ``k``/``m`` parsing as a
    ``spawn:`` directive's ``allowance:``); unset or unparsable falls back
    to :data:`DEFAULT_RESIDENT_ALLOWANCE_TOKENS`. Deliberately a *separate*
    config key from the strand's ``spawn.allowance_tokens`` — the seat's
    window is a continuous conversation, not one dispatched thought, and an
    operator may need to size them apart — but the same parser and the same
    magnitude until its own evidence says otherwise.
    """
    cfg = cfg or {}
    raw = cfg.get("resident.allowance_tokens")
    tokens = parse_tokens(raw) if raw is not None else None
    return tokens if tokens is not None else DEFAULT_RESIDENT_ALLOWANCE_TOKENS


def resident_window_key(reset_epoch: "float | int | None") -> str | None:
    """A stable identity for the binding quota window, from its reset instant.

    While a window is open, the provider's own stated reset instant does not
    move; once it passes, the next reading names a new, later instant — so
    the reset epoch itself is a window's identity, cheaply, with no clock of
    our own to keep in sync. ``None`` when no reset reading exists this
    heartbeat (an absent quota facet, or a Shell with no reset field) — the
    caller must read that as "unknown," never as "the window rolled."
    """
    if reset_epoch is None:
        return None
    try:
        return str(int(float(reset_epoch)))
    except (TypeError, ValueError):
        return None


def resident_allowance_state(
    meta: "dict[str, Any]",
    *,
    cfg: "Mapping[str, Any] | None",
    reset_epoch: "float | int | None",
    live_spent: "int | None",
) -> "dict[str, object]":
    """The resident seat's own standing allowance for this heartbeat.

    Mirrors the strand bookkeeping in ``daemon._collect_allowance_facet``
    (write the freshly-metered numbers back onto *meta* so the boundary
    directive and cut-time checks read a recent value without re-metering),
    but the ceiling is **config-owned** (:func:`resident_ceiling_tokens`),
    never a quota-percent conversion: design-the-allowance.md's 2026-09-05
    review and design-the-continuous-seat.md's "Boundaries" section both
    reject presenting a token budget derived from a guessed quota rate
    before that rate has its own evidence (slice 3's job, not this one's).

    What the reset clock *does* decide is the **window**: this-window spend
    is the live cumulative meter reading minus a baseline captured when the
    window was first seen (at dispatch, or the first heartbeat that can see
    a quota reading), so a continuous seat's multi-day transcript is not
    charged for tokens it spent in a prior window. A heartbeat with no known
    reset (*reset_epoch* is ``None`` — no quota reading yet) keeps whatever
    window/baseline is already stamped rather than rolling on a merely
    missing reading.

    The ceiling is always derivable from config alone, so it is reported
    even on a heartbeat with no meter reading yet (*live_spent* is
    ``None`` — the very first boundary before the Shell has written
    anything readable, or a Shell with no collector wired): ``{"tokens":
    N, "spent": None}``, never a fabricated zero spend. This mirrors the
    strand branch in ``daemon._collect_allowance_facet``, and lets
    :func:`brr.facets.build` render the honest ``absent`` state (a
    collector *is* wired, it just has nothing yet) rather than
    ``unimplemented``.
    """
    tokens = resident_ceiling_tokens(cfg)
    if live_spent is None:
        meta["resident_allowance_tokens"] = tokens
        meta["resident_allowance_spent"] = None
        return {"tokens": tokens, "spent": None, "scope": "resident"}
    window_key = resident_window_key(reset_epoch)
    stored_window = meta.get("resident_allowance_window")
    stored_baseline = meta.get("resident_allowance_baseline")
    if stored_baseline is None or (
        window_key is not None and window_key != stored_window
    ):
        baseline = int(live_spent)
        meta["resident_allowance_window"] = window_key
        meta["resident_allowance_baseline"] = baseline
    else:
        baseline = int(stored_baseline)
    spent = max(0, int(live_spent) - baseline)
    meta["resident_allowance_tokens"] = tokens
    meta["resident_allowance_spent"] = spent
    return {"tokens": tokens, "spent": spent, "scope": "resident"}
