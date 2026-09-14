"""The HUD — one typed shape for ``portal-state.json``.

Move 5 of the daemon rewrite (``design-the-loom.md`` §17, row *the HUD
itself*). ``daemon._write_live_portal_state`` used to assemble the portal as a
hand-built dict from ~22 parameters: a signature used as a data structure, and
a JSON file every reader (the hooks' chip, ``brnrd do``, ``brnrd await``, the
dashboard, every strand) parsed without a type to parse it into. This module is
that type.

- :class:`HUD` — one frozen field per top-level key the portal carries, with
  nested frozen dataclasses where the writer always built the same fixed
  shape, and plain ``dict`` where the shape is genuinely open (``resources``,
  ``delivery``, ``scm``, ``produce``, ``await``, ``resource_hold``).
- :class:`HUDInputs` — the writer's parameters as one value.
- :func:`build` — the collection the writer did, moved here verbatim (reads,
  and the heartbeat's own side effects: movement stamps, boot cost, the await
  and starvation resolutions), ending in a :class:`HUD`.
- :func:`write` — ``HUD.to_json()`` onto ``<outbox>/portal-state.json``,
  atomically.

``HUD.to_json()`` is byte-identical to the dict writer it replaced (the
goldens in ``tests/test_hud_golden.py`` were captured from the unsplit writer
on ``main``). ``HUD.from_json()`` is for readers.

The schema is the frame's, fixed and versioned (``version``); nobody edits a
sense. ``from_json`` is lenient about *absence* (an old portal without
``tick`` reads as ``None``) and drops keys the schema does not name.

Import cost matters: ``brr.hooks`` runs in a fresh subprocess per boundary and
reads this module, so nothing here imports :mod:`brr.daemon` at module level.
"""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from . import account
    from .run import Run

#: The portal's file name inside a run's outbox.
PORTAL_STATE_NAME = "portal-state.json"

#: The schema version the frame writes.
VERSION = 1


def _plain(value: Any) -> Any:
    """A field value as the JSON payload carries it (nested shapes unfolded)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return value.to_dict()
    return value


class _Shape:
    """``to_dict`` / ``from_dict`` for a fixed sub-shape: its fields are its keys."""

    def to_dict(self) -> dict[str, Any]:
        return {f.name: _plain(getattr(self, f.name)) for f in dataclasses.fields(self)}

    @classmethod
    def from_dict(cls, raw: Any):
        if not isinstance(raw, dict):
            return None
        return cls(**{f.name: raw.get(f.name, _default_of(f)) for f in dataclasses.fields(cls)})


def _default_of(f: dataclasses.Field) -> Any:
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return f.default_factory()  # type: ignore[misc]
    return None


@dataclass(frozen=True)
class Tick(_Shape):
    """The frame's beat this capsule belongs to (move 2b)."""

    n: int
    at: str


@dataclass(frozen=True)
class RunFacet(_Shape):
    """``run`` — which run, in which phase, on which Runner."""

    id: str
    event_id: str
    status: str
    phase: str
    attempt: int | None
    env: str
    runner: str | None
    repo: str | None
    branch: str | None


@dataclass(frozen=True)
class Attention(_Shape):
    """``attention`` — is anything waiting on the run."""

    pending_event_count: int
    pending_outbox_file_count: int
    needs_attention: bool


@dataclass(frozen=True)
class Inbound(_Shape):
    """``inbound`` — the waking event and what else is pending for this run."""

    current_event: str
    current_event_replyable: bool
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Outbound(_Shape):
    """``outbound`` — what the run has delivered and what is still staged."""

    replies_current: int
    replies_other: int
    outbound_messages: int
    any_sent: bool
    pending_outbox_files: list[str] = field(default_factory=list)
    oldest_pending_age_seconds: float | None = None


@dataclass(frozen=True)
class Card(_Shape):
    """``card`` — the run body's write-head and how far behind the run it is."""

    active: bool
    text: str
    age_seconds: int | None
    stale: bool
    state_moved_seconds: int | None
    #: Move 5b (design-the-loom §19.2): the frame-drafted "since your last
    #: card write" item ``{id, text, at, trigger}`` — ``note: <id>`` accepts
    #: it, a card edit folds it in (``card_frame``). ``None`` when none stands.
    delta: dict[str, Any] | None = None


@dataclass(frozen=True)
class Budget(_Shape):
    """``budget`` — elapsed seconds since the run started (no clock kills a run)."""

    elapsed_seconds: int | None


@dataclass(frozen=True)
class ShuttleFacet(_Shape):
    """``shuttle`` — the actor's state (move 2), when a home was given."""

    state: str
    why: str
    since: str
    run_id: str


