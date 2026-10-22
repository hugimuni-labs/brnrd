"""Snapshot v1: what the loom already knows, pushed to the home board.

The contract is threads/inbox/loom-snapshot.md in the self repo. Every field
is read from a projection the loom keeps anyway (the threads table, main's
READMEs, the ledger fold, loom/usage, config); a value it lacks is omitted.
Only thread READMEs and ledger heads are read: never people/** or a body.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .home import Home, thread_of
from .project import cached_projection, fold, is_notice, projection_cache, sender_threads

STATES = {"working", "holding", "idle", "parked", "failed", "molted", "settled", "stopped"}
# The threads table's words for a start waiting on fuel; the board says parked.
_PARKED = {"paced", "pending"}
_PR = re.compile(r"https://github\.com/[^\s/]+/[^\s/]+/pull/\d+")
_SENTENCE = re.compile(r"(?<=[.!?])\s")
LOG = 200
HEAD = 280
WISHES = 100

# Cadence, in seconds of ``clock``: tests replace both.
ASSEMBLE_EVERY = 3
DEBOUNCE = 10
IDLE = 60
clock = time.monotonic

_TEXTS: dict[str, str] = {}


def _iso(value) -> str:
    when = (datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float))
            else datetime.fromisoformat(value))
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _readmes(home: Home) -> dict[str, str]:
    """README text per thread on main; a blob is read once for its life."""
    from .lifecycle import main_tree
    from .selfrepo import git
    repo = home.root / "self"
    if not (repo / ".git").exists():
        return {p.parent.name: p.read_text() for p in (repo / "threads").glob("*/README.md")}
    _tip, blobs = main_tree(repo)
    found = {}
    for path, blob in blobs.items():
        parts = path.split("/")
        if len(parts) == 3 and parts[2] == "README.md":
            if blob not in _TEXTS:
                _TEXTS[blob] = git(repo, "cat-file", "blob", blob, check=False).stdout
            found[parts[1]] = _TEXTS[blob]
    return found


def _done(text: str) -> list[dict]:
    """``## Done`` lines; a line naming a PR is that PR's receipt."""
    rows, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line == "## Done"
        elif inside and line.startswith("- "):
            item = line[2:].strip()
            url = _PR.search(item)
            words = item.partition(" — ")[2] or item
            rows.append({"kind": "pr", "ref": url.group(0), "line": words} if url else {"line": item})
    return rows


def _plan(rows) -> dict | None:
    if rows is None:
        return None
    plan = {"done": sum(done for done, _ in rows), "total": len(rows)}
    open_rows = [text for done, text in rows if not done]
    if open_rows:
        plan["next"] = open_rows[0].rpartition(" · re: ")[0] or open_rows[0]
    return plan


def _state(now: str) -> str | None:
    word = now.split(" ", 1)[0]
    word = "parked" if word in _PARKED else word
    return word if word in STATES else None


def _fuel(home: Home, cores: dict[str, list[str]], floor: float) -> list[dict]:
    from .usage import _SHELL_NAME, _past_reset
    found = []
    for path in sorted((home.root / "loom/usage").glob("*.json")):
        shell = path.name.removesuffix(".json")
        try:
            payload = json.loads(path.read_text())
            gauges = [{"window": w["name"], "left_pct": w["left_pct"],
                       **({"resets_at": _iso(w["resets_at"])} if w.get("resets_at") else {})}
                      for w in payload["windows"]
                      if isinstance(w.get("name"), str) and isinstance(w.get("left_pct"), (int, float))]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if not _SHELL_NAME.fullmatch(shell) or not gauges:
            continue
        state = ("dry" if any(g["left_pct"] <= floor for g in gauges)
                 else "stale" if _past_reset(payload, time.time()) else "ok")
        found.append({"shell": shell, "state": state, "gauges": gauges,
                      **({"cores": cores[shell]} if cores.get(shell) else {})})
    return found


def _log_rows(accepted, writers, done_refs, wish_threads) -> list[dict]:
    rows = []
    for f in accepted:
        d = f.data
        if f.kind == "letter":
            strand = f.by.removeprefix("strand:") if f.by.startswith("strand:") else None
            thread = writers.get(strand) if strand else None
            if thread is None:
                try:
                    thread = thread_of(d.get("to"))
                except ValueError:
                    pass
            body = str(d.get("body") or "")
            ref = next((u for u in _PR.findall(body) if u in done_refs.get(thread, ())), None)
            kind = "notice" if is_notice(f) else "receipt" if ref else "letter"
            row = {"id": d.get("id") or f.id, "at": _iso(f.at), "from": d.get("from") or strand,
                   "to": d.get("to"), "thread": thread, "kind": kind, "head": body[:HEAD], "ref": ref}
        elif f.kind == "wish.result" and d.get("delivered"):
            row = {"id": f.id, "at": _iso(f.at), "from": f.by.removeprefix("strand:"),
                   "thread": wish_threads.get(d.get("wish")), "kind": "receipt",
                   "head": str(d.get("line") or "")[:HEAD], "ref": d.get("ref")}
        else:
            continue
        rows.append({k: v for k, v in row.items() if v})
    return rows[-LOG:]


