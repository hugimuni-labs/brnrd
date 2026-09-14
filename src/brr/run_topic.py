"""The event carries its topic — the run's `.topic`, inheritance, assignment.

design-the-loom §21 (move 5c): every act that enters the loom belongs to
**exactly one topic at the moment of entry**, picked or minted then, and is
never reclassified after the fact. This module is the frame's half of that
rule; ``heddles`` holds the store (the per-topic index, the thread map, the
proposal) and ``outbox/*`` call in here at each verb.

The pieces, in the order a run meets them:

1. **The proposal at dispatch.** ``worker.prepare`` stamps the waking event
   with ``topic_proposed`` (``heddles.propose``: the signatures' best match
   over the event's text, else the thread's last assigned topic, else
   nothing). A proposal is not an assignment: nothing is indexed.
2. **The boot sets the topic.** Beside ``.card`` and ``.mood`` the wake asks
   for ``.topic`` in the run's outbox — one line: an existing heddle's slug,
   ``new <slug>`` (minted here, a resident only), or ``null``.
   :func:`settle` reads it on the heartbeat and at every drain; the first
   slug it resolves **confirms or overrides** the proposal: the waking event
   gains ``topic:``, one ``event`` row lands in that topic's index, and the
   thread remembers it. An event that already carries ``topic:`` (a strand's
   dispatch, assigned by its parent at entry) is never re-stamped.
3. **Every act inherits.** :func:`for_act` answers the topic of one outbox
   act: its own ``topic: <slug>`` if it names a live heddle, else the run's
   ``.topic``, else the waking event's ``topic``, else none.
4. **A run that errs unassigned** gets ``topic: None`` and ``topic_unset:
   True`` on its manifest (:func:`mark_unset_on_error`); the next run on the
   thread reads :func:`predecessor_topic_unset` into its bundle and may
   assign it once with ``topic: assign <slug> -> <event-id>``.

Portable: a home with no topics proposes nothing and assigns nothing, and
when no topic resolves nothing is written anywhere.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from . import heddles

CONTROL_NAME = ".topic"  # == run_ledger.RUN_TOPIC_CONTROL_NAME (pinned by test)
_NULL_WORDS = frozenset({"null", "none", "~", "-"})
_CONTROL_READ_CAP = 512

#: ``task.meta`` keys this module owns. ``topic`` is the *waking event's*
#: confirmed topic (``Run.from_event`` copies event frontmatter into meta, so
#: a dispatch-assigned event arrives here already set).
META_RUN_TOPIC = "run_topic"
META_CONTROL_STAMP = "run_topic_control"
META_EVENT_TOPIC = "topic"
META_PROPOSED = "topic_proposed"
META_PROPOSED_WHY = "topic_proposed_by"
META_UNSET = "topic_unset"

Notice = Callable[[str, str], None]


@dataclass(frozen=True)
class Control:
    """The first line of ``.topic``: ``op`` ∈ ``slug`` · ``new`` · ``null``."""

    op: str
    slug: str
    stamp: str


def read_control(outbox_dir: Path | None) -> Control | None:
    """``.topic``, parsed leniently — ``None`` when absent, empty or not the
    grammar (``<slug>`` · ``new <slug>`` · ``null``, an optional ``topic:``
    prefix). Never raises."""
    if outbox_dir is None:
        return None
    path = Path(outbox_dir) / CONTROL_NAME
    try:
        if path.is_symlink():
            return None
        with path.open("rb") as handle:
            raw = handle.read(_CONTROL_READ_CAP)
    except OSError:
        return None
    lines = raw.decode("utf-8", errors="replace").splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    if first.lower().startswith("topic:"):
        first = first[len("topic:"):].strip()
    first = first.strip("`").strip()
    if not first:
        return None
    words = first.split()
    stamp = first
    if len(words) == 1 and words[0].lower() in _NULL_WORDS:
        return Control("null", "", stamp)
    if len(words) == 2 and words[0].lower() == "new" and heddles.SLUG_RE.match(words[1]):
        return Control("new", words[1], stamp)
    if len(words) == 1 and heddles.SLUG_RE.match(words[0]):
        return Control("slug", words[0], stamp)
    return None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mint(account_home: Path, slug: str) -> Path | None:
    """A topic file with an empty signature — it lights on assignments and
    claims until the resident writes a signature. ``None`` on a failed write."""
    directory = heddles.topics_dir(account_home)
    if directory is None:
        return None
    topic = heddles.Topic(slug=slug, path=directory / f"{slug}.md", title=slug, body=f"# {slug}\n")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = topic.path.with_name(f".{topic.path.name}.tmp")
        tmp.write_text(heddles.render_topic(topic), encoding="utf-8")
        os.replace(tmp, topic.path)
    except OSError:
        return None
    return topic.path


def assign(
    account_home: Path | None,
    slug: str | None,
    *,
    kind: str,
    ref: str,
    run: str = "",
    thread: str = "",
    at: object = None,
) -> bool:
    """One act → one index row. ``True`` when a row was written; nothing
    happens for no slug or no home. *thread* also records the thread's last
    assigned topic — the proposal's fallback — and is passed only for the
    conversation's own acts (the event, a message on it), never for a strand,
    a bolt, a fold or produce, which may sit under a different layer."""
    if not slug or account_home is None:
        return False
    written = heddles.append_index(account_home, slug, kind=kind, ref=ref, at=at, run=run)
    if thread:
        heddles.record_thread_topic(account_home, thread, slug, at=at, run=run)
    return written


def _event_path(inbox_dir: Path | None, event_id: str) -> Path | None:
    if inbox_dir is None or not event_id:
        return None
    path = Path(inbox_dir) / f"{event_id}.md"
    return path if path.is_file() else None


def stamp_event(
    inbox_dir: Path | None, event_id: str, **updates: object,
) -> bool:
    """Set flat keys on the inbox event file; ``False`` when it is gone."""
    from . import protocol

    path = _event_path(inbox_dir, event_id)
    if path is None:
        return False
    try:
        protocol.update_event_meta({"_path": path}, **updates)
    except (OSError, ValueError):
        return False
    return True


def settle(
    task: Any,
    *,
    outbox_dir: Path | None,
    account_home: Path | None,
    inbox_dir: Path | None = None,
    notice: Notice | None = None,
    is_strand: bool = False,
) -> str | None:
    """Fold the current ``.topic`` into the run; return the run's topic.

    Idempotent: a control already settled (same first line) does nothing but
    answer. A new control:

    - ``new <slug>`` mints ``surface/topics/<slug>.md`` (a resident only —
      the heddles are the whole cloth's) unless it exists, then assigns;
    - ``<slug>`` assigns when it names a live heddle (an alias resolves to
      the topic that absorbed it); an unknown slug is refused by notice and
      the run keeps whatever topic it had;
    - ``null`` says, deliberately, that the run has no topic.

    The first slug settled stamps the waking event (see module docstring).
    Never raises.
    """
    meta = getattr(task, "meta", None)
    if not isinstance(meta, dict):
        return None
    try:
        control = read_control(outbox_dir)
        if control is None or meta.get(META_CONTROL_STAMP) == control.stamp:
            return meta.get(META_RUN_TOPIC) or None
        meta[META_CONTROL_STAMP] = control.stamp
        say = notice or (lambda _kind, _text: None)
        if control.op == "null":
            meta[META_RUN_TOPIC] = None
            return None
        slug = heddles.resolve_slug(account_home, control.slug)
        if control.op == "new" and slug is None:
            if is_strand:
                say("refused", f".topic refused: new {control.slug} — a strand names an existing "
                    "heddle; minting one is the seat's act")
                return meta.get(META_RUN_TOPIC) or None
            if account_home is None:
                say("dropped", f".topic dropped: new {control.slug} — no account home holds the topics")
                return meta.get(META_RUN_TOPIC) or None
            path = _mint(account_home, control.slug)
            if path is None:
                say("dropped", f".topic dropped: new {control.slug} — the topic file could not be written")
                return meta.get(META_RUN_TOPIC) or None
            say("advisory", f".topic: minted surface/topics/{control.slug}.md (an empty signature — "
                "it lights on assignments until you write one)")
            slug = control.slug
        if slug is None:
            say("refused", f".topic refused: {control.slug} is not a heddle — write an existing "
                f"slug, or `new {control.slug}` to mint it")
            return meta.get(META_RUN_TOPIC) or None
        previous = meta.get(META_RUN_TOPIC)
        meta[META_RUN_TOPIC] = slug
        event_topic = meta.get(META_EVENT_TOPIC)
        event_id = str(getattr(task, "event_id", "") or "")
        if not event_topic:
            meta[META_EVENT_TOPIC] = slug
            meta.pop(META_UNSET, None)
            stamp_event(inbox_dir, event_id, topic=slug)
            assign(
                account_home, slug, kind="event", ref=event_id,
                run=str(getattr(task, "id", "") or ""),
                thread=str(getattr(task, "conversation_key", "") or ""),
            )
        elif previous and previous != slug:
            say("advisory", f".topic: {previous} → {slug} — acts from here carry {slug}; "
                f"the waking event stays {event_topic} (nothing is reclassified)")
        return slug
    except Exception:  # noqa: BLE001 - a control read must never sink a boundary
        return meta.get(META_RUN_TOPIC) or None


def run_topic(task: Any) -> str | None:
    """The run's own topic as last settled, else the waking event's."""
    meta = getattr(task, "meta", None)
    if not isinstance(meta, dict):
        return None
    return meta.get(META_RUN_TOPIC) or meta.get(META_EVENT_TOPIC) or None


def for_act(
    frontmatter: Mapping[str, Any] | None,
    task: Any,
    *,
    outbox_dir: Path | None,
    account_home: Path | None,
    inbox_dir: Path | None = None,
    notice: Notice | None = None,
    is_strand: bool = False,
) -> str | None:
    """The one topic an outbox act belongs to.

    ``topic: <slug>`` on the act wins when it names a live heddle; a
    ``topic:`` that names none is said once by notice and the act inherits.
    Omitted ⇒ the run's ``.topic`` (settled fresh here, so a control written
    a moment before the act counts), else the waking event's ``topic``, else
    ``None``. A multi-slug value (a 5b ``spawn:`` claim) assigns its first
    live slug.
    """
    say = notice or (lambda _kind, _text: None)
    # The run's `.topic` settles first, whatever the act says: a control
    # written before this act is the boot's answer, and it — not the act —
    # is what confirms or overrides the waking event's proposal.
    settled = settle(
        task, outbox_dir=outbox_dir, account_home=account_home,
        inbox_dir=inbox_dir, notice=notice, is_strand=is_strand,
    )
    raw = str((frontmatter or {}).get("topic") or "").strip()
    if raw and account_home is not None:
        for token in raw.replace(",", " ").replace("·", " ").split():
            slug = heddles.resolve_slug(account_home, token)
            if slug:
                return slug
        say("advisory", f"topic: {raw!r} names no heddle — the act inherits the run's topic")
    if settled:
        return settled
    return run_topic(task)


def mark_unset_on_error(task: Any, outbox_dir: Path | None = None) -> bool:
    """A run ending in ``error`` with no settled topic carries ``topic: None``
    and ``topic_unset: True`` on its manifest. ``True`` when it marked."""
    meta = getattr(task, "meta", None)
    if not isinstance(meta, dict):
        return False
    if meta.get(META_RUN_TOPIC) or meta.get(META_EVENT_TOPIC):
        return False
    if read_control(outbox_dir) is not None and meta.get(META_CONTROL_STAMP):
        # ``null`` written on purpose is a topic decision, not an unset one.
        return False
    meta[META_EVENT_TOPIC] = None
    meta[META_UNSET] = True
    return True


def predecessor_topic_unset(
    brr_dir: Path | None, conversation_key: str, current_run_id: str,
) -> dict[str, str] | None:
    """``{run, event}`` of the previous run on this thread when it ended with
    ``topic_unset`` — the one line the next wake's bundle carries."""
    from . import conversations
    from .run import Run, run_manifest_path

    if brr_dir is None or not conversation_key:
        return None
    try:
        records = conversations.read_records(brr_dir, conversation_key)
    except Exception:  # noqa: BLE001
        return None
    for record in reversed(records):
        if record.get("kind") != "run":
            continue
        run_id = str(record.get("run_id") or "")
        if not run_id or run_id == current_run_id:
            continue
        prior = Run.from_file(run_manifest_path(Path(brr_dir) / "runs", run_id))
        if prior is None:
            return None
        if prior.meta.get(META_UNSET) is True and not prior.meta.get(META_EVENT_TOPIC):
            return {"run": prior.id, "event": prior.event_id}
        return None
    return None