@dataclass(frozen=True)
class Strand(_Shape):
    """``strand`` — is this run a strand, and has it submitted."""

    is_strand: bool
    submitted: bool


@dataclass(frozen=True)
class Seat(_Shape):
    """``seat`` — whether the daemon parks this seat when its turn ends."""

    parks_on_turn_end: bool


@dataclass(frozen=True)
class Schedule(_Shape):
    """``schedule`` — the still-armed dated letters (#904)."""

    armed: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Knowledge(_Shape):
    """``knowledge`` — where kb pages link to."""

    kb_base_url: str | None


@dataclass(frozen=True)
class Name(_Shape):
    """``name`` — whether the run's ``.name`` control file is written."""

    written: bool


@dataclass(frozen=True)
class Bolt(_Shape):
    """``bolt`` — present only once a ``cut:`` was accepted this run."""

    accepted: bool
    annotated: int
    accepted_at: str | None


#: The frame's typed produce rows (``land:`` writes them, #1975), one file per
#: run under ``<brr_dir>/runs/<run_id>/``. ``outbox.land`` spells its path
#: with this name.
PRODUCE_LEDGER_NAME = "produce.jsonl"

#: The four kinds of produce the loom draws (``design-the-loom.md`` §3).
LOOM_KINDS = ("knot", "heddle", "card", "page")

#: Today's relic kinds, read as the loom's: a commit or a merge is a knot; a
#: branch is a heddle and a PR is a raised one; an issue or a warp item is a
#: card; a kb page is a page. ``comment``, ``message`` and ``file`` have no
#: loom kind yet — counted as ``unmapped``, never guessed into one.
RELIC_LOOM_KIND = {
    "commit": "knot", "merge": "knot",
    "branch": "heddle", "pr": "heddle",
    "issue": "card", "item": "card",
    "kb": "page",
}

#: How many refs ``produce.ledger.last`` carries.
LEDGER_LAST = 5


def relic_ref(record: dict[str, Any]) -> str | None:
    """The coordinate a relic names: a sha, a branch, ``#n``, an address, a path."""
    kind = str(record.get("kind") or "")
    if kind in {"commit", "merge"}:
        value = record.get("sha")
    elif kind == "branch":
        value = record.get("name")
    elif kind in {"pr", "issue"}:
        number = record.get("number")
        value = f"#{number}" if number not in (None, "") else None
    elif kind == "item":
        value = record.get("address")
    else:
        value = record.get("path")
    text = str(value or "").strip()
    return text or None


def read_produce_ledger(brr_dir: Path | None, run_id: str | None) -> list[dict[str, Any]]:
    """Every parseable row of this run's ``produce.jsonl``, in file order. Never raises."""
    if brr_dir is None or not run_id:
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = (Path(brr_dir) / "runs" / run_id / PRODUCE_LEDGER_NAME).read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _same_ref(kind: str, a: str, b: str) -> bool:
    if a == b:
        return True
    # A knot's sha may be abbreviated on one side (relics derive short shas;
    # ``land:`` writes the full one): one is a prefix of the other, 7+ chars.
    if kind == "knot":
        short, long_ = sorted((a, b), key=len)
        return len(short) >= 7 and long_.startswith(short)
    return False


