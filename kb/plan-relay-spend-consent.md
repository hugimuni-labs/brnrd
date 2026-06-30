# Plan: brnrd relay spend-plan and consent gate

**Status: implementation in progress (2026-06-29, evt-1782733549720122881-f608).**

This plan executes step 9 of `design-runner-cores.md` (implementation sequence):
spending-plan prompt, wallet balance read, cap enforcement, and audit rows for
brnrd-owned relay fallback.

Context: `decision-llm-relay.md` accepted relay pricing (provider cost + 10–15%
service fee, shown separately); `design-runner-cores.md` designed the fallback
chain and spend-plan gate. This plan operationalizes the gate.

## Background

When a run exhausts local LLM quota (no usable Codex key, Claude quota spent, etc.),
the fallback chain is:

1. Try cheaper local Shell/Core in the same or lower cost class (automatic fallback,
   already shipped 2026-06-29)
2. If no local fallback exists, offer **brnrd relay** with a spending plan for
   user approval
3. If relay balance is empty, offer top-up or "wait for quota reset"

The spending plan is the consent gate: before the run uses relay tokens, the user
sees the projected cost (model, cap, provider cost, service fee, balance) and
approves, denies, or reshapes the task.

## Design summary

### Spending plan data model (`spending_plan.py`)
- `SpendingPlan` dataclass: reason, model, provider, token estimates, costs,
  balance, cap, consent state
- `calculate_spending_plan()`: estimate provider cost + relay fee from token rates
- `format_spending_plan_message()`: human-friendly approval prompt

Service fee rate: 12% (midpoint of 10–15% range; exact rate locks in
`design-billing.md`).

### Relay runner selection (`runner_select.py`)
- `relay_runners()`: list all available brnrd relay runners
- `best_relay_runner()`: pick the best relay runner, optionally by provider
- Extended `RespawnRequest`: add `relay_consent` field (pending/approved/denied/capped)

### Daemon integration (deferred to next slice)
After automatic local fallback exhausts in `daemon.py` ~line 1330:
- Check for available relay runners
- If relay exists and no local fallback, emit an `attempt_failed` with
  `needs_relay_consent=true` instead of hard failure
- The resident responds with a respawn request (`relay_consent=approved`) or
  denies it

### Resident integration (deferred to next slice)
When the resident sees `needs_relay_consent=true`:
- Read `spending_plan` details from portal (injected by daemon)
- Emit approval or denial via respawn portal:
  ```
  respawn: true
  shell: brnrd-codex-relay
  relay_consent: approved
  reason: "User approved relay spend"
  ```

### Portal updates (`portal-state.json` facets)
- `resources.relay_candidates`: available relay runners + best candidate
- `resources.relay_consent`: pending spending plan details and consent state
- `resources.relay_balance`: current wallet balance and per-run cap

## Implementation slices

### Slice 1: Spending plan model ✓ done 2026-06-29
- `spending_plan.py`: `SpendingPlan` + calculation + formatting
- Tests: cost calculation, cap checking, message formatting

### Slice 2: Relay runner selection ✓ done 2026-06-29
- `runner_select.py`: `relay_runners()`, `best_relay_runner()`
- Extend `RespawnRequest` with `relay_consent` field

### Slice 3: Daemon fallback → relay (deferred)
After local fallback exhausts at line ~1330 in `daemon.py`:
- Check `best_relay_runner()`
- If found, emit `attempt_failed` with `needs_relay_consent=true` and
  `spending_plan` dict
- Save spending plan to task/event state for potential audit
- Stay in "paused waiting for approval" state, not "hard failure"

### Slice 4: Portal expose (deferred)
- `_write_live_portal_state()`: add `relay_consent` block with plan details
- `resources.relay_candidates`: best relay runner metadata
- Card render: "Local quota exhausted. Relay available. Approve to continue?"

### Slice 5: Resident respawn consumer (deferred)
- When resident sees `needs_relay_consent=true`, read spending plan from portal
- Emit `respawn: true, shell: brnrd-relay, relay_consent: approved`
- Daemon consumer: if `relay_consent=approved`, proceed with relay runner

### Slice 6: Wallet balance read (deferred)
- Hook into brnrd account for relay balance and per-run cap
- Project into spending plan before emission
- If balance < estimated cost, offer top-up path

### Slice 7: Audit and billing (deferred)
- After relay run completes, log provider cost, service fee, total spent
- Debit wallet at ledger level (separate line items for provider + fee)
- Handle cap overage (hard stop at cap, fail fast if needed)

## Open details

1. **Exact relay service fee rate**: decision-billing.md to lock to exact %
2. **Per-run cap default**: currently 1.00 USD; should match user's risk tolerance
3. **Per-day cap default**: currently 5.00 USD; should sync with subscription tier
4. **Token estimation**: how does resident know estimated token count before run?
   - Option A: Run a cheap preview/estimation pass (Codex mini, etc.)
   - Option B: Use historical median from similar tasks
   - Option C: Show estimate as "unknown" and use a safe default cap
5. **Auto-approve policy**: should users be able to set "always approve relay if balance > X"?
   - Recommended: no v1 — every relay spend requires explicit approval
6. **Top-up UX**: when balance is empty, should we offer:
   - Manual top-up link to Stripe?
   - Auto-topup if card on file?
   - Wait notification with time to reset?

## Companions

- `decision-llm-relay.md` — pricing decision; relay service fee, BYO-free default
- `design-runner-cores.md` — runner selection, fallback chain, spending plan
  intent
- `design-billing.md` — wallet ledger, credit buckets, Stripe integration, relay
  fee rate
- `runner_select.py` — `relay_runners()`, `best_relay_runner()`, `RespawnRequest`
- `spending_plan.py` — data model, calculation, formatting

## Sequencing within gardening Task 2

This is a continuation of Task 2 (informed respawn model). Shipped slices:
- 2026-06-28 foundation: `runner_select.py`, Shell/Core metadata, portal upgrade
- 2026-06-29 selector/Core-registry/capability-cache/portal/local-fallback/
  quality-escalation

Remaining slices for Task 2:
1. **Relay spend-plan and consent gate** (this plan; slices 1-2 done, 3-7 deferred)
2. Quota-reset deferral (schedule a retry when quota resets)
3. (Possibly) proactive quality escalation (offer stronger Core before wall,
   not only after failure)

Each deferred slice owns its own wake.
