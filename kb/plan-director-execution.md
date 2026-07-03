# Plan: director-loop execution — pacing, reveal, and the stingy delegator

Status: active — opened 2026-07-03 from the maintainer's "next big chunks"
call (structured gamification + resource-aware director), converting
[`design-director-loop.md`](design-director-loop.md) into tickets a
lesser-light can pick up. The design page holds the reasoning and the
scrutiny; this page holds the executable slices. Tickets are dated
snapshots, not specs — when a ticket and this page disagree, this page and
the design page win.

## Ground rules (from the design, binding on every ticket)

- **No manufactured choice.** The run-end contract's most common value is
  "nothing to decide — continuing / done". Options appear only at genuine
  forks. A reviewer should reject any implementation that makes options the
  default shape.
- **The director is not a daemon component.** The daemon contributes
  deterministic seams (inject, parse, park, render, schedule); ranking and
  pacing judgment stay in the resident's prompt layer.
- **Progression rides existing surfaces** — issues, PRs, commits, the card,
  `kb/log.md`. No XP, no badges, no streaks.
- **Event-driven first; ambient loops opt-in** with a silence condition.

## Workstream A — structured pacing ("gamification" without dopamine)

### A1 — run-end next-move contract (prompt only) — owner: resident — [#211](https://github.com/Gurio/brr/issues/211) — *shipped 2026-07-03*

Phase 1 of the design. Add "the next move" to `docs/portals.md` and one
compact rule to the delivery-portals block in
`src/brr/prompts/daemon-substrate.md`: an addressed run's final reply ends
in one of `done — receipt` | `continuing — what's next` | `blocked — what's
needed` | genuine fork: 2–4 numbered options + recommendation + one-line
reason. Explicitly name manufacturing-options as the failure mode.
Acceptance: prose contract only; pins in `test_prompts.py` / `test_docs.py`
for the four states; no parser. Resident-owned because it is voice +
guardrail judgment, and it must not bloat the just-compressed contract.
Effort: one short wake.

### A2 — closeout parse + option folding (small code) — owner: delegable — [#212](https://github.com/Gurio/brr/issues/212)

Phase 2. Optional outbox/stdout frontmatter key `next:` carrying
`state / options / recommended`. Daemon parses into the run record; gate
delivery renders options as a numbered list; a short follow-up reply
("2", "B") on the same conversation key within N hours prepends the chosen
option text to the spawned run's prompt. Touch points: `daemon.py` closeout
path, gate rendering, `docs/portals.md`, tests mirroring the existing
outbox-frontmatter tests. No new store — rides the run record.
Depends on: A1 (contract wording settles the field names).
Effort: 1–2 focused wakes. Spec is complete enough for an economy core.

### A3 — the quest log / ranked move list — owner: delegable, resident reviews — [#213](https://github.com/Gurio/brr/issues/213)

Phase 3, and the already-decided inter-run plan home
([`decision-account-centered-daemon.md`](decision-account-centered-daemon.md)
§4). `plans/<repo>.md` in the account dominion repo: a ranked list of the
likeliest valuable moves. Daemon injects the top entries each wake
(bounded — top 3–5, not the file); resident re-ranks at closeout when the
run changed the picture; card links it. Sub-fork CS5 (physical file
location) must be confirmed with the maintainer at execution. Depends on:
nothing hard; lands best after A1 so closeouts feed the ranking.
Effort: 2–3 wakes (file contract + injection + re-rank prompt rule + tests).

### A4 — director tick (opt-in schedule entry) — owner: resident

