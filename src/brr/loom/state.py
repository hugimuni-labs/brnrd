"""``GET /loom/state.json`` — the loom screen's one read, rebuilt every beat.

design-the-loom.md §3 (the space: passes, beads, threads, knots, heddles),
§4 (the screen: window, heddles, bench, gauge, cloth), §20 (heddles as
layers with signatures), §22 (the weft line and the wyrd-tree grown from the
paths the beads touched). :func:`build` is the whole contract: a ``dict``
with fixed top-level keys, every part tolerant of absence (``None`` / ``[]``),
nothing invented, nothing fetched over the network.

The sources, by key — each part's own docstring names its files:

- ``shuttle`` — ``<account_home>/shuttle.json`` (:class:`brr.shuttle.Shuttle`).
- ``run`` — the live run: ``shuttle.run_id`` → ``<brr>/runs/<id>/run.md``
  (:meth:`brr.run.Run.from_file`) → its outbox (``outbox_path``, else the
  ``<brr>/outbox/*/`` whose ``portal-state.json`` names the run); ``.name``,
  ``.mood``, ``.topic``, ``.card`` (:func:`brr.card_frame.card_halves`,
  :func:`brr.course.parse`).
- ``hud`` — that outbox's ``portal-state.json`` (:class:`brr.hud.HUD`), the
  runner's quota snapshot beside it, the last ``boundaries.jsonl`` row that
  carried a chip and a context reading, and each owned strand's ``run.md`` +
  portal.
- ``heddles`` — ``<account_home>/surface/topics/*.md``
  (:func:`brr.heddles.load_topics`) joined with the portal's lit list.
- ``warp`` — ``<account_home>/surface/warp/*.md``
  (:func:`brr.items.load_items`), state derived from ``done:`` / ``retired:``
  / open ``needs:``.
- ``beads`` — the live run's ``<brr>/runs/<id>/boundaries.jsonl`` tail, places
  relativised by :func:`brr.heddles.relative_place`, topics by the heddles'
  compiled signatures.
- ``cloth`` — ``<brr>/run-ledger.jsonl`` tail, plus the live run and its live
  strands (``run.md`` + ``.relics.jsonl``), topics from each topic's
  ``<slug>.index.jsonl`` (:func:`brr.heddles.index`).
- ``tree`` — the union of the beads' places and the places the cloth runs'
  own ``boundaries.jsonl`` rows ``mutate``\\ d; heat decays by the hour.
- ``bench`` — ``<account_home>/bench/**/*.md``.

This module never writes. ``BRNRD_LOOM_STRICT=1`` makes a part's failure
raise instead of degrading to its empty shape (the tests set it).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .. import heddles as heddles_mod

#: The screen's beat: one state per beat, never two.
BEAT_MS = 600
TRANSITIONS_LAST = 12
BEADS_LAST = 240
CLOTH_LAST = 80
DETAIL_CHARS = 160
TREE_MAX = 500
BENCH_MAX = 200
#: How far back from a jsonl file's end a tail read looks.
_TAIL_CAP_BYTES = 8 * 1024 * 1024
#: The places that are runtime, never the tree.
_NOT_TREE = (".brr/", ".git/")
_STRAND_LIVE = frozenset({"pending", "running", "processing"})


def _strict() -> bool:
    return os.environ.get("BRNRD_LOOM_STRICT", "").strip() not in ("", "0", "false")


def _safe(fn: Callable[[], Any], empty: Any) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one unreadable part never sinks the screen
        if _strict():
            raise
        return empty


_epoch = heddles_mod._epoch
_iso = heddles_mod._iso


# ── small readers ────────────────────────────────────────────────────────


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def tail_rows(
    path: Path | None,
    n: int,
    keep: Callable[[dict[str, Any]], bool] | None = None,
    *,
    cap: int = _TAIL_CAP_BYTES,
) -> list[dict[str, Any]]:
    """The last *n* JSON-object rows of a jsonl file (that *keep* admits), in
    file order. Reads at most *cap* bytes from the end. Never raises."""
    if path is None or n <= 0:
        return []
    try:
        with Path(path).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - cap)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # the first line of a mid-file read is partial
    rows: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(rows) >= n:
            break
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and (keep is None or keep(row)):
            rows.append(row)
    rows.reverse()
    return rows


def _brr_dir(repo_root: Path) -> Path:
    from .. import gitops

    try:
        return gitops.shared_brr_dir(Path(repo_root))
    except Exception:  # noqa: BLE001 - not a git checkout: the local .brr
        return Path(repo_root) / ".brr"


def _roots(repo_root: Path, brr_dir: Path) -> tuple[Path, ...]:
    """The checkouts an absolute boundary path is relativised against: the
    repo root this was asked for and the host checkout its ``.brr`` lives in
    (a strand's worktree nests inside the host's)."""
    out: list[Path] = []
    for root in (Path(repo_root), brr_dir.parent):
        for spelling in (root, _resolved(root)):
            if spelling not in out:
                out.append(spelling)
    # Longest first, so a worktree nested in the host wins over the host.
    return tuple(sorted(out, key=lambda p: -len(str(p))))


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _first_line(text: str | None) -> str | None:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    return None


# ── shuttle ──────────────────────────────────────────────────────────────


def read_shuttle(account_home: Path | None) -> dict[str, Any] | None:
    """``<account_home>/shuttle.json`` — the seat's state and its last
    :data:`TRANSITIONS_LAST` transitions ``{at, from, to, why, tick}``, and
    ``tick``, the frame's latest beat number. ``None`` when there is no record
    (read with the file's own loader, which would *create* a missing one —
    so absence is checked first: this never writes)."""
    from .. import shuttle as shuttle_mod

    if account_home is None or not (Path(account_home) / "shuttle.json").is_file():
        return None
    try:
        seat = shuttle_mod.Shuttle.load(Path(account_home))
    except ValueError:
        return None
    from .. import tick as tick_mod

    latest = _safe(lambda: tick_mod.current(Path(account_home)), None)
    return {
        "state": seat.state,
        "why": seat.why or None,
        "since": seat.since or None,
        "run_id": seat.run_id or None,
        # The frame's beat (``<home>/tick.json``, :func:`brr.tick.current`) —
        # the page's scan is clocked on it; ``null`` before any loop ticked.
        "tick": latest.n if latest is not None else None,
        "transitions": [
            {"at": row.get("at"), "from": row.get("from"), "to": row.get("to"), "why": row.get("why"),
             "tick": _int(row.get("tick"))}
            for row in seat.transitions[-TRANSITIONS_LAST:]
        ],
    }


# ── the live run ─────────────────────────────────────────────────────────


class _Live:
    """What the live run resolves to: its manifest, outbox and portal."""

    def __init__(self, run_id: str, run: Any, outbox_dir: Path | None, portal: dict[str, Any]):
        self.run_id = run_id
        self.run = run
        self.outbox_dir = outbox_dir
        self.portal = portal

    @property
    def meta(self) -> dict[str, Any]:
        meta = getattr(self.run, "meta", None)
        return meta if isinstance(meta, dict) else {}


def _load_run(brr_dir: Path, run_id: str) -> Any:
    from ..run import Run

    if not run_id or "/" in run_id or run_id.startswith("."):
        return None
    return Run.from_file(brr_dir / "runs" / run_id / "run.md")


def _portal_run_id(portal: Mapping[str, Any] | None) -> str | None:
    run = (portal or {}).get("run")
    return str(run.get("id") or "") or None if isinstance(run, dict) else None


def find_outbox(brr_dir: Path, run_id: str, run: Any = None) -> tuple[Path | None, dict[str, Any]]:
    """The run's outbox dir and its portal: ``run.md``'s ``outbox_path`` when
    its portal names the run, else the ``<brr>/outbox/*/`` that does."""
    meta = getattr(run, "meta", None) or {}
    hinted = str(meta.get("outbox_path") or "").strip()
    if hinted:
        portal = _read_json(Path(hinted) / "portal-state.json")
        if portal is not None and _portal_run_id(portal) == run_id:
            return Path(hinted), portal
    root = brr_dir / "outbox"
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return None, {}
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        portal = _read_json(Path(entry.path) / "portal-state.json")
        if portal is not None and _portal_run_id(portal) == run_id:
            return Path(entry.path), portal
    return None, {}


def find_live(brr_dir: Path, shuttle: Mapping[str, Any] | None) -> _Live | None:
    """The run the Shuttle is on, unless the seat is ``released`` or the run
    has no manifest and no portal to read."""
    if not shuttle or shuttle.get("state") == "released":
        return None
    run_id = str(shuttle.get("run_id") or "")
    if not run_id:
        return None
    run = _load_run(brr_dir, run_id)
    outbox_dir, portal = find_outbox(brr_dir, run_id, run)
    if run is None and outbox_dir is None:
        return None
    return _Live(run_id, run, outbox_dir, portal)


def _started(run: Any) -> str | None:
    """When the run went ``running`` — the first such transition on its manifest."""
    transitions = (getattr(run, "meta", None) or {}).get("transitions")
    if isinstance(transitions, str):
        try:
            transitions = json.loads(transitions)
        except ValueError:
            transitions = None
    for row in transitions if isinstance(transitions, list) else ():
        if isinstance(row, dict) and row.get("to") == "running":
            return _iso(_epoch(row.get("at")))
    return None


def _shell_core(meta: Mapping[str, Any], portal: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The run's Shell and Core: ``run.md``'s ``shell``/``core`` (a strand's
    dispatch writes them), else the portal catalog row its ``runner_name``
    selects. Never split out of a profile name."""
    shell = str(meta.get("shell") or "") or None
    core = str(meta.get("core") or "") or None
    if shell and core:
        return shell, core
    runner_name = str(meta.get("runner_name") or "")
    runner = ((portal.get("resources") or {}).get("runner") or {}) if isinstance(portal, dict) else {}
    for row in runner.get("catalog") or () if isinstance(runner, dict) else ():
        if isinstance(row, dict) and runner_name and row.get("name") == runner_name:
            shell = shell or (str(row.get("shell") or row.get("hooks") or "") or None)
            core = core or (str(row.get("model") or "") or None)
            break
    return shell, core


def _section_lines(text: str, heading: str) -> list[str] | None:
    from .. import card_frame

    span = card_frame._section_span(text, heading)
    if span is None:
        return None
    return text[span[0]:span[1]].splitlines()[1:]


_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")


def read_card(outbox_dir: Path | None) -> dict[str, Any] | None:
    """``<outbox>/.card`` split by :func:`brr.card_frame.card_halves`: the
    weaver's ``## Now`` (text), ``## Plan`` (:func:`brr.course.parse` rows) and
    ``## Vector`` (bullets); the frame's ``## Ledger`` lines as written."""
    from .. import card_frame, course

    text = _read_text(outbox_dir / card_frame.CARD_NAME) if outbox_dir else None
    if text is None:
        return None
    weaver, frame = card_frame.card_halves(text)
    now_lines = _section_lines(weaver, "## Now")
    now = "\n".join(now_lines).strip() if now_lines is not None else ""
    parsed = course.parse(weaver)
    vector_lines = _section_lines(weaver, "## Vector") or []
    vector = [m.group(1).strip() for m in map(_BULLET_RE.match, vector_lines) if m]
    ledger_lines = _section_lines(frame, card_frame.LEDGER_HEADING) or []
    return {
        "now": now or None,
        "plan": [{"text": row.text, "done": row.done} for row in (parsed.rows if parsed else [])],
        "vector": vector,
        "ledger": [line for line in ledger_lines if line.strip()],
    }


def read_run(live: _Live | None, now_epoch: float) -> dict[str, Any] | None:
    """``run`` — the live run's facet. Name/mood/topic are its outbox control
    files (``.name``, ``.mood``'s first line, ``.topic``); the topic falls back
    to the portal's ``run.topic``; ``mood_glyph`` is :func:`brr.emotes.glyph`."""
    from .. import emotes

    if live is None:
        return None
    meta, portal, outbox = live.meta, live.portal, live.outbox_dir
    mood = _first_line(_read_text(outbox / ".mood")) if outbox else None
    started = _started(live.run)
    elapsed = _int(((portal.get("budget") or {}).get("elapsed_seconds")))
    if elapsed is None and started is not None:
        elapsed = max(0, int(now_epoch - (_epoch(started) or now_epoch)))
    topic = _first_line(_read_text(outbox / ".topic")) if outbox else None
    portal_topic = (portal.get("run") or {}).get("topic") if isinstance(portal.get("run"), dict) else None
    if not topic or topic.split()[0] in ("new", "null"):
        topic = portal_topic or None
    shell, core = _shell_core(meta, portal)
    return {
        "id": live.run_id,
        "name": (_first_line(_read_text(outbox / ".name")) if outbox else None) or None,
        "mood": mood,
        "mood_glyph": _safe(lambda: emotes.glyph(mood), None) if mood else None,
        "started": started,
        "elapsed_s": elapsed,
        "topic": topic,
        "shell": shell,
        "core": core,
        "card": _safe(lambda: read_card(outbox), None),
    }


# ── hud ──────────────────────────────────────────────────────────────────


def _quota(outbox_dir: Path | None, portal: Mapping[str, Any]) -> dict[str, Any]:
    """``{<bucket>_pct_left: n}`` — the runner's quota snapshot beside the
    portal (``.claude-usage-levels.json``: session, week, each week model),
    else the portal quota summary's buckets parsed the way the chip parses
    them (``hooks._quota_buckets``)."""
    from .. import claude_usage

    out: dict[str, Any] = {}
    snap = _read_json(outbox_dir / claude_usage.SNAPSHOT_NAME) if outbox_dir else None
    buckets = ((snap or {}).get("quota") or {}).get("buckets") if isinstance((snap or {}).get("quota"), dict) else None
    if isinstance(buckets, dict):
        for name, value in buckets.items():
            if name == "week_models" and isinstance(value, dict):
                for model, row in value.items():
                    pct = (row or {}).get("remaining_percentage") if isinstance(row, dict) else None
                    if _int(pct) is not None:
                        out[f"{_key(model)}_pct_left"] = _int(pct)
            elif isinstance(value, dict) and _int(value.get("remaining_percentage")) is not None:
                out[f"{_key(name)}_pct_left"] = _int(value.get("remaining_percentage"))
    if out:
        return out
    from .. import hooks

    resources = portal.get("resources") if isinstance(portal.get("resources"), dict) else {}
    for _letter, pct, part in hooks._quota_buckets(resources):
        # The bucket's name is the clause before its percent, verbatim —
        # codex says ``5h``/``7d``, which the chip's letter regex reads as
        # ``h``/``d``; a window is not renamed into another Shell's words.
        match = _QUOTA_LABEL_RE.match(part)
        if match and pct.isdigit():
            out.setdefault(f"{_key(match.group('label'))}_pct_left", int(pct))
    return out


_QUOTA_LABEL_RE = re.compile(r"\s*(?P<label>.+?)\s+\d+(?:\.\d+)?\s*%\s*left")


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label).strip().lower()).strip("_") or "bucket"


def _strands(brr_dir: Path, portal: Mapping[str, Any], where: "Where") -> list[dict[str, Any]]:
    """The portal's ``owned_children``, each read at its own ``run.md``
    (status, ``spawn_allowance_tokens``) and portal (``strand.submitted``);
    ``spent`` is the parent portal's weighted draw for it; ``places``,
    ``last_bead_at`` and ``last_place_kind`` its own boundaries
    (:func:`strand_trace`)."""
    coexisting = ((portal.get("resources") or {}).get("coexisting_runs") or {})
    children = coexisting.get("owned_children") if isinstance(coexisting, dict) else None
    out: list[dict[str, Any]] = []
    for child in children or ():
        if not isinstance(child, dict):
            continue
        run_id = str(child.get("run_id") or "")
        run = _load_run(brr_dir, run_id) if run_id else None
        meta = getattr(run, "meta", None) or {}
        status_raw = str(getattr(run, "status", "") or "")
        submitted = False
        if run is not None:
            _, child_portal = find_outbox(brr_dir, run_id, run)
            submitted = bool(((child_portal or {}).get("strand") or {}).get("submitted"))
        if run is None:
            status = None
        elif status_raw in _STRAND_LIVE:
            status = "submitted" if submitted else "live"
        else:
            status = "done"
        places, last_bead_at, last_kind = strand_trace(
            brr_dir, run_id, where, source=str(getattr(run, "source", "") or "")
        )
        out.append({
            "id": run_id or None,
            # A strand queued in the spawn pool has an edge but no run id yet;
            # its dispatch event is the handle it already has.
            "event_id": str(child.get("event_id") or "") or None,
            "title": str(child.get("title") or meta.get("title") or "") or None,
            "status": status,
            "spent": _int(child.get("weighted")),
            "allowance": _int(meta.get("spawn_allowance_tokens")),
            "places": places,
            "last_bead_at": last_bead_at,
            "last_place_kind": last_kind,
        })
    return out


STRAND_PLACES = 12
_STRAND_BEADS_SCAN = 4000


def strand_trace(
    brr_dir: Path, run_id: str, where: "Where", *, source: str = ""
) -> tuple[list[str], str | None, str | None]:
    """A strand's position on the tree: the last :data:`STRAND_PLACES` distinct
    repo places in its own ``<brr>/runs/<id>/boundaries.jsonl`` (newest first,
    extracted exactly as ``beads[].places``), its last bead's ``at`` and that
    bead's :func:`place_kind`. ``([], None, None)`` when the log is absent or
    holds no bead."""
    if not run_id or "/" in run_id or run_id.startswith("."):
        return [], None, None
    rows = tail_rows(brr_dir / "runs" / run_id / "boundaries.jsonl", _STRAND_BEADS_SCAN, _is_bead)
    if not rows:
        return [], None, None
    places: list[str] = []
    for row in reversed(rows):
        for place in row_paths(row, where)[0]:
            if place not in places:
                places.append(place)
            if len(places) >= STRAND_PLACES:
                break
        if len(places) >= STRAND_PLACES:
            break
    return places, rows[-1].get("at"), place_kind(rows[-1], where, source=source)


def _last_boundary(rows: list[dict[str, Any]], pick: Callable[[dict[str, Any]], Any]) -> Any:
    for row in reversed(rows):
        value = pick(row)
        if value is not None:
            return value
    return None


def read_hud(
    brr_dir: Path, live: _Live | None, rows: list[dict[str, Any]], where: "Where"
) -> dict[str, Any] | None:
    """``hud`` — the live outbox's portal. ``chip`` is the last chip the hook
    injected (a boundary row's ``inject`` first line), else
    :func:`brr.hud.render_bar` on the portal; ``ctx_tokens`` the last
    boundary's ``ctx.tokens_after``; ``spend`` the portal's allowance facet;
    ``full`` the whole typed HUD exactly as ``brnrd hud --json`` prints it
    (``HUD.from_dict(portal).to_dict()``), so the gauge can render every
    instrument the chip carries."""
    from .. import hud as hud_mod

    if live is None or not live.portal:
        return None
    portal = live.portal
    current = _safe(lambda: hud_mod.HUD.from_dict(portal), None)
    chip = _last_boundary(rows, lambda r: _first_line(r.get("inject")) if isinstance(r.get("inject"), str) else None)
    if chip is None and current is not None:
        chip = _safe(lambda: hud_mod.render_bar(current, outbox_dir=live.outbox_dir), None) or None
    resources = portal.get("resources") if isinstance(portal.get("resources"), dict) else {}
    allowance = resources.get("allowance") if isinstance(resources.get("allowance"), dict) else {}
    spend = None
    if allowance.get("status") == "known":
        spend = {
            "tokens": _int(allowance.get("spent")),
            "allowance_tokens": _int(allowance.get("tokens")),
            "pct": _int(allowance.get("pct")),
        }
    ctx = _last_boundary(rows, lambda r: _int((r.get("ctx") or {}).get("tokens_after")) if isinstance(r.get("ctx"), dict) else None)
    return {
        "chip": chip,
        "quota": _safe(lambda: _quota(live.outbox_dir, portal), {}),
        "spend": spend,
        "ctx_tokens": ctx,
        "strands": _safe(lambda: _strands(brr_dir, portal, where), []),
        "full": _safe(lambda: current.to_dict(), None) if current is not None else None,
    }


# ── heddles ──────────────────────────────────────────────────────────────

_COMPILED_LOCK = threading.Lock()
_COMPILED: dict[str, tuple[tuple, list[Any]]] = {}


def compiled_topics(account_home: Path | None) -> list[Any]:
    """Every live topic's compiled signature (:func:`brr.heddles._compile`),
    recompiled only when the topic files change (the heddles' own fingerprint)."""
    directory = heddles_mod.topics_dir(account_home)
    fingerprint = heddles_mod._fingerprint(directory)
    if not fingerprint:
        return []
    key = str(directory)
    with _COMPILED_LOCK:
        cached = _COMPILED.get(key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        compiled = [heddles_mod._compile(t) for t in heddles_mod.load_topics(directory)]
        _COMPILED[key] = (fingerprint, compiled)
        return compiled


def read_heddles(compiled: list[Any], portal: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """``heddles`` — every live topic file's slug, rune and signature, lit by
    the portal's ``heddles`` list (``brightness``, ``last_match_at``); a topic
    the portal does not name is dark (``0.0``, ``null``)."""
    lit = {}
    for row in (portal or {}).get("heddles") or ():
        if isinstance(row, dict) and row.get("slug"):
            lit[str(row["slug"])] = row
    out = []
    for item in compiled:
        topic = item.topic
        row = lit.get(topic.slug, {})
        value = row.get("brightness")
        out.append({
            "slug": topic.slug,
            "rune": topic.rune or None,
            "lit": float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0,
            "last_lit": row.get("last_match_at") or None,
            "signature": topic.signature.as_dict(),
        })
    return out


def match_topics(
    compiled: Iterable[Any],
    *,
    places: Iterable[str] = (),
    text: str = "",
    run_id: str = "",
) -> list[str]:
    """Slugs whose signature matches: a place against ``places``, a word in
    *text*, or *run_id* named in ``threads``."""
    places = list(places)
    out = []
    for item in compiled:
        hit = any(p.match(place) for p in item.places for place in places)
        hit = hit or (bool(text) and any(w.search(text) for w in item.words))
        hit = hit or (bool(run_id) and run_id in item.threads)
        if hit:
            out.append(item.topic.slug)
    return out


# ── warp ─────────────────────────────────────────────────────────────────


def item_state(item: Any, by_id: Mapping[str, Any]) -> str:
    """``done`` / ``retired`` from the receipt row, else ``held`` while a
    ``needs:`` id is still open (:func:`brr.items.open_blockers`), else ``ready``."""
    from .. import items

    if item.state in ("done", "retired"):
        return item.state
    return "held" if items.open_blockers(item, by_id) else "ready"


def read_warp(account_home: Path | None) -> dict[str, Any]:
    """``warp`` — ``<account_home>/surface/warp/*.md`` via
    :func:`brr.items.load_items`. Goals (``type: goal``) apart; an item's
    state is ``done`` / ``retired`` from its receipt row, else ``held`` while a
    ``needs:`` id is still open (:func:`brr.items.open_blockers`), else
    ``ready``. ``taken`` is the last run on its ``taken:`` row."""
    from .. import items

    if account_home is None:
        return {"goals": [], "items": []}
    loaded = items.load_items(Path(account_home) / "surface" / items.WARP_DIRNAME)
    by_id = {item.id: item for item in loaded}
    goals, rows = [], []
    for item in loaded:
        if item.type == items.GOAL_TYPE:
            goals.append({"id": item.id, "title": item.headline, "metric": item.metric})
            continue
        state = item_state(item, by_id)
        taken = [t for t in item.taken if t.startswith("run-")]
        rows.append({
            "id": item.id,
            "type": item.type,
            "title": item.headline,
            "topics": list(item.topics),
            "needs": list(item.needs),
            "state": state,
            "taken": taken[-1] if taken else None,
        })
    return {"goals": goals, "items": rows}


# ── places and kinds ─────────────────────────────────────────────────


#: The account home's four trees the home tree grows from.
HOME_TREE_DIRS = ("dominion", "knowledge", "surface", "bench")
#: What a bead touched, as the loom draws it.
PLACE_KINDS = ("file", "home", "forge", "wire", "shed", "crew", "clock")


class Where:
    """The coordinates a boundary row's paths resolve against: the repo
    roots (:func:`_roots`), the account home and the shared ``.brr``."""

    __slots__ = ("roots", "home", "home_spellings", "brr_dir", "outbox_spellings")

    def __init__(self, roots: Iterable[Path], home: Path | None, brr_dir: Path):
        self.roots = tuple(roots)
        self.home = home
        self.brr_dir = brr_dir
        self.home_spellings = _spellings(home) if home is not None else ()
        self.outbox_spellings = _spellings(brr_dir / "outbox")

    def key(self) -> tuple:
        return (self.roots, self.home_spellings)


def _spellings(path: Path) -> tuple[str, ...]:
    out: list[str] = []
    for spelling in (str(path), str(_resolved(path))):
        spelling = spelling.rstrip("/")
        if spelling and spelling not in out:
            out.append(spelling)
    return tuple(sorted(out, key=len, reverse=True))


def _under(value: str, bases: Iterable[str]) -> str | None:
    """*value* relative to the first base it sits under (``""`` for the base
    itself), or ``None``."""
    for base in bases:
        if value == base:
            return ""
        if value.startswith(base + "/"):
            return value[len(base) + 1:]
    return None


#: An absolute path token in a command line — not one glued to a variable
#: (``$O/000007-x.md``) or a word.
_ABS_PATH_RE = re.compile(r"(?<![\w.~$}/-])/(?:[\w.@+-]+/)*[\w.@+-]+")
#: A relative path token with at least one ``/``.
_REL_PATH_RE = re.compile(r"(?<![\w./$~}@-])(?:\./)?[\w@+-][\w.@+-]*(?:/[\w.@+-]+)+")


def detail_paths(row: Mapping[str, Any]) -> list[str]:
    """The path tokens a row's ``detail`` names — where the act looked:
    absolute ones as written, relative ones joined to the row's ``cwd``.
    Unchecked; :func:`row_paths` keeps only the ones that exist."""
    detail = row.get("detail") if isinstance(row.get("detail"), str) else ""
    if not detail:
        return []
    cwd = str(row.get("cwd") or "").rstrip("/")
    found: list[str] = [m.group(0) for m in _ABS_PATH_RE.finditer(detail)]
    if cwd.startswith("/"):
        for match in _REL_PATH_RE.finditer(detail):
            token = match.group(0)
            found.append(f"{cwd}/{token[2:] if token.startswith('./') else token}")
    out: list[str] = []
    for value in found:
        if not value.endswith("…") and value not in out:
            out.append(value)
    return out


def frame_paths(row: Mapping[str, Any]) -> list[str]:
    """The frame's own ``place.path`` / ``place.paths`` for the row, if any."""
    place = row.get("place")
    if not isinstance(place, dict):
        return []
    raw = [place.get("path")]
    if isinstance(place.get("paths"), list):
        raw += place["paths"]
    out: list[str] = []
    for value in raw:
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def raw_paths(row: Mapping[str, Any]) -> list[str]:
    """Every path a row names, ``detail`` first, then the frame's — for
    classifying (:func:`place_kind`), never as places."""
    out = detail_paths(row)
    return out + [p for p in frame_paths(row) if p not in out]


def home_place(value: str, where: Where) -> str | None:
    """*value* as a home place (``knowledge/…``) when it sits under one of
    :data:`HOME_TREE_DIRS` of the account home; ``None`` otherwise."""
    rest = _under(value, where.home_spellings) if value.startswith("/") else None
    if not rest:
        return None
    head = rest.split("/", 1)[0]
    return rest.rstrip("/") if head in HOME_TREE_DIRS else None


def _resolve_paths(values: Iterable[str], where: Where, *, check: bool) -> tuple[list[str], list[str]]:
    places: list[str] = []
    homes: list[str] = []
    for value in values:
        if check and not os.path.exists(value):
            continue
        home = home_place(value, where)
        if home is not None:
            if home not in homes:
                homes.append(home)
            continue
        rel = heddles_mod.relative_place(value, where.roots)
        if not rel or rel.startswith(_NOT_TREE) or rel in (".brr", ".git") or rel in places:
            continue
        places.append(rel)
    return places, homes


def row_paths(row: Mapping[str, Any], where: Where) -> tuple[list[str], list[str]]:
    """``(repo places, home places)`` — where the act *looked*, in order of
    evidence: the paths ``detail`` names that exist; else the frame's own
    ``place`` extraction; else the row's ``cwd``. Only paths inside a repo root
    (relativised by :func:`brr.heddles.relative_place`, a worktree's
    ``.brr/worktrees/<id>/`` prefix included) or under the account home's
    :data:`HOME_TREE_DIRS` are places; runtime paths (``.brr/``, ``.git/``)
    and anything else (``/tmp/…``) are not."""
    for values, check in ((detail_paths(row), True), (frame_paths(row), False)):
        places, homes = _resolve_paths(values, where, check=check)
        if places or homes:
            return places, homes
    cwd = str(row.get("cwd") or "")
    return _resolve_paths([cwd] if cwd.startswith("/") else [], where, check=True)


def row_places(row: Mapping[str, Any], roots: Iterable[Path]) -> list[str]:
    """A boundary row's repo places (:func:`row_paths` without a home)."""
    return row_paths(row, Where(roots, None, Path("/nonexistent-brr")))[0]


_FORGE_RE = re.compile(r"\b(?:gh\s+(?:pr|issue|api)|git\s+push)\b")
_CREW_RE = re.compile(r"(?:^|[\s>'\"])(?:spawn:\s*true\b|(?:to|stop):\s*(?:run|evt)-[\w-]+)")
_SHED_RE = re.compile(r"\bbrnrd\s+await\b")
_WIRE_NAME_RE = re.compile(r"(?:^|/)(?:\d{6}-[^/\s]*\.md|\.card|\.mood)$")
_WIRE_DETAIL_RE = re.compile(r"(?<![\w.])(?:\d{6}-[\w.-]*\.md|\.card|\.mood)\b")


def place_kind(row: Mapping[str, Any], where: Where, *, source: str = "") -> str:
    """What a bead touched, one of :data:`PLACE_KINDS`, first match wins:

    - a wake row (``phase: session-start``): ``clock`` when the run's *source*
      is ``schedule``, ``crew`` for a ``spawn*`` source, else ``wire``;
    - ``gh pr|issue|api`` or ``git push`` in ``detail`` → ``forge``;
    - a ``spawn: true`` / ``to: <id>`` / ``stop: <id>`` directive in ``detail`` → ``crew``;
    - ``brnrd await`` → ``shed``;
    - a path under ``<brr>/outbox/`` naming ``000NNN-*.md``, ``.card`` or
      ``.mood`` (on the path, or in ``detail`` beside the outbox dir), or any
      ``conversations/`` path → ``wire``;
    - a path under the account home's ``dominion/ knowledge/ surface/ bench/`` → ``home``;
    - else ``file``.
    """
    if row.get("phase") == "session-start":
        if source == "schedule":
            return "clock"
        return "crew" if source.startswith("spawn") else "wire"
    detail = row.get("detail") if isinstance(row.get("detail"), str) else ""
    if _FORGE_RE.search(detail):
        return "forge"
    if _CREW_RE.search(detail):
        return "crew"
    if _SHED_RE.search(detail):
        return "shed"
    raw = raw_paths(row)
    for value in raw:
        if "/conversations/" in value or value.startswith("conversations/"):
            return "wire"
        inside = _under(value, where.outbox_spellings)
        if inside is not None and (_WIRE_NAME_RE.search(value) or _WIRE_DETAIL_RE.search(detail)):
            return "wire"
    if any(home_place(value, where) is not None for value in raw):
        return "home"
    return "file"


def _is_bead(row: Mapping[str, Any]) -> bool:
    return bool(row.get("act")) or row.get("phase") == "session-start"


def read_beads(
    rows: list[dict[str, Any]], where: Where, compiled: list[Any], run_id: str, *, source: str = "",
    first_n: int | None = 0,
) -> list[dict[str, Any]]:
    """``beads`` — one per boundary row that carried an act (the post-tool
    rows) or woke the run (``session-start``, ``act`` = that phase), oldest
    first. ``n`` is the bead's index among the run's beads (``first_n`` + its
    position; ``GET /loom/page/bead?run=&n=`` takes it). ``places`` are repo
    places, ``home_places`` the account home's (:func:`row_paths`);
    ``place_kind`` is :func:`place_kind`."""
    out = []
    for index, row in enumerate(rows):
        ctx = row.get("ctx") if isinstance(row.get("ctx"), dict) else {}
        detail = row.get("detail") if isinstance(row.get("detail"), str) else ""
        places, homes = row_paths(row, where)
        out.append({
            "n": first_n + index if first_n is not None else None,
            "at": row.get("at"),
            "act": row.get("act") or row.get("phase"),
            "place_kind": place_kind(row, where, source=source),
            "places": places,
            "home_places": homes,
            "ctx_after": _int(ctx.get("tokens_after")),
            "delta": _int(ctx.get("delta")),
            "detail": detail[:DETAIL_CHARS] or None,
            "topics": match_topics(compiled, places=places, text=detail, run_id=run_id),
        })
    return out


# ── cloth ────────────────────────────────────────────────────────────────


def _refs_summary(refs: Iterable[Any]) -> tuple[list[int], int, int]:
    """``(prs, knots, pages)`` from relic-shaped refs: PR numbers from ``pr``
    rows and merges; a knot per ``commit`` / ``merge``; a page per ``kb`` /
    ``page``."""
    prs: set[int] = set()
    knots = pages = 0
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "")
        if kind == "pr" and _int(ref.get("number")) is not None:
            prs.add(int(ref["number"]))
        if kind == "merge" and _int(ref.get("pr")) is not None:
            prs.add(int(ref["pr"]))
        if kind in ("commit", "merge"):
            knots += 1
        if kind in ("kb", "page"):
            pages += 1
    return sorted(prs), knots, pages


def _ledger_tokens(row: Mapping[str, Any]) -> int | None:
    """Input + output + cache writes — the tokens the run itself made the
    provider process. Cache *reads* are excluded (a warm seat's re-reads of
    one scroll would dwarf everything it did). ``None`` when none is known."""
    parts = [_int(row.get(k)) for k in ("tokens_input", "tokens_output", "tokens_cache_creation")]
    known = [p for p in parts if p is not None]
    return sum(known) if known else None


def run_topics(account_home: Path | None, compiled: list[Any]) -> dict[str, list[str]]:
    """run id → the topics whose index (aliases resolved) holds an act of it —
    every kind of row (``message``, ``strand``, ``event``, ``produce``, …)."""
    out: dict[str, list[str]] = {}
    for item in compiled:
        slug = item.topic.slug
        for row in heddles_mod.index(account_home, slug):
            run = str(row.get("run") or "")
            if run and slug not in out.setdefault(run, []):
                out[run].append(slug)
    return out


_RUN_TOPIC_CACHE: dict[str, tuple[tuple, str | None]] = {}
_RUN_TOPIC_LOCK = threading.Lock()


def run_md_topic(brr_dir: Path, run_id: str, names: Mapping[str, Any]) -> str | None:
    """The topic a run was stamped with — ``run.md``'s ``topic:`` (the frame
    writes the waking event's assignment there) — resolved through *names*
    (slug or ``ids:`` alias → topic). ``null``/unknown ⇒ ``None``. Cached by
    the file's size and mtime."""
    if not run_id or "/" in run_id or run_id.startswith("."):
        return None
    path = brr_dir / "runs" / run_id / "run.md"
    try:
        stat = path.stat()
    except OSError:
        return None
    ident = (stat.st_size, stat.st_mtime_ns)
    with _RUN_TOPIC_LOCK:
        cached = _RUN_TOPIC_CACHE.get(str(path))
    if cached is not None and cached[0] == ident:
        raw = cached[1]
    else:
        run = _load_run(brr_dir, run_id)
        raw = str(((getattr(run, "meta", None) or {}).get("topic")) or "").strip().strip("`").lower() or None
        with _RUN_TOPIC_LOCK:
            _RUN_TOPIC_CACHE[str(path)] = (ident, raw)
    if not raw:
        return None
    if raw.startswith("topic:"):
        raw = raw[len("topic:"):].strip()
    if raw in ("null", "none", "new") or not heddles_mod.SLUG_RE.match(raw):
        return None
    topic = names.get(raw)
    return topic.slug if topic is not None else raw


def _with_stamp(topics: list[str], stamped: str | None) -> list[str]:
    return topics + [stamped] if stamped and stamped not in topics else list(topics)


_CLAIM_SPLIT_RE = re.compile(r"[\s·]+")


def _claim_slugs(text: str | None) -> list[str]:
    """``topics: a b`` / ``a b`` (the ``.topics`` control's two shapes, the
    node's rendered one) → slug-shaped tokens, in order."""
    first = (text or "").splitlines()[0].strip() if (text or "").strip() else ""
    if first.lower().startswith("topics:"):
        first = first[len("topics:"):].strip()
    return [t for t in _CLAIM_SPLIT_RE.split(first) if heddles_mod.SLUG_RE.match(t)]


def run_claim_topics(
    account_home: Path | None, repo_label: str | None, run_id: str, outbox_dir: Path | None = None
) -> list[str]:
    """The run's own ``.topics`` claim — the live outbox's control
    (:func:`brr.run_ledger.read_run_topics_control`), else the copy closeout
    renders on its node, ``<home>/runs/<repo>/<run>/topics.md``
    (``daemon._persist_run_topics``) — what the dashboard shows."""
    from .. import run_ledger

    if outbox_dir is not None:
        claimed = run_ledger.read_run_topics_control(outbox_dir)
        if claimed:
            return list(claimed)
    if account_home is None or not repo_label or not run_id:
        return []
    from .. import account

    node = Path(account_home) / "runs" / account.slug_repo_label(repo_label) / run_id / "topics.md"
    return _claim_slugs(_read_text(node))


def merge_topics(names: Mapping[str, Any], *groups: Iterable[str | None]) -> list[str]:
    """Deduped, in group order; a name the topics know resolves to its slug
    (an ``ids:`` alias to the topic that absorbed it), an unknown slug-shaped
    name stays as written — a claim may name a topic not minted yet."""
    out: list[str] = []
    for group in groups:
        for name in group or ():
            if not name:
                continue
            topic = names.get(name)
            slug = topic.slug if topic is not None else name
            if heddles_mod.SLUG_RE.match(slug) and slug not in out:
                out.append(slug)
    return out


def _live_row(brr_dir: Path, run_id: str, topics: list[str]) -> dict[str, Any] | None:
    run = _load_run(brr_dir, run_id)
    if run is None:
        return None
    meta = run.meta if isinstance(run.meta, dict) else {}
    outbox_dir, portal = find_outbox(brr_dir, run_id, run)
    relics = tail_rows(outbox_dir / ".relics.jsonl", 10_000) if outbox_dir else []
    prs, knots, pages = _refs_summary(relics)
    shell, core = _shell_core(meta, portal)
    name = _first_line(_read_text(outbox_dir / ".name")) if outbox_dir else None
    title = str(meta.get("title") or "") or None
    return {
        "run": run_id,
        "started": _started(run),
        "ended": None,
        "name": name or title,
        "title": title,
        "mood": _first_line(_read_text(outbox_dir / ".mood")) if outbox_dir else None,
        "shell": shell,
        "core": core,
        "topics": topics,
        "prs": prs,
        "knots": knots,
        "pages": pages,
        "tokens": None,
        "parent": str(meta.get("spawn_parent_run_id") or "") or None,
        "trail": [],
    }


def read_cloth(
    brr_dir: Path,
    account_home: Path | None,
    compiled: list[Any],
    live: _Live | None,
    strands: list[dict[str, Any]],
    where: "Where | None" = None,
    repo_label: str | None = None,
) -> dict[str, Any]:
    """``cloth`` — the last :data:`CLOTH_LAST` runs of ``<brr>/run-ledger.jsonl``
    (one row per run, its last entry), then the live run and its live strands
    when the ledger has not closed them yet. ``ended`` is ``null`` for the live
    run always; ``duration_s`` is ``ended − started``, ``null`` while live.
    ``topics`` = the ``run.md`` stamp ∪ the run's own ``.topics`` claim ∪ the
    topic indexes, deduped in that order; ``trail`` = its last
    :data:`TRAIL_PLACES` distinct repo places, newest first (:func:`trail`)."""
    where = where or Where(_roots(brr_dir.parent, brr_dir), account_home, brr_dir)
    topics_by_run = _safe(lambda: run_topics(account_home, compiled), {})
    names = _safe(lambda: heddles_mod._topic_names(account_home), {}) if account_home else {}
    ledger = tail_rows(brr_dir / "run-ledger.jsonl", CLOTH_LAST * 2, lambda r: bool(r.get("run_id")))
    latest: dict[str, dict[str, Any]] = {}
    for row in ledger:
        latest.pop(str(row["run_id"]), None)
        latest[str(row["run_id"])] = row
    live_id = live.run_id if live is not None else None
    rows = []
    for run_id, row in list(latest.items())[-CLOTH_LAST:]:
        prs, knots, pages = _refs_summary(row.get("external_refs") or ())
        rows.append({
            "run": run_id,
            "started": row.get("started_at"),
            # A resumed seat has a closed ledger row for an earlier stint; the
            # live run's end is not written yet, whatever that row says.
            "ended": None if run_id == live_id else row.get("ended_at"),
            "name": row.get("name"),
            "title": None,
            "mood": None,
            "shell": row.get("runner_shell"),
            "core": row.get("runner_core"),
            "topics": merge_topics(
                names,
                [run_md_topic(brr_dir, run_id, names)],
                run_claim_topics(account_home, row.get("repo_label") or repo_label, run_id),
                topics_by_run.get(run_id, []),
            ),
            "trail": trail(brr_dir, run_id, where),
            "prs": prs,
            "knots": knots,
            "pages": pages,
            "tokens": _ledger_tokens(row),
            "parent": row.get("parent_run_id"),
        })
    open_ids = []
    if live is not None:
        open_ids.append(live.run_id)
    open_ids += [s["id"] for s in strands if s.get("id") and s.get("status") in ("live", "submitted")]
    for run_id in open_ids:
        if run_id in latest:
            continue
        live_outbox = find_outbox(brr_dir, run_id, _load_run(brr_dir, run_id))[0]
        topics = merge_topics(
            names,
            [run_md_topic(brr_dir, run_id, names)],
            run_claim_topics(account_home, repo_label, run_id, live_outbox),
            topics_by_run.get(run_id, []),
        )
        row = _safe(lambda: _live_row(brr_dir, run_id, topics), None)
        if row is not None:
            row["trail"] = trail(brr_dir, run_id, where)
        if row is not None:
            rows.append(row)
    for row in rows:
        if row["run"] in open_ids and row["run"] in latest:
            live_outbox = find_outbox(brr_dir, row["run"], _load_run(brr_dir, row["run"]))[0]
            row["topics"] = merge_topics(
                names, row["topics"], run_claim_topics(account_home, repo_label, row["run"], live_outbox)
            )
            live_row = _safe(lambda: _live_row(brr_dir, row["run"], row["topics"]), None) or {}
            for key in ("name", "title", "mood", "shell", "core"):
                if live_row.get(key):
                    row[key] = live_row[key]
        row["duration_s"] = _duration(row.get("started"), row.get("ended"))
    return {"rows": rows}


def _duration(started: Any, ended: Any) -> int | None:
    """``ended − started`` in whole seconds; ``None`` while live or unknown."""
    a, b = _epoch(started), _epoch(ended)
    if a is None or b is None or b < a:
        return None
    return int(round(b - a))


# ── tree ─────────────────────────────────────────────────────────────────


class _PlaceScan:
    """One boundaries file's places, read incrementally: ``(tree, place)`` →
    ``[last epoch, mutated]`` with tree ``repo`` or ``home``. Finished runs
    are read once per process."""

    __slots__ = ("offset", "ident", "places", "beads")

    def __init__(self) -> None:
        self.offset = 0
        self.ident: tuple = ()
        self.places: dict[tuple[str, str], list[Any]] = {}
        self.beads = 0


_SCANS: dict[str, _PlaceScan] = {}
_SCANS_LOCK = threading.Lock()


def scan_places(path: Path, where: Where) -> dict[tuple[str, str], list[Any]]:
    return _scan(path, where)[0]


TRAIL_PLACES = 8


def trail(brr_dir: Path, run_id: str, where: Where) -> list[dict[str, Any]]:
    """A run's last :data:`TRAIL_PLACES` distinct repo places, newest first,
    each ``{path, at}`` — from the same incremental read of its
    ``boundaries.jsonl`` the tree uses, so it costs nothing new per beat."""
    if not run_id or "/" in run_id or run_id.startswith("."):
        return []
    places = scan_places(brr_dir / "runs" / run_id / "boundaries.jsonl", where)
    rows = [(key[1], seen[0]) for key, seen in places.items() if key[0] == "repo" and seen[0] is not None]
    rows.sort(key=lambda kv: (-kv[1], kv[0]))
    return [{"path": path, "at": _iso(at)} for path, at in rows[:TRAIL_PLACES]]


def bead_count(path: Path, where: Where) -> int | None:
    """How many beads the file holds (the same incremental read)."""
    return _scan(path, where)[1]


def _scan(path: Path, where: Where) -> tuple[dict[tuple[str, str], list[Any]], int | None]:
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return {}, None
    with _SCANS_LOCK:
        scan = _SCANS.get(key)
        ident = (stat.st_ino, where.key())
        if scan is None or scan.ident != ident or stat.st_size < scan.offset:
            scan = _PlaceScan()
            scan.ident = ident
            _SCANS[key] = scan
        if stat.st_size > scan.offset:
            try:
                with path.open("rb") as handle:
                    handle.seek(scan.offset)
                    chunk = handle.read(stat.st_size - scan.offset)
            except OSError:
                return dict(scan.places), scan.beads
            end = chunk.rfind(b"\n")
            if end >= 0:
                scan.offset += end + 1
                for line in chunk[: end + 1].decode("utf-8", errors="replace").splitlines():
                    if '"act"' not in line and "session-start" not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict) or not _is_bead(row):
                        continue
                    scan.beads += 1
                    at = _epoch(row.get("at"))
                    mutated = row.get("act") == "mutate"
                    places, homes = row_paths(row, where)
                    for tree_key in [("repo", p) for p in places] + [("home", h) for h in homes]:
                        seen = scan.places.setdefault(tree_key, [None, False])
                        if at is not None and (seen[0] is None or at > seen[0]):
                            seen[0] = at
                        seen[1] = seen[1] or mutated
        return dict(scan.places), scan.beads


def read_tree(
    brr_dir: Path,
    where: Where,
    compiled: list[Any],
    beads: list[dict[str, Any]],
    cloth: Mapping[str, Any],
    now_epoch: float,
) -> dict[str, Any]:
    """``tree`` — two trees of the same shape. ``repo`` (and ``places``, its
    alias for one version): every repo place the beads touched and every one a
    cloth run touched in its own ``boundaries.jsonl``. ``home.places``: the
    same for the account home's ``dominion/ knowledge/ surface/ bench/``.
    ``knots`` counts the runs whose ``mutate`` rows touched a place (the
    ledger's commits carry no paths, so the run that changed the file is what
    the files attest); ``heat`` is :func:`brr.heddles.brightness` of the last
    touch — halves every hour."""
    last: dict[tuple[str, str], float | None] = {}
    knots: dict[tuple[str, str], set[str]] = {}

    def touch(key: tuple[str, str], at: float | None) -> None:
        prior = last.get(key)
        if key not in last or (at is not None and (prior is None or at > prior)):
            last[key] = at if at is not None else prior

    for bead in beads:
        at = _epoch(bead.get("at"))
        for place in bead.get("places") or ():
            touch(("repo", place), at)
        for place in bead.get("home_places") or ():
            touch(("home", place), at)
    for row in (cloth or {}).get("rows") or ():
        run_id = str(row.get("run") or "")
        if not run_id or "/" in run_id:
            continue
        for key, (at, mutated) in scan_places(brr_dir / "runs" / run_id / "boundaries.jsonl", where).items():
            touch(key, at)
            if mutated:
                knots.setdefault(key, set()).add(run_id)

    def tree(kind: str) -> list[dict[str, Any]]:
        rows = [(key[1], at) for key, at in last.items() if key[0] == kind]
        ordered = sorted(rows, key=lambda kv: (-(kv[1] or 0.0), kv[0]))[:TREE_MAX]
        return [
            {
                "path": place,
                "heat": heddles_mod.brightness(at, now_epoch),
                "last": _iso(at),
                "knots": len(knots.get((kind, place), ())),
                "topics": match_topics(compiled, places=[place]) if kind == "repo" else [],
            }
            for place, at in ordered
        ]

    repo = tree("repo")
    return {"repo": repo, "places": repo, "home": {"places": tree("home")}}


# ── bench ────────────────────────────────────────────────────────────────


def _bench_meta(text: str) -> tuple[dict[str, str], list[str]]:
    """A bench file's flat frontmatter and its repeated ``mark:`` lines, in order."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, []
    meta: dict[str, str] = {}
    marks: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            continue
        if key.strip() == "mark":
            if value.strip():
                marks.append(value.strip())
        else:
            meta[key.strip()] = value.strip()
    return meta, marks


def read_bench(account_home: Path | None) -> dict[str, Any]:
    """``bench`` — ``<account_home>/bench/<repo>/<place>/<commit>.md`` files
    (``outbox/fold.py`` writes them, ``outbox/mark.py`` appends ``mark:``):
    ``path`` is the file relative to the bench without ``.md`` — the value
    ``GET /loom/bench?path=`` takes — ``place`` its frontmatter's (else the
    segments between repo and commit). Symlinks are not followed."""
    if account_home is None:
        return {"folds": []}
    root = Path(account_home) / "bench"
    if not root.is_dir():
        return {"folds": []}
    folds = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if not name.endswith(".md") or path.is_symlink():
                continue
            rel = path.relative_to(root)
            if len(rel.parts) < 2:
                continue
            meta, marks = _bench_meta(_read_text(path) or "")
            folds.append({
                "path": rel.with_suffix("").as_posix(),
                "place": meta.get("place") or "/".join(rel.parts[1:-1]) or ".",
                "marks": marks,
            })
            if len(folds) >= BENCH_MAX:
                return {"folds": folds}
    return {"folds": folds}


# ── the contract ─────────────────────────────────────────────────────────


def _repo_label(live: _Live | None, brr_dir: Path) -> str | None:
    if live is not None:
        run = live.portal.get("run") if isinstance(live.portal.get("run"), dict) else {}
        label = run.get("repo") or live.meta.get("repo_label")
        if label:
            return str(label)
    last = tail_rows(brr_dir / "run-ledger.jsonl", 1, lambda r: bool(r.get("repo_label")))
    return str(last[0]["repo_label"]) if last else None


def locate(repo_root: Path | str, account_home: Path | str | None) -> Where:
    """The coordinates :func:`build` reads with — for the bench pages."""
    brr_dir = _brr_dir(Path(repo_root))
    home = Path(account_home) if account_home else None
    return Where(_roots(Path(repo_root), brr_dir), home, brr_dir)


def build(repo_root: Path | str, account_home: Path | str | None, *, now: object = None) -> dict[str, Any]:
    """The loom screen's state — see the module docstring for every source.

    *now* (epoch, ISO time or datetime) pins the clock for heat, elapsed time
    and ``at``; omitted ⇒ the wall clock. Never writes; never raises unless
    ``BRNRD_LOOM_STRICT`` is set.
    """
    now_epoch = _epoch(now) if now is not None else None
    if now_epoch is None:
        now_epoch = time.time()
    repo_root = Path(repo_root)
    home = Path(account_home) if account_home else None
    brr_dir = _brr_dir(repo_root)
    roots = _roots(repo_root, brr_dir)

    shuttle = _safe(lambda: read_shuttle(home), None)
    live = _safe(lambda: find_live(brr_dir, shuttle), None)
    compiled = _safe(lambda: compiled_topics(home), [])
    boundaries = []
    if live is not None:
        boundaries = _safe(
            lambda: tail_rows(brr_dir / "runs" / live.run_id / "boundaries.jsonl", BEADS_LAST * 4),
            [],
        )
    beaded = [row for row in boundaries if _is_bead(row)][-BEADS_LAST:]

    where = Where(roots, home, brr_dir)
    source = str(getattr(live.run, "source", "") or "") if live is not None else ""
    hud = _safe(lambda: read_hud(brr_dir, live, boundaries, where), None)
    first_n = None
    if live is not None:
        total = _safe(lambda: bead_count(brr_dir / "runs" / live.run_id / "boundaries.jsonl", where), None)
        first_n = max(0, total - len(beaded)) if total is not None else None
    beads = _safe(
        lambda: read_beads(beaded, where, compiled, live.run_id if live else "", source=source, first_n=first_n),
        [],
    )
    repo_label = _safe(lambda: _repo_label(live, brr_dir), None)
    cloth = _safe(
        lambda: read_cloth(brr_dir, home, compiled, live, (hud or {}).get("strands") or [], where, repo_label),
        {"rows": []},
    )
    return {
        "at": _iso(now_epoch),
        "beat_ms": BEAT_MS,
        "repo": repo_label,
        "shuttle": shuttle,
        "run": _safe(lambda: read_run(live, now_epoch), None),
        "hud": hud,
        "heddles": _safe(lambda: read_heddles(compiled, live.portal if live else None), []),
        "warp": _safe(lambda: read_warp(home), {"goals": [], "items": []}),
        "beads": beads,
        "cloth": cloth,
        "tree": _safe(
            lambda: read_tree(brr_dir, where, compiled, beads, cloth, now_epoch),
            {"repo": [], "places": [], "home": {"places": []}},
        ),
        "bench": _safe(lambda: read_bench(home), {"folds": []}),
    }


#: The contract's top-level keys, in order (the tests pin them).
KEYS = ("at", "beat_ms", "repo", "shuttle", "run", "hud", "heddles", "warp", "beads", "cloth", "tree", "bench")
