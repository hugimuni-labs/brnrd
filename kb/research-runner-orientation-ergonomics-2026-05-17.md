# Runner orientation ergonomics, 2026-05-17

Status: shipped on 2026-05-17 - review filed and the recommended
recent-conversation filtering slice implemented the same day.

Daemon-launched runner review after the orientation layering work and
the AGENTS.md trim / workspace-rule drift guard landed. This is a
current-run counterpart to the earlier runner and Cursor reviews:

- [`research-runner-orientation-ergonomics-2026-05-16.md`](research-runner-orientation-ergonomics-2026-05-16.md)
  - pre-slice daemon-runner view that led to the Mode block and
  run-context-as-recovery framing.
- [`research-cursor-orientation-ergonomics-followup-2026-05-16.md`](research-cursor-orientation-ergonomics-followup-2026-05-16.md)
  - external-session follow-up that drove the AGENTS.md trim and
  workspace-rule drift guard.
- [`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md)
  - plan-of-record for the four-layer model.

## Summary

The daemon-runner hot path is much better than the 2026-05-16 review
described. Before any tool call, the prompt answered the important
"where am I?" questions:

- stage: brr daemon task;
- source: GitHub;
- environment: Docker;
- delivery: stdout captured by brr, remote-chat path hygiene;
- branch: task branch from `main`, no auto-land target;
- runtime recovery: run context file only if the bundle is missing a
  needed detail.

That meant I did not need to open the generated run context file, and
I did not open `kb/log.md` during startup. The injected recent-activity
block satisfied the log-read requirement until I later needed an older,
targeted log entry to reconcile kb drift.

For a normal implementation task, the minimum useful orientation is now
close to: read `AGENTS.md`, read `kb/index.md`, check git status, then
read the relevant subject/code files. For this review, the extra reads
were earned: the task explicitly asked for redundancy analysis, so I
read the orientation plan, prior reviews, `run.md`, `prompts.py`, and
the relevant log entries.

## What improved

### 1. The Mode block pays for itself

The previous runner review had to infer stage from scattered prompt
shape and the generated run context. This run did not. The Mode block
made stage/source/environment/delivery/recovery explicit, and the
stage-vs-environment distinction was immediately clear.

That is the highest-value improvement in the whole arc. It removed an
entire class of first-minute confusion instead of asking the runner to
be disciplined while reading more context.

### 2. Recent Activity now avoids the full-log tax

The run prompt says the injected `Recent Activity (from kb/log.md)`
block plus the bundle's recent-conversation block satisfies AGENTS.md's
log startup step. I followed that. The full log was not part of startup.

I later read a narrow slice of `kb/log.md` only because the plan page and
the on-disk AGENTS.md disagreed about what had shipped. That is the
right failure mode: pay for older history when a concrete contradiction
appears, not on every task.

### 3. The run context file stayed cold

The bundle carried enough runtime state for this task. I never opened
the generated run context file. The "open only if a detail you need
isn't in this bundle" wording is doing real work.

## Friction found before the follow-up

### 1. Recent in this conversation was mostly operational noise

This bundle's `Recent in this conversation` section listed heartbeat,
artifact path, kb-maintenance, finalizing, done, push-started, and
push-done records from the prior task. None of those helped me answer
the user's ergonomics question.

The section is conceptually useful, but it should carry semantic
conversation memory, not daemon lifecycle chatter. For ordinary user
tasks, the useful records are:

- recent user events and their summaries;
- previous agent final replies or a compact summary of them;
- branch / commit facts when they affect follow-up routing.

The shipped follow-up filters that prompt surface to useful records:
events, task branch rows, final done / failed / conflict outcomes, and
push summaries. Progress updates, heartbeat records, response artifact
paths, and push-started rows are suppressed by default; if the filtered
set is empty, the whole section is omitted. Raw lifecycle records remain
available through the conversation log when a daemon-debugging task
explicitly needs them.

### 2. The AGENTS.md read is sometimes redundant, but safely so

In this host, the prompt already contained an injected AGENTS.md block
with the current `Revision:` marker, and the brr run prompt still told
me to read the repo-root file. I did read it. That cost one file read,
but it also verified the cached/injected copy was not stale.

Given the Cursor stale-rule finding from 2026-05-16, I would not remove
the on-disk read requirement yet. The safer eventual wording is
conditional: an injected AGENTS.md copy can satisfy the read only when
it carries a current revision marker and the host gives the runner a
trustworthy freshness signal. We do not have that signal today.

### 3. Delivery instructions are intentionally over-represented

The daemon prompt carries delivery rules in the run preamble and again
inside the Task Context Bundle. The surrounding runner environment also
has its own final-answer/file-link conventions. That is redundant, but
not all redundancy here is bad: stdout capture and remote path hygiene
are load-bearing, and one mistake drops or mangles the user-visible
answer.

The possible improvement is not "say this once." It is "make the bundle
the single full daemon delivery contract, and let the run preamble point
at it when a bundle is present." That probably means either a small
daemon-specific prompt preamble or a structured way for
`build_daemon_prompt()` to shorten the generic `run.md` delivery
section. This is lower priority than conversation filtering.

### 4. The orientation plan had become stale

`plan-agent-orientation-layering.md` still listed the AGENTS.md
canonical-home cleanup, workspace-rule staleness mitigation, and
cold-start sanity-check block as open. The log and on-disk AGENTS.md
show those shipped in commit `ddee9bd`.

That drift cost a targeted log read during this review. It is a small
example of why AGENTS.md's state-first kb rule matters: current-state
plan pages need to be updated when the work ships, not only the log.
This review's kb commit updates the plan and index so the next runner
does not re-derive it.

## Tooling verdict

I do not need more tools for this class of task. `rg`, `sed`, `git log`,
and the kb graph were enough. The missing capability is not a new local
tool; it is better curation of what brr already injects:

- make recent conversation semantic by default;
- keep raw lifecycle records available only for daemon-debugging tasks;
- keep the run context file cold unless the bundle lacks a needed fact.

## Shipped next slice

The follow-up implementation sliced conversation filtering around the
review's recommendation:

1. `prompts.format_recent_conversation()` omits purely mechanical
   lifecycle updates by default and renders the same semantic summary
   for the daemon bundle and generated run context.
2. `daemon._recent_conversation_for_prompt()` filters before passing
   recent records downstream, reads extra headroom so semantic records
   survive noisy concurrent tails, and still strips the in-flight task.
3. Prompt and daemon-conversation tests prove heartbeat / finalizing /
   push-started / response-artifact records disappear from ordinary
   daemon prompts while useful event, task, done, and push facts still
   render.

This removes the most visible remaining prompt noise without weakening
the runtime recovery story.

## Implementation-run notes

This follow-up run's bundle omitted `Recent in this conversation`
entirely, which is the desired shape when the filtered set has no
semantic records. Code inspection after rebasing onto current `main`
still found the formatter would render heartbeat / finalizing /
artifact-path records whenever they were present, so the filtering
slice was still needed.

Rebasing a PR branch under an auto-land task remains a separate branch
workflow wrinkle: brr's normal branch finalization and push path is
fast-forward-only, while a true PR-branch rebase requires a deliberate
force-with-lease publish story. This task keeps that as an operational
finding, not an implementation change; hiding force-push semantics
inside ordinary auto-land would be the wrong default.
