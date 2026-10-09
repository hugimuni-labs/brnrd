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

from .attention import actionable, unrunnable_ids
from .config import LoomConfig, granting_window, load_config, loom_clock
from .home import Home, is_channel, mint
from .ledger import LedgerConflict
from .project import fold, holder, owed
from .selfrepo import ReadmeError, parse_readme
from .speak import CHANNEL, speak


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
    path = home.thread_dir(thread) / "README.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = parse_readme(text)
    except ReadmeError:
        return None
    value = data.get("for")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class Router:
    def __init__(self, home: Home, config: LoomConfig | None = None):
        self.home = home
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

    def route(self, facts: list[Fact], bodies: dict, adapter: str, core: str) -> list[Fact]:
        if not self.armed():
            return facts
        from .loom import _attention, _policy, _start, body_alive

        home = self.home
        threads = _routable(home)
        for thread in threads:
            if not self.armed():
                return facts
            if any(body.thread == thread for body in bodies.values()):
                continue
            facts = self._release_abandoned(facts, thread, body_alive)
            if holder(facts, thread):
                continue
            letters = actionable(facts, thread)
            if not letters:
                continue
            target = readme_for(home, thread)
            if target and target != self.config.name:
                _attention(
                    home, f"attention:for-other:{thread}", "for: other brnrd",
                    thread=thread,
                )
                continue
            if adapter == "fake" and _policy(home, thread) is None:
                _attention(
                    home, f"attention:no-policy:{thread}",
                    f"fake adapter: thread {thread} has no policy file",
                    thread=thread,
                )
                continue
            if not self.armed():
                return facts
            from .loom import _next_gen, _record
            strand = f"s-{home.install_id()}-{mint(6)}"
            gen = _next_gen(facts, thread)
            try:
                stored = _record(home, "lease", {
                    "thread": thread, "strand": strand, "gen": gen,
                    "install": home.install_id(), "router_gen": self.lease.gen,
                }, f"lease:{thread}:{gen}")
            except LedgerConflict:
                continue
            facts.append(stored)
            facts = _start(home, strand, thread, gen, adapter, core, bodies, facts)
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
        unrunnable = unrunnable_ids(facts)
        pending = owed(facts, thread)
        alive_work = [
            fact for fact in pending if str(fact.data.get("id")) not in unrunnable
        ]
        fused = any(
            fact.kind == "attention" and fact.id == f"attention:fuse:{strand}"
            for fact in facts
        )
        take = False
        if own and fused and unrunnable and alive_work:
            take = True
        elif owner and not own and install_silent(facts, owner, self.config.router_ttl):
            take = True
        if not take:
            return facts
        return self._keep(facts, "released", {
            "thread": thread, "strand": strand, "gen": gen,
            "why": "holder gone", "install": self.home.install_id(),
            "router_gen": self.lease.gen,
        }, f"released:{thread}:{gen}")

    def _unrouted(self, facts: list[Fact], known: set[str]) -> list[Fact]:
        from .home import thread_of
        from .loom import _attention

        state = fold(facts)
        for fact in state.accepted:
            if fact.kind != "letter" or fact.data.get("id") in state.handled:
                continue
            letter_id = str(fact.data.get("id") or fact.id)
            destination = fact.data.get("to")
            if is_channel(destination):
                continue
            try:
                thread = thread_of(str(destination))
            except ValueError:
                _attention(
                    self.home, f"attention:unroutable:{letter_id}",
                    f"unroutable to {destination!r}", letter=letter_id,
                )
                continue
            if thread not in known:
                _attention(
                    self.home, f"attention:no-thread:{letter_id}",
                    f"no such thread {thread}", thread=thread, letter=letter_id,
                )
        return facts

    def speak_out(self, facts: list[Fact]) -> list[Fact]:
        if not self.armed() or self.lease is None:
            return facts
        state = fold(facts)
        sent = {
            str(fact.data.get("key"))
            for fact in facts
            if fact.kind == "speech" and fact.data.get("state") == "sent"
        }
        for fact in state.accepted:
            if not self.armed() or self.lease is None:
                return facts
            to = fact.data.get("to")
            if fact.kind != "letter" or not is_channel(to):
                continue
            key = str(fact.data.get("id") or "")
            if not key or key in sent or key in state.handled:
                continue
            raw = fact.data.get("router_gen")
            try:
                grant = int(raw)
            except (TypeError, ValueError):
                continue
            if grant != self.lease.gen:
                continue
            body = str(fact.data.get("body") or "")
            context: dict = {}
            if to != CHANNEL:
                from .channels.relay import person_dm
                if person_dm(self.home, str(to)) is None:
                    from .loom import _attention
                    _attention(
                        self.home, f"attention:speech-refused:{key}",
                        f"speech refused: {to} is not a known person's direct chat",
                        letter=key,
                    )
                    continue
                body = f"{_header(state, fact)}\n\n{body}"
                event_id = _answers_event(state, fact)
                if event_id:
                    context["event_id"] = event_id
            status, speech = speak(
                self.home, self.lease, key, body, channel=str(to), context=context,
            )
            if speech is not None:
                facts.append(speech)
                if speech.data.get("state") == "sent":
                    sent.add(key)
            if status == "stepped-down":
                self.granting = False
                return facts
        return facts


def _header(state, letter: Fact) -> str:
    """``<strand> · <thread>``: who is speaking, from which thread (fork 3)."""
    strand = letter.by.removeprefix("strand:")
    thread = next((name for name, (holder, _gen) in state.holder.items()
                   if holder == strand), None)
    return f"{strand} · {thread}" if thread else strand


def _answers_event(state, letter: Fact) -> str | None:
    """The relay event a letter answers: its ``re`` names a relay-cited letter."""
    re_id = letter.data.get("re")
    if not re_id:
        return None
    for fact in state.accepted:
        if fact.kind == "letter" and fact.data.get("id") == re_id:
            cites = str(fact.data.get("cites") or "")
            if cites.startswith("source:relay:"):
                return cites.split("source:relay:", 1)[1]
    return None


def _routable(home: Home) -> list[str]:
    from .loom import _threads
    return [thread for thread in _threads(home) if home.on_main(thread)]
