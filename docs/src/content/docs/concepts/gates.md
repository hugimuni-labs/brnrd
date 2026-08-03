---
title: Gates & authorization
description: Understand how channels reach the daemon and who can trigger work today.
---

A gate is the door between a channel and the daemon on your machine. Telegram,
Slack, GitHub, Signal, and the managed cloud path carry requests in and
replies out. The dashboard is another way to watch and steer the same local
work.

While a run is active, its portals carry the live progress card, interim
replies, follow-up messages, and final handoffs. That makes a long task
observable and correctable instead of silent.

## Authorization today

Authorization happens before enqueue. GitHub, Telegram, and Signal bind it to
a person; Slack still binds it to the configured channel.

| Gate | Who can trigger a run today |
|---|---|
| Managed or self-hosted Telegram | The paired user plus explicitly allowlisted user ids. Other group members and unattributed senders are denied. |
| Self-hosted Slack | Any member of the polled channel. |
| Self-hosted Signal | The paired number plus explicitly allowlisted numbers. Any other sender, and every group message, is denied. |
| GitHub (self-hosted) | Logins with `write`, `maintain`, or `admin` permission, plus explicitly allowlisted logins. Public commenters and read-only users are denied. |
| GitHub (managed) | Addressed comments require GitHub's signed `OWNER`, `MEMBER`, or `COLLABORATOR` author association, or an explicitly allowlisted login. Applying the `brnrd` label is the universal assignment signal. Assigning an issue or pull request to the optional marker account is also a summon, and so is requesting a review from it on a pull request. |

The operating rules follow from that boundary:

- keep GitHub, Telegram, and Signal allowlists narrow;
- remember that a Telegram group does not authorize its whole membership by default;
- use Slack only when every member of the configured channel may drive the daemon;
- set `trust.collaborator_env=solitary` when authorized collaborators should not
  inherit the operator's normal runtime authority;
- remember that every inbound message becomes potential instruction to an
  approval-bypassed coding agent.

See [Connect](../../getting-started/connect/) for setup commands and
[Security & privacy](../../security/) for the full trust posture.

## Signal (self-hosted)

The Signal gate speaks to a locally running
[signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api)
container — a REST wrapper around `signal-cli` — rather than to Signal's own
servers directly. You run that container yourself and either link it as a
secondary device from the Signal app or register a dedicated number for it;
brnrd only ever talks to the local API.

1. Start a signal-cli-rest-api container (`json-rpc` mode is recommended — it
   lets the gate's poll hold open briefly instead of hammering the
   container; `normal`/`native` modes work too, just poll on a fixed
   interval instead).
2. Link or register a number against that container, following
   signal-cli-rest-api's own linking flow.
3. Run `brnrd gate setup signal` in this repo. It asks for the container's
   URL (e.g. `http://127.0.0.1:8080`), the gate's own number, and your
   number as the paired principal — the same default-closed shape Telegram
   uses (`gates/telegram.py`'s `_sender_tier`): the paired number and any
   number added to `.brr/gates/signal.json`'s `allowlist` may trigger a run;
   everyone else is silently denied.
4. Message the gate's number from your paired number to start a run.

**v1 cuts, named plainly:**

- **Direct messages only.** A group message is recognised and skipped
  rather than misrouted to whichever number happens to be in it — this is
  *the Signal direct gate*, not a Signal group gate.
- **No attachments.** An inbound image or file is not downloaded (unlike
  Telegram's photo/document support); a caption-only or text-only message
  still triggers a run.
- **No live progress card.** Telegram and Slack edit a single message in
  place as a run progresses; Signal only gets the final reply for now,
  because message editing isn't part of the request shape this gate uses.
  The interim/final reply stream itself works normally.
- **No interactive menus.** The inline-keyboard menu flow Telegram renders
  has no Signal equivalent yet; a menu-bearing reply still arrives as plain
  numbered text.

## Separate the door from the author

A gate credential owns ingress and replies for that channel. Runner-authored
GitHub produce—branches, pull requests, issue comments—is a separate identity.
Set a dedicated account's token as `GH_TOKEN` in the daemon environment to
make that identity authoritative for runner subprocesses; inherited
`GITHUB_TOKEN` credentials are then withheld from the runner. Use the narrowest
token type and repository permissions GitHub supports for the ownership model.

Git commit attribution is independent of API authentication. Configure
`user.name` and an email verified by the dedicated account if commits should
also appear as authored by it. Never write either token into the repository or
brnrd config. The dedicated account needs Write access to create branches; a
comment-only or Triage collaborator cannot publish the runner's work.

Managed mode keeps the visible summons marker separate from the acting
identity. The installed `brnrd-dev` GitHub App creates a `brnrd` label when it
first discovers a repository; applying that label or mentioning `@brnrd-bot`
summons the resident without granting a second account repository access. The
App receives the signed event and posts replies, branches, and pull requests
with its short-lived installation token.

The `brnrd-bot` user account may additionally occupy GitHub's assignee or
reviewer slots after a repository owner grants it access. That is an optional
GitHub affordance, not the universal route: automating the collaborator
invitation would require giving the App repository **Administration: write**
solely to manage the marker account. brnrd deliberately keeps that broader
permission out of the install contract; the App-native label carries the same
repo-centered intent with the existing Issues permission.

**The two summons verbs need different grants on the marker account**, and the
difference is easy to get wrong:

- **assignment** — the marker must have **Write** permission, or be an
  organization member with Read. GitHub's assignable set is exactly that; a
  Read-only outside collaborator never appears in the assignee picker, and the
  refusal looks like a missing account rather than a missing grant.
- **review request** — **Read** is enough. Anyone with read access to the
  repository can be requested as a reviewer.

So a review request is the cheaper summons: it lets the marker stay a
read-only account. (An earlier revision of this page claimed Read sufficed for
the assignee slot. It does not; corrected 2026-07-29.)
