"""Dashboard publishing lanes extracted from brr.gates.cloud.

The relay gate owns the HTTP session and persisted cloud state. It supplies
those dependencies through PublisherContext, keeping this module acyclic while
the compatibility names in cloud remain monkeypatchable.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import functools
import hashlib
import json
import re
import shutil
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import (
    claude_status,
    claude_usage,
    codex_status,
    codex_usage,
    dominion,
    emotes,
    gitops,
    presence,
    protocol,
    run_ledger,
    run_progress,
    runner_quota,
    run_stop_request,
    schedule as schedule_mod,
    usage_samples,
    wake_request,
)
from ..gates.github.parse import parse_origin_url
from ..run import Run, list_runs, run_manifest_path
from . import runtime


@dataclass(frozen=True)
class PublisherContext:
    """Relay-owned dependencies whose compatibility targets stay in cloud."""

    request: Callable[..., dict]
    load_state: Callable[[Path], dict]
    corpus_resolve: Callable[[Path], Any]
    quota_snapshot: Callable[[Path], list[dict[str, Any]]]
    runners_snapshot: Callable[[Path], dict[str, Any]]
    pr_review_repo_labels: Callable[[Path], list[str]]


_context_factory: Callable[[], PublisherContext] | None = None


def configure_context(factory: Callable[[], PublisherContext]) -> None:
    global _context_factory
    _context_factory = factory


def _context() -> PublisherContext:
    if _context_factory is None:
        raise RuntimeError("cloud publisher context is not configured")
    return _context_factory()

_CLAUDE_QUOTA_PUBLISH_MAX_AGE_SECONDS = 240.0
_CODEX_QUOTA_PUBLISH_MAX_AGE_SECONDS = 120.0
_DASHBOARD_PUBLISH_INTERVAL_S = 3

# --- publish scopes: the one gate over every dashboard lane (#417) ---------
#
# ``publish.layers`` names *what may be mirrored to brnrd.dev*, and
# ``SECURITY.md`` promises a reader that it "opts the mirror down to fewer
# layers (or ``none``)". Until #417 that promise held for exactly one of the
# seven lanes: ``_publish_corpus`` read the config and the other six did not,
# so ``none`` left runners, live-runs, activity, quota, the PR review queue and
# the run ledger publishing exactly as before. The vocabulary below is what
# makes the sentence true — it names every lane, not only the corpus lane's own
# three slices.
#
# Resolution rules, all fail-closed:
#
# - **absent / empty** → everything. This is the shipped default and #417
#   deliberately does not change it; flipping the mirror to opt-in is a product
#   call, not a repair.
# - ``none`` → nothing at all, on every lane. It wins over any scope named
#   beside it: an off switch that another token can override is not an off
#   switch.
# - **any other token** → only what is named ships. A token that matches no
#   scope is a typo, and a typo must not read as "mirror everything" — it fails
#   closed and says so once on stderr, because a misconfiguration silent in both
#   directions is how this class of bug survives.
#
# Naming a corpus slice (``authored`` / ``knowledge`` / ``runs``) enables the
# corpus lane carrying just that slice; naming ``corpus`` enables all three.

# Mirrors ``account.CORPUS_LAYERS`` — the corpus lane's own slices, which are
# sub-scopes of one lane rather than lanes in their own right. Duplicated
# rather than imported because ``account`` is a deferred import everywhere else
# in this module; ``test_every_publisher_is_a_registered_gated_lane`` pins the
# two together.
_PUBLISH_CORPUS_SLICES = ("authored", "knowledge", "runs")

# Every publish lane, in the order the tick drives them. Order is load-bearing
# for the first two — see the comments in ``_dashboard_publish_tick``.
_PUBLISH_TICK_ORDER = (
    "runners",
    "live_runs",
    "activity",
    "corpus",
    "quota",
    "pr_review_queue",
    "run_ledger",
)

# lane name -> the gated publisher, populated by ``@_publish_lane``.
_PUBLISH_LANES: dict[str, Callable[..., None]] = {}

_PUBLISH_OFF = "none"

# Raw ``publish.layers`` values already warned about, so a standing typo costs
# one line at startup rather than one every ``_DASHBOARD_PUBLISH_INTERVAL_S``.
# A guard that fires constantly stops being read.
_publish_scope_warned: set[str] = set()


def _resolve_publish_scopes(cfg: dict) -> tuple[frozenset[str], frozenset[str]]:
    """Parse ``publish.layers`` into ``(enabled lanes, enabled corpus slices)``.

    The single parser behind both the per-lane gate and ``_publish_selection``
    — one derivation of "what may leave this machine", so the two cannot drift
    into disagreeing about what the operator asked for.
    """
    raw = str(cfg.get("publish.layers") or "").strip()
    if not raw:
        return frozenset(_PUBLISH_TICK_ORDER), frozenset(_PUBLISH_CORPUS_SLICES)

    tokens = {part.strip().lower().replace("-", "_") for part in raw.split(",")}
    tokens.discard("")
    if _PUBLISH_OFF in tokens:
        return frozenset(), frozenset()

    known = set(_PUBLISH_TICK_ORDER) | set(_PUBLISH_CORPUS_SLICES)
    unknown = sorted(tokens - known)
    if unknown and raw not in _publish_scope_warned:
        _publish_scope_warned.add(raw)
        print(
            "[brnrd:cloud] publish.layers names unrecognised scope(s): "
            f"{', '.join(unknown)} — they mirror nothing. Valid scopes: "
            f"{', '.join(sorted(known))}, or '{_PUBLISH_OFF}'."
        )

    slices = tokens & set(_PUBLISH_CORPUS_SLICES)
    if "corpus" in tokens:
        slices = set(_PUBLISH_CORPUS_SLICES)
    lanes = tokens & set(_PUBLISH_TICK_ORDER)
    if slices:
        lanes.add("corpus")
    else:
        lanes.discard("corpus")
    return frozenset(lanes), frozenset(slices)


def _publish_lane(name: str) -> Callable:
    """Register a publish lane *and* gate it, in one indivisible act.

    This is the structural half of #417: a publisher becomes reachable from
    ``_dashboard_publish_tick`` only by entering ``_PUBLISH_LANES``, and the
    only thing that puts it there is the decorator that also refuses to run it
    when the operator has switched its scope off. There is no way to acquire
    the registration without the gate, so a lane added later cannot silently
    escape ``publish.layers`` the way six of the original seven did.

    The gate sits on the lane's own door rather than on the tick's loop so
    that a caller reaching a publisher from anywhere else is bound by it too.
    ``lanes`` is threaded in by the tick, which resolves the config once per
    pass instead of once per lane.
    """

    def register(fn: Callable[[Path, Path | None, dict, Path], None]) -> Callable:
        @functools.wraps(fn)
        def guarded(
            brr_dir: Path,
            inbox_dir: Path | None,
            state: dict,
            *,
            lanes: frozenset[str] | None = None,
            responses_dir: Path | None = None,
        ) -> None:
            if lanes is None:
                lanes, _slices = _resolve_publish_scopes(_publish_config(brr_dir))
            if name not in lanes:
                return
            # #1396/#1437 — every lane gets the same fixed layout by
            # default (``brr_dir / "responses"``), which is only correct
            # outside account mode. `_dashboard_publish_tick` resolves the
            # real one (`cloud.run_loop`'s own `responses_dir`, which
            # diverges in account mode) and threads it through here so a
            # lane never has to rebuild it itself.
            fn(brr_dir, inbox_dir, state, responses_dir if responses_dir is not None else brr_dir / "responses")

        guarded.lane = name  # type: ignore[attr-defined]
        _PUBLISH_LANES[name] = guarded
        return guarded

    return register


def _dashboard_publish_tick(brr_dir: Path, inbox_dir: Path, responses_dir: Path | None = None) -> None:
    """One publish pass — see ``_dashboard_publish_loop`` for why it exists.

    Split out from the loop so a test can drive a single tick without
    threading or monkeypatching ``time.sleep`` on a ``while True``.

    The tick has no per-lane call sites left: it walks ``_PUBLISH_TICK_ORDER``
    and every entry is gated by construction (#417). Order still matters at
    the head of that tuple —

    - **Runners first**: its response piggybacks the pending wake request
      (#328 tap-to-request), the one dispatch-relevant datum in this tick.
      Behind the others, a slow or 502-retrying dashboard PUT stretched the
      mirror's staleness to tens of seconds — long enough for a tap racing
      its own follow-up message to lose (found live 2026-07-11).
    - **Live runs second**, for the same reason: since #476 its response
      piggybacks the account's pending run stops, and a stop is the most
      latency-sensitive datum in the tick — the user is watching the run burn
      while they wait. Behind the slower publishes it inherits exactly the
      staleness that ate a tap on 2026-07-11.

    *responses_dir*, when omitted, defaults to ``brr_dir / "responses"`` —
    correct outside account mode, and preserved so a direct 2-arg call (this
    file's own tests drive most of them that way) keeps working. account
    mode's real value diverges (``account_context.responses_dir``) and rides
    down from ``cloud.run_loop`` via ``_dashboard_publish_loop`` — see #1396 /
    #1437, and the ``_publish_lane`` docstring for where this is threaded to.
    """
    state = _context().load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return
    # Resolved once per pass, not once per lane: `publish.layers` is a file
    # read, and this loop runs every `_DASHBOARD_PUBLISH_INTERVAL_S`.
    lanes, _slices = _resolve_publish_scopes(_publish_config(brr_dir))
    if not lanes:
        return
    for lane in _PUBLISH_TICK_ORDER:
        _PUBLISH_LANES[lane](brr_dir, inbox_dir, state, lanes=lanes, responses_dir=responses_dir)


def _dashboard_publish_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path | None = None) -> None:
    """Publish the dashboard snapshots on their own short cadence.

    This thread is the *only* publisher. ``_loop_once`` used to publish once
    per inbox long-poll return too, on the theory that duplicate publishes
    were "harmless, idempotent overwrites" — they weren't: two threads
    PUTting the same activity snapshot concurrently raced the server's
    delete-then-insert replace into ``UniqueViolation`` 500s (seen live
    2026-07-09 as ``PUT /v1/daemons/activity -> 502`` spam). One publisher,
    no race. This loop is also what actually delivers on "a live
    dashboard": `_loop_once`'s cadence is capped at ``_POLL_WAIT_S`` (25s,
    chosen for chat responsiveness) whether or not any inbox event ever
    arrives. See kb/plan-loom-realtime-build.md slice 0.

    *responses_dir* is ``cloud.run_loop``'s own value, passed through
    unchanged (see ``_dashboard_publish_tick``'s docstring) — this thread
    used to be started with only ``(brr_dir, inbox_dir)``, so anything
    downstream that needed a responses dir had to rebuild
    ``brr_dir / "responses"`` and got it wrong in account mode (#1396,
    re-opening #1437 a third time in one week).
    """
    while True:
        try:
            _dashboard_publish_tick(brr_dir, inbox_dir, responses_dir)
        except Exception as e:
            print(f"[brnrd:cloud] dashboard publish loop error: {e}")
        time.sleep(_DASHBOARD_PUBLISH_INTERVAL_S)


def _iso_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _iso_from_event(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _summary(text: str, *, limit: int = 140) -> str:
    one_line = " ".join((text or "").split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1].rstrip() + "…"


def _runner_payload(meta: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    name = str(meta.get("runner_name") or meta.get("shell") or "").strip()
    shell = str(meta.get("runner_shell") or meta.get("shell") or "").strip()
    core = str(meta.get("runner_core") or meta.get("core") or "").strip()
    klass = str(meta.get("runner_class") or "").strip()
    if name:
        out["name"] = name
    if shell:
        out["shell"] = shell
    elif name:
        out["shell"] = name
    if core:
        out["core"] = core
    if klass:
        out["class"] = klass
    return out


def _run_activity_records(brr_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    runs_dir = brr_dir / "runs"
    for task in list_runs(runs_dir):
        if task.status not in {"pending", "running"}:
            continue
        manifest = run_manifest_path(runs_dir, task.id)
        try:
            stat = manifest.stat()
        except OSError:
            stat = None
        updated = _iso_from_epoch(stat.st_mtime if stat else None)
        started = _iso_from_epoch(stat.st_ctime if stat else None)
        records.append(
            {
                "id": f"run:{task.id}",
                "kind": "run",
                "source": task.source,
                "conversation_key": task.conversation_key,
                # #502: task-body excerpts leave the machine only for threads
                # the managed backend already carries (source == "cloud").
                # Locally-gated traffic (telegram/slack/github direct) ships
                # id/kind/status — the body never reached brnrd.dev inbound,
                # so the activity mirror must not leak it outbound.
                "summary": (
                    _summary(task.body) or task.event_id
                    if task.source == "cloud"
                    else task.event_id
                ),
                "runner": _runner_payload(task.meta),
                "status": task.status,
                "phase": str(task.meta.get("publish_status") or ""),
                "branch": (
                    str(task.meta.get("branch_name") or task.meta.get("publish_branch") or "")
                    if task.meta.get("has_new_commit") is True else ""
                ),
                "pr_number": task.meta.get("pr_number"),
                "started_at": started,
                "updated_at": updated,
                "links": {},
            }
        )
    return records


def _schedule_activity_records(brr_dir: Path) -> list[dict[str, Any]]:
    try:
        from .. import config as conf

        cfg = conf.load_config(brr_dir.parent)
        dom = None
        for candidate in dominion.resident_dominion_candidates(brr_dir.parent, cfg):
            if candidate.path.is_dir():
                dom = candidate.path
                break
        if dom is None:
            return []
        entries = schedule_mod.parse_schedule(dom)
    except Exception:
        return []
    state = schedule_mod.load_state(brr_dir)
    pacing = state.get("_pacing") if isinstance(state, dict) else None
    pacing = pacing if isinstance(pacing, dict) else {}
    records: list[dict[str, Any]] = []
    for entry in entries:
        entry_pacing = pacing
        per_entry = pacing.get("entries")
        if isinstance(per_entry, dict):
            candidate = per_entry.get(entry.id)
            if isinstance(candidate, dict):
                entry_pacing = candidate
        entry_pacing_mode = str(entry_pacing.get("mode") or "normal")
        scheduled_for: float | None = None
        status = "scheduled"
        if entry.kind == "at":
            rec = state.get(entry.id) or {}
            if rec.get("fired"):
                continue
            scheduled_for = entry.at
        elif entry.kind == "every":
            rec = state.get(entry.id) or {}
            last = rec.get("last_fired")
            try:
                last_fired = float(last)
            except (TypeError, ValueError):
                last_fired = None
            if last_fired is not None and entry.interval:
                effective_interval = entry.interval
                if entry_pacing_mode == "quota-paced":
                    try:
                        effective_interval *= float(entry_pacing.get("factor") or 1)
                    except (TypeError, ValueError):
                        pass
                scheduled_for = last_fired + effective_interval
            status = (
                entry_pacing_mode
                if entry_pacing_mode in {"quota-paced", "quota-paused"}
                else "recurring"
            )
        records.append(
            {
                "id": f"schedule:{entry.id}",
                "kind": "scheduled",
                "source": "schedule",
                "conversation_key": entry.conversation_key or f"schedule:{entry.id}",
                # #502: schedule bodies are dominion content (resident-authored
                # task specs) and never transit the managed backend otherwise —
                # the mirror carries the entry id, not an excerpt.
                "summary": f"self-scheduled thought: {entry.id}",
                "runner": {},
                "status": status,
                "phase": entry.kind,
                "scheduled_for": _iso_from_epoch(scheduled_for),
                # THE FORWARD WELD: the warp threads this entry's firings are
                # meant to serve, so the dashboard's lane can draw an armed
                # pick's crossing the same way it draws a burning one's. Rides
                # `links` — already free-form JSON on the activity record and
                # already served back — so this needs no schema, no migration,
                # and no new endpoint. Call signs only (`schedule.parse_serves`
                # drops anything else), which are already public as corpus; the
                # entry's body stays behind, per #502.
                "links": {"serves": list(entry.serves)} if entry.serves else {},
            }
        )
    return records


def _respawn_activity_records(inbox_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event in protocol.list_pending(inbox_dir):
        parent = str(event.get("respawned_from_event") or "").strip()
        if not parent:
            continue
        deferred = protocol.event_is_deferred(event)
        records.append(
            {
                "id": f"respawn:{event.get('id')}",
                "kind": "respawn",
                "source": str(event.get("source") or ""),
                "conversation_key": str(event.get("conversation_key") or ""),
                # #502: same cloud-only excerpt rule as run records above.
                "summary": (
                    _summary(str(event.get("body") or "")) or parent
                    if str(event.get("source") or "") == "cloud"
                    else parent
                ),
                "runner": _runner_payload(event),
                "status": "scheduled" if deferred else str(event.get("status") or "pending"),
                "phase": str(event.get("respawn_reason") or ""),
                "branch": str(event.get("branch") or event.get("branch_target") or ""),
                "pr_number": event.get("pr_number") or event.get("github_pr_number"),
                "defer_until": _iso_from_event(event.get("defer_until")),
                "links": {},
            }
        )
    return records


def _activity_snapshot(brr_dir: Path, inbox_dir: Path) -> list[dict[str, Any]]:
    return [
        *_run_activity_records(brr_dir),
        *_schedule_activity_records(brr_dir),
        *_respawn_activity_records(inbox_dir),
    ]


@_publish_lane("activity")
def _publish_activity(brr_dir: Path, inbox_dir: Path, state: dict, responses_dir: Path) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    try:
        _context().request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/activity",
            token=state["token"],
            json={"records": _activity_snapshot(brr_dir, inbox_dir)},
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] activity publish failed: {e}")


# The unified corpus (authored surface + home knowledge + complete run nodes) is
# published on *change*, not every tick: the old per-tick full-text PUT suited a
# handful of small authored pages, but the knowledge layer is ~150 files and
# megabytes (an 890KB log among them) — re-sending that every 3s is waste and,
# on a slow link, a staleness tax. Each mirrored file is also capped so one huge
# page cannot bloat the payload; a capped file still appears in the listing,
# marked ``truncated`` rather than silently dropped.
_CORPUS_FILE_CAP_BYTES = 256 * 1024

# #502 data minimization: the server-side mirror is a bounded *render cache*,
# never a second system of record — the repo, dominion, and knowledge repos
# stay the durable copies. Two publish levers bound what leaves the machine:
#
# - ``publish.layers`` (``.brr/config``): names what may be mirrored at all —
#   any mix of the corpus slices ``authored,knowledge,runs`` and the other
#   publish lanes, or ``none``; absent means everything. Parsed once by
#   ``_resolve_publish_scopes`` (see the #417 block above the tick), which is
#   also what gates the six non-corpus lanes.
# - ``publish.runs_window_days``: only run nodes younger than this ship
#   (default 14). ``0`` drops the runs layer, a negative value removes the
#   bound. The window is derived from the run-directory name
#   (``run-YYMMDD-…``), so it needs no extra state and slides at publish
#   time — a run aging out changes the fingerprint and the next publish
#   trims it from the mirror.
_RUNS_WINDOW_DAYS_DEFAULT = 14
_RUN_DIR_RE = re.compile(r"^run-(\d{2})(\d{2})(\d{2})-\d{4}")


def _publish_config(brr_dir: Path) -> dict:
    try:
        from .. import config as conf

        return conf.load_config(brr_dir.parent)
    except Exception:
        return {}


def _run_file_date(path: str) -> datetime | None:
    """The run-dir date encoded in a runs-layer corpus path, if any."""
    for part in path.split("/"):
        m = _RUN_DIR_RE.match(part)
        if m:
            yy, mm, dd = (int(g) for g in m.groups())
            try:
                return datetime(2000 + yy, mm, dd, tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def _publish_selection(files: list, cfg: dict, *, now: datetime | None = None) -> list:
    """Apply the #502 publish bounds to the discovered corpus.

    The slice set comes from ``_resolve_publish_scopes`` — the same parser the
    per-lane gate reads — so "which corpus slices may ship" and "may the corpus
    lane ship at all" cannot answer the operator's config differently (#417).
    """
    _lanes, layers = _resolve_publish_scopes(cfg)
    layers = set(layers)
    try:
        window_days = int(cfg.get("publish.runs_window_days", _RUNS_WINDOW_DAYS_DEFAULT))
    except (TypeError, ValueError):
        window_days = _RUNS_WINDOW_DAYS_DEFAULT
    if window_days == 0:
        layers.discard("runs")
    cutoff: datetime | None = None
    if window_days > 0:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=window_days)
    selected = []
    for f in files:
        if f.layer not in layers:
            continue
        if f.layer == "runs" and cutoff is not None:
            dated = _run_file_date(f.path)
            # Undated runs-layer files (layer indexes and the like) always
            # ship; only files inside a dated run dir age out.
            if dated is not None and dated < cutoff:
                continue
        selected.append(f)
    return selected


# Last successfully published corpus fingerprint, keyed by brr_dir. Module-level
# because a single publisher thread owns this loop (see _dashboard_publish_loop),
# and because "" after a restart is the right default: republish once on boot so
# a schema/convention change (e.g. the home-relative path move) always lands.
_corpus_publish_hash: dict[str, str] = {}


def _corpus_resolve(brr_dir: Path):
    """Resolve the account corpus read-only: ``(files, knowledge_dir)`` or None.

    ``None`` (skip publish) rather than raising when no account context resolves
    — a plain repo-local ``.brr/`` without an account home is a normal shape.
    """
    from .. import account as account_mod

    repo_root = brr_dir.parent
    try:
        ctx = account_mod.resolve_context(repo_root, create=False)
        return account_mod.corpus_files(ctx), account_mod.knowledge_path(ctx)
    except Exception as e:
        print(f"[brnrd:cloud] corpus snapshot skipped: {e}")
        return None


def _corpus_fingerprint(files: list, knowledge_dir: Path) -> str:
    """A cheap change signal for the corpus — no full reads of the large layer.

    Authored pages are few, so their content is hashed directly. Knowledge and
    run pages are many and large, so they contribute only (path, size,
    mtime) plus the knowledge repo HEAD sha — enough to notice a curate or a
    sync without reading the 890KB log on every heartbeat.
    """
    h = hashlib.sha256()
    for f in files:
        h.update(f.layer.encode("utf-8"))
        h.update(b"\x00")
        h.update(f.path.encode("utf-8"))
        h.update(b"\x00")
        if f.layer == "authored":
            try:
                h.update(f.abspath.read_bytes())
            except OSError:
                pass
        else:
            try:
                st = f.abspath.stat()
                h.update(f"{st.st_size}:{st.st_mtime_ns}".encode("utf-8"))
            except OSError:
                pass
        h.update(b"\n")
    # A connected home may not have linked knowledge yet; missing the nested
    # repo is a normal shape, so the change signal just omits the HEAD shard.
    head = gitops.rev_parse(knowledge_dir, "HEAD") if knowledge_dir.is_dir() else None
    if head:
        h.update(head.encode("utf-8"))
    return h.hexdigest()


def _corpus_payload(files: list) -> list[dict]:
    """Read each corpus file for the PUT, capping oversized mirrors."""
    payload: list[dict] = []
    for f in files:
        try:
            raw = f.abspath.read_text(encoding="utf-8")
        except OSError:
            continue  # a file that vanished mid-tick is not fatal; skip it
        truncated = False
        encoded = raw.encode("utf-8")
        if len(encoded) > _CORPUS_FILE_CAP_BYTES:
            # Cut on a byte boundary, then drop any partial trailing char.
            raw = encoded[:_CORPUS_FILE_CAP_BYTES].decode("utf-8", "ignore")
            truncated = True
        payload.append({"path": f.path, "markdown": raw, "layer": f.layer, "truncated": truncated})
    return payload


@_publish_lane("corpus")
def _publish_corpus(brr_dir: Path, inbox_dir: Path | None, state: dict, responses_dir: Path) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    resolved = _corpus_resolve(brr_dir)
    if resolved is None:
        return
    files, knowledge_dir = resolved
    # #502: bound the mirror *before* fingerprinting so a window slide (a run
    # aging past the cutoff) reads as a change and triggers the trimming PUT.
    files = _publish_selection(files, _publish_config(brr_dir))
    fingerprint = _corpus_fingerprint(files, knowledge_dir)
    key = str(brr_dir)
    if _corpus_publish_hash.get(key) == fingerprint:
        return  # unchanged since the last publish — skip the network round-trip
    payload = _corpus_payload(files)
    try:
        out = _context().request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/surface",
            token=state["token"],
            json={"files": payload},
            timeout=15,
        )
        # Mark clean only after a successful PUT so a failed publish retries.
        _corpus_publish_hash[key] = fingerprint
    except Exception as e:
        print(f"[brnrd:cloud] corpus publish failed: {e}")
        return
    # A 200 is not a mirrored corpus: the server drops layers the account's
    # repos have not jointly consented to and answers OK for what remains —
    # found live 2026-07-27 with the dashboard reading "No corpus mirrored
    # yet" while this lane believed it had published 2,814 files. `files` in
    # the response is the accepted subset; sent-some-got-none is the one
    # provably-narrowed shape, so it is the one this names. Fingerprint stays
    # marked clean on purpose — re-PUTting an all-dropped payload every 3 s
    # tick would hammer the same refusal; the message re-fires on the next
    # real corpus change instead, which in practice is every run.
    accepted = out.get("files") if isinstance(out, dict) else None
    if payload and isinstance(accepted, list) and not accepted:
        print(
            f"[brnrd:cloud] corpus publish: server accepted 0 of {len(payload)} "
            "file(s) — every layer was dropped at the publish-consent seam "
            "(no connected repo has recorded corpus consent, or their scopes "
            "intersect to nothing). The dashboard work surface stays empty "
            "until consent is recorded."
        )


def _quota_window(
    label: str,
    percent: float | None,
    reset: str | None = None,
    resets_at: float | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "used": None,
        "limit": None,
        "percent": percent,
        "reset": reset,
        # Machine-parseable reset instant (unix epoch seconds), alongside the
        # display-text `reset` above — the window-track visual's time-
        # remaining axis needs this, `reset` alone is prose (2026-07-06,
        # kb/design-dashboard-live-surface.md "Shipped" gap this closes).
        "resets_at": resets_at,
    }


# A Codex rate-limit window is identified by its *duration*, not by the slot
# (`primary`/`secondary`) it happens to arrive in — see
# `codex_status.py`'s module docstring for the live case that proved it.
_CODEX_WINDOW_LABELS: dict[int, str] = {
    300: "5h window",   # the classic `primary`
    10080: "weekly",    # the classic `secondary` — and, since 2026-07-13, sometimes `primary`
}


def _codex_window_label(window_minutes: float | None, fallback: str) -> str:
    """A quota window's display label, derived from how long the window is.

    Falls back to the historical positional label only when the snapshot
    carries no duration at all (a cache written by an older brr, or a rollout
    event that omitted ``window_minutes``) — there, the slot is genuinely the
    only evidence available, and guessing beyond it would be fabrication.
    """
    if window_minutes is None:
        return fallback
    minutes = int(window_minutes)
    known = _CODEX_WINDOW_LABELS.get(minutes)
    if known:
        return known
    # An unrecognized duration is still a real window and still worth showing:
    # name it after itself rather than dropping it or forcing it into one of
    # the two labels we know (OpenAI has changed this shape once already).
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d window"
    if minutes % 60 == 0:
        return f"{minutes // 60}h window"
    return f"{minutes}m window"


def _codex_quota_windows(quota: dict[str, Any]) -> list[dict[str, Any]]:
    """The Codex windows the account actually reports, labelled by duration.

    Two changes from the positional read this replaces, both about not lying:

    - a window is labelled from its own ``window_minutes``, so a weekly window
      delivered in the ``primary`` slot renders as ``weekly`` (the reported bug:
      the number was there, under the wrong name, while ``weekly`` published
      ``percent: null`` and the dashboard drew it as unavailable);
    - a slot the account does not report is *omitted* rather than published as a
      null-percent window. An absent window and an unknown window look identical
      on the panel, and only one of them is true — today's Plus account simply
      has no separate 5h limit to show.
    """
    windows: list[dict[str, Any]] = []
    for slot, fallback in (("primary", "5h window"), ("secondary", "weekly")):
        percent = quota.get(f"{slot}_remaining_percent")
        if percent is None:
            continue
        windows.append(
            _quota_window(
                _codex_window_label(quota.get(f"{slot}_window_minutes"), fallback),
                percent,
                resets_at=quota.get(f"{slot}_resets_at"),
            )
        )
    return windows


def _codex_quota_shell(brr_dir: Path) -> dict[str, Any] | None:
    """Codex's quota row: the app-server probe, backstopped by the rollout read.

    The rollout read alone *does* have an idle-window gap — the comment here
    used to deny it ("live every loop tick, no idle-window gap the way Claude's
    cached PTY scrape has"), and that was simply wrong: nothing writes a
    ``token_count`` event between runs, so an idle Codex froze this row until the
    dashboard aged it out to ``stale`` (#312 made that honest, #315 asked for it
    to stop happening). The active ``codex app-server`` probe closes it — an
    account-metadata call that needs no run and spends no quota — on the same
    bounded idle cadence the Claude row already refreshes on.
    """
    levels = codex_usage.merge_levels(
        codex_usage.load_or_refresh_snapshot(
            brr_dir,
            max_age_seconds=_CODEX_QUOTA_PUBLISH_MAX_AGE_SECONDS,
            timeout_seconds=10.0,
        ),
        codex_status.load_levels(),
    )
    usage_samples.record(brr_dir, "codex", levels)
    quota = levels.get("quota") if isinstance(levels, dict) else None
    if not isinstance(quota, dict):
        return None
    windows = _codex_quota_windows(quota)
    if not windows:
        return None
    return {
        "shell": "codex",
        "status": "known",
        # The reading's own capture time, not "now" — whichever seam supplied the
        # quota stamped it (`merge_levels` carries that stamp through), so the
        # dashboard measures staleness off the same clock for both shells and a
        # failed probe can never make a frozen rollout look live.
        "updated_at": levels.get("updated_at"),
        "windows": windows,
        # Trailing burn (`usage_samples.recent_burn`). Not a window and never
        # drawn as one: a *rate*, derived from the timestamped rollout samples
        # brr already tails. It exists because OpenAI stopped publishing the 5h
        # window for this account on 2026-07-12 (proven at the source: the
        # app-server now reports exactly one window), so the short-horizon
        # question that bar answered — am I burning too fast right now? — lost
        # its only instrument. A weekly percentage cannot answer it: 53% left is
        # calm at a drip and an alarm at six points an hour. This says which.
        #
        # Measured off the shell-agnostic sample store, not a rollout scan: the
        # readings brr already takes every heartbeat *are* the series, for both
        # Shells (`usage_samples`). One store, so the two rows can never
        # disagree about the same account.
        "burn": usage_samples.recent_burn(brr_dir, "codex"),
        # Free "Full reset (Weekly + 5 hr)" grants sitting unredeemed on the
        # account — only the app-server seam knows about these, and a quota row
        # that reads 4% left while four resets go unused is telling half a truth.
        "reset_credits": quota.get("reset_credits_available"),
        # Claude's shell carries a proven per-run USD figure in `credits`
        # (`_claude_credits_block`, sourced from the headless result JSON's
        # `total_cost_usd`); Codex's CLI result JSON has no equivalent
        # accounting field, so there is nothing bounded to read here — named
        # explicitly rather than just omitting the key, which reads
        # identically to "unknown" from the dashboard (brnrd.dev live-run
        # dashboard posture, 2026-07-13: "do not fabricate or infer spend
        # from model names").
        "spend": {
            "status": "unimplemented",
            "reason": "no per-run cost figure in the Codex CLI's result JSON yet",
        },
    }


def _claude_week_model_windows(
    levels: dict[str, Any], buckets: dict[str, Any]
) -> list[dict[str, Any]]:
    """Per-model weekly windows (Fable's own pool today) as real windows.

    ``claude_usage.parse_usage_text`` already parses ``Current week
    (Fable)`` alongside the primary ``Current week`` line into
    ``levels["week_models"][label]`` (full reset info) and the deduped
    percentage into ``quota.buckets.week_models[label]`` — but until now
    nothing here ever read either, so a Fable-heavy account's own weekly
    pool was silently dropped from the dashboard: not wrong, just never
    published, which reads identically to "unknown" from the outside (the
    brnrd.dev live-run dashboard report this closes, 2026-07-13). One window
    per labeled model, sorted for a stable publish order.
    """
    bucket_pcts = buckets.get("week_models") if isinstance(buckets, dict) else None
    meta = levels.get("week_models") if isinstance(levels, dict) else None
    if not isinstance(bucket_pcts, dict):
        return []
    out: list[dict[str, Any]] = []
    for label in sorted(bucket_pcts):
        bucket = bucket_pcts.get(label)
        if not isinstance(bucket, dict):
            continue
        pct = bucket.get("remaining_percentage")
        if pct is None:
            continue
        label_meta = meta.get(label) if isinstance(meta, dict) else None
        reset = label_meta.get("reset") if isinstance(label_meta, dict) else None
        resets_at = label_meta.get("resets_at") if isinstance(label_meta, dict) else None
        out.append(_quota_window(f"weekly ({label})", pct, reset, resets_at))
    return out


def _claude_quota_shell(brr_dir: Path) -> dict[str, Any] | None:
    # A prior run's per-run outbox dir is preferred when one exists (freshest
    # cache, and the shape every warm reading has used since #1027). A cold
    # daemon — no Claude run has ever completed — has none, and used to stop
    # here: `load_or_refresh_snapshot` returns `None` for `outbox_dir is
    # None` without ever attempting the PTY `/usage` scrape, which needs no
    # prior run at all (`claude_usage.capture_levels(cwd=…)`). Fall back to
    # `brr_dir` itself — the same durable account-shared directory
    # `claude_status` already caches its own snapshot into (`_shared_dir`,
    # `BRR_SHARED_DIR`, wired at `daemon.py:3803`, read a few lines below in
    # `_claude_credits_block`) and the same pattern `_codex_quota_shell`
    # above already uses cold (`codex_usage.load_or_refresh_snapshot(brr_dir,
    # …)`). No second cache location minted — this is the one slot, reused.
    outbox_dir = runner_quota.latest_claude_usage_outbox_dir(brr_dir) or brr_dir
    levels = claude_usage.load_or_refresh_snapshot(
        outbox_dir,
        cwd=brr_dir,
        max_age_seconds=_CLAUDE_QUOTA_PUBLISH_MAX_AGE_SECONDS,
        timeout_seconds=10.0,
        wait_for_credits=True,
    )
    usage_samples.record(brr_dir, "claude", levels)
    quota = levels.get("quota") if isinstance(levels, dict) else None
    buckets = quota.get("buckets") if isinstance(quota, dict) else None
    credits = _claude_credits_block(brr_dir, usage_levels=levels)
    if not isinstance(buckets, dict):
        if credits is None:
            return None
        buckets = {}
    session = (
        buckets.get("session") if isinstance(buckets.get("session"), dict) else {}
    )
    week = buckets.get("week") if isinstance(buckets.get("week"), dict) else {}
    session_pct = session.get("remaining_percentage")
    week_pct = week.get("remaining_percentage")
    week_model_windows = _claude_week_model_windows(
        levels if isinstance(levels, dict) else {}, buckets
    )
    if (
        session_pct is None and week_pct is None
        and not week_model_windows and credits is None
    ):
        return None
    return {
        "shell": "claude",
        "status": "known",
        # The scrape's own capture time, not "now". The cloud publisher now
        # refreshes the cached PTY probe on a bounded idle cadence, but the
        # dashboard still measures freshness off this field so a failed or
        # skipped refresh cannot make old data look live.
        "updated_at": levels.get("updated_at"),
        "windows": [
            _quota_window(
                "5h window", session_pct, levels.get("session_reset"), levels.get("session_resets_at")
            ),
            _quota_window(
                "weekly", week_pct, levels.get("week_reset"), levels.get("week_resets_at")
            ),
            *week_model_windows,
        ],
        # Trailing burn, same reading and same discipline as the Codex row —
        # this Shell simply had no series to measure until `usage_samples`
        # started keeping one. It is the Shell doing most of the spending, so
        # "am I burning too fast right now?" was going unanswered exactly where
        # it mattered most.
        "burn": usage_samples.recent_burn(brr_dir, "claude"),
        "credits": credits,
    }


def _claude_credits_block(
    brr_dir: Path,
    *,
    usage_levels: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Claude credits evidence from `/usage` plus per-run spend, when proven.

    ``usage_levels["usage_credits"]`` is Claude's account credit-balance
    surface from the interactive ``/usage`` panel (amount spent / cap /
    reset). Separately, the run-scoped ``total_cost_usd`` in the headless
    result JSON is
    an internal accounting figure, not a real charge. It becomes real dollars
    the moment the subscription's 5h/weekly window is exhausted and Anthropic
    falls the account through to metered credits (confirmed live 2026-07-07:
    a maintainer-observed run kept working straight through an exhausted 5h
    window, billed ~$1 in credits) — so this is not a projection, it is the
    same terminal-JSON field :mod:`brr.claude_status` already collects for
    the boot-prompt ``spend`` facet, just never published to the dashboard
    before now. ``None`` when no run has ever produced one (cold cache, or a
    Codex-only daemon).
    """
    # ``brr_dir`` is the account-shared dir claude_status now writes its
    # durable copy into (``BRR_SHARED_DIR``, #1027) — read it directly first.
    # The glob-over-surviving-outbox-dirs hunt stays as a fallback for the
    # rollout window before any claude run has produced the shared copy yet;
    # it is the same hunt that used to be the *only* source and rarely found
    # anything, since a per-run outbox is swept the moment its run ends.
    levels = claude_status.load_snapshot(brr_dir)
    if levels is None:
        outbox_dir = runner_quota.latest_claude_spend_outbox_dir(brr_dir)
        levels = claude_status.load_snapshot(outbox_dir) if outbox_dir else None
    spend = levels.get("spend") if isinstance(levels, dict) else None
    usage = (
        usage_levels.get("usage_credits")
        if isinstance(usage_levels, dict) else None
    )
    total = spend.get("total_cost_usd") if isinstance(spend, dict) else None
    if not isinstance(usage, dict) and total is None:
        return None
    block = {
        "total_cost_usd": total,
        "summary": spend.get("summary") if isinstance(spend, dict) else None,
        "updated_at": levels.get("updated_at") if isinstance(levels, dict) else None,
    }
    if isinstance(usage, dict):
        block.update(
            {
                "enabled": usage.get("enabled"),
                "used_percentage": usage.get("used_percentage"),
                "remaining_percentage": usage.get("remaining_percentage"),
                "spent_amount": usage.get("spent_amount"),
                "limit_amount": usage.get("limit_amount"),
                "currency": usage.get("currency"),
                "reset": usage.get("reset"),
                "resets_at": usage.get("resets_at"),
                "summary": usage.get("summary") or block.get("summary"),
                # Set when this credits reading was carried across a `/usage`
                # scrape whose async region came back rate-limited
                # (`claude_usage.carry_forward_sections`). The number is real —
                # it just wasn't seen *this* tick, and a dollar figure that
                # can't say when it was last confirmed is a dollar figure that
                # will eventually be believed at the wrong moment.
                "carried_from": usage.get("carried_from"),
                "run_spend_summary": spend.get("summary") if isinstance(spend, dict) else None,
                "updated_at": (
                    usage_levels.get("updated_at")
                    if isinstance(usage_levels, dict) else block.get("updated_at")
                ),
            }
        )
    return block


def _quota_snapshot(brr_dir: Path) -> list[dict[str, Any]]:
    """This daemon's runner-quota snapshot: real per-shell 5h/weekly windows.

    Mirrors the Activity/Plans publish shape (#237) — reads whatever local
    evidence already exists (Codex's live rollout read, Claude's cached
    ``/usage`` scrape via :func:`runner_quota.latest_claude_usage_outbox_dir`).
    Claude's cached scrape is refreshed here on a bounded idle cadence shorter
    than the dashboard's stale threshold, not on every publish tick. A shell
    with no evidence yet is omitted, not reported as a fake zero.
    """
    shells = [_claude_quota_shell(brr_dir), _codex_quota_shell(brr_dir)]
    return [shell for shell in shells if shell is not None]


def _shell_level_label(windows: list[dict[str, Any]]) -> str | None:
    """Compact level string from a quota-window list, or ``None`` when unknown.

    Finds the most-constraining (lowest-percent) window among those with a
    known percentage.  Returns ``None`` rather than any placeholder when no
    window has a reading — the caller must render nothing, not a fake healthy
    label (#632 standing decision 2).
    """
    known: list[tuple[float, str | None, float | None]] = [
        (float(w["percent"]), w.get("reset"), w.get("resets_at"))
        for w in windows
        if isinstance(w.get("percent"), (int, float))
    ]
    if not known:
        return None
    min_pct, min_reset, min_resets_at = min(known, key=lambda x: x[0])
    if min_pct < 1.0:
        if min_reset:
            return f"exhausted, resets {min_reset}"
        if min_resets_at is not None:
            import datetime
            dt = datetime.datetime.fromtimestamp(
                float(min_resets_at), tz=datetime.timezone.utc
            )
            return "exhausted, resets " + dt.strftime("%b %-d")
        return "exhausted"
    return f"{round(min_pct)}%"


def quota_shell_labels(brr_dir: Path) -> dict[str, str | None]:
    """Compact level label per shell from cached quota readings.

    Returns ``{"claude": "82%", "codex": "exhausted, resets Jul 28"}`` etc.
    Calls ``_quota_snapshot`` once (one read per pool, not per profile) and
    extracts a single label per shell.  Shells with no known reading are
    omitted entirely — callers must treat a missing key as unknown, never as
    healthy (#632 standing decision 2).  Errors are absorbed and return ``{}``.
    """
    try:
        shells = _context().quota_snapshot(brr_dir)
    except Exception:
        return {}
    result: dict[str, str | None] = {}
    for shell_data in shells:
        shell = str(shell_data.get("shell") or "").strip()
        if not shell:
            continue
        label = _shell_level_label(list(shell_data.get("windows") or []))
        if label is not None:
            result[shell] = label
    return result


def _gate_health_snapshot(brr_dir: Path) -> list[dict[str, Any]]:
    """Configured ingestion paths, including quiet paths with no poll yet."""
    return runtime.gate_health_rows(brr_dir)


def _repo_initialised_snapshot(brr_dir: Path) -> dict[str, bool]:
    """This daemon's own boot-kernel init facts for the repo it's paired to
    (#1268's wire half): ``agents_md_missing`` / ``kb_missing``, the same
    two existence checks ``prompts.build_boot_score`` runs on every wake
    (#1261) — one stat call plus one config-scoped kb lookup, cheap enough
    to repeat here on the quota publish cadence rather than thread the
    wake's own reading across process boundaries.

    ``brr_dir.parent`` is the repo root whenever the *local* (non-worktree)
    ``.brr`` exists, which is always true for the checkout this publisher
    runs from — ``daemon.start()`` performs the equivalent
    ``repo_root / "AGENTS.md"`` check before ``brr_dir`` is even resolved,
    at the one root the whole daemon process is hosted from. A run's own
    per-thought worktree (``.brr/worktrees/<run-id>``) never reaches this
    function; only the main daemon loop calls ``_publish_quota``.
    """
    from .. import config as conf, knowledge

    repo_root = brr_dir.parent
    agents_md_missing = not (repo_root / "AGENTS.md").exists()
    try:
        kb_missing = knowledge.active_kb_dir(repo_root, conf.load_config(repo_root)) is None
    except Exception:
        # Conservative direction on failure, same call `build_boot_score`
        # makes: an unknown answer reports "present" rather than a
        # possibly-wrong "missing" (a false "missing" would darken a
        # capability that's actually fine).
        kb_missing = False
    return {"agents_md_missing": agents_md_missing, "kb_missing": kb_missing}


# Failure causes this process has already given a full traceback to, so a
# repeating one costs one traceback total rather than one per tick (#1386:
# 4,860 byte-identical one-line prints overnight, and not one of them named
# the raising line — the same deduped-as-noise signature #818 already named,
# with a real starved lane hiding under it). Same "warn once" shape as
# ``_publish_scope_warned`` above — a module-level set, keyed on what makes
# two occurrences the *same* cause rather than merely the same message text.
_quota_publish_causes_seen: set[str] = set()


@_publish_lane("quota")
def _publish_quota(brr_dir: Path, inbox_dir: Path | None, state: dict, responses_dir: Path) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    try:
        init_facts = _repo_initialised_snapshot(brr_dir)
        _context().request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/quota",
            token=state["token"],
            json={
                "shells": _quota_snapshot(brr_dir),
                "gates": _gate_health_snapshot(brr_dir),
                "repo_agents_md_missing": init_facts["agents_md_missing"],
                "repo_kb_missing": init_facts["kb_missing"],
            },
            timeout=10,
        )
    except Exception as e:
        # Identity, not just text: the traceback's own frame is the cause,
        # and `repr` on top of the type name catches two exceptions that
        # stringify identically but raise from different lines (e.g. two
        # bare `KeyError()`s render the same `str(e)`, "").
        cause = f"{type(e).__module__}.{type(e).__qualname__}: {e!r}"
        if cause not in _quota_publish_causes_seen:
            _quota_publish_causes_seen.add(cause)
            print(
                f"[brnrd:cloud] quota publish failed: {e}\n"
                f"{traceback.format_exc()}"
            )
        else:
            print(f"[brnrd:cloud] quota publish failed: {e}")


def _runners_snapshot(brr_dir: Path) -> dict[str, Any]:
    """This daemon's runner catalog: locally-discovered Shell+Core profiles.

    #328's spool rack, daemon-owned discovery: the same PATH-filtered,
    probe-augmented projection the Run Context Bundle's "Runner catalog"
    block injects into every wake (`runner.available_runner_catalog` —
    Core registry + `runner_cores.probe_shell_models`, no network).
    ``default`` is the profile `resolve_runner` resolves for a plain wake
    right now — the ``shell=``/``core=`` config pin, or the cost-aware
    selection when unpinned. Publishing the *discovered* view (not the
    packaged registry alone) is deliberate: installed shells update on
    their own clock, and a hardcoded menu rots silently (#343).
    """
    from .. import config as _config, runner
    from ..run import _cfg_environment_policy, _docker_configured, resolve_env

    repo_root = brr_dir.parent
    default: str | None
    try:
        default = runner.resolve_runner(repo_root)
    except Exception:
        default = None
    try:
        profiles = runner.available_runner_catalog(repo_root, selected=default)
    except Exception as e:
        print(f"[brnrd:cloud] runner catalog read failed: {e}")
        profiles = []
    cfg = _config.load_config(repo_root)
    policy = _cfg_environment_policy(cfg)
    try:
        environment_default = resolve_env(policy, cfg)
    except ValueError:
        environment_default = policy or None
    docker_reason: str | None = None
    if not _docker_configured(cfg):
        docker_reason = "docker.image is not configured"
    elif shutil.which("docker") is None:
        docker_reason = "Docker CLI is not on PATH"
    environments = [
        {"name": "worktree", "available": True},
        {"name": "docker", "available": docker_reason is None, "reason": docker_reason},
        {"name": "solitary", "available": docker_reason is None, "reason": docker_reason},
    ]
    # #932's conversation-sticky, made visible: the record that actually
    # answers "who wakes next" for the bound conversation. Same liveness
    # verdict dispatch uses (`wake_request.live_sticky_view`), so the rack
    # can never advertise a promise dispatch no longer honours. None when
    # absent/expired — the mirror clears on the next publish tick.
    sticky = wake_request.live_sticky_view(
        brr_dir, wake_request.sticky_ttl_seconds(cfg)
    )
    return {
        "profiles": profiles,
        "default": default,
        "environment_default": environment_default,
        "environments": environments,
        "sticky": sticky,
    }


@_publish_lane("runners")
def _publish_runners(brr_dir: Path, inbox_dir: Path | None, state: dict, responses_dir: Path) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    payload = _context().runners_snapshot(brr_dir)
    try:
        body = _context().request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/runners",
            token=state["token"],
            json=payload,
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] runners publish failed: {e}")
        return
    # #328 tap-to-request: mirror the account's pending tap (if any) so
    # dispatch knows within a publish tick that one exists at all. One-way
    # since #733 — the daemon has nothing to ack back, because it no longer
    # decides anything about a tap. See src/brr/wake_request.py.
    pending = body.get("pending_wake_request") if isinstance(body, dict) else None
    wake_request.store_pending(
        brr_dir, pending if isinstance(pending, dict) else None,
    )
    # #932's exit tap: the dashboard asked for the conversation-sticky to be
    # dropped. Tense-guarded (a sticky claimed after the ask survives it);
    # the next publish tick reports sticky=None, which is what retires the
    # ask server-side — no second ack channel.
    release_at = body.get("sticky_release_at") if isinstance(body, dict) else None
    if release_at and wake_request.release_sticky(brr_dir, release_at):
        print("[brnrd:cloud] conversation-sticky released by dashboard ask")


# #733: the one bound on dispatch's one server call. Dispatch already spends
# ~4s before a runner starts, so ~2s is noise against it — but it has to be a
# *bound*, not a hope, because this is the only place a wake blocks on
# brnrd.dev being reachable. Deliberately below the gateway-retry path too:
# riding `_RETRY_SLEEPS_S` would turn a deploy-window 502 into ~9s of held
# dispatch, and a tap is worth 2s of waiting, not 9.
_WAKE_CLAIM_TIMEOUT_S = 2.0


def claim_wake_request(
    brr_dir: Path,
    *,
    request_id: str,
    event_id: str | None = None,
    source: str | None = None,
    event_created: str | None = None,
    timeout: float = _WAKE_CLAIM_TIMEOUT_S,
) -> dict | None:
    """Ask the server whether this wake spends the parked tap (#733).

    The single claim point. Returns the server's verdict dict (``apply``,
    ``reason``, ``status``, ``profile``, ``repo_label``, ``environment``), or
    **None** when no answer was obtained — not connected, timed out,
    unreachable, refused, malformed.

    None is fail-open by construction: dispatch treats it exactly as it
    treats an empty mirror, so an unreachable brnrd.dev costs the tap and
    nothing else. That is the one honest cost of moving the decision to its
    owner — dispatch now has a network dependency it never had — and it is
    bounded to :data:`_WAKE_CLAIM_TIMEOUT_S` and to the rare wake that has a
    tap parked for it at all.
    """
    request_id = str(request_id or "").strip()
    if not request_id:
        return None
    state = _context().load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return None
    body = {"request_id": request_id}
    if event_id:
        body["event_id"] = str(event_id)
    if source:
        body["source"] = str(source)
    if event_created:
        body["event_created"] = str(event_created)
        # Our clock, read now, so the server can judge the tap's age against
        # the event's age instead of comparing two absolute stamps taken on
        # two machines. Only sent alongside ``event_created``, which is the
        # only thing it is a reference point for.
        body["daemon_now"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    try:
        result = _context().request(
            state["brnrd_url"],
            "POST",
            "/v1/daemons/runners/wake-request/claim",
            token=state["token"],
            json=body,
            timeout=timeout,
            retry=False,
        )
    except Exception as e:
        print(f"[brnrd:cloud] wake-request claim failed: {e}")
        return None
    return result if isinstance(result, dict) else None


def _live_run_progress(brr_dir: Path, stream: str, run_id: str) -> run_progress.RunProgressView | None:
    """Best-effort progress projection for one live presence entry.

    Swallows its own failure — a malformed or half-written conversation
    log for one run must not take down the whole publish tick for every
    other live run.
    """
    if not stream or not run_id:
        return None
    try:
        return run_progress.project_run(brr_dir, stream, run_id)
    except Exception:
        return None


# --- live-run wire bounds (#685 ask 2, guard B) ------------------------------
#
# A deliberate duplication of `src/brnrd/schemas.py::LiveRunIn`'s field caps.
# `src/brr` (this daemon) cannot import `src/brnrd` (the API): they ship
# separately. The collector should not rely on the server's mercy — the server
# now truncates display fields, but a daemon publishing to an older API, or to
# one whose truncation regressed, still wants its own bound.
#
# Pinned to `tests/fixtures/live_run_bounds.json`, which is *generated* from
# `LiveRunIn` (#723). A parity test between the two implementations would be
# the wrong pin: #722's four implementations of one rule agreed with each other
# perfectly for the bug's whole life. The fixture makes "deliberately mirrored
# by hand" a checkable claim, and a bound that changes in `LiveRunIn` reddens a
# test here instead of 422ing a dashboard.
#
# Note the *shape*: the matched set is the closed class and truncation is the
# default, so a *shown* field added to `LiveRunIn` later is bounded here as
# soon as the table is regenerated, without anyone remembering to list it.
#
# The split is **matched vs shown**, not display vs identity. `repo_label`
# reads like display and is matched: `publish_scope._subject_permits` resolves
# a row's consent through it, and an unresolvable label falls back to the
# *publisher's* consent — so truncating it here would publish an opted-out
# repo's row under someone else's permission.
_LIVE_RUN_TRUNCATION_MARK = "…"
_LIVE_RUN_IDENTITY_FIELDS = frozenset({"id", "parent_run_id", "repo_label", "run_id"})
_LIVE_RUN_STRING_BOUNDS = {
    "card_text": 4096,
    "id": 64,
    "kind": 32,
    "label": 256,
    "lifecycle": 16,
    "mood": 64,
    "mood_glyph": 16,
    "mood_rest": 16,
    "name": 60,
    "parent_run_id": 64,
    "phase": 32,
    "repo_label": 256,
    "run_id": 64,
    "stream": 256,
}

# --- where the work happens (the overlay-that-shows-the-room slice) ---------
#
# Three additions to the live-run row, all derived from state the daemon
# already writes — no new telemetry source (design-resident-field.md §Data
# and delivery: "derive it from the existing per-run truth rather than
# creating a second telemetry truth"):
#
# * ``room``  — where this thought's hands are: environment kind, the branch
#   the tree is actually on (asked of git live, because a run renames its
#   branch mid-flight; manifest as fallback), and the worktree dir name.
#   Source: the run manifest (`.brr/runs/<id>/run.md`) + one bounded
#   ``git rev-parse`` per live run per publish tick (~25-30s).
# * ``edge``  — the latest attested tool boundary: phase, classified act,
#   tool names, the *already-redacted* detail summary `hooks.record_boundary`
#   wrote (`_tool_detail` caps at 500 B and masks secrets at write time),
#   response byte count, and whether the daemon injected context there.
#   Source: a bounded tail read of `boundaries.jsonl` — never the whole file.
# * ``lifecycle`` + ``await_until`` — starting | weaving | awaiting |
#   closing. `awaiting` is read off the run's own portal capsule (the
#   `await` facet: armed and unresolved), never inferred from quietness —
#   AWAIT must stay distinguishable from "between wakes" (the runner still
#   exists) and from a long silent tool call.

#: How much of a `boundaries.jsonl` tail one publish tick may read.
_EDGE_TAIL_BYTES = 16_384

#: `run_progress` phase → wire lifecycle. Terminal phases publish nothing:
#: a run in one leaves the presence registry within a tick, and the Cloth
#: owns the past tense.
_LIFECYCLE_BY_PHASE = {
    "queued": "starting",
    "preparing": "starting",
    "running": "weaving",
    "finalizing": "closing",
    "stopping": "closing",
}


def _live_run_manifest(brr_dir: Path, run_id: str) -> dict[str, Any]:
    """The run's `run.md` frontmatter, `{}` on any failure."""
    if not run_id:
        return {}
    try:
        text = (brr_dir / "runs" / run_id / "run.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        return protocol.parse_frontmatter(text) or {}
    except Exception:
        return {}


def _live_branch(tree: Path) -> str | None:
    """The branch *tree* is on right now, or ``None`` — never a guess.

    Asked of git rather than the manifest because a run legitimately
    renames or switches its branch mid-flight; the manifest records the
    seed. Bounded and best-effort: a publish tick must not hang on a git
    that is wedged.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    branch = out.stdout.strip()
    return branch if branch and branch != "HEAD" else None


def _room_payload(brr_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    """Where this run's hands are: env kind, live branch, worktree dir name."""
    env = str(manifest.get("env") or "").strip() or None
    worktree = str(manifest.get("worktree_path") or "").strip() or None
    tree = Path(worktree) if worktree else brr_dir.parent
    branch = _live_branch(tree)
    if branch is None:
        branch = (
            str(manifest.get("branch_name") or "").strip()
            or str(manifest.get("host_context_branch") or "").strip()
            or None
        )
    dir_name = Path(worktree).name if worktree else None
    if not (env or branch or dir_name):
        return None
    return {
        "env": (env or "")[:16] or None,
        "branch": (branch or "")[:256] or None,
        "dir": (dir_name or "")[:256] or None,
    }


def _edge_dir(record_cwd: Any, manifest: Mapping[str, Any], brr_dir: Path) -> str | None:
    """The boundary's working dir, publishable — relative or basename only.

    The transcript records the raw cwd (local surface); the wire must not
    carry a host path. Relativize against the run's own tree (worktree
    path, else the checkout the daemon serves); the tree root itself
    publishes as ``.``; a cwd outside the tree degrades to its basename.
    """
    if not isinstance(record_cwd, str) or not record_cwd.strip():
        return None
    cwd = record_cwd.strip()
    tree = str(manifest.get("worktree_path") or "").strip() or str(brr_dir.parent)
    try:
        rel = Path(cwd).resolve().relative_to(Path(tree).resolve())
    except (OSError, ValueError):
        return (Path(cwd).name or None) and Path(cwd).name[:256]
    text = str(rel)
    return ("." if text == "." else text)[:256]


#: Disclosure bound for a boundary's ``detail`` **on the wire**, distinct from
#: ``hooks._DETAIL_BASH_MAX`` (500), which is a *retention* cap sized for
#: transport and storage of the local, gitignored ``boundaries.jsonl``.
#:
#: Measured 2026-08-28: the two were the same number doing two different jobs,
#: and the room rendered the body of a chat message off a boundary line —
#: ``cat > /tmp/reply5.md <<'MDEOF' **#1671 merged — thanks. …**`` — because a
#: reply written as a heredoc puts its whole text into argv. ``redact_detail``
#: did not catch it: prose is not a secret pattern, and it is right not to
#: become a prose classifier.
#:
#: The bound lives **here**, at the seam between the local log and the wire,
#: rather than in a renderer. There are four renderers of ``edge.detail``
#: today (the ASCII room, ``ResidentField``, the ``/new`` HUD, and
#: ``liveRuns``' own summary); bounding one leaves three, and a fifth would
#: have to remember. Nothing beyond this ever leaves the machine, so no
#: renderer has to.
_WIRE_DETAIL_MAX = 120

#: A heredoc's *operator and delimiter* are command shape and stay; its body
#: is a payload that merely travelled through argv and goes. Kept separate
#: from the length bound: the bound is the guarantee, this is legibility —
#: without it a truncated heredoc reads as 120 characters of someone's prose
#: instead of as "a file was written here".
_HEREDOC_RE = re.compile(r"<<-?\s*(?:'[^']+'|\"[^\"]+\"|[\w.-]+)")


def _wire_detail(detail: object) -> str | None:
    """Bound a boundary detail for publication. See ``_WIRE_DETAIL_MAX``."""
    if not isinstance(detail, str) or not detail:
        return None
    match = _HEREDOC_RE.search(detail)
    shaped = detail[: match.end()] + " …" if match else detail
    return shaped[:_WIRE_DETAIL_MAX] or None


def _edge_payload(
    brr_dir: Path, run_id: str, manifest: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """The latest attested tool boundary, from a bounded transcript tail.

    Skips a subagent's boundaries (#1095 — a limb's act is not the run's)
    and returns ``None`` rather than a zero-valued row when the transcript
    is absent or unreadable: no edge attested is different from a quiet one.
    """
    if not run_id:
        return None
    path = brr_dir / "runs" / run_id / "boundaries.jsonl"
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _EDGE_TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for raw in reversed(tail.splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue  # the seek may have torn the first line — skip, walk on
        if not isinstance(record, dict) or record.get("subagent"):
            continue
        phase = record.get("phase")
        if not isinstance(phase, str) or not phase:
            continue
        tools = [
            str(name)[:64]
            for name in (record.get("tools") or [])
            if isinstance(name, str)
        ][:16]
        at = record.get("at")
        act = record.get("act")
        detail = record.get("detail")
        out_bytes = record.get("out_bytes")
        return {
            "at": at if isinstance(at, str) else None,
            "phase": phase[:16],
            "act": act[:32] if isinstance(act, str) and act else None,
            "tools": tools,
            "detail": _wire_detail(detail),
            "out_bytes": out_bytes if isinstance(out_bytes, int) else None,
            "injected": bool(record.get("inject")),
            "dir": _edge_dir(record.get("cwd"), manifest or {}, brr_dir),
        }
    return None


def _await_facet(brr_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    """The run's own portal `await` facet, ``None`` when unreadable."""
    event_id = str(manifest.get("event_id") or "").strip()
    if not event_id:
        return None
    try:
        raw = json.loads(
            (brr_dir / "outbox" / event_id / "portal-state.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return None
    facet = raw.get("await") if isinstance(raw, dict) else None
    return facet if isinstance(facet, dict) else None


def _portals_payload(brr_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    """Correspondence waiting at this run's door — the *put to read* fact.

    Reads the same portal capsule ``_await_facet`` does, taking the
    ``inbound.events`` view the daemon refreshes on its heartbeat: how many
    pending events stand at this run's portal, and when the oldest arrived.
    Counts and one timestamp only — the wire carries the *fact* of a
    waiting message, never its content (same secrets posture as the edge's
    pre-redacted detail). ``None`` when the capsule is absent or unreadable:
    no portal attested is different from an empty one.
    """
    event_id = str(manifest.get("event_id") or "").strip()
    if not event_id:
        return None
    try:
        raw = json.loads(
            (brr_dir / "outbox" / event_id / "portal-state.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return None
    inbound = raw.get("inbound") if isinstance(raw, dict) else None
    if not isinstance(inbound, dict):
        return None
    events = inbound.get("events")
    if not isinstance(events, list):
        return None
    oldest: str | None = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        created = ev.get("created")
        if isinstance(created, str) and created and (oldest is None or created < oldest):
            oldest = created
    return {"pending": len(events), "oldest_at": oldest}


def _lifecycle_payload(
    brr_dir: Path,
    manifest: Mapping[str, Any],
    view: run_progress.RunProgressView | None,
) -> tuple[str | None, str | None]:
    """``(lifecycle, await_until)`` for one live row.

    AWAIT is a *positive* fact — the `await` facet armed and unresolved —
    never an inference from silence, and CLOSING is the attested finalizing
    phase, rendered only while genuinely in flight (the resident-field
    rule: no ceremony held open for theatre).
    """
    phase = (view.phase if view is not None else None) or None
    lifecycle = _LIFECYCLE_BY_PHASE.get(phase or "")
    if lifecycle is None and view is None and str(manifest.get("id") or ""):
        # A registered run with no conversation record yet: the wake is
        # being assembled or the Shell has not spoken — starting.
        lifecycle = "starting"
    if lifecycle != "weaving":
        return lifecycle, None
    facet = _await_facet(brr_dir, manifest)
    if facet and facet.get("armed") and not facet.get("resolved"):
        deadline = facet.get("deadline")
        return "awaiting", deadline if isinstance(deadline, str) else None
    return lifecycle, None


def _bounded_live_run(row: dict[str, Any]) -> dict[str, Any]:
    """Truncate this row's over-long display strings, marked.

    Matched keys are left exactly as read: truncating a value some decision is
    resolved against is *wrong data*, not shortened data. `id`/`run_id`/
    `parent_run_id` would silently re-point this row at another run, and
    `repo_label` decides whose consent the row publishes under. The server
    rejects those, and one rejected row now costs one row.
    """
    for field, cap in _LIVE_RUN_STRING_BOUNDS.items():
        if field in _LIVE_RUN_IDENTITY_FIELDS:
            continue
        value = row.get(field)
        if isinstance(value, str) and len(value) > cap:
            keep = max(0, cap - len(_LIVE_RUN_TRUNCATION_MARK))
            row[field] = value[:keep] + _LIVE_RUN_TRUNCATION_MARK[:cap]
    return row


def _live_runs_snapshot(brr_dir: Path) -> list[dict[str, Any]]:
    """This daemon's live/coexisting-runs snapshot (#258).

    Reads the local presence registry (``src/brr/presence.py``) — every
    daemon-dispatched thought (and any ad-hoc session) already registers
    and heartbeats there, so this is a publish step over data that already
    exists, the same shape as Activity/Plans/Quota (#237). No new
    collection mechanism, just the account-scoped visibility those three
    don't give: "what is my daemon doing right now, across every repo it
    touches" (`kb/design-dashboard-live-surface.md` §"Reconsidered
    2026-07-06").

    #200's remaining slice: phase / progress-card richness, folded into
    this same publish tick rather than a new transport — ``project_run``
    (already used by the chat gates to render the compact card) gives us
    both ``phase`` and the live ``.card`` note text from the same
    per-conversation record projection. Read cost is real (``read_records``
    loads the whole conversation log, not a tail) and this now pays it once
    per active run per publish tick (~25-30s) instead of only on card
    writes — acceptable for a first cut, worth revisiting with a tailed
    read if a busy thread's log makes this tick hot. Budget/keepalive
    posture is deliberately *not* included here: that state lives only in
    the worker's in-memory loop today (``daemon.py``'s
    ``_keepalive_until``/budget tracking), nothing persists it yet, so it
    would need new state-threading, not just a read — named as the
    remaining gap rather than guessed at.
    """
    out: list[dict[str, Any]] = []
    for entry in presence.list_active(brr_dir):
        stream = str(entry.get("stream") or "")
        run_id = str(entry.get("run_id") or "")
        view = _live_run_progress(brr_dir, stream, run_id)
        manifest = _live_run_manifest(brr_dir, run_id)
        lifecycle, await_until = _lifecycle_payload(brr_dir, manifest, view)
        out.append(
            _bounded_live_run({
                "id": str(entry.get("id") or ""),
                "kind": str(entry.get("kind") or ""),
                "stream": stream,
                "label": str(entry.get("label") or ""),
                "name": str(entry.get("name") or ""),
                "run_id": run_id,
                "repo_label": str(entry.get("repo_label") or ""),
                "started_at": _iso_from_epoch(entry.get("started_at")),
                "last_seen": _iso_from_epoch(entry.get("last_seen")),
                # Joins the live view to the same parent/child shape the
                # closed-run ledger already carries (run_ledger.py's
                # `parent_run_id`/`is_subspawn`) — named as a gap and
                # closed in kb/design-multi-workstream-concurrency.md
                # "Ranked moves" #1: a running `spawn:` child is now
                # distinguishable from a resident thought *while it's
                # still live*, not only after it closes into the ledger.
                "parent_run_id": str(entry.get("parent_run_id") or "") or None,
                "is_subspawn": bool(entry.get("is_subspawn")),
                # Shell+Core the running thought is on — same
                # name/shell/core/class shape `_runner_payload` already
                # produces for Activity/respawn rows, now carried on the
                # presence entry itself (`presence.register`'s runner_*
                # kwargs) so the live view can answer "which Runner is this"
                # while a run is still in flight, not only after it closes
                # into the ledger (brnrd.dev live-run dashboard posture,
                # 2026-07-13). ``{}`` when the entry predates this field or
                # no runner was selected yet (ad-hoc session presence).
                "runner": _runner_payload(entry),
                # #200 remaining slice: live phase + progress-card note,
                # None when there's no conversation record yet (a
                # just-registered entry) or projection failed.
                "phase": (view.phase if view is not None else None) or None,
                "card_text": (view.agent_card_text if view is not None else None) or None,
                "card_updated_at": (view.agent_card_updated_at if view is not None else None) or None,
                # #342: relics-so-far. Joined by ``project_run`` from the
                # daemon-refreshed portal capsule (``relics.live_portal_counts``)
                # — the git derivation ran once on the daemon heartbeat, so
                # this publish tick pays one small JSON read per run, never
                # per-tick git work. ``None`` = nothing attested (ad-hoc
                # session, no capsule yet); ``{}`` = known, no produce yet.
                "relics_counts": (view.relics_counts if view is not None else None),
                # #566 slice 0: resident-authored mood. The raw handle rides
                # from the presence entry (heartbeat-refreshed from `.mood`);
                # glyph and pitch are resolved *here*, where `brr.emotes`
                # exists, so the frontend never owns an emote table and an
                # unknown handle degrades to name-only — never a guessed
                # face (the library's honesty bar).
                **_mood_payload(entry),
                # the-run-that-claims-its-thread, live-read steer: the
                # resident's claimed topic slugs, same heartbeat-refreshed
                # pass-through as `mood` — raw slugs only, no resolution
                # here (there's nothing to resolve; unlike mood there's no
                # glyph lookup). `[]` for an entry that predates this field
                # or has claimed nothing yet.
                "topics": [
                    str(slug) for slug in (entry.get("topics") or [])
                    if isinstance(slug, str)
                ],
                # the-overlay-that-shows-the-room: where the work happens,
                # published rather than guessed browser-side. All three are
                # derived from run-node state the daemon already writes —
                # the manifest, the boundary transcript's redacted tail,
                # and the portal capsule's `await` facet. `None` everywhere
                # for an ad-hoc session (no run dir) — absent stays absent.
                "lifecycle": lifecycle,
                "await_until": await_until,
                "room": _room_payload(brr_dir, manifest) if manifest else None,
                "edge": _edge_payload(brr_dir, run_id, manifest),
                # the-field-takes-its-body: the message-ceremony fact — how
                # many pending events stand at this run's portal and when
                # the oldest arrived, so a sent message can render as
                # *resting, put to read* until the boundary that folds it
                # in attests the read. Counts only, never bodies.
                "portals": _portals_payload(brr_dir, manifest) if manifest else None,
            })
        )
    return out


def _mood_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a presence entry's mood handle into wire fields.

    ``mood`` is the raw resident-authored handle (or ``None``); ``mood_glyph``,
    ``mood_frames`` and ``mood_pitch`` are present only when the handle resolves
    in the emote library — absent resolution is absent data, not a default face.

    ``mood_frames`` is every breath the face can take (``Emote.sequences``: the
    primary cycle first, then any alternates), each a base→expression→base run
    of equal-width glyphs. It exists because this function used to publish
    ``frames[0]`` alone while ``_daemon_mood_payload`` four lines below
    published the whole list — so the daemon's *derived* face animated on the
    dashboard and the resident's *authored* one could not. Worse than static:
    ``frames[0]`` is the resting frame by the library's own rule, and across
    the 98 situational emotes there are 15 distinct resting frames, 61 of them
    the same ``b·_·d`` — the wire was collapsing the whole palette onto a
    handful of neutral faces. The expression was never leaving this process.

    ``mood_glyph`` stays for the surfaces that genuinely cannot move (and for a
    dashboard deployed before this field existed), but it is the *resting*
    frame and a renderer with ``mood_frames`` should prefer those.
    """
    handle = str(entry.get("mood") or "").strip() or None
    payload: dict[str, Any] = {
        "mood": handle,
        "mood_glyph": None,
        "mood_frames": None,
        "mood_rest": None,
        "mood_pitch": None,
    }
    if handle:
        emote = emotes.lookup(handle)
        if emote is not None:
            payload["mood_glyph"] = emote.frames[0]
            payload["mood_frames"] = [list(seq) for seq in emote.sequences]
            # What the chip holds between flickers. Not ``frames[0]``: that is
            # the animation's base and is shared across a whole face family,
            # so a surface resting on it says "a mood is set here" without
            # saying which. ``Emote.resting_frame`` is the library's answer to
            # "what does this face look like while still".
            payload["mood_rest"] = emote.resting_frame
            payload["mood_pitch"] = emote.pitch
    return payload


def _daemon_mood_payload(brr_dir: Path) -> dict[str, Any] | None:
    """The daemon's own telemetry face: what the board wears at rest.

    Derived, not authored: ``idle`` when nothing is live, ``running`` when
    any run-kind presence is active. Deliberately the two-state floor —
    richer states (quota_starved, failing, …) belong to whoever computes
    them, and inventing them here from partial signals would break the
    honesty bar the emote library states for itself.
    """
    active = [e for e in presence.list_active(brr_dir) if str(e.get("run_id") or "")]
    state = "running" if active else "idle"
    emote = emotes.for_telemetry(state)
    if emote is None:
        return None
    return {
        "state": state,
        "name": emote.name,
        "glyph": emote.frames[0],
        "frames": list(emote.frames),
        # Alternates too, on the same shape the per-run mood now uses: a
        # board that sits at ``idle`` for hours is exactly where one
        # repeating cycle reads like a spinner instead of a body.
        "sequences": [list(seq) for seq in emote.sequences],
        "rest": emote.resting_frame,
        "pitch": emote.pitch,
    }


def _spawn_pool_width(brr_dir: Path) -> int:
    """Configured ``spawn:`` pool width (``spawn.max_concurrent``), for the
    loom-envelope Phase 1 limits panel (`kb/design-multi-workstream-
    concurrency.md` §"Loom envelope").

    Piggybacked on the live-runs publish tick rather than a new endpoint —
    the *active* count is already derivable from ``is_subspawn`` entries in
    ``_live_runs_snapshot`` above, this is the one number that publish
    doesn't already carry. Reuses ``daemon._max_concurrent_spawns``'s own
    clamped-default parsing via a deferred import rather than duplicating
    it: ``daemon.py`` already does a deferred ``from .gates import cloud``
    (see its own comment there), so importing the other direction here has
    to stay deferred too, executed at runtime after both modules are
    fully loaded, not at import time.
    """
    from .. import config as _config
    from ..daemon import _max_concurrent_spawns

    cfg = _config.load_config(brr_dir.parent)
    return _max_concurrent_spawns(cfg)


def _dispatch_run_stops(
    brr_dir: Path, inbox_dir: Path | None, requests: list, responses_dir: Path | None = None,
) -> None:
    """Apply user-issued stops served on the live-runs publish (#476).

    The seam where a dashboard tap becomes a dead process. Everything about
    *how* to stop a run lives in ``daemon._apply_run_stop`` — the same
    function the ``stop:`` outbox verb reaches — so this is only routing:
    resolve the handle against the daemon's control registry, dispatch, and
    record the ack.

    Authority is already settled by the time a request gets here. The server
    scopes the tap to the account's own live runs; this side only kills what
    is in *this* daemon's registry, which is by construction only runs it
    dispatched. Unlike the ``stop:`` verb there is no dispatch-edge check: a
    human account owner is not a run, and the rule that stops a run reaching
    sideways to kill a sibling would, applied here, refuse the owner access
    to their own resident thought — the exact case this affordance exists
    for (see ``brnrd/routers/dashboard.py::dashboard_run_stop``).
    """
    from ..daemon import _apply_run_stop, _find_run_control

    for request in requests:
        run_id = request["run_id"]
        control = _find_run_control(run_id)
        if control is None:
            # Already finished, or never ran on this daemon. Ack it anyway:
            # leaving it pending would re-serve a stop for a run that no
            # longer exists on every tick until its TTL.
            run_stop_request.record_consumed(brr_dir, request["request_id"])
            print(f"[brnrd:cloud] stop {run_id}: no live run, nothing to kill")
            continue
        stage = _apply_run_stop(
            control,
            inbox_dir,
            stopped_by="user",
            reason="stopped from the dashboard",
            # #1389: lets a resident's own waking event carry the utterance
            # sweep's aggregate reply. #1396/#1437: this must be the real
            # responses dir threaded down from `cloud.run_loop`, not a
            # rebuilt `brr_dir / "responses"` — in account mode those
            # diverge (the cloud gate's `brr_dir` is the default repo's
            # `.brr`, its responses dir is the account's), and a reply
            # landing in a directory no gate thread polls closes the event
            # (`_set_event_status_if_present(anchor, "done")` still fires)
            # while delivering nothing, with no error anywhere.
            responses_dir=responses_dir if responses_dir is not None else brr_dir / "responses",
        )
        run_stop_request.record_consumed(brr_dir, request["request_id"])
        print(f"[brnrd:cloud] stop {run_id} ({stage}) by account owner")


def _report_live_runs_losses(body: Any) -> None:
    """Print what the live-runs publish lost, if anything (#685 guard C).

    Silent on a clean publish — this loop runs every
    ``_DASHBOARD_PUBLISH_INTERVAL_S`` and a per-tick line would be noise that
    trains a reader to stop reading. Tolerant of an older API that does not
    send these fields yet: absent means nothing to say, not a crash.
    """
    if not isinstance(body, dict):
        return
    rejected = body.get("runs_rejected")
    if isinstance(rejected, list) and rejected:
        for row in rejected[:8]:
            if not isinstance(row, dict):
                continue
            print(
                "[brnrd:cloud] live-runs row dropped: "
                f"id={row.get('id')!r} fields={row.get('fields')} "
                f"— {row.get('detail')}"
            )
    truncated = body.get("fields_truncated")
    if isinstance(truncated, list) and truncated:
        print(
            "[brnrd:cloud] live-runs fields truncated server-side: "
            + ", ".join(str(item) for item in truncated[:16])
        )


@_publish_lane("live_runs")
def _publish_live_runs(brr_dir: Path, inbox_dir: Path | None, state: dict, responses_dir: Path) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    # #476 wyrd §3: ack the stops already dispatched into the kill path, and
    # pick up any the account has parked since the last tick. Same publish
    # tick, no extra request — the same piggyback economics as #328's
    # wake requests on the catalog publish.
    acked = run_stop_request.consumed_ids(brr_dir)
    try:
        body = _context().request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/live-runs",
            token=state["token"],
            json={
                "runs": _live_runs_snapshot(brr_dir),
                "spawn_max_concurrent": _spawn_pool_width(brr_dir),
                # #566 slice 0: the daemon-level telemetry face for the
                # board at rest — the NOW seam and wordmark need a face
                # precisely when no run exists to carry one. First caller
                # of `emotes.for_telemetry` (the layer shipped in #601 and
                # never wired). Same piggyback economics as
                # `spawn_max_concurrent` above: one field on a publish that
                # already happens.
                "daemon_mood": _daemon_mood_payload(brr_dir),
                "consumed_run_stop_request_ids": acked,
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] live-runs publish failed: {e}")
        return
    # #685 ask 2, guard C: a 200 that stored nothing is the same outage with a
    # friendlier face, and an absent reading renders as "fine" (#632). The
    # server now tells us what it refused and what it shortened; say it in the
    # same neighbourhood as the failure line above, on the success path.
    _report_live_runs_losses(body)
    run_stop_request.clear_consumed(brr_dir, acked)
    served = body.get("pending_run_stop_requests") if isinstance(body, dict) else None
    pending = run_stop_request.unhandled(
        brr_dir, served if isinstance(served, list) else [],
    )
    if pending:
        _dispatch_run_stops(brr_dir, inbox_dir, pending, responses_dir)


def _github_repo_label(label: str, repo_root: Path) -> str | None:
    try:
        remote = gitops.default_remote(repo_root)
        if remote:
            url = gitops.remote_url(repo_root, remote)
            if url:
                parsed = parse_origin_url(url)
                if parsed:
                    return parsed
    except Exception:
        pass
    text = str(label or "").strip()
    if text.count("/") == 1 and all(part.strip() for part in text.split("/", 1)):
        return text
    return None


def _pr_review_repo_labels(brr_dir: Path) -> list[str]:
    from .. import account as account_mod

    repo_root = brr_dir.parent
    try:
        ctx = account_mod.resolve_context(repo_root, create=False)
        repos = ctx.repos
    except Exception:
        repos = {account_mod.repo_label(repo_root): account_mod.AccountRepo(label=account_mod.repo_label(repo_root), root=repo_root)}

    out: list[str] = []
    seen: set[str] = set()
    for label, repo in sorted(repos.items()):
        repo_label = _github_repo_label(label, repo.root)
        if repo_label is None:
            continue
        key = repo_label.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(repo_label)
    return out


def _pr_review_snapshot(brr_dir: Path) -> list[dict[str, Any]]:
    """This daemon's account-scoped open-PR review queue (#259).

    Mirrors the Activity/Plans/Quota/Live-runs publish shape: collect local
    daemon evidence with the same ``gh`` dependency the director tick already
    uses, then let brnrd store the latest snapshot. The dashboard derives age
    from ``created_at``; this layer deliberately does not manufacture urgency.
    """
    prs: list[dict[str, Any]] = []
    for repo_label in _context().pr_review_repo_labels(brr_dir):
        cmd = [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,url,createdAt,isDraft,author,headRefName",
            "--repo",
            repo_label,
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=brr_dir.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("gh not found; install/authenticate GitHub CLI to publish PR review queue") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"gh pr list timed out for {repo_label}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"gh pr list failed for {repo_label}: {detail}")
        try:
            rows = json.loads(result.stdout or "[]")
        except ValueError as exc:
            raise RuntimeError(f"gh pr list returned invalid JSON for {repo_label}") from exc
        if not isinstance(rows, list):
            raise RuntimeError(f"gh pr list returned non-list JSON for {repo_label}")
        for row in rows:
            if not isinstance(row, dict):
                continue
            author = row.get("author")
            author_login = str(author.get("login") or "") if isinstance(author, dict) else str(author or "")
            number = row.get("number")
            try:
                number_int = int(number)
            except (TypeError, ValueError):
                continue
            prs.append(
                {
                    "number": number_int,
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                    "repo_label": repo_label,
                    "created_at": str(row.get("createdAt") or ""),
                    "draft": bool(row.get("isDraft")),
                    "author": author_login,
                }
            )
    return prs


@_publish_lane("pr_review_queue")
def _publish_pr_review_queue(brr_dir: Path, inbox_dir: Path | None, state: dict, responses_dir: Path) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    try:
        _context().request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/pr-review-queue",
            token=state["token"],
            json={"prs": _pr_review_snapshot(brr_dir)},
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] pr-review-queue publish failed: {e}")


# Covers the loom's declared seven-day shelf at the observed ~25 runs/day,
# with headroom for bursts, without turning every 3s publish into full history.
_RUN_LEDGER_SNAPSHOT_LIMIT = 256


def _run_ledger_snapshot(brr_dir: Path) -> list[dict[str, Any]]:
    """This daemon's recent closed-run receipt rows (#271).

    Reads the local-first ``.brr/run-ledger.jsonl`` written at run closeout.
    Missing files and malformed lines are not publish failures: the ledger
    invariant is "unavailable evidence becomes absent/null, not a closeout or
    dashboard failure."
    """
    path = run_ledger.ledger_path(brr_dir.parent)
    rows: deque[dict[str, Any]] = deque(maxlen=_RUN_LEDGER_SNAPSHOT_LIMIT)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        return []
    return list(rows)


@_publish_lane("run_ledger")
def _publish_run_ledger(brr_dir: Path, inbox_dir: Path | None, state: dict, responses_dir: Path) -> None:
    if not (state.get("token") and state.get("brnrd_url")):
        return
    try:
        _context().request(
            state["brnrd_url"],
            "PUT",
            "/v1/daemons/run-ledger",
            token=state["token"],
            json={"rows": _run_ledger_snapshot(brr_dir)},
            timeout=10,
        )
    except Exception as e:
        print(f"[brnrd:cloud] run-ledger publish failed: {e}")