def _bare(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if v not in (None, "", [], {})}


def _wish_rows(told, facts) -> list[dict]:
    """Open wishes first, then by the newest result, else the wish itself."""
    letters = {str(f.data.get("id", f.id)): f for f in facts if f.kind == "letter"}
    answers = {}
    for f in facts:
        if f.kind == "letter" and f.data.get("wish") and f.data.get("verdict"):
            old = answers.get(f.data["wish"])
            if old is None or _when(f.at) >= _when(old.at):
                answers[f.data["wish"]] = f

    def last(wish):
        return max((_when(r["at"]) for r in wish.results), default=_when(wish.at))
    rows = []
    for w in sorted(told.values(), key=lambda w: (not w.open, -last(w).timestamp(), w.id))[:WISHES]:
        says = []
        for say in w.says:
            # An unplaced say carries its quote 4th, at 0/None: it cites the whole letter.
            ident, offset, size = say[:3]
            letter = letters.get(ident)
            if letter is None:
                continue
            body = str(letter.data.get("body") or "")
            text = body[offset:] if size is None else body[offset:offset + size]
            says.append(_bare({"id": ident, "from": letter.data.get("from"),
                               "at": _iso(letter.at), "head": text[:HEAD]}))
        answer = answers.get(w.id)
        rows.append(_bare({
            "id": w.id, "thread": w.thread, "from": w.sender, "line": w.line, "state": w.state,
            "origin": w.origin, "at": _iso(w.at), "says": says, "reading": w.reading,
            "results": [_bare({"kind": r.get("kind"), "ref": r.get("ref"), "line": r.get("line"),
                               "delivered": bool(r.get("delivered")), "at": _iso(r["at"])})
                        for r in w.results],
            "verdict": _bare({"as": w.verdict["as"], "evidence": w.verdict.get("evidence"),
                              "at": _iso(w.verdict["at"])}) if w.verdict else None,
            "answer": {"verdict": answer.data["verdict"], "at": _iso(answer.at),
                       "head": str(answer.data.get("body") or "")[:HEAD]}
            if answer is not None and _when(answer.at) > _when(w.at) else None}))
    return rows


