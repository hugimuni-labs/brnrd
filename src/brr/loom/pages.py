"""The bench pages — ``GET /loom/page/<kind>``: one thing, joined from disk.

The state (:mod:`brr.loom.state`) is the screen's row for everything; a page
is what opens on the bench when one row is clicked (design-the-loom.md §4:
"whatever is clicked opens here"). Five kinds, each a pure read:

- ``bead``   — one boundary row of a run, whole;
- ``pass``   — one run: its contract, card, produce, strands, last beads;
- ``item``   — one warp item file, parsed, with its siblings;
- ``place``  — one repo or home path: heat, the beads and passes that touched
  it, its folds;
- ``heddle`` — one topic: its file and its index.

Every page answers ``(payload, read)``: ``read`` lists the files (and the one
local ``git log``) consulted, so an unknown field stays ``null`` *and* says
where it was looked for. ``payload`` is ``None`` when the thing is not there.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .. import heddles as heddles_mod
from . import state as st

BEAD_ROWS_CAP_BYTES = 64 * 1024 * 1024
CONTRACT_CHARS = 600
PAGE_BEADS = 12
# a place's bands, kept per pass: enough to draw where the work landed in a
# file, bounded so one long run cannot make this page unbounded.
PLACE_SPANS = 60
HEDDLE_ROWS = 24
COMMITS_MAX = 50
_RUN_ID_RE = re.compile(r"^run-[A-Za-z0-9-]+$")
_ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_STATE_ORDER = {"ready": 0, "held": 1, "done": 2, "retired": 3}

Page = tuple[dict[str, Any] | None, list[str]]


def _rel(path: Path | str) -> str:
    return str(path)


def _all_beads(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            data = handle.read(BEAD_ROWS_CAP_BYTES)
    except OSError:
        return []
    rows = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        if '"act"' not in line and "session-start" not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and st._is_bead(row):
            rows.append(row)
    return rows


def _window_tokens(full: Mapping[str, Any] | None) -> int | None:
    """A context-window size, if the HUD's ``resources.context_window`` carries
    one as a number (today it says ``no window size yet``)."""
    facet = ((full or {}).get("resources") or {}).get("context_window")
    if not isinstance(facet, dict):
        return None
    for key in ("window_tokens", "window", "size_tokens", "limit_tokens"):
        value = st._int(facet.get(key))
        if value is not None:
            return value
    return None


# ── bead ─────────────────────────────────────────────────────────────────


def bead_page(repo_root: Path | str, account_home: Path | str | None, run_id: str, n: int) -> Page:
    """``{run, n, at, act, place_kind, places, home_places, detail_full,
    result_bytes, tools, ctx_after, delta, window_tokens, chip, prev, next}``.

    *n* indexes the run's beads in file order (``beads[].n``); negative counts
    from the end. ``result_bytes`` is the row's ``out_bytes``;
    ``window_tokens`` a number from the run portal's HUD
    ``resources.context_window`` if it has one; ``chip`` the row's ``inject``
    first line."""
    where = st.locate(repo_root, account_home)
    if not _RUN_ID_RE.match(run_id or ""):
        return None, []
    path = where.brr_dir / "runs" / run_id / "boundaries.jsonl"
    read = [_rel(path)]
    rows = _all_beads(path)
    index = n + len(rows) if n < 0 else n
    if not 0 <= index < len(rows):
        return None, read
    row = rows[index]
    run = st._load_run(where.brr_dir, run_id)
    read.append(_rel(where.brr_dir / "runs" / run_id / "run.md"))
    outbox, portal = st.find_outbox(where.brr_dir, run_id, run)
    full = None
    if portal:
        from .. import hud as hud_mod

        read.append(_rel(outbox / "portal-state.json"))
        full = hud_mod.HUD.from_dict(portal).to_dict()
    ctx = row.get("ctx") if isinstance(row.get("ctx"), dict) else {}
    places, homes = st.row_paths(row, where)
    return {
        "run": run_id,
        "n": index,
        "at": row.get("at"),
        "act": row.get("act") or row.get("phase"),
        "place_kind": st.place_kind(row, where, source=str(getattr(run, "source", "") or "")),
        "places": places,
        "home_places": homes,
        "detail_full": row.get("detail") if isinstance(row.get("detail"), str) else None,
        "result_bytes": st._int(row.get("out_bytes")),
        "tools": row.get("tools") if isinstance(row.get("tools"), list) else None,
        "ctx_after": st._int(ctx.get("tokens_after")),
        "delta": st._int(ctx.get("delta")),
        "window_tokens": _window_tokens(full),
        "chip": st._first_line(row.get("inject")) if isinstance(row.get("inject"), str) else None,
        "prev": index - 1 if index > 0 else None,
        "next": index + 1 if index + 1 < len(rows) else None,
    }, read


# ── pass ─────────────────────────────────────────────────────────────────


def _ledger_row(brr_dir: Path, run_id: str) -> dict[str, Any] | None:
    rows = st.tail_rows(brr_dir / "run-ledger.jsonl", 1, lambda r: r.get("run_id") == run_id)
    return rows[0] if rows else None


def _event_body(brr_dir: Path, event_id: str, read: list[str]) -> str | None:
    from .. import protocol

    if not event_id or "/" in event_id or event_id.startswith("."):
        return None
    for directory in (brr_dir / "inbox",):
        path = directory / f"{event_id}.md"
        read.append(_rel(path))
        event = protocol._read_event(path) if path.is_file() else None
        if event is not None:
            return str(event.get("body") or "")[:CONTRACT_CHARS] or None
    return None


def _git_commits(repo_dir: Path, branch: str, read: list[str]) -> list[dict[str, str]] | None:
    """``git log main..<branch>`` (else ``origin/<branch>``) in the host
    checkout — a local read. The strand pin (``GIT_DIR``/``GIT_WORK_TREE``) is
    dropped so the host's refs answer. ``None`` when git cannot say."""
    if not branch or not re.match(r"^[\w./-]+$", branch) or ".." in branch:
        return None
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    for ref in (branch, f"origin/{branch}"):
        cmd = ["git", "-C", str(repo_dir), "log", "--no-color", f"--max-count={COMMITS_MAX}",
               "--format=%h%x09%s", f"main..{ref}", "--"]
        read.append("$ " + " ".join(cmd[3:]))
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode == 0:
            out = []
            for line in done.stdout.splitlines():
                sha, _, subject = line.partition("\t")
                if sha:
                    out.append({"sha": sha, "subject": subject})
            return out
    return None


