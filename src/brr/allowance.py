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
from typing import Any

from . import claude_status, codex_status

#: ``spawn.allowance_tokens`` config default (design-the-allowance.md §2)
#: when a ``spawn:`` directive names no ``allowance:`` of its own.
DEFAULT_ALLOWANCE_TOKENS = 20_000_000

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
