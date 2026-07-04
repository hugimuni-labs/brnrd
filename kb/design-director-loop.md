# Design: the director loop — game pacing as product thesis

Status: active — design round opened 2026-07-01 from the maintainer's
five-part voice dump (evt-4mzl…evt-4nzp); scrutinised and mapped this wake.
Companion brand/naming exploration: [`design-brand-brnrd-brr.md`](design-brand-brnrd-brr.md).

## The thesis (compressed from the notes)

Terminal games are more fun than terminal engineering because games are
*designed*: they throw a meaningful decision at you at intervals, execute
hidden work, reveal progress, and hand you the next decision. Agentic coding
has the execution but not the design — agents either wait for commands,
over-explain, or run too far without a progression loop. The missing layer is
a **director**: something that decides when the user needs a decision, when
the agent continues silently, and when a result is revealed. The loop:

> meaningful choice → hidden execution → reveal → new state → next choice

with progression carried on the normal collaboration surfaces (issues, PRs,
commits, messages), not badges or XP. The external LLM's sharpest line holds
up: **this is a product thesis, not a UX garnish** — "a directed co-op
workflow" is a different category from "agent runner infrastructure."

## Verdict up front

The diagnosis is right and most of the machine already exists here under
other names. The dangerous parts are all in the *execution shape*: three
specific crashes to avoid, then a four-phase path where phase 1 costs zero
code. The single most important architectural call: **the director is a
stance the resident holds plus two small mechanical seams — not a daemon
component.**

## Where the crash is (scrutiny)

**1. Manufactured choice.** The notes' own run-end contract ("every agent run
should output options A–D") collides with a standing product guardrail:
`run.md` → *take the reversible calls yourself; hand genuine forks to the
user with options weighed*. A contract that **requires** options at every run
end manufactures forks that aren't there. The user learns within a week that
the options are filler, stops reading them, and now the product nags — the
exact "fake dopamine garbage" the notes reject, wearing a quest log's
clothes. Good games don't quiz you every 30 seconds either; between real
decisions there is flow. **Resolution:** the run-end contract must have
"nothing to decide — continuing / done" as its *most common* value. Options
appear only at genuine forks and arc boundaries. Pacing is the product;
choice frequency is not.

**2. The Director as daemon infrastructure.** The daemon is deterministic
machinery: it schedules, routes, injects, captures. It cannot rank moves or
judge when a reveal is due — that's model work. A "Director component" in
`daemon.py` would need its own LLM calls, its own budget, its own failure
modes, and would duplicate the resident. **Resolution:** the director is the
*resident* operating under a pacing contract (prompt layer), with the daemon
contributing only what it already knows how to do: inject state, park
continuations, render cards, fire scheduled wakes.

