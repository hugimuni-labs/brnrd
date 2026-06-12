# Conversation continuity — the dialogue-faithful wake

Status: active. The conversation-tail fix and the playbook seed ship
together as this page's companion change; the cross-gate keying and
multi-maintainer attribution sections are analysis of deferred work, not
shipped behaviour.

Peer: [`decision-drop-streams.md`](decision-drop-streams.md) (conversations
are routing + history, not identity) and the bundled
[conversations doc](../src/brr/docs/conversations.md).

## The incident

A resident wake answered the wrong question. The user asked about
**git union-merge** behaviour (a follow-up in an ongoing Telegram thread);
the agent replied as if the topic were *message routing across projects*.
Nothing was destroyed — the work it did was salvageable — but a short
follow-up landed in the wrong key entirely. Two shapes compounded to make
that misread likely, and they are independent: one is the conversation
**tail** the wake renders, the other is how the **thread** itself is keyed.

## Root cause A — a kind-blind tail evicted the dialogue

The conversation log interleaves two very different kinds of record:
*messages* (the user's events and the agent's own replies) and *lifecycle*
rows (task status, per-phase updates, heartbeats, artifacts). The agent's
wake rendered a flat "last N records" tail, and lifecycle records vastly
outnumber messages — a single long run emits a heartbeat every
`_HEARTBEAT_INTERVAL` (30s), so a 15-minute thought writes ~30 heartbeat
updates plus phase and task rows. The old render cap was 8 records and the
daemon read a window of ~20; a single chatty run buried every prior message
under its own lifecycle noise.

Two aggravators sat underneath:

- **Agent replies weren't dialogue.** Reply artifacts (`response`,
  `interim_response`, `outbound_message`) recorded only a file *path*, not
  the reply text. Even when a reply row survived the cap, it carried no
  content — so the agent couldn't see what it had just told the user.
- **Event summaries were first-line-only.** A multi-line user message kept
  only its first line as the summary, dropping the part a later follow-up
  often refers to.

With the thread's own messages evicted, the wake fell back to the one
narrative that's always injected: `kb/log.md`'s *Recent Activity*. But that
is the **repo-wide, cross-thread** through-line — useful for orientation,
wrong as the referent for a thread-local "it". A union-merge follow-up read
against another thread's routing work is exactly the failure that produces.

## Root cause B — the thread was split across two gates

The same human, in what felt like one conversation, reached the resident
through **two different gate paths**: a brnrd-hosted gate (the `cloud` gate)
and a self-hosted Telegram bot (a second bot account). Conversation keys are
derived per gate-thread by `gate_thread_key`
([`conversations.py`](../src/brr/conversations.py)):

- self-hosted Telegram → `telegram:{chat}:{topic}`
- brnrd-hosted same chat → `cloud:telegram:{chat}:{topic}`

So the two paths produce **different keys** and therefore different
conversation logs. A follow-up sent to one bot cannot see the other bot's
turns — the histories never thread together, even though the user
experiences a single line of conversation. This is the routing half of the
misread, and it is the multi-gate case that
[`decision-drop-streams.md`](decision-drop-streams.md) deliberately deferred
("a line of work that spans multiple conversations or multiple gates").

## What ships now — a dialogue-faithful tail (root cause A)

A shared renderer, `conversations.render_conversation_tail`, that both the
daemon prompt ([`prompts.py`](../src/brr/prompts.py)) and the per-task
run-context file ([`run_context.py`](../src/brr/run_context.py)) delegate
to, so the two surfaces can't drift:

- **Messages get their own block.** User turns and agent replies are
  selected and floored *independently* (up to `RECENT_MESSAGES_MAX` of
  each), merged chronologically, and rendered as "user (source): …" /
  "you: …". A flood of one kind can no longer evict the other.
- **Lifecycle is demoted** to a compact secondary block (`RECENT_LIFECYCLE_MAX`
  rows) for operational orientation — present, but unable to crowd out the
  conversation.
- **Replies carry their text.** The three reply-artifact sites now store a
  whitespace-collapsed `summary` of the reply body, so the agent sees its
  own side of the dialogue. Legacy path-only rows render as nothing rather
  than a blank "you:".
- **Event summaries keep the whole message** (whitespace-collapsed, length-
  bounded via `summarize_text`), not just the first line.
- **The read window is generous** (`RECENT_READ_WINDOW`, 400) so the recent
  message turns survive even when hundreds of lifecycle rows sit between
  them in the raw stream.

## What ships now — a playbook seed (the using half)

