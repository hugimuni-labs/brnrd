"""A park names what it waits on; the loom watches it and wakes the thread.

A park is a ``wish.read`` whose reading starts ``waiting:``. Its ``wait``
field is ``{"on": <condition>, "until": <instant>}``, ``on`` absent for a
prose park. Thread-owned parks end on the owner's next read or a closing
result. Older parks retain the wish-wide cancellation rule. Every tick
``observe_waits`` checks the live waits against facts already held and the
forge states the ``Watcher`` polled off the loop; when one comes true, or its
``until`` passes, the loom writes one notice to the parking thread, and that
letter wakes it. The notice's id derives from the read, so it is written once
on any install, and writing it is the resolution: nothing else is stored.

Conditions, one row each in ``KINDS``, resolve on any terminal outcome:

- ``pr:<owner>/<repo>#<n>``: merged or closed.
- ``ci:<owner>/<repo>@<sha>``: every check run concluded, green or red.
- ``wish:<w-id>``: delivered, accepted, dropped or answered since the park, or
  a person's letter cites it. A body's own &ref is progress, never an outcome.
- ``letter:<id>``: a letter replies to it.
- ``at:<instant>``: the clock passes it.
- ``cond:<name>[,<name>…]`` or ``cond:all``: stands; ``outcome`` never resolves it.
  Each firing of a named condition (``conditions``) is its own notice to the
  parker's thread, so one park keeps receiving them until it is re-read.
- ``file:<path>``: the path holds something. A body that starts a long command
  itself runs ``(cmd; echo $? > <path>) &``, parks on the path and stops; the
  jack holds it and the notice says what the file says. The shell creates the
  file before it writes it, so an empty file is still pending. The loom reads the path on
  its own machine, so it is absolute, and it is the room's machine.

A park that waits on someone (``TURNED``, or prose) also carries a ``turn``
(config ``turn``): each time one passes while it still waits, the loom judges
that slot once, and writes one ``turn`` notice to the thread only if the
week's fuel is ahead of pace and the thread has acts it could move
(``idle.candidates``). The park stays. A slot that fails is skipped, never
made up: a long wait on a person wakes the body to work its own queue, at a
pace the fuel can carry, without the body arming a timer (continuous).
"""

from __future__ import annotations

import heapq
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import PurePosixPath

from .timers import parse_instant, stored_when

#: A typed wait re-asks its thread after this long; ``--until`` overrides it.
TYPED = timedelta(days=7)
#: A prose park's re-checks back off, counted from the latest prose re-park.
PROSE = (timedelta(hours=24), timedelta(days=3), timedelta(days=7))
#: Seconds between forge polls of the open ``pr``/``ci`` waits.
EVERY = 60
#: Seconds between polls of every open pull request a thread claimed (``loom/prs.json``).
PRS_EVERY = 300
#: Wish states a ``wish:`` wait resolves on.
SETTLED = {"delivered", "accepted", "dropped", "expired", "answered"}

_PR = re.compile(r"^pr:(?:([\w.-]+/[\w.-]+))?#(\d+)$")
_CI = re.compile(r"^ci:(?:([\w.-]+/[\w.-]+))?@([0-9a-f]{7,40})$")
_WISH = re.compile(r"^wish:(w-[a-z0-9]+)$")
_LETTER = re.compile(r"^letter:(\S+)$")
#: Conditions that wait on someone, so a park on them carries a turn; a prose park does too.
TURNED = ("letter:", "wish:", "pr:")
#: ``cond:`` names its conditions, ``conditions.NAMES``; its parse error lists them.
GRAMMAR = "pr:[owner/repo]#N, ci:[owner/repo]@sha, wish:<w-id>, letter:<id>, at:<when>, file:<path>, cond:<names>|all"


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _instant(text: str) -> datetime | None:
    """A stored instant: the ``…Z`` form, or a fact's ISO ``at``."""
    found = parse_instant(text)
    if found is not None:
        return found
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def when(text: str, now: datetime, flag: str) -> str:
    try:
        return stored_when(text, now)
    except ValueError:
        raise ValueError(f"{flag}: expected YYYY-MM-DDTHH:MM:SSZ, +<n>m, +<n>h or +<n>d, not {text!r}") from None


