# Agent ergonomics — observability design

Status: active — proposed 2026-05-27 in response to the docker-runner
ergonomics pass earlier the same day, where three independent agent
reviews surfaced the same friction (stale image / missing pytest / gh
auth confusion) and the only thing that aggregated the signal was a
human pasting the reviews into a chat for analysis. **Probe slice
shipped 2026-06-02** (`src/brr/ergonomics/`): the deterministic probe
layer, the shipped `NullErgoProxy` / `LogErgoProxy` /
`LocalErgoProxy`, the owner-aware resolver, the local JSONL store, and
the `brr ergonomics` CLI. Telemetry, `BrnrdErgoProxy`, and the
dashboard views remain designed-not-built below.

Two design refinements landed 2026-06-03 (see "Ownership decides
routing" and "What probes are for — the vantage rule"): routing is
driven by **run ownership** (a launcher-stamped property), not a
free-form config knob; and the probe set is bounded by a **vantage
rule** so the harness doesn't accrete static checks that belong to the
agent. The shipped default is now a quiet daemon-log of probe findings
for user-owned runs (token-free), not silence.

**The `response` mode (visible reflection footer) was retired
2026-06-08** with the resident-agent reshape (see
[`design-agent-dominion.md`](design-agent-dominion.md)). The user knob
is now `off|log|local`; an `ergonomics=response` config value collapses
to `log`, and `prompts/self-review.md` + `prompts.reflection_enabled`
are gone. The resident folds runtime friction into its own **dominion
journal** — the pain-evaluation loop in its playbook — rather than a
reply footer, so the agent's reflection has a durable home instead of a
one-shot footer. This retires only the *visible* reflection surface;
the deferred hidden-reflection-capture pipeline (the `reflection`
`Record` kind, sampling, splitter) described below is unaffected, and
when it lands it feeds the dominion/ergonomics back-channel, not a reply.

Companion to:

- [`design-environment-shaping.md`](design-environment-shaping.md) — the
  observe → remember → shape **loop** that consumes this back-channel's
  observations and routes them to action (salience triage, layered-control
  routing, action rungs). This design is that loop's observation layer.
- [`subject-managed-mode.md`](subject-managed-mode.md) — the tenancy
  split (self-hosted brr vs hosted brnrd) that shapes who needs to
  see ergonomics data and where it routes.
- [`design-brnrd-protocol.md`](design-brnrd-protocol.md) — the wire
  format between daemons and brnrd; the ergo proxy for managed
  tenants rides on it.
- [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md) — the
  dashboard's view inventory; an ergonomics surface joins the eight
  views when this design ships.
- [`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md) —
  the *forward* channel (how context reaches the agent). This design
  is the *back* channel.
- [`research-runner-orientation-ergonomics-2026-05-16.md`](research-runner-orientation-ergonomics-2026-05-16.md),
  [`research-runner-context-ergonomics-2026-05-09.md`](research-runner-context-ergonomics-2026-05-09.md),
  [`research-cursor-orientation-ergonomics-2026-05-16.md`](research-cursor-orientation-ergonomics-2026-05-16.md),
  and the 2026-05-27 log entry — the source material that
  motivated this design.

## Problem this design answered

The baseline this design replaced was a single knob,
`runner.self_review`, that injects
[`src/brr/prompts/self-review.md`](../src/brr/prompts/self-review.md)
into runner prompts. That prompt asks the agent to end its stdout
with a free-text **Ergonomics review:** footer covering orientation,
tooling, and branch metadata. The daemon does nothing with the
footer beyond shipping it as part of the response file to the gate.
(Removed 2026-06-03: the knob is gone — the same "review in the reply"
behaviour is now `ergonomics=response`, skippable and owner-gated. See
"Ownership decides routing".)

That baseline had six concrete failure modes:

1. **Signal lives in user-visible output.** The footer rides in the
   same response the user reads. For real users this is chat
   pollution; for managed-mode (brnrd) it would be doubly wrong
   because the platform operator — not the user — is the one who
   needs to act on the data.
2. **Lossy by default.** Nothing parses, stores, or routes the
   footer anywhere. The signal exists only as long as the user
   bothers to read and remember it.
3. **No aggregation.** When three agents across three tasks report
   the same `gh auth status` confusion, nothing connects the dots.
   Each report is rediscovered fresh; the third one carries the
   same novelty cost as the first.
4. **All-or-nothing toggle.** No sampling, no per-source rules.
   Either every task pays the prompt cost and pollutes every
   response, or no task does.
5. **Agent-only signal.** Plenty of friction is detectable by the
   daemon itself without an LLM in the loop (image-mtime vs
   Dockerfile, gh-auth-token resolvable, required tools on PATH).
   None of that runs today; an agent has to notice the symptom and
   put it in prose.
6. **No corroboration.** When the agent does report a problem,
   there's no deterministic ground truth to cross-check against.
   "Agent said pytest was missing" could be a stale image, a bad
   PATH, or the agent guessing wrong.

The fix isn't a tweak to `self-review.md`. The fix is to make
ergonomics a first-class observability surface, separated from the
response channel, with the right shape for each tenancy.

## Three-layer model

Three sources, complementary, each catching what the others miss:

| Layer | When it runs | Producer | Catches |
|-------|--------------|----------|---------|
| **Probe** | Task prep (pre-invoke; startup/finalize hooks still designed) | Daemon code, deterministic | Stale image, unresolvable auth, worktree buildup, low disk, drifted bundled docs, anything host/operator-vantage checkable in O(ms) |
| **Telemetry** | During and after the runner invocation | Daemon code, deterministic | Runner exit code, attempt count, retry reasons, time-in-phase, subprocess failures inside container, tool-call patterns visible in trace |
| **Reflection** | Sampled, post-task | Runner agent, free-text via prompt | Confusion, surprise, redundant tool calls, prompt incoherence, anything that needs introspection — the residual the deterministic layers can't see |

Probe + telemetry are the workhorses: cheap, structured, no LLM in
the loop. Reflection catches what they miss but isn't worth running
on every task. The deterministic layers also corroborate reflection
("agent says X; the probe confirms / contradicts"), so when both
fire on the same task there's a high-confidence finding rather than
a guess.

## Canonical record shape

All three layers emit the same record shape so the proxy, the
storage, and any downstream renderer don't care which layer
produced it:

```python
class Record:
    kind: Literal["probe", "telemetry", "reflection"]
    issue: str                   # stable identifier, e.g.
                                 # "stale_image", "auth_unresolvable",
                                 # "worktree_buildup",
                                 # "runner_retried", "reflection_raw"
    severity: Literal["info", "warn", "error"]
    detail: dict                 # issue-specific structured payload
    task_id: str | None          # absent for daemon-startup probes
    project_id: str
    env: str                     # "host" / "worktree" / "docker" / ...
    image: str | None            # docker image ref when env=docker
    source: str | None           # event source: "github", "telegram", ...
    timestamp: float
    daemon_version: str
```

The `issue` field is a small enum the daemon owns. Reflection
records always use `issue="reflection_raw"` and put the agent's
prose in `detail.body`; downstream parsing into more specific
issues (e.g. `reflection_inferred_stale_image`) happens at the
proxy or off-line. The daemon doesn't run a second LLM call to
classify.

## The ergo proxy

The producer-side abstraction that records cross to get to their
destination is the **ergo proxy** — a small `ErgoProxy` Protocol
that producers (probe, telemetry, reflection) write `Record`
instances to without caring how (or whether) they reach an
operator. Same record format on the wire across all of them. The
proxy is an *internal* class; what selects it is run ownership plus
one user-facing knob (see "Ownership decides routing").

| Proxy            | What it does |
|------------------|--------------|
| `NullErgoProxy`  | Drop the record. Resolved for `ergonomics=off` and operator-owned runs until the brnrd sink lands. |
| `LogErgoProxy`   | Emit a `warn`+ line to the daemon log, deduped by issue-signature within a window. No disk, no tokens — the zero-config default for user-owned runs. |
| `LocalErgoProxy` | Append JSONL to `.brr/ergonomics/<YYYY-MM-DD>.jsonl`; daily rotation; `brr ergonomics …` reads it. |
| `BrnrdErgoProxy` | (designed, not built) Batched HTTPS POST to brnrd's ergonomics endpoint; per-project + cross-project rollups. Lands with managed compute. |

`response` mode is *not* a proxy — it's a reflection-visibility
choice (see "Ownership decides routing"); probes in `response` mode
still flow through `LogErgoProxy`.

Proxies are stackable in principle (log + ship to brnrd) but the v1
surface is single-proxy. Adding a `prometheus` / `otel` / `loki`
proxy later is a single new class implementing the same `ErgoProxy`
Protocol — the producers don't change.

(The name is a nod to the 2006 anime; the role fits cleanly because
the abstraction's whole job is to proxy ergonomic observations from
producers to their eventual reader, opaque to both sides.)

## Ownership decides routing (and who decides)

The signal that drives routing is **who operates the run**, not the
env class and not whose image it is. A self-hoster running brr's own
bundled image on their own box is still a *user-owned* run — the
ergonomics are theirs to interpret against their project's needs. A
run dispatched onto brnrd's managed compute is *operator-owned*. The
same `DockerEnv` class serves both (the "caller axis" from
[`subject-managed-mode.md`](subject-managed-mode.md)); ownership is a
**launcher-stamped `RunContext.owner`** field (`user` | `operator`),
set by whichever side started the run — never read from the repo, so
it can't be forged by a committed `.brr/config`.

Ownership decides both the default sink and who gets to choose:

| `owner` | default sink | who configures it | in the user's chat reply? |
|---------|-------------|-------------------|---------------------------|
| `user` (local daemon — host, worktree, *and* docker) | `LogErgoProxy` (quiet daemon log) | the user, via the `ergonomics` knob | only in `response` mode |
| `operator` (managed compute run) | `NullErgoProxy` today; later `BrnrdErgoProxy` | **fixed; the `ergonomics` knob is ignored** | never |

The user-facing knob is a single plain value (the word "proxy" stays
out of the user surface):

```
ergonomics = off | log | local | response      # default: log
```

| value | probes | the agent's own review |
|-------|--------|------------------------|
| `off` | nothing | not injected |
| `log` (default) | `warn`+ to the daemon log (token-free) | not injected |
| `local` | persisted to `.brr/ergonomics`, queryable via `brr ergonomics` | not injected |
| `response` | to the daemon log | injected (skippable) and **left visible** in the reply |

Two rules make this non-leaky:

- **The `ergonomics` knob governs user-owned runs only.** Operator-owned
  runs aren't user-configurable, so there's no path to a contradictory
  config state and "a managed user can't decide where their operator's
  ergonomics go" falls out for free. The override lives in one place
  (the owner-aware resolver), not as scattered `if managed` checks; if a
  user explicitly set `response`/`local` and lands in an operator-owned
  run, the resolver ignores it. Current code returns `NullErgoProxy`
  until `BrnrdErgoProxy` and its endpoint land.
- **`response` only injects reflection; it never changes the operator
  path.** The "user never sees ergonomics in a *managed* reply, ever"
  invariant is absolute; `response` is a self-hosted, opt-in choice to
  see your *own* agent's notes in your *own* chat — the same spirit as
  `LocalErgoProxy` letting the user own the data, rendered inline.

This replaces the standalone `runner.self_review` knob, which was
removed outright (no users yet) — the same behaviour is now
`ergonomics=response`.

## What probes are for — the vantage rule

Deterministic probes are tempting to grow into an ever-expanding list
of static checks — a harness slowly absorbing intelligence that belongs
to the agent, encoding a snapshot of today's model limits, never
complete. The rule that bounds them:

> **Probes observe what's outside the agent's vantage** (host
> filesystem, cross-task state, pre-run resolution, installed-version
> drift — facts the agent in its sandbox structurally can't see).
> **Reflection observes what's inside it** (confusing code, wasted tool
> calls, missing deps *in the sandbox*, "was the context enough").
> **Never add a probe for something the agent can see for itself.**

Consequences:

- The probe set stays small and slow-growing (host-vantage facts are a
  bounded set); the open-ended tail routes to reflection, which is
  general and grows the harness by zero.
- "Incomplete probes" is correct, not a deficiency — completeness is
  reflection's job. Chasing a complete probe set is the trap. (This is
  why "probes only, drop reflection" is rejected below.)
- Probe growth becomes a principled promotion pipeline: reflection is
  the *discovery* mechanism (these probes were born from observed
  reviews); a finding graduates to a probe **only if it's
  host-vantage**.

Applying the rule retired `missing_tool` (host `gh`): in host/worktree
the agent shares the PATH and could check for itself, so it's the
agent's to notice. The five kept probes are all host/operator-vantage:
`stale_image` (container can't see the host Dockerfile), `auth_unresolvable`
(host-side token resolution, pre-run), `worktree_buildup` (cross-task),
`low_disk` (host filesystem / operator health), `drifted_bundled_docs`
(installed-brr-version vs repo).

A future, *most-thin-harness* direction (not built; tracked as #83):
feed host-vantage facts **forward** into the agent's context and let the
agent judge whether they matter for the task, while still recording them
for operator aggregation — keeping the judgment with the agent and
reusing the orientation forward-channel.

### Tenancy → routing → visibility

| Tenancy | `owner` | Default | What the user sees in chat | What the operator sees |
|---------|---------|---------|----------------------------|------------------------|
| Self-hosted (with or without brnrd connected) | `user` | `LogErgoProxy` (quiet log) | Nothing, unless they set `ergonomics=response` | Nothing — *they are* the operator; opt into `local` + `brr ergonomics`, or explicitly share to brnrd's improve pool |
| Managed compute run | `operator` | `NullErgoProxy` today; later `BrnrdErgoProxy` | Nothing — `response` is ignored on operator runs | Nothing today; later, brnrd dashboard's per-project view + cross-project rollups |

Two invariants this gives us:

- **A *managed* user never sees ergonomics in their task reply, ever.**
  Operator-owned runs ignore the `ergonomics` knob, so `response` can't
  leak into a managed reply. (A *self-hosted* user opting into
  `response` to see their own agent's notes in their own chat is a
  separate, deliberate choice — not pollution.)
- **The platform operator's view is the only planned place for
  fleet-wide rollups.** Self-hosted users see their own data; brnrd
  operators see the aggregate once the brnrd sink lands (and only that
  — per-user detail needs explicit opt-in).

Note this refines the original framing, which made `NullErgoProxy` the
self-hosted default and treated *all* user-visible ergonomics as
pollution. The pollution concern was really about the *default* and
about *managed* replies; a quiet daemon **log** (not the chat reply)
for user-owned runs gives the self-hoster free efficiency signal
without touching the response, and `response` is an explicit opt-in.

## Redaction at the proxy boundary

The brnrd proxy redacts before shipping. Probe and telemetry
records are mostly safe (image refs, tool names, exit codes) but
the reflection layer's free text can mention file paths,
environment variable names, error messages — anything the agent saw.

Default redaction rules for `BrnrdErgoProxy`:

- Strip absolute paths under `$HOME` and the repo root; leave
  basenames or a redacted token (`<repo>/<path>`).
- Strip git remote URLs and replace with the host (`<github.com>`).
- Strip values that match common secret patterns (`gh[ops]_…`,
  `sk-…`, `ghp_…`, AWS keys, OpenAI keys).
- Drop the `detail` field entirely for any reflection record above
  a configurable size; keep the issue identifier so the rollup
  still counts.

`LocalErgoProxy` writes the raw record (the user owns the data).
The redaction layer is a separate function the brnrd proxy composes;
the local one can opt into it for users who want to preview what
would ship before consenting to share.

## When each layer runs

**Designed full probe lifecycle** has two points:

1. Daemon startup: one-shot environment audit (config sanity, gate
   reachability, optional `gh auth token` check, bundled-doc
   freshness vs source). Emits records once per daemon process.
2. Task prep: per-task probe set scoped to the resolved env (image
   freshness, GitHub token resolvable for docker tasks, worktree
   health, disk health, bundled-doc drift). Emits records tagged with
   `task_id`.

Probes are cheap (O(ms) each, single-digit count per task) and
unconditional once the proxy is non-null. They never gate the task —
emitting an `error`-severity record doesn't refuse to run; the
operator decides whether to act on it.

**What shipped.** The v1 probe set runs at **task prep only** — one
hook in `daemon._run_worker` right after `env.prepare`, so the
resolved image / GitHub token / worktree state is visible. The five
probes are all host/operator-vantage (see the vantage rule):
`stale_image` (image `Created` vs the bundled Dockerfile's mtime,
docker only), `auth_unresolvable` (docker task, github in play, no
token resolved — host-side pre-run resolution), `worktree_buildup`
(kept worktrees past a threshold — cross-task), `low_disk` (host
filesystem), `drifted_bundled_docs` (repo `AGENTS.md` vs the installed
bundled template — installed-version drift). `missing_tool` was tried
in the first cut and **retired 2026-06-03** under the vantage rule: in
host/worktree the agent shares the PATH and can check `gh` itself.

Routing (2026-06-03) is owner-aware: `probe_task_prep` resolves the
proxy from `RunContext.owner` plus the `ergonomics` knob, then emits
findings. The default for user-owned runs is `LogErgoProxy` (quiet,
deduped, token-free), so probes run for every user-owned task by
default and surface only `warn`+ to the daemon log; `off`
short-circuits to `NullErgoProxy` and pays nothing. Operator-owned runs
also short-circuit to `NullErgoProxy` until the brnrd sink is built.
Deferred to a follow-up: the one-shot
**daemon-startup** audit (resolved here as design open-question #2 —
hardcode task-prep first, add a startup phase only when a probe needs
it), and **in-container** PATH probing for docker tasks (spawning a
probe container breaks the O(ms) contract, and most in-container facts
are agent-vantage anyway).

**Telemetry** rides on the existing run-progress and task-lifecycle
infrastructure. The daemon already emits structured packets
(`run_progress.py`); the telemetry layer is a sidecar consumer that
turns lifecycle events into ergonomics records when they match a
pattern (retry count ≥ N, phase duration > threshold, runner exit
code non-zero, etc.). No new instrumentation; same observations,
different proxy.

**Reflection** is the most expensive layer (prompt tokens + the
agent's attention). In `response` mode it's injected **every task**
(the user asked to see it, so there's nothing to sample). In the
deferred hidden-capture modes (`local`/`brnrd`) it's sampled, since
nobody's reading every one:

- Off by default for hidden capture (sample rate 0.0)
- Per-project knob `ergonomics.reflection_sample_rate` (0..1)
- Forced sample on retry (failure tasks are more informative than
  success tasks)
- Forced sample on probe `error`-severity hit (the deterministic
  layer flagged something; ask the agent to corroborate)

**Reflection's two visibility modes.** Where the agent's review goes
depends on the mode, and the two modes need different machinery:

- **`response` (shipped 2026-06-03, user-owned only).** Inject the
  skippable nudge; leave the review **in the reply**. No splitter, no
  markers, no stripping — the review is the deliverable the user asked
  to see. This replaces the old `runner.self_review` footer (removed),
  with one change: the block is *skippable* — the agent omits it
  entirely when there's nothing worth acting on, rather than writing a
  "nothing to report" line.
- **`local` / `brnrd` reflection (deferred).** Capture the review
  *without* showing it to the user — which needs the marker + splitter
  machinery below to cut it out of the response cleanly. Shipping this
  is a later slice; `response` mode needs none of it.

### Reflection extraction (deferred — for the capture-but-hide modes)

The current footer shape — "end your stdout with an **Ergonomics
review:** footer" — is workable but unbounded: the parser can't tell
where the agent's footer starts or ends, and any post-footer content
silently leaks into the review. The slice that wires *hidden*
reflection capture (local/brnrd) tightens the shape with explicit
markers. `response` mode does not use any of this — it leaves the
review visible and so needs no parsing.

**Prompt change.** The nudge becomes "wrap your review in HTML
comment markers at the very end of your stdout":

```markdown
<!-- BRR_ERGONOMICS_START -->
2-4 sentences of prose, same guidance content as today's self-review.md.
<!-- BRR_ERGONOMICS_END -->
```

**Daemon change.** A small `split_response_and_reflection(stdout)
-> (response_text, reflection_text | None)` step inserted into the
response-shipping path (`runner._write_response_file` and the
gate-side post code). The response text goes to the gate; the
reflection text becomes the body of a `Record(kind="reflection",
issue="reflection_raw", detail={"body": ...})` shipped to the
configured `ErgoProxy`.

**Sampling stays daemon-side, not agent-side.** The agent always
gets the nudge when injected; the daemon decides per-task whether
to *inject* the nudge based on `ergonomics.reflection_sample_rate`
plus the forced-override rules above. Producer logic stays
centralised; the agent doesn't have to roll a die or remember
context-specific sampling state.

**Failure modes are deliberately asymmetric:**

- *Parser misses the markers* (agent forgot, agent reworded the
  block, output truncated mid-write). Response ships unchanged;
  no reflection record. Benign — user gets exactly what the agent
  wrote; we just don't get that task's reflection.
- *Markers leak to the user* (parser bug, agent placed them
  unexpectedly, future prompt rewording slips through). HTML
  comments render as nothing in every markdown renderer that
  matters (Telegram, GitHub, Slack-via-markdown). Worst case the
  user sees raw `<!-- ... -->` text, which is mildly ugly but not
  data loss.

The asymmetry is deliberate: better to occasionally leak an
invisible footer than to occasionally strip real response content
from a user's chat. The parser defaults to "if the marker pair
isn't well-formed, leave the response intact and skip the record."

The whole reflection layer in code is ~60–80 LOC: a small module
holding the marker constants, the splitter function, the
sampling-decision helper, and a one-shot prompt-template selector
that injects the new wrapper text in place of today's footer
instruction.

## CLI shape (self-hosted)

A new top-level verb under the seven proposed in
[`decision-cli-shape.md`](decision-cli-shape.md) is overkill — this
fits as a subcommand under the existing `brr config` namespace plus
a small read-only view command. Sketch:

```
brr config set ergonomics local      # off | log (default) | local | response

brr ergonomics summary [--days 7]    # top issues, counts, last seen
brr ergonomics list [--issue …]      # raw records, filterable
brr ergonomics clear [--before DATE] # local store cleanup
brr ergonomics share                 # one-shot upload of N days
                                     # to brnrd's improve pool
                                     # (requires `brnrd connect`)
```

Final shape settles when the implementation plan slices; the
design only commits to "there is a CLI surface, it reads from
`LocalErgoProxy`, it includes a sharing path."

**Shipped (2026-06-02):** `brr ergonomics summary [--days N] [--json]`,
`brr ergonomics list [--issue X] [--days N] [--limit N] [--json]`, and
`brr ergonomics clear [--before YYYY-MM-DD]`, all reading the local
JSONL store. `brr ergonomics share` is deferred until `BrnrdErgoProxy`
lands (it needs the brnrd improve-pool endpoint). The routing knob is
the bare `ergonomics` value (`off|log|local|response`, default `log`);
until the `brr config` subcommand exists (#50), set it by editing
`.brr/config` (`ergonomics=local`). The verb is top-level and
operator-facing, consistent with the #49 CLI-taxonomy split
(human/operator verbs stay top-level; agent-only verbs move under
`brr agent`).

## brnrd dashboard surface

Two views, added to the dashboard MVP's eight as a follow-up slice:

- **Project ergonomics** — per-project rollup over a time window:
  top issues by count, severity distribution, image-staleness
  badge, reflection sample stream (when shared). For the user.
- **Fleet ergonomics** — cross-project, operator-only: top issues
  across the whole brnrd fleet, with breakdown by env / image /
  daemon version. For brnrd operators to spot brr bugs and friction
  patterns to fix at the platform level.

The fleet view is auth-gated to platform operators; users see only
their own projects. Both views read from the same brnrd-side
records store that `BrnrdErgoProxy` writes to.

## Self-hosted opt-in to the brnrd improve pool

Independent of using brnrd for managed compute, self-hosted users
can opt to share their (redacted) ergonomics records with brnrd's
improve pool. The wire format is identical to the managed-tenant
proxy, but the user explicitly opts in via `brr ergonomics share
--enable` (a dedicated opt-in, orthogonal to the `ergonomics` routing
knob — sharing is a separate consent, not a fifth mode). The pool
contributors get nothing back (no dashboard access; they're not paying
customers) — the upside is "you helped find this bug" and the
corresponding fix reaching their next `pip install -U brr`.

This is small but load-bearing for the brr-as-OSS story: it gives
self-hosters a way to contribute observability without forcing
them to become brnrd subscribers, and gives brnrd the breadth of
data needed to surface friction that wouldn't show up in the paid
fleet alone.

## Implementation footprint (rough)

| Slice | LOC | Ship-blocking on |
|-------|-----|------------------|
| `ErgoProxy` Protocol + `Null`/`Log`/`Local` proxies + owner-aware resolver | ~200 | — **(shipped; `Log` + owner routing 2026-06-03)** |
| Probe set v1 (image staleness, gh auth resolvable, worktree health, low disk, doc drift — all host-vantage) | ~230 | — **(shipped 2026-06-02; `missing_tool` retired 2026-06-03)** |
| Telemetry sidecar reading from `run_progress` | ~200 | — |
| Reflection `response` mode (skippable nudge, left visible) | ~30 | — **(shipped 2026-06-03; re-homes `runner.self_review`)** |
| Hidden reflection (marker prompt + splitter + sampling, for `local`/`brnrd`) | ~80 | `ErgoProxy` + a small redaction helper |
| `brr ergonomics` CLI (`summary`/`list`/`clear`) | ~200 | `ErgoProxy` + JSONL store — **(shipped 2026-06-02; `share` deferred with `BrnrdErgoProxy`)** |
| `BrnrdErgoProxy` + ergonomics endpoint stub | ~300 | `design-brnrd-protocol.md` slot for the endpoint |
| Dashboard project-ergonomics view | ~400 | `plan-brnrd-dashboard-mvp.md` slice landing for templating infra |
| Dashboard fleet-ergonomics view | ~200 | Platform-operator auth role in brnrd |

Roughly 1800 LOC across the daemon-side stack; the brnrd / dashboard
work is a separate slice that follows once brnrd ships. Self-hosted
gets value at slice 1 + 2 + 5 (ergo proxy + probe + CLI ≈ 600 LOC).

The whole thing is shippable incrementally. The probe layer alone
is a real win for self-hosted users today; it's the first slice
worth landing even if the proxy stays `NullErgoProxy` for the rest
(probes can emit warnings to the daemon's log channel as a
degenerate destination until the storage layer lands).

## Open questions

1. **Default `reflection_sample_rate` for the deferred hidden-capture
   modes.** Resolved for `response` (unconditional — the user asked to
   see it) and for the user-owned default (`log` — no reflection, so no
   token cost). Still open for the *hidden* `local`/`brnrd` capture and
   for managed: probably ≥0.1 on managed since the operator-side value
   is real, but not 1.0 (token cost adds up across the fleet). 0.1 is a
   guess; production data should tune.

2. **Probe-on-startup vs probe-on-task: cost-benefit.** Some probes
   (image mtime, daemon-environment audit) only need to run once
   per daemon process. Others (gh auth resolvable, worktree health)
   need to run per task. Splitting these correctly is mechanical;
   the question is whether to add a `probe_at` lifecycle hook
   pattern or just hardcode the two phases.

3. **What's the minimal redaction surface that's actually safe for
   the brnrd pool?** The list above is a starting point; full
   coverage would need a small security pass. Conservative default:
   for the `brnrd_pool` proxy variant, ship probe + telemetry
   records only; reflection records stay local until the redaction
   layer has bake time.

4. **How does this interact with `.brr/traces/`?** Today traces are
   forensic-only artifacts kept on error/conflict, removed on clean
   done. The telemetry layer could read trace files to extract more
   detail (e.g. how many tool calls the agent made), but that
   couples it to a specific runner. Probably out of scope for v1;
   revisit when there's demand for finer-grained per-task
   instrumentation.

5. **Subject hub timing.** This design plus the existing ergonomics
   research pages and `plan-agent-orientation-layering.md` could
   anchor a `subject-agent-ergonomics.md` or `subject-observability.md`
   hub. The design page stays the clearer home until telemetry and the
   brnrd sink settle; promote once this stops being one design thread.

## What was rejected

- **Keep `runner.self_review` as-is.** Doesn't compose with managed
  mode; doesn't aggregate; unconditionally pollutes user-visible
  output (no skip); throws away data. Resolved 2026-06-03 by folding it
  into `ergonomics=response` — same "review in the reply" behaviour,
  but skippable, owner-gated (never on managed), and on the path that
  also feeds `log`/`local` capture. The old knob was removed outright
  (no users yet), not aliased.

- **Force the agent to emit JSON instead of prose.** Forcing
  structure in the prompt costs tokens, constrains the agent's
  signal, and produces brittle output that drifts when prompt
  templates change. Cheaper to keep the prose footer, ship it as
  the body of a `reflection_raw` record, and parse downstream.

- **Use only deterministic probes + telemetry; drop reflection.**
  Rejected, and the vantage rule says why: probes can only see what we
  thought to check for *and* are bounded to host-vantage facts by
  design, so they structurally can't cover the inside-the-sandbox
  bucket. Reflection catches the unknown-unknowns the three-review
  pattern surfaced. Chasing a "complete" probe set is the trap — keep
  reflection; sample it (or show it, in `response` mode).

- **Make routing a free-form config knob.** Rejected: a user-set value
  that gets silently ignored on managed runs (or, worse, honoured and
  leaking the operator's ergonomics into a managed reply) is exactly
  the "configurations that don't make sense" footgun. Routing keys off
  launcher-stamped `RunContext.owner` instead; the `ergonomics` knob is
  scoped to user-owned runs by construction, so a nonsensical
  combination can't be expressed.

- **Bolt ergonomics into the existing audit log.** Audit log is a
  *user-facing* log of significant operations (spawn started, cred
  added, etc.) for compliance and billing transparency. Ergonomics
  is *operator-facing* friction data — different audience, different
  retention, different write rate. Same datastore is fine; same
  presentation is not.

- **Generate ergonomics records from external monitoring (Prometheus
  scraping, Datadog agent, OpenTelemetry).** Out of scope for v1.
  The `ErgoProxy` Protocol lets a future contributor add an OTel
  exporter without touching producers, which is the right level of
  decoupling — but baking an external dependency into the daemon now
  conflicts with [`decision-runtime-dependencies.md`](decision-runtime-dependencies.md).
