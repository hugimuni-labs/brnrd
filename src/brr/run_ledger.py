"""Append-only closed-run cost ledger.

The ledger is deliberately local-first: every closed daemon run appends one
JSON object to ``.brr/run-ledger.jsonl``.  Server mirroring and rollup queries
can project this later; the first invariant is that closeout never loses the
raw per-run row.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from . import allowance
from . import claude_status
from . import claude_usage
from . import codex_status
from . import gitops
from . import relics
from . import runner_select
from .run import Run

LEDGER_NAME = "run-ledger.jsonl"
RUN_NAME_CONTROL_NAME = ".name"
_RUN_NAME_MAX_BYTES = 240
_RUN_NAME_MAX_CHARS = 60
RUN_MOOD_CONTROL_NAME = ".mood"
# First line is the emote handle (`emotes.py` caps handles well under this);
# anything after is free narration the wire never carries. Byte cap mirrors
# hooks._MOOD_READ_CAP_CHARS so the two readers of the same file agree on
# how much of it is ever trusted.
_RUN_MOOD_MAX_BYTES = 500
_RUN_MOOD_MAX_CHARS = 64
#: The resident's own topic claim (the-run-that-claims-its-thread): first
#: line, or a `topics:`-prefixed line — same lenient-parse tolerance as
#: every other control file. Whitespace/`·`-separated slugs, same separator
#: convention `items._split_ids` already uses for an item's own `topics:`
#: row and `taken:` row.
RUN_TOPICS_CONTROL_NAME = ".topics"
#: Move 5c (design-the-loom §21): the run's *one* topic, set at boot beside
#: `.card` and `.mood` — an existing heddle's slug, `new <slug>`, or `null`.
#: Read by :mod:`brr.run_topic`; `.topics` above stays the 5b claim.
RUN_TOPIC_CONTROL_NAME = ".topic"
_RUN_TOPICS_MAX_BYTES = 2000
_RUN_TOPICS_MAX_SLUGS = 32
_TOPIC_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_BEFORE_WEEKLY_KEY = "run_ledger_weekly_used_before"
_BEFORE_FIVE_HOUR_KEY = "run_ledger_five_hour_used_before"
_BASELINE_RUNNER_KEY = "run_ledger_baseline_runner"
_LAST_LEVELS_KEY = "run_ledger_last_levels"
_CLAUDE_TRANSCRIPTS_KEY = "run_ledger_claude_transcripts"

_ROW_FIELDS = (
    "run_id",
    "event_id",
    "started_at",
    "ended_at",
    "wall_clock_seconds",
    "runner_shell",
    "runner_core",
    "core_expected",
    "core_mismatch",
    "substitution_reason",
    "runner_substituted_from",
    "runner_substitutions",
    "repo_label",
    "source_system",
    "external_refs",
    "reply_archive",
    "terminal_route",
    "name",
    "parent_run_id",
    "is_subspawn",
    "tokens_input",
    "tokens_output",
    "tokens_cache_read",
    "tokens_cache_creation",
    "context_window_used",
    "weekly_pct_delta",
    "five_hour_pct_delta",
    "usd_subscription_attributed",
    "usd_credits_equivalent",
    # design-the-bolt.md, fork 4 (signed): the one success signal going
    # forward — "accepted" / "annotated" / absent. `terminal_route`'s
    # five-value zoo demotes to debug detail as a follow-up, not this diff.
    "bolt",
    # The bounded resident declaration plus daemon dissent.  ``produce`` is
    # deliberately represented once, by ``external_refs`` above.
    "bolt_declaration",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def mark_run_started(
    task: Run,
    runner_name: str | None,
    outbox_dir: Path | None,
    work_dir: Path | None,
) -> None:
    """Record start time and the pre-run quota snapshot on *task*."""
    task.meta.setdefault("started_at", now_iso())
    if task.meta.get(_BASELINE_RUNNER_KEY) == runner_name:
        return
    task.meta[_BASELINE_RUNNER_KEY] = runner_name or ""
    task.meta.pop(_LAST_LEVELS_KEY, None)
    task.meta.pop(_CLAUDE_TRANSCRIPTS_KEY, None)
    levels = load_quota_levels(
        runner_name,
        outbox_dir,
        work_dir,
        force_claude_refresh=False,
    )
    weekly, five_hour = quota_used_percentages(levels)
    if weekly is not None:
        task.meta[_BEFORE_WEEKLY_KEY] = weekly
    else:
        task.meta.pop(_BEFORE_WEEKLY_KEY, None)
    if five_hour is not None:
        task.meta[_BEFORE_FIVE_HOUR_KEY] = five_hour
    else:
        task.meta.pop(_BEFORE_FIVE_HOUR_KEY, None)


def record_boundary_levels(
    task: Run, levels: Mapping[str, Any] | None, *, work_dir: Path | None = None,
) -> None:
    """Keep the latest observed quota/token reading for closeout.

    A held or awaited seat can end before its Shell emits a final envelope.
    The heartbeat already read this data; retain its compact, JSON-shaped
    snapshot on the run so closeout can use it rather than turning a known
    token reading into a null merely because the final read is absent.
    """
    # A released Claude process never emits its final result envelope. Pin
    # its transcript while the execution root still belongs to this run;
    # closeout must not hunt for the newest session in a shared cwd later.
    if (
        claude_status.supported(task.meta.get("runner_name"))
        and work_dir is not None
    ):
        started = _parse_iso(task.meta.get("started_at"))
        if started is not None:
            path = allowance.latest_claude_transcript(
                work_dir, not_before=started.timestamp(),
            )
            if path is not None:
                paths = task.meta.setdefault(_CLAUDE_TRANSCRIPTS_KEY, [])
                if str(path) not in paths:
                    paths.append(str(path))
    if not isinstance(levels, Mapping):
        return
    snapshot = _level_snapshot(levels)
    if snapshot:
        task.meta[_LAST_LEVELS_KEY] = _prefer_last_boundary_levels(
            snapshot, task.meta.get(_LAST_LEVELS_KEY),
        )


def append_closed_run(
    repo_root: Path,
    task: Run,
    cfg: Mapping[str, Any] | None = None,
    *,
    outbox_dir: Path | None = None,
    work_dir: Path | None = None,
) -> Path:
    """Append one closed-run row and return the ledger path.

    This function is intentionally best-effort about source data: unavailable
    quota or token sources become ``null`` fields, not closeout failures.
    """
    row = build_closed_run_row(
        task,
        cfg or {},
        outbox_dir=outbox_dir,
        work_dir=work_dir,
    )
    path = ledger_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def build_closed_run_row(
    task: Run,
    cfg: Mapping[str, Any] | None = None,
    *,
    outbox_dir: Path | None = None,
    work_dir: Path | None = None,
    after_levels: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or {}
    runner_name = _str_or_none(task.meta.get("runner_name"))
    runner_shell = _str_or_none(task.meta.get("runner_shell")) or _runner_shell(
        runner_name
    )
    ended_at = _str_or_none(task.meta.get("ended_at")) or now_iso()
    task.meta["ended_at"] = ended_at

    if after_levels is None:
        # Session meters follow the standing root; produce below retains
        # work_dir as its project anchor.
        execution_root = task.meta.get("execution_root")
        after_levels = load_quota_levels(
            runner_name,
            outbox_dir,
            Path(execution_root) if execution_root else work_dir,
            force_claude_refresh=True,
        )
    after_levels = _prefer_last_boundary_levels(
        after_levels, task.meta.get(_LAST_LEVELS_KEY)
    )

    # Prefer the model id(s) actually observed in this run's own result JSON
    # (`modelUsage.keys()`) over the static runner-catalog placeholder
    # ("default") an unpinned Claude profile resolves to at dispatch time —
    # the real id is only knowable once the run has produced output (#255).
    # Written back onto the task manifest too, so every later consumer of
    # ``runner_core`` (the run-state doc, a future wake's Mode block) sees
    # the same resolved value rather than diverging from the ledger row.
    #
    # Core attestation (follow-up to the shell=/core= shadowing bug fixed
    # 2026-07-09, runner.py::_warn_if_shell_shadows_core): before observed
    # overwrites expected, compare the two. `core_expected` is what the
    # config/catalog *claimed* at dispatch; `runner_core` becomes what the
    # Shell *actually ran*; `core_mismatch` is the alarm bit when the claim
    # and the observation disagree — the reliable "did this run respect the
    # pinned core" signal, so a shadowed/misrouted config can never again go
    # silent for days.
    expected_core = (
        _str_or_none(task.meta.get("core_requested"))
        or _str_or_none(task.meta.get("runner_core"))
    )
    resolved_core = (
        _str_or_none(task.meta.get("core_observed"))
        or claude_status.resolved_model_id(after_levels)
    )
    if resolved_core:
        task.meta["core_observed"] = resolved_core
        task.meta["runner_core"] = resolved_core
    mismatch = core_mismatch(expected_core, resolved_core)
    if mismatch:
        print(
            f"[brnrd:run-ledger] WARNING: run {task.id} was dispatched with "
            f"core={expected_core!r} but the Shell observed "
            f"{resolved_core!r} — the configured core pin was not respected.",
            file=sys.stderr,
        )

    try:
        substitutions = [
            dict(entry) for entry in (task.meta.get("runner_substitutions") or [])
            if isinstance(entry, dict)
        ]
    except Exception:
        substitutions = []

    after_weekly, after_five_hour = quota_used_percentages(after_levels)
    before_weekly = _num(task.meta.get(_BEFORE_WEEKLY_KEY))
    before_five_hour = _num(task.meta.get(_BEFORE_FIVE_HOUR_KEY))
    weekly_delta = _delta(after_weekly, before_weekly)
    five_hour_delta = _delta(after_five_hour, before_five_hour)

    subscription_price = subscription_price_for_shell(
        cfg,
        runner_shell=runner_shell,
        runner_name=runner_name,
    )
    usd_subscription = (
        round((subscription_price / 100.0) * weekly_delta, 6)
        if subscription_price is not None and weekly_delta is not None
        else None
    )

    tokens = token_fields(after_levels)
    if claude_status.supported(runner_name) and any(
        tokens[key] is None for key in (
            "tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation"
        )
    ):
        recovered = token_fields({"tokens": _claude_transcript_tokens(task)})
        tokens = {
            key: value if value is not None else recovered[key]
            for key, value in tokens.items()
        }
    started_at = _str_or_none(task.meta.get("started_at"))
    # Run relics (#200/#317, kb/design-run-relics.md): commits/branch/PR are
    # auto-derived from git + the ``.pr`` control file, captured kb pages and
    # the terminal reply are appended during knowledge closeout, and only
    # issue/comment/message/summary context depends on resident reporting.
    # Falls back to the pre-existing (always-empty in practice, since
    # nothing ever wrote it) ``task.meta["external_refs"]`` path so a task
    # that somehow pre-populated it directly doesn't regress.
    # Scope resolution handles the host-run case: no assigned branch, so the
    # commits are measured from the checkout's run-start HEAD instead
    # (relics.collection_scope) — otherwise a host run that merged its work
    # into the seed branch books an empty manifest. That fallback scope is
    # the shared checkout every concurrent run measures from, so it also
    # needs a run-identity filter (#575) — a worktree run's own isolated
    # branch (``branch_name`` set) needs none, since no sibling can land a
    # commit there.
    #
    # ``work_dir`` here is the *repo_root* anchor, not necessarily the tree
    # to read: an isolated run (strand worktree or ``create_clone``) has its
    # own tree, and reading *repo_root*'s live branch instead silently
    # substitutes whatever the shared checkout happens to be on (#1776).
    # relics.scope_roots resolves the pair this closeout actually needs —
    # the run's own tree while it's still there, *repo_root* once
    # ``WorktreeEnv.finalize`` has torn it down (a clone's branch is landed
    # into *repo_root* as a local ref before that happens, so reading it
    # there afterward is still faithful; only the *live* branch probe,
    # inside collection_scope, must never fall back to *repo_root*).
    probe_root, collect_root = relics.scope_roots(task.meta, work_dir)
    relic_branch, relic_seed = relics.collection_scope(task.meta, probe_root)
    commit_run_id = task.id if not task.meta.get("branch_name") else None
    collected_relics = relics.collect(
        collect_root,
        branch=relic_branch,
        seed_ref=relic_seed,
        outbox_dir=outbox_dir,
        commit_run_id=commit_run_id,
        # #1788: only meaningful for an isolated run's own tree — a host
        # run already measures from host_start_oid (collection_scope's
        # branchless fallback above), and the plan's recorded seed_oid can
        # predate that baseline. `commit_run_id is None` is exactly "not a
        # host run" (mirrors the identity-filter guard just above).
        seed_oid=(
            relics.seed_oid_of(task.meta) if commit_run_id is None else None
        ),
    )
    row = {
        "run_id": task.id,
        "event_id": task.event_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_clock_seconds": wall_clock_seconds(started_at, ended_at),
        "runner_shell": runner_shell,
        "runner_core": _str_or_none(task.meta.get("runner_core")),
        "core_expected": expected_core,
        "core_mismatch": mismatch,
        # *Why* a substitution happened, when the envelope carried a signal
        # (fallback/refusal/iterations). ``None`` when clean or unobservable —
        # the reason rides next to the ``core_mismatch`` alarm bit (#substitution).
        "substitution_reason": (
            claude_status.substitution_reason(after_levels)
            or _dispatch_substitution_reason(substitutions)
        ),
        # #1929: the two columns that make a failover countable. Before them,
        # a run that changed Shell mid-flight was indistinguishable from one
        # that never did — `core_expected` had been rewritten to the
        # substitute by `_record_task_runner`, so the field designed to catch
        # a swap recorded agreement with itself, and the envelope-derived
        # `substitution_reason` above is `None` whenever the substitute is not
        # a Claude Shell (which is the common case: a fallback crosses
        # failure domains by construction).
        "runner_substituted_from": (
            _str_or_none(substitutions[0].get("from")) if substitutions else None
        ),
        "runner_substitutions": substitutions or None,
        "repo_label": _str_or_none(task.meta.get("repo_label")),
        "source_system": _source_system(task),
        "external_refs": collected_relics or external_refs(task.meta.get("external_refs")),
        "reply_archive": _str_or_none(task.meta.get("reply_archive")),
        # #743: which channel carried the run's terminal stream — the column
        # that makes "how often does the static dispatch catch something a
        # run would otherwise have lost?" a query instead of a directory
        # walk. ``None`` when the run produced no terminal body at all.
        "terminal_route": _str_or_none(task.meta.get("terminal_route")),
        "name": read_run_name_control(outbox_dir) or "",
        "parent_run_id": _str_or_none(task.meta.get("spawn_parent_run_id")),
        "is_subspawn": bool(task.meta.get("spawn_immediate")),
        "tokens_input": tokens["tokens_input"],
        "tokens_output": tokens["tokens_output"],
        "tokens_cache_read": tokens["tokens_cache_read"],
        "tokens_cache_creation": tokens["tokens_cache_creation"],
        "context_window_used": tokens["context_window_used"],
        "weekly_pct_delta": weekly_delta,
        "five_hour_pct_delta": five_hour_delta,
        "usd_subscription_attributed": usd_subscription,
        "usd_credits_equivalent": usd_credits_equivalent(after_levels),
        "bolt": _bolt_value(task.meta.get("bolt")),
        "bolt_declaration": bolt_declaration_value(task.meta.get("bolt")),
    }
    return {field: row.get(field) for field in _ROW_FIELDS}


def _bolt_value(bolt_meta: Any) -> str | None:
    """``"accepted"`` / ``"annotated"`` / ``None`` — the ledger's bolt column.

    ``None`` when this run was never cut (``task.meta["bolt"]`` unset — a
    run that ended without ever declaring completion, whether by closing
    another way or dying mid-flight), not a fabricated default.
    """
    if not isinstance(bolt_meta, dict):
        return None
    return "annotated" if bolt_meta.get("annotated") else "accepted"


def bolt_declaration_value(bolt_meta: Any) -> dict[str, Any] | None:
    """The durable cut declaration, absent on pre-declaration bolt metadata.

    Older rows and frames carry only ``accepted_at`` / ``annotated`` (and
    sometimes ``spend_declared``).  Do not manufacture empty asks or owed
    rows for those runs: absence means the writer did not retain them.
    """
    if not isinstance(bolt_meta, dict):
        return None
    # Pre-change metadata retained ``spend_declared`` alone.  The explicit
    # marker prevents that one surviving field from masquerading as a full
    # declaration with invented empty siblings.
    if bolt_meta.get("declaration_version") != 1:
        return None
    if bolt_meta.get("omitted") is True:
        return {
            "omitted": True,
            "reason": str(bolt_meta.get("reason") or "persistence limits exceeded"),
        }
    fields = ("asks", "owed", "decisions", "spend_declared", "next", "dissent", "strands")
    return {field: bolt_meta.get(field) for field in fields}


def _dispatch_substitution_reason(substitutions: list[dict]) -> str | None:
    """The reason a *dispatch* substituted, when the envelope has none.

    ``claude_status.substitution_reason`` reads the Claude result envelope and
    is the right answer when a Claude Shell silently served a different model.
    It cannot answer the other case at all: brr itself swapping Shells after
    an operational failure, where the substitute is usually *not* Claude and
    writes no such envelope. Same column, two sources, envelope first — it is
    the more specific observation, and it is about the attempt that actually
    produced the output.
    """
    if not substitutions:
        return None
    last = substitutions[-1]
    kind = str(last.get("failure_kind") or "").strip() or "operational failure"
    frm = str(last.get("from") or "?").strip()
    to = str(last.get("to") or "?").strip()
    return f"dispatch fallback: {frm} -> {to} after {kind}"


def core_mismatch(expected: str | None, observed: str | None) -> bool | None:
    """Did the Shell actually run the core the config pinned?

    Returns ``None`` (unverifiable) when there is nothing to compare:
    no observation (non-Claude Shells produce no ``modelUsage``, or the run
    died before result JSON), or an unpinned dispatch (``"default"`` means
    "whatever the Shell chooses" — anything observed is by definition
    respected). Returns ``False`` when at least one observed id matches the
    pin — subagents legitimately resolve to other tiers (Explore on haiku
    under a fable parent), so the joined ``a+b`` observation only alarms
    when *none* of its ids match. Matching is prefix-tolerant in both
    directions so a date-suffixed concrete id (``claude-haiku-4-5-20251001``)
    matches its shorter catalog pin and vice versa.
    """
    return runner_select.core_mismatch(expected, observed)


def ledger_path(repo_root: Path) -> Path:
    return gitops.shared_brr_dir(repo_root) / LEDGER_NAME


def load_quota_levels(
    runner_name: str | None,
    outbox_dir: Path | None,
    work_dir: Path | None,
    *,
    force_claude_refresh: bool,
) -> dict[str, Any] | None:
    """Read the current Shell quota levels without raising."""
    try:
        if codex_status.supported(runner_name):
            return codex_status.load_levels()
        if claude_status.supported(runner_name):
            usage = claude_usage.load_or_refresh_snapshot(
                outbox_dir,
                cwd=work_dir,
                max_age_seconds=0.0 if force_claude_refresh else None,
            )
            result = claude_status.load_snapshot(outbox_dir)
            return _merge_levels(usage, result)
    except Exception:
        return None
    return None


def quota_used_percentages(
    levels: Mapping[str, Any] | None,
) -> tuple[float | None, float | None]:
    """Return ``(weekly_used, five_hour_used)`` from a levels snapshot."""
    if not isinstance(levels, Mapping):
        return None, None
    weekly = _num(levels.get("week_used_percentage"))
    five_hour = _num(levels.get("session_used_percentage"))
    quota = levels.get("quota")
    if isinstance(quota, Mapping):
        weekly = weekly if weekly is not None else _num(quota.get("secondary_used_percent"))
        five_hour = five_hour if five_hour is not None else _num(
            quota.get("primary_used_percent")
        )
        # Claude's carried /usage readings and heartbeat snapshots retain
        # named buckets, even when the top-level used percentages are absent.
        buckets = quota.get("buckets")
        if isinstance(buckets, Mapping):
            def used(name: str) -> float | None:
                bucket = buckets.get(name)
                remaining = _num(bucket.get("remaining_percentage")) if isinstance(
                    bucket, Mapping
                ) else None
                return 100.0 - remaining if remaining is not None else None

            weekly = weekly if weekly is not None else used("week")
            five_hour = five_hour if five_hour is not None else used("session")
    return weekly, five_hour


def _claude_transcript_tokens(task: Run) -> dict[str, int]:
    """Recover measured usage when release/stop prevented a result envelope.

    Only an attested session id or a path pinned during this run may be read.
    Resumed transcripts can predate the run; timestamp bounds exclude that
    history. Streaming writes can repeat a message id; its last usage wins.
    """
    session_id = task.meta.get("claude_session_id")
    paths = [Path(path) for path in task.meta.get(_CLAUDE_TRANSCRIPTS_KEY, [])]
    if session_id:
        path = claude_status.session_transcript_path(session_id)
        if path is not None and path not in paths:
            paths.append(path)
    started = _parse_iso(task.meta.get("started_at"))
    ended = _parse_iso(task.meta.get("ended_at"))
    # Ledger stamps have second precision; include usage in that final second.
    if ended is not None and ended.microsecond == 0:
        ended += timedelta(seconds=1)
    if not paths or started is None:
        return {}
    messages: dict[str, dict[str, int]] = {}
    for path in paths:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle):
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(row, dict) or row.get("type") != "assistant":
                        continue
                    stamp = _parse_iso(row.get("timestamp"))
                    if stamp is None or stamp < started or (ended and stamp >= ended):
                        continue
                    message = row.get("message")
                    if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                        continue
                    usage = message["usage"]
                    measured = {}
                    for snake, camel in (
                        ("input_tokens", "inputTokens"),
                        ("output_tokens", "outputTokens"),
                        ("cache_read_input_tokens", "cacheReadInputTokens"),
                        ("cache_creation_input_tokens", "cacheCreationInputTokens"),
                    ):
                        value = _int_or_none(usage.get(camel, usage.get(snake)))
                        if value is not None:
                            measured[snake] = value
                    if measured:
                        key = str(message.get("id") or f"{path}:{index}")
                        messages.setdefault(key, {}).update(measured)
        except OSError:
            continue
    totals: dict[str, int] = {}
    for usage in messages.values():
        for key, value in usage.items():
            totals[key] = totals.get(key, 0) + value
    return totals


def _level_snapshot(levels: Mapping[str, Any]) -> dict[str, Any]:
    """Retain only the measured parts closeout consumes, never opaque payloads."""
    snapshot: dict[str, Any] = {}
    for key in ("quota", "tokens", "updated_at", "model_ids", "spend"):
        value = levels.get(key)
        if isinstance(value, Mapping):
            snapshot[key] = dict(value)
        elif isinstance(value, (str, int, float, list)) and not isinstance(value, bool):
            snapshot[key] = value
    return snapshot


def _prefer_last_boundary_levels(
    current: Mapping[str, Any] | None,
    last: Any,
) -> dict[str, Any] | None:
    """Use a boundary reading only to fill terminal gaps, never overwrite it."""
    if not isinstance(current, Mapping):
        return dict(last) if isinstance(last, Mapping) else None
    merged = dict(current)
    if not isinstance(last, Mapping):
        return merged
    for key in ("quota", "tokens", "spend"):
        now_value = merged.get(key)
        prior_value = last.get(key)
        if not isinstance(prior_value, Mapping):
            continue
        if not isinstance(now_value, Mapping):
            merged[key] = dict(prior_value)
            continue
        combined = dict(prior_value)
        combined.update({key: value for key, value in now_value.items() if value is not None})
        merged[key] = combined
    for key in ("updated_at", "model_ids"):
        if merged.get(key) is None and last.get(key) is not None:
            merged[key] = last[key]
    return merged


def token_fields(levels: Mapping[str, Any] | None) -> dict[str, int | float | None]:
    tokens = levels.get("tokens") if isinstance(levels, Mapping) else None
    if not isinstance(tokens, Mapping):
        tokens = {}
    return {
        "tokens_input": _int_or_none(tokens.get("input_tokens")),
        "tokens_output": _int_or_none(tokens.get("output_tokens")),
        "tokens_cache_read": _int_or_none(tokens.get("cache_read_input_tokens")),
        "tokens_cache_creation": _int_or_none(
            tokens.get("cache_creation_input_tokens")
        ),
        "context_window_used": _num(tokens.get("context_window_used_percent")),
    }


def subscription_price_for_shell(
    cfg: Mapping[str, Any],
    *,
    runner_shell: str | None,
    runner_name: str | None,
) -> float | None:
    """Configured monthly subscription price for a runner Shell/profile.

    Flat config keys are accepted in decreasing specificity:
    ``run_ledger.subscription_price.<runner_name>``,
    ``run_ledger.subscription_price.<runner_shell>``,
    ``runner.subscription_price.<runner_name>``,
    ``runner.subscription_price.<runner_shell>``,
    then the generic ``run_ledger.subscription_price``.
    """
    keys: list[str] = []
    for key in (runner_name, runner_shell):
        if key:
            keys.extend([
                f"run_ledger.subscription_price.{key}",
                f"runner.subscription_price.{key}",
            ])
    keys.append("run_ledger.subscription_price")
    for key in keys:
        value = _num(cfg.get(key))
        if value is not None:
            return value
    return None


def wall_clock_seconds(started_at: str | None, ended_at: str | None) -> float | None:
    start = _parse_iso(started_at)
    end = _parse_iso(ended_at)
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds()), 3)


def external_refs(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def read_run_name_control(outbox_dir: Path | None) -> str | None:
    """Read the resident-authored one-line run name, capped for dashboards."""
    if outbox_dir is None:
        return None
    try:
        raw = (outbox_dir / RUN_NAME_CONTROL_NAME).read_bytes()
    except OSError:
        return None
    lines = raw[:_RUN_NAME_MAX_BYTES].decode("utf-8", errors="replace").splitlines()
    value = lines[0].strip()[:_RUN_NAME_MAX_CHARS] if lines else ""
    return value or None


def read_run_mood_control(outbox_dir: Path | None) -> str | None:
    """Read the resident-authored mood handle: `.mood` first line, capped.

    The daemon-side sibling of ``hooks._read_mood`` (which serves the
    in-run statusline and must stay import-light in the hook path). Returns
    the raw handle without resolving it against ``emotes`` — resolution
    happens at the serving edge, where an unknown handle renders as nothing
    rather than a guessed face (the emote library's honesty bar).
    """
    if outbox_dir is None:
        return None
    try:
        raw = (outbox_dir / RUN_MOOD_CONTROL_NAME).read_bytes()
    except OSError:
        return None
    lines = raw[:_RUN_MOOD_MAX_BYTES].decode("utf-8", errors="replace").splitlines()
    value = lines[0].strip()[:_RUN_MOOD_MAX_CHARS] if lines else ""
    return value or None


def read_run_topics_control(outbox_dir: Path | None) -> list[str] | None:
    """Read the resident-claimed topic slugs from `.topics`, lenient.

    Accepts either shape on the first line: bare slugs (`the-loom the-post`)
    or a `topics:`-prefixed row (`topics: the-loom the-post`) — the same two
    forms the task spec asks for, whitespace/`·`-separated. Slug-shape junk
    is dropped rather than rejected (never a crash on garbage); a `.topics`
    that survives filtering to nothing returns ``None``, same as absent —
    a run's topic claim is either real slugs or no claim, never an empty
    list on the wire. Deliberately **not** validated against existing
    `surface/topics/` pages: a run may mint a topic (its own resident act),
    so an unknown slug is the drift audit's finding, not a capture error.
    """
    if outbox_dir is None:
        return None
    try:
        raw = (outbox_dir / RUN_TOPICS_CONTROL_NAME).read_bytes()
    except OSError:
        return None
    lines = raw[:_RUN_TOPICS_MAX_BYTES].decode("utf-8", errors="replace").splitlines()
    if not lines:
        return None
    first = lines[0].strip()
    if first.lower().startswith("topics:"):
        first = first[len("topics:"):].strip()
    tokens = [t for t in re.split(r"[\s·]+", first) if t]
    slugs = [t for t in tokens if _TOPIC_SLUG_RE.match(t)][:_RUN_TOPICS_MAX_SLUGS]
    return slugs or None


def usd_credits_equivalent(levels: Mapping[str, Any] | None) -> float | None:
    """Return a proven credit-equivalent USD value, or null.

    The first local ledger pass has no stable token-to-managed-credit mapping.
    Keep the field present but empty until the managed-compute pricing source is
    wired, instead of smuggling five-hour quota or subscription dollars into it.
    """
    return None


def _merge_levels(
    *snapshots: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    sources: list[str] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            continue
        source = snapshot.get("source")
        if isinstance(source, str) and source.strip():
            sources.append(source.strip())
        for key in (
            "quota",
            "spend",
            "context_window",
            "plan_type",
            "tokens",
            "model_ids",
            "fallback_signals",
            "session_used_percentage",
            "week_used_percentage",
        ):
            if key in snapshot:
                merged[key] = snapshot[key]
    if sources:
        merged["source"] = " + ".join(dict.fromkeys(sources))
    return merged or None


def _source_system(task: Run) -> str | None:
    return _str_or_none(task.meta.get("source_system")) or _str_or_none(task.source)


def _runner_shell(runner_name: str | None) -> str | None:
    if not runner_name:
        return None
    if runner_name.startswith("claude"):
        return "claude"
    if runner_name.startswith("codex"):
        return "codex"
    return runner_name


def _delta(after: Any, before: Any) -> float | None:
    """A run's quota draw, or ``None`` when the window reset underneath it.

    These columns are *used*-percentages, so the draw is ``after - before``.
    That subtraction is only meaningful while both readings belong to the same
    window: when a 5h or weekly window rolls over (or a reset credit is spent)
    mid-run, ``used`` drops back toward zero and the run books a large negative
    cost — it gets *credited* for spending. Live evidence at the time this
    guard was written: 5 of 181 weekly rows and 20 of 253 five-hour rows on
    this account were negative, reaching -77 and -83 respectively, and
    ``usd_subscription_attributed`` (derived from the weekly delta) inherited
    the sign.

    ``usage_samples.recent_burn`` already refuses to measure across a reset for
    exactly this reason; the ledger simply never learned the same lesson.
    A reset is unrecoverable here — the pre-reset portion of the run's spend
    went with the old window — so the honest row is a null, which every
    consumer already handles, rather than a negative that reads as real.
    """
    after_num = _num(after)
    before_num = _num(before)
    if after_num is None or before_num is None:
        return None
    if after_num < before_num:
        return None
    return round(after_num - before_num, 6)


def _parse_iso(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    number = _num(value)
    try:
        return int(number) if number is not None else None
    except (ValueError, OverflowError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None
