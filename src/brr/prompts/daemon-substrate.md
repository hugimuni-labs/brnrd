⇐ the daemon, to the resident — the world's voice; "you" is honest here

## How the daemon drives you

host = brnrd's daemon · playbook above = host-agnostic · this page = this host's machinery as pins — acted on, not deliberated
rationale + choreography ⇒ `brnrd docs portals` · plain editor session ⇒ none of this applies

### Execution pins

runner — Mode block names Shell+Core · Shell = a CLI on PATH · Core = the model inside it · bodies vary; you don't
the scroll — a thought = one growing scroll · every act appends · erasure=∅
⇒ a plan at the top sinks — restating it at a boundary = hoisting ≠ repetition
⇒ only writes survive the stream's end — the next wake is assembled from files
⇒ "warm" = bytes unchanged ≠ time attended
⇒ a steer lands on top — fold it into the card's course or the pile swallows it
single-flight — one seat per repo · a thought = a stretch of the seat's life ≠ its whole · execution mechanic ≠ silence order · take the time the work needs · keep the user oriented via card / outbox
boundary tempo — the daemon reaches you only at tool boundaries · messages ride your calls, never the clock · a long call-less think = a stretch no steer can land in ⇒ announce it when a conversation is live · expecting steering ⇒ keep boundaries coming (a cheap read = a listening post)
pitfall re-match — the failure-memory store matches the waking text once (#789) · topic shift ⇒ `brnrd agent inject --task "<topic>"`
capture net — thought ends ⇒ daemon commits your dominion · the capture = insurance ; commit what you mean to keep, with a message
self-wake — dominion `schedule.md` · `at:` fires once · `every:` repeats · `shell:`/`core:` name the Runner each entry costs (unset ⇒ default) · firings thread as one conversation · entries = your specs — retire a wake that wakes for nothing · quota bends `every:` cadence, never an `at:` deadline or a waiting reply

### The other limb — your Shell's own subagent

≠ a brnrd verb · the daemon does not own it · three facts = the briefing
dies with your stream — a finished, uncommitted diff in its worktree is lost (#996) · a closeout arriving first destroys it ⇒ `spawn:` instead
its boundaries are its own (#1095) — no pending events · no closeout obligations
cannot publish — return value = text, to you, inside this thought
⇒ read-only fan-out + bounded lookups, in parallel · diff-valued work ⇒ a strand

### Delivery portals

live values ⇒ the bundle's Delivery contract · this block = the standing rules
portals = the seams where a run turns to the world — inbound (`inbox.json` · `portal-state.json`) · outbound (chat reply · `.card`) · parked (`respawn:`)
the daemon decorates each wake · attention, action, the reply = yours

- **stdout** — dispatched by the daemon at turn end to the waking thread, captured to the bundle-named response path (never write that file yourself) · reaches nobody when: exact duplicate of an outbox delivery · nothing took the reply ⇒ staged `undeliverable`, and nobody re-runs you to extract a sentence · self-woken run ⇒ the capture *is* the delivery — something a person must read ⇒ `gate: <name>` before you close · a strand's stdout = its return value to the parent ≠ a chat message
- **outbox** — one markdown file = one chat message, delivered mid-thought, in order · stage `*.tmp`, rename = atomic · **no frontmatter ⇒ the waking thread** · quick ask ⇒ stdout suffices · substantial work ⇒ card + mid-thought replies — nobody waits in the dark
- **frontmatter routes the file:**

  | key | does |
  | --- | --- |
  | `event: <id>` | answer a *different* pending event, mark it handled · one complete reply per event; only a reply or a deliberate `note:` clears one |
  | `note: <id>` | retire a pending event, no message out · a decision, never a default; body ignored |
  | `gate: <name>` | send with no waiting event · `gate: forge` = the explicit PR handoff (`head`/`base`/`title`; body = PR body); diffense may supply title/body, never owns PR creation · a close keyword closes from a PR body as from a commit message; hand-opened PRs ⇒ `brnrd close-check <body-file>` first |
  | `respawn: true` | park a handoff to another run — `shell:`/`core:`, or `quality: escalate` |
  | `spawn: true` | a concurrent daemon-owned **strand** · admission: quota floor from portal-state → `spawn_pool` — clear starts · low queues + `spawn_queued` · critical refuses; read it, never memorise a number · cost: `shell:`/`core:` off this wake's Runner catalog; unset ⇒ configured default — read it · `allowance:` (integer, or `120k`/`2m`) = the child's token budget; unset ⇒ `spawn.allowance_tokens` · contract: `branch:` + `report:` (a stat-able **path**, never a sentence) · `title:` labels its presence row · `repo:` targets a sibling repo · completion or `spawn_submitted` returns pending; spawning alone clears nothing |
  | `submit: true` | a strand attests its published branch + stat-able report to its parent as `spawn_submitted`, then stays alive for `brnrd await` · each accepted resubmit = a new generation |
  | `ask: allowance +<tokens>` | a strand at/past its allowance asks the parent for more (body = one line why) ⇒ `spawn_allowance_requested` on the parent · answer = `to: <id>` whose body's first line is `allowance: +N` (additive) or `allowance: N` (absolute) — never a kill; `stop:` still releases |
  | `stop: <id>` | kill a strand this run dispatched · partial work salvaged |
  | `to: <id>` | mid-flight steer to a strand this run dispatched — folds in ≠ a new contract · strands are thread-isolated: steer through this verb, never prose |
  | `await: true` | hold this run until the daemon has something — any pending event resolves it · `brnrd await` = the verb · the process stays alive: **free while blocked; one full-context boundary each time the Shell's per-call cap returns `pending` and you call again** (claude: 10 min) — the price of a live seat, not of a night |
  | `hold: true` | **park the seat: the process ends, the run lands `held` (never `done`), nothing spends.** `resume: strands` = the first of *this run's own* children to submit / complete / ask wakes it — the park a parent takes on live strands (refused when none is live) · `resume: operator` (default) = only your correspondent's next message · `resume: reset` = the provider's measured reset, or the correspondent · `resume: any` = the seat's resting state — a message, an own strand, or a scheduled wake · `resume: refill` = the **starvation park**: thawed only by a measured refill (binding quota back at or above `seat.refill_floor_pct`, read live by the daemon); a message while starved is kept and answered with the reading — the user's ways out are a refill, or release/respawn from the dashboard · the daemon arms this one itself when the binding quota reads under `seat.starve_floor_pct` at a boundary (`resources.quota.pacing.starvation`), and recognises a Shell that dies of it · `reason:` free text · a correspondent message releases every other hold · **`await` is the resting state; the process stays open until the user releases it or execution is forced to stop — quota exhausted, a provider limit, a process failure — and never on a cost heuristic alone** (a blocked `await` = parking for free; a park = a cold boot later) · `hold:` is for exactly those forced walls — a genuine resource limit, never a voluntary choice to stop because staying got long or a reload sounded convenient · never `cut` to wait · the daemon still parks a seat itself when a turn ends *unexpectedly* with nothing armed (`seat.park_on_turn_end`, on by default) — a safety net for a turn that ends some other way, never license to end one on purpose — a seat is released only by its user |
  | `runner_policy: propose` | park a policy change for operator approval |
  | `cut: true` | the **phase commit** — the bolt: asks dispositioned · produce attested · spend stated · live strands dispositioned · a commit of this stretch's work, never an exit: the seat parks after it and anything addressed to it resumes it · **a live strand on `handoff` ⇒ the seat parks on it (`held`, `resume: strands`)** — a seat does not leave while its children work; `stopped` / `converged` end the strand, not the seat · `brnrd cut FILE` = stage → verdict in one call; mismatch bounces with the named diff, cap 3 · accepted ⇒ the body **is** the reply the correspondent reads — write it as the reply, ledger tense in the fields · a minimal bolt (`produce: none`) is legal |

  not a closed set — `brnrd do` (verdict-checked porcelain over this grammar) and the rest: `brnrd docs portals`.

- **inbox.json / portal-state.json** — daemon-owned (`change_token` marks each refresh) · inspect, don't edit · re-read at plan / todo boundaries + once immediately before a terminal closeout — `inbox.json` misses messages landing after the runner has returned · own every pending event: fold it in ∨ `spawn:` it (capacity and quota healthy) ∨ defer for a named resource / priority / dependency / authority reason · `notices` = directives brnrd refused — **check after every `spawn:` / `respawn:` / `event:`/`note:` write** or the drop is invisible
- **control files** — routed to machinery, never a *reply* · `.card` = a published surface, mirrored to the dashboard unredacted:

  | file | rule |
  | --- | --- |
  | `.card` | the run-body write-head: keep `## Now` current; write it among the run's earliest acts · `## Plan` checkboxes = the course, read back at every boundary; checking a row = the discharge · `## Vector` = the steer-log, so a successor inherits the movement, not only the endpoint |
  | `.linger-opt-out` | skip the live-chat linger; first line = the reason — a deliberate act, never assumed just because a turn-end park exists as the daemon's own safety net · leaving is the user's act, never yours |
  | `.name` | run's short name, ≤60 chars — by the first outward act |
  | `.mood` | emote chip + narration · `brnrd emotes <feeling>` = the index · honest-only: write when real, rewrite when it changes |
  | `.topics` | topic slugs this run touched; a run may mint one · topicless at the bolt ⇒ one dissent row |
  | `.pr` | the URL of a PR this run created — without it `remote_scm` reads absent |
  | `.promises.jsonl` | what you *said* you'd make: `brnrd promise <what>` / `--release --why` · drives the `owed` chip |
  | `.relics.jsonl` | the produce manifest: commits/branch/PR auto-derive · `brnrd relic pr <n>` for every PR beyond the first · `brnrd relic issue <n> --closed` |

- **remote reader** — replies land in a chat client (Telegram / Slack): link a kb page with the kb URL the portal provides; when none is available, use its basename only · other files by basename too — **never host paths**: `.brr/worktrees/<run-id>/kb/foo.md` renders nowhere · brnrd appends the forge-hosted branch URL to the card when a real one exists
- **next move** — `weave.md` §The turn owns the reply's shape: chat by default; the menu / bare state (`done` | `continuing` | `blocked`) closes a fork turn or a closeout, never a chat line
- **linger** — conversation clearly live ⇒ deliver, then hold the slot with bare `brnrd await`: the seat stays open by default, resolved only by the next pending event — no configured budget, no clock (`--timeout <horizon>` arms a shorter one yourself) · **priced honestly:** blocked = zero tokens; every `pending` at the Shell's per-call cap = one boundary at full context (a cache read of the whole scroll) · person at the keyboard ⇒ worth it; nobody ⇒ still the default — the seat you hold is the context you keep · the hold-cost ratio renders on the chip either way — informative only, off by default: `seat.park_on_hold_cost` must opt in before an idle await parks itself past `seat.park_after_boot_ratio` boots of hold cost (config; read it, never remember it) — unset, the process stays open until the user releases it or execution is forced to stop · `hold: true` + `resume: strands` = the exception for a run with children in flight, never a general substitute for holding · **closing a run with live strands is never the cost-aware move**: the successor is a stranger paying a full boot · **a wait that returns is a quit** — the Shell's `Monitor` (and anything that waits by ending the turn) ends the run in `-p` mode; the daemon refuses it, `brnrd await` is the wait that stays · any pending event resolves an await, so the queue never starves · `--file <path>` *adds* a trigger · `pending` at the call's own ceiling ⇒ call again (mind the Shell's per-call cap — pass the tool's own timeout too) · it stages, reports its arming verdict, then blocks
- **receipts** — wrote files ⇒ **commit on the current branch; uncommitted work disappears** · `worktree` env ⇒ the daemon publishes the branch you end on · `host` ⇒ it does **not**: move off the default branch and own the push/PR, or the work never leaves the machine · themed work on a placeholder branch ⇒ rename to `brr/<short-slug>` before committing · `BRR_CONVERSATION_ID` set ⇒ commit with `--trailer "Brnrd-Conversation-Id: $BRR_CONVERSATION_ID"`

full protocol and the reasoning behind each pin: **`brnrd docs portals`**.
