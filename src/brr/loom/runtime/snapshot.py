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

from .adapters import body_label
from .home import Home, thread_of
from .project import cached_projection, fold, is_notice, projection_cache, sender_threads
from .wishes import progress

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
            letter = letters.get(say.letter)
            if letter is None:
                continue
            text = say.text(str(letter.data.get("body") or ""))
            says.append(_bare({"id": say.letter, "from": letter.data.get("from"),
                               "at": _iso(letter.at), "head": text[:HEAD]}))
        answer = answers.get(w.id)
        rows.append(_bare({
            "id": w.id, "thread": w.thread, "from": w.sender, "line": w.line, "state": w.state,
            "origin": w.origin, "at": _iso(w.at), "says": says, "reading": w.reading,
            "results": [_bare({"kind": r.get("kind"), "ref": r.get("ref"), "line": r.get("line"),
                               "delivered": bool(r.get("delivered")), "at": _iso(r["at"])})
                        for r in w.results],
            "left": _bare({"steps": w.left["steps"], "gen": w.left["gen"], "by": w.left["by"],
                           "at": _iso(w.left["at"])}) if w.left else None,
            # Steers after the latest left: what the left has not taken in yet.
            "steered": [i for x in w.stamps if x["letters"] and x["at"] > (w.left["at"] if w.left else w.born)
                        for i in x["letters"]],
            "verdict": _bare({"as": w.verdict["as"], "evidence": w.verdict.get("evidence"),
                              "at": _iso(w.verdict["at"])}) if w.verdict else None,
            "answer": {"verdict": answer.data["verdict"], "at": _iso(answer.at),
                       "head": str(answer.data.get("body") or "")[:HEAD]}
            if answer is not None and _when(answer.at) > _when(w.at) else None}))
    return rows


class View:
    """The projections every section reads, each read once per assemble."""

    def __init__(self, home: Home, facts: list, config):
        from .lifecycle import main_snapshot
        from .receipts import receipts
        from .selfrepo import forge_threads
        from .threads import _table_inputs, thread_table
        from .wishes import waits_on, wishes
        self.home, self.facts, self.config = home, facts, config
        self.table = [row for row in thread_table(home, facts) if row["thread"] != "operator"]
        self.main = main_snapshot(home)
        self.activity = cached_projection(facts, "thread-table", repr(self.main),
                                          lambda rows: _table_inputs(rows, self.main))[4]
        self.state = fold(facts)
        self.writers = sender_threads(facts)
        self.texts = _readmes(home)
        self.forge = forge_threads(home.root / "self")
        self.held = {strand: thread for thread, (strand, _gen) in self.state.holder.items()}
        self.ends = {f.data["strand"]: f.data["cause"]
                     for f in self.state.accepted if f.kind == "attempt.ended"}
        self.leases, cores = {}, {}
        for f in self.state.accepted:
            if f.kind == "lease" and f.data.get("shell"):
                self.leases.setdefault(f.data["thread"], {})[f.data["strand"]] = f
                if f.data.get("core"):
                    cores.setdefault(f.data["shell"], set()).add(f.data["core"])
        self.cores = {shell: sorted(c) for shell, c in cores.items()}
        self.told = wishes([f for f in facts if f.kind.startswith("wish") or f.kind == "letter"])
        self.shipped = receipts(facts, home.install_id())
        # An open ask is the person's turn unless it waits on a thread that has not asked onward.
        self.waits: dict[str, list[str]] = {}
        for ident, head in sorted(waits_on(self.told, self.main).items()):
            self.waits.setdefault(self.told[ident].thread, []).append(head)
        self.asked = {t for t, hs in self.waits.items() if any(not h.startswith("thread:") for h in hs)}


def _turn(v: View, row: dict) -> str:
    from .lifecycle import CLOSED
    thread, now, owed_by = row["thread"], _state(row["now"]), row["owed-by"]
    if v.main.get(thread, {}).get("status") in CLOSED:
        return "none"
    if "operator" in owed_by.split(", ") or thread in v.asked:
        return "yours"
    if now in {"parked", "failed"}:
        return "blocked"
    if row["owed"] or now == "working" or any(
            owed_by.startswith(w) for w in ("body:", "loom:", "opener:")):
        return "mine"
    return "none"


def thread(v: View, row: dict) -> dict:
    from .threads import opened_for
    thread = row["thread"]
    data = v.main.get(thread, {})
    now = _state(row["now"])
    turn = _turn(v, row)
    strands = []
    for strand, lease in v.leases.get(thread, {}).items():
        word = now if v.held.get(strand) == thread else _state(v.ends.get(strand, ""))
        strands.append({"id": strand, "body": body_label(lease.data["shell"], lease.data.get("core") or ""),
                        **({"state": word} if word else {}), "since": _iso(lease.at)})
    last = v.activity.get(thread)
    told = v.told
    entry = {"id": thread, "title": _SENTENCE.split(data.get("headline", ""), 1)[0],
             "status": data.get("status"), "turn": turn,
             "blocked_on": row["restart"] if turn == "blocked" else None,
             "opened_by": data.get("opened-by"),
             "waits_on": v.waits.get(thread),
             "opened_for": [{"wish": ident, **({"line": told[ident].line, "state": told[ident].state}
                                               if ident in told else {})}
                            for ident in opened_for(data)],
             "plan": progress(told, thread), "strands": strands,
             "receipts": [{k: val for k, val in {
                 "repo": r.repo, "number": r.number, "url": r.url, "head": r.head,
                 "state": r.state(), "merge_commit": r.merge_commit, "load": r.load()}.items()
                 if val not in (None, "")} for r in v.shipped.get(thread, ())],
             "done": _done(v.texts.get(thread, "")), "owed": row["owed"],
             "last_at": _iso(last) if last else None,
             "url": f"{v.forge}/{thread}" if v.forge else None}
    return {k: val for k, val in entry.items() if val not in (None, "", [])}