def proposal(
    account_home: Path | None, event: Mapping[str, Any], *, thread: str = "",
) -> tuple[str | None, str]:
    """``(slug, why)`` for an inbound event — a strand's dispatch that carries
    ``topic:`` already is assigned, never proposed."""
    if str(event.get("topic") or "").strip():
        return None, "assigned"
    return heddles.propose(account_home, event.get("body") or "", thread=thread)


# ── The act scope: one staged file's topic, resolved once, read anywhere ──
#
# The drain hands one outbox file to one verb row (and, for ``cut:`` and
# ``land:``, to the rows it falls through to). Several seams inside those
# handlers write an act — ``daemon._stage_outbound`` (every message),
# ``_queue_spawn_request`` (a strand), the bolt, the bench file, the produce
# row — and each must carry the *same* topic. ``outbox.table.dispatch`` opens
# a scope per file; the topic is resolved lazily on first read (a ``note:``
# or ``await:`` never pays for it) and every seam reads it from here, the way
# ``outbox.notices.attributed`` carries the source file to every notice.


class ActScope:
    def __init__(
        self,
        frontmatter: Mapping[str, Any] | None,
        task: Any,
        *,
        outbox_dir: Path | None,
        account_home: Path | None,
        inbox_dir: Path | None,
        notice: Notice | None,
        is_strand: bool,
    ) -> None:
        self.frontmatter = dict(frontmatter or {})
        self.task = task
        self.outbox_dir = outbox_dir
        self.account_home = account_home
        self.inbox_dir = inbox_dir
        self.notice = notice
        self.is_strand = is_strand
        self._resolved = False
        self._topic: str | None = None

    def topic(self) -> str | None:
        if not self._resolved:
            self._resolved = True
            try:
                self._topic = for_act(
                    self.frontmatter, self.task,
                    outbox_dir=self.outbox_dir, account_home=self.account_home,
                    inbox_dir=self.inbox_dir, notice=self.notice,
                    is_strand=self.is_strand,
                )
            except Exception:  # noqa: BLE001
                self._topic = None
        return self._topic