def pass_places(boundaries: Path, where: Any, *, rows: int = 4000) -> list[dict[str, Any]]:
    """One run's repo places with its **own measured weight** on each —
    ``({path, touches, reads, writes, covered, deepest, whole, spans, last}
    heaviest first, rows_scanned)``.

    ``cloth.rows[].trail`` is eight places and a timestamp
    (:data:`brr.loom.state.TRAIL_PLACES`) — enough to say *where*, never
    enough to say *how hard*, which is why every place in the per-run view
    used to be drawn at one brightness. The measurement that answers it has
    been sitting on the boundary rows since #2011: ``chunks``, with ``rel``
    already joined to the tree's own vocabulary (#2023). This reads the run's
    boundaries once and totals them per place.

    ``spans`` is kept (capped at :data:`PLACE_SPANS`) because a band's
    POSITION is the reading, not its count: forty reads of one function and
    one read of forty files are the same number and a different morning. What
    is deliberately *not* here is any aggregate that would hide that — the
    caller unions bands, it never sums them (the same rule ``_dirty_chunks``
    states about its own overlapping sets).

    A place with no chunk record is simply absent: this is the measured half,
    and a caller that also wants presence joins it to the trail. Absence of a
    record is not a weight of zero.
    """
    out: dict[str, dict[str, Any]] = {}
    scanned = st.tail_rows(boundaries, rows, st._is_bead)
    for row in scanned:
        at = row.get("at")
        for chunk in row.get("chunks") or ():
            if not isinstance(chunk, dict):
                continue
            rel = chunk.get("rel")
            if not isinstance(rel, str) or not rel:
                rel = heddles_mod.relative_place(str(chunk.get("path") or ""), where.roots)
            if not rel or str(rel).startswith(st.TREE_SKIP_PREFIXES):
                continue
            entry = out.setdefault(rel, {"path": rel, "touches": 0, "reads": 0, "writes": 0,
                                         "covered": 0, "deepest": 0, "whole": 0, "spans": [],
                                         "last": None})
            entry["touches"] += 1
            write = str(chunk.get("kind") or "") == "write"
            entry["writes" if write else "reads"] += 1
            if at and (entry["last"] is None or str(at) > str(entry["last"])):
                entry["last"] = at
            start, end = st._int(chunk.get("from")), st._int(chunk.get("to"))
            if start is None:
                entry["whole"] += 1
                continue
            end = start if end is None else end
            low, high = min(start, end), max(start, end)
            entry["covered"] += high - low + 1
            entry["deepest"] = max(entry["deepest"], high)
            if len(entry["spans"]) < PLACE_SPANS:
                entry["spans"].append({"from": low, "to": high, "kind": "write" if write else "read"})
    return sorted(out.values(), key=lambda e: (-e["touches"], e["path"])), len(scanned)