def project_produce(
    ledger_rows: list[dict[str, Any]],
    relic_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """``produce.ledger`` — the run's produce as the loom's four kinds.

    Two sources, one projection. The frame's typed rows (``produce.jsonl``)
    come first; today's relics (the records ``relics.live_summary`` already
    compiled, never ``.relics.jsonl`` read a second time) are folded in under
    :data:`RELIC_LOOM_KIND`. A relic naming the same ``(kind, ref)`` as a
    frame row is the same produce: ``land:`` writes both a ``knot`` row and a
    ``merge`` relic from one read, and it counts once.

    ``last`` is the newest :data:`LEDGER_LAST` refs: frame rows by ``at``
    (newest first), then relics in manifest order — relics carry no time, so
    they never outrank a stamped row.
    """
    entries: list[dict[str, Any]] = []
    unmapped = 0

    def _admit(kind: str, ref: str, at: Any, source: str) -> None:
        for seen in entries:
            if seen["kind"] == kind and _same_ref(kind, seen["ref"], ref):
                return
        entries.append({"kind": kind, "ref": ref, "at": at, "source": source})

    stamped = [r for r in ledger_rows if isinstance(r.get("at"), str)]
    unstamped = [r for r in ledger_rows if not isinstance(r.get("at"), str)]
    for row in sorted(stamped, key=lambda r: r["at"], reverse=True) + unstamped:
        kind = str(row.get("kind") or "")
        ref = str(row.get("ref") or "").strip()
        if kind not in LOOM_KINDS or not ref:
            unmapped += 1
            continue
        _admit(kind, ref, row.get("at"), "frame")
    for record in relic_records:
        if not isinstance(record, dict):
            continue
        kind = RELIC_LOOM_KIND.get(str(record.get("kind") or ""))
        ref = relic_ref(record)
        if kind is None or ref is None:
            unmapped += 1
            continue
        _admit(kind, ref, None, "relic")
    counts = {kind: 0 for kind in LOOM_KINDS}
    for entry in entries:
        counts[entry["kind"]] += 1
    return {
        "counts": counts,
        "last": entries[:LEDGER_LAST],
        "unmapped": unmapped,
        "sources": {
            "frame": sum(1 for e in entries if e["source"] == "frame"),
            "relic": sum(1 for e in entries if e["source"] == "relic"),
        },
    }


def with_ledger(
    produce: dict[str, Any], brr_dir: Path | None, run_id: str | None,
) -> dict[str, Any]:
    """*produce* (the relics facet) plus its ``ledger`` projection; never mutates it."""
    records = produce.get("records") if produce.get("known") else None
    return {
        **produce,
        "ledger": project_produce(
            read_produce_ledger(brr_dir, run_id),
            records if isinstance(records, list) else [],
        ),
    }


#: HUD field name → portal key, where Python's grammar forced a rename.
_KEY_OF = {"await_": "await"}
#: HUD fields omitted from the payload when ``None`` (the key is absent, not null).
_OMIT_WHEN_NONE = frozenset({"bolt"})
#: Nested shapes, by HUD field name.
_SHAPE_OF: dict[str, type[_Shape]] = {
    "tick": Tick, "run": RunFacet, "attention": Attention, "inbound": Inbound,
    "outbound": Outbound, "card": Card, "budget": Budget, "shuttle": ShuttleFacet,
    "strand": Strand, "seat": Seat, "schedule": Schedule, "knowledge": Knowledge,
    "name": Name, "bolt": Bolt,
}


@dataclass(frozen=True)
class HUD:
    """``portal-state.json`` as one value — the chip, the portal and the verb read this.

    Field order is the order the writer evaluated them in (it still is: see
    :func:`build`); the JSON is written with sorted keys, so order is not
    part of the bytes.
    """

    version: int
    generated_at: str
    tick: Tick | None
    run: RunFacet
    attention: Attention
    notices: list[dict[str, Any]]
    inbound: Inbound
    outbound: Outbound
    delivery: dict[str, Any]
    card: Card
    budget: Budget
    await_: dict[str, Any]
    shuttle: ShuttleFacet | None
    strand: Strand
    seat: Seat
    resource_hold: dict[str, Any] | None
    scm: dict[str, Any] | None
    produce: dict[str, Any]
    schedule: Schedule
    knowledge: Knowledge
    name: Name
    resources: dict[str, Any]
    #: Move 5b (design-the-loom §20): the lit heddles, brightest first —
    #: ``{slug, rune, brightness, last_match_at, matched_by}`` — scored on the
    #: heartbeat (``daemon._frame_heartbeat`` → ``heddles.light``) and read
    #: here from the stash, so the boundary flush pays nothing for them.
    heddles: list[dict[str, Any]] = field(default_factory=list)
    bolt: Bolt | None = None
    change_token: str = ""

    # ── the payload ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """The portal payload. ``bolt`` is omitted until a bolt was accepted."""
        out: dict[str, Any] = {}
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if value is None and f.name in _OMIT_WHEN_NONE:
                continue
            out[_KEY_OF.get(f.name, f.name)] = _plain(value)
        return out

    def to_json(self) -> str:
        """The exact text ``portal-state.json`` holds."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str) + "\n"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HUD":
        """A reader's HUD from a parsed portal payload (lenient about absence)."""
        if not isinstance(raw, dict):
            raise TypeError(f"a HUD is read from a JSON object, not {type(raw).__name__}")
        values: dict[str, Any] = {}
        for f in dataclasses.fields(cls):
            key = _KEY_OF.get(f.name, f.name)
            value = raw.get(key, _default_of(f))
            shape = _SHAPE_OF.get(f.name)
            if shape is not None:
                value = shape.from_dict(value)
            values[f.name] = value
        return cls(**values)

    @classmethod
    def from_json(cls, text: str | bytes) -> "HUD":
        """A reader's HUD from ``portal-state.json``'s text."""
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: Path | str) -> "HUD | None":
        """Read a portal file; ``None`` when it is missing or not a HUD."""
        try:
            return cls.from_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None


# ── the verb's renderings (``brnrd hud``) ────────────────────────────