_ACT: contextvars.ContextVar[ActScope | None] = contextvars.ContextVar(
    "brr_run_topic_act", default=None,
)


@contextlib.contextmanager
def acting(scope: ActScope):
    token = _ACT.set(scope)
    try:
        yield scope
    finally:
        _ACT.reset(token)


def current_act() -> ActScope | None:
    return _ACT.get()


def act_topic(task: Any) -> str | None:
    """The topic of the act being written now: the open scope's, else the
    run's (a closeout write outside any drain inherits)."""
    scope = _ACT.get()
    if scope is not None:
        return scope.topic()
    return run_topic(task)


def act_home(account_context: Any = None) -> Path | None:
    scope = _ACT.get()
    if scope is not None and scope.account_home is not None:
        return scope.account_home
    if account_context is None:
        return None
    try:
        from . import account

        return account.context_home_root(account_context)
    except Exception:  # noqa: BLE001
        return None


def confirm_event(
    account_home: Path | None,
    inbox_dir: Path | None,
    event_id: str,
    slug: str | None,
    *,
    run: str = "",
    thread: str = "",
) -> bool:
    """A reply carrying a topic confirms its target event — once. An event
    that already has ``topic:`` keeps it (nothing is reclassified)."""
    from . import protocol

    if not slug or not event_id:
        return False
    path = _event_path(inbox_dir, event_id)
    if path is None:
        return False
    try:
        existing = protocol.parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    if str(existing.get("topic") or "").strip():
        return False
    if not stamp_event(inbox_dir, event_id, topic=slug):
        return False
    return assign(account_home, slug, kind="event", ref=event_id, run=run, thread=thread)
