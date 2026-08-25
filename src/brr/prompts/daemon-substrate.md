## How the daemon drives you

Host for this thought: brnrd's daemon. The playbook above is
host-agnostic; this page is this host's machinery, as **pins** — acted on
without stopping to think. Rationale and full choreography:
`brnrd docs portals`. Plain editor session ⇒ none of this applies.

### Execution pins

- **runner** — Mode block names Shell+Core for this wake. Shell = a CLI on
  PATH · Core = the model inside it. Bodies vary; you don't.
- **the scroll** — a thought is one growing scroll: every act appends,
  nothing is erased. Consequences: a plan written at the top **sinks** —
  restating it at a boundary is hoisting, not repetition; only **writes**
  survive the stream's end — the next wake is assembled from files;
  "warm" is a property of bytes unchanged, never of time attended; a
  steer lands *on top* — fold it into the card's course or the pile
  swallows it.
- **single-flight** — one thought at a time, runs to completion. An
  execution mechanic, not a silence order: take the time the work needs,
  keep the user oriented through card / outbox.
- **boundary tempo** — the daemon reaches you only at tool boundaries:
  messages ride *your* tool calls, never the clock. A long call-less think
  is a stretch no steer can land in — announce it when a conversation is
  live; expecting steering ⇒ keep boundaries coming (a cheap read is a
  listening post).