**3. Ambient cost.** A director that "watches project state and user
attention" continuously is a spend multiplier — the exact failure
`design-self-scheduled-thoughts.md` already names ("a thought that wakes for
nothing is friction you pay every cycle"). **Resolution:** event-driven
first. The loop advances when runs end and when the user speaks. A periodic
director tick is an *opt-in* `schedule.md` entry, and its body must include
its own silence condition ("message only if the ranking changed").

Positioning risk (game vocabulary vs B2B) is real but is a brand question —
handled in [`design-brand-brnrd-brr.md`](design-brand-brnrd-brr.md).

## What already exists (the mapping)

The notes were written without knowledge of the architecture; the striking
thing is how much of the director already shipped under other names:

| Game-loop element | Existing machinery |
| --- | --- |
| Decision point, "choose / override / delegate" | Genuine-fork surfacing (`run.md` reconsider contract); PLAN→approve parked portal; `runner_policy: propose` approval loop |
| Hidden execution | Single-flight daemon runs; worktrees; subagents |
| Reveal moments | Progress card narration (`.card`); mid-thought outbox replies; diffense review pack; `gate: forge` PR handoff |
| Quest log / ranked standing moves | **Inter-run plan home** — decided in [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md) §4: repo-tagged plans in the account dominion repo, daemon-injected between wakes, card-linked |
| Progression surfaces | Issues, PRs, commits, `kb/log.md`, the activity dashboard |
| "Keep the run open for follow-ups" | runner-owned `.keepalive` + `inbox.json` folding at plan boundaries; daemon-owned `delivered · attending` floor after current-thread delivery |
| Director's own clock | `schedule.md` self-scheduled thoughts |

So the thesis is not a new subsystem; it is a **completion criterion** for
things already half-built, plus a naming of the feel they should add up to.
What's genuinely missing is three things.

## The three real gaps

**Gap 1 — run-end next-move contract.** Today a run ends in free prose. The
loop wants a structured closeout: state (`done | continuing | blocked |
needs-choice | ready-for-review`), what changed, and — *only when real* — 2–4
options with a recommendation and a reason. This is where "what should we do
next?" stops being a blank page.

**Gap 2 — the standing move ranking.** The inter-run plan home is decided
but not built. The director loop is what it's *for*: a resident-curated,
ranked list of the likeliest valuable moves per repo, injected every wake,
rendered on the dashboard and card. Rank + refresh discipline is the delta
over the already-decided plan file.

**Gap 3 — pacing policy.** When to reveal, when to ask, when to shut up and
work. Pure prompt layer. The reveal moments are already enumerated (after
discovery, after decision, after diff, after tests, after PR); the contract
just has to say that reveals happen *at those seams and not continuously*.

## Implementation plan (sequenced so a lesser model can execute)

Each phase is independently shippable and reversible. Phase 1 needs no code.

**Phase 1 — pacing + closeout as prompt contract (no code).**
Add a short "The next move" section to the portals doc
(`src/brr/docs/portals.md`) and a sentence to the delivery-contract text in
`src/brr/prompts.py`: an addressed run's final reply ends with one of
(a) *done — receipt*, (b) *continuing — what's next and when to expect it*,
(c) *blocked — what's needed*, or (d) *a genuine fork: 2–4 numbered options,
a recommendation, one-line reason*. Explicitly: most runs end (a)–(c);
manufacturing (d) is the named failure mode. Acceptance: prose only, user
replies in prose, existing conversation threading routes it. This ships the
*feel* immediately.

**Phase 2 — parse the closeout (small code).**
An optional fenced block in the final stdout (or outbox frontmatter key
`next:`) carrying `state / options / recommended`. Daemon parses it into the
run record, renders options as a numbered list in the delivered reply, and
prepends the chosen option text when a short follow-up reply ("2", "B") is
matched to a pending option set on the same conversation key. Touch points:
`prompts.py` (contract text), `daemon.py` closeout path (parse + stash),
gate delivery rendering, `docs/portals.md`, tests mirroring the existing
outbox-frontmatter tests. No new process, no new store — it rides the run
record. ~1–2 focused wakes.

**Phase 3 — the quest log (already-decided work, now with a purpose).**
Execute the inter-run plan home per
[`decision-account-centered-daemon.md`](decision-account-centered-daemon.md)
§4 / implementation note 4, with the ranking discipline added: the file is a
ranked move list (`plans/<repo>.md` in the account dominion repo), the
resident re-ranks it at closeout when the run changed the picture, the
daemon injects the top of it each wake and links it from the card. Dashboard
renders it. This is the always-on surface that replaces "modal choice
spam" — the user *glances* at standing moves instead of being interrupted.

**Phase 4 — director tick (opt-in, existing machinery only).**
A `schedule.md` entry whose body is the director stance: "re-rank the move
list from repo/forge state; message the gate only if the top move changed or
something is newly blocked." No daemon feature at all — this is exactly the
ambient-initiative pattern `design-self-scheduled-thoughts.md` designed,
with the silence condition as the brake.

## The orchestrator/worker question (brnrd spawns brrs)

The notes also sketch a two-tier execution shape: a stingy, unhurried
orchestrator (brnrd) that holds the conversation and spawns focused workers
(brrs) for tedious bounded work, picking cores by task complexity. Scrutiny:

- **The rails exist.** Cheap answer-or-respawn dispatcher, `respawn: true`
  with `shell:`/`core:`/`quality: escalate`, in-run subagents, the
  cost-ranked runner catalog. Nothing new is needed to *behave* this way.
- **A mandatory tier split is premature** — the maintainer's own fence is
  right. Always-two-hops means latency on every trivial exchange, double
  context assembly, and a new failure surface, paid before models are fast
  enough to hide it.
- **The shape that works today:** delegation as *resident policy*, not
  process architecture. The resident keeps user-interfacing, commits, and
  judgement; it spawns subagents/respawns for bounded tedium; the stingy
  behaviours (grep before read, count lines before opening, keep the run
  open for follow-ups) are prompt-level and largely already present. When a
  worker wake is spawned, it gets the slim stack: task + files + structured
  result; no dominion write, no scheduling, no kb governance.
- **Revisit trigger:** when a strong-class core's time-to-first-token and
  cost make the orchestrator hop invisible, promote the split from policy to
  default. That's a model-economics date, not a design blocker.

This reframing also answers "how is a brr mechanically different from
brnrd": same rails, different injected stack — which is already how
subagents work. The naming half lives in the brand page (resolved
2026-07-02: `brr` stays retired as a name; the split is essence, not
vocabulary).

### Re-justifying the split (maintainer fork, 2026-07-04)

B3/B4 shipped the worker/resident split leaning on a pollution-risk framing
("worktrees mean it's unlikely to pollute your space"). The maintainer
pushed back correctly: dominion and kb are both git-versioned, worktrees
already isolate concurrent file mutation, and a diverged dominion/kb merges
mechanically like any other branch. Pollution was never the real risk the
split was guarding against — re-examined, the actual justification is two
things unrelated to data safety:

- **Judgment scope, not merge conflicts.** A worker wake has no continuity
  — it reads no recent-log, holds no pitfalls, won't be there to defend or
  revisit a call next week. Git can merge two divergent kb edits; it cannot
  merge two divergent *editorial judgments* about what's worth keeping in
  shared standing memory. That accountability gap, not file contention, is
  why a worker doesn't get kb governance or scheduling authority — a dozen
  bounded workers each free to schedule wakes or rewrite kb pages is a
  governance problem no worktree fixes.
- **Cost.** The resident stack (identity core, dominion, playbook, plans,
  policy, ledger, pitfalls, kb health, introspection) is real injected
  tokens on every wake. A worker doing "read this file, fix this bug,
  return a diff" pays for all of it and uses none of it. That overhead is
  waste, not caution.

Net: the split stands, on sharper ground than it shipped with. Not because
isolation is scarce (it isn't — worktrees + git already cover that) but
because standing-memory judgment and full-stack cost are real and orthogonal
to isolation.

**Respawn vs in-run subagents — not the same capability, don't unify.**
The maintainer also asked whether `respawn:` is still earning its keep, and
whether spawning should be unified into one mechanism now that "cheap
dispatcher escalates to a stronger core" is no longer the load-bearing
architecture (`decision-account-centered-daemon.md` §3 keeps that dispatcher
narrow — unpinned message events only, not the general spawn path). That
framing of `respawn:` is stale and worth retiring explicitly, but the
mechanism itself is not redundant with in-run subagents (the `Agent` tool):
a subagent is in-process, same Shell, supervised live in this conversation;
`respawn:` parks a brand-new top-level daemon event that can move to a
**different Shell entirely** (Codex ⇄ Claude), a different repo, or simply
outlive this run's return. An in-harness subagent structurally cannot do
any of that — it has no path to a different provider's CLI. So: keep both,
reframe `respawn:`'s stated purpose from "dispatcher escalation" to
"cross-runner / cross-repo / outlives-this-run handoff," and keep
`worker: true` as the orthogonal stack-weight dial it already is (B4) —
applies regardless of *why* the handoff happened, not tied to an escalation
story.

## Hot-idle residency and quota-aware pacing (maintainer, 2026-07-02)

Follow-up sharpening the stingy-director economics: if the wake already
spawned in a strong core, downshifting mid-conversation buys nothing — the
paid asset is the assembled context. The proposal: a wake that, instead of
terminating, idles hot (`while n < 100: sleep 30; check portal; act if
input; n++`) — near-free residency because the conversation is already
paid for — plus proactive loops paced by *observed* quota/allowance data
rather than fixed intervals.

Scrutiny, held against the current machinery:

- **The cache economics are real but have a 5-minute cliff.** Provider
  prompt caches (~5m TTL) make a 30s poll loop genuinely cheap: each
  iteration pays only new tokens. Past the TTL, every iteration re-reads
  the full context uncached. So hot-idle is a *short-horizon* instrument —
  minutes, not hours — exactly matching the maintainer's own "it should
  occasionally terminate" caveat (context drift, cost accumulation).
- **The slot is the scarcer resource.** Under single-flight, a hot-idle
  wake occupies the run slot; a queued unrelated event waits behind a loop
  that is mostly sleeping. Hot-idle should yield when `portal-state.json`
  shows unrelated pending work — the fold-in contract already reads at
  plan boundaries; an idle loop must too.
- **Quota visibility exists as data.** `claude_usage` / `claude_status` /
  `codex_status` already extract shell-reported usage and limits. What's
  missing is the *policy seam*: feeding those data points into wake pacing
  (schedule intervals, proactive-loop budgets, core selection) instead of
  only into runner availability. That is the concrete follow-up — a
  consumption-aware input to `schedule.md` cadence and respawn class,
  tied to the co-maintainer workstream's standing-loop idea.
- **Partially built already:** `.keepalive` extends a run past budget, and
  the daemon re-invokes on tracked completions; what does not exist is a
  sanctioned in-run idle-poll pattern. If adopted, it should be a named
  contract (max iterations, TTL-aware sleep step, yield-on-unrelated-work)
  rather than each wake improvising a `while` loop.

Direction: agree in principle as a *short* post-delivery linger (catch the
follow-up that arrives 40 seconds after the reply — today that spawns a
cold run), not as long residency. Shipped shape has two layers: runner-owned
linger for true same-thought fold-in, and daemon-owned `attending` for the
post-return safety net/card truth. The quota-aware pacing piece deserves
its own design pass; it is policy on existing telemetry, not new
infrastructure.

Telemetry update (2026-07-03): the Claude `/usage` PTY probe is down from
~18s to ~3.5s, its cache TTL now 10s (maintainer's call — under the 30s
heartbeat any TTL means "probe every beat", so 10s is the freshest the
beat can deliver; `BRR_CLAUDE_USAGE_TTL` to override), and the parser now
keeps per-model weekly buckets separate — the TUI added a `Current week
(Fable)` line that previously clobbered the all-models number. Pacing
policy can now read a per-Core weekly constraint (the binding one for a
Fable-cored director), fresh to one beat, without new collection work.

**Maintainer reaffirmed the target shape (2026-07-04):** restated the
end-state directly — trigger (a message, or a tag on a ticket/PR) starts a
session; the resident does the work cost-permitting; the human's loop is
review/clarify/merge, "the self-hosted co-maintainer"; the session "stays
open for a long time... reset or restart occasionally," bounded mainly by
context window, not per-message termination. Checked against what's built:
the trigger half is already there (forge issue/PR events spawn a full
resident run with no dispatcher hop, per
`decision-account-centered-daemon.md` §3's routing table) and the
review/merge loop is already the `gate: forge` PR handoff. The *residency*
half is still deliberately short of this vision — B5 shipped a ~10–15m
linger plus a 90s post-return attending floor, not the long-session-with-
occasional-reset shape described here, precisely because of this section's
own cache-cliff economics (past ~5m every idle iteration re-reads full
context uncached). That scoping-down was correct as a v1 guardrail, not
necessarily as the destination — the maintainer's restatement reads as
"the short linger is a stepping stone, not the target." Revisiting it
productively needs B6's data (can the quota afford longer residency?) and an
explicit reset policy (context-window pressure or a scheduled cadence,
not "the runner returned"). Not re-scoped yet — named here so the next pass
on hot-idle residency starts from "this is still the standing ask," not
from a stale "short linger settled it."

Execution tickets for this design:
[`plan-director-execution.md`](plan-director-execution.md).

## B1 — quota-aware pacing policy (decided 2026-07-04)

The policy half of [#214](https://github.com/Gurio/brr/issues/214), written
against the telemetry that landed 2026-07-03 (per-Core weekly buckets, 10s
TTL). Scrutiny while writing it: `_merge_level_snapshots`
(`daemon.py:2436`) currently forwards the `quota` key from a Shell's level
snapshot wholesale but the snapshot itself
(`claude_usage.parse_usage_text`, `codex_status.parse_token_count`) only
ever put a rendered *string* summary in that dict — the numeric
`used_percentage` fields computed a few lines earlier
(`session_used_percentage`, `week_used_percentage`, `week_models[label]`)
never made it past the parser function. So today there is genuinely no
programmatic access to "how low is the binding bucket" downstream of the
collector — only a human-readable line. B2 needs to close that gap before
any pacing decision can read a number instead of parsing prose.

**Binding bucket.** The lowest live remaining-percent among: session,
week (all-models), and any active per-model week bucket (Codex: primary +
secondary rate-limit windows). "Remaining" always means `100 -
used_percentage`; a shell with no collector for a slot contributes
nothing (never guessed).

**Two floors, account policy, not hardcoded** (mirrors the
`delivery.post_delivery_attend_seconds` convention — dotted key, sane
default, `.brr/config` overridable):

- `pacing.quota_low_floor_pct` (default `20.0`) — below this, `every:`
  schedule entries stretch: the due-check uses `interval *
  pacing.quota_stretch_factor` (default `3.0`) instead of the entry's
  stated interval, so a standing loop backs off without being silenced.
- `pacing.quota_critical_floor_pct` (default `8.0`) — below this, `every:`
  entries do not fire at all this beat (ambient loops pause). Recovery
  above the floor resumes normal cadence on the next beat; no separate
  "resume" bookkeeping needed since the check re-evaluates live each beat.

**What is never discretionary:** `at:` one-shot entries (deadlines,
reminders) and anything gate-addressed (a real user waiting on a reply).
Quota pressure bends *ambient* initiative, never a promise already made to
someone.

**Respawn core class.** Downshifting is resident policy (B3), not a new
daemon mechanism — B1 only supplies the number the resident's own
delegation judgment reads (the Mode block's `Quota:` line already carries
it). A daemon-side automatic override of a resident's explicit `shell:`/
`core:` respawn choice is out of scope here; it would second-guess a
judgment call the resident is better placed to make with the full picture
(task shape, not just quota).

**B2 scope (plumbing, delegable):** thread the buckets through
(`claude_usage`/`codex_status` → `quota` dict → `_merge_level_snapshots` →
`_fire_due_schedules`), add the floor/stretch config readers, apply the
stretched interval (or the pause) only to `kind == "every"` entries before
calling `schedule.due_entries`, and surface the binding percent + which
floor (if any) is active in `resources` so a mid-run boundary can see the
same number the scheduler used. Full spec: `plan-director-execution.md`
§B1–B2 depends-on note; exact touch points named in the B2 delegation
brief (kb/log.md, this date).

## Cache TTL vs compaction, and B6's data problem revisited (2026-07-04)

Maintainer question (telegram): does idle wall-clock time itself get billed
while a permission-gated session waits on a user reply, and is TTL-eviction
the same thing as compaction? Two separate mechanisms, worth naming apart:

- **Cache TTL eviction** (~5m Anthropic, similar order for Codex) is
  time-based: no request arrives within the window ⇒ the *next* request is a
  cache miss, priced as a full uncached input read. Idle time itself is not
  metered — nothing is billed while no call is made. The cost is deferred,
  not incurred, and it only lands as "more expensive," never as "charged for
  waiting."
- **Compaction** (context summarization when the window fills) is
  capacity-based, triggered by accumulated tokens, unrelated to how long the
  session sat idle. Conflating the two overstates the cost of a long
  permission-gated wait — the real tax is only the next-call cache miss, and
  only if the wait outlasted the TTL.

This confirms rather than revises §Hot-idle residency above (the 5-minute
cliff framing there was already right).

**B6 ("blocked on data... a week+ of observed per-runner burn"): partially
already unblocked.** Checked `$CODEX_HOME/sessions/**/rollout-*.jsonl` on
the operator's machine: 69 of 88 recent rollout files (2026-06-20 through
2026-07-04 — the actual dogfooding window, not a guess) have `cwd` under
this repo's worktrees, and every one carries `token_count` events with
`rate_limits.primary`/`secondary` (used_percent, window_minutes, resets_at)
timestamped per turn. That is a real ~2-week time series of Codex quota
burn already sitting on disk, retroactively minable — no forward waiting
period needed for the Codex half of B6. A one-off script over existing
rollout files, not new collection, not a bench.

Claude side has no equivalent: `claude_usage`'s PTY scrape of `/usage`
returns only the current snapshot, nothing persisted historically. Claude
session transcripts (`~/.claude/projects/**/*.jsonl`) do carry per-turn
token/cost usage, which could reconstruct relative burn *rate* but not
percent-of-weekly-cap (that arithmetic lives inside Anthropic's own
`/usage` rendering, not in the transcript). So: Codex's half of B6 can be
answered now from history; Claude's half still needs forward logging
(cheapest shape: persist the already-computed `claude_usage` snapshot to a
durable log on each heartbeat, starting now, rather than waiting on a new
collection mechanism).

**No new bench needed for this.** The maintainer's "do we need a bench?"
reads as a different question than [`design-bench-loop.md`](design-bench-loop.md)
answers — that bench measures prompt/protocol seam-following under a
lesser-light runner (card discipline, fold-in, next-move), not quota
economics. What B6 needs is data extraction (Codex: retroactive script over
rollout files; Claude: a forward log line) and then a policy pass over that
data — not a scenario harness that spends quota to observe behavior we can
already read off disk.

## Forks left to the maintainer

- None hard-blocking for phases 1–2. Phase 3's physical file location has a
  parked sub-fork (CS5) in the account-daemon decision — confirm on
  execution.
- ~~Whether option sets render as plain numbered text everywhere or as native
  buttons on gates that support them (Telegram inline keyboards)~~ —
  **settled 2026-07-03 (maintainer): plain numbered text everywhere.** A
  compact numbered closeout invites exactly the reply shape the loop wants —
  free-form, multi-part, composable ("1a 2a 3c and do x please") — while
  inline buttons collapse the exchange to one tap per option set and fight
  the mixed reply. The MUD instinct was right; the tech is ready now.
  No button rendering work is planned.
