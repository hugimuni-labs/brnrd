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
  `brnrd account connect`, **dashboard publishing** additionally mirrors seven
  lanes of repo-derived content to brnrd.dev every 3 seconds. Read that as
  continuous mirroring rather than periodic snapshotting: what an agent writes
  into a published lane is on brnrd.dev seconds later. The cadence bounds how
  *stale* the mirror can be; it bounds nothing about review, because there is no
  interval in which you could read what is about to ship and stop it. That is the
  largest outbound surface brnrd has, so it gets its own section below rather than
  a clause here. **No publisher reads your working tree**, so brnrd does not ship
  your checkout or a `git diff` of it. That is a statement about the mechanism,
  and it is narrower than it sounds: the corpus lane mirrors agent-written pages
  *verbatim*, and those pages routinely quote the code the agent was working on —
  measured against a real account, the mirrored run and knowledge pages contained
  fenced `python`, `diff`, `bash`, `toml` and `yaml` blocks, including
  unified-diff fragments of repository test files. **Treat "we don't ship your
  source" as a claim about what brnrd reads, never a guarantee about what leaves.**
  Read the table below before deciding whether that distinction is the one you
  care about. The same caveat applies to diffense: a review pack carries file
  paths, line numbers, and agent prose *about* your code rather than a patch, and
  it transits brnrd.dev in memory only, TTL-bounded, behind an unguessable token,
  and is never persisted.
