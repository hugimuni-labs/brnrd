# brr vs GitHub Agentic Workflows (gh-aw) — deep comparison

**Date.** 2026-04-23
**Sources.** Local clone at `.local/gh-aw` (v0.68.x era, Apr 2026), `github.github.com/gh-aw/` docs mirrored in `docs/src/content/docs/`, `README.md`,
`.github/aw/github-agentic-workflows.md` (the canonical schema reference),
pattern docs (`chat-ops`, `multi-repo-ops`, `central-repo-ops`, …), plus web
research on community reception, maturity and adjacent projects.

The user asked this question with a specific use-case in mind: **a remotely
controlled, repo-first, agentic CLI runner**. Everything below is weighted
against that target.

---

## 1 · one-sentence positioning

| | brr | gh-aw |
|---|---|---|
| What it is | A Python-stdlib playbook generator plus an on-box daemon that converts file-protocol events into local AI-CLI invocations against your repo | A `gh` CLI extension that compiles markdown+frontmatter workflow files into GitHub Actions `.lock.yml` files, running ephemeral coding agents inside GHA runners |
| Where the agent runs | **on your machine** (local / worktree / future docker / ssh / kube) | **inside a GitHub Actions runner** (GitHub-hosted or customer self-hosted GHA runner) |
| Who triggers it | **anything that writes a file** — Telegram, Slack, `git push`, a bash script, a webhook transformed to a file event | **GitHub events only** — `issues`, `pull_request`, `issue_comment`, `slash_command`, `label_command`, `schedule`, `workflow_dispatch`, `push`, … |
| Ownership | self-hosted by design; user owns `~/.config/brr/` (optionally a git clone of a user-owned repo); zero cloud | GitHub-native by design; you do **not** opt out of GitHub as the scheduler, storage layer, identity boundary, or billing unit |
| State of the art | private project, pre-1.0, no release cadence; shaping up around the "fleet & steering" design | GitHub Next technical preview since 2026-02-13, CLI `v0.68.x` as of mid-April, measurable internal success-rate audits, GPL removal, SBOM, rate-limiting in flight |

They look superficially similar because both:

- ship a **markdown playbook** as the source of truth,
- run **AI coding agents** backed by CLIs like Claude / Codex / Gemini / Copilot,
- care about **security, durability (commit+push), and repo-first operation**.

That similarity is where the rhyme ends — their center of gravity is opposite.

---

## 2 · side-by-side architectural map

```
brr                                           gh-aw
---                                           -----
AGENTS.md + kb/            (playbook, repo)   .github/workflows/*.md     (playbook, repo)
  ↓                                              ↓ gh aw compile
  (read live by AI CLI)                       .github/workflows/*.lock.yml   (generated GHA YAML)
                                                 ↓ GitHub Actions
.brr/inbox/<event>.md      (file protocol)    GitHub event (issue, PR,
  ↑                                           comment, slash_command, …)
  │ gate: telegram/slack/git/anything            ↓ activation job (skip-if, roles,
  ↓                                              rate-limit, stale-check)
.brr daemon                (Python, on-box)   agent job          (GHA runner, ephemeral,
  ↓ subprocess                                  firewalled, MCP Gateway, safe-outputs)
AI CLI (claude/codex/                            ↓
        gemini)                                safe-outputs job  (validated writes via
  ↓ git commit, push                             GitHub API: create-issue, add-comment,
.brr/responses/<event>.md                        create-pr, …)
  ↓ gate delivers answer
Telegram/Slack/git push
```

That diagram says everything. They share the "markdown playbook → coding agent
over MCP → durable git outcome" spine. They disagree on the **runtime
substrate** and on the **transport for human intent**.

---

## 3 · axis-by-axis opposition

### 3.1 · execution substrate

- **brr:** on the machine that holds the working copy. Minimal isolation
  today (local or `git worktree`), actively moving to an `Env` plugin
  interface (`prepare / invoke / finalize`) with built-ins for `docker`,
  `ssh`, and an entry-point group (`brr.envs`) for `kube` and friends.
- **gh-aw:** inside a GHA runner. Ephemeral by definition. No story for
  running the *same workflow* on a box you own outside of configuring a GHA
  self-hosted runner — and even then the control plane is still GHA.

