# Cursor orientation ergonomics — follow-up, 2026-05-16

Status: active — recommendations queued on
[`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md);
no AGENTS.md / kb edits applied yet pending operator selection.

Second-pass external Cursor session view, taken after slices 1 and 2
of the agent-orientation layering plan landed earlier the same day.
Pairs with the prior same-day reviews:

- [`research-cursor-orientation-ergonomics-2026-05-16.md`](research-cursor-orientation-ergonomics-2026-05-16.md)
  — first-pass review that prompted the layering plan.
- [`research-runner-orientation-ergonomics-2026-05-16.md`](research-runner-orientation-ergonomics-2026-05-16.md)
  — daemon-launched runner view that converged on the same direction.
- [`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md)
  — synthesis plan; slices 1 and 2 shipped in commits `9f0fd9e` and
  `363ae72`.

The point of this page is to record what is still painful for an
external Cursor agent **after** those slices shipped, so the
`Open follow-ups` block on the plan can be re-prioritised against
fresh evidence rather than against the pre-ship guess.

## TL;DR

Slices 1 and 2 work as designed when the playbook is read fresh.
Two new findings dominate this session:

1. **The workspace-rule injection of `AGENTS.md` is stale relative to
   the file on disk.** Cursor delivered the *pre-slice-2* AGENTS.md
   as my workspace rule (no "How to read this playbook", "Session
   startup" + "Work re-review" still split, log recipe still "last
   5–10 entries"). The on-disk file has the new shape. So the very
   thing the slice was meant to short-circuit — agents filtering
   daemon-only material on every read — silently still happens for
   Cursor sessions until the agent does a direct `Read` of the file
   and notices the drift.
2. **The user-flagged README ↔ AGENTS.md duplication is real and
   trimmable.** The first 11 lines of `AGENTS.md` (`# Project`
   block) and the 16-line `## Build and run` section restate
   material that lives canonically in `README.md` and
   `pyproject.toml`. Adopters carry these lines too via `brr init`,
   so the cost is paid in every adopter repo on every session.

Other findings are smaller polish on top of slices 1+2 and one
recommendation to **drop slice 3** (the snapshot regression test) as
not worth the byte-equality tax.

## Setup

- Tool: Cursor, Claude Opus 4.7, ad-hoc session.
- Repo state: clean working tree on `main` at `490508b` (the same
  point the previous review documented).
- Already in context at turn 1 (zero tool calls):
  - `AGENTS.md` injected as a workspace rule — **but**
    pre-slice-2 content (see Finding 1).
  - Git status snapshot — stale; showed three already-committed
    files as modified. Same friction the prior review called out;
    still not fixed because it's a Cursor-side cache issue.
  - One open file: `terminals/6.txt` — same git-push housekeeping
    session as last time. Verbatim ambient noise.
  - Skills list: nine skills surfaced
    (`babysit`, `canvas`, `create-hook`, `create-rule`,
    `create-skill`, `sdk`, `split-to-prs`, `statusline`,
    `update-cursor-settings`, plus `gitlab-ci-author`). None
    relevant to "do an ergonomics review on a stdlib Python
    research repo".
  - Recently viewed files: just `AGENTS.md` itself (cursor on line
    13). Useful but doesn't carry forward across sessions.
- Task in the user turn: do another ergonomics review; pay attention
  to redundancy, mode-specific data in AGENTS.md, and the fact that
  brr is running outside its managed environment.

## Orientation cost — what I actually had to fetch

After the (stale) AGENTS.md was already in context, my reading
batch:

| File | Lines | Used for | Honest verdict |
|------|-------|----------|----------------|
| `kb/research-cursor-orientation-ergonomics-2026-05-16.md` | 316 | Find out what the prior same-day review said so I don't re-derive | Earned every line; without it I'd have repeated half the work |
| `kb/index.md` | 183 | Subject hub map | Useful, on-budget |
| `kb/log.md` | 1755 | Recent activity | Read whole because the *workspace-rule* AGENTS.md said "last 5–10 entries" with no fetch recipe. The *on-disk* AGENTS.md has the `offset=-300` recipe in slice 2 — I just couldn't see it until the next step |
| `src/brr/AGENTS.md` | 476 | Compare on-disk to workspace-rule injection | This is when I noticed the drift |
| `kb/plan-agent-orientation-layering.md` | 152 | Plan-of-record for what shipped | Useful — confirmed slices 1+2 status |
| `kb/research-runner-orientation-ergonomics-2026-05-16.md` | 205 | The runner-side view that paired with the cursor view | Useful for the redundancy framing |
| `README.md` | 172 | Confirm the user's claim about overlap | Confirmed; see Finding 2 |
| `kb/repo-dive-in-map.md` (head only) | 120 of 1389 | Verify the cheap polish from slice 2 landed | The two-halves declaration works — I stopped after the orientation block |
| `src/brr/prompts/run.md` | 81 | Verify slice-1 changes match the docs | Confirmed |
| `terminals/6.txt` | 158 | Same as before — confirm it was unrelated noise | Same verdict |

Total fetched: ~3,700 lines plus the stale ~410-line workspace-rule
injection. Lower than the ~4,200 the prior review reported, mostly
because the dive-in-map's two-halves declaration let me stop early.
~25–30% used. The compounding lever now is **not** more reading
discipline; it is cutting the lines that are read but not used.

## Findings

### 1. Workspace-rule injection of `AGENTS.md` is stale

Concrete evidence from this session:

- The workspace-rule body delivered to my context started with
  `## Stewardship` immediately after the `# Project` block, no
  `## How to read this playbook` section. It contained
  `### Session startup` + `### Work re-review` (separate sections)
  and `Read kb/log.md for recent activity — the last 5-10 entries`.
- The on-disk `src/brr/AGENTS.md` has `## How to read this
  playbook` between Project and Stewardship, has `### Orientation`
  in Workflow, has `### When the brr daemon runs you` as an
  explicit daemon-only subsection, and gives the
  `Read kb/log.md offset=-300` and `grep '^## \[' kb/log.md | tail
  -10` recipes by name.
- `git log --oneline -5 src/brr/AGENTS.md` shows the slice-2
  refactor at `9f0fd9e` and the kb commit at `363ae72`. The rule
  injection predates both.

What this means in practice: the agent reads the rule (because the
rule is *already in context*; reading the file again costs a tool
call), follows the older "read kb/log.md" instruction without the
tail recipe, and either reads the whole 1755-line log or invents
the recipe themselves. The slice that was supposed to fix this is
silently invisible.

Cursor side, this is a workspace-rule cache invalidation gap. brr
can't ship code for it. It can mitigate cheaply:

- **Top-of-file last-modified marker on `AGENTS.md`** — a single
  short line just under the heading, e.g.
  `<!-- last structurally revised: 2026-05-16 — see kb/plan-agent-orientation-layering.md -->`,
  or a visible "Revision: …" line if HTML comments are stripped by
  some readers. If the workspace-rule version's marker is older
  than today's date, the agent has a clear cue to read the file
  fresh and trust it over the rule. If they match, the rule is
  authoritative and the agent skips the read. The marker is one
  line; it costs nothing per session.
- **Re-state the detection rule in "How to read this playbook"** —
  add a single line: *"If your runtime injects this file as a
  cached workspace rule, treat the rule as advisory and trust the
  on-disk file when in doubt."* The first time the agent reads
  AGENTS.md fresh they pick up the rule and act on it next session.

This is not a perfect fix. The first session after a structural
revision still pays the cost of an extra read. But it changes the
tax from "silent old behaviour" to "explicit drift detection",
which is the kind of thing the agent can act on.

### 2. README ↔ AGENTS.md elevator-pitch + install duplication

The user named this in the task. Confirmed and trimmable.

The first 11 lines of `AGENTS.md`:

```
# Project

brr is a structured AI agent playbook tool with remote execution. It produces
`AGENTS.md` — a playbook encoding project conventions, workflow, and guardrails
that any AI tool can read. A daemon layer adds remote execution via gates
(Telegram, Slack, GitHub) and keeps the host checkout fresh against the remote
between tasks. Pure stdlib Python (>=3.10), zero runtime dependencies.

This file is the source of truth for both brr's own development and the
playbook adopters receive when they run `brr init`. It lives at
`src/brr/AGENTS.md` and is symlinked from the repo root for tool conventions.
```

`README.md` lines 1–7 carry a slightly different paraphrase of the
same product summary; `README.md` line 18 carries
`Zero runtime dependencies. Stdlib Python only. No database, no
cloud, no lock-in.` The agent reads both; the agent does not need
either to *act* in this repo. The only operationally load-bearing
fact is "this file lives at `src/brr/AGENTS.md`, the root is a
symlink" — three lines max.

Same shape on `## Build and run`: 16 lines of pip/uv/CLI invocations
that the agent can recover from `pyproject.toml` and `README.md` →
Development.

A trimmed AGENTS.md `# Project` + `## Build and run` could be:

```markdown
# Project

This file is the playbook every AI tool reads in this repo. It lives
at `src/brr/AGENTS.md`; the root `AGENTS.md` is a symlink. See
`README.md` if you need the user-facing product overview.

## Build and run

Editable install + tests:
`pip install -e ".[dev]"` then `pytest`. See `README.md` →
Development for variants.
```

That cuts ~25 lines from every adopter's playbook on every session.
Adopters' `# Project` and `## Build and run` are rewritten per repo
by `brr init`, so the trim is a template change for brr itself plus
a lighter default for adopters.

### 3. Workflow → Code guidelines bullet drift

The `## Code guidelines` section ends with:

```
- Before editing a module, read the file you're changing along with its
  obvious callers and the utilities it relies on, unless it the task is real
  straightforward. "Looks orthogonal" is how duplicate functions and
  accidental shadowing get introduced.
```

This is a Stewardship-level discipline, not a code-style rule. It
also has a minor typo (`unless it the task is`). Suggested fix:
move the rule into Stewardship (compress to a one-line bullet
under "Slash code, tests, and pages…"), strip the typo. Saves the
section drift and a re-read tax.

### 4. Stewardship section is right but on the heavy side

Six bullets / paragraphs across ~20 lines. The actionable kernel
for an agent is two ideas:

1. Reason from first principles before changing behaviour or
   design.
2. **If the request contradicts existing code, decisions, designs,
   or guardrails, surface the contradiction *before* resolving it.**

The rest is supporting prose. Not wrong, but compressible to ~10
lines without losing teeth. Lower priority than findings 1–3.

### 5. Self-review #5 overlaps Knowledge base → Health checks

`Self-review` step 5 ("If you created or removed kb pages, check
that `kb/index.md` is current and the new pages are linked from a
subject hub or peer.") restates Health checks bullet 1 / 6
("Pages referenced in `index.md` that no longer exist (or vice
versa)" + "Orphans"). And the deterministic preflight checks both
already.

Tiny trim: collapse Self-review #5 to "Verify kb health checks pass
for any kb pages you touched" with a link.

### 6. Cold-start sanity check for ad-hoc agents

Findings 1, plus the unfixable Cursor-side issues from the prior
review, suggest a small *new* block in AGENTS.md → "How to read
this playbook" → ad-hoc-agent stage. Not preachy, just operational:

```markdown
**Sanity-check the runtime before trusting context.** External
hosts inject ambient state that may not match this task:

- Workspace-rule copies of this playbook can lag the on-disk file
  — trust the file when in doubt.
- Git status snapshots may be stale — re-run `git status` before
  reasoning about uncommitted work.
- Open editor terminals, recently viewed files, surfaced "skills"
  may be unrelated to the user's task. Use them only when the
  task references them.
```

Lives only in the ad-hoc stage block; daemon and kb-maintenance
stages don't have these problems. Three bullets, cheap.

### 7. `kb/log.md` length is fine for now; watch the threshold

1755 lines, 67 entries. Slice 2's tail-fetch recipe works: an agent
following the playbook reads ~300 lines of the most recent
material. The cheap option is paying. The medium option
(`kb/log-recent.md` mirror) and heavy option (split by
quarter/month) are not yet earning their keep.

Watch threshold: when `kb/log.md` crosses ~3000 lines / ~100
entries the recipe starts losing, because individual entries can
run 80+ lines and the tail-300 window covers fewer entries. At
that point a quarterly split (`kb/log-archive-2026-Q1.md` etc.)
is the lowest-friction next step.

### 8. `kb/repo-dive-in-map.md` two-halves declaration earns its keep

The cheap polish from slice 2 (the "How to read this page"
declaration distinguishing orientation from reference) worked. I
stopped reading after the orientation block (line ~120 of 1389)
because it pointed at the snapshot and named the rest as
deep-reference. The deeper split (orientation vs reference as
separate pages, or per-ring split) listed under open follow-ups
on the plan is not pulling its weight as a priority — the cheap
polish is enough today. Mark deferred indefinitely on the plan,
revisit only if a future review surfaces an agent over-reading
the map.

### 9. Slice 3 (snapshot regression test) — recommend dropping

The plan's slice 3 proposes a snapshot test for a realistic full
daemon prompt + run context, to catch duplication / drift between
the bundle and the run-context file.

`tests/test_prompts.py::TestDaemonModeGuardrails` already pins
the load-bearing anchors for the Mode block, the
"injected-extract-satisfies-the-step" claim, and the run-context
recovery framing. A snapshot would freeze ergonomically-good prose
into byte equality and become a tax on every prompt edit (and
prompt edits are the *cheap* iteration loop).

The honest cost / benefit:

- Cost: every prompt copy-edit churns the snapshot, agents have to
  re-bless it, the diff in PRs becomes long noise.
- Benefit: catches new duplication and stale claims. The
  guardrail tests already do this for the load-bearing claims.

Recommend marking slice 3 as **rejected** on the plan with a
breadcrumb to this finding.

### 10. Cursor-side wishlist (no change from prior review)

The prior review filed three Cursor-side observations as recorded
friction:

- Timestamp / mark-stale the git-status snapshot.
- Tag `terminals/*.txt` as ambient editor state.
- Declare the runtime mode in the system prompt so the agent
  doesn't have to infer it.

This session reproduces all three. Add one more for completeness:

- **Workspace-rule cache invalidation on file change.** Cursor's
  workspace-rule injection drifts behind the on-disk file. Cache
  by content hash, not by registration. Until that ships, brr's
  top-of-file marker (Finding 1) is the available defence.
- **Skill filtering by task domain.** Surfacing nine skills with
  no prefilter on a stdlib-Python research repo is a small but
  recurring tax. Even a cheap filter ("don't surface
  `gitlab-ci-author` if no GitLab CI files exist") would help.

Recorded for future reviewers; not brr's to ship.

## What changed since the prior review

| Recommendation in prior review | Status now |
|--------------------------------|------------|
| Make AGENTS.md mode-aware up front | **Shipped** in slice 2 (`9f0fd9e`). Working when read fresh; partly invisible when delivered as a stale workspace rule (Finding 1). |
| Trim `kb/log.md` reading cost (cheap option) | **Shipped** in slice 2 — Orientation gives the tail-fetch recipe. |
| Slim or split the dive-in-map | **Cheap polish shipped**; deeper split rated low-priority by Finding 8. |
| Make redundant facts authoritative in one place each | **Open**. Finding 2 is the most concrete instance and the user explicitly named it. |
| Add explicit external-mode hints | **Shipped** in slice 2 ("How to read this playbook" stage block). Finding 6 proposes a small extension for runtime sanity checks. |
| Cursor-side wishlist | **Open** (not brr's). Reproduced; one new item appended (Finding 10). |

## Suggested next moves

In rough order of leverage. None of these are large; all four AGENTS.md
edits could land in one commit on `main`.

1. **AGENTS.md trim** — Findings 2 (project + build-and-run), 3
   (move the new code-guideline bullet into Stewardship + fix typo),
   5 (compress self-review #5). One commit, ~30-line net deletion.
2. **AGENTS.md mitigation for workspace-rule staleness** —
   Finding 1: top-of-file `Revision:` line and a one-line note in
   the ad-hoc-agent stage block.
3. **AGENTS.md cold-start sanity-check block** — Finding 6: three
   bullets in the ad-hoc stage block. Independent of (2); same
   commit is fine.
4. **Plan housekeeping** — mark slice 3 rejected with a breadcrumb
   (Finding 9), downgrade the dive-in-map deep split (Finding 8),
   note that the canonical-home cleanup follow-up has a concrete
   first target (Finding 2 / item 1 above).
5. **Stewardship compression** (Finding 4) — only if you want a
   second pass; lower leverage.

If the operator wants only the user-flagged item addressed, that
is item 1 of (1) alone — strip the elevator pitch and shrink Build
and run to a pointer.

## Out of scope (recorded so future passes don't re-derive)

- Replacing `AGENTS.md` with per-mode files. Tested mentally and
  rejected last review and the previous one before it.
  Mode-awareness inside the file is the right shape; staleness in
  caches is a *delivery* problem, not a *shape* problem.
- Pre-injecting more orientation pages into Cursor's workspace
  rules. Same answer as before: rule budget is finite and adding
  more paths multiplies the cache-staleness surface in Finding 1.
- A `brr docs orient` CLI. Same answer as previous reviews.