- **Credential scope.** On the managed path the GitHub token handed to the agent
  is a repository-scoped App installation token (1-hour lifetime). Self-hosted
  setups fall back to whatever you configured — typically a PAT or
  `gh auth token`, whose scope is as broad as you made it; under prompt injection
  a broad credential can act across all your repositories
  ([#415](https://github.com/hugimuni-labs/brnrd/issues/415)). The `solitary` environment
  injects no GitHub credential at all. Gate and daemon tokens are stored 0600
  under `.brr/gates/`.

## What dashboard publishing mirrors

This section applies **only** if you ran `brnrd account connect`. Without it, no
lane below exists. The tables were produced by driving each publisher and
capturing the payload, not by reading the code — where a claim could not be
driven, it says so.

The daemon runs a single publisher thread that walks all seven lanes and then
sleeps, so the lanes are re-published every 3 seconds plus the time the round
trips take. Six of them PUT unconditionally on every pass; the corpus lane — the
largest — PUTs only when its fingerprint has changed, which makes it
change-driven rather than periodic and puts an edited page on brnrd.dev within
one pass of the edit. Each snapshot is a **render cache**, replaced wholesale on
every publish: the repo, dominion, and knowledge repos remain the durable copies,
and disconnecting your last repo deletes the mirror server-side.

| Lane | Endpoint | What it carries | Free text? |
|---|---|---|---|
| Corpus | `PUT /v1/daemons/surface` | Whole Markdown pages, one record each (`path`, `layer`, `markdown`, `truncated`). Three layers: **authored** (your work surface and plan pages), **knowledge** (every kb page), **runs** (per-run `body.md`, `state.md`, and `messages/*.md` — the full text of what was said to the agent and what it replied). Files over 256 KB are cut and flagged `truncated`. | **Yes — whole pages, in full, including any code they quote.** The single largest lane. |
| Run ledger | `PUT /v1/daemons/run-ledger` | Up to 256 closed-run receipt rows: run/event ids, timestamps, wall-clock, Shell+Core, token counts, quota deltas, cost attribution, plus `external_refs` — commit shas and **subjects**, branch names, PR numbers, report **file paths on your machine**, and a free-prose `summary` the agent wrote about the run. | **Yes** — `summary`, commit subjects, branch names, local paths. |
| Live runs | `PUT /v1/daemons/live-runs` | One row per running thought: ids, stream, repo label, timestamps, parent/subspawn shape, Shell+Core, phase, relic counts, mood handle, and **`card_text`** — the live progress-card note the agent is writing right now. | **Yes** — `card_text`, `name`, `label`. |
| Activity | `PUT /v1/daemons/activity` | Pending/running tasks, scheduled entries, and parked respawns: id, kind, source, conversation key, status, phase, branch, PR number, timestamps, and `summary`. | **Conditionally** — see the cloud-gate rule below. |
| Quota | `PUT /v1/daemons/quota` | Per-Shell quota windows and trailing burn; **real billing figures** (spent/limit amounts and currency) and reset labels that carry **your timezone**; plus gate health rows carrying `last_error` — the **raw error string** from the last failed gate poll, unfiltered. | **Yes** — `last_error`, spend summaries. |
| PR review queue | `PUT /v1/daemons/pr-review-queue` | Open PRs across the repos in your account: number, **title**, URL, repo label, author login, created-at, draft flag. Collected by shelling out to `gh pr list`. | **Yes** — PR titles. |
| Runners | `PUT /v1/daemons/runners` | Your locally-discovered Shell+Core catalog: profile names, models, provider, class, cost rank, availability, staleness, plus which environments are usable and why not. No repo content — but it is a **fingerprint of your machine's tooling**: which agent CLIs are installed and authenticated, and whether Docker is present. | No. |

**The activity lane's cloud-gate rule holds — and covers only that lane.** Driven
with two records side by side: a task from a locally-gated thread
(`source: telegram`) published its bare event id, and a task from a thread the
backend already carries (`source: cloud`) published a 140-character body excerpt.
Self-scheduled entries publish the entry id, never the scheduled task text.

**The live-runs lane applies no such rule.** `card_text`, `name` and `label` are
published for every active run regardless of which gate it came from, so a
Telegram- or GitHub-triggered run's progress note is mirrored even though the same
run's activity-lane `summary` is withheld. If you rely on the cloud-gate bound,
rely on it for the activity lane only.

**Two lanes are bidirectional.** The `runners` and `live-runs` responses carry
dashboard-issued wake requests and run-stop requests back to your daemon. They
are how the dashboard's "wake this runner" and "stop this run" controls reach
you, not just how it displays them.

**`.mood` narration stays local.** Only the first line — the emote handle — rides
the presence entry. Anything you write below it is never read by the publisher.

### Bounds you control

`publish.layers` in `.brr/config` names **what may be mirrored at all**. Absent, it
mirrors everything. Otherwise, only what you name ships, and anything you do not
name is off:

| Value | Effect |
|---|---|
| _(unset)_ | All seven lanes. This is the default. |
| `none` | **Nothing publishes.** All seven lanes stop — no snapshot is even collected. |
| `authored` / `knowledge` / `runs` | The corpus lane, carrying only the slices you name. |
| `corpus` | The corpus lane, all three slices. |
| `runners`, `live_runs`, `activity`, `quota`, `pr_review_queue`, `run_ledger` | That lane. |

Values combine with commas (`publish.layers=authored,quota`). `none` wins over
anything named beside it. A value matching no lane mirrors **nothing** and prints
a warning naming the token — a typo here fails closed, not open.

`publish.runs_window_days` (default 14) bounds the corpus lane's **runs** layer
and nothing else: run nodes older than the window are trimmed from the mirror at
the next publish, and `0` drops that layer entirely. Driven with a year-old run in
the fixture, it was correctly excluded from the corpus.

The corpus lane's other two layers have **no age bound at all**: every page in the
authored surface and the knowledge base ships, however old, subject only to the
256 KB per-file cap.

The other two run-shaped lanes are bounded differently, and neither by age:

- The **run ledger** ships its most recent 256 rows regardless of date. Driven
  with 301 rows, 256 shipped and the oldest carried a 2025 timestamp.
- The **activity** lane is bounded by run *status*, not age — it publishes tasks
  that are pending or running. A manifest left in `running` keeps publishing
  indefinitely.

So the 14-day figure is the retention for run *bodies and message text*. It is not
the retention for run receipts, their free-prose summaries, or a stuck task.

Turning a lane off stops collection, not merely transmission: with
`publish.layers=none` the daemon does not build a snapshot and does not shell out
to `gh` for the PR queue.

One caveat, stated because it bears on how much weight this switch carries:
`publish.layers` lives in `.brr/config`, which is not one of the daemon-owned
security keys and is writable by anything with local write access to the checkout
— including the agent itself. It bounds what brnrd publishes; it is not a control
an untrusted run cannot reach.

### What this switch does not turn off

`publish.layers` governs the **dashboard mirror**. A connected daemon still talks
to brnrd.dev for the relay job you connected it for, on paths this switch does not
touch: inbox polling (`/v1/daemons/inbox`), reply delivery
(`/v1/daemons/responses`), progress-card updates for conversations that originated
at brnrd.dev (`/v1/daemons/card`), review-pack relay (`/v1/daemons/pack`), config-change
proposals, and pairing/registration. If you want no traffic at all, disconnect —
do not rely on `publish.layers`.

### Provenance, and what was not measured

The seven lanes and their fields above were captured from live publisher
invocations against a populated daemon state. The retention claims in the
*Credentials & data flow* bullet that concern the **backend's own** storage —
inbound bodies nulled after reply, never-answered bodies nulled at 14 days, event
rows pruned at 90, and the in-memory-only handling of review packs — are
server-side behaviours that were **not** re-driven here, and are stated on the
strength of the backend implementation rather than a measured payload.

One interaction worth stating plainly, because the two halves are true separately
and misleading together: the queue nulls an inbound message body after the reply
is delivered, **and** the corpus lane's `runs` layer mirrors that same message
text as part of the run node for the length of `publish.runs_window_days`. The
window, not the null-after-reply, is the effective retention for message text on a
cloud-connected daemon.

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
