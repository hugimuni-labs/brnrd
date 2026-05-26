# Plan: `brr kb` subcommand — kb health for users and agents

**Status: accepted 2026-05-26** (locked in PR #40 MR review;
implementation feedback may reshape — treat the per-subcommand
output sketches as a working spine, not a contract). Adds
`brr kb` as a top-level verb in the noun-first CLI taxonomy, exposing
kb-graph health + structural stats + per-page introspection to
both the human user (who needs to know "what should I act on?")
and to non-brr-operated agents (Cursor, Codex CLI, Claude Code —
the agents working on the project outside a `brr run` session).
Addresses [issue #41](https://github.com/Gurio/brr/issues/41) and
the long-standing wart that the kb is half the project's value
prop but has no first-class read surface from the CLI. Companion
to [`decision-cli-shape.md`](decision-cli-shape.md) (where `brr
kb` is added as the 7th top-level verb), to
[`subject-kb.md`](subject-kb.md) (the canonical kb hub that this
plan's verbs surface state from), and to
[`AGENTS.md`](../AGENTS.md) → "Knowledge base" (the maintenance
contract `brr kb check` and `brr kb status` mechanise).

## Why this exists

Three populations consume kb state today, in three different
ways, with two of them poorly served:

| Consumer | How they read kb today | Gap |
|----------|------------------------|------|
| **Human user** | Opens `kb/index.md`, scrolls, grep | No "what needs my attention?" signal. The `Status: proposed, not yet accepted` markers carry meaning (the user should review and accept / reject) but are invisible without scanning every plan / design / decision page. |
| **Brr-operated agent** (inside `brr run`) | Daemon injects `kb/index.md` + `kb/log.md` tail + relevant subject hubs into the task context bundle | Works. The orientation context is automatic; agent doesn't need a separate query surface. |
| **Non-brr agent** (Cursor, Codex CLI, Claude Code editing the repo directly) | Reads the same files; relies on AGENTS.md → "Health checks" to remember what to look for | No mechanical health summary. The agent has to read AGENTS.md, then walk `kb/` page by page looking for orphans / aspirational drift / status markers. Slow, error-prone, and impossible to do well in a short session. |

The brr-operated agent has the easiest path because the daemon
does the orientation work upfront. The other two consumers need a
shared, callable surface. The hypothesis: same CLI verbs serve
both (humans read the human output; agents read `--json`). One
surface to build, one to document, one to keep in sync with
AGENTS.md's maintenance contract.

## What this is NOT

- **Not a separate agent-only tool.** Adding `brrkb` / `brr-kb`
  as a sibling binary would pollute the install surface and
  split the docs. Agents are users when they shell out; they
  should hit the same verbs.
- **Not a daemon-side RPC.** Talking to the daemon for kb stats
  would couple the kb (which exists independent of a running
  daemon) to a process that may not be up. Filesystem-only is
  the right grain — kb is a directory of markdown.
- **Not a kb editor.** Mutation lives in the existing
  `kb-maintenance` prompt stage and in normal markdown edits.
  This plan is read-only; mutations go through the LLM
  redundancy pass per
  [`decision-kb-shape.md`](decision-kb-shape.md).

## Decision

Add `brr kb` as the **7th top-level verb** in the CLI shape, with
six sub-verbs:

```
brr kb status                       # one-screen health summary
brr kb pages [filters]              # list pages with status markers
  --proposed | --accepted | --superseded | --abandoned
  --untouched-since 30d
  --orphaned                        # not referenced from kb/index.md
                                    # or any other page
brr kb proposed                     # shortcut for `brr kb pages --proposed`
                                    # (the top user signal)
brr kb log [--since <date>]         # tail of kb/log.md, filterable
brr kb check                        # graph + status-marker validation
brr kb doc <page>                   # per-page summary (status, lineage,
                                    # links from/to, age, word count)
```

All sub-verbs support `--json` for machine consumption. Default
output is human-readable.

7 top-level verbs instead of 6 bends the "minimal" promise from
[`decision-cli-shape.md`](decision-cli-shape.md) by one. kb
deserves the top-level noun because (a) it's half the project's
identity (the methodology), (b) the verbs are domain-distinct
from `config` (introspection of a different layer), and (c) the
agent audience uses these verbs often enough that a deep path
(`brr config kb …`) would be friction every time.

## The human signal: `brr kb status` + `brr kb proposed`

Default `brr kb status` output (target: fits in a terminal
without scroll):

```
$ brr kb status
brr kb status — 2026-05-25 21:45 local

Pages:          47 total
                  37 active / shipped / accepted
                  6 proposed, not yet accepted
                  3 superseded (with successor links)
                  1 abandoned (with breadcrumb)

Activity:       9 entries in kb/log.md since 2026-05-22
                Last entry: 2026-05-25 pass 4 follow-up

Health:         ✓ all pages reachable from kb/index.md
                ✓ all status markers parse
                ⚠ 2 pages untouched > 30 days but not marked
                  superseded:
                    kb/plan-overlays.md (blocked since 2026-05-10)
                    kb/llm-wiki.md (reference; OK if intentional)
                ⚠ 6 pages need your review (status: proposed):
                    kb/decision-cli-shape.md
                    kb/design-billing.md
                    kb/design-brnrd-protocol.md
                    kb/decision-pricing-shape.md
                    kb/decision-monorepo-structure.md
                    kb/design-config-layout.md

Next step:      brr kb proposed   # review pending decisions
                brr kb check      # full graph validation
```

The `⚠ 6 pages need your review` line is the **load-bearing
human signal**. Today, knowing this requires walking every plan /
design / decision page. The status command surfaces the count and
the list in one tap.

`brr kb proposed` is the shortcut to skip the summary and just
get the list:

```
$ brr kb proposed
6 pages marked "Status: proposed, not yet accepted":

  kb/decision-cli-shape.md      proposed 2026-05-25  CLI verb shape after managed-mode reshape
  kb/design-billing.md          proposed 2026-05-25  Credit wallet + Stripe + EU compliance
  kb/design-brnrd-protocol.md   proposed 2026-05-25  Wire format daemon↔brnrd
  kb/decision-pricing-shape.md  proposed 2026-05-25  Free dispatcher + paid managed compute
  kb/decision-monorepo-structure.md proposed 2026-05-25  Single brr package + extras
  kb/design-config-layout.md    proposed 2026-05-25  Three scopes: project / local / account

Run `brr kb doc <page>` for per-page summaries, or open them
directly. Update the Status: line and add an Accepted-on: date
when you decide.
```

This is the surface that makes "kb status has meaningful info to
the user" actionable.

## The agent signal: `--json` everywhere + `brr kb check`

Every verb supports `--json`. Schemas are stable enough for
agents to consume without LLM parsing:

```
$ brr kb status --json
{
  "generated_at": "2026-05-25T21:45:12+02:00",
  "counts": {
    "total": 47, "active": 37, "proposed": 6, "superseded": 3,
    "abandoned": 1
  },
  "activity": {
    "log_entries_last_7d": 9,
    "last_entry": "2026-05-25 pass 4 follow-up"
  },
  "health": {
    "reachable_from_index": true,
    "status_markers_parse": true,
    "untouched_warnings": [
      {"page": "kb/plan-overlays.md", "marker": "blocked", "since": "2026-05-10"},
      {"page": "kb/llm-wiki.md", "marker": null, "since": "2026-04-12"}
    ],
    "proposed_pages": [
      {"page": "kb/decision-cli-shape.md", "title": "...", "proposed_on": "2026-05-25"},
      ...
    ]
  }
}
```

`brr kb check` is the **agent's post-edit validation**:

```
$ brr kb check
✓ all 47 pages reachable from kb/index.md or a subject hub
✓ all 6 status-marker lines parse cleanly
✗ 1 broken cross-reference:
    kb/design-billing.md:312
      → [`design-future-renewals.md`](design-future-renewals.md)
        (target file does not exist)
✗ 1 aspirational-drift smell:
    kb/subject-envs.md:88 advertises "supports devcontainer env"
    but `src/brr/envs/__init__.py` does not register it.
    Either trim the prose or move the claim to a `Status:
    designed` / `Status: in flight` marker.

Exit code: 1 (errors found)
```

Exit code is non-zero on `✗` errors (broken links, parse
failures); zero on warnings only (`⚠`). Lets non-brr agents wire
`brr kb check` into a pre-commit / pre-push step.

`--json` mode returns structured findings:

```
$ brr kb check --json
{
  "errors": [
    {
      "kind": "broken_link",
      "page": "kb/design-billing.md",
      "line": 312,
      "target": "design-future-renewals.md"
    },
    {
      "kind": "aspirational_drift",
      "page": "kb/subject-envs.md",
      "line": 88,
      "claim": "supports devcontainer env",
      "evidence": "src/brr/envs/__init__.py does not register it"
    }
  ],
  "warnings": [...]
}
```

Aspirational-drift detection is heuristic (greps for "supports",
"pluggable", "configurable" in subject pages; cross-references
the claim against the named code surface). False positives
acceptable — the check is a prompt to look, not a hard gate.

## Where the AGENTS.md contract lands

Today's AGENTS.md → "Health checks" lists what to scan for. With
the kb subcommand in place, it becomes a one-line pointer:

```
Health checks: run `brr kb check` (machine: --json). Surfaces
broken cross-references, missing lifecycle markers, orphan
pages, aspirational-drift smells. For the underlying contract
on what each finding means, see [`subject-kb.md`](kb/subject-kb.md).
```

This is the change AGENTS.md gets: replace the implicit
"remember to look for X, Y, Z" with the explicit "run this
command, fix what it surfaces."

For the **session-start orientation** (Workflow → Orientation in
AGENTS.md), `brr kb status` is the one-tap version of step 1
("read kb/index.md"). The agent still reads the actual subject
hubs / pages relevant to the task, but the status check tells
them what the global state is (proposed-pending decisions, recent
activity, drift warnings) in one call instead of N reads.

## What `brr kb check` checks (the full list)

1. **Reachability.** Every page (except `index.md`, `log.md`,
   subject hubs themselves) must have at least one inbound link
   from `index.md` or another non-index page. Orphans are flagged.
2. **Cross-reference integrity.** Every `[...](...)` link to a
   relative `kb/*` path resolves to a file that exists.
3. **Status-marker syntax.** `plan-*` / `design-*` /
   `decision-*` pages should have a top-of-page `Status: ...`
   line matching the grammar from
   [`AGENTS.md`](../AGENTS.md) → "Lifecycle markers". Pages
   missing the line OR pages whose status doesn't parse are
   flagged.
4. **Superseded link integrity.** Pages marked
   `Status: superseded by <link>` must have a working link to
   the successor.
5. **Stale-active warnings.** Pages marked `Status: active` /
   `proposed` untouched for > 30 days are warned about (not
   errors) — common cause is the page outliving its decision.
6. **Aspirational-drift smells.** Heuristic. Greps subject hubs
   for capability assertions ("supports X", "pluggable", "X is
   shipped") and checks whether the named code surface
   registers / implements the claim. False positives acceptable.
7. **Sibling drift.** Heuristic. Subject hubs claim something
   (e.g. "envs: host / worktree / docker"), and the env design
   page claims something different (e.g. adds `ssh`). Sibling
   pages should agree on lists, labels, backend names. Flagged
   for manual review.

Each check has a stable `kind` field in `--json` mode so agents
can filter to the subset they want to act on.

## Implementation slice

~400-600 LOC total. All in `src/brr/kb/` (new module):

- `src/brr/kb/__init__.py` — public API: `KbState`, `Page`,
  `check()`, `status()`, `pages()`, `doc()`, `log_tail()`.
- `src/brr/kb/parse.py` — markdown parsing (page front matter,
  status markers, cross-references). Stdlib-only; `markdown` /
  `mistune` not needed (we control the format and only need to
  extract a few patterns).
- `src/brr/kb/graph.py` — build the reachability graph from
  `index.md` + cross-references.
- `src/brr/kb/check.py` — the seven checks above, each as a
  small function returning `Findings`.
- `src/brr/kb/cli.py` — argparse wiring for `brr kb status |
  pages | proposed | log | check | doc`.
- Tests in `tests/test_kb_cli.py` covering parse, graph,
  individual checks, JSON output stability.

Wires into `src/brr/cli/__init__.py` via the same noun-first
sub-parser pattern the other verbs use. No daemon dependency.

## Done definition

- All six sub-verbs implemented with stable `--json` schemas.
- AGENTS.md → "Health checks" updated to point at `brr kb
  check`.
- Brr daemon-side context-injection updated to include
  `brr kb status --json` output in the Task Context Bundle
  (so brr-operated agents share the same surface non-brr
  agents see).
- Tests cover the seven checks against a fixture kb tree.
- README → Quickstart mentions `brr kb status` once with a
  one-liner ("see what needs your attention").

## Out of scope

- **kb mutation.** Adding / removing / renaming pages is done
  by editing markdown; the kb-maintenance prompt stage is the
  LLM-driven mutation path. `brr kb` is read-only.
- **Diffing kb state across commits.** "What changed in the kb
  since v0.5?" is interesting but not a launch need; git log
  on `kb/` is the fallback.
- **Cross-repo kb federation.** Out of brr's scope — each repo
  has its own `kb/`.
- **A dashboard view.** Useful eventually (the
  `plan-brnrd-dashboard-mvp.md` could surface kb status per
  project); not a launch need.

## Open questions

- **Where do `brr kb` outputs land for brr-operated agents?**
  Two paths: (a) inject `brr kb status --json` output into the
  Task Context Bundle's run-context file; (b) make the runner
  prompt mention "you can call `brr kb status` if you need
  current state". (a) is upfront context (cheap; reliable);
  (b) is on-demand (less context bloat; trust the agent to
  call). Probably (a) at launch, evaluate (b) per token budget
  in real runs.
- **Should `brr kb status` warn when `kb/log.md` is empty for
  the session?** Possible signal that the session forgot the
  log entry. Easy to add later if it pays off.
- **Stale-untouched threshold.** 30 days is a guess. Per-page
  override via a frontmatter field could come later if the
  default is wrong.
- **Aspirational-drift heuristic.** Starts cheap (grep +
  cross-reference). May want LLM-assisted version later for
  pages where the pattern is less mechanical (e.g. a subject
  hub describing a workflow). LLM version is out of scope at
  launch — keep the heuristic small and the false-positive rate
  honest.

## Estimate

~1 week of focused work for the full slice (parse + graph + 7
checks + 6 sub-verbs + tests + AGENTS.md rewrite + daemon-side
context-injection update). Ships in a single PR; no cross-page
coordination needed beyond AGENTS.md.

## Read next

1. [`decision-cli-shape.md`](decision-cli-shape.md) for the
   placement of `brr kb` as the 7th top-level verb in the
   noun-first taxonomy.
2. [`subject-kb.md`](subject-kb.md) for the canonical kb hub —
   the contract `brr kb check` mechanises.
3. [`decision-kb-shape.md`](decision-kb-shape.md) for the four
   memory layers, graph topology, lifecycle markers, and the
   LLM-redundancy-pass model for mutation.
4. [`AGENTS.md`](../AGENTS.md) → "Knowledge base" → "Health
   checks" for the contract this plan mechanises.
5. [issue #41](https://github.com/Gurio/brr/issues/41) — the
   GitHub-side tracker for this work.

## Lineage

- 2026-05-25 — drafted as part of the pass-4 follow-up second
  wave (the user re-raised the "kb has no read surface for
  non-brr agents" pain point flagged in #41 alongside the
  three-scope config and cross-platform daemoning work).
  Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  (pass-4 follow-up — second wave). Replaces the implicit
  "talk to the daemon somehow" placeholder and the
  considered-and-rejected separate-tool option.