def render_bar(hud: HUD, *, outbox_dir: Path | None = None) -> str:
    """The chip's bar line, rendered from *hud* by the hooks' own renderer.

    The same ``hooks.format_delta`` → ``hooks._render_bar`` path a mid-run
    boundary takes, on this object's payload, with every chip due (no prior
    bar to diff against). What is not in the HUD — the mood, the course, the
    room, which the hook reads from the run's own control files — is not
    rendered here, so this line is the HUD's share of the bar, not a replay of
    the last one injected.
    """
    from . import hooks

    line = hooks.format_delta(hud.to_dict(), outbox_dir=outbox_dir)
    return line or ""


def render_produce(hud: HUD) -> str:
    """What the frame attests: ``produce.ledger``, one row per ref, then the relic counts.

    For comparing against the card's *Produce* section, which the resident
    writes; this is the other book.
    """
    produce = hud.produce if isinstance(hud.produce, dict) else {}
    ledger = produce.get("ledger") if isinstance(produce.get("ledger"), dict) else None
    lines: list[str] = []
    if ledger is None:
        lines.append("ledger: absent (a portal written before move 5)")
    else:
        counts = ledger.get("counts") if isinstance(ledger.get("counts"), dict) else {}
        lines.append(
            "ledger: " + " · ".join(f"{kind} {int(counts.get(kind) or 0)}" for kind in LOOM_KINDS)
            + f" · unmapped {int(ledger.get('unmapped') or 0)}"
        )
        sources = ledger.get("sources") if isinstance(ledger.get("sources"), dict) else {}
        lines.append(
            f"sources: frame {int(sources.get('frame') or 0)} · relic {int(sources.get('relic') or 0)}"
        )
        for entry in ledger.get("last") or []:
            if not isinstance(entry, dict):
                continue
            at = f" @ {entry['at']}" if entry.get("at") else ""
            lines.append(f"  {entry.get('kind')} {entry.get('ref')} ({entry.get('source')}){at}")
    if produce.get("known"):
        counts = produce.get("counts") if isinstance(produce.get("counts"), dict) else {}
        lines.append(
            "relics: " + (" · ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none")
        )
    else:
        lines.append("relics: unknown (no work tree measured)")
    return "\n".join(lines)


@dataclass(frozen=True)
class HUDInputs:
    """Everything a HUD is built from — the writer's parameters as one value.

    Built at the worker's call sites (``worker/prepare``, ``worker/dispatch``,
    ``worker/stream``); ``daemon._write_live_portal_state`` keeps its old
    signature and builds one of these. Mutable collaborators the throw shares
    by reference (the ``Run``, ``card_state``, ``output_stats``) stay
    references: frozen is a statement about the seam, not a deep copy.
    """

    outbox_dir: Path | None
    inbox_dir: Path
    current_event_id: str
    task: "Run"
    phase: str
    attempt: int | None = None
    runner_name: str | None = None
    runner_meta: dict[str, object] | None = None
    runner_catalog: list[dict[str, object]] | None = None
    quality_escalation: dict[str, object] | None = None
    relay_consent: dict[str, object] | None = None
    card_state: dict[str, object] | None = None
    output_stats: dict[str, int] | None = None
    start_monotonic: float | None = None
    work_dir: Path | None = None
    quota_summary: str | None = None
    refresh_levels: bool = True
    cfg: dict | None = None
    brr_dir: Path | None = None
    account_context: "account.AccountContext | None" = None
    repo_label: str | None = None
    shuttle_home: Path | None = None