def pass_page(repo_root: Path | str, account_home: Path | str | None, run_id: str) -> Page:
    """``{id, title, name, mood, status, contract, shell, core, started, ended,
    duration_s, parent, topics, branch, card, produce: {prs, commits, pages},
    report_path, report_exists, strands, beads, bead_total}``.

    ``beads`` is the last :data:`PAGE_BEADS`; ``bead_total`` is how many the
    run actually has, so a reader can say *"the last 12 of 340"* instead of
    presenting a tail as a life. It was already counted here and thrown away,
    which is the cheapest way there is to turn a bound into a false whole.
    ``None`` means the count could not be taken.

    ``places`` is :func:`pass_places` — this run's own repo places with the
    weight it measured on each, over all its boundaries rather than the
    twelve beads above. It is what makes a per-run view able to say *how
    hard*, not only *where*.

    Sources: ``<brr>/runs/<id>/run.md``, its outbox (``.name``, ``.mood``,
    ``.card``, ``portal-state.json``, ``.relics.jsonl``), the run's
    ``run-ledger.jsonl`` row, its dispatch event file (``contract`` = the body's
    first 600 chars), ``<brr>/forge-pr-state.json`` (PRs on its branch), the
    local ``git log main..<branch>``, ``produce.jsonl`` and the boundaries."""
    where = st.locate(repo_root, account_home)
    home = Path(account_home) if account_home else None
    brr_dir = where.brr_dir
    if not _RUN_ID_RE.match(run_id or ""):
        return None, []
    read = [_rel(brr_dir / "runs" / run_id / "run.md"), _rel(brr_dir / "run-ledger.jsonl")]
    run = st._load_run(brr_dir, run_id)
    ledger = _ledger_row(brr_dir, run_id)
    if run is None and ledger is None:
        return None, read
    meta = getattr(run, "meta", None) or {}
    ledger = ledger or {}
    outbox, portal = st.find_outbox(brr_dir, run_id, run)
    if outbox is not None:
        read.append(_rel(outbox))
    status = str(getattr(run, "status", "") or "") or None
    live = status in st._STRAND_LIVE
    shell, core = st._shell_core(meta, portal)
    started = st._started(run) or ledger.get("started_at")
    ended = None if live else ledger.get("ended_at")
    compiled = st.compiled_topics(home)
    names = heddles_mod._topic_names(home) if home else {}
    topics = st.merge_topics(
        names,
        [st.run_md_topic(brr_dir, run_id, names)],
        st.run_claim_topics(home, ledger.get("repo_label") or meta.get("repo_label"), run_id, outbox),
        st.run_topics(home, compiled).get(run_id, []),
    )

    branch = str(meta.get("spawn_contract_branch") or meta.get("branch_name") or "") or None
    relics = st.tail_rows(outbox / ".relics.jsonl", 10_000) if outbox else []
    refs = list(ledger.get("external_refs") or []) + relics
    if branch is None:
        branch = next((str(r.get("name")) for r in refs if isinstance(r, dict) and r.get("kind") == "branch" and r.get("name")), None)

    prs: dict[int, dict[str, Any]] = {}
    forge = st._read_json(brr_dir / "forge-pr-state.json")
    read.append(_rel(brr_dir / "forge-pr-state.json"))
    for pr in (forge or {}).get("prs") or ():
        if isinstance(pr, dict) and branch and pr.get("branch") == branch and st._int(pr.get("number")) is not None:
            prs[int(pr["number"])] = {"number": int(pr["number"]), "url": pr.get("url"), "state": pr.get("state")}
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        number = st._int(ref.get("number")) if ref.get("kind") == "pr" else st._int(ref.get("pr")) if ref.get("kind") == "merge" else None
        if number is not None and number not in prs:
            prs[number] = {"number": number, "url": ref.get("url"), "state": "MERGED" if ref.get("kind") == "merge" else None}

    commits = _git_commits(brr_dir.parent, branch, read) if branch else None
    if commits is None:
        commits = [{"sha": str(r.get("sha")), "subject": str(r.get("subject") or "")}
                   for r in refs if isinstance(r, dict) and r.get("kind") in ("commit", "merge") and r.get("sha")] or None

    pages: list[str] = []
    from .. import hud as hud_mod

    produce_rows = hud_mod.read_produce_ledger(brr_dir, run_id)
    read.append(_rel(brr_dir / "runs" / run_id / hud_mod.PRODUCE_LEDGER_NAME))
    for row in produce_rows:
        if row.get("kind") == "page" and row.get("ref") and str(row["ref"]) not in pages:
            pages.append(str(row["ref"]))
    for ref in refs:
        if isinstance(ref, dict) and ref.get("kind") in ("kb", "page") and ref.get("path") and str(ref["path"]) not in pages:
            pages.append(str(ref["path"]))

    report = str(meta.get("spawn_contract_report") or "") or None
    strands = st._strands(brr_dir, portal, where) if portal else []
    known = {s.get("id") for s in strands}
    for row in st.tail_rows(brr_dir / "run-ledger.jsonl", 5000, lambda r: r.get("parent_run_id") == run_id):
        if row.get("run_id") not in known:
            known.add(row.get("run_id"))
            strands.append({"id": row.get("run_id"), "title": row.get("name"), "status": "done"})

    boundaries = brr_dir / "runs" / run_id / "boundaries.jsonl"
    read.append(_rel(boundaries))
    tail = st.tail_rows(boundaries, PAGE_BEADS, st._is_bead)
    total = st.bead_count(boundaries, where)
    beads = st.read_beads(
        tail, where, compiled, run_id, source=str(getattr(run, "source", "") or ""),
        first_n=max(0, total - len(tail)) if total is not None else None,
    )
    places, scanned = pass_places(boundaries, where)
    event_id = str(getattr(run, "event_id", "") or ledger.get("event_id") or "")
    name = st._first_line(st._read_text(outbox / ".name")) if outbox else None
    return {
        "id": run_id,
        "title": str(meta.get("title") or "") or name or ledger.get("name"),
        "name": name or ledger.get("name"),
        "mood": st._first_line(st._read_text(outbox / ".mood")) if outbox else None,
        "status": status or ("done" if ledger else None),
        "contract": _event_body(brr_dir, event_id, read),
        "shell": shell or ledger.get("runner_shell"),
        "core": core or ledger.get("runner_core"),
        "started": started,
        "ended": ended,
        "duration_s": st._duration(started, ended),
        "parent": str(meta.get("spawn_parent_run_id") or "") or ledger.get("parent_run_id"),
        "topics": topics,
        "branch": branch,
        "card": st.read_card(outbox) if outbox else None,
        "produce": {"prs": sorted(prs.values(), key=lambda p: p["number"]), "commits": commits, "pages": pages},
        "report_path": report,
        "report_exists": Path(report).is_file() if report else None,
        "strands": strands,
        "beads": beads,
        "bead_total": total,
        "places": places,
        # the scan's own bottom, said: `places` is taken off the last
        # `places_scanned` bead rows, and a run with more beads than that has
        # early work this page cannot see. A reader compares it to
        # `bead_total` rather than assuming the sum is the whole life.
        "places_scanned": scanned,
    }, read