def canonical(text: str, now: datetime, repo=None) -> str:
    """The condition as stored: repo filled in, a relative ``at`` made absolute.

    ``repo(number)`` names the repo of a bare ``pr:#N``/``ci:@sha`` or None.
    """
    text = text.strip()
    for pattern, kind in ((_PR, "pr"), (_CI, "ci")):
        match = pattern.fullmatch(text)
        if match:
            owner = match.group(1) or (repo(match.group(2) if kind == "pr" else None) if repo else None)
            if not owner or owner.count("/") != 1:
                raise ValueError(f"--wait {text}: name the repo, {kind}:<owner>/<repo>"
                                 + ("#" if kind == "pr" else "@") + match.group(2))
            return f"{kind}:{owner}" + ("#" if kind == "pr" else "@") + match.group(2)
    if _WISH.fullmatch(text) or _LETTER.fullmatch(text):
        return text
    if text.startswith("at:"):
        return "at:" + when(text[3:], now, "--wait at:")
    if text.startswith("cond:"):
        from .conditions import parse
        return "cond:" + ",".join(parse(text[5:]))
    if text.startswith("file:") and text[5:].strip():
        # Relative to the parker's cwd, which the loom does not share.
        return "file:" + os.path.abspath(os.path.expanduser(text[5:].strip()))
    raise ValueError(f"--wait {text}: expected one of {GRAMMAR}")


def answer_shape(text: str | None, now: datetime) -> str:
    """Keep the answer's vocabulary while normalizing refs with the wait parser."""
    if text is None:
        return "reply"
    if text in ("reply", "press"):
        return text
    kind, _, ref = text.partition(":")
    if kind == "merge" and _PR.fullmatch("pr:" + ref) and "/" in ref:
        return "merge:" + canonical("pr:" + ref, now)[3:]
    if kind == "date":
        return "date:" + when(ref, now, "--answered-by date:")
    if kind == "file" and ref.strip() and "\x00" not in ref:
        path = PurePosixPath(ref.strip())
        if path.parts and not path.is_absolute() and ".." not in path.parts:
            return f"file:{path}"
    raise ValueError("--answered-by: expected reply, press, merge:<owner/repo>#N, file:<self path>, or date:<when>")


def answer_waits(found):
    """Open non-reply asks enter the same observer as parked wishes."""
    for ask in found.values():
        if not ask.open or ask.sender != "resident" or ask.answered_by == "reply":
            continue
        kind, sep, ref = ask.answered_by.partition(":")
        on = {"merge": "pr", "date": "at"}.get(kind, kind) + sep + ref
        yield ask, {"at": ask.born, "wait": {"on": on}, "closes_ask": True}


def board_press(ident: str, data: dict) -> bool:
    """Board verdicts arrive from either the loopback board or the relay's loom channel."""
    native = ident.split(":", 2)[-1]
    return (data.get("verdict") in ("agree", "correct")
            and str(data.get("from", "")).startswith("person:")
            and (native.startswith("loom:") or re.search(r":board-[a-z0-9]+$", native) is not None))


@lru_cache(maxsize=256)
def _main_has_file(repo, tip, path) -> bool:
    from .selfrepo import git
    result = git(repo, "cat-file", "-t", f"{tip}:{path}", check=False)
    return result.returncode == 0 and result.stdout.strip() == "blob"


def park(wish, condition: str | None, until: str | None, now: datetime, *, found=None,
         default: str | None = None, turn: float = 0, owner=None) -> dict:
    """The ``wait`` a new park stores; a prose park backs off over its re-parks.
    A park on someone stores ``turn``, the seconds between its turns, when it is not 0."""
    wait: dict = {}
    if condition is not None:
        wait["on"] = condition
        if condition.startswith("wish:") and found is not None and condition[5:] in found:
            wait["was"] = found[condition[5:]].state
        span = TYPED
    else:
        prose = 0
        for read in reversed(wish.reads):
            if owner is not None and read.get("thread") != owner:
                continue
            if not read["reading"].startswith("waiting:") or (read.get("wait") or {}).get("on"):
                break
            prose += 1
        span = PROSE[min(prose, len(PROSE) - 1)]
        wait["step"] = min(prose, len(PROSE) - 1) + 1
    if until is not None:
        wait["until"] = when(until, now, "--until")
    elif not str(condition or "").startswith("cond:"):
        # A cond: park stands: it is a subscription, not a question with a deadline.
        wait["until"] = _iso(now + span)
    if default is not None:
        wait["default"] = default
    if turn > 0 and (condition is None or condition.startswith(TURNED)):
        wait["turn"] = int(turn)
    return wait


