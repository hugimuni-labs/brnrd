# Positioning and runtime dependencies — 2026-05-21

Status: runtime-dependency slice accepted on 2026-05-22 via
[`decision-runtime-dependencies.md`](decision-runtime-dependencies.md);
broader positioning remains research. A `plan-readme-rework.md` (or
similar) is still the natural follow-up if the README positioning
recommendations land.

Peer of [`research-brr-vs-gh-aw.md`](research-brr-vs-gh-aw.md): both
reason about brr's market position. This page reframes the
zero-dependency constraint as one symptom of a broader positioning
question — *what does brr have to do to "pop" with the audience that
actually adopts AI coding tools today?* — and answers both halves
together because separating them produces local-optimum advice on each.

The page is intentionally opinionated. The recommendations are
positioning hypotheses, not measured outcomes; see *Honest disclaimers*
at the end.

## TL;DR

1. **`dulwich` is a trap.** It can't replace [`worktree.py`](../src/brr/worktree.py)
   (no `git worktree` support), so adopting it splits the git code path
   instead of slashing it. Every brr adopter already has git. Pass.
2. **`requests` is a clean modest win.** Roughly 80-100 LOC saved across
   the three gates and noticeably better error stderr. Take it when the
   deps stance is settled.
3. **Per-forge SDKs (PyGithub / python-telegram-bot / slack_sdk) are
   the bigger LOC lever** — 300-500 LOC potentially gone, whole bug
   categories removed — but they come with opinionated release cycles
   and capability locking. Defer to a separate decision.
4. **Zero-deps is not the moat.** It's a tiebreaker. The actual moats
   are playbook-first portability, Telegram-as-remote-control, and
   file-protocol gates. The README buries all three.
5. **The highest-leverage adoption move is not in code.** It's a 60-90
   second demo video at the top of the README, a benefit-led tagline,
   and `uvx brr` instead of `pip install brr` as the lead install
   command. Code-side, dropping the "zero runtime dependencies" line
   from [`README.md`](../README.md), softening
   [`src/brr/AGENTS.md`](../src/brr/AGENTS.md), and adopting
   `requests` was accepted on 2026-05-22; the rest of the positioning
   recommendations remain research.

---

## Part 1 · The deps re-evaluation

### What zero-deps was actually protecting