# ── item ─────────────────────────────────────────────────────────────────


def item_page(account_home: Path | str | None, item_id: str) -> Page:
    """``{id, title, type, topics, needs, advances, refs, prompt, metric,
    body, state, taken, siblings}`` from ``<home>/surface/warp/<id>.md``
    (:func:`brr.items.parse_item`). ``siblings``: the other items sharing its
    first topic, ready first, then held, done, retired."""
    from .. import items

    if not account_home or not _ITEM_ID_RE.match(item_id or ""):
        return None, []
    warp = Path(account_home) / "surface" / items.WARP_DIRNAME
    path = items.resolve_item(warp, item_id)
    read = [_rel(warp / f"{item_id}.md")]
    item = items.parse_item(path) if path else None
    if item is None:
        return None, read
    loaded = items.load_items(warp)
    by_id = {i.id: i for i in loaded}
    siblings = []
    if item.topics:
        for other in loaded:
            if other.id != item.id and other.type != items.GOAL_TYPE and item.topics[0] in other.topics:
                siblings.append({"id": other.id, "title": other.headline, "state": st.item_state(other, by_id)})
        siblings.sort(key=lambda r: (_STATE_ORDER.get(r["state"], 9), items._id_sort_key(by_id[r["id"]])))
    taken = [t for t in item.taken if t.startswith("run-")]
    return {
        "id": item.id,
        "title": item.headline,
        "type": item.type,
        "topics": list(item.topics),
        "needs": list(item.needs),
        "advances": list(item.advances),
        "refs": item.refs or None,
        "prompt": item.prompt,
        "metric": item.metric,
        "body": item.body or None,
        "state": st.item_state(item, by_id) if item.type != items.GOAL_TYPE else item.state,
        "taken": taken[-1] if taken else None,
        "siblings": siblings,
    }, read


# ── place ────────────────────────────────────────────────────────────────


def _clean_place(path: str) -> str | None:
    text = (path or "").strip().replace("\\", "/").strip("/")
    if not text or text.startswith("~"):
        return None
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return text


TEXT_LINES = 200
_BINARY_PROBE = 8192
_GH_REMOTE_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def _git_dir_of(start: Path, stop: Path) -> tuple[Path | None, Path | None]:
    """``(worktree top, common git dir)`` of the checkout holding *start*,
    walking up no further than *stop*; ``.git`` files (worktrees) followed."""
    here = start if start.is_dir() else start.parent
    stop = stop.resolve()
    while True:
        dot = here / ".git"
        if dot.is_dir():
            return here, dot
        if dot.is_file():
            text = st._read_text(dot) or ""
            match = re.match(r"gitdir:\s*(.+)", text.strip())
            if match:
                gitdir = Path(match.group(1).strip())
                gitdir = gitdir if gitdir.is_absolute() else (here / gitdir)
                common = st._read_text(gitdir / "commondir")
                if common:
                    common_path = Path(common.strip())
                    gitdir = common_path if common_path.is_absolute() else gitdir / common_path
                return here, gitdir.resolve()
        if here.resolve() == stop or here.parent == here:
            return None, None
        here = here.parent


