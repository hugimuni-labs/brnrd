# Trust & execution model

brnrd runs coding agents that execute commands and edit files on your machine —
with your authority, against your real repository. This document describes that
architecture: what each gate does, what each execution environment actually
isolates, where data crosses the network, and which controls are enforced today
versus still tracked as work.

It reflects the code as it ships (alpha). Where the design's intent is not yet
matched by an enforced control, the gap is named and linked to a tracking issue.

## The model in one paragraph

brnrd runs coding agents that execute commands with **your** authority against your
**real** repository, using your real credentials and network. The runners are
launched with their approval prompts deliberately bypassed
(`claude --dangerously-skip-permissions` or `codex exec --dangerously-bypass-…`).
The base assumption is that **whoever can reach a configured gate
has been authorized to instruct the agent**. GitHub and Telegram authorize the
individual sender before enqueue; Slack still authorizes at channel membership.
Everything below is defense-in-depth over that base. None of it is a cage for a
hostile agent or a hostile message. Authorizing a person gives their text a path to
your runner, so grant that right as carefully as shell access.

## The trusted-agent base

- Agent authority equals your shell's authority in the repository directory.
- **Any text brnrd ingests is potential instruction.** Issue bodies, PR and review
  comments, and chat messages flow into the agent's prompt verbatim. Prompt
  injection is therefore in scope and is the primary control path, not a corner
  case. It is mitigated by *who you let trigger runs* and *how much authority the
  environment grants* — not by sandboxing the agent.
- The mitigation strategy is **trust-tiering the ingress and the environment**, plus
  operator hygiene. GitHub and Telegram stamp an authorized principal's tier on the
  event; unattributed ingress fails closed to `untrusted`, which runs in `solitary`
  when available or is refused. Slack remains channel-authorized; see the gaps below.

## Who can trigger a run

Authorization is checked before an event reaches the runner. The policy differs by
gate, so the configured principal or channel is the important boundary.

| Gate | Who can trigger a run today | Notes |
|---|---|---|
| Telegram (self-hosted & managed) | The paired user, plus explicitly allowlisted user ids. | Default-closed per sender. Anonymous admins, channel posts, and unattributed senders are denied; a group is safe by default because membership alone grants nothing. |
| Slack (self-hosted) | Any member of the configured channel. | Channel-scoped; no per-sender allowlist yet. |
| GitHub (self-hosted) | Logins with `write`, `maintain`, or `admin` permission, plus explicitly allowlisted logins. | Permission is verified through GitHub before enqueue. Public commenters and read-only users are denied even when they use the configured trigger. |
| GitHub (managed) | GitHub's signed `OWNER`, `MEMBER`, or `COLLABORATOR` author association, plus explicitly allowlisted logins. | Default-closed per webhook author; the signed payload is the authorization source. |

Passing authorization does not make inbound text benign. A compromised collaborator
or an intentionally hostile instruction can still drive an approval-bypassed agent.
Keep the principal lists narrow and use a tighter collaborator environment when the
sender should not inherit the operator's normal runtime authority.

## Execution environments — honest isolation matrix

Every project picks an execution environment. Here is what each one actually
isolates, and what it does not:

| Dimension | `host` | `worktree` | `docker` (as shipped) |
|---|---|---|---|
| **Repo access** | Writes hit your live working tree. | Separate worktree + `brr/<run-id>` branch; main checkout untouched. Shares the `.git` object store. | Same worktree isolation, but the repo is bind-mounted **read-write** at the host path — the agent's writes are real host writes. |
| **Credentials** | Full inherited environment and `~/.*`. | Same as host. | **More** surface, not less: model keys forwarded, `~/.claude` `~/.codex` `~/.gemini` `~/.gitconfig` `~/.ssh` bind-mounted **read-write**, `GITHUB_TOKEN` injected. |
| **Network** | Full host network. | Full host network. | Control only — default is full egress; `docker.network=none` is opt-in. |
| **Host filesystem** | Full — the agent can read `~/.aws`, `/etc`, sibling repos. | Full — same user, only the working directory differs. | **Genuinely narrower** — only the repo and mounted credential dirs are visible. This is docker's one real security-positive property. |
| **Process** | Subprocess of the daemon, same UID. | Same. | PID/mount namespaces; runs as host UID. Not a privilege boundary. |

**The honest one-line claim per environment:**

- **`host`** — no isolation; the same trust boundary as running the CLI yourself.
- **`worktree`** — keeps runs off your working tree on their own branch. Shares your
  `.git`, credentials, network, and filesystem. **Not a security boundary.**
