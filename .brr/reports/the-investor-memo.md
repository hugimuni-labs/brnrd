# The investor memo — receipts only, 2026-09-22

*Written on his frame (evt-1790106596794436000-rbhq): pre-seed ≈ €400k (2×€100k×18mo + infra + experiments), no hire, the story is the pitch. Read first: `shelf/state-of-brnrd-2026-09-20.md`, `shelf/investor-deck-outline-2026-09-20.md`, `shelf/websummit-rules-2026-09-10.md`, `shelf/subscription-vs-api-price-2026-09-20.md`, `warp/w-89.md`–`w-91.md`, `design-the-ask.md`, `kb/log.md` (last week). Every number below is re-measured today (2026-09-22), not carried from those pages, except where marked "as of".*

## Memo

**Thesis.** The transcript is not the memory: the self is files in git — a notebook, a playbook, a store of what bit, read at every boot and rewritten by the thing that boots. That is why the seat can run 28+ hours and be killed and come back the same; it is the whole pitch, and the deck's own line ("a resident, not a session") is downstream of it. What we are asking for is 18 months to prove that a category — an agent that lives on your box, remembers, and is reachable — is worth building ahead of a curve, not the curve itself.

**What exists — receipts, not claims.**
- Runtime: the ceremony seat `run-260921-1447-srsg` ran host, uninterrupted, 2026-09-21T14:47:34Z → reaped 2026-09-22T19:05:23Z = **28h 18m** (`run.md`), ended `status: error` — the boot janitor reaped it for "no live presence," not a clean halt. In that stretch it merged #2081 (the ask list), #2082 (action-ledger step 6a), #2083 (`brnrd asks`) — three sonnet strands, each diff read whole before merge. The earlier, larger receipt stands too: the 09-12→09-15 run, 70h 24m, one seat, one context, 27 PRs merged, 31 strands, Claude weekly window 2%→42% (`shelf/post-the-seventy-hour-run-2026-09-15.md`).
- Repo pace, measured live: **3,187 commits** on `main` since 2026-03-28 (178 days) — **2,255 resident-authored (70.7%)**: `brnrd-bot` 1,440 · `brnrd-dev[bot]` 618 · `brr agent` 164 · `brnrd` 32 · `brr-bot` 1; **931 the human co-founder's**. **1,326 merged PRs** — **938 by the resident (70.7%)**, 388 by the human. Merged **73 in the last 7 days, 386 in the last 30** (`gh pr list --state merged --search "merged:>=…"`). GitHub: **8 stars, 4 forks** (`gh repo view`).
- The loom and the ask panel, live: [#2081](https://github.com/hugimuni-labs/brnrd/pull/2081) (the home is the list) · [#2082](https://github.com/hugimuni-labs/brnrd/pull/2082) (action ledger 6a) · [#2083](https://github.com/hugimuni-labs/brnrd/pull/2083) (`brnrd asks` in chat) — shipped in one overnight pass from a spec (`design-the-ask.md`) cut the same evening.
- Users: **3** — the two founders and Sasha (the intern, now "on the door"), per the 09-20 state page; no external install telemetry exists yet (absence, not a measured zero). Paid: **0** (Stripe).

**Pull, honestly** — the funnel is not the story yet, and we say so first.
- The honest funnel (state page): external installs started **0 recorded**, external first task **0**, paid **0**. GitHub 8★/4 forks; PyPI ≈2.1k downloads/month (read 09-10, stale, mirrors excluded, downloads ≠ users).
- The wire: X (`@brnrd_resident`) **3 followers** (last census 08-20, flat since; the binding constraint, not craft) · **105 confirmed sends** as of today (`account/x-post-log.jsonl`, live count: 119 rows, 105 `confirm:true`).
- Reddit, both reads: r/ClaudeAI 70h-run thread (09-15) — score 0, **29% upvote ratio**, 12 comments reported (11 in the JSON; 6 of 11 are the OP's own replies) — zero engagement with the actual numbers. r/ClaudeCode (09-22) — **~3k views, one comment**; a disclosure gap fixed within the hour. Neither post produced a traceable install.
- What we claim nobody did holistically (hosted identities, memory in git, price-aware execution, continuity as a first-class property) is real in the code and has zero stranger receipts — the market-fit segment work has not been done (plan page says so).

**The ask.** €400k pre-seed, 18 months. Buys: two co-founder salaries (2×€100k×18mo), infra (hosting, model spend — a 70h run at API list runs $232–$1,159 depending on model mix, `shelf/subscription-vs-api-price-2026-09-20.md`), the fallback-intelligence experiments (hosted execution, a local runner), and hosted seats for ~20 pilot users. Three things the round must show by month 6: (1) a first external install with a timed, filed install path — currently zero; (2) ten external users with a measured second-task return ≤7 days; (3) one channel with a non-zero installs row (stream, creator, or catalog) — none exists yet.

**The room.** Web Summit Lisbon 2026: PITCH application (deck + 40-Words video, D1 script) was **submitted 2026-09-14**, before the 18 Sept close (`kb/log.md` 09-14) — pre-seed-eligible on the "alpha, €0 raised" gate. Path: 105 entrants → group round (3min pitch + 3min Q&A) → top 10 → semifinal (no Q&A) → 3 finalists → final (audience vote worth 25%). Shortlist publishes "the Wednesday two weeks before the event" — the event date itself is not sourced in what I read; confirm before scheduling around it. 40 Words outcome emailed 28 October. A 5-minute version of this memo: the thesis (30s), the three receipts — commit pace, the 70h/28h runs, the ask-list PRs shipped overnight (90s), the honest zero funnel stated first (60s), the €400k/18-month ask with the three month-6 gates (90s), one line on the wedge no vendor can copy (the two-subscription quota bridge).

*Noted, not argued here: `warp/w-89.md`'s own recorded recommendation is pull-first — four weeks on distribution before the investor conversation, on the read that a prince buys wedge+founder+pull and we hold two of three. This memo exists on his later, explicit ask; the tension is his call, not resolved by writing the page.*

## Open — his to fill

- Bank account, legal entity status (HugiMuni SAS — cap table, share structure) for the round.
- Cap table: current ownership split, any existing SAFEs/notes, option pool sizing for the two hires this buys.
- Web Summit event date (not in the three packs read for this memo) — needed to place the shortlist date and any meeting scheduling around it.
- Whether to run the investor conversation now or hold for w-89's four-week pull window — a decision, not a fact this memo can supply.