def build(inputs: HUDInputs) -> HUD:
    """Collect this tick's HUD. Moved verbatim from the dict writer on ``main``.

    Not a pure read: exactly the side effects the writer had, in the same
    order — ``outbox_dir`` created, the run's movement stamp, the boundary
    levels on the run ledger, the boot-cost and context-window control
    writes, the await/hold/starvation resolutions on ``task.meta``. Every
    helper still defined in :mod:`brr.daemon` is looked up there at call time
    (``daemon.<name>``), so ``monkeypatch.setattr(daemon, …)`` still lands.

    Raises ``OSError`` like the writer's body did; :func:`write_live` is the
    caller that swallows it.
    """
    from . import card_frame
    from . import correspondent as correspondent_mod
    from . import daemon, presence, protocol, relics, resource_hold, run_ledger, shuttle
    from . import schedule as schedule_mod
    from . import tick as tick_mod

    outbox_dir = inputs.outbox_dir
    inbox_dir = inputs.inbox_dir
    current_event_id = inputs.current_event_id
    task = inputs.task
    phase = inputs.phase
    attempt = inputs.attempt
    runner_name = inputs.runner_name
    runner_meta = inputs.runner_meta
    runner_catalog = inputs.runner_catalog
    quality_escalation = inputs.quality_escalation
    relay_consent = inputs.relay_consent
    card_state = inputs.card_state
    output_stats = inputs.output_stats
    start_monotonic = inputs.start_monotonic
    work_dir = inputs.work_dir
    quota_summary = inputs.quota_summary
    refresh_levels = inputs.refresh_levels
    cfg = inputs.cfg
    brr_dir = inputs.brr_dir
    account_context = inputs.account_context
    repo_label = inputs.repo_label
    shuttle_home = inputs.shuttle_home

    outbox_dir.mkdir(parents=True, exist_ok=True)
    events = daemon._pending_events_for_agent(
        inbox_dir, current_event_id,
        strand=daemon._is_strand(task.meta) if hasattr(task, "meta") else False,
        account_context=account_context,
        repo_label=repo_label,
        observer_run_id=task.id,
    )
    await_state = daemon._resolve_await_state(
        task, events,
        outbox_dir=outbox_dir,
        shuttle_home=shuttle_home,
    )
    # The bolt (design-the-bolt.md): absent until a `cut:` is accepted
    # this run — sibling work reads exactly this shape, so the key
    # itself is omitted rather than carrying a `None`/`accepted: false`
    # placeholder for a run that was never cut.
    bolt_meta = (
        task.meta.get("bolt")
        if hasattr(task, "meta") and isinstance(task.meta.get("bolt"), dict)
        else None
    )
    bolt_state = (
        {
            "accepted": True,
            "annotated": int(bolt_meta.get("annotated") or 0),
            "accepted_at": bolt_meta.get("accepted_at"),
        }
        if bolt_meta else None
    )
    stats = output_stats or {}
    card_text = (card_state or {}).get("last", "")
    pending_files = daemon._outbox_message_files(outbox_dir)
    # Oldest-pending age (#1379): free here — ``pending_files`` is
    # already sorted oldest-mtime-first by ``_outbox_message_files``, so
    # the first entry (if any) is the oldest staged-but-undrained file
    # without a second directory scan. ``do.py``'s ``await_verdict``
    # QUEUED branch surfaces this so "still queued" can say *how long*
    # instead of reading identically whether the daemon is 2 seconds or
    # 40 minutes behind — the latter being exactly the poison-file wedge
    # this age exists to make visible even after #1379's per-file guard
    # stops it from ever reaching zero throughput.
    oldest_pending_age_seconds: float | None = None
    if pending_files and outbox_dir is not None:
        try:
            oldest_pending_age_seconds = max(
                0.0,
                time.time() - (outbox_dir / pending_files[0]).stat().st_mtime,
            )
        except OSError:
            oldest_pending_age_seconds = None
    elapsed = (
        int(time.monotonic() - start_monotonic)
        if start_monotonic is not None else None
    )
    # Card age: seconds since the note last changed (write *or*
    # withdrawal), falling back to the run's own start when the card
    # has never been touched — a wake that never writes a note is, from
    # the watching user's side, indistinguishable from one whose note
    # went stale the moment it woke up.
    card_written_monotonic = (
        (card_state or {}).get("written_monotonic") or start_monotonic
    )
    card_age = (
        time.monotonic() - card_written_monotonic
        if isinstance(card_written_monotonic, (int, float)) else None
    )
    live_branch, live_seed = (
        relics.collection_scope(task.meta, Path(work_dir))
        if work_dir else (None, None)
    )
    # collection_scope is rename-aware (#1293): once a mid-run
    # ``git branch -m`` moves the worktree off the prepare-time
    # placeholder, this is the live name, not the stale stamp — so the
    # SCM facet's branch label tracks the same ground truth the produce
    # facet below derives commits against, instead of disagreeing with it.
    scm_facet = daemon._scm_facet(work_dir, live_branch or task.meta.get("branch_name"))
    # A host run's scope is the shared checkout (relics.collection_scope's
    # host_start_oid fallback); gate it by run identity so the live
    # facet never flashes a concurrent sibling's commits as this run's
    # produce (#575). A worktree run's own branch needs no filter.
    live_commit_run_id = (
        task.id if not task.meta.get("branch_name") else None
    )
    produce_facet = (
        relics.live_summary(
            work_dir,
            branch=live_branch,
            seed_ref=live_seed,
            outbox_dir=outbox_dir,
            commit_run_id=live_commit_run_id,
            # #1788: only meaningful for an isolated run's own tree — a
            # host run already measures from `host_start_oid`
            # (collection_scope's branchless fallback, `live_commit_run_id
            # is not None` here), and the plan's recorded seed_oid can
            # predate that baseline, widening the window this filter
            # exists to bound. `live_commit_run_id is None` is exactly
            # "not a host run" (mirrors the guard above).
            seed_oid=(
                relics.seed_oid_of(task.meta)
                if live_commit_run_id is None else None
            ),
        )
        if work_dir else {"known": False}
    )
    # Staleness is measured against the run's *movement*, not the wall
    # clock (maintainer, 2026-07-19, agreeing with the run that raised it:
    # "tied to elapsed-since-last-state-changing-action, it'd keep the
    # pressure where it belongs"). A timer-only rule fires on an accurate
    # card during a long test suite, and the cheapest way to satisfy it is
    # a cosmetic edit — which trains writing to the file to quiet the
    # nudge rather than because the surface moved. Here the nudge can only
    # fire when something a card would actually report has changed since
    # the card was last written.
    state_moved_monotonic = daemon._note_run_state_movement(
        task, scm=scm_facet, produce=produce_facet, stats=stats, events=events,
    )
    card_stale = daemon._card_is_stale(
        card_written_monotonic=card_written_monotonic,
        state_moved_monotonic=state_moved_monotonic,
        card_active=bool(card_text),
    )
    run_levels, run_level_slots = daemon._collect_levels(
        runner_name, outbox_dir, work_dir,
        refresh=refresh_levels, shared_dir=brr_dir,
        codex_thread_id=(
            task.meta.get("codex_thread_id")
            if hasattr(task, "meta") else None
        ),
    )
    # The closeout collector can have less evidence than this heartbeat
    # after a seat holds or awaits. Retain this already-observed reading.
    run_ledger.record_boundary_levels(task, run_levels)
    allowance_facet_input = daemon._collect_allowance_facet(
        task, runner_name, work_dir, cfg=cfg, levels=run_levels,
    )
    draws_facet_input = daemon._collect_quota_draws(task, allowance_facet_input)
    daemon._record_boot_cost(task, runner_name, work_dir, outbox_dir)
    daemon._record_context_window(runner_name, work_dir, outbox_dir)
    # design-the-seat-that-never-quits.md §machinery slice 3: needs both
    # numbers `_record_boot_cost` and `_collect_allowance_facet` just
    # computed, so it runs after both — may resolve `await_state` with a
    # new "park" outcome and stamp `pending_resource_hold` for the
    # ordinary worker-tail routing (`_finalize_resource_hold`) to pick up
    # once this turn actually ends.

    # design-the-continuous-seat.md §Presence: how long the person on
    # the other end has been quiet. Measured off this run's own inbox,
    # so it costs one directory scan per heartbeat and never a platform
    # call. A `None` thread key (a schedule-woken run in a repo nobody
    # has messaged) renders the facet `absent`, not fabricated.
    correspondent_facet_input = correspondent_mod.facet_input(
        inbox_dir,
        thread_key=daemon._task_thread_key(task),
        correspondent_key=daemon._task_correspondent_key(task),
        brr_dir=brr_dir,
    )
    await_state, hold_facet_input = daemon._hold_ratio_facet(
        task, await_state, cfg, outbox_dir, allowance_facet_input,
    )
    # The run boundary knows its own Core (the resolved profile's
    # `model`, e.g. "opus"/"fable") — pass it so a thin week_models
    # bucket for a *different* Core doesn't bind this run's pacing (#561).
    binding_model = str((runner_meta or {}).get("model") or "").strip() or None
    pacing_status = daemon._quota_pacing_status(cfg or {}, run_levels, model=binding_model)
    pace = daemon._quota_window_pace(run_levels, model=binding_model)
    if pace is not None:
        pacing_status = dict(pacing_status or {})
        pacing_status["pace"] = pace
    # The starvation park: same reading the pacing facet just proved,
    # judged against `seat.starve_floor_pct` — stamps the hold the
    # worker tail finalizes, resolves an idle await with `park`.
    await_state, starvation_facet_input = daemon._starvation_facet(
        task, await_state, cfg, pacing_status, run_levels,
    )
    if isinstance(pacing_status, dict) and starvation_facet_input is not None:
        pacing_status = dict(pacing_status)
        pacing_status["starvation"] = starvation_facet_input
    coexisting_snapshot: list[dict[str, object]] | None = None
    if brr_dir is not None:
        try:
            # Account-wide, same reason as `present_snapshot` above
            # (#1727): this facet is what tells a resident it has a
            # live strand at all, and a cross-repo strand's presence
            # never lands in this checkout's registry.
            coexisting_snapshot = [
                e for e in presence.list_active_account(brr_dir)
                if e.get("run_id") != task.id
            ]
        except OSError:
            coexisting_snapshot = None
    shuttle_projection: dict[str, object] | None = None
    if shuttle_home is not None:
        live_shuttle = shuttle.Shuttle.load(shuttle_home)
        shuttle_projection = {
            "state": live_shuttle.state,
            "why": live_shuttle.why,
            "since": live_shuttle.since,
            "run_id": live_shuttle.run_id,
        }
    # The frame's beat (move 2b): which loop iteration this capsule
    # belongs to. ``generated_at`` stays — nothing reads ``tick`` yet
    # but the chip. Read from memory in the daemon (the loop is in this
    # process), so the boundary path pays no I/O for it.
    frame_tick = tick_mod.current(shuttle_home)
    # ── the capsule, in the writer's evaluation order ──────────────
    hud = HUD(
        version=VERSION,
        generated_at=time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        tick=(
            Tick(n=frame_tick.n, at=frame_tick.at)
            if frame_tick is not None else None
        ),
        run=RunFacet(
            id=task.id,
            event_id=current_event_id,
            status=task.status,
            phase=phase,
            attempt=attempt,
            env=task.env,
            runner=runner_name,
            repo=task.meta.get("repo_label"),
            branch=task.meta.get("branch_name"),
        ),
        attention=Attention(
            pending_event_count=len(events),
            pending_outbox_file_count=len(pending_files),
            needs_attention=bool(events or pending_files),
        ),
        # Directives brr refused or dropped this run (a spawn it could
        # not queue, a reply addressed to an event that no longer
        # exists). Silence here used to be indistinguishable from
        # success — the file is deleted either way.
        notices=daemon._read_outbox_notices(outbox_dir),
        inbound=Inbound(
            current_event=current_event_id,
            # Mechanical fact, not a hint: can a reply addressed to the
            # waking event actually be delivered? Same predicate the
            # dispatch path uses to decide terminal-stream suppression,
            # so the Stop-hook delivery warning cannot nag about a
            # reply the router would refuse (#562).
            current_event_replyable=daemon._terminal_reply_lands(
                str(getattr(task, "source", "") or ""),
                spawn_parent_run_id=str(
                    task.meta.get("spawn_parent_run_id") or ""),
                runs_dir=brr_dir / "runs" if brr_dir is not None else None,
            ),
            events=events,
        ),
        outbound=Outbound(
            replies_current=int(stats.get("current", 0)),
            replies_other=int(stats.get("other", 0)),
            outbound_messages=int(stats.get("outbound", 0)),
            any_sent=bool(
                stats.get("current")
                or stats.get("other")
                or stats.get("outbound")
            ),
            pending_outbox_files=pending_files,
            oldest_pending_age_seconds=(
                round(oldest_pending_age_seconds, 1)
                if oldest_pending_age_seconds is not None else None
            ),
        ),
        delivery=daemon._live_delivery_projection(
            task, cfg, brr_dir,
            already_delivered=bool(
                stats.get("current") or stats.get("other")
                or stats.get("outbound")
            ),
        ),
        card=Card(
            active=bool(card_text),
            text=card_text,
            age_seconds=(
                int(card_age) if card_age is not None else None
            ),
            stale=card_stale,
            # What the staleness verdict is measured against, so the
            # briefing can say *why* the card is behind rather than only
            # that it is old.
            state_moved_seconds=(
                int(time.monotonic() - state_moved_monotonic)
                if state_moved_monotonic is not None else None
            ),
            delta=card_frame.portal_delta(task.meta),
        ),
        budget=Budget(elapsed_seconds=elapsed),
        await_=await_state,
        shuttle=(
            ShuttleFacet(**shuttle_projection)
            if shuttle_projection is not None else None
        ),
        # design-the-seat-that-never-quits.md: what happens when this
        # turn ends with nothing armed — the hooks' Stop phase reads it
        # to say "phase commit, then the seat parks" only when the
        # daemon will actually park (never a claim the machinery does
        # not back).
        strand=Strand(
            is_strand=bool(hasattr(task, "meta") and daemon._is_strand(task.meta)),
            submitted=bool(hasattr(task, "meta") and task.meta.get("submitted")),
        ),
        seat=Seat(
            parks_on_turn_end=bool(
                hasattr(task, "meta") and not daemon._is_strand(task.meta)
                and daemon._seat_park_enabled(cfg)
            ),
        ),
        # design-the-allowance.md's resource hold: published only once
        # armed (``None`` renders as absent, same as `scm`/`produce`
        # below) — "the UI shows why the seat is waiting and the one
        # action that would release it" (design-the-continuous-seat.md).
        resource_hold=resource_hold.portal_projection(
            task.meta.get("resource_hold")
        ),
        scm=scm_facet,
        # Move 5: the relics facet plus the loom's four kinds, projected from
        # the frame's ``produce.jsonl`` and the same relic records (added
        # after the movement stamp above, which reads the relics facet alone).
        produce=with_ledger(produce_facet, brr_dir, task.id),
        # #904's armed dated-letters projection: the still-armed `at:`
        # schedule entries, read from the snapshot `_fire_due_schedules`
        # writes each scheduling tick (`schedule.save_armed_letters`) —
        # projection only, never re-parsed from the dominion here, so a
        # per-run heartbeat stays cheap. `[]` when there is no brr_dir
        # (ad-hoc callers) or nothing armed.
        schedule=Schedule(
            armed=(
                schedule_mod.load_armed_letters(brr_dir)
                if brr_dir is not None else []
            ),
        ),
        knowledge=Knowledge(kb_base_url=task.meta.get("kb_base_url")),
        name=Name(written=bool(run_ledger.read_run_name_control(outbox_dir))),
        resources=daemon._resources_facet(
            quota_summary,
            # Per-Shell level source (see _collect_levels): Codex reads its
            # subscription quota + context window live from the session
            # rollout file; Claude gets terminal spend/context accounting
            # from result JSON. The wired-slot set decides whether an empty
            # slot reads 'absent' vs 'unimplemented'.
            levels=run_levels,
            levels_collector=run_level_slots,
            branch=task.meta.get("branch_name"),
            # A resident-declared `.pr` control file wins over task.meta:
            # it's this run's own live evidence (written the moment `gh pr
            # create` succeeds), whereas `github_pr_number` in task.meta
            # only exists for tasks that originated *from* a GitHub
            # issue/PR — a run that creates its own PR mid-thought had no
            # way to update that field before this file existed.
            pr_number=(
                daemon._read_pr_control(outbox_dir / daemon._PR_CONTROL_NAME)
                or task.meta.get("github_pr_number")
            ),
            runner_name=runner_name,
            runner_meta=runner_meta,
            runner_catalog=runner_catalog,
            quality_escalation=quality_escalation,
            relay_consent=relay_consent,
            pacing_status=pacing_status,
            coexisting=coexisting_snapshot,
            wake_request=task.meta.get("wake_request"),
            allowance=allowance_facet_input,
            draws=draws_facet_input,
            hold=hold_facet_input,
            correspondent=correspondent_facet_input,
        ),
        heddles=[
            dict(h) for h in ((card_state or {}).get("heddles") or [])
            if isinstance(h, dict)
        ],
        bolt=Bolt(**bolt_state) if bolt_state is not None else None,
    )
    resources = hud.resources
    if task.meta.get("root_kind") == "home":
        remote_scm = resources["remote_scm"]
        remote_scm.update(
            {
                "status": "absent",
                "branch": None,
                "pr_number": None,
                "pr_state": "none",
                "summary": None,
                "note": "account-home runs have no forge lane",
            }
        )
    # Spawn admission is quota-shaped. The old numeric headroom projection
    # invited callers to make a second admission decision from a stale cap.
    #
    # ``spawn_quota_queued`` is a *once* guard, not a state: it is stamped
    # the tick a low floor defers a spawn and deliberately never cleared,
    # so a spawn that waits three ticks still emits exactly one
    # ``spawn_queued`` event. That makes it useless on its own here —
    # ``list_pending`` returns ``processing`` events too (a still-running
    # spawn survives its own ``set_status(..., "processing")`` write), so
    # counting the flag alone reports a child that queued once and has
    # been *running* for an hour as still waiting. Status is what answers
    # "is it still waiting"; the flag only answers "did it ever wait".
    coexisting_facet = resources["coexisting_runs"]
    queued_spawns = sum(
        1 for ev in protocol.list_pending(inbox_dir)
        if ev.get("spawn_immediate")
        and ev.get("spawn_quota_queued")
        and ev.get("status") == "pending"
    ) if inbox_dir is not None else 0
    coexisting_facet["spawn_pool"] = {
        "floor": pacing_status.get("floor") if pacing_status else None,
        "queued": queued_spawns,
    }
    coexisting_facet["owned_children"] = daemon._owned_child_controls(task.id)
    return dataclasses.replace(hud, change_token=daemon._change_token(hud.to_dict()))


def write(hud: HUD, outbox_dir: Path) -> Path:
    """Put *hud* on ``<outbox_dir>/portal-state.json``, atomically."""
    from . import protocol

    path = Path(outbox_dir) / PORTAL_STATE_NAME
    protocol._atomic_write(path, hud.to_json())
    return path


def write_live(inputs: HUDInputs) -> Path | None:
    """Build and write this tick's HUD — the heartbeat/flush entry point.

    ``None`` when there is no outbox to write into, or when an ``OSError``
    stopped the build or the write (the writer's contract on ``main``: a
    portal refresh never raises into the worker).
    """
    if not inputs.outbox_dir:
        return None
    try:
        return write(build(inputs), inputs.outbox_dir)
    except OSError:
        return None