def park_thread(wish, read, facts=None) -> str:
    thread = read.get("thread") or (read.get("wait") or {}).get("thread")
    if not thread and facts is not None and read.get("writer", "").startswith("strand:"):
        from .project import sender_threads
        thread = sender_threads(facts).get(read["writer"].removeprefix("strand:"))
    return thread or wish.thread


def live(found: dict) -> list[tuple]:
    """One active park per thread, with the original rule for unstamped reads."""
    rows = []
    for wish in found.values():
        if not wish.open or not wish.reads:
            continue
        delivered = max((r["at"] for r in wish.results if r["delivered"]), default="")
        latest = {read["thread"]: read for read in wish.reads if read.get("thread")}
        rows.extend((wish, read) for read in latest.values()
                    if read["at"] >= delivered and read.get("wait") and read["reading"].startswith("waiting:"))

        # Do not revive a legacy park cancelled by any later read or result.
        read = wish.reads[-1]
        if (not read.get("thread") and read.get("wait") and read["reading"].startswith("waiting:")
                and not any(result["at"] > read["at"] for result in wish.results)):
            rows.append((wish, read))
    return rows

def wish_waiters(home, facts, found: dict, target: str, now: datetime) -> list[tuple]:
    """Open, unresolved parks on a wish, with the thread each would wake."""
    from .home import notice_id
    from .ledger import kind_facts
    rows = [(wish, read) for wish, read in live(found)
            if read["wait"].get("on") == f"wish:{target}"]
    if not rows:
        return []
    notices = {fact.id for fact in kind_facts(facts, "letter")}
    return [(wish, read, parker(home, facts, wish, read)) for wish, read in rows
            if notice_id("wait", subject(wish, read)) not in notices
            and outcome(wish, read, facts, found, {}, now) is None]


def pr_repo(data: dict) -> str | None:
    """``owner/repo`` of a ``pr.opened`` fact, read off its url; its ``repo`` is the library name."""
    match = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/\d+", str(data.get("url") or ""))
    return match.group(1) if match else None


def open_claims(receipts: dict) -> set[str]:
    """``owner/repo#N`` of each claimed pull request not yet merged, for the cards.

    A receipt's ``repo`` is the library name; the forge needs the owner its url names.
    """
    return {f"{pr_repo({'url': r.url})}#{r.number}" for rows in receipts.values() for r in rows
            if not r.merge_commit and pr_repo({"url": r.url})}


def want(read) -> str:
    """The key a park's forge state is polled and kept under.

    A ``ci:`` key carries the park's time, ``ci:<repo>@<sha> since <at>``: the
    verdict counts only the attempt running since (``GitHubChecks.judge``), so two
    parks on one sha may read differently."""
    on = str(read["wait"].get("on", ""))
    return f"{on} since {read['at']}" if on.startswith("ci:") else on


def unresolved(found, facts):
    """Live waits that have yet to produce their terminal notice."""
    from .home import notice_id
    from .ledger import LedgerView, kind_facts
    ids = facts.by_id if isinstance(facts, LedgerView) else {f.id for f in kind_facts(facts, "letter")}
    return [(wish, read) for wish, read in live(found)
            if notice_id("wait", subject(wish, read)) not in ids]


def thread_lines(home, facts, thread):
    """The waits the current context inherits on this thread."""
    from .wishes import told
    lines = []
    for wish, read in unresolved(told(facts), facts):
        if parker(home, facts, wish, read) != thread:
            continue
        on = read["wait"].get("on") or read["reading"].removeprefix("waiting:").strip()
        lines.append(f"waiting: {wish.id} on {on} since {read['at']}")
    return lines


def forge_wants(found: dict, facts=None) -> set[str]:
    rows = live(found) if facts is None else unresolved(found, facts)
    return {want(read) for _wish, read in [*rows, *answer_waits(found)]
            if str(read["wait"].get("on", "")).startswith(("pr:", "ci:"))}