**Implication for your use case.** If "repo-first" means *the agent sees the
real filesystem of the actual repo on the actual host you control*, gh-aw
literally cannot do it; it always runs in a fresh checkout on a transient
runner. That's not a bug — it's the product.

### 3.2 · how the human's request gets in

- **brr:** a gate writes a file to `.brr/inbox/`. That's the entire
  protocol. Telegram, Slack, a cron script, a Raspberry Pi button, a
  self-hosted Gitea webhook converted to a file by a 20-line shell adapter —
  all first-class. The daemon doesn't know or care which.
- **gh-aw:** GitHub is the transport *and* the identity boundary. The rich
  trigger surface (events, slash_command, label_command, schedule,
  workflow_dispatch, skip-if-match, skip-if-no-match, skip-if-check-failing,
  rate-limit, manual-approval, roles, skip-bots, bot allowlist, …) is all
  phrased in GitHub terms and only runs when GitHub fires the event.

For the stated use case — **remotely controlled from Telegram**, from your
phone, from `git push`, from a workflow you own end-to-end — brr's gate
abstraction is the point. gh-aw's only Telegram story is "someone on Telegram
files a GitHub issue for you" plus a hand-rolled MCP server.

### 3.3 · durability contract

They agree on the shape, they differ on the plumbing.

- **brr:** the durability contract is *commit + push + `.brr/responses/<event>.md`*.
  Env layer makes this explicit (see `kb/deck-brr-fleet-steering.md` §
  "durability contract"): every non-`local` env is ephemeral, so only git
  refs + the response file survive.
- **gh-aw:** the durability contract is *git refs via `safe-outputs`*.
  `safe-outputs.create-issue`, `add-comment`, `create-pull-request`,
  `update-issue`, `add-labels`, `create-discussion`, `create-agent-session`,
  `update-release` — all structured, permissioned, validated. The agent has
  **read-only** permissions; writes happen in a downstream `output` job that
  talks to the GitHub API under sanitized inputs.

The gh-aw safe-outputs system is the **one idea brr might meaningfully
steal** (see § 8).

### 3.4 · playbook model

- **brr:** `AGENTS.md` (universal, works with any AI CLI that reads it:
  Claude Code, Cursor, Codex, Copilot CLI, Gemini CLI) + `kb/` persistent
  knowledge base. Zero compile step. No YAML DSL. Override chain resolved at
  runtime (`.brr/prompts/` → repo, future: overlays at `~/.config/brr/`).
- **gh-aw:** one markdown file per workflow with a *large* YAML frontmatter
  schema (hundreds of fields — triggers, permissions, tools, MCP config,
  safe-outputs, features, imports, runs-on, concurrency, rate-limit, …).
  The file is **compiled** to `.lock.yml` via `gh aw compile`; both files
  are committed.

brr chose "no compile, universal playbook". gh-aw chose "heavy declarative
compile step with strong static validation, actionlint/zizmor/poutine scans,
SHA-pinning, hash-verified frontmatter". Those are philosophically opposite
decisions, not different implementations of the same thing.

### 3.5 · security posture

gh-aw ships a genuinely impressive defense-in-depth stack because it
**must**: it runs agents on event payloads from anyone who can comment on
your issues. The stack:

- read-only default permissions, writes *only* via `safe-outputs`
- AWF (Agent Workflow Firewall) — network egress allow-list sidecar
- MCPG (MCP Gateway) — centralised MCP-call routing
- XPIA (cross-prompt-injection) system prompt on by default
- input sanitization (`steps.sanitized.outputs.text`)
- compile-time validation, actionlint+shellcheck+zizmor+poutine scanners
- SHA-pinned action refs (`action-mode: release`)
- integrity reactions (👍 promotes content, 👎 demotes)
- skip-roles / skip-bots / roles / rate-limit / manual-approval
- `stop-after` deadlines and per-workflow concurrency discriminators

brr's threat model is smaller: **you are the principal**. The messages
arrive via a gate you authenticated yourself, the runner executes on your
host under your user, and the output is your commit. That's why brr's
security surface is essentially "use the runner CLI's own sandbox, isolate
per-task via worktree or container, never commit secrets". One story is not
better than the other — they suit different threat models.

### 3.6 · multi-repo / fleet