def gh_url(file_path: Path, stop: Path, read: list[str]) -> str | None:
    """``https://github.com/<org>/<repo>/blob/<default branch>/<path>`` for a
    file in a checkout whose ``origin`` is GitHub — read from the checkout's
    git ``config`` and ``refs/remotes/origin/HEAD`` (else ``main``), no git
    call. ``None`` when the remote is not GitHub or there is no checkout."""
    top, gitdir = _git_dir_of(file_path, stop)
    if top is None or gitdir is None:
        return None
    read.append(_rel(gitdir / "config"))
    config = st._read_text(gitdir / "config") or ""
    url = None
    in_origin = False
    for line in config.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_origin = stripped.replace(" ", "") in ('[remote"origin"]',)
            continue
        if in_origin:
            key, _, value = stripped.partition("=")
            if key.strip() == "url":
                url = value.strip()
                break
    match = _GH_REMOTE_RE.match(url or "")
    if not match:
        return None
    head = st._read_text(gitdir / "refs" / "remotes" / "origin" / "HEAD") or ""
    branch_match = re.match(r"ref:\s*refs/remotes/origin/(\S+)", head.strip())
    branch = branch_match.group(1) if branch_match else "main"
    try:
        rel = file_path.resolve().relative_to(top.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    return f"https://github.com/{match.group(1)}/{match.group(2)}/blob/{branch}/{rel}"


def file_text(file_path: Path, start: int | None, end: int | None) -> dict[str, Any]:
    """``{from, to, lines, total, binary, text}`` — the first
    :data:`TEXT_LINES` lines, or ``start``..``end`` (1-based, inclusive,
    capped at :data:`TEXT_LINES` lines). A file with a NUL byte in its first
    8 KB is binary: no text."""
    try:
        with file_path.open("rb") as handle:
            probe = handle.read(_BINARY_PROBE)
    except OSError:
        return {"from": None, "to": None, "total": None, "binary": None, "text": None}
    if b"\0" in probe:
        return {"from": None, "to": None, "total": None, "binary": True, "text": None}
    lines = (st._read_text(file_path) or "").splitlines()
    first = max(1, start or 1)
    last = min(len(lines), end if end is not None else first + TEXT_LINES - 1, first + TEXT_LINES - 1)
    chunk = lines[first - 1:last] if last >= first else []
    return {"from": first if chunk else None, "to": last if chunk else None, "total": len(lines),
            "binary": False, "text": "\n".join(chunk)}


# ── attention: which lines a pass read or edited ─────────────────────────

_SEGMENT_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_SED_RANGE_RE = re.compile(r"(\d+)(?:\s*,\s*(\d+))?p")
_EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit", "apply_patch"})


def _tokens(segment: str) -> list[str]:
    import shlex

    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _names_place(token: str, cwd: str, place: str, where: Any) -> bool:
    if not token or token.startswith("-"):
        return False
    value = token if token.startswith("/") else (f"{cwd.rstrip('/')}/{token}" if cwd.startswith("/") else token)
    if st.home_place(value, where) == place:
        return True
    return heddles_mod.relative_place(value, where.roots) == place


def chunk_ranges(row: Mapping[str, Any], place: str, where: Any) -> tuple[list[dict], dict] | None:
    """The spans *row* **measured** in *place*, from the ``chunks`` the hook
    recorded — or ``None`` when this row's chunks say nothing about this place.

    #2021 made a write stop being a flag: ``git diff --unified=0`` puts the
    real hunks on the boundary row, beside the reads the shell parser already
    ranged. :func:`attention_of_row` was written before that and never looked:
    it re-derived every range by parsing ``detail`` as text, so an ``Edit`` on
    a file whose exact changed lines were sitting in ``row["chunks"]`` landed
    in ``unranged["edit"]`` — *twelve* of them on ``src/brr/loom/state.py`` in
    the live feed the day this was written, a file whose read bands were drawn
    to the line. The zoom's innermost level was being fed a guess while the
    measurement lay one key away.

    Precedence is **per place, not per row**: chunks are capped
    (:data:`brr.hooks.SHELL_PLACE_PATHS_MAX`) and best-effort, so a row whose
    chunks name ``a.py`` may still have read ``b.py`` in a ``grep`` the cap
    cut off. When this row's chunks name *this* place, they are the account of
    it and the text parser is not consulted; when they do not, ``None`` hands
    the question back.

    A write with no span (``changed: true`` — git could not speak, so the
    interception guess is all there is) is the honest ``unranged`` it always
    was: counted, never drawn as a line.
    """
    chunks = row.get("chunks")
    if not isinstance(chunks, list):
        return None
    found: list[dict] = []
    unranged = {"read": 0, "edit": 0}
    named = False
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        rel = chunk.get("rel")
        if not isinstance(rel, str) or not rel:
            rel = heddles_mod.relative_place(str(chunk.get("path") or ""), where.roots)
        if rel != place:
            continue
        named = True
        kind = "edit" if str(chunk.get("kind") or "") == "write" else "read"
        start, end = st._int(chunk.get("from")), st._int(chunk.get("to"))
        if start is None:
            unranged[kind] += 1
            continue
        end = start if end is None else end
        found.append({"from": min(start, end), "to": max(start, end), "kind": kind, "src": "measured"})
    return (found, unranged) if named else None


def attention_of_row(row: Mapping[str, Any], place: str, where: Any, total: int | None) -> tuple[list[dict], dict]:
    """The line ranges one boundary row read or edited in *place*, and the
    touches it made that name no range (``{read: n, edit: n}``).

    Shapes, from ``detail``: ``sed -n 'A,Bp'`` · ``head -n N`` / ``head -N`` ·
    ``tail -n N`` (the last N of the file as it is now) · ``grep``/``rg``/``cat``
    naming the file (the whole file). A Read row (``tools: [Read]``, detail =
    the path, no offset recorded) reads the whole file; an edit tool row
    (Edit/Write/MultiEdit/apply_patch) records no range — counted, not drawn.
    ``git diff``/``show`` hunk headers in ``detail`` (``+++ b/<path>`` then
    ``@@ … +C,D @@``) are edits.

    All of that is the fallback. When the row's own ``chunks`` name this place
    the measurement wins outright (:func:`chunk_ranges`) and nothing here
    runs; every range this function returns therefore carries
    ``src: "parsed"`` and is a reading of a *command line*, not of the file.
    """
    measured = chunk_ranges(row, place, where)
    if measured is not None:
        found, missed = measured
        if total:
            found = [{**r, "from": min(r["from"], total), "to": min(r["to"], total)}
                     for r in found if r["from"] <= total]
        return found, missed
    detail = row.get("detail") if isinstance(row.get("detail"), str) else ""
    cwd = str(row.get("cwd") or "")
    tools = [str(t) for t in row.get("tools") or () if isinstance(t, str)]
    ranges: list[dict] = []
    unranged = {"read": 0, "edit": 0}
    whole = (1, total) if total else None
    if tools and tools[0] in ("Read",) and _names_place(detail.strip(), cwd, place, where):
        if whole:
            ranges.append({"from": whole[0], "to": whole[1], "kind": "read", "whole": True, "src": "parsed"})
        else:
            unranged["read"] += 1
        return ranges, unranged
    if tools and tools[0] in _EDIT_TOOLS and place in (detail.replace("\\", "/")):
        if any(_names_place(tok, cwd, place, where) for tok in detail.split()):
            unranged["edit"] += 1
            return ranges, unranged
    current_file = None
    for line in detail.splitlines() if "@@" in detail else ():
        header = re.match(r"^\+\+\+ b/(.+)$", line.strip())
        if header:
            current_file = header.group(1).strip()
            continue
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line.strip())
        if hunk and current_file and heddles_mod.relative_place(current_file, where.roots) == place:
            begin = int(hunk.group(1))
            length = int(hunk.group(2)) if hunk.group(2) is not None else 1
            ranges.append({"from": begin, "to": max(begin, begin + length - 1), "kind": "edit"})
    for segment in _SEGMENT_SPLIT_RE.split(detail):
        tokens = _tokens(segment)
        if not tokens:
            continue
        cmd = tokens[0].rsplit("/", 1)[-1]
        named = [t for t in tokens[1:] if _names_place(t, cwd, place, where)]
        if not named:
            continue
        if cmd == "sed" and "-n" in tokens:
            script = next((t for t in tokens[1:] if _SED_RANGE_RE.search(t) and not _names_place(t, cwd, place, where)), "")
            for match in _SED_RANGE_RE.finditer(script):
                begin = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else begin
                ranges.append({"from": min(begin, end), "to": max(begin, end), "kind": "read"})
        elif cmd == "head":
            count = _count_flag(tokens)
            ranges.append({"from": 1, "to": count if count is not None else 10, "kind": "read"})
        elif cmd == "tail":
            count = _count_flag(tokens)
            count = count if count is not None else 10
            if total:
                ranges.append({"from": max(1, total - count + 1), "to": total, "kind": "read"})
            else:
                unranged["read"] += 1
        elif cmd in ("grep", "rg", "cat", "less", "nl", "wc"):
            if whole:
                ranges.append({"from": whole[0], "to": whole[1], "kind": "read", "whole": True})
            else:
                unranged["read"] += 1
    ranges = [{**r, "src": "parsed"} for r in ranges]
    if total:
        ranges = [
            {**r, "from": min(r["from"], total), "to": min(r["to"], total)} for r in ranges if r["from"] <= total
        ]
    return ranges, unranged