def assemble(home: Home, facts: list, config) -> dict:
    """The snapshot document, ``at`` included; pure reads of what is held."""
    from .lifecycle import CLOSED, main_snapshot
    from .threads import _table_inputs, opened_for, thread_table
    from .wishes import waits_on, wishes
    from .adapters import resolve
    table = [row for row in thread_table(home, facts) if row["thread"] != "operator"]
    main = main_snapshot(home)
    activity = cached_projection(facts, "thread-table", repr(main),
                                 lambda rows: _table_inputs(rows, main))[4]
    state = fold(facts)
    writers = sender_threads(facts)
    texts = _readmes(home)
    held = {strand: thread for thread, (strand, _gen) in state.holder.items()}
    ends = {f.data["strand"]: f.data["cause"] for f in state.accepted if f.kind == "attempt.ended"}
    leases, cores = {}, {}
    for f in state.accepted:
        if f.kind == "lease" and f.data.get("shell") and f.data.get("core"):
            leases.setdefault(f.data["thread"], {})[f.data["strand"]] = f
            cores.setdefault(f.data["shell"], set()).add(f.data["core"])
    told = wishes([f for f in facts if f.kind.startswith("wish") or f.kind == "letter"])
    from .receipts import receipts
    shipped = receipts(facts)
    # An open ask is the person's turn unless it waits on a thread that has not asked onward.
    heads = waits_on(told, main)
    waits: dict[str, list[str]] = {}
    for ident, head in sorted(heads.items()):
        waits.setdefault(told[ident].thread, []).append(head)
    asked = {t for t, hs in waits.items() if any(not h.startswith("thread:") for h in hs)}
    doc_threads, done_refs, present = [], {}, []
    for row in table:
        thread = row["thread"]
        data = main.get(thread, {})
        now = _state(row["now"])
        owed_by = row["owed-by"]
        if data.get("status") in CLOSED:
            turn = "none"
        elif "operator" in owed_by.split(", ") or thread in asked:
            turn = "yours"
        elif now in {"parked", "failed"}:
            turn = "blocked"
        elif row["owed"] or now == "working" or any(
                owed_by.startswith(w) for w in ("body:", "loom:", "opener:")):
            turn = "mine"
        else:
            turn = "none"
        strands = []
        for strand, lease in leases.get(thread, {}).items():
            word = now if held.get(strand) == thread else _state(ends.get(strand, ""))
            strands.append({"id": strand, "body": f"{lease.data['shell']}/{lease.data['core']}",
                            **({"state": word} if word else {}), "since": _iso(lease.at)})
        done = _done(texts.get(thread, ""))
        done_refs[thread] = {d["ref"] for d in done if "ref" in d}
        plan = _plan(data.get("plan_rows"))
        last = activity.get(thread)
        title = _SENTENCE.split(data.get("headline", ""), 1)[0]
        entry = {"id": thread, "title": title, "status": data.get("status"), "turn": turn,
                 "blocked_on": row["restart"] if turn == "blocked" else None,
                 "opened_by": data.get("opened-by"),
                 "waits_on": waits.get(thread),
                 "opened_for": [{"wish": ident, **({"line": told[ident].line, "state": told[ident].state}
                                                   if ident in told else {})}
                                for ident in opened_for(data)],
                 "plan": plan, "strands": strands,
                 "receipts": [{k: v for k, v in {
                     "repo": r.repo, "number": r.number, "url": r.url, "head": r.head,
                     "state": r.state(), "merge_commit": r.merge_commit, "load": r.load()}.items()
                     if v not in (None, "")} for r in shipped.get(thread, ())],
                 "done": done, "owed": row["owed"], "last_at": _iso(last) if last else None}
        doc_threads.append({k: v for k, v in entry.items() if v not in (None, "", [])})
        if now == "working" and thread in state.holder:
            strand = state.holder[thread][0]
            lease = leases.get(thread, {}).get(strand)
            present.append((last or 0, {
                "strand": strand, "thread": thread, "state": now,
                "now": (plan or {}).get("next"), "owed": row["owed"],
                "body": f"{lease.data['shell']}/{lease.data['core']}" if lease else None}))
    choices = {f"{s}/{c}" for s, cs in cores.items() for c in cs} | ({config.body} if config.body else set())

    def usable(choice):
        try:
            return bool(resolve(home, choice.partition("/")[0]))
        except ValueError:
            return False
    bodies = {"default": config.body,
              "choices": sorted(filter(usable, choices), key=lambda c: (c != config.body, c))}
    doc = {"v": 1, "at": _iso(time.time()),
           "presence": {k: v for k, v in max(present, key=lambda p: p[0])[1].items() if v is not None}
           if present else None,
           "fuel": _fuel(home, {s: sorted(c) for s, c in cores.items()}, config.hold_floor_pct),
           "bodies": {k: v for k, v in bodies.items() if v},
           "threads": doc_threads,
           "log": _log_rows(state.accepted, writers, done_refs,
                            {w.id: w.thread for w in told.values()}),
           "wishes": _wish_rows(told, facts)}
    return {k: v for k, v in doc.items() if v not in (None, [], {})}


def digest(doc: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in doc.items() if k != "at"},
                                     sort_keys=True).encode()).hexdigest()


class Publisher:
    """Assemble every few seconds; PUT on a settled change, else once a minute.

    The PUT runs on its own thread, one at a time, so the loop never waits on
    the network. A failure is logged once, until a push succeeds again.
    """

    def __init__(self, home: Home, log):
        self.home, self.log = home, log
        self.doc = self.sum = self.sent = None
        self.assembled = self.changed = self.attempted = None
        self.worker: threading.Thread | None = None
        self.failing = False

    def step(self, config, router) -> None:
        if not config.relay_state:
            return
        now = clock()
        if self.assembled is None or now - self.assembled >= ASSEMBLE_EVERY:
            self.assembled = now
            facts = router.reader.read(self.home)
            with projection_cache(router.projections):
                doc = assemble(self.home, facts, config)
            if digest(doc) != self.sum:
                self.doc, self.sum, self.changed = doc, digest(doc), now
        if self.doc is None or (self.worker and self.worker.is_alive()):
            return
        settled = self.sum != self.sent and now - self.changed >= DEBOUNCE
        if self.attempted is None or settled or now - self.attempted >= IDLE:
            self.attempted, self.sent = now, self.sum
            doc = dict(self.doc, at=_iso(time.time()))
            self.worker = threading.Thread(target=self._put, args=(Path(config.relay_state), doc),
                                           name="loom-snapshot", daemon=True)
            self.worker.start()

    def _put(self, state_dir: Path, doc: dict) -> None:
        from brr.gates import cloud
        try:
            state = cloud._load_state_from_dir(state_dir)
            if not state.get("brnrd_url") or not state.get("token"):
                raise RuntimeError("cloud state has no URL or token")
            cloud._request(state["brnrd_url"], "PUT", "/v1/daemons/loom", token=state["token"],
                           json=doc, retry=False)
            self.failing = False
        except Exception as exc:
            if not self.failing:
                self.log(self.home, f"snapshot: push failed: {exc}")
            self.failing = True
