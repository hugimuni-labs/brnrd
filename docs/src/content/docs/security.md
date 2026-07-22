---
title: Security & privacy
description: The current authorization, execution, data-flow, and operator trust boundaries.
---

brnrd runs coding agents with your authority against real repositories. Runner
approval prompts are bypassed on purpose so unattended work can execute. Any
text a connected gate accepts — a chat message, issue body, or review comment —
is potential instruction to that agent.

This is trusted-agent automation with defense in depth, not a sandbox for
hostile tasks.

## Who can trigger work

GitHub and Telegram authorize the individual sender before enqueue. The
self-hosted GitHub gate verifies `write`, `maintain`, or `admin` permission; the
managed webhook requires GitHub's signed `OWNER`, `MEMBER`, or `COLLABORATOR`
association; both also accept explicitly allowlisted logins. Telegram accepts the
paired user plus explicitly allowlisted user ids. Anonymous admins, channel posts,
public commenters, read-only self-hosted GitHub users, and other group members are
denied by default. Slack remains channel-scoped, so every member of its configured
channel can submit work.

Authorization says who may instruct the agent; it does not make their text safe.
Keep principal lists narrow. For people who may submit work but should not inherit
your normal runtime authority, set `trust.collaborator_env=solitary`.

## Execution authority

| Mode | Honest boundary |
|---|---|
| `host` | No isolation; equivalent to running the CLI yourself. |
| `worktree` | Separates the working tree and branch. Shares credentials, network, filesystem, and `.git`; not a security boundary. |
| `docker` | Narrows host-file visibility and can control network. The repo is read-write, model/GitHub/SSH credentials cross in, and network is on by default; not a credential or containment boundary. |
| `solitary` | Provider-only egress, per-run copies of the selected Shell's credentials, and no GitHub credential. The repo is still read-write. |

Ingress carries an `owner`, `collaborator`, or `untrusted` tier. Owners use the
configured environment; collaborators can be tightened with
`trust.collaborator_env`; untrusted or unattributed ingress defaults to
`solitary` and is refused when that environment is unavailable.

## Local stays local — with one honest caveat

Your checkout, `.git`, and run execution stay on your machine. Remote messages
travel through the transport you choose and, in managed mode, transit
brnrd.dev on the way to your daemon.

When you run `brnrd account connect`, the dashboard also mirrors **derived
project knowledge** to brnrd.dev: plans, the decision ledger, run summaries,
pull-request titles and URLs, and quota posture. Your source code does not
leave the machine through that mirror.

## Operator checklist

- Never paste credentials into a task; configure them through the runner or
  gate.
- Scope GitHub credentials as narrowly as your workflow allows.
- Treat every authorized principal as someone who can instruct your agent.
- Use `trust.collaborator_env=solitary` for collaborators who should not inherit
  your normal runtime authority.
- Keep gate state private on disk.

The full threat model and isolation matrix live in the repository's
[SECURITY.md](https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md).
