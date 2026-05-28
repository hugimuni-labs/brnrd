# Agent ergonomics — observability design

Status: active — proposed 2026-05-27 in response to the docker-runner
ergonomics pass earlier the same day, where three independent agent
reviews surfaced the same friction (stale image / missing pytest / gh
auth confusion) and the only thing that aggregated the signal was a
human pasting the reviews into a chat for analysis.

Companion to:

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

## What's wrong with the current shape

Today there's a single knob, `runner.self_review`, that injects
[`src/brr/prompts/self-review.md`](../src/brr/prompts/self-review.md)
into runner prompts. That prompt asks the agent to end its stdout
with a free-text **Ergonomics review:** footer covering orientation,
tooling, and branch metadata. The daemon does nothing with the
footer beyond shipping it as part of the response file to the gate.

This shape has six concrete failure modes:

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
| **Probe** | Task prep (pre-invoke), task finalize | Daemon code, deterministic | Stale image, missing tools, unresolvable auth, dirty repo, low disk, drifted bundled docs, anything checkable in O(ms) |
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
                                 # "stale_image", "missing_tool",
                                 # "auth_unresolvable",
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
operator. Three concrete implementations ship; same record format
on the wire across all of them; proxy choice is tenancy-driven,
not data-driven.

| Proxy            | Default for           | What it does |
|------------------|-----------------------|--------------|
| `NullErgoProxy`  | Self-hosted, factory  | Drop the record. Hot path stays cheap; nothing is captured. |
| `LocalErgoProxy` | Self-hosted, opt-in   | Append JSONL to `.brr/ergonomics/<YYYY-MM-DD>.jsonl`; daily rotation; `brr ergonomics …` CLI reads from there. |
| `BrnrdErgoProxy` | Managed mode (auto when `brnrd connect` runs), or self-hosted opt-in to "help improve brr" | Batched HTTPS POST to brnrd's ergonomics endpoint; brnrd-side stores per-project + cross-project rollups. |

Proxies are stackable in principle (write local + ship to brnrd)
but the v1 surface is single-proxy. Adding a `prometheus` /
`otel` / `loki` proxy later is a single new class implementing the
same `ErgoProxy` Protocol — the producers don't change.

(The name is a nod to the 2006 anime; the role fits cleanly because
the abstraction's whole job is to proxy ergonomic observations from
producers to their eventual reader, opaque to both sides.)

### Tenancy → routing → visibility

| Tenancy | Default proxy | What the user sees in chat | What the operator sees |
|---------|---------------|----------------------------|------------------------|
| Self-hosted, no brnrd | `NullErgoProxy` | Nothing (current behaviour) | Nothing — they're the operator; they can opt-in to `LocalErgoProxy` and run `brr ergonomics` |
| Self-hosted, brnrd connected | `LocalErgoProxy` + optional `BrnrdErgoProxy` | Nothing; reflection footer stripped from response | `brr ergonomics` locally + (if shared) brnrd's "fleet ergonomics" view |
| Managed by brnrd | `BrnrdErgoProxy` | Nothing; reflection footer **never injected** | brnrd dashboard's per-project ergonomics view + cross-project rollups for platform operators |

Two invariants this gives us:

- **The user never sees ergonomics data in their task response,
  ever.** The current `self-review.md` injection produces that
  pollution; this design eliminates it by routing the reflection
  layer through a proxy instead of stdout.
- **The platform operator's view is the only place fleet-wide
  rollups exist.** Self-hosted users see their own data; brnrd
  operators see the aggregate (and only that — per-user detail
  needs explicit opt-in).

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

**Probe** runs at two points:

1. Daemon startup: one-shot environment audit (config sanity, gate
   reachability, optional `gh auth token` check, bundled-doc
   freshness vs source). Emits records once per daemon process.
2. Task prep: per-task probe set scoped to the resolved env (image
   freshness, tool presence inside the container, GitHub token
   resolvable for docker tasks, worktree health). Emits records
   tagged with `task_id`.