- **`docker`** — dependency and network isolation, and it narrows which host files
  the agent can see to the repo plus mounted credential dirs. It is **not** a
  credential or containment boundary: the repo is mounted read-write, your
  credentials cross in, and the network is on by default. Assumes a trusted agent.
  See [#80](https://github.com/hugimuni-labs/brnrd/issues/80).
- **`solitary`** — the hardened preset (`environment=solitary`, one value): egress
  locked to the run's model provider through an allowlisting proxy sidecar (a
  literal `network=none` would brick every cloud runner — the model call itself is
  network), per-run *copies* of only the selected Shell's credentials (host CLI
  state can't be modified from inside; `.ssh` never mounted), and no GitHub
  credential at all — "no push from inside" holds structurally; the daemon
  publishes the branch from the host after the run. What it cannot close: content
  shown to the model provider (the conversation is a channel), and the repo mount
  stays read-write pending [#80](https://github.com/hugimuni-labs/brnrd/issues/80)'s
  `isolation=clone`. Details: `brnrd docs envs`.

The environment is chosen by **the trust tier of the event source** ([#524](https://github.com/hugimuni-labs/brnrd/pull/524)):
owner-authored events run in the configured environment; unattributed or untrusted
sources fail closed to `solitary` or are refused outright, and a lower tier can
never escalate the environment a higher tier configured.

## Credentials & data flow

- **Where data crosses the network.** Inbound: a gate poll or a managed webhook.
  Outbound: the gate reply, a `git push` to your forge, an overflow reply posted to
  your own GitHub **secret** gist (unlisted URL — anyone with the link can read it),
  and — for cloud-connected daemons — dashboard/plan relay to brnrd.dev.
- **What stays local.** Your checkout, `.git`, run execution, responses, traces, the
  knowledge base, and the dominion. `.brr/` is gitignored.
- **What managed mode sees.** The inbound message body (nulled after the reply is
  delivered; never-answered bodies are nulled after 14 days and event rows pruned
  after 90 — the queue is a relay, not an archive), and durable routing metadata
  (sender id/username, chat id, repo name, comment URL). If you run
  `brnrd account connect`, dashboard publishing also mirrors a **bounded render
  cache** of the account corpus to brnrd.dev: the authored work surface, knowledge
  pages, and run nodes from the last 14 days — run nodes include full run bodies
  and message text for that window, so "derived summaries only" would be an
  understatement; the repo, dominion, and knowledge repos remain the durable
  copies. Bounds are yours to tighten: `publish.runs_window_days` resizes the run
  window and `publish.layers` opts the mirror down to fewer layers (or `none`).
  Task-body excerpts in the activity lane are published only for threads the
  backend already carries (cloud-gated); locally-gated traffic reports status
  metadata without text. Disconnecting your last repo purges the mirror. Your
  source code does not leave the machine. Diffense review packs transit brnrd.dev
  in memory only, TTL-bounded, behind an unguessable token, and are never
  persisted.
- **Credential scope.** On the managed path the GitHub token handed to the agent
  is a repository-scoped App installation token (1-hour lifetime). Self-hosted
  setups fall back to whatever you configured — typically a PAT or
  `gh auth token`, whose scope is as broad as you made it; under prompt injection
  a broad credential can act across all your repositories
  ([#415](https://github.com/hugimuni-labs/brnrd/issues/415)). The `solitary` environment
  injects no GitHub credential at all. Gate and daemon tokens are stored 0600
  under `.brr/gates/`.

## Managed backend

The managed backend (brnrd.dev) is comparatively well-behaved: webhook signatures
are verified, stored tokens are hashed, device-flow pairing is used, message bodies
are nulled after reply, and **all code executes locally on your daemon** — the
backend relays, it does not run your agent.

## Hardening checklist for operators

- Prefer **private** repositories over public ones for GitHub gates.
- Prefer the managed one-to-one Telegram path. A group remains default-closed to the
  paired user; add other user ids only when they should be able to drive the daemon.
- For authorized collaborators, set **`trust.collaborator_env=solitary`** — the
  one-value preset composing provider-only egress, per-run credential copies, and no
  GitHub credential. Unattributed/untrusted ingress already defaults to `solitary`
  (or refusal when it is unavailable).
- Scope the GitHub token you give the agent; prefer a repo-scoped App token over a
  broad PAT where possible.
- `chmod 0600` your `.brr/gates/*.json`.
- Never paste credentials into a task; configure them through the runner or gate.
- Be aware that the agent can write to the knowledge base and dominion, which you may
  push to a remote — avoid persisting secrets there.

## Known gaps being tracked

| Gap | Severity | Tracking / mitigation |
|---|---|---|
| Untrusted text → approval-bypassed agent with operator authority (umbrella) | Critical | via [#23](https://github.com/hugimuni-labs/brnrd/issues/23) |
| Slack authorizes the configured channel, not individual senders | High | Use only with a channel whose full membership may drive the daemon |
| Docker is not a credential/containment boundary | High | [#80](https://github.com/hugimuni-labs/brnrd/issues/80) |
| Full-scope GitHub token handed to the agent | High | [#415](https://github.com/hugimuni-labs/brnrd/issues/415) — managed path is a repo-scoped App token since [#498](https://github.com/hugimuni-labs/brnrd/pull/498)/[#520](https://github.com/hugimuni-labs/brnrd/pull/520); the self-hosted fallback chain remains |

## Found a gap?

If you spot a hole in any of the above, raise it privately rather than in a public
issue. Report it to **security@hugimuni.fr**.