- **brr:** the planned `brnrd` registry + broadcaster (`fleet.toml` with
  repo paths / tags / profiles; `brnrd all --profile=personal run "<task>"`)
  is a thin broadcast layer *above* per-repo brrs. Overlays at
  `~/.config/brr/profiles/<name>/` — optionally a user-owned git clone —
  steer all repos at once with one `git push`.
- **gh-aw:** `imports:` pulls shared workflow fragments from
  `owner/repo/path@ref`. `MultiRepoOps` pattern adds `target-repo` to safe
  outputs. Cross-repo auth via PAT or GitHub App installation tokens.
  "Central repo ops" — one repo dispatches work out to many — is a first-
  class pattern.

Both acknowledge the problem. brr solves it as *one edit in my config repo
→ N working copies converge* (filesystem / git-pull rollout). gh-aw solves
it as *one shared workflow imported by N repos at compile time, with
cross-repo tokens and safe-outputs*. brr is stronger when the repos are on
different providers (GitHub / GitLab / Gitea / filesystem). gh-aw is
stronger when everything is inside one GitHub org.

### 3.7 · runner portability

- **brr:** runner is whatever AI CLI exists on `$PATH`. Built-in profiles
  for `claude`, `codex`, `gemini`. Anything that respects `AGENTS.md`
  works. Swapping models = editing `.brr/config`.
- **gh-aw:** `engine: copilot | claude | codex | custom` is a YAML field;
  swapping is one line but you're still inside GHA. Authentication is
  GitHub-centric (`COPILOT_GITHUB_TOKEN`, Anthropic via GitHub App, etc.).
  The April 2026 audits show auth misconfig is the #1 failure mode.

### 3.8 · zero runtime dependencies

- **brr:** hard constraint. stdlib only, pip-installable, MIT. Runs on any
  Python 3.10+.
- **gh-aw:** Go binary as a `gh` extension, plus an astro/vite docs site,
  npm/js for scripts, a Go test suite, and the companion services AWF and
  MCPG. Installing it is a `curl | bash` or `gh extension install github/gh-aw`,
  but *running* it is GitHub Actions + whatever companions your policy needs.

---

## 4 · market fit

### 4.1 · who buys gh-aw

- **Primary.** GitHub-first orgs (enterprise, mid-market SaaS) that already
  live in GHA and want "Continuous AI" on issues/PRs — triage, labelling,
  review, doc-updater, release notes, dependency upgrades, scheduled
  research. Signal: the Feb 2026 technical-preview changelog entry, `@github`
  using its own tool in-repo, Peli's Agent Factory samples.
- **Budget line.** Engineering platform team / DevEx / "enablement". Paid
  for via GHA minutes + Copilot / Claude / Codex API usage, with audit and
  compliance happening in GitHub's existing enterprise tooling.
- **Sell.** "Natural-language GitHub Actions, with the same security bar
  your CI already has."

gh-aw has real traction *inside the Microsoft/GitHub orbit* and is clearly
on the roadmap toward GA. It's reasonable to expect it to become the default
way orgs do repository-scoped "Continuous AI" within 18 months.

### 4.2 · who buys brr