Probes are cheap (O(ms) each, single-digit count per task) and
unconditional once the proxy is non-null. They never gate the task —
emitting an `error`-severity record doesn't refuse to run; the
operator decides whether to act on it.

**Telemetry** rides on the existing run-progress and task-lifecycle
infrastructure. The daemon already emits structured packets
(`run_progress.py`); the telemetry layer is a sidecar consumer that
turns lifecycle events into ergonomics records when they match a
pattern (retry count ≥ N, phase duration > threshold, runner exit
code non-zero, etc.). No new instrumentation; same observations,
different proxy.

**Reflection** is the most expensive layer (prompt tokens + the
agent's attention) so it's sampled:

- Off by default (sample rate 0.0)
- Per-project knob `ergonomics.reflection_sample_rate` (0..1)
- Forced sample on retry (failure tasks are more informative than
  success tasks)
- Forced sample on probe `error`-severity hit (the deterministic
  layer flagged something; ask the agent to corroborate)

### Reflection extraction

The current `self-review.md` shape — "end your stdout with an
**Ergonomics review:** footer" — is workable but unbounded: the
parser can't tell where the agent's footer starts or ends, and any
post-footer content silently leaks into the review. The
implementation slice that wires the reflection proxy in tightens
the shape with explicit markers.

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
brr config set ergonomics.proxy local
brr config set ergonomics.reflection_sample_rate 0.1

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
proxy, but the user explicitly opts in via
`brr ergonomics share --enable` or `brr config set
ergonomics.proxy brnrd_pool`. The pool contributors get nothing
back (no dashboard access; they're not paying customers) — the
upside is "you helped find this bug" and the corresponding fix
reaching their next `pip install -U brr`.

This is small but load-bearing for the brr-as-OSS story: it gives
self-hosters a way to contribute observability without forcing
them to become brnrd subscribers, and gives brnrd the breadth of
data needed to surface friction that wouldn't show up in the paid
fleet alone.

## Implementation footprint (rough)

| Slice | LOC | Ship-blocking on |
|-------|-----|------------------|
| `ErgoProxy` Protocol + `NullErgoProxy` + `LocalErgoProxy` | ~150 | — |
| Probe set v1 (image staleness, gh auth resolvable, tools on PATH, worktree health) | ~250 | — |
| Telemetry sidecar reading from `run_progress` | ~200 | — |
| Reflection wrapper-marker prompt + splitter + proxy wiring | ~80 | `ErgoProxy` + a small redaction helper |
| `brr ergonomics` CLI | ~200 | `ErgoProxy` + JSONL store |
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

1. **Default `reflection_sample_rate` for self-hosted vs managed.**
   Self-hosted should default to 0 (opt-in) to keep the response
   clean by default. Managed should probably default to ≥0.1 since
   the operator-side value is real, but not 1.0 (token cost adds up
   across the fleet). 0.1 is a guess; production data should tune.

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

5. **Subject hub timing.** This design + the existing 4
   ergonomics-related research pages + `plan-agent-orientation-
   layering.md` could anchor a `subject-agent-ergonomics.md` or
   `subject-observability.md` hub. Premature today (no shipped
   implementation; the back-channel infrastructure is the
   load-bearing missing piece); the hub goes in once the ergo
   proxy + probe slices land.

## What was rejected

- **Keep `runner.self_review` as-is.** Doesn't compose with managed
  mode; doesn't aggregate; pollutes user-visible output; throws
  away data. The shape needs to change before brnrd ships, not
  after.

- **Force the agent to emit JSON instead of prose.** Forcing
  structure in the prompt costs tokens, constrains the agent's
  signal, and produces brittle output that drifts when prompt
  templates change. Cheaper to keep the prose footer, ship it as
  the body of a `reflection_raw` record, and parse downstream.

- **Use only deterministic probes + telemetry; drop reflection.**
  The deterministic layers can only see what we thought to check
  for. Reflection catches the unknown-unknowns, which is exactly
  the bucket the three-review pattern surfaced. Keep it; sample it.

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