def _count_flag(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens):
        if token == "-n" and index + 1 < len(tokens) and tokens[index + 1].lstrip("+").isdigit():
            return int(tokens[index + 1].lstrip("+"))
        if re.fullmatch(r"-n\+?\d+", token):
            return int(token[2:].lstrip("+"))
        if re.fullmatch(r"-\d+", token):
            return int(token[1:])
    return None


def merge_ranges(ranges: list[dict]) -> list[dict]:
    """Per kind, overlapping or touching ranges merge: ``count`` sums, ``last``
    is the newest. Whole-file touches (``whole: true`` — a grep, a cat, a Read
    with no offset) merge only with each other, so one ``grep`` never swallows
    the ranges a ``sed -n`` actually looked at. Sorted by kind, ranged first.

    ``src`` survives the merge and is the band's *weakest* claim: a measured
    git hunk merged with a range parsed out of a shell command reads
    ``mixed``, never ``measured``. The zoom's whole risk is drawing a guess in
    the same ink as a measurement — a band that is partly guessed must not be
    able to launder itself by touching one that is not."""
    out: list[dict] = []
    for kind, whole in (("read", False), ("edit", False), ("read", True), ("edit", True)):
        rows = sorted(
            (r for r in ranges if r["kind"] == kind and bool(r.get("whole")) == whole),
            key=lambda r: (r["from"], r["to"]),
        )
        merged: list[dict] = []
        for row in rows:
            src = row.get("src")
            if merged and row["from"] <= merged[-1]["to"] + 1:
                top = merged[-1]
                top["to"] = max(top["to"], row["to"])
                top["count"] += row.get("count", 1)
                top["last"] = max(filter(None, (top["last"], row.get("last"))), default=None)
                if top.get("src") != src:
                    top["src"] = "mixed"
            else:
                entry = {"from": row["from"], "to": row["to"], "kind": kind,
                         "count": row.get("count", 1), "last": row.get("last"), "src": src}
                if whole:
                    entry["whole"] = True
                merged.append(entry)
        out += merged
    return out


