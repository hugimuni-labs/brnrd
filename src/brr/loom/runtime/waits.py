"""A park names what it waits on; the loom watches it and wakes the thread.

A park is a ``wish.read`` whose reading starts ``waiting:``. Its ``wait``
field is ``{"on": <condition>, "until": <instant>}``, ``on`` absent for a
prose park. The live wait is the latest read of an open wish with nothing
after it: a result, a verdict or another read ends it. Every tick
``observe_waits`` checks the live waits against facts already held and the
forge states the ``Watcher`` polled off the loop; when one comes true, or its
``until`` passes, the loom writes one notice to the wish's thread, and that
letter wakes it. The notice's id derives from the read, so it is written once
on any install, and writing it is the resolution: nothing else is stored.

Conditions, one row each in ``KINDS``, resolve on any terminal outcome:

- ``pr:<owner>/<repo>#<n>``: merged or closed.
- ``ci:<owner>/<repo>@<sha>``: every check run concluded, green or red.
- ``wish:<w-id>``: delivered, accepted or dropped since the park, or a letter
  answers or cites it (how a person answers an ask).
- ``letter:<id>``: a letter replies to it.
- ``at:<instant>``: the clock passes it.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

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
SETTLED = {"delivered", "accepted", "dropped"}

_PR = re.compile(r"^pr:(?:([\w.-]+/[\w.-]+))?#(\d+)$")
_CI = re.compile(r"^ci:(?:([\w.-]+/[\w.-]+))?@([0-9a-f]{7,40})$")
_WISH = re.compile(r"^wish:(w-[a-z0-9]+)$")
_LETTER = re.compile(r"^letter:(\S+)$")
GRAMMAR = "pr:[owner/repo]#N, ci:[owner/repo]@sha, wish:<w-id>, letter:<id>, at:<when>"


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
            if not owner:
                raise ValueError(f"--wait {text}: name the repo, {kind}:<owner>/<repo>"
                                 + ("#" if kind == "pr" else "@") + match.group(2))
            return f"{kind}:{owner}" + ("#" if kind == "pr" else "@") + match.group(2)
    if _WISH.fullmatch(text) or _LETTER.fullmatch(text):
        return text
    if text.startswith("at:"):
        return "at:" + when(text[3:], now, "--wait at:")
    raise ValueError(f"--wait {text}: expected one of {GRAMMAR}")


def park(wish, condition: str | None, until: str | None, now: datetime, *, found=None,
         default: str | None = None) -> dict:
    """The ``wait`` a new park stores; a prose park backs off over its re-parks."""
    wait: dict = {}
    if condition is not None:
        wait["on"] = condition
        if condition.startswith("wish:") and found is not None and condition[5:] in found:
            wait["was"] = found[condition[5:]].state
        span = TYPED
    else:
        prose = 0
        for read in reversed(wish.reads):
            if not read["reading"].startswith("waiting:") or (read.get("wait") or {}).get("on"):
                break
            prose += 1
        span = PROSE[min(prose, len(PROSE) - 1)]
        wait["step"] = min(prose, len(PROSE) - 1) + 1
    wait["until"] = when(until, now, "--until") if until is not None else _iso(now + span)
    if default is not None:
        wait["default"] = default
    return wait


def live(found: dict) -> list[tuple]:
    """``(wish, read)`` for each open wish whose latest read is a park with a wait, untouched since."""
    rows = []
    for wish in found.values():
        if not wish.open or not wish.reads:
            continue
        read = wish.reads[-1]
        if not read.get("wait") or not read["reading"].startswith("waiting:"):
            continue
        if any(result["at"] > read["at"] for result in wish.results):
            continue
        rows.append((wish, read))
    return rows


def forge_wants(found: dict) -> set[str]:
    return {read["wait"]["on"] for _wish, read in live(found)
            if str(read["wait"].get("on", "")).startswith(("pr:", "ci:"))}


def _answers(facts, target: str, since: str, replied: str | None = None) -> str | None:
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
        if letter.data.get("re") == target or target in (letter.data.get("links") or ()):
            return str(letter.data.get("id") or letter.id)
    return None


def outcome(wish, read, facts, found: dict, forge: dict, now: datetime) -> str | None:
    """What came true for this park, in words, or None while it still waits."""
    wait = read["wait"]
    on = wait.get("on")
    if on is None:
        pass
    elif on.startswith("pr:"):
        number = int(on.rpartition("#")[2])
        repo = on[3:].partition("#")[0]
        from .ledger import kind_facts
        for merged in kind_facts(facts, "pr.merged"):
            if merged.data.get("repo") == repo and int(merged.data.get("number") or 0) == number:
                return f"{on} merged ({merged.data.get('merge_commit', '')[:8]})"
        state = forge.get(on)
        if state and state.get("state") in ("merged", "closed"):
            return f"{on} {state['state']} at {state.get('at')}: {state.get('url')}"
    elif on.startswith("ci:"):
        state = forge.get(on)
        if state and state.get("state") in ("green", "red"):
            return f"{on} is {state['state']}"
    elif on.startswith("wish:"):
        target = found.get(on[5:])
        if target is not None and target.state in SETTLED and target.state != wait.get("was"):
            return f"{target.id} is {target.state}"
        answer = _answers(facts, on[5:], read["at"])
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
    until = parse_instant(str(wait.get("until", "")))
    if until is not None and until <= now:
        return f"nothing resolved by {wait['until']}"
    return None


def _advice(wish, read, resolved: bool) -> str:
    wait = read["wait"]
    if not resolved and wait.get("default") is not None:
        return (f"nobody answered by {wait['until']}. Your rec was: {wait['default']}. If it is inside "
                "your grant, carry it out now and say so on the thread; a merge, money beyond your "
                "budget or an outward act is never defaulted: re-ask")
    if wait.get("on") is None:
        nxt = PROSE[min(int(wait.get("step") or 1), len(PROSE) - 1)]
        return (f"re-check {wait.get('step', 1)} of a prose park. If it waits on something the loom can "
                f"watch, re-park with wish {wish.id} --read \"waiting: …\" --wait <{GRAMMAR}>; "
                f"if it still waits on words, re-park in prose (next re-check in {_span(nxt)}), "
                "or deliver, ask or settle")
    if resolved:
        return f"go on with {wish.id}: deliver, re-park with a new --wait, or ask"
    return f"re-judge {wish.id}: re-park with a new --wait or --until, deliver, ask or drop it"


def _span(delta: timedelta) -> str:
    return f"{delta.days}d" if delta.days else f"{delta.seconds // 3600}h"


def subject(wish, read) -> str:
    """The same read on every install: its wish, instant and writer."""
    return f"{wish.id}|{read['at']}|{read.get('writer', '')}"


def observe_waits(home, facts, armed: bool, forge: dict | None = None, now: datetime | None = None):
    """Write a notice for each live wait that came true or ran out; only the lease holder writes."""
    if not armed:
        return facts
    from .loom import notice
    from .wishes import told
    found = told(facts)
    rows = live(found)
    if not rows:
        return facts
    now = now or datetime.now(timezone.utc)
    for wish, read in rows:
        said = outcome(wish, read, facts, found, forge or {}, now)
        if said is None:
            continue
        resolved = not said.startswith("nothing resolved by")
        head = (f"wait on {wish.id} {'resolved' if resolved else 'ran out'}: {said}. "
                f"Parked as: {read['reading']}")
        notice(home, facts, "wait", subject(wish, read), head, _advice(wish, read, resolved),
               thread=wish.thread)
    return facts


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

    def ci(self, repo: str, sha: str) -> dict:
        return {"state": self.checks.verdict(f"https://github.com/{repo}.git", sha)}

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
    lines read (wake-vision). A failing poll raises from ``step``, so the
    loop's guard records it.
    """

    def __init__(self, path=None):
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

    def step(self, config, armed: bool) -> None:
        if self.path is not None and not self.cards:
            try:
                self.cards = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self.cards = {}
        open_prs = {key for key in self.prs if self.cards.get(key, {}).get("state") not in ("merged", "closed")}
        cards = self.path is not None and bool(open_prs)
        if not armed or not (self.wants or cards):
            self.failing = None
            return
        if not (self.worker and self.worker.is_alive()):
            now = time.monotonic()
            wants = set(self.wants) if self.checked is None or now - self.checked >= EVERY else set()
            prs = (open_prs if cards and (self.prs_checked is None or now - self.prs_checked >= PRS_EVERY)
                   else set())
            if wants:
                self.checked = now
            if prs:
                self.prs_checked = now
            if wants or prs:
                self.worker = threading.Thread(target=self._poll, args=(forge_for(config), wants, prs),
                                               name="loom-waits", daemon=True)
                self.worker.start()
        if self.failing is not None:
            raise self.failing[0].with_traceback(self.failing[1])

    def _poll(self, forge, wants: set[str], prs: set[str] = frozenset()) -> None:
        try:
            if prs:
                from .home import atomic_write
                # One unreadable pull request keeps its last card; the rest still land.
                cards, missed = dict(self.cards), None
                for key in sorted(prs):
                    repo, _, number = key.partition("#")
                    try:
                        cards[key] = forge.card(repo, int(number))
                    except Exception as exc:
                        missed = missed or exc
                atomic_write(self.path, json.dumps(cards, indent=1, sort_keys=True) + "\n")
                self.cards = cards
                if missed is not None:
                    raise missed
            if not wants:
                self.failing = None
                return
            states = {}
            for on in sorted(wants):
                if on.startswith("pr:"):
                    repo, _, number = on[3:].partition("#")
                    states[on] = forge.pull(repo, int(number))
                else:
                    repo, _, sha = on[3:].partition("@")
                    states[on] = forge.ci(repo, sha)
            self.states = states
            self.failing = None
        except Exception as exc:
            self.failing = (exc, exc.__traceback__)