A mechanically faithful tail still has to be *used* well. The shipped seed
[`dominion-playbook.md`](../src/brr/prompts/dominion-playbook.md) gains a
"Reading the wake well" section that names the discrimination the incident
turned on: the **conversation tail is this thread's back-and-forth**;
`kb/log.md`'s Recent Activity is the cross-thread through-line; a terse
follow-up's referent lives in the thread, not the log; threads don't bleed
just because they share a user; and a tail that looks like it's missing a
turn the user assumes you saw is a signal to *say so*, not to invent an
antecedent from the log. The same section carries the run-to-run half the
user asked for: notice how this wake's shape differs from a comparable one,
and keep a logbook/snapshot of actions and environment so "how was it before,
how is it now" is answerable rather than guessed. The mechanical fix makes
the dialogue visible; the seed says where to place trust when it is.

## Continuity: how the discussed problem changes

The chat framed two candidate fixes — a richer auto-injected context, or a
playbook nudge — and asked whether a fix was even necessary. It is, and the
two are complementary, not alternatives. Root cause A is a real context-shape
bug: no amount of guidance rescues a wake whose tail has been flattened to
heartbeats. Fixing the tail restores the raw material; the seed governs how
the agent reads it. Together they move the resident from "reconstruct the
conversation from a noisy cap and lean on the shared log when it's thin" to
"the thread is in front of you; the log is orientation."

The **run-difference / context-diff** idea (auto-inject how this wake's
context changed since the last comparable one) is a genuine future shape, but
it belongs to the host, not this change. Until a host hands it over, the seed
makes it the agent's own durable move — snapshot the input, log the actions —
which is both cheaper and host-agnostic. (See the environment-shaping loop in
[`design-environment-shaping.md`](design-environment-shaping.md) for the
observe → remember → shape framing this rides on.)

## Deferred: unifying the cross-gate thread (root cause B)

Not fixed here — the user flagged it as "worth looking at later," and it
wants the actual incident events to confirm the mechanism before a shape is
chosen. It is the same problem brnrd already owns as **cross-gate
continuity** ([`subject-managed-mode.md`](subject-managed-mode.md): "metadata
graph + on-demand gate-history fetch") and that
[`plan-conversation-id-propagation.md`](plan-conversation-id-propagation.md)
threads identity for. Candidate shapes, from least to most machinery:

1. **Explicit key aliasing.** Let a `cloud:telegram:{chat}` event and a
   native `telegram:{chat}` event resolve to one conversation key when they
   demonstrably share an origin chat. `conversation_key_for_event` already
   honours an explicit `conversation_key`; the open question is who is
   authoritative about "same origin" — the gate that knows the platform IDs.
2. **brnrd identity graph.** The hosted side already plans a metadata-only
   graph; cross-gate grouping for the *same user across hosted + self-hosted*
   is a natural extension, kept data-minimal (no contents).
3. **Leave them separate; lean on the through-line.** Accept two threads and
   rely on `kb/log.md` for the cross-thread story. Cheapest, and now safer
   because root cause A stops the log from being *mis*used as a thread
   referent — but it does not give a short follow-up the other bot's turns.

Recommendation: confirm the mechanism against the real events first
(`brr agent inject` on the incident), then prefer (1) for the self-hosted
pair and fold the hosted pair into brnrd's existing cross-gate plan rather
than inventing a parallel one.

## How multi-maintainer changes this

A future small-team tier (deferred in the pricing work) reframes both halves.
Threads keyed by *gate-thread* rather than *human* age well here: two
maintainers in two chats are already separate conversations, no change
needed. What stops aging well is everything **shared**:

- **The tail's "who" needs to load-bear.** Message rows already carry
  `source`; with several humans on one project, the rendered "user (source)"
  should distinguish *which* maintainer, and "you:" replies may want
  run/owner attribution so a wake can tell its own prior turn from a
  sibling thought's. The dialogue-faithful renderer is the right seam to
  add that to — it already separates messages from lifecycle.
- **The shared layers become cross-maintainer.** `kb/log.md` and the single
  `brr-home` dominion are already multi-writer (the society-of-mind framing
  in [`design-agent-dominion.md`](design-agent-dominion.md) anticipates many
  concurrent thoughts on one memory). A team makes per-entry authorship in
  the log matter more, and makes "don't let threads bleed" a guardrail about
  *people*, not just gates.

No code for this ships now; it is recorded so the renderer and key shapes are
chosen with the team case in view rather than retrofitted.
