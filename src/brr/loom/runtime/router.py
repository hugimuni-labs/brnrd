"""One router on the home. Everyone else ingests and runs the strands they already hold.

The router lease is a ``LocalLeaseAuthority`` at ``<home>/ledger/leases/``.
Grants and sends carry ``router_gen`` and stop at ``until - max_skew - margin``
by this loom's clock, so a peer whose clock runs fast cannot acquire while
this one is still granting.
"""

from __future__ import annotations

from datetime import datetime, timezone

from brr.daemon2.facts import Fact
from brr.daemon2.leases import Lease, LocalLeaseAuthority

from .config import LoomConfig, granting_window, load_config, loom_clock
from .home import Home, is_channel, mint
from .ledger import LedgerConflict, LedgerReader
from .project import (
    fold, holder, is_notice, sender_threads,
)
from .selfrepo import ReadmeError, parse_readme
from .owners import letter_owners, letters_for
from .lifecycle import main_snapshot
from .speak import speak

REST_FACE = "b·_·d"


def install_of(strand: str) -> str | None:
    parts = strand.split("-")
    if len(parts) == 3 and parts[0] == "s" and len(parts[1]) == 4:
        return parts[1]
    return None


def _when(fact: Fact) -> datetime:
    at = datetime.fromisoformat(fact.at)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at


def install_silent(facts: list[Fact], install: str, ttl: float) -> bool:
    """No fact from ``install`` for ``ttl`` wall seconds.

    Fact timestamps are wall time. The skewed lease clock is not used here;
    a peer that is merely ahead on the lease clock is not "silent".
    """
    latest: datetime | None = None
    mark = f"loom:{install}"
    for fact in facts:
        owned = fact.by == mark or (fact.hlc is not None and fact.hlc[2] == install)
        if not owned:
            continue
        at = _when(fact)
        if latest is None or at > latest:
            latest = at
    if latest is None:
        return True
    return (datetime.now(timezone.utc) - latest).total_seconds() >= ttl


def readme_for(home: Home, thread: str) -> str | None:
    """``for:`` from a step-2 README. A step-1 plain README has none."""
    value = main_snapshot(home).get(thread, {}).get("for")
    return value.strip() if isinstance(value, str) and value.strip() else None



