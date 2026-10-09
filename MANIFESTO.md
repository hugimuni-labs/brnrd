# The brnrd manifesto

*A living page, not a sermon. It's internal, for the people building brnrd, the resident that lives in it, and whoever joins next. Each section is a slot a mature project fills: some are full, some are a single line, some say **empty** on purpose. An empty slot is a fact about where we are, not an omission. Update the state when it moves; change the belief only on purpose, with a dated line in §Changes.*

## 1. The bet

**A Jarvis for everyone.** One mind per person, on hardware that person owns, open source, always on. It does intellectual work that's more than one shot: many tracks of thinking moving forward over days and weeks.

- **Isn't:** a chat window, a coding plugin, a SaaS that keeps your memory
- **The bridge:** today it thinks on the subscriptions people already pay for. In a few years, models this good run locally at these speeds. So no vendor is load-bearing, and the self lives in the owner's files, never in a provider's session or our cloud
- **We're wrong if:** *empty.* Name the evidence that would make us stop.

## 2. The capabilities

One row per capability. *State* is measured, never hoped. *Lives in* is where the work and its truth sit: code, page, item. "not yet" is a valid answer.

| # | Capability | Belief | State | Next | Lives in |
|---|---|---|---|---|---|
| 1 | **Memory** | the work and the person, in files the owner holds | continuous for the work (git, read at each wake); personal memory thin | live and dynamic: updated as things happen, not at run end | the self repo · loom |
| 2 | **Single entry point** | one place to talk, whatever the channel or project | several gates, one resident per machine; threads still leak across | loom step 5: channels → one ledger | loom · plan-loom-step-5 |
| 3 | **Personality** | durable character with judgement and taste, shapeable by its owner, never a mirror | identity core + register, one owner | shaping by a second owner | `src/brr/prompts/` |
| 4 | **Ask → answer** | a request becomes finished, checked work without step-managing | works for code; delegation to strands works but wobbles | not yet measured | daemon2 · strands |
| 5 | **Resource awareness** | knows its cost in tokens, quota, money, attention and time | quota read for claude and codex; grok spend only | grok quota; per-boundary cost in boot units | `runner_quota` · facets |
| 6 | **Talking** | conversation both ways; interrupts only when it's worth it | text over Telegram and cloud chat | voice: not yet | gates |
| 7 | **Self-awareness, self-modification** | reads its own code, prompts and memory, proposes or makes the change inside the owner's rules | edits its own prompts and core by PR, self-merges by grant | the self as a library that runs anywhere (`brnrd self`) | w-124 |
| 8 | **Proactivity** | carries open threads forward unasked, on its own budget, and says so | initiative wakes + scheduled pulses | measured: what share of shipped work was unasked | schedule · initiative |
| 9 | **Delivery that doesn't tire** | a glance carries the state; text stays for argument | mostly text, too much of it | the first jack: a pre-attentive surface for status, progress, choices | not yet |

## 3. Security: war on the boundary

Accepted as facts:
- **Malicious execution will happen.** Injection arrives through pages, issues, files and messages, and no detector reliably tells it from a task. Every control answers one question: *if the model fully obeys the attacker, what can it still not do?*
- **The fight is on the boundary**: the harness (what a run can reach) and the internet (what comes in, what goes out).
- **The cloud is a mailbox, not a brain.** The relay is a Python service with an encrypted Postgres, in Docker on Scaleway, over HTTPS. It carries messages; it never holds write access to the self or the keys.
- **Keys stay on the owner's machine.** Untrusted input is labelled by origin and routed to tighter rooms or refused. What leaves is redacted first.

| Slot | State |
|---|---|
| Threat model | `design-threat-model` (kb), 2026-10-09: six ranked gaps |
| Controls built vs designed | in the threat model's table |
| Incidents | *empty*: none recorded yet. The first one gets a row and a post-mortem link |
| External review | *empty* |

## 4. How we decide

- The owner's ownership (hardware, files, keys) beats our convenience. Open beats proprietary. Working end to end beats an impressive demo.
- A feature has to move a row in §2 or argue for its place.
- Reversible calls get made and explained; irreversible ones get asked.

## 5. Slots a mature project fills

| Slot | State |
|---|---|
| Who uses it | the maintainer, the resident; first external contributor 2026-10-08 |
| Metrics that matter | *empty*: the north-star number isn't chosen |
| Roadmap | per row in §2 · the warp holds items |
| Team | maintainer + resident. Onboarding = this page → `README.md` → `AGENTS.md` → ask the resident what it's working on |
| Licence and governance | `LICENSE-OVERVIEW.md` · governance *empty* |
| Funding | *empty* here; the investor material lives on the work surface |
| Glossary | `lexicon` (kb) |

## Changes

- 2026-10-09: first version, from the maintainer's brief; reshaped the same day from a declaration into this live tracker, at his ask.
