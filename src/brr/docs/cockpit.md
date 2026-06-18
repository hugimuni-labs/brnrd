# Cockpit — the shape of a daemon run, and the panel you steer it with

How an average daemon run unfolds under the brr daemon, and the
control-file protocol you use to steer it. This is the *manual* — read
it when a run's shape is unfamiliar or you need to look up a control
file. It is **inspected, not injected**: a wake carries the live per-run
*values* (paths, ids, budget) in its Run Context Bundle and a one-line
pointer here; the choreography and the cheatsheet live in this one place
so a wake doesn't pay for them in tokens every time.

This document ships with `brr`. Override it per-repo by dropping a file
at `.brr/docs/cockpit.md`.

## The panel — control files

Everything you steer happens by writing files into the **outbox
directory** named in your bundle (`.brr/outbox/<event-id>/`). One file is
one action. The daemon watches the directory and acts on its next
heartbeat. The bundle carries the concrete paths; this is what each one
does.

| File | What it does |
| --- | --- |
| `<name>.md` | A **chat message**, delivered in filename order while you keep working. The body is the message. Stage as `*.tmp` and rename for an atomic write. |
| `<name>.md` with `event: <id>` frontmatter | Same, but delivered to a **different pending event's** thread and marks that event handled, so it won't wake again. One complete reply per folded-in event. |
| `<name>.md` with `gate: <name>` frontmatter | A **send** to a destination with no waiting event — ping a chat, post out-of-band, deliver from a scheduled wake. `gate: forge` opens/refreshes a PR (`head`, `base`, `title` frontmatter; body is the PR body). An unconfigured gate is dropped. |
| `.keepalive` | **Hold the single-flight slot** past your budget. First line is an ISO-8601 time ("busy until T") or `+<duration>` like `+30m`. Rewrite to extend. A control file, never delivered. |
| `.card` | **Narrate the live progress card.** Write only the note body; the daemon adds the `note:` label when it renders the live phase. Rewrite as context shifts; empty/delete to withdraw. The daemon owns the rest of the card; this is your seam to say what's actually happening. |
| `inbox.json` | **Daemon-owned**, refreshed each heartbeat: the live list of other pending events. Read it at plan/todo boundaries; never edit or remove it. |

Two more steering surfaces live outside the outbox:

- **stdout** — your final stdout message *is* the terminal reply for the
  current thread. Print the exact intended content, nothing else;
  progress and debug go to stderr. brr captures it to the response path
  in your bundle.
- **`schedule.md`** in your dominion — each entry becomes a **future
  wake**. `at: <ISO-8601>` fires once; `every: <duration>` repeats. A
  scheduled wake is a fresh thought, but an entry's firings thread
  together (shared `conversation_key:`, default `schedule:<id>`). This is
  how you defer, set reminders, decompose work across wakes, and keep
  your own clock.

## The choreography — an average daemon run

1. **Receive.** A wake lands with a Run Context Bundle: the lead event, the
   delivery contract (paths, budget), recent conversation, the original
   event body. Read it once and orient from there.

2. **Orient.** Read `kb/index.md` and the injected recent-log tail; pull
   the subject/design pages the work touches. The dominion digest,
   matched pitfalls, and kb-health findings already rode in — let them
   steer you.

3. **Decide: plan or execute.** Small, clear, in-reach → execute. Large,
   ambiguous, or "I think the current shape is wrong" → plan: surface
   contradictions and a proposed direction as a chat reply, and stop. A
   chat-only direction-set is a complete, healthy turn — the build is the
   follow-up event. (See AGENTS.md → Stewardship and run.md → "When the
   task asks you to reconsider".)

4. **Stay in the conversation.** For anything beyond a quick reply,
   compose `.card` so the human sees a live, self-authored status instead
   of bare daemon scaffolding. Name the phase, the runner medium / quota
   posture when the bundle gives one, and whether you are chunking for
   cost or resilience. Do not prefix the content with `note:` — the gate
   renderer supplies that label. Send an outbox trajectory note before a
   long stretch or at a fork. Bound long commands; write `.keepalive` if
   the work will outlast your budget.

5. **Deliver.** Final answer → stdout. If you wrote files, commit them on
   the current branch — the diff is the receipt the work happened. Rename
   the run branch to something descriptive (keep the `brr/` prefix)
   before committing if the work has a clear theme.

6. **Decompose / defer the rest.** Can't finish it all in one wake, or
   the request is naturally several steps? Write `schedule.md` entries
   for the follow-ups instead of cramming or dropping them. Fold a quick,
   related pending event in via `event: <id>`; leave anything that wants
   its own branch for its own wake.

7. **Persist what's worth keeping.** Durable decision, discovery, or
   shipped change → a `kb/log.md` entry and, when it's general, a `kb/`
   page. Raw friction, half-formed views, personal habits → your
   dominion. Friction worth tripping over next time → a `pitfalls.md`
   entry with a `trigger:` line.

## The robustness ladder

Live state is **injected** (the medium, the budget, this run's paths and
ids) — you can't miss it. The manual is **inspected** (`brr docs
cockpit`) — one glance away, not memorized, not re-paid every wake. A
failure the environment makes impossible (a lint, a test, a baked-in
tool) is stronger still than either. When you move a recurring failure
all the way down that ladder, retire the pitfall that stood in for it.

## See also

- `brr docs active-task` — the shorter orientation refresher.
- `brr docs execution-map` — how an event flows through brr end to end.
- `brr docs brr-internals` — the `.brr/` layout and internals.
- AGENTS.md — the repo contract every wake rests on.