- **Primary.** An individual (or a couple) maintaining several repos —
  some on GitHub, some on GitLab, some on a self-hosted Gitea, some
  completely off-SCM — who wants to *kick off* agentic work from Telegram
  / Slack / phone / SSH and have it land on a box they own. The "fleet
  & steering" deck's end-to-end demo (`git push` to overlay repo → `brnrd
  all run` → 3 repos converge) is that user's pitch.
- **Budget line.** Your hobby time. Possibly a small consulting team that
  values self-hosted ideology and cross-SCM portability.
- **Sell.** "I own the whole pipeline, it runs on my hardware, it's a
  single pip install, it steers every repo I have from one markdown file,
  and I talk to it from Telegram."

The market for brr is *much smaller* in absolute terms than for gh-aw —
but it is **structurally un-addressable by gh-aw** because gh-aw requires
GitHub as its substrate. The "self-hosted agentic operator on a box I own"
niche is what projects like OpenClaw / Clawdbot / Trigger.dev / n8n-plus-
Claude-Code are all reaching for. brr's differentiators in that niche:

1. **zero runtime deps** (Python stdlib) — beats the Docker-compose-heavy
   alternatives on install friction,
2. **gate-as-filesystem** — adding a new transport is 20 lines of shell,
3. **playbook-first** — the deliverable is `AGENTS.md + kb/` which works
   even when you throw brr away, giving every user a no-lock-in on-ramp.

### 4.3 · are they competitors?

**No, mostly.** They occupy adjacent but non-overlapping niches:

| use-case | winner |
|---|---|
| "A GitHub issue opens → agent triages it" | **gh-aw** (brr could do it with a webhook→file bridge but gh-aw is purpose-built) |
| "I Telegram my phone 'review the last PR on the gitea repo on my NAS'" | **brr** (gh-aw cannot; it needs GitHub) |
| "Nightly agentic dependency upgrade across an entire GitHub org" | **gh-aw** (`schedule:` + `MultiRepoOps`) |
| "One edit to a prompt file propagates to 10 personal repos across GitHub / GitLab / self-hosted" | **brr** (overlay-as-git-clone + `brnrd`) |
| "Slash-command `/deploy staging` in a PR comment triggers an agent" | **gh-aw** (first-class `slash_command`) |
| "`git push` to any repo triggers a local agent to regenerate derived artefacts before I open the PR" | **brr** (`git` gate + local env) |
| "Untrusted contributors can safely trigger agentic actions with firewalled network, sanitised inputs, integrity reactions" | **gh-aw** (entire stack is purpose-built for this) |
| "I work offline on a plane, `brr up` still answers Telegram queued while I was offline once I reconnect" | **brr** (files buffer, daemon drains; gh-aw requires GitHub reachable) |

The only *real* contention zone is "team on a private GitHub repo who wants
agent-on-PR". There, gh-aw wins on polish, security, and integration depth
— but costs you GHA minutes, the compile step, and the GitHub-shaped worldview.

---

## 5 · assessment against your stated use case

> *"remotely controlled repo-first agentic CLI runner"*

Decomposing that phrase:

- **remotely controlled.** gh-aw is *event-controlled* from GitHub. You
  control it by doing something on GitHub (comment, label, dispatch). That's
  not the same as controlling it from your phone's Telegram while you're on
  a train. If "remote" means "not sitting at this terminal", gh-aw *kind of*
  qualifies; if it means "not at a computer at all, not necessarily on
  GitHub.com", gh-aw does not.
- **repo-first.** Both qualify. But gh-aw is repo-first **as a scheduling
  boundary**; brr is repo-first **as an execution boundary** (the agent
  literally runs in your working copy).
- **agentic.** Both qualify, same engines under the hood.
- **CLI runner.** brr shells out to the AI CLI you already have; gh-aw
  embeds the AI engine inside its lock-yml harness. If the *CLI* part is
  load-bearing — i.e. you want the same `claude` or `codex` binary that
  you use interactively, reading the same `AGENTS.md`, writing to the same
  git index — brr is the literal match.

### verdict for your use case

- **gh-aw is not a substitute for brr.** It is substrate-mismatched and
  transport-mismatched for what you described. Adopting it would mean
  re-tooling around GitHub events and GHA runners, losing Telegram / Slack
  / git / self-hosted repos as first-class citizens, and accepting a
  declarative compile step.
- **gh-aw is a plausible complement.** If some of your repos are on GitHub
  and you'd like "continuous AI" on those *specifically* (nightly triage,
  PR auto-label, doc updater), run gh-aw **on those repos alongside** brr,
  with each tool doing what it does best. The overlap is small enough that
  the two can coexist without stepping on each other; they would merely
  commit to the same branches occasionally.
- **gh-aw has ideas worth cloning.** See § 8.

---

## 6 · what gh-aw does well (and how it compares)

### 6.1 · structured frontmatter

Hundreds of well-documented fields, schema-validated, with dedicated
scanners. Every trigger / permission / MCP server / safe-output is typed.
Huge authoring ergonomics win once you're past the initial learning cliff.

brr equivalent today: `.brr/config` (flat key-value) + event fields. Much
simpler, much less expressive. Closer to brr's thesis — but there's a real
ergonomic gap if your workflows grow past trivial.

### 6.2 · safe-outputs

The single most portable idea in gh-aw. Instead of "the agent has write
perms", you say:

```yaml
safe-outputs:
  create-issue: { max: 1, labels: [needs-triage] }
  add-comment: { max: 3 }
  create-pull-request:
