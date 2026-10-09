# Relay reply target

Status: implementation complete; awaiting parent review. Submission acknowledgment unconfirmed (see below).

Branch: `brr/relay-reply-target`
Base: `brr/loom-step-5-slice-1` (PR #2240)
PR: https://github.com/hugimuni-labs/brnrd/pull/2241

## Result

The shared Telegram parser carries `reply_to_message.message_id` as
`ParsedMessage.reply_to_message_id`. The webhook emits it as the optional
`reply_to.reply_to_message_id`; a non-reply retains its exact prior metadata
shape. The incoming `message_id` stays distinct.

`pull_once` preserves the raw reply target on its durable source fact.
The base already supplied `route_bare(facts, chat, reply_to_letter, now)` and
passed resolved letter ids into it; retained that hook rather than adding a
second abstraction. The passthrough test checks the hook's arguments and
that a reply selects the first thread after another thread spoke last.
Sent-message-id → letter resolution remains slice 2's work.

The server half is additive. It takes effect only on a relay deploy;
this PR does not deploy it or enable the adapter.

## Update shapes used

- Existing `tests/test_brnrd_telegram.py::_message` update: `update_id: 42`,
  `message.message_id: 42`, chat `555`, thread `9`, sender Ada (`user_id: 42`).
- Same update plus `message.reply_to_message` with target `message_id: 17`,
  thread `9`, the same chat/date, a bot sender, and text `s-aaaa-first1 · first`.
  Both variants are posted through the webhook and drained through
  `/v1/daemons/inbox`, asserting the complete metadata map.
- Relay fixture: incoming id `100`, optional target `17`. Raw target remains
  separate from the resolved outbound letter id; ordinary events omit it.
- Parser cases also cover an absent or empty nested reply object.

## Verification

`pytest -q tests/test_telegram_channel.py tests/test_brnrd_telegram.py tests/test_loom_relay.py`
→ **104 passed**, one existing Starlette/httpx deprecation warning.

`git diff --check` passed. Only targeted suites run, per the task contract.

## Unverified

- Live Telegram → deployed relay → loom round trip: requires relay deployment
  and the slice-3 test-chat proof run.
- Sent Telegram message id → outbound letter lookup: deliberately belongs to
  slice 2. Raw-id preservation alone does not change reply routing today.
- Full CI suite: not run; targeted tests were requested.

## Handoff status

PR and branch were verified at `7bbb87b8` after publishing, authored by
`brnrd-bot` and opened through the managed GitHub App (`app/brnrd-dev`).
Two `submit: true` directives were consumed, but this run's portal still
reports `strand.is_strand: false` and `strand.submitted: false`, with no
refusal notice. Parent notification cannot be attested from that state.
Several `brnrd await` leases returned `pending` with no events. The completed
produce is returned through the declared stdout dispatcher channel because
warm submission remains unconfirmed. Nothing was deployed or merged.