def place_page(
    repo_root: Path | str, account_home: Path | str | None, path: str, state: Mapping[str, Any] | None = None,
    *, text_from: int | None = None, text_to: int | None = None, run: str | None = None,
) -> Page:
    """``{path, kind, tree, heat, last, knots, topics, gh_url, text, attention,
    unranged, beads, passes, folds, fold}``.

    ``kind`` is ``home`` for a path under the account home's
    ``dominion/ knowledge/ surface/ bench/`` that exists there, else ``file``.
    ``heat``/``last``/``knots``/``topics`` are the tree entry of *state* (the
    beat's :func:`brr.loom.state.build`). ``passes``: the cloth runs whose own
    boundaries touched it, newest first. ``beads``: the last 12 beads that
    touched it — the live run's, then those passes'. ``folds``: bench files
    whose ``place`` is it; ``fold`` the newest one's text. ``gh_url``: the file
    on GitHub (:func:`gh_url`). ``text``: :func:`file_text`, only for a file
    that resolves inside the repo or the home. ``attention``: every range the
    beads touching it read or edited, across the live run and those passes
    (:func:`attention_of_row`), merged (:func:`merge_ranges`); ``unranged``
    the touches that named no lines.

    ``run`` **scopes the page to one pass**: its beads, its passes row, its
    attention, and nobody else's. Unscoped, this page is a union over every
    run that ever touched the file — the right answer to *"what is this
    file"* and the wrong one to *"what did that run do here"*. The frozen
    inspection frame asks the second, and a union answering it is exactly the
    misattribution "a past tree in a present world" named: yesterday
    inspected, today's bands lit over it. ``scope`` echoes the run back so a
    caller cannot mistake which question it asked; ``scope_known`` is false
    when that run left no boundaries to read, so an empty page reads as *no
    record* rather than *no work*. Text is current-checkout only: a scope
    other than the live run returns ``text=None`` and
    ``text_basis="unavailable-for-pass"``. Recorded attention remains
    available, without a present-day file length supplied for whole-file
    ranges. No verified historical source snapshot is stored here."""
    where = st.locate(repo_root, account_home)
    place = _clean_place(path)
    if place is None:
        return None, []
    state = state if state is not None else st.build(repo_root, account_home)
    home = Path(account_home) if account_home else None
    is_home = bool(home) and place.split("/", 1)[0] in st.HOME_TREE_DIRS and (home / place).exists()
    tree_kind = "home" if is_home else "repo"
    read = ["state.json (this beat)"]
    tree = (state.get("tree") or {})
    entries = (tree.get("home") or {}).get("places") if is_home else tree.get("repo") or tree.get("places")
    entry = next((e for e in entries or () if e.get("path") == place), None)
    beads_key = "home_places" if is_home else "places"
    beads: list[dict[str, Any]] = []
    scope = run if _RUN_ID_RE.match(run or "") else None
    run = (state.get("run") or {}).get("id")
    for bead in (reversed(state.get("beads") or []) if not scope or scope == run else ()):
        if place in (bead.get(beads_key) or ()):
            beads.append({**bead, "run": run})
            if len(beads) >= PAGE_BEADS:
                break
    passes = []
    rows = list(reversed((state.get("cloth") or {}).get("rows") or []))
    for row in rows:
        run_id = str(row.get("run") or "")
        if not _RUN_ID_RE.match(run_id) or (scope and run_id != scope):
            continue
        boundaries = where.brr_dir / "runs" / run_id / "boundaries.jsonl"
        seen = st.scan_places(boundaries, where).get((tree_kind, place))
        if seen is None:
            continue
        read.append(_rel(boundaries))
        passes.append({"run": run_id, "name": row.get("name"), "last": st._iso(seen[0]), "mutated": bool(seen[1])})
        if len(beads) < PAGE_BEADS and run_id != run:
            for bead_row in reversed(st.tail_rows(boundaries, 4000, st._is_bead)):
                places, homes = st.row_paths(bead_row, where)
                if place in (homes if is_home else places):
                    beads.append({
                        "run": run_id, "at": bead_row.get("at"), "act": bead_row.get("act"),
                        "place_kind": st.place_kind(bead_row, where),
                        "detail": str(bead_row.get("detail") or "")[: st.DETAIL_CHARS] or None,
                    })
                    if len(beads) >= PAGE_BEADS:
                        break
    passes.sort(key=lambda r: r["last"] or "", reverse=True)
    root = home if is_home else where.brr_dir.parent
    file_path = (root / place) if root else None
    inside = False
    if file_path is not None:
        try:
            file_path.resolve().relative_to(root.resolve())
            inside = file_path.is_file()
        except (OSError, ValueError):
            inside = False
    historical = bool(scope and scope != (state.get("run") or {}).get("id"))
    # A run scopes the attention, not a source snapshot. The recorded ranges
    # span edits and revisions; HEAD (even a commit made during the run) is
    # not a verified version of those lines. Do not read live text as history.
    text = file_text(file_path, text_from, text_to) if inside and not historical else None
    text_basis = "unavailable-for-pass" if historical else "current-checkout"
    url = gh_url(file_path, root, read) if inside else None
    total = (text or {}).get("total")
    ranges: list[dict] = []
    unranged = {"read": 0, "edit": 0}
    runs_rows = [(run, None)] if run and (not scope or scope == run) else []
    runs_rows += [(p["run"], None) for p in passes if p["run"] != run]
    if scope:
        runs_rows = [r for r in runs_rows if r[0] == scope]
    for run_id, _ in runs_rows:
        boundaries = where.brr_dir / "runs" / str(run_id) / "boundaries.jsonl"
        for bead_row in st.tail_rows(boundaries, 4000, st._is_bead):
            found, missed = attention_of_row(bead_row, place, where, total)
            for item in found:
                item["last"] = bead_row.get("at")
            ranges += found
            unranged["read"] += missed["read"]
            unranged["edit"] += missed["edit"]
    folds = [f for f in (state.get("bench") or {}).get("folds") or () if f.get("place") == place]
    fold = None
    if folds and home:
        newest = sorted(folds, key=lambda f: f.get("path") or "")[-1]
        text_path = home / "bench" / f"{newest['path']}.md"
        read.append(_rel(text_path))
        fold = {"path": newest["path"], "marks": newest.get("marks") or [], "text": st._read_text(text_path)}
    scope_known = (where.brr_dir / "runs" / scope / "boundaries.jsonl").is_file() if scope else None
    if entry is None and not passes and not beads and not folds and not inside and not scope:
        return None, read
    return {
        "path": place,
        "scope": scope,
        "scope_known": scope_known,
        "text_basis": text_basis,
        "kind": "home" if is_home else "file",
        "tree": tree_kind,
        "heat": (entry or {}).get("heat"),
        "last": (entry or {}).get("last"),
        "knots": (entry or {}).get("knots"),
        "topics": (entry or {}).get("topics") or [],
        "gh_url": url,
        "text": text,
        "attention": merge_ranges(ranges),
        "unranged": unranged,
        "beads": beads[:PAGE_BEADS],
        "passes": passes,
        "folds": folds,
        "fold": fold,
    }, read