def _answers(facts, target: str, since: str, replied: str | None = None,
             citer=lambda letter: True) -> str | None:
    """A letter that replies to ``target`` at or after ``replied`` (default ``since``), or cites it after ``since``.

    Only the reply index reaches back past ``since``: the citation scan walks
    the letters newest first and stops there, so a tick never walks the ledger.
    """
    from .ledger import LedgerView, kind_facts
    replied = since if replied is None else replied
    if isinstance(facts, LedgerView):
        for ident in facts.letters_by_reply.get(target, ()):
            letter = facts.by_id.get(ident)
            if letter is not None and letter.at >= replied:
                return str(letter.data.get("id") or letter.id)
    for letter in reversed(kind_facts(facts, "letter")):
        if letter.at < since:
            break
        if letter.data.get("re") == target or (target in (letter.data.get("links") or ()) and citer(letter)):
            return str(letter.data.get("id") or letter.id)
    return None


def outcome(wish, read, facts, found: dict, forge: dict, now: datetime, *, self_repo=None) -> str | None:
    """What came true for this park, in words, or None while it still waits."""
    wait = read["wait"]
    on = wait.get("on")
    closing = read.get("closes_ask", False)
    if on is None:
        pass
    elif on.startswith("pr:"):
        number = int(on.rpartition("#")[2])
        repo = on[3:].partition("#")[0]
        from .ledger import kind_facts
        names = {f.data.get("repo") for f in kind_facts(facts, "pr.opened")
                 if int(f.data.get("number") or 0) == number and pr_repo(f.data) == repo}
        for merged in kind_facts(facts, "pr.merged"):
            if merged.data.get("repo") in names and int(merged.data.get("number") or 0) == number:
                return f"{on} merged ({merged.data.get('merge_commit', '')[:8]})"
        state = forge.get(on)
        if state and (state.get("state") == "merged" or (not closing and state.get("state") == "closed")):
            return f"{on} {state['state']} at {state.get('at')}: {state.get('url')}"
        if not closing and state and state.get("state") == "unreadable":
            return f"{on} cannot be read: {state.get('error')}"
    elif on.startswith("ci:"):
        state = forge.get(want(read))
        if state and state.get("state") in ("green", "red"):
            return f"{on} is {state['state']}"
    elif on.startswith("wish:"):
        target = found.get(on[5:])
        if target is not None and target.state in SETTLED and target.state != wait.get("was"):
            return f"{target.id} is {target.state}"
        if target is None or target.answered_by == "reply":
            from .wishes import _person
            answer = _answers(facts, on[5:], read["at"], citer=_person)
            if answer is not None:
                return f"{on[5:]} was answered by {answer}"
    elif on.startswith("letter:"):
        answer = _answers(facts, on[7:], read["at"], replied="")
        if answer is not None:
            return f"{on[7:]} was answered by {answer}"
    elif on.startswith("at:"):
        due = parse_instant(on[3:])
        if due is not None and due <= now:
            return f"{on[3:]} has passed"
    elif on == "press":
        from .ledger import kind_facts
        for letter in reversed(kind_facts(facts, "letter")):
            if letter.at < wish.born:
                break
            if letter.data.get("wish") == wish.id and board_press(letter.id, letter.data):
                return f"board press {letter.data['verdict']} ({letter.id})"
    elif on.startswith("file:"):
        if closing:
            from .lifecycle import main_tip
            tip = main_tip(self_repo)
            path = on[5:]
            return f"{on} exists on self main ({tip})" if tip and _main_has_file(self_repo, tip, path) else None
        try:
            with open(on[5:], "rb") as handle:
                head = handle.read(200).decode("utf-8", "replace").strip()
        except FileNotFoundError:
            head = None
        except OSError as exc:
            head = f"unreadable: {exc.strerror}"
        if head:
            return f"{on[5:]} exists ({head.splitlines()[0]})"
    until = parse_instant(str(wait.get("until", "")))
    if until is not None and until <= now:
        return f"nothing resolved by {wait['until']}"
    return None