Phase 4. Not a code ticket: a `schedule.md` entry whose body is the
director stance ("re-rank the move list from repo/forge state; message the
gate only if the top move changed or something is newly blocked"). Written
by the resident in its own dominion once A3 exists. Zero daemon work.

### A5 — diffense reveal re-skin — owner: resident

The maintainer switched diffense off because reading it was boring — the
reveal was flat, not the analysis wrong. Once A1–A2 give runs a reveal
grammar, re-present the diffense pack through it (finding count + severity
on the card, expandable detail on request, PR-comment mode unchanged).
Judgment-heavy presentation work; keep resident-side. Blocked by: A1, A2.

## Workstream B — the stingy, resource-aware director

### B1 — quota-aware pacing policy (design + prompt) — owner: resident — [#214](https://github.com/Gurio/brr/issues/214)

The policy seam named in the design's telemetry note: per-Core quota
(`claude_usage` week buckets incl. per-model "Fable week", `codex_status`
rate limits) is now fresh data (10s TTL on a 30s beat). Write the policy:
how observed quota bends schedule cadence (stretch `every:` intervals when
the binding week bucket is low), respawn core class (economy below a
threshold), and proactive-loop budgets. Deliverable: a short design section
in [`design-director-loop.md`](design-director-loop.md) + prompt rules
(daemon-substrate or portals doc) + the thresholds as account-policy
values, not hardcoded. Resident-owned: it is spend judgment.

### B2 — quota facts into the wake (plumbing) — owner: delegable — [#214](https://github.com/Gurio/brr/issues/214)

Whatever B1 decides to *say*, the daemon must *inject*: the Mode block's
`- Quota:` line already carries session/week/Fable-week; extend portal-state
`resources` so mid-run boundaries see quota movement, and thread the same
numbers into scheduled-wake spawn decisions (skip/defer a low-value tick
when the binding bucket is under B1's floor). Touch points: `daemon.py`
scheduler path, `facets.py`, tests. Depends on: B1 thresholds.
Effort: 1–2 wakes.

### B3 — delegation as resident policy (prompt) — owner: resident — [#215](https://github.com/Gurio/brr/issues/215)

The orchestrator/worker question, resolved in the design as policy-not-
architecture: the resident keeps user-interfacing, commits, and judgment;
bounded tedium goes to subagents / `respawn:` with explicit `shell:`/
`core:` or `quality: escalate` — and *downshift* for tedium (an economy
core for mechanical sweeps), not only escalate. Deliverable: a delegation
section in the playbook/substrate naming when to spawn what, with the
cost-ranked catalog as the menu. Revisit trigger for a real two-tier split
stays model-economics-dated (see design).

### B4 — worker stack slim-down — owner: delegable — [#215](https://github.com/Gurio/brr/issues/215)

When a wake is spawned as a *worker* (respawn handoff, subagent-style
bounded task), it should get the slim stack: task + files + structured
result contract; no dominion write, no scheduling, no kb governance, no
full playbook. Today respawned runs get the full resident stack.
Touch points: `prompts.py` (a worker preamble variant), respawn path in
`daemon.py`, tests. Depends on: B3 naming the two stacks.
Effort: 1–2 wakes. Cleanly spec-able.

### B5 — post-delivery linger (named contract) — owner: delegable, resident reviews — [#216](https://github.com/Gurio/brr/issues/216) — *closed 2026-07-03*

The hot-idle scrutiny's surviving slice: a *short* post-delivery linger to
catch the follow-up that lands ~40s after the reply (observed live
2026-07-03: the stitch weave covers only the dispatch window; later
follow-ups spawn a cold run). A named contract — max iterations, TTL-aware
sleep step (stay inside the ~5m provider cache), yield immediately when
`portal-state.json` shows unrelated pending work — not an improvised
`while` loop. Touch points: portals doc (the contract), possibly a
`.linger` control file mirroring `.keepalive`, daemon slot accounting,
tests. Depends on: B1 (linger spends quota; the policy says when it's
worth it).

*v1 outcome (2026-07-03):* shipped as pure contract — portals manual
§"post-delivery linger" + a compact substrate rule — after confirming in
`daemon.py::_result_satisfied_delivery` that a mid-thought outbox reply on
the current thread already counts as the satisfying signal (`current_reply`
via `output_stats["current"]`), so a linger needs **no daemon change**:
outbox delivers, `.keepalive` holds the slot, `portal-state.json` gives the
yield signal. No `.linger` file — card + keepalive already carry the
posture. Parameters set by the maintainer's live ask (exponential backoff
30s → cap 240s inside the ~5m provider cache window; absolute yield on any
unrelated pending event; default horizon 10–15m past last delivery). The
multi-hour vigil the maintainer floated (up to 10–20h at 2–3m polls) is
deliberately **not** in v1: it needs compaction + B1's quota floors to be
honest about spend; revisit under #214. First live firing same run
(run-260703-1503-k3ah): delivered via outbox, lingered ~14m through 5
polls (30→60→120→240→240s), watched the outbox drain and the folded
event retire through `change_token` movement, exited quiet at horizon —
contract held end to end with zero daemon support.

*Closed 2026-07-03* after the safety-net audit: `.keepalive` extensions
verified live up to `hard_cap = max(4×budget, budget+1h)` checked per 10s
heartbeat (`daemon.py::_budget_exceeded`), so a keepalive-holding linger
can't be reaped inside its horizon; a mechanical tail-sleep example
(bounded `timeout` poll per tool call, backoff in the call sequence)
landed in the portals manual for lesser-light cores — per-call polling
also lets portal-update hooks push pending events between polls, which is
the yield-rule safety net. Remaining scope split to
[#219](https://github.com/Gurio/brr/issues/219): a non-terminal
`attending` card phase (`interim_response` on the lead event + live
keepalive ⇒ "delivered · attending"), so a lingering run stops reading as
unfinished work.

## Voice workstream — remaining tail (context, not new scope)

- AGENTS.md house-voice pass — resident, own commit (round-6 direction:
  settled/dry/exact + register density in enumerable sections; no resident
  intimacy, no glyph-load-bearing — it must load-bear solo for foreign
  agents and adopter seeds).
- `user_commitment: full | profane` gate field ([#217](https://github.com/Gurio/brr/issues/217)) — *v1 shipped
  2026-07-03*: the maintainer set `user_commitment=full` in `.brr/config`;
  the daemon now threads that key into the communication snapshot and the
  bundle renders a "Reader model" line (`full` licenses weave-density
  replies; other values unfold to plain prose; absent = profane default,
  no line). *#217 closed 2026-07-03*; per-correspondent declaration at the
  gate boundary (Telegram command / account config) remains the eventual
  shape — re-ticket when it becomes real.
- `introspection.md` rework — round-6 finding, maintainer confirmed
  planned (2026-07-03): re-cut the development-mode attention block to the
  register-era voice; resident-owned, own commit.

## Sequencing (cheapest feel-win first)

1. A1 (prompt-only; the loop starts feeling designed immediately)
2. B3 (prompt-only; stinginess becomes policy) + B1 (design)
3. A2 → A3 (the mechanical spine) with B2 alongside
4. A4, A5, B4, B5, user_commitment plumbing
5. AGENTS.md pass whenever a quiet wake allows — independent

## Receipts

- Design + scrutiny: [`design-director-loop.md`](design-director-loop.md)
- Register/voice context: [`design-weave-register.md`](design-weave-register.md)
- Quest-log decision: [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md) §4
- Maintainer call opening this plan: telegram thread, 2026-07-03
  (evt-…-yd8n)