```

The agent has read-only tokens; a downstream job validates its structured
output and calls the real API. This generalises cleanly across providers
(GitHub → GitLab → Gitea → Telegram-reply → email) and is independently
useful. Adopting a subset of this in brr would reduce the attack surface
for "agent pushes directly to main" mistakes.

### 6.3 · skip-if-* preconditions

`skip-if-match`, `skip-if-no-match`, `skip-if-check-failing` all express
"don't run me unless X" declaratively. brr's triage prompt (`prompts/triage.md`)
does something similar in natural language; the declarative version is
cheaper, more predictable, and doesn't burn tokens.

### 6.4 · `imports:` and shared workflows

Compile-time include from `owner/repo/path@ref`. Good for shared sub-agents
("the docs-writer") across an org. brr's overlay-as-git-clone reaches the
same end via live filesystem lookup — no compile step, but also no per-
workflow granularity. For personal use, the overlay wins on simplicity.

### 6.5 · rate-limit / stop-after / manual-approval

`rate-limit: { max: 5, window: 60, ignored-roles: [admin] }` is a feature
brr will eventually want as the number of gates grows. Today a malicious or
misconfigured Telegram webhook could spam `.brr/inbox/`.

### 6.6 · compiled artefact = audit trail

The `.lock.yml` is a committed, hash-stable, diff-able record of exactly
what will run. That's legitimately valuable for review and compliance.
brr's closest equivalent is the trace system (`.brr/traces/<kind>/...`),
which is per-invocation rather than per-workflow.

---

## 7 · what brr does that gh-aw structurally cannot

1. **Non-GitHub repos.** GitLab, Gitea, Codeberg, SourceHut, private SSH
   remotes. gh-aw has no concept here; brr treats them identically.
2. **Human-initiated remote control over chat.** Telegram, Slack, XMPP,
   Matrix, SMS — anything that can write a file is a gate. gh-aw requires
   the human to be *in GitHub* (comment, label, dispatch).
3. **On-box stateful execution.** The agent shares your actual working
   copy, your actual caches (`.cache/`, `node_modules/`, poetry envs),
   your actual IDE state if you want it to. gh-aw's ephemeral runner is
   cold every time.
4. **Works offline.** Gate writes file, daemon processes it when online
   again. gh-aw requires GitHub reachable at trigger time.
5. **Zero-cloud deployment.** Everything lives under `$HOME` / a VPS you own.
6. **Fleet steering across heterogeneous providers.** `brnrd` registry is
   provider-agnostic; `overlay_sync=auto` over a user-owned git repo
   propagates prompt/config edits to every repo you track, regardless of
   where those repos live.
7. **Agent-decided branching / env / needs_context.** The triage step lets
   an agent ask for more info, pick a branch strategy, or escalate to a
   container — all on-box, all observable, all cheap. gh-aw's equivalent
   is workflow composition (different workflows for different outcomes),
   which costs a compile-edit-commit cycle.

---

## 8 · what brr could credibly adopt from gh-aw

Ordered by value-per-LOC:

1. **`safe-outputs`-style structured writes** *(high value, low cost)*. Add
   a small layer over the runner where the agent emits structured intents
   (`{op: "comment", channel: "telegram", body: "…"}`, `{op: "commit",
   message: "…", files: [...]}`) and the daemon validates and dispatches
   them. Makes gates symmetrical (request → structured response) and
   unlocks provider-agnostic "create issue" / "open PR" primitives on top
   of whichever SCM CLI is present (`gh`, `glab`, `tea`).
2. **`rate-limit` and `stop-after`** *(medium value, small LOC)*. Per-gate
   message rate limits and task deadlines prevent runaway loops.
3. **Declarative `skip-if-*` preconditions in the Task spec** *(medium
   value)*. Agent-decided triage is elegant but overkill for "don't re-run
   if there's already an open task for this issue".
4. **XPIA system prompt on by default** *(low cost, meaningful hardening)*.
   Even in single-principal mode, Telegram messages can include untrusted
   text (forwarded content, webhooks). A tiny appended system prompt on
   every runner invocation is free insurance.
5. **Trace/audit shape parity with `.lock.yml`** *(medium value)*. brr
   already has `.brr/traces/`; consider adding a per-task rendered
   "effective prompt" snapshot committed alongside the response so the
   audit trail matches gh-aw's compiled-workflow story.
6. **Scanners for committed prompts** *(optional, later)*. actionlint /
   zizmor analogues for the `AGENTS.md`-ecosystem don't exist yet; if brr
   ships a `brr lint` that checks prompt files for common smells it would
   cost ~100 LOC and give a real differentiator.

Do **not** adopt:

- **the compile step.** It fights brr's zero-ceremony thesis and gh-aw's
  own user feedback already cites confusion about when to recompile.
- **frontmatter as a giant DSL.** Gh-aw's schema is a symptom of being
  locked to GHA's YAML model. brr can keep config flat + prompt-driven.
- **AWF / MCPG as coupled components.** They're the right answer for
  untrusted multi-principal workloads and the wrong answer for "me and my
  box". Document the interfaces (network allowlist, MCP gateway) as things
  you *could* plug in if your env (docker, kube) wants them.
- **hard coupling to any SCM.** gh-aw chose GitHub — that's the whole
  game. brr must stay provider-agnostic.

---

## 9 · risk to brr from gh-aw getting popular

- **Low to moderate.** gh-aw will soak up the GitHub-hosted-team market.
  That's a market brr was never going to win (no realistic path to
  out-integrating GitHub *on GitHub*). If anything, gh-aw's existence
  **validates the playbook-plus-agent thesis** and normalises
  `AGENTS.md`-style markdown as the contract — brr gets to free-ride that
  normalisation.
- **What brr should do.** Keep leaning hard into the *axes gh-aw structurally
  cedes*: self-hosted, cross-provider, gate-pluggable, CLI-first, offline-
  tolerant, fleet-across-providers. The "fleet & steering" direction you
  locked in (`brnrd`, overlay-as-git-clone, Env protocol) is exactly right
  — do not pivot toward compiled YAML workflows.

---

## 10 · concrete recommendation

1. **Do not adopt gh-aw** as the engine for brr's stated use case. The
   substrate (GHA runner) and transport (GitHub events) are the wrong
   shape for "remote control from Telegram, on a box you own, across
   repos on multiple SCMs". You would be giving up the three properties
   that make brr worth building.
2. **Do consider running gh-aw alongside brr on GitHub-hosted repos
   where it makes sense.** Think of gh-aw as your "continuous AI on
   GitHub events" layer and brr as your "remote-controlled agentic
   operator on your box" layer. They write to the same git index; they
   don't compete for it because their triggers are disjoint.
3. **Do steal the safe-outputs pattern, rate-limit / stop-after, and an
   XPIA nudge.** Those are portable wins independent of the rest of gh-aw.
4. **Do *not* adopt the compile step, the frontmatter DSL, or the
   GitHub-shaped worldview.** Those would dilute brr's thesis.
5. **Do watch gh-aw's `imports:` evolution.** If it matures into a clean
   sharing primitive, the design might inform how overlay profiles compose
   in brr v-next.

If you had to sell brr in one line against gh-aw, after reading both code-
bases: *"gh-aw is GitHub's answer to continuous AI inside GitHub. brr is
the self-hoster's answer to remote-controlled AI across your whole fleet,
GitHub or not."* Those are not the same product, and you would lose more
than you'd gain by collapsing them.

---

## references

- `./local/gh-aw/README.md`
- `./local/gh-aw/create.md`, `./local/gh-aw/install.md`
- `./local/gh-aw/.github/aw/github-agentic-workflows.md` (canonical schema, 2404 lines)
- `./local/gh-aw/docs/src/content/docs/introduction/{overview,how-they-work}.mdx`
- `./local/gh-aw/docs/src/content/docs/patterns/{chat-ops,multi-repo-ops}.md`
- GitHub Changelog, *"GitHub Agentic Workflows are now in technical preview"*, 2026-02-13
- `github/gh-aw` discussions #20558, #23173, #27292, #27432 (internal audits, 2026-03 → 2026-04)
- `kb/deck-brr-current.md`, `kb/deck-brr-fleet-steering.md`, `kb/design-env-interface.md`, `kb/notes-pondering-fleet.md`
- `awesome-ai-agents-2026` for adjacent-project framing (Trigger.dev, OpenClaw, n8n)