def _advice(wish, read, resolved: bool, said: str = "") -> str:
    wait = read["wait"]
    if not resolved and wait.get("default") is not None:
        return (f"nobody answered by {wait['until']}. Your rec was: {wait['default']}. If it is inside "
                "your grant, carry it out now and say so on the thread; a merge, money beyond your "
                "budget or an outward act is never defaulted: re-ask")
    if wait.get("on") is None:
        nxt = PROSE[min(int(wait.get("step") or 1), len(PROSE) - 1)]
        return (f"re-check {wait.get('step', 1)} of a prose park. If the loom can watch the "
                f"condition, re-park with wish {wish.id} --read \"waiting: …\" --wait <{GRAMMAR}>; "
                f"if their word moves the work, re-park in prose (next re-check in {_span(nxt)}), "
                "or deliver, ask or settle")
    if resolved and str(wait.get("on", "")).startswith("pr:") and "cannot be read" in said:
        return f"the forge has no such pull request: re-park {wish.id} with the repo and number corrected"
    on = str(wait.get("on") or "")
    if resolved and on.startswith("wish:") and " was answered by " in said:
        return (f"record how it was answered: wish {on[5:]} --answer took|changed|never; "
                f"then go on with {wish.id}: deliver, re-park with a new --wait, or ask")
    if resolved:
        return f"go on with {wish.id}: deliver, re-park with a new --wait, or ask"
    return f"re-judge {wish.id}: re-park with a new --wait or --until, deliver, ask or drop it"


def _span(delta: timedelta) -> str:
    return f"{delta.days}d" if delta.days else f"{delta.seconds // 3600}h"


def subject(wish, read) -> str:
    """The same read on every install: its wish, instant and writer."""
    return f"{wish.id}|{read['at']}|{read.get('writer', '')}"


#: Turn slots this process has judged, by ``turn_subject``: each is judged once, pass or fail.
_JUDGED: set[str] = set()
#: When each thread last took a turn in this process: two parks on one thread share one pace.
_TURNED: dict[str, datetime] = {}