class Router:
    def __init__(self, home: Home, config: LoomConfig | None = None):
        self.home = home
        self.reader = LedgerReader()
        self.projections = {}
        self.projection_revision = -1
        self.config = config if config is not None else load_config(home.root)
        self.clock = loom_clock
        self.authority = LocalLeaseAuthority(
            home.root / "ledger" / "leases", clock=self.clock,
        )
        self.lease: Lease | None = None
        self.granting = False
        self.caught_up = False
        self._renew_bucket: int | None = None

    def _window(self) -> bool:
        if self.lease is None:
            return False
        return granting_window(
            self.clock(), self.lease.until,
            self.config.max_skew, self.config.margin,
        )

    def armed(self) -> bool:
        """Authorize, then refuse the skew window. A false steps down now."""
        if self.lease is None or not self.granting or not self.caught_up:
            return False
        if not self.authority.authorize(self.lease):
            self.lease = None
            self.granting = False
            self.caught_up = False
            return False
        if not self._window():
            self.granting = False
            return False
        return True

    def _keep(self, facts: list[Fact], kind: str, data: dict, fact_id: str) -> list[Fact]:
        from .loom import _record
        try:
            facts.append(_record(self.home, kind, data, fact_id))
        except LedgerConflict:
            pass
        return facts

    def _prior_visible(self, facts: list[Fact], gen: int) -> bool:
        if gen <= 1:
            return True
        for fact in facts:
            if fact.kind not in {"router", "router.renewed"}:
                continue
            try:
                previous = int(fact.data["gen"])
            except (KeyError, TypeError, ValueError):
                continue
            if previous < gen:
                return True
        return False

    def begin_tick(self, facts: list[Fact]) -> list[Fact]:
        if self.lease is None:
            return self._acquire(facts)
        renewed = self.authority.renew(self.lease, self.config.router_ttl)
        if renewed is None:
            self.lease = None
            self.granting = False
            self.caught_up = False
            return self._acquire(facts)
        self.lease = renewed
        facts = self._mark_renewed(facts)
        self.granting = self.caught_up and self._window()
        return facts

    def _acquire(self, facts: list[Fact]) -> list[Fact]:
        acquired = self.authority.acquire(
            "router", self.home.install_id(), self.config.router_ttl,
        )
        if acquired is None:
            self.lease = None
            self.granting = False
            self.caught_up = False
            return facts
        self.lease = acquired
        self._renew_bucket = self._bucket()
        known = any(
            fact.kind == "router" and int(fact.data.get("gen", -1)) == acquired.gen
            for fact in facts
        )
        if not known:
            facts = self._keep(facts, "router", {
                "install": self.home.install_id(),
                "gen": acquired.gen,
                "until": acquired.until,
            }, f"router:{acquired.gen}")
        self.caught_up = self._prior_visible(facts, acquired.gen)
        self.granting = self.caught_up and self._window()
        return facts

    def _bucket(self) -> int:
        width = self.config.router_ttl / 3
        if width <= 0:
            width = self.config.router_ttl or 1
        return int(self.clock() // width)

    def _mark_renewed(self, facts: list[Fact]) -> list[Fact]:
        if self.lease is None:
            return facts
        bucket = self._bucket()
        if bucket == self._renew_bucket:
            return facts
        self._renew_bucket = bucket
        return self._keep(facts, "router.renewed", {
            "install": self.home.install_id(),
            "gen": self.lease.gen,
            "until": self.lease.until,
        }, f"router.renewed:{self.home.install_id()}:{self.lease.gen}:{bucket}")

    def route(self, facts: list[Fact], bodies: dict, default: tuple[str, str]) -> list[Fact]:
        if not self.armed():
            return facts
        from .loom import _attention, _closures, _policy, _start, body_alive, notice

        home = self.home
        threads = _routable(home, facts)
        facts = _closures(home, facts, threads)
        from .lifecycle import CLOSED, read_thread
        pending = []
        for thread in threads:
            if read_thread(home, thread).get("status") in CLOSED:
                continue
            if not self.armed():
                break
            facts = self._release_abandoned(facts, thread, body_alive)
            if holder(facts, thread):
                continue
            letters = [f for f in letters_for(facts, main_snapshot(home), thread) if not f.data.get("notice")]
            if not letters:
                continue
            target = readme_for(home, thread)
            if target and target != self.config.name:
                self._keep(facts, "letter.disposed", {"dispositions": [
                    {"id": f.id, "outcome": "operator", "reason": "for: other brnrd"}
                    for f in letters]}, f"for-other:{thread}:{letters[-1].id}")
                continue
            from .adapters import RepoSeatError, at_birth, repos as seat_repos, seat_text
            from .repos import AuthorError, require_body_author
            try:
                text = seat_text(home, thread)
                choice = at_birth(home, thread, default, text)
                declared = seat_repos(text)
                require_body_author(home)
            except (ValueError, OSError) as exc:
                if isinstance(exc, RepoSeatError):
                    hint = "correct the repo lines in its seat"
                elif isinstance(exc, AuthorError) and exc.enrolled:
                    hint = "choose an author that is not an enrolled person"
                elif isinstance(exc, AuthorError):
                    hint = "set author in loom/config.toml to Name <email>"
                else:
                    hint = "correct its body: shell/core choice"
                notice(home, facts, "body", thread, f"thread {thread}: {exc}",
                       hint, thread=thread)
                continue
            if choice[0] == "fake" and _policy(home, thread) is None:
                self._keep(facts, "letter.disposed", {"dispositions": [
                    {"id": f.id, "outcome": "operator", "reason": "fake adapter has no policy"}
                    for f in letters]}, f"no-policy:{thread}:{letters[-1].id}")
                continue
            if not self.armed():
                break
            from .loom import _next_gen, _record
            strand = f"s-{home.install_id()}-{mint(6)}"
            gen = _next_gen(facts, thread)
            data = {
                "thread": thread, "strand": strand, "gen": gen,
                "install": home.install_id(), "router_gen": self.lease.gen,
                "shell": choice[0], "core": choice[1], "repos": declared,
            }
            molts = {f.id: f for f in fold(facts).accepted if f.kind == "molt"}
            for letter in reversed(letters):
                molt = molts.get(letter.data.get("molt"))
                # Only this thread's own last body may hand its room on: a molt
                # marker on a letter to another thread would graft a foreign room.
                if (molt is not None and molt.data.get("strand") == letter.data.get("from")
                        and molt.data.get("gen") == letter.data.get("gen")
                        and sender_threads(facts).get(letter.data.get("from")) == thread):
                    if not molt.data.get("fresh", False):
                        data["parent"] = letter.data["from"]
                    break
            try:
                stored = _record(home, "lease", data, f"lease:{thread}:{gen}")
            except LedgerConflict:
                continue
            facts.append(stored)
            pending.append((strand, thread, gen, choice, declared))
        # Clone/start is slow under load; doing it inside the grant loop burned
        # the skew window so later threads never got a lease this generation.
        for strand, thread, gen, choice, declared in pending:
            facts = _start(home, strand, thread, gen, bodies, facts, choice, repos=declared)
        if not self.armed():
            return facts
        facts = self._unrouted(facts, set(threads))
        return facts

    def _release_abandoned(self, facts, thread: str, body_alive) -> list[Fact]:
        held = holder(facts, thread)
        if held is None or self.lease is None:
            return facts
        strand, gen = held
        if body_alive(self.home.room(strand)):
            return facts
        owner = install_of(strand)
        own = owner == self.home.install_id()
        if not (owner and not own and install_silent(facts, owner, self.config.router_ttl)):
            return facts
        from .loom import _ingest, _end_attempt, _drained
        from .lifecycle import read_thread, CLOSED
        facts = _ingest(self.home, facts, self.config.router_ttl)
        state = fold(facts)
        shown = state.shown.get(strand, set())
        letters = [f for f in letters_for(facts, main_snapshot(self.home), thread) if f.id in shown]
        molted = any(f.kind == 'molt' and f.data.get('strand') == strand and f.data.get('gen') == gen
                     for f in state.accepted)
        cause = ('molted' if molted else 'settled' if read_thread(self.home, thread).get('status') in CLOSED
                 else 'failed' if letters else 'idle')
        facts = _end_attempt(self.home, facts, strand, thread, gen, None, letters, cause)
        facts = _drained(self.home, facts, strand, thread, gen)
        return self._keep(facts, "released", {
            "thread": thread, "strand": strand, "gen": gen,
            "why": "holder gone", "install": self.home.install_id(),
            "router_gen": self.lease.gen,
        }, f"released:{thread}:{gen}")

    def _unrouted(self, facts: list[Fact], known: set[str]) -> list[Fact]:
        """Materialize the table's climb/invalid-address decision atomically."""
        from .loom import notice
        for fate in letter_owners(facts, main_snapshot(self.home)).values():
            if fate.action not in {"climb", "unroutable"}:
                continue
            letter = fate.letter
            if fate.action == "climb":
                kind, target = "climbed", fate.thread
                what = f"letter {letter.id} to closed thread {letter.data.get('to', '').removeprefix('thread:')} climbed here"
            else:
                kind, target = "unroutable", "inbox" if "inbox" in known else None
                what = f"letter {letter.id} addressed to {letter.data.get('to')!r}, which is not a thread on main"
            data = {"id": letter.id, "outcome": "operator", "reason": fate.reason}
            if target:
                successor = notice(self.home, facts, kind, letter.id, what,
                                   str(letter.data.get('body') or ''),
                                   thread=target, about=letter, stands=True, draft_only=True)
                if isinstance(successor, Fact):
                    data.update(outcome="done", successor=successor.data)
            self._keep(facts, "letter.disposed", {"dispositions": [data]},
                       f"letter.disposed:{letter.id}")
        return facts

    def speak_out(self, facts: list[Fact]) -> list[Fact]:
        if not self.armed() or self.lease is None:
            return facts
        from .loom import _log, notice, notice_id
        state = fold(facts)
        threads = sender_threads(facts)
        ids = {fact.id for fact in facts}
        table = letter_owners(facts, main_snapshot(self.home))
        sent = {key for key, fate in table.items() if fate.done}
        for fact in state.accepted:
            if not self.armed() or self.lease is None:
                return facts
            to = fact.data.get("to")
            if fact.kind != "letter" or not is_channel(to):
                continue
            key = str(fact.data.get("id") or "")
            if not key or key not in table or table[key].owner != "channel":
                continue
            raw = fact.data.get("router_gen")
            try:
                grant = int(raw)
            except (TypeError, ValueError):
                continue

            def tell(kind: str, what: str, can: str) -> None:
                # A loom-authored failure has no replying body to repair its send.
                if is_notice(fact) and key.startswith("loom/failed-message-"):
                    self._keep(facts, "letter.disposed", {"dispositions": [{
                        "id": key, "outcome": "operator", "reason": f"{kind}: {what}",
                    }]}, f"letter.disposed:{key}")
                    return
                # Always the thread that drafted it; the draft is never resent.
                successor = notice(self.home, facts, kind, key,
                       f"your message {key} to {to} {what}", can,
                       thread=threads.get(fact.by.removeprefix("strand:")),
                       about=fact, stands=True, draft_only=True)
                if isinstance(successor, Fact):
                    self._keep(facts, "letter.disposed", {"dispositions": [{
                        "id": key, "outcome": "done", "reason": kind,
                        "successor": successor.data,
                    }]}, f"letter.disposed:{key}")

            fresh = "send a fresh message if it still matters; this one is never sent again"
            if grant != self.lease.gen:
                if grant < self.lease.gen:
                    tell("stale-speech", "was not sent: it was drafted under router "
                         f"gen {grant} and a new router (gen {self.lease.gen}) took over", fresh)
                continue
            try:
                body = str(fact.data.get("body") or "")
                context = {k: str(fact.data[k]) for k in ("from", "clean", "edit") if k in fact.data}
                if fact.data.get("refs"):
                    context["refs"] = " ".join(fact.data["refs"])
                from .home import channel_parts
                _instance, native = channel_parts(str(to), self.home)
                if native is not None:
                    from .connector import person_dm
                    if person_dm(self.home, str(to)) is None:
                        tell("speech-refused",
                             "was refused: that is not a known person's direct chat", fresh)
                        continue
                    body = f"{_header(state, fact)}\n\n{body}"
                    event_id = _answers_event(state, fact)
                    from .project import reply_channels
                    if event_id and reply_channels(facts).get(fact.data.get("re")) == to:
                        context["re"] = event_id
                status, speech, unsure = speak(
                    self.home, self.lease, key, body, channel=str(to), context=context,
                )
            except Exception as exc:
                why = f"was refused: {type(exc).__name__}: {exc}"
                tell("speech-refused", why, fresh)
                _log(self.home, f"speech {key} {why}")
                continue
            if status == "not-sent":
                tell("speech-refused", f"was not sent: {speech.data['reason']}", fresh)
            if unsure:
                tell("maybe-sent", f"may or may not have arrived: letter {', '.join(unsure)} "
                     "was attempted and no receipt came back", fresh)
            if speech is not None:
                facts.append(speech)
                if speech.data.get("state") == "sent":
                    sent.add(key)
            if status == "stepped-down":
                self.granting = False
                return facts
        return facts


def _header(state, letter: Fact) -> str:
    """The resident's face and thread; strand ids stay in the ledger."""
    strand = letter.by.removeprefix("strand:")
    thread = next((f.data["thread"] for f in reversed(state.accepted)
                   if f.kind == "lease" and f.data["strand"] == strand), None)
    return f"{REST_FACE} ⌁ [ {thread} ]" if thread else f"{REST_FACE} ⌁"


def _answers_event(state, letter: Fact) -> str | None:
    """The transport id of the inbound letter this letter answers."""
    re_id = letter.data.get("re")
    for fact in state.accepted:
        if fact.kind == "letter" and fact.data.get("id") == re_id:
            cites = str(fact.data.get("cites") or "")
            if cites.startswith("source:"):
                return cites.split(":", 2)[2]
    return None


def _routable(home: Home, facts: list[Fact]) -> list[str]:
    return sorted(main_snapshot(home))