The now-retired constraint showed up in three places:
[`README.md`](../README.md) ("Zero runtime dependencies. Stdlib Python
only."), [`src/brr/AGENTS.md`](../src/brr/AGENTS.md) Constraints ("Zero
runtime dependencies is a hard constraint — stdlib Python only."), and
[`pyproject.toml`](../pyproject.toml) (`dependencies = []`). It was
implicitly protecting four things:

| Goal | Reality check | Verdict |
|------|---------------|---------|
| Install friction | `pip install brr` is fast because there's nothing to download. `uv tool install brr-with-deps` is *also* fast (uv resolves and downloads in <1s). | Mostly resolved by `uv`. |
| No native compile | Avoids the "pip wants a C compiler" failure mode common with cryptography/pydantic-v1/numpy. | Still real, but solved by avoiding deps that require native compilation, not by zero-deps. |
| No venv pollution | "Drop it into system Python and it works." | False today — every Python adopter has venvs anyway; `pipx`/`uvx` ship the venv as part of the install. |
| Cross-platform / Windows | If there are no deps, there's no platform-specific binary to fail. | Orthogonal — brr's Windows blockers are signal handling, daemon model, and bash gate examples (see below). |
| Dev simplicity (`pip install -e .`) | True. Slightly less true with deps, but `uv pip install -e ".[dev]"` is still one line. | Marginal. |
| Fork-and-customise simplicity | Same as above. | Marginal. |

The strongest surviving rationale is *no native compile* — and that's a
"avoid native compilation requirements" rule, not a "stdlib only" rule.
The constraint as written is overshooting its real target.

### What zero-deps was not buying

- **Adoption.** Compare the actual landscape:
  - Aider: ~30 runtime deps, hundreds of thousands of installs.
  - Continue: hundreds of npm deps, top-of-VSCode-marketplace.
  - Cline: ships as a TypeScript VSCode extension.
  - Claude Code: closed-source npm package with deps.
  - gh-aw: Go binary as a gh extension, plus astro/vite docs, plus
    npm/js for scripts, plus a Go test suite. (Spelled out in
    [`research-brr-vs-gh-aw.md`](research-brr-vs-gh-aw.md) §3.8.)
  
  None of these tools won or lost on dep count. The tools winning have
  killer demos, integration density, and brand momentum.
- **Windows.** The repo is closer to Windows-clean than the
  "unsupported" framing implies. There is no `os.fork`, no `fcntl`, no
  `pty`. [`src/brr/envs/__init__.py`](../src/brr/envs/__init__.py)
  already does `getattr(os, "getuid", None)` checks. The real Windows
  blockers are:
  - Daemon supervision via SIGTERM/SIGINT against a PID file —
    Windows console-control events differ, and the `signal.SIGTERM`
    handler in [`daemon.py`](../src/brr/daemon.py) doesn't fire there
    in the same way.
  - Bind-mount path conventions for Docker (`C:\…` vs `/host_mnt/c/…`).
  - The bash example in [`src/brr/gates/README.md`](../src/brr/gates/README.md)
    and the `brr` launcher shell script at the repo root.
  
  All of those are architecture decisions, not dep decisions. Adopting
  `requests` does not move the Windows-support needle by one notch.
- **Tech-crowd appeal.** This is the inversion the framing was missing.
  To the systems-senior crowd ("I know C++ better than Python"),
  "stdlib only" reads as discipline. To the AI-coding-tools creator
  crowd (the actual buyer — see Part 2), "stdlib only" reads as either
  *invisible* (they don't notice it) or *quaint* (they notice and
  think "didn't get the memo"). Aider's README does not say "minimal
  dependencies"; it says "AI pair programming in your terminal".

### Per-candidate cost-benefit

#### `dulwich` → [`src/brr/gitops.py`](../src/brr/gitops.py)

**Verdict: pass. Net negative.**

dulwich is the obvious candidate because pure-Python git seems to fit
the "stdlib-spirit" half of the constraint. It does not survive
contact with brr's architecture.

- **No `git worktree` support in dulwich.**
  [`src/brr/worktree.py`](../src/brr/worktree.py) (212 LOC) shells out
  to `git worktree add`, `git worktree list --porcelain`, and
  `git worktree remove`. This module is load-bearing for every task —
  each task runs in `.brr/worktrees/<task-id>/`. dulwich cannot
  replace these calls. So the code that would migrate is roughly
  *half* of [`src/brr/gitops.py`](../src/brr/gitops.py); the rest
  (`check-ref-format`, `worktree list`, `branch -D`,
  `config branch.X.remote`, `remote get-url`) stays on subprocess.
- **Split paths add bug surface, not remove it.** Today brr has one
  "how do we talk to git" answer: shell out to the real git binary.
  Adopting dulwich means *two* answers, with subtle differences in
  ff-merge edge cases (e.g., what counts as "non-fast-forward" when
  refs have diverged-then-converged via shared commits).
  [`sync.py`](../src/brr/sync.py) and [`branching.py`](../src/brr/branching.py)
  are the most divergence-sensitive consumers; both are tested heavily
  against real git semantics.
- **Adopter audience already has git.** Every brr adopter has a git
  binary by definition (the project is git-rooted). Avoiding the git
  binary buys nothing.
- **dulwich's release cadence is slow and its maintainer surface is
  small.** Introducing it as a load-bearing dep would couple brr's
  release cadence to a project with non-overlapping priorities.

Counter-argument: in a hypothetical zero-dep, single-binary brr (think
"shiv" or "pex" pack), dulwich would matter because git wouldn't be
guaranteed. brr is not packaged that way, isn't trying to be, and
doesn't need to be — see Part 2 on `uvx` as the canonical install
path.

#### `requests` → [`src/brr/gates/*.py`](../src/brr/gates/)

**Verdict: take, once the deps stance is settled.**

- **LOC saved.** Across the three gates, an audit of the `urllib`
  glue (request construction, JSON encode, header juggling, the
  `_read_error_payload` / `_api_call` dance in
  [`src/brr/gates/telegram.py`](../src/brr/gates/telegram.py) and
  [`src/brr/gates/github/`](../src/brr/gates/github/)) suggests
  80-100 LOC removable. That's not transformative on a 9k-LOC
  codebase, but it's a clean reduction.
- **Better failure stderr.** The github gate has a custom
  `GitHubAPIError` class that hand-decodes `HTTPError.read()` to
  surface API messages; with `requests` this is `response.json()`.
  Telegram has a `_TelegramNotModified` exception that exists
  partially because `urllib.error.HTTPError` doesn't carry the parsed
  body cleanly. Both shrink.
- **Real cost.** Adds one dep, ~500 KB on disk, ~2 MB in deps tree
  (urllib3, charset_normalizer, idna, certifi). Audience does not
  care; install time still dominated by Python interpreter cold start.
- **Migration scope.** Mechanical and bounded — three files, well-
  tested via mocks at the API boundary already. Estimated half a day
  of work including tests.

#### Per-forge SDKs (PyGithub / githubkit / python-telegram-bot / slack_sdk)

**Verdict: defer to a separate decision; bigger lever, bigger cost.**

The LOC math is more interesting here than for `requests`:

| Gate | Current LOC | Estimated post-SDK | Notes |
|------|-------------|---------------------|-------|
| [`gates/github/`](../src/brr/gates/github/) | 1044 at the 2026-05-21 monolith snapshot | ~600-700 | githubkit or PyGithub absorb pagination, rate-limit headers, comment posting. The trigger logic stays brr-specific. |
| [`gates/telegram.py`](../src/brr/gates/telegram.py) | 593 | ~400-450 | python-telegram-bot owns getUpdates, sendMessage, editMessageText, parse_mode handling, HTTP retries. |
| [`gates/slack.py`](../src/brr/gates/slack.py) | 359 | ~250-300 | slack_sdk handles auth, conversations.history, chat.postMessage / chat.update. |

So ~700-900 LOC potentially gone, plus whole categories of
brr-specific bugs (parse_mode escaping, pagination off-by-ones,
rate-limit Retry-After parsing) become someone else's problem.

The costs are not trivial:

- **Opinionated deps with their own release cycles.** brr's currently
  bug-free github gate stops being self-contained; future Telegram
  Bot API changes ride on python-telegram-bot's release pace.
- **Capability locking.** If python-telegram-bot doesn't expose a
  feature brr needs (e.g., a Telegram bug-workaround), brr is stuck
  patching downstream or forking.
- **Async drift risk.** Both python-telegram-bot v20+ and slack_sdk's
  modern surface are async-first. The brr daemon is sync-threaded.
  Adopting these SDKs probably means writing sync-wrapper helpers,
  which partly defeats the LOC savings.
- **Image weight.** ~3-5 MB combined for the SDKs themselves. Still
  fine but compounding.

Recommendation: revisit after the positioning work in Part 2 lands.
The LOC savings are real but second-order. The positioning fix is
first-order for adoption.

#### Others surveyed and dismissed

- **`httpx`** — replaces both `requests` and `aiohttp` with one
  modern API. No async demand from brr yet; `requests` is the lower-
  friction choice. Reconsider only if brr ever grows async surface.
- **`click` / `typer`** — [`cli.py`](../src/brr/cli.py) is 122 lines.
  Savings are negligible relative to the dep cost. Pass.
- **`rich` / `textual`** — gate progress rendering already lives in
  [`run_progress.py`](../src/brr/run_progress.py) with Telegram-HTML
  and Slack-mrkdwn styles. There's no terminal-rendering surface
  worth `rich`-ifying. Pass.
- **`pydantic`** — `@dataclass` is fine for the small data classes
  brr uses (Task, RunContext, PublishPlan, SyncResult). Pass.
- **`GitPython`** — wraps subprocess, slow, opinionated. Brr already
  *is* the subprocess-wrapper layer. Pass.
- **`structlog` / `loguru`** — logging is mostly `print` to stderr
  today. The right time to add structured logging is when there's a
  consumer for it (brnrd, log shipping); not now.
- **`platformdirs`** — would help if brr started writing XDG paths
  (`~/.config/brr/`, `~/.local/state/brr/`) per
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §3, §5. Worth
  it *when* those features ship; not before.
- **`tomli` / `tomllib`** — already stdlib since 3.11; brr requires
  3.10 so this is a real wart for one feature. Drop if/when brr ever
  reads TOML config (it doesn't today; `.brr/config` is INI-ish).

### Stance recommendation (accepted for the runtime-deps slice)

- **Drop the "Zero runtime dependencies. Stdlib Python only." line from
  [`README.md`](../README.md)** — it's signalling a value the audience
  doesn't share.
- **Soften the Constraints bullet in
  [`src/brr/AGENTS.md`](../src/brr/AGENTS.md)** from a hard
  constraint to a soft preference: "Prefer stdlib; small deps that do
  not require native compilation are acceptable when they pay for
  themselves."
- **Allow `requests`** as the first adoption, on the cost-benefit
  above.
- **Hold per-forge SDKs** until the README / tagline / install
  surface is rebuilt. The LOC savings are real but they're not what's
  blocking adoption.

---

## Part 2 · What makes brr pop

### Audience honesty

The user's stated framing: "systems-senior, knows C++ better than
Python". That self-description is accurate, and it explains the
codebase aesthetic (clean stdlib, explicit subprocess, no magic). It is
*not* the buyer.

The buyer for AI coding tools today is the **AI-tool creator crowd**:

- Indie devs, vibe-coders, infra hackers.
- Mostly TS and Python; Mac primary, Linux secondary.
- Skews young, online, on X / Discord / Show HN.
- Optimises for: visible demos, integration density, "I tweeted my
  setup and got 200 likes" feedback loops, agentic-AI hype-cycle
  alignment.
- Reads READMEs in 30 seconds. Bails if the hero paragraph doesn't
  produce a felt benefit.

Competitive set (in rough order of mindshare today): Cursor, Claude
Code, Continue, Cline, Aider, Codex CLI, Devin, gh-aw, Goose, Aiden,
OpenDevin, dozens of weekly-launched contenders. brr is not really
competing with any one of them — but the *attention budget* is what's
contested.

This audience does not care about stdlib-only. They care about *what
they can do with brr in five minutes that they cannot do with anything
else*.

### What brr has that's underemphasized

#### Playbook-first portability

`AGENTS.md` + `kb/` works in Cursor, Claude Code, Codex CLI, Gemini
*without brr installed*. The README mentions this in passing
("Playbook only — `AGENTS.md` + `kb/` work with any AI tool, no brr
needed") but doesn't lead with it.

AGENTS.md is becoming a real standard — OpenAI's Codex, Anthropic's
Claude Code, and Cursor all support it natively as of early 2026. brr
is uniquely positioned to be *the* AGENTS.md tooling: the project that
ships the canonical playbook template, the kb pattern, the preflight
checks, and the daemon that operates against it.

This is a no-lock-in wedge nobody else has. Adopters keep the value
even if they throw brr away tomorrow. That's a much more compelling
on-ramp than "another agent runner".

#### Telegram-as-remote-control

This is the killer concrete hook. The user demo:

1. Set up a Telegram bot.
2. From your phone, message: *"fix the failing tests in auth/"*.
3. See a live progress card update in the chat as the agent works.
4. Receive the final response with a clickable branch URL.

This is *visceral*, *demo-able*, and *differentiated*. gh-aw can't do
it (requires GitHub). Cursor can't do it (editor-bound). Devin can do
something like it but only as a SaaS. brr does it on hardware you
own, with whatever AI CLI you prefer, in one daemon.

It is the strongest single message brr could send. The README mentions
Telegram twice, briefly. There is no demo video.

#### File-protocol gates

[`src/brr/gates/README.md`](../src/brr/gates/README.md) documents a
20-line bash gate example. This is a plugin story without a plugin
SDK — anything that can write a file can trigger an agent.

This appeals to the *infra hacker* slice of the creator crowd. "I
wired my OctoPrint to brr in 30 lines of bash and now my 3D printer
asks Claude to fix its config" is exactly the kind of demo that
goes viral.

#### Self-hosted by design

Counter-positions cleanly against:

- **gh-aw**: GitHub-locked. Brr runs anywhere.
- **Devin / Cognition**: SaaS. Brr is yours.
- **Cursor / Continue**: editor-bound. Brr is daemon-shaped, so any
  trigger reaches the agent.
- **Aider**: terminal-bound (no remote trigger). Brr is everywhere.

This is already part of brr's identity (see
[`subject-fleet-overlays.md`](subject-fleet-overlays.md)). The README
hints at it ("No database, no cloud, no lock-in") but doesn't make it
the lead.

### What's working against adoption

#### Tagline buries the hook

Current [`README.md`](../README.md):

> **brr**
>
> Structured AI agent playbook with persistent knowledge base and
> remote execution.

Three abstract nouns ("playbook", "knowledge base", "remote execution")
and zero verbs. Compare:

| Tool | Tagline |
|------|---------|
| Aider | AI pair programming in your terminal |
| Cursor | The AI Code Editor |
| Claude Code | Claude as a coding agent in your terminal |
| Cline | An autonomous coding agent right in your IDE |
| Continue | The leading open-source AI code assistant |
| gh-aw | Continuous AI (with a GitHub Actions sticker on the side) |

All concrete, all verbed, all immediately legible. Brr's tagline asks
the reader to translate three abstract concepts into a benefit before
they care.

Proposed direction:

> **brr** — Run your AI coding agent from Telegram, Slack, or GitHub.
> On your hardware.

That's specific, concrete, and tells you in one line what's
*different* about brr versus the other 50 agents announced this month.
Variants worth A/Bing: "Your AI coding agent, controlled from
anywhere"; "Telegram-controlled AI coding agents, on your box";
"AGENTS.md + remote control: agentic work without a SaaS."

#### README density

Current [`README.md`](../README.md) is ~170 lines, covering install,
quickstart, what-brr-creates, architecture, CLI table, extending,
environments, branching, docker auth, and dev setup. The unconverted
reader bails at line 30.

Target shape: ~80 lines, structured as

1. Tagline + GIF (visible without scroll).
2. One-paragraph "what it is".
3. 30-second quickstart (`uvx brr init` + first message).
4. Three bullets: playbook portability, remote control, self-hosted.
5. Link out to docs for everything else.

The existing density is fine for *current contributors*. It's hostile
to *prospective users*. Two surfaces, two audiences.

#### Install instructions signal "dated Python"

`pip install brr` first reads, to the TS-coming-from-Cursor audience,
as "2018 Python tool I'll have to fight with venvs to use". Lead with
`uvx brr` (run once without install) and `uv tool install brr` (global
install), then `pipx install brr`, then `pip install brr` as the
fallback for purists. uv is mainstream now — Cursor, Continue, and
most modern Python tools recommend it.

Bonus: a `Dockerfile`-based one-liner for the "I don't have Python on
my box" reader, leveraging the existing
[`src/brr/Dockerfile`](../src/brr/Dockerfile).

#### No demo artifact

This is the highest-leverage gap. Zero screenshots. Zero GIFs. Zero
video. The killer feature (Telegram progress card) is *invisible from
the landing page*.

A 60-90 second screencast solves this. Concrete shot-list:

1. `uvx brr init` in a fresh repo. (5s)
2. `brr setup telegram`, paste bot token, success. (10s)
3. `brr up` in one terminal, message bot from phone in
   picture-in-picture. (5s)
4. Live progress card edits in real time as the agent runs:
   `running` → `attempt 1/3` → `finalizing` → `pushed`. (40s)
5. Final response with branch URL. Click the URL — opens the diff in
   the forge. (10s)
6. Cut to text card: "Self-hosted. Works with Claude, Codex, Gemini.
   `pip install brr`." (5s)

GIF version (≤8 MB, autoplay-friendly) for in-README rendering; full
video for the landing page / Twitter / Show HN.

#### No public surface

No `brnrd.dev` or similar landing. No Discord. No public X presence. No
Show HN. No tech-channel demo video. Once "ready" is reached, these
*are* the leverage points. They should be planned now, not
retrofitted.

Minimum viable surface for launch:

- Static landing page (one HTML file, the demo video, three bullets,
  install command). Even GitHub Pages is fine.
- Discord with three channels (announcements, help, showcase). Costs
  nothing; signals "this is a thing".
- One Show HN post with the demo video. One X thread same.
- One YouTube/Twitch demo video by a known AI-coding-tool reviewer
  (Theo, Fireship-style). Reach out *after* the README is polished.

#### Name

"brr" is short, ownable, and brandable. It's also:

- Hard to SEO (cold-weather memes, BRR=base rate, etc.).
- Pronunciation-ambiguous (acronym? word? sound effect?).
- Says nothing about the product.

**Don't rename.** The brand investment is real and the name is
defensibly fine — short names with no intrinsic meaning have worked
(Stripe, Vercel, Fly, Anthropic). Compensate via:

- Strong tagline (see above) doing all the explanatory work.
- Sibling product name for the hosted service: `brnrd` at
  `brnrd.dev` — short, ownable, reads as a reflection-palindrome
  of brr+rrb (designed for an animated hero gif). The hosted
  service product name complements the OSS daemon name without
  duplicating it. Decided 2026-05-25 after surveying domain
  costs (`brr.run` ~$120/yr premium domain vs `brnrd.dev`
  ~$15/yr) and weighing the brand-asset value of the palindrome
  animation.
- Consistent "brr" lowercase everywhere as a brand signature.

#### CLI verb friction

The current CLI:

| Command | Issue |
|---------|-------|
| `brr auth telegram` | Leaks adapter internals — *auth* is a developer concept. |
| `brr bind telegram` | "Bind" reads as low-level. |
| `brr setup telegram` | Good — high-level, user-facing. |
| `brr up` / `brr down` | Good — daemon idiom (docker, ddev, supabase). |
| `brr init` | Standard. |
| `brr run` | Standard. |

Proposed:

- `brr connect telegram` — replaces `brr auth` + `brr bind` as the
  one-step happy path (already implemented under `brr setup`).
- Keep `brr auth` and `brr bind` as composable sub-flows for power
  users; just don't lead with them in docs.

Low priority. Worth doing before broader audience reaches the CLI; not
worth doing before the demo video lands.

### The killer demo, specified

Already in shot-list form above. The single most important artifact in
this entire research is that 60-90 second video. Ranking it #1 in the
moves list is deliberate.

### Concrete moves, ranked by leverage

Highest impact per unit effort first. The deps re-evaluation is #6,
which reflects the actual cost-benefit honestly.

1. **Demo GIF and full video** at the top of the README. Highest
   impact for cost; the demo *is* the pitch.
2. **Tagline rewrite + README compression.** ~80 lines, benefit-led,
   deep-link to docs for current bulk.
3. **Lead install with `uvx brr` / `uv tool install brr`**, then
   pipx, then pip. Signals modern Python.
4. **Subdomain + landing page.** Static, single-page; the demo video,
   three bullets, install command. GitHub Pages or Cloudflare Pages.
5. **Discord + first public artifact.** Show HN, X thread, demo on a
   tech channel — *after* #1-#4 land.
6. **Drop "zero runtime dependencies"** from
   [`README.md`](../README.md) and the Constraints bullet in
   [`src/brr/AGENTS.md`](../src/brr/AGENTS.md). Allow `requests`. This
   is the actionable output of the deps re-evaluation.
7. **Per-forge SDK migration.** After #1-#6 land. The positioning is
   the moat-builder; the LOC savings are gravy.
8. **CLI verb cleanup** (`brr connect`). Cosmetic but worth it before
   broader audience.

Moves 1-4 are content/positioning work and total maybe a week of
focused effort. Move 5 is calendar-bound (you need #1-#4 polished
first). Move 6 is half a day. Move 7 is two-three days. Move 8 is
half a day.

### What we are *not* recommending

- **Don't rebrand.** Name is fine; tagline carries the weight.
- **Don't ship a SaaS.** Self-hosted is part of the differentiation;
  hosted brnrd is a *separate* product line (see
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §6).
- **Don't add async.** No demand; threaded daemon model is working.
- **Don't add a plugin SDK.** File-protocol gates already are the
  plugin story.
- **Don't add Windows support yet.** Adopters skew Mac/Linux; Windows
  is a six-month project on its own (see daemon-model audit above).
  Note "Linux and macOS" explicitly in the README to set expectations.
- **Don't ship overlays before adoption proves out.** The fleet plan
  in [`subject-fleet-overlays.md`](subject-fleet-overlays.md) is
  paused for the right reason.

### Honest disclaimers

- "Creator crowd" is inferred from public competitive signals,
  Twitter/X chatter, Show HN rankings, and adjacent-tool README
  patterns — not from a survey of brr's actual users or prospective
  adopters.
- The tagline draft is one option of many; A/B testing it against
  variants is the right next step, not "ship the first draft".
- LOC savings estimates for SDK migration are scaled from the gate
  files' current shape and one engineer's intuition. They could be ±50%.
- "Drop zero-deps" is *positioning* advice, not *engineering* advice.
  The engineering recommendation is "allow `requests`; defer SDKs".
  The positioning recommendation is "stop bragging about a property
  the audience doesn't value".
- Demo video impact ("10x landing impact") is a heuristic, not a
  measurement. The real number could be 3x or 50x depending on the
  video quality and the channel.

## Lineage and read next

Read next, in order of decreasing certainty:

- **[`kb/decision-runtime-dependencies.md`](decision-runtime-dependencies.md)** —
  accepted on 2026-05-22. Bakes Part 1 into a binding stance:
  stdlib-preferred soft default, small deps that avoid native
  compilation requirements allowed when they pay for themselves,
  `requests` greenlit, SDK migrations as a separate later decision.
- **`kb/plan-readme-rework.md`** — operationalises moves 1-4 from
  Part 2 above: tagline, demo video shot-list, README skeleton,
  install-command reordering, deep-link targets.
- **`kb/plan-launch-surface.md`** — operationalises moves 4-5: domain,
  landing page, Discord, launch posts.

Adjacent context already in the kb:

- [`research-brr-vs-gh-aw.md`](research-brr-vs-gh-aw.md) — peer
  research; covers the substrate/transport/durability axis vs gh-aw
  and the market-fit assessment this page extends.
- [`notes-pondering-fleet.md`](notes-pondering-fleet.md) — strategic
  pondering around overlays, brnrd, fleet supervision, cross-platform.
- [`subject-fleet-overlays.md`](subject-fleet-overlays.md) — the
  paused fleet/overlay strand; informs Part 2's "don't ship overlays
  before adoption proves out".
- [`src/brr/AGENTS.md`](../src/brr/AGENTS.md) Constraints — the line
  this research recommends softening.
- [`README.md`](../README.md) — the surface this research recommends
  rebuilding around the demo video.