def turn_slot(read, now: datetime) -> int:
    """How many turns have passed on this park: 0 before the first, or with no turn."""
    turn = int(read["wait"].get("turn") or 0)
    began = _instant(str(read.get("at", "")))
    if turn <= 0 or began is None:
        return 0
    return max(int((now - began).total_seconds() // turn), 0)


def turn_subject(wish, read, slot: int) -> str:
    return f"{subject(wish, read)}|turn {slot}"


def _turn(home, facts, wish, read, now: datetime, pace, held) -> None:
    """One ``turn`` notice for this park's current slot, when the pace and the thread allow it.

    A thread with a live body takes none: held, its keepalive already carries the same acts."""
    slot = turn_slot(read, now)
    if slot < 1:
        return
    key = turn_subject(wish, read, slot)
    if key in _JUDGED:
        return
    _JUDGED.add(key)
    thread = parker(home, facts, wish, read)
    last = _TURNED.get(thread)
    if last is not None and (now - last).total_seconds() < int(read["wait"]["turn"]):
        return
    if thread in held():
        return
    said = pace(thread)
    if not said:
        return
    from . import idle
    from .loom import notice
    lines = idle.candidates(home, facts, thread, now=now.timestamp())
    if not lines:
        return
    from .wishes import _short
    began = _instant(str(read["at"]))
    reading = _short(read["reading"].removeprefix("waiting:").strip(), 60)
    nxt = _iso(began + timedelta(seconds=int(read["wait"]["turn"]) * (slot + 1)))
    head = (f"turn: {wish.id} parked {idle._age((now - began).total_seconds())} on \"{reading}\" and still waits; "
            f"{said}. This thread could move:\n" + "".join(f"  - {line}\n" for line in lines))
    if notice(home, facts, "turn", key, head.rstrip("\n"),
              f"the park stays; its next turn is {nxt}, if the week is still ahead then",
              thread=thread):
        _TURNED[thread] = now


class _Pending:
    """Indexes for one wish projection; the clock visits only due parks.

    Rebuilt when wish/letter inputs change, discarded with a repaired ledger.
    Files are external inputs and must still be checked each tick. Forge
    values are copied: the watcher may replace or mutate its state mapping.
    """

    def __init__(self, found, facts, now):
        self.found = found
        self.rows = {}
        self.files = set()
        self.forge = {}
        self.states = {}
        self.prs = set()
        self.pr_revision = None
        self.due = []
        self.first = True
        self.retry = set()
        for wish, read in [*unresolved(found, facts), *answer_waits(found)]:
            key = wish.id if read.get("closes_ask") else subject(wish, read)
            self.rows[key] = (wish, read)
            on = str(read["wait"].get("on") or "")
            if on.startswith("file:"):
                self.files.add(key)
            if on.startswith(("pr:", "ci:")):
                self.forge.setdefault(want(read), set()).add(key)
            if on.startswith("pr:"):
                self.prs.add(key)
            times = [parse_instant(str(read["wait"].get("until", "")))]
            if on.startswith("at:"):
                times.append(parse_instant(on[3:]))
            for due in times:
                if due is not None:
                    heapq.heappush(self.due, (due, key))
            self._next_turn(key, read, now)
        self.order = {key: index for index, key in enumerate(self.rows)}

    def _next_turn(self, key, read, now):
        turn = int(read["wait"].get("turn") or 0)
        began = _instant(read["at"])
        if turn > 0 and began is not None and now is not None:
            due = began + timedelta(seconds=turn * (turn_slot(read, now) + 1))
            heapq.heappush(self.due, (due, key))

    def candidates(self, facts, forge, now):
        selected = set(self.rows) if self.first else set(self.files)
        selected.update(self.retry)
        self.first = False
        revision = tuple(len(facts.by_kind[k]) for k in ("pr.opened", "pr.merged"))
        if revision != self.pr_revision:
            selected.update(self.prs)
            self.pr_revision = revision
        for condition, keys in self.forge.items():
            state = dict(forge.get(condition) or {})
            if self.states.get(condition) != state:
                selected.update(keys)
                self.states[condition] = state
        timed = set()
        while self.due and self.due[0][0] <= now:
            _, key = heapq.heappop(self.due)
            timed.add(key)
        for key in timed:
            self._next_turn(key, self.rows[key][1], now)
        selected.update(timed)
        self.retry = selected
        return [self.rows[key] for key in sorted(selected, key=self.order.__getitem__)]


def observe_waits(home, facts, armed: bool, forge: dict | None = None, now: datetime | None = None,
                  pace=None):
    """Write a notice for each live wait that came true or ran out; only the lease holder writes.

    ``pace(thread)`` says the week's pace when that thread's fuel is ahead of it, else ``""``;
    without it no park takes its turn."""
    if not armed:
        return facts
    from .home import notice_id
    from .loom import notice
    from .wishes import told
    from .ledger import LedgerView
    found = told(facts)
    now = now or datetime.now(timezone.utc)
    if isinstance(facts, LedgerView):
        pending = getattr(facts, "_pending_waits", None)
        if pending is None or pending.found is not found:
            pending = facts._pending_waits = _Pending(found, facts, now)
        rows = pending.candidates(facts, forge or {}, now)
    else:
        pending = None
        rows = [*unresolved(found, facts), *answer_waits(found)]
    if not rows:
        return facts
    from functools import cache

    @cache
    def held() -> frozenset:
        from .project import fold
        return frozenset(fold(facts).holder)
    for wish, read in rows:
        if read.get("closes_ask"):
            said = outcome(wish, read, facts, found, forge or {}, now, self_repo=home.root / "self")
            if said is not None:
                notice(home, facts, "ask-closed", wish.id, f"closed &{wish.id}: {said}",
                       "The ask is answered.", thread=wish.thread, closes_ask=wish.id)
            continue
        said = outcome(wish, read, facts, found, forge or {}, now)
        if said is None:
            if pace is not None and read["wait"].get("turn"):
                try:
                    _turn(home, facts, wish, read, now, pace, held)
                except Exception as exc:  # noqa: BLE001 - a turn is an offer; it never costs the waits
                    from .loom import _log
                    _log(home, f"turn: skipped {wish.id} ({type(exc).__name__}: {exc})")
            continue
        resolved = not said.startswith("nothing resolved by")
        head = (f"wait on {wish.id} {'resolved' if resolved else 'ran out'}: {said}. "
                f"Parked as: {read['reading']}")
        wrote = notice(home, facts, "wait", subject(wish, read), head, _advice(wish, read, resolved, said),
                       thread=parker(home, facts, wish, read))
        on = str(read["wait"].get("on") or "")
        if wrote and not resolved and read["wait"].get("default") is not None and on.startswith("wish:"):
            # The default date passed unanswered and the body is told to do its rec: the ask lapsed.
            from .ledger import append
            from .trust import answer_fact
            append(home, answer_fact(f"loom:{home.install_id()}", on[5:], "lapsed", notice_id("wait", subject(wish, read))))
    if pending is not None:
        pending.retry.clear()
    return facts


def parker(home, facts, wish, read) -> str:
    """The recorded destination, with the wish's thread as a routing fallback."""
    thread = park_thread(wish, read, facts)
    return thread if home.routable(thread) else wish.thread


class Forge:
    """A pull request's state and a commit's checks, one GET each."""

    def __init__(self, checks):
        self.checks = checks

    def pull(self, repo: str, number: int) -> dict:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/pulls/{number}",
            headers={"Authorization": f"Bearer {self.checks._token()}",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.load(resp)
        if body.get("merged_at"):
            return {"state": "merged", "at": body["merged_at"], "url": body.get("html_url")}
        if body.get("state") == "closed":
            return {"state": "closed", "at": body.get("closed_at"), "url": body.get("html_url")}
        return {"state": "open", "url": body.get("html_url")}

    def ci(self, repo: str, sha: str, since: str | None = None) -> dict:
        return {"state": self.checks.verdict(f"https://github.com/{repo}.git", sha, since)}

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{path}",
            headers={"Authorization": f"Bearer {self.checks._token()}",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    def card(self, repo: str, number: int) -> dict:
        """What a thread line says of an open pull request: draft, CI on its head, when the head was pushed.

        The head commit's committer date stands for the push: GitHub keeps no
        push time on a pull request, and ``updated_at`` moves on every comment.
        """
        body = self._get(f"{repo}/pulls/{number}")
        if body.get("merged_at"):
            return {"state": "merged"}
        if body.get("state") == "closed":
            return {"state": "closed"}
        head = str((body.get("head") or {}).get("sha") or "")
        commit = self._get(f"{repo}/commits/{head}") if head else {}
        return {"state": "open", "draft": bool(body.get("draft")), "head": head,
                "ci": self.checks.verdict(f"https://github.com/{repo}.git", head) if head else "pending",
                "pushed": ((commit.get("commit") or {}).get("committer") or {}).get("date")}


def forge_for(config) -> Forge:
    from .update import forge_for as checks_for
    return Forge(checks_for(config))


class Watcher:
    """Polls the forge for the open ``pr``/``ci`` waits off the loop, the way ``Updater`` does.

    The tick hands it what it wants; ``states`` is what the last poll read.
    It also reads every open pull request in ``prs`` (``<repo>#<n>``) each
    ``PRS_EVERY`` seconds and writes the cards to ``path``, which the thread
    lines read (wake-vision). A failure is kept per condition: the others'
    states still land, and a failed one keeps its last state. A pull request
    the forge answers 404 for is ``unreadable``, which resolves its wait with
    that word. Only a poll in which every read failed raises from ``step``,
    so the loop's guard records an outage, not one bad condition.
    """

    def __init__(self, path=None, comments=None):
        from .comments import Cursor
        self.states: dict[str, dict] = {}
        self.wants: set[str] = set()
        self.prs: set[str] = set()
        self.path = path
        self.checked: float | None = None
        self.prs_checked: float | None = None
        #: The last cards read; a closed or merged one is never read again.
        self.cards: dict[str, dict] = {}
        self.worker: threading.Thread | None = None
        self.failing: tuple[Exception, object] | None = None
        #: The last poll's failure per condition, in words.
        self.errors: dict[str, str] = {}
        #: ``owner/repo#N`` → claiming thread, merged or open: whose comments are read (``comments``).
        self.owners: dict[str, str] = {}
        self.cursor = Cursor(comments)
        self.reader = None
        #: Comments read and not yet told; the tick takes them under the lock.
        self.found: list[dict] = []
        self.lock = threading.Lock()

    def take(self) -> list[dict]:
        with self.lock:
            found, self.found = self.found, []
        return found

    def step(self, config, armed: bool) -> None:
        if self.path is not None and not self.cards:
            try:
                self.cards = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self.cards = {}
        open_prs = {key for key in self.prs if self.cards.get(key, {}).get("state") not in ("merged", "closed")}
        cards = self.path is not None and bool(open_prs)
        if not armed or not (self.wants or cards or self.owners):
            self.failing = None
            return
        if not (self.worker and self.worker.is_alive()):
            now = time.monotonic()
            wants = set(self.wants) if self.checked is None or now - self.checked >= EVERY else set()
            due = self.prs_checked is None or now - self.prs_checked >= PRS_EVERY
            prs = open_prs if cards and due else set()
            owners = dict(self.owners) if due else {}
            if wants:
                self.checked = now
            if prs or owners:
                self.prs_checked = now
            if wants or prs or owners:
                self.worker = threading.Thread(target=self._poll, args=(forge_for(config), wants, prs),
                                               kwargs={"owners": owners, "open_prs": open_prs},
                                               name="loom-waits", daemon=True)
                self.worker.start()
        if self.failing is not None:
            raise self.failing[0].with_traceback(self.failing[1])

    def _poll(self, forge, wants: set[str], prs: set[str] = frozenset(), *,
              owners: dict[str, str] | None = None, open_prs: set[str] = frozenset()) -> None:
        read, missed = 0, None
        if owners:
            from .comments import Reader, poll
            if self.reader is None:
                self.reader = Reader(forge.checks._token)
            self.reader.token = forge.checks._token
            found, missed, read = poll(self.reader, self.cursor, owners, set(open_prs))
            with self.lock:
                self.found.extend(found)
        if prs:
            from .home import atomic_write
            # One unreadable pull request keeps its last card; the rest still land.
            cards = dict(self.cards)
            for key in sorted(prs):
                repo, _, number = key.partition("#")
                try:
                    cards[key] = forge.card(repo, int(number))
                    read += 1
                except Exception as exc:
                    missed = missed or exc
            try:
                atomic_write(self.path, json.dumps(cards, indent=1, sort_keys=True) + "\n")
                self.cards = cards
            except Exception as exc:
                missed, read = exc, 0
        states, errors = dict(self.states), {}
        for on in sorted(wants):
            try:
                if on.startswith("pr:"):
                    repo, _, number = on[3:].partition("#")
                    states[on] = forge.pull(repo, int(number))
                else:
                    target, _, since = on[3:].partition(" since ")
                    repo, _, sha = target.partition("@")
                    states[on] = forge.ci(repo, sha, since or None)
                read += 1
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and on.startswith("pr:"):
                    states[on] = {"state": "unreadable", "error": f"HTTP {exc.code}"}
                    read += 1
                else:
                    errors[on], missed = str(exc), missed or exc
            except Exception as exc:
                errors[on], missed = str(exc), missed or exc
        self.states = {on: state for on, state in states.items() if on in wants} if wants else self.states
        self.errors = errors
        self.failing = (missed, missed.__traceback__) if missed is not None and not read else None


def rest_parks(parks):
    """Name the nearest known deadlines first, then undated parks in ledger order."""
    def deadline(row):
        wait = row[1]["wait"]
        on = wait.get("on", "")
        times = [_instant(value) for value in (on[3:] if on.startswith("at:") else "", wait.get("until"))]
        return min((at for at in times if at is not None), default=datetime.max.replace(tzinfo=timezone.utc))

    def label(row):
        wish, read = row
        kind, _, target = read["wait"].get("on", "").partition(":")
        if kind == "at" and (at := _instant(target)) is not None:
            return f"{at:%Y-%m-%d %H:%MZ}"
        if kind == "pr":
            return f"pull request {target}"
        if kind == "ci":
            repo, _, sha = target.partition("@")
            return f"checks for {repo} {sha[:8]}"
        if kind == "file":
            return f"the result in {os.path.basename(target)}"
        if kind == "letter":
            return f"a reply to {target}"
        if kind == "wish":
            return f"an answer on {target}"
        if kind == "cond":
            return "a change in " + target.replace(",", ", ").replace("-", " ").replace("_", " ")
        reading = read["reading"].removeprefix("waiting:").strip().partition("\n")[0]
        return reading[:100] or wish.line[:100]

    shown = [label(row) for row in sorted(parks, key=deadline)[:2]]
    if len(parks) > len(shown):
        more = len(parks) - len(shown)
        shown[-1] += f" ({more} more {'park' if more == 1 else 'parks'})"
    return shown