def presence(v: View) -> dict | None:
    """The working thread with the newest activity."""
    present = []
    for row in v.table:
        thread, now = row["thread"], _state(row["now"])
        if now == "working" and thread in v.state.holder:
            strand = v.state.holder[thread][0]
            lease = v.leases.get(thread, {}).get(strand)
            present.append((v.activity.get(thread) or 0, {
                "strand": strand, "thread": thread, "state": now,
                "now": (progress(v.told, thread) or {}).get("next"),
                "owed": row["owed"],
                "body": body_label(lease.data["shell"], lease.data.get("core") or "") if lease else None}))
    if not present:
        return None
    return {k: val for k, val in max(present, key=lambda p: p[0])[1].items() if val is not None}


def fuel(v: View) -> list[dict]:
    return _fuel(v.home, v.cores, v.config.hold_floor_pct)


def bodies(v: View) -> dict:
    from .adapters import resolve
    config = v.config
    choices = {body_label(s, c) for s, cs in v.cores.items() for c in cs} | ({config.body} if config.body else set())

    def usable(choice):
        try:
            return bool(resolve(v.home, choice.partition("/")[0]))
        except ValueError:
            return False
    found = {"default": config.body,
             "choices": sorted(filter(usable, choices), key=lambda c: (c != config.body, c))}
    return {k: val for k, val in found.items() if val}


def log(v: View) -> list[dict]:
    done_refs = {row["thread"]: {d["ref"] for d in _done(v.texts.get(row["thread"], "")) if "ref" in d}
                 for row in v.table}
    return _log_rows(v.state.accepted, v.writers, done_refs, {w.id: w.thread for w in v.told.values()})


def _raise(step, fn, *args):
    fn(*args)


def assemble(home: Home, facts: list, config, guard=_raise) -> dict:
    """The snapshot document, ``at`` included; pure reads of what is held.

    Each section runs through ``guard`` as ``publisher.<section>``: the loop's
    guard leaves a section that raises out of this document and keeps its streak.
    """
    v = View(home, facts, config)
    sections = {"presence": presence, "fuel": fuel, "bodies": bodies,
                "threads": lambda v: [thread(v, row) for row in v.table],
                "log": log, "wishes": lambda v: _wish_rows(v.told, v.facts)}
    doc = {"v": 1, "at": _iso(time.time())}
    for name, section in sections.items():
        guard(f"publisher.{name}", lambda name=name, section=section: doc.__setitem__(name, section(v)))
    return {k: val for k, val in doc.items() if val not in (None, [], {})}


def digest(doc: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in doc.items() if k != "at"},
                                     sort_keys=True).encode()).hexdigest()


class Publisher:
    """Assemble every few seconds; PUT on a settled change, else once a minute.

    The PUT runs on its own thread, one at a time, so the loop never waits on
    the network. Until a push succeeds again, every step raises the last
    push failure, so the loop's guard holds the streak. ``guard`` runs each
    section, so one that raises costs that section, not the push.
    """

    def __init__(self, home: Home, log, guard=_raise):
        self.home, self.log, self.guard = home, log, guard
        self.doc = self.sum = self.sent = None
        self.assembled = self.changed = self.attempted = None
        self.worker: threading.Thread | None = None
        self.failing: tuple[Exception, object] | None = None

    def step(self, config, router) -> None:
        from .gates import state_dir
        gate = state_dir(self.home.root, config)
        if gate is None:
            return
        now = clock()
        if self.assembled is None or now - self.assembled >= ASSEMBLE_EVERY:
            self.assembled = now
            facts = router.reader.read(self.home)
            with projection_cache(router.projections):
                doc = assemble(self.home, facts, config, self.guard)
            if digest(doc) != self.sum:
                self.doc, self.sum, self.changed = doc, digest(doc), now
        if self.doc is not None and not (self.worker and self.worker.is_alive()):
            self._push(gate, now)
        if self.failing is not None:
            raise self.failing[0].with_traceback(self.failing[1])

    def _push(self, gate: Path, now) -> None:
        settled = self.sum != self.sent and now - self.changed >= DEBOUNCE
        if self.attempted is None or settled or now - self.attempted >= IDLE:
            self.attempted, self.sent = now, self.sum
            doc = dict(self.doc, at=_iso(time.time()))
            self.worker = threading.Thread(target=self._put, args=(gate, doc),
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
            self.failing = None
        except Exception as exc:
            self.failing = (exc, exc.__traceback__)
