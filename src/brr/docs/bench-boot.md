# Bench boot — scoring the reaction, not the perception

`brnrd bench boot` is rung ~2 of w-56 (`brnrd prompts replay` is rung 1 —
see `src/brr/replay.py`). It dispatches a real ad-hoc daemon run of *this
repo* per `(runner x prompts-dir)` pair, injects two scripted mid-run
steers and a scope-changing follow-up timed to tool-call boundaries, and
scores the run node from artefacts only — never from its transcript's
prose.

This document ships with `brnrd`. Users can override it per-repo by
dropping a file at `.brr/docs/bench-boot.md`.

## The rubric it answers to

The maintainer's own words (2026-08-15, on the boot lobotomy, verbatim):

> our tests shouldn't be about whether it detects it did the initial reads
> or not; the haiku model should be able to orient, send messages, pick a
> face, update a plan, fold a follow up, deliver the original requests and
> respect and complete all the mid-run steers, cut a bolt, etc. We don't
> care how it perceives the weave, we care about the reaction.

Every row this bench scores maps to one clause of that sentence, and every
row reads an artefact a run leaves behind rather than asking the run (or a
judge reading its transcript) whether it *understood* anything.

## Running it

```
brnrd bench boot \
  --runner claude-haiku [--runner codex-mini ...] \
  --prompts .brr/prompts-before [--prompts .brr/prompts-after ...] \
  --scenario bench/scenarios/make-brnrd-visible.yaml \
  --out /tmp/boot-bench-run
```

For every `(runner, prompts-dir)` pair this:

1. clones the current repo into a throwaway tree (`worktree.create_clone`,
   #746 — the same isolated-clone mechanism the daemon gives a live run),
   stages every `*.md` in `--prompts` into that clone's `.brr/prompts/`
   (the exact override directory `prompts.effective_prompt_path` already
   prefers over the bundled templates — never a fork of the assembler),
   and writes a minimal `.brr/config` pinning `shell=<runner>`;
2. spawns a real `python -m brr up` against it and injects the scenario's
   `ask` as the lead inbox event, then polls the dispatched run's own
   `boundaries.jsonl` (the per-tool-boundary transcript `hooks.py`
   already writes for an unrelated reason) and injects each steer / the
   follow-up the moment its `after_boundary` count is reached, with a
   hard `--timeout`;
3. scores the run node from artefacts once the run and every injected
   event have gone terminal — see "The rows" below;
4. writes `<out>/<runner>__<prompts-dir-name>.json` (the row table + the
   first row that failed) and, once every pair has run, `<out>/summary.md`
   (the whole matrix as one table).

Real quota, real runner auth: this is an operator/resident tool, same
class as `brnrd bench run`, deliberately not CI material.

## The rows

Read straight off `src/brr/bench.py`'s `score_boot` — this list exists so
a reader doesn't have to:

| row | artefact | what "passed" means |
| --- | --- | --- |
| `reply` | the lead event's response file | non-empty, and the run didn't time out |
| `face` | the run node's preserved `mood` file, first line | resolves via `emotes.lookup` |
| `steer_N` | `boundaries.jsonl` + the card-render timeline | the `## Plan`/`## Course` rows (`course.token`) changed, or a new commit landed, strictly after steer N's `after_boundary` |
| `plan_fold` | same evidence as `steer_N` | same check, keyed to the scenario's `follow_up` |
| `ask_complete` | the scenario's own `done_when` predicate | `file_contains` (a path exists and contains a string) or `commit_touches` (some commit touches a path) — mechanical, never a judgment call |
| `bolt` | `run-ledger.jsonl`'s `bolt` column | `== "accepted"` |

**First divergence** is the `name` of the first row (in the table order
above) that failed — the single number a reader compares across a matrix
without reading every detail string.

## Scenario format

YAML, one file per scenario, under `bench/scenarios/`:

```yaml
ask: "make brnrd more visible this week"

steers:
  - after_boundary: 3
    text: "one constraint I forgot: nothing that costs money"
  - after_boundary: 8
    text: "leave a one-line pointer in notes.md"

follow_up:
  after_boundary: 14
  text: "scope change: narrow this to the README's first screen"

done_when:
  file_contains:
    path: notes.md
    needle: README
  # or: commit_touches: README.md
```

`bench/scenarios/make-brnrd-visible.yaml` is the underspecified, real-ask
scenario named above. `bench/scenarios/smoke.yaml` is the trivial one a
haiku run finishes in well under five minutes — used by the CLI-wiring
tests (never dispatched for real there).

`after_boundary` counts **post-tool** boundaries only (`hooks.
PHASE_POST_TOOL`) — the boundary that fires once a tool call has actually
run and returned. A pre-tool boundary (about to run) isn't a reaction yet,
so it doesn't count.

## Open forks

**No daemon verb ties an inbox event's delivery to another run's boundary
count.** `brnrd bench boot` closes this by polling `boundaries.jsonl`
externally and calling `protocol.create_event` the moment a threshold
crosses — the same primitive a human or another run already uses to reach
the inbox, just driven from outside instead of told to the daemon. The
smallest verb that would remove the polling: an optional field on a
pending inbox event's own frontmatter (e.g. `deliver_after_boundary: N`)
that the daemon's own dispatch loop honors before it ever hands the event
to a run — deferring an already-queued event's delivery until the
addressed run's boundary count reaches N, the same shape `runner_policy:
propose` already gives an operator for a different kind of deferred
directive. Not built here: the spec for this work was mechanism-only, and
a daemon-side change is a different, larger review than a bench script.

**"Real ad-hoc run of this repo" was read as: clone the repo the bench is
invoked from, dispatch a fresh isolated daemon process against the clone**
(`worktree.create_clone` + a scratch `BRNRD_HOME`, mirroring the existing
`brnrd bench run` sandbox pattern) — not as reaching into whatever daemon
process happens to already be running for the operator's own dominion.
The alternative (dispatching through a live, already-running daemon)
would need one running to test against at all and would inject a
scripted probe into the operator's real conversation history; the chosen
reading keeps a boot-bench run fully disposable and reproducible without
a live daemon, at the cost of not exercising cross-process delivery to an
already-warm daemon.
