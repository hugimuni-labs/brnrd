# Security & privacy

brnrd runs coding agents with your authority against real repositories. Runner
approval prompts are bypassed on purpose so unattended work can execute. Any
text a connected gate accepts — a chat message, issue body, or review comment —
is potential instruction to that agent.

This is trusted-agent automation with defense in depth, not a sandbox for
hostile tasks.

## Who can trigger work

Authorization currently keys on a channel or trigger, not each sender.
GitHub mentions on a connected repo can be used by any commenter; a bound chat
can be used by any member of that room. Per-commenter GitHub authorization is
tracked in [#408](https://github.com/Gurio/brr/issues/408), and per-sender chat
authorization in [#409](https://github.com/Gurio/brr/issues/409).

Until those release blockers land, use private repositories only and prefer
managed one-to-one Telegram. Do not connect a public-repo gate or trust a group
chat with your daemon.

## Execution authority

| Mode | Honest boundary |
|---|---|
| `host` | No isolation; equivalent to running the CLI yourself. |
| `worktree` | Separates the working tree and branch. Shares credentials, network, filesystem, and `.git`; not a security boundary. |
| `docker` | Narrows host-file visibility and can control network. The repo is read-write, model/GitHub/SSH credentials cross in, and network is on by default; not a credential or containment boundary. |

The configured environment does not currently change with the trust level of
the incoming source.

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
- Treat every gate as a door into your shell.
- Use `docker.network=none` when a task does not need network access.
- Keep gate state private on disk.

The full threat model and isolation matrix live in the repository's
[SECURITY.md](https://github.com/Gurio/brr/blob/main/SECURITY.md).
