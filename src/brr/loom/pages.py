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


def pass_page(repo_root: Path | str, account_home: Path | str | None, run_id: str) -> Page:
    """``{id, title, name, mood, status, contract, shell, core, started, ended,
    duration_s, parent, topics, branch, card, produce: {prs, commits, pages},
    report_path, report_exists, strands, beads}``.

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
    topics = st._with_stamp(st.run_topics(home, compiled).get(run_id, []), st.run_md_topic(brr_dir, run_id, names))

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


def place_page(
    repo_root: Path | str, account_home: Path | str | None, path: str, state: Mapping[str, Any] | None = None
) -> Page:
    """``{path, kind, tree, heat, last, knots, topics, beads, passes, folds, fold}``.

    ``kind`` is ``home`` for a path under the account home's
    ``dominion/ knowledge/ surface/ bench/`` that exists there, else ``file``.
    ``heat``/``last``/``knots``/``topics`` are the tree entry of *state* (the
    beat's :func:`brr.loom.state.build`). ``passes``: the cloth runs whose own
    boundaries touched it, newest first. ``beads``: the last 12 beads that
    touched it — the live run's, then those passes'. ``folds``: bench files
    whose ``place`` is it; ``fold`` the newest one's text."""
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
    run = (state.get("run") or {}).get("id")
    for bead in reversed(state.get("beads") or []):
        if place in (bead.get(beads_key) or ()):
            beads.append({**bead, "run": run})
            if len(beads) >= PAGE_BEADS:
                break
    passes = []
    rows = list(reversed((state.get("cloth") or {}).get("rows") or []))
    for row in rows:
        run_id = str(row.get("run") or "")
        if not _RUN_ID_RE.match(run_id):
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
    folds = [f for f in (state.get("bench") or {}).get("folds") or () if f.get("place") == place]
    fold = None
    if folds and home:
        newest = sorted(folds, key=lambda f: f.get("path") or "")[-1]
        text_path = home / "bench" / f"{newest['path']}.md"
        read.append(_rel(text_path))
        fold = {"path": newest["path"], "marks": newest.get("marks") or [], "text": st._read_text(text_path)}
    if entry is None and not passes and not beads and not folds:
        return None, read
    return {
        "path": place,
        "kind": "home" if is_home else "file",
        "tree": tree_kind,
        "heat": (entry or {}).get("heat"),
        "last": (entry or {}).get("last"),
        "knots": (entry or {}).get("knots"),
        "topics": (entry or {}).get("topics") or [],
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