# ── heddle ───────────────────────────────────────────────────────────────


def heddle_page(account_home: Path | str | None, slug: str, state: Mapping[str, Any] | None = None) -> Page:
    """``{slug, title, rune, aliases, signature, lit, last_lit, rows, counts,
    total}`` — the topic file (:func:`brr.heddles.parse_topic_file`), the last
    24 rows of its index with aliases resolved (:func:`brr.heddles.index`) as
    ``{at, kind, ref, run}``, and the count of every row by kind."""
    if not account_home or not heddles_mod.SLUG_RE.match(slug or ""):
        return None, []
    home = Path(account_home)
    names = heddles_mod._topic_names(home)
    topic = names.get(slug)
    directory = heddles_mod.topics_dir(home)
    read = [_rel(directory / f"{slug}.md"), _rel(directory / f"{slug}{heddles_mod.INDEX_SUFFIX}")]
    if topic is None:
        return None, read
    rows = heddles_mod.index(home, topic.slug)
    counts: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("kind") or "")
        counts[kind] = counts.get(kind, 0) + 1
    lit = next((h for h in (state or {}).get("heddles") or () if h.get("slug") == topic.slug), {})
    return {
        "slug": topic.slug,
        "title": topic.title or None,
        "rune": topic.rune or None,
        "aliases": list(topic.aliases),
        "signature": topic.signature.as_dict(),
        "lit": lit.get("lit", 0.0),
        "last_lit": lit.get("last_lit"),
        "rows": [{"at": r.get("at"), "kind": r.get("kind"), "ref": r.get("ref"), "run": r.get("run") or None}
                 for r in rows[-HEDDLE_ROWS:]],
        "counts": counts,
        "total": len(rows),
    }, read