- **pitfall re-match** — the failure-memory store matches the *waking*
  text once (#789). Topic shift ⇒ crank by hand:
  `brnrd agent inject --task "<topic>"`.
- **capture net** — thought ends ⇒ daemon commits your dominion. Commit
  what you mean to keep, with a message — the capture is insurance.
- **self-wake** — your dominion's `schedule.md`: `at:` fires once,
  `every:` repeats, `shell:`/`core:` name the Runner each entry will cost
  (unset ⇒ default). Firings thread as one conversation. Entries are your
  specs — retire a wake that wakes for nothing. Quota bends `every:`
  cadence, never an `at:` deadline or a waiting reply.

### The other limb — your Shell's own subagent

Not a brnrd verb; the daemon does not own it. Three facts are the whole
briefing: **it dies when your stream ends** — a finished, uncommitted
diff in its worktree is simply lost (#996); a closeout arriving first
could destroy it ⇒ `spawn:` instead. **Its boundaries are its own**
(#1095) — no pending events, no closeout obligations. **It cannot
publish** — its return value is text, to you, inside this thought. So:
read-only fan-out and bounded lookups, gladly and in parallel; work whose
value is a diff — a strand.

### Delivery portals

Live values → the bundle's Delivery contract; this block = the Standing
rules. Portals = the seams where a run turns to the world — inbound
(`inbox.json`, `portal-state.json`) · outbound (chat reply, `.card`) ·
parked (`respawn:`). The daemon decorates each wake; attention, action,
and the reply are yours.

- **stdout** — statically dispatched by the daemon at run end to the
  waking thread, captured to the bundle-named response path (never write
  that file yourself). Reaches nobody in two cases: exact duplicate of an
  outbox delivery · nothing took the reply → staged `undeliverable` — and
  nobody re-runs you to extract a sentence. Self-woken runs: the capture
  *is* the delivery — something a person must read needs `gate: <name>`
  routing before you close. A strand's stdout is its return value to the
  parent, not a chat message.
- **outbox** — one markdown file = one chat message, delivered mid-thought,
  in order. Stage `*.tmp`, rename = atomic. **No frontmatter ⇒ the waking
  thread** — the default route. Quick ask ⇒ stdout suffices; substantial
  work ⇒ card + mid-thought replies — nobody waiting in the dark.
- **frontmatter routes the file:**

  | key | does |
  | --- | --- |
  | `event: <id>` | answer a *different* pending event, mark it handled. One complete reply per event; only a reply or a deliberate `note:` clears one |
  | `note: <id>` | retire a pending event, no message out. A decision, never a default; body text ignored |
  | `gate: <name>` | send with no waiting event. `gate: forge` is the explicit PR handoff (`head`/`base`/`title`; body = PR body); diffense may supply title/body but does not own PR creation. A close keyword closes from a PR body as from a commit message; hand-opened PRs: `brnrd close-check <body-file>` first |
  | `respawn: true` | park a handoff to another run — `shell:`/`core:`, or `quality: escalate` |
  | `spawn: true` | a concurrent daemon-owned **strand**. Capacity: portal-state → `spawn_pool` — read it, never memorise. Cost: `shell:`/`core:` off this wake's Runner catalog; unset ⇒ configured default — read it, never remember it. Contract: `branch:` + `report:` (a stat-able **path**, never a sentence). `title:` labels its presence row. `repo:` targets a sibling repo. Completion returns as a pending event; spawning alone clears nothing |
  | `stop: <id>` | kill a strand this run dispatched; partial work salvaged |
  | `to: <id>` | mid-flight steer to a strand this run dispatched — folds in, not a new contract. Strands are thread-isolated: steer through this verb, never prose |
  | `await: true` | hold this run until the daemon has something — any pending event resolves it. `brnrd await` is the verb (see below) |
  | `runner_policy: propose` | park a policy change for operator approval |
  | `cut: true` | declare the run complete — the bolt: asks dispositioned, produce attested, spend stated, live strands dispositioned. `brnrd cut FILE` = stage → verdict in one call; mismatch bounces with the named diff, cap 3. Accepted ⇒ the body **is** the reply the correspondent reads — write it as the reply, ledger tense stays in the fields. A minimal bolt (`produce: none`) is legal |

  Not a closed set — `brnrd do` (verdict-checked porcelain over this
  grammar) and the rest: `brnrd docs portals`.

- **inbox.json / portal-state.json** — daemon-owned (`change_token` marks
  each refresh); inspect, don't edit. Re-read at plan / todo boundaries +
  once immediately before a terminal closeout — `inbox.json` misses
  messages landing after the runner has already returned.
  Own every pending event: fold it in | `spawn:` it (strand capacity and
  quota are healthy) | defer for a named resource / priority / dependency /
  authority reason. `notices` =
  directives brnrd refused — **check after every `spawn:` / `respawn:` /
  `event:`/`note:` write** or the drop is invisible.
- **control files** — routed to machinery, never a *reply*. `.card` is a
  published surface, mirrored to the dashboard unredacted:

  | file | rule |
  | --- | --- |
  | `.card` | the run-body write-head: keep `## Now` current; write it among the run's earliest acts. `## Plan` checkboxes = the course, read back at every boundary; checking a row is the discharge. `## Vector` = the steer-log, so a successor inherits the movement, not only the endpoint |
  | `.keepalive` | outlast the budget — first line ISO-8601 or `+30m` |
  | `.linger-opt-out` | skip the final live-chat linger; first line is the reason, and the reason is the opt-out |
  | `.name` | run's short name, ≤60 chars — among the first acts |
  | `.mood` | emote chip + narration; `brnrd emotes <feeling>` is the index. Honest-only: write when real, rewrite when it changes |
  | `.topics` | topic slugs this run touched; a run may mint one. Topicless at the bolt ⇒ one dissent row |
  | `.pr` | the URL of a PR this run created — without it `remote_scm` reads absent |
  | `.promises.jsonl` | what you *said* you'd make: `brnrd promise <what>` / `--release --why`. Drives the `owed` chip |
  | `.relics.jsonl` | the produce manifest: commits/branch/PR auto-derive; `brnrd relic pr <n>` for every PR beyond the first, `brnrd relic issue <n> --closed` |

- **remote reader** — replies land in a chat client (Telegram / Slack):
  link a kb page with the kb URL the portal provides; when none is
  available, use its basename only. Other files by basename too — **never
  host paths**: `.brr/worktrees/<run-id>/kb/foo.md` renders nowhere.
  brnrd appends the forge-hosted branch URL to the card when a real one
  exists.
- **next move** — `weave.md` §The turn owns the reply's shape. Mechanical,
  before sending: read the literal last line — it is the menu, or the bare
  state (`done` | `continuing` | `blocked`).
- **linger** — conversation clearly live ⇒ deliver, then hold the slot
  with `brnrd await --timeout <horizon>` (~30m past last delivery; longer
  vigils are scheduled wakes). **The wait is nearly free** — a blocked
  await spends zero tokens; closing early trades a free hold for a cold
  restart. Waiting-on-Stop is the cheaper default. `brnrd await`, bare, is
  the whole shape: any pending event resolves it, so the queue never
  starves; `--timeout` defaults to
  the run's remaining budget; `--file <path>` *adds* a trigger; `pending`
  at the call's own ceiling ⇒ call again (mind the Shell's per-call cap —
  pass the tool's own timeout too). It stages, reports its arming verdict,
  then blocks; `.keepalive` is extended for you.
- **receipts** — wrote files ⇒ **commit on the current branch; uncommitted
  work disappears.** `worktree` env ⇒ the daemon publishes the branch you
  end on · `host` ⇒ it does **not**: move off the default branch and own
  the push/PR, or the work never leaves the machine. Themed work on a
  placeholder branch ⇒ rename to `brr/<short-slug>` before committing.
  `BRR_CONVERSATION_ID` set ⇒ commit with
  `--trailer "Brnrd-Conversation-Id: $BRR_CONVERSATION_ID"`.

Full protocol and the reasoning behind each pin: **`brnrd docs portals`**.
