"""The connect-time greeting — #1244 fork 2.

`brnrd account connect` used to end at pairing. On an uninitialized repo
(``AGENTS.md`` missing) that used to be the whole story: nothing ever told
the resident there was a repo to set up, even once the daemon started
booting, pairing, and polling fine with no ``AGENTS.md`` (#1261 fork 1).
This module is the plumbing fork 2 asked for — connect ends by queueing a
first-wake event, minted through the same :func:`protocol.create_event`
machinery every other inbox event uses (schedule fires, spawn completions):
no parallel queue. What the resident does with that wake — which questions,
in what order — is its own judgement at runtime; the reused
``init-playbook.md`` is a prior handed to it (see
:func:`prompts.build_connect_greeting_task`), not a script this module
enforces.

**Why the event's ``source`` is a real gate name, not a synthetic
``"connect"``.** Delivery *ownership* is keyed on the literal event
``source`` string (``daemon._gate_owns_source``): an unowned source (the
``schedule`` / ``spawn`` shape) only ever reaches a human through the
once-only, end-of-run ``_resolve_notify_gate`` fallback — which cannot
carry a real back-and-forth interview, since every *interim* reply in
between would queue for a gate that is never watching that thread and
simply never send. So the queued event is stamped with the literal name of
the gate that will carry it, plus that gate's own addressing meta (a
Telegram chat id, a Slack channel, …) — indistinguishable in shape from an
ordinary inbound message on that channel, except its body is code-composed
rather than typed by the human. The account's ``cloud`` relay is
deliberately excluded as a target: its API is reply-shaped
(``gates.cloud.addressed`` requires a ``cloud_event_id`` to answer, and
there is nothing yet to answer at connect time), so it cannot originate a
first message — see :func:`door_for_greeting`.

**Why the event also stamps ``trust_tier="owner"``.** ``trust.resolve_tier``
fails closed: any ingress-gate source (``telegram``, ``slack``, …) with no
stamped tier resolves to ``untrusted``, because a real gate always stamps
the sender's actual authorization and an unstamped ingress event is
unattributed by construction. This event never went through a gate's own
inbound authorization check — it is a local, already-privileged CLI action
(the repo owner running ``brnrd account connect`` themselves) synthesized
to *look like* an inbound message only for delivery's sake — so it must
stamp the one tier it can honestly claim, or the wake it dispatches would
be silently refused or downgraded as a stranger's message on the owner's
own repo. See :func:`queue_greeting`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import prompts, protocol
from .gates import runtime as gate_runtime

#: Doors that can *originate* a message (a bound default recipient) rather
#: than only reply to one already inbound. ``cloud`` and ``github`` are
#: deliberately absent — see the module docstring for ``cloud``; ``github``
#: addresses a repo issue/PR, not a person, which is not "a door that can
#: say hello". An account paired only to ``cloud``, with no direct chat
#: gate configured, has no usable door yet; :func:`door_for_greeting`
#: returns ``None`` rather than guessing one.
_GREETING_GATE_PREFERENCE: tuple[str, ...] = ("telegram", "slack", "signal")

#: Tags a queued greeting event so a second `connect` (re-pairing after a
#: token rotation, fixing a URL, …) never stacks a duplicate one behind it
#: — the same idempotency pairing itself already has.
GREETING_META_KEY = "connect_greeting"


@dataclass
class GreetingOutcome:
    queued: bool
    event_id: str | None = None
    door: str | None = None
    reason: str | None = None


def door_for_greeting(brr_dir: Path) -> tuple[str, dict[str, object]] | None:
    """The first configured gate that can originate this message, plus the
    addressing meta its own delivery loop needs to reach the same recipient
    it would already fall back to for an unaddressed send. ``None`` when no
    configured gate qualifies.
    """
    configured = set(gate_runtime.configured_gates(brr_dir))
    for name in _GREETING_GATE_PREFERENCE:
        if name not in configured:
            continue
        state = gate_runtime.load_state(brr_dir, name)
        if name == "telegram":
            chat_id = state.get("chat_id") or state.get("last_chat_id")
            if chat_id:
                return "telegram", {"telegram_chat_id": int(chat_id)}
        elif name == "slack":
            channel = state.get("channel")
            if channel:
                return "slack", {"slack_channel": str(channel)}
        elif name == "signal":
            sender = state.get("paired_sender") or state.get("last_recipient")
            if sender:
                return "signal", {"signal_sender": str(sender)}
    return None


def _facts(repo_root: Path) -> dict[str, Any]:
    """A trimmed ``init_wake.collect_facts`` — repo/gh/gate facts only.

    Runner and shell detection are dropped: unlike the terminal init wake,
    this event dispatches through the *normal* daemon lifecycle, which
    already hands every run its own Runner catalog and Mode block —
    restating a subset of that here would drift from, or duplicate, the
    standard surface rather than add anything.
    """
    from . import init_wake

    facts = init_wake.collect_facts(repo_root, runner_name="")
    for key in ("runner_name", "detected_runners", "detected_shells", "missing_shells"):
        facts.pop(key, None)
    return facts


def queue_greeting(repo_root: Path, brr_dir: Path) -> GreetingOutcome:
    """Queue the first-wake greeting event, or say why not.

    Never queues once ``AGENTS.md`` exists, never queues a second time
    while one is already pending, and never queues onto a door that cannot
    originate a message (see :func:`door_for_greeting`).
    """
    if (repo_root / "AGENTS.md").exists():
        return GreetingOutcome(queued=False, reason="AGENTS.md already exists")
    if not prompts.init_playbook_available(repo_root):
        # Same guard `init_wake.wake_path_available` uses for the terminal
        # wake: a brnrd built with the bundled playbook removed (or emptied
        # by a per-repo override) must not dispatch a task-less wake.
        return GreetingOutcome(
            queued=False, reason="the init playbook prompt is not installed",
        )

    inbox_dir = brr_dir / "inbox"
    for event in protocol.list_pending(inbox_dir):
        if event.get(GREETING_META_KEY):
            return GreetingOutcome(
                queued=False,
                reason="a greeting is already pending",
                event_id=str(event.get("id") or "") or None,
            )

    resolved = door_for_greeting(brr_dir)
    if resolved is None:
        return GreetingOutcome(
            queued=False,
            reason=(
                "no configured door can originate a message yet (telegram/"
                "slack/signal) — the cloud connection alone can't start a "
                "conversation, only reply to one already inbound"
            ),
        )
    door, address_meta = resolved

    task = prompts.build_connect_greeting_task(repo_root, facts=_facts(repo_root))
    event_path = protocol.create_event(
        inbox_dir,
        door,
        task,
        # `trust.resolve_tier` fails closed for any ingress-gate source with
        # no stamped tier (untrusted) — correct for a real inbound message,
        # wrong here: this event was never inbound at all, it is a local,
        # already-privileged CLI action (the repo owner ran `brnrd account
        # connect` themselves) synthesized to look like one only for
        # delivery purposes. A real gate always stamps the sender's actual
        # authorization; this stands in for that stamp with the one tier a
        # code-composed connect-time event can honestly claim.
        trust_tier="owner",
        **address_meta,
        **{GREETING_META_KEY: True},
    )
    return GreetingOutcome(queued=True, event_id=event_path.stem, door=door)
