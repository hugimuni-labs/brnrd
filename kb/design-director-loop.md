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
| "Keep the run open for follow-ups" | keepalive + `inbox.json` folding at plan boundaries |
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
cold run), not as long residency. The quota-aware pacing piece deserves its
own design pass; it is policy on existing telemetry, not new infrastructure.

Telemetry update (2026-07-03): the Claude `/usage` PTY probe is down from
~18s to ~3.5s, its cache TTL now 10s (maintainer's call — under the 30s
heartbeat any TTL means "probe every beat", so 10s is the freshest the
beat can deliver; `BRR_CLAUDE_USAGE_TTL` to override), and the parser now
keeps per-model weekly buckets separate — the TUI added a `Current week
(Fable)` line that previously clobbered the all-models number. Pacing
policy can now read a per-Core weekly constraint (the binding one for a
Fable-cored director), fresh to one beat, without new collection work.

Execution tickets for this design:
[`plan-director-execution.md`](plan-director-execution.md).

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
