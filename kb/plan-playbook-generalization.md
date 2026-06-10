# Plan — generalize the dominion playbook; brr becomes one driver

Status: shipped on 2026-06-10 (all three slices landed on branch
`playbook-generalization`)

## Why

The dominion playbook is the resident's standing self-orientation, but it
was written assuming brr's daemon is always the host — carrying substrate
mechanics (single-flight, capture-at-sleep, scheduled wakes, outbox,
keepalive) that don't apply, or *actively mislead*, when a plain editor
session or any non-brr wrapper reads it. The sharpest case: "brr captures
whatever you leave at sleep" is a footgun for an ad-hoc reader, whose
dominion writes are silently lost if nothing commits them.

The north star is **every agentic tool participating as a thought**. So the
playbook should describe *who the resident is and how it persists*
(host-agnostic), and brr's mechanics should live in brr's own driver's
manual — which any other wrapper could supply its own version of.

Reframe: **the playbook is the resident; brr is one driver of it.**

## Shape

Three homes for what is today one over-loaded playbook:

- **Core playbook** (`prompts/dominion-playbook.md`; agent-owned,
  host-agnostic): who you are, continuity-is-memory, society-of-mind,
  the dominion (commit it yourself; `needs_sync`; self-inject; the
  dissonance loop), kb-is-shared, ownership, environment-shaping, a
  generalized delivery + context map, and the closing meaning section.
- **brr's driver's manual** (new bundled prompt doc; brr-owned, injected
  only on the **daemon** path): scheduled-wakes, the capture-on-failure
  net, the Task Context Bundle layer, and a pointer to the per-task
  delivery contract (outbox / keepalive / budget — which already live in
  the bundle). This is brr's running substrate, not something the agent
  owns.
- **`brr agent inject`** (new tool; `agent` reserved as a verb group):
  prints brr's assembled wake-context — the dominion digest + matched
  pitfalls + the recent `kb/log` tail — by reusing the runner's own
  assembly path (a factored `_build_injected_blocks`), so a non-brr
  wrapper reuses the exact semantic and the tool can't drift from what a
  runner actually receives.

## Calls made (with the user)

- **capture-at-sleep**: removed from the agent's model (the footgun); the
  agent commits its dominion like anything else. The daemon keeps
  committing on the **failure path** as a silent net (a thought that
  errored still persists the pain that caused it), undocumented to the
  agent so nothing relies on it.
- **single-flight**: removed as identity; folded into society-of-mind —
  you are many thoughts, not one process; what constitutes you is the
  shared memory palace they read and write. A conflicting thought is not
  a race; you meet it as a contradiction in your own memory when you next
  look, and reconcile it. The execution fact ("brr runs one at a time")
  drops to brr's side if stated at all.
- **self-inject**: kept; backed by `brr agent inject`.
- **needs_sync**: kept; wording degenericized (your *host*, not "the
  daemon").
- **scheduled-wakes / outbox / keepalive**: brr-substrate → driver's
  manual (daemon-owned). The agent doesn't own these.
- **`agent` reserved as a CLI verb group**; first verb `inject`.

## Slices (all shipped 2026-06-10)

1. **Generalize the core playbook** + fix the injected `_build_dominion_block`
   (it carried the same capture-at-sleep line). Society-of-mind replaced
   single-flight-as-identity; commit-yourself replaced capture-at-sleep;
   context map and delivery generalized. Playbook shrank 15.5 → 13.5 KiB,
   restoring ~7 KiB of inject-budget headroom. ✅
2. **Extract brr's driver's manual** (`prompts/daemon-substrate.md`):
   single-flight, the capture-at-sleep net, and self-scheduled wakes.
   Injected only on the daemon path (`build_daemon_prompt`), not `brr
   run`; the per-task delivery contract stays in the Task Context Bundle. ✅
3. **`brr agent inject`**: factored `_build_injected_blocks` out of
   `_join_prompt_parts` and exposed `build_injected_context` on top, so the
   tool and the runner share one assembly (no drift). The command prints
   the dominion digest + matched pitfalls + recent `kb/log` tail. ✅

## Links

- Generalizes the resident orientation designed in
  [`design-agent-dominion.md`](design-agent-dominion.md).
- Orientation entry point that now points ad-hoc tools at the playbook:
  [`AGENTS.md`](../AGENTS.md) → Workflow → Orientation.
- Co-development counterpart that asks the resident to weigh its own
  injected context: [`design-context-introspection.md`](design-context-introspection.md).
