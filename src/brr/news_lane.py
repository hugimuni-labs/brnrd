"""The news lane: typed, deduped facts about the world outside the repo, with receipts.

The gap this closes: `release_availability.py` already knows when a newer
brnrd exists, but that fact only ever reached CLI stdout, the daemon boot
log, and the wake prompt — the resident heard it, never the human at the
other end of the chat, and the web dashboard said nothing about versions at
all. This module is the seam between "the daemon observed something" and
the two places a person actually looks: the dashboard (pulled, always
current) and chat (pushed, on a cadence a human chose, not a stream of
per-item pings).

Shape, deliberately narrow:

- A :class:`NewsItem` is the unit — kind, subject, prior -> current, when it
  was observed, the receipt (a URL or endpoint name) that said so, and an
  optional ``expires_at`` for the one class of fact that is a deadline
  rather than news (see "Cadence" below). No producer-specific fields leak
  past this type; a dashboard renderer or a chat sender that only ever
  sees ``NewsItem`` cannot drift when a new kind of fact shows up.
- A **producer** is any ``Callable[[Path], Sequence[NewsItem]]``. This
  module ships exactly one (:func:`release_producer`, wrapping
  :mod:`release_availability`'s PyPI + npm channels); a sibling producer
  for a shell version bump, a newly available model, or a pinned core's
  retirement date plugs into :data:`DEFAULT_PRODUCERS` without this file's
  dashboard/announce code changing at all.
- **The dashboard reads :func:`collect` directly** — pull-based, so nothing
  needs deduping: whatever is true right now is what renders, every time.
- **Cadence: a daily briefing, not a stream of pings** (the maintainer's own
  framing). News is not urgent, and a lane that pings per item trains
  people to ignore it. Two lanes, both push-based and both deduped against
  the same ledger, split on one property only:

  - :func:`pending_interrupts` — items with ``expires_at`` set: "a
    dependency you have pinned retires on this date" is a deadline, not
    news, and skips the batch. The rule is *has an expiry*, not *is
    important* — keep it that narrow, or the exception eats the batching.
  - :func:`due_briefing` — everything else, batched into at most one
    message per :data:`BRIEFING_INTERVAL_SECONDS`, and only when there is
    something new to say (never an empty send).

  Both read :data:`CHAT_POLICY` first: a kind that isn't chat-worthy never
  reaches either lane, interrupt or briefing.
- **A rendered briefing is a view, never the store.** Nothing here persists
  composed text as a source of truth — :func:`due_briefing` regenerates the
  message from :func:`collect` (each producer's own receipted state) and
  the ledger (what has already been said) every time it is asked. The
  ledger is the only thing this module persists, and it records *what was
  said*, never *what the product currently is* — that's `.collect()`'s job,
  re-derived fresh from each producer on every call.
- **Delivery is one seam.** Neither lane sends anything itself — each
  returns data (a list of items, or a :class:`Briefing`) and a
  ``record_*`` function the caller invokes only once delivery is
  confirmed. ``daemon.py`` is the one caller today, and sends via a chat
  gate; a future dated-file writer to the account home plugs in at that
  same call site without this module changing.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import account, release_availability

#: The ledger's own cache file — machine-scoped like
#: ``release_availability.cache_path``, and deliberately a *different* file:
#: that cache records what is *true* (refreshed on a 24h TTL); this one
#: records what has been *said* (refreshed only on confirmed delivery). The
#: two lifecycles must not share a file or a truthful-but-unsent value could
#: never be distinguished from an already-announced one.
LEDGER_NAME = "news-lane-announced.json"
SCHEMA = 1

#: The daily briefing's own clock. Deliberately not a ``schedule.md`` entry
#: — the briefing rides the daemon's existing heartbeat tick (see
#: ``daemon.py``'s call site next to ``release_availability``'s own), and
#: this interval is the debounce that keeps a ~10s tick from composing more
#: than one message a day.
BRIEFING_INTERVAL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class NewsItem:
    """One typed, receipted fact: *subject* changed from *prior* to *current*.

    ``expires_at`` is the one field that changes cadence, not meaning: set
    it (an ISO date or any human-readable deadline string a producer wants
    rendered verbatim) when the fact is "acts as of a date" rather than
    "changed" — a pinned core's retirement notice, say. Leave it ``None``
    for everything else. Nothing here validates its format; the producer
    that sets it owns what it means.
    """

    kind: str
    subject: str
    prior: str | None
    current: str
    observed_at: float
    source: str
    expires_at: str | None = None

    @property
    def key(self) -> str:
        """Stable identity for dedup/persistence — never rendered."""
        return f"{self.kind}:{self.subject}"

    def render(self) -> str:
        if self.expires_at:
            return f"{self.subject}: {self.current} (retires {self.expires_at})"
        if self.prior and self.prior != self.current:
            return f"{self.subject} update available: {self.prior} → {self.current}"
        return f"{self.subject}: {self.current}"


# --- Producers ---------------------------------------------------------

#: A producer inspects the repo/installation and reports whatever it
#: currently believes is true — usually zero or one item, never a promise
#: about the future. Exceptions are the caller's problem to guard against
#: (see :func:`collect`'s fail-open loop), not this type's.
Producer = Callable[[Path], "Sequence[NewsItem]"]


def release_producer(repo_root: Path) -> list[NewsItem]:
    """Wraps :mod:`release_availability`'s PyPI + npm channels.

    Which channel actually installed this copy is not knowable from here,
    so both are checked and both are named — never one guessed to stand in
    for the other (the maintainer's own framing for this strand).
    """
    now = time.time()
    items: list[NewsItem] = []
    for subject, obs, source in (
        ("pypi", release_availability.observation(repo_root), release_availability.PYPI_URL),
        (
            "npm",
            release_availability.npm_observation(repo_root),
            release_availability.NPM_REGISTRY_URL,
        ),
    ):
        if obs is None or not obs.available:
            continue
        items.append(
            NewsItem(
                kind="release",
                subject=subject,
                prior=obs.installed,
                current=obs.latest,
                observed_at=now,
                source=source,
            )
        )
    return items


#: The extension point. A sibling producer (shell-version, model-
#: availability, a pinned core's retirement date) is a function of this
#: same shape, added to this tuple — nothing else in this module changes.
DEFAULT_PRODUCERS: tuple[Producer, ...] = (release_producer,)


def collect(
    repo_root: Path, *, producers: Sequence[Producer] | None = None
) -> list[NewsItem]:
    """Every currently-true news item, across all registered producers.

    Fail-open per producer, matching ``release_availability``'s own
    posture: one producer's bug or a malformed cache must not blank the
    dashboard line every *other* producer would still have shown.

    ``producers`` defaults to :data:`DEFAULT_PRODUCERS` **read at call
    time**, not baked into the signature — a mutable module default in a
    parameter list is bound once at import, so a test (or a sibling
    registering a producer at runtime) patching the module attribute would
    silently have no effect on a default already captured. Every function
    in this module that takes ``producers`` follows the same ``None``
    sentinel for that reason.
    """
    items: list[NewsItem] = []
    for producer in producers if producers is not None else DEFAULT_PRODUCERS:
        try:
            items.extend(producer(repo_root))
        except Exception:
            continue
    return items


# --- Chat policy ---------------------------------------------------------

#: Which *kinds* ever earn an unprompted chat message (interrupt or
#: briefing — both consult this first). ``release`` is on: the maintainer's
#: own scoping of the daily briefing names "published brnrd releases"
#: explicitly. Shell-version / model-availability / retirement kinds join
#: this table the day a producer for them exists; until then there is no
#: kind string to list, and an unlisted kind defaults to dashboard-only
#: (see :func:`is_chat_worthy`) rather than guessing one.
CHAT_POLICY: dict[str, bool] = {
    "release": True,
}


def is_chat_worthy(kind: str) -> bool:
    """``CHAT_POLICY``'s lookup, defaulting an unlisted kind to dashboard-only.

    The default matters as much as the table: a sibling producer that adds
    a new ``kind`` and forgets to list it here gets silence, never an
    accidental chat blast.
    """
    return bool(CHAT_POLICY.get(kind, False))


# --- Ledger: announced values + the briefing clock ------------------------
#
# One file, two independent facts: "what has been said" (per item key) and
# "when the last briefing went out" (one timestamp). Both are write-once-
# per-event, both are read far more often than written, and splitting them
# into two files would only double the failure surface for no isolation
# benefit — nothing here ever needs one without the other in the same tick.


def ledger_path(repo_root: Path) -> Path:
    """The machine-scoped announce ledger — one per installed brnrd, not per repo."""
    del repo_root  # mirrors release_availability.cache_path's own reasoning
    return account._xdg_state_home() / account.DEFAULT_STATE_NAMESPACE / LEDGER_NAME


def _load_state(repo_root: Path) -> dict[str, object]:
    try:
        payload = json.loads(ledger_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_ledger(repo_root: Path) -> dict[str, str]:
    announced = _load_state(repo_root).get("announced")
    if not isinstance(announced, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in announced.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _last_briefing_at(repo_root: Path) -> float | None:
    value = _load_state(repo_root).get("last_briefing_at")
    return float(value) if isinstance(value, (int, float)) else None


def _write_state(repo_root: Path, *, announced: dict[str, str], last_briefing_at: float | None) -> None:
    path = ledger_path(repo_root)
    payload: dict[str, object] = {"schema": SCHEMA, "announced": announced}
    if last_briefing_at is not None:
        payload["last_briefing_at"] = last_briefing_at
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def record_announced(repo_root: Path, item: NewsItem) -> None:
    """Mark *item*'s current value as said — call only after confirmed delivery."""
    ledger = _load_ledger(repo_root)
    ledger[item.key] = item.current
    _write_state(repo_root, announced=ledger, last_briefing_at=_last_briefing_at(repo_root))


# --- Interrupt lane: a dependency with an expiry --------------------------


def pending_interrupts(
    repo_root: Path, *, producers: Sequence[Producer] | None = None
) -> list[NewsItem]:
    """Chat-worthy items with an expiry, never announced at this current value.

    The one exception to batching (see the module docstring's "Cadence"):
    checked every tick, sent the moment it's new, independent of
    :func:`due_briefing`'s daily clock. Read-only, same contract as
    :func:`due_briefing` — the caller records via :func:`record_announced`
    only once delivery is confirmed.
    """
    ledger = _load_ledger(repo_root)
    return [
        item
        for item in collect(repo_root, producers=producers)
        if item.expires_at and is_chat_worthy(item.kind) and ledger.get(item.key) != item.current
    ]


# --- Briefing lane: everything else, batched daily ------------------------


@dataclass(frozen=True)
class Briefing:
    """A view over the items due to be said — never persisted as such."""

    items: tuple[NewsItem, ...]
    generated_at: float

    def render(self) -> str:
        lines = "\n".join(f"- {item.render()}" for item in self.items)
        return f"brnrd news\n{lines}"


def due_briefing(
    repo_root: Path,
    *,
    now: float | None = None,
    interval: float = BRIEFING_INTERVAL_SECONDS,
    producers: Sequence[Producer] | None = None,
) -> Briefing | None:
    """The next daily briefing, or ``None`` when nothing is due.

    ``None`` covers two distinct reasons, deliberately not distinguished in
    the return value (a caller checking "why not" has no action to take
    either way): the interval since the last briefing hasn't elapsed, or it
    has but nothing chat-worthy has changed since. Never returns an empty
    :class:`Briefing` — a briefing with nothing to say is not sent, and
    does not reset the clock (the next tick with real news still finds the
    interval already elapsed).
    """
    current_time = time.time() if now is None else now
    last_sent = _last_briefing_at(repo_root)
    if last_sent is not None and current_time - last_sent < interval:
        return None
    ledger = _load_ledger(repo_root)
    items = tuple(
        item
        for item in collect(repo_root, producers=producers)
        if not item.expires_at and is_chat_worthy(item.kind) and ledger.get(item.key) != item.current
    )
    if not items:
        return None
    return Briefing(items=items, generated_at=current_time)


def record_briefing_sent(repo_root: Path, briefing: Briefing) -> None:
    """Mark every item in *briefing* as said and advance the daily clock.

    Call only after confirmed delivery — same contract as
    :func:`record_announced`, which this delegates to per item.
    """
    ledger = _load_ledger(repo_root)
    for item in briefing.items:
        ledger[item.key] = item.current
    _write_state(repo_root, announced=ledger, last_briefing_at=briefing.generated_at)
