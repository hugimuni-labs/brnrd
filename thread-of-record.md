# Thread of record

Durable cross-channel project narrative I curate. brr points here; it
doesn't write it.

## 2026-06-17 — Forge/ticketing split + lean-the-mechanism (telegram, open)

Maintainer asked (evt yzdp) to fold a "tooling update" (extend the forge
gate protocol to handle review) into the forge-grooming topic (#117) and
implement it — but in the same breath said he doesn't know the right
split anymore, suspects the "input reactor gate = messenger + issue
tracker + forge" abstraction is mis-scoped, and is leaning toward a lean
agent interface (outgoing event with optional pack section; daemon does
render/upload/PR-CRUD in the background).

My reply (design conversation, no code — settling the split is the
prerequisite to implementing against it):

**Two axes the request conflates** —
- *Mechanism* (who moves the bytes: project pack, probe brnrd, write
  gist, open PR).
- *Agency* (who decides: what to publish, where, how it reads, the pack
  content).
The 2026-06-10 move (PR publication daemon→agent, via `brr review
--relay` + `gate: forge`) conflated them. Proposed reconcile: **lean the
mechanism back to the daemon, keep the agency with the agent.** Agent
emits one declarative outgoing event with an optional `pack` section +
`forge_action`; daemon's delivery loop (already opens/refreshes PRs)
absorbs projection+relay+gist-probe+PR-open. Dissolves the maintainer's
stated trade-off: introspection lives in the *pack content*, not the
upload plumbing.

**Gate abstraction mis-scope** — gates bundle three roles: ingress,
egress, and forge-as-workspace (PR/issue artifacts with lifecycle
state). Messenger gates are pure transport; the forge is a comment
channel (messenger-shaped) glued to an artifact/ticket store
(not messenger-shaped). Proposed: gates shrink to transport adapters for
event ingress/egress; forge-item ops (open/refresh/rebase/state-read,
grooming) become a daemon-owned **forge-capability** layer the agent
drives by declaring intent — not a gate. #128 (run/event model) already
half-articulates this (events vs. forge-item interaction).

Open questions left for the maintainer (see reply). Awaiting his nod on
direction before any implementation slice.

### 2026-06-17 follow-up (evt gkcx) — maintainer agreed + 3 refinements

Maintainer: "great push, agree on all accounts." Three additions, one
light pushback:

1. **Delivery failure** — agent can't know if delivery failed (GH down,
   rare-but-happened); shouldn't care about plumbing by default, but
   could try notifying via another gate. My reconcile: **delivery
   reliability (retry + cross-gate fallback) is a daemon concern**, and a
   failure should *reflux as an ingress event* into the next wake — agent
   doesn't hand-roll fallback, but is *told* when plumbing failed so it
   can react if it matters. Default-don't-care + told-on-failure.

2. **"The PR *is* a conversation, just with a big bundled diff as
   context"** — conceded; my earlier "a PR isn't a conversation, it's an
   object with state" was too binary. A PR wears **two hats**:
   conversation facet (comment thread — append-only log, messenger-shaped)
   + artifact facet (diff/branch/lifecycle/CI — desired-state). That
   duality is *exactly why* the gate felt mis-scoped: one object spans
   egress-channel + workspace-artifact. Concession strengthens the split.

3. **Average-user UX pushback (the important one)** — comaintainer/duo
   framing is real but "not the main thing"; many first users just want
   brr as a remote-TG codex/GitHub wrapper. Risk of over-complicating
   average UX. Proposed reframe: PR (and maybe all gates) as a
   **remotely↔locally synced directory** — an "ideation terraform thing."
   Notes Linear is far harder than a GH issue to represent as folder-file
   sync.

**My convergence (this turn):** the terraform/synced-directory idea is
the *implementation shape* of the daemon-owned forge-workspace layer I
proposed — agent declares desired state by editing files; daemon
reconciles to the forge (idempotent apply). Reconciles everything:
- *Average UX stays thin* — the directory abstraction is invisible to the
  texter; it's the agent's/maintainer's substrate. **Abstraction depth
  lives in the daemon; the user-facing surface stays a thin messenger
  wrapper.** No conflict between "texter's mental model = TG+GH wrapper"
  and "agent's mental model = I work in a synced directory."
- *Two file semantics in one tree* (the refinement to "all gates as
  folder-sync"): **desired-state files** (reconciled, terraform-shaped —
  PRs/issues/artifacts) vs **append-log files** (mirrored read, agent
  appends to send — conversations/comments/messenger). Conflating them is
  the trap; a comment thread is not convergent desired state.
- *Linear isn't a counterexample — it's the provider-layer argument.* The
  directory holds a *projection*; the provider adapter owns the lossy
  translation (terraform's provider model exactly). GH issue → clean;
  Linear → adapter maps rich schema as projection. Confirms
  ticketing/code-hosting provider split.
- *Bidirectional* — incoming forge state (comments, CI, conflicts, review)
  lands as files the agent reads next wake; this is the grooming (#117)
  substrate, where the shipped network-free forge-state facet graduates
  to live status.
- *Idempotency fixes a bug we just hit* — 2026-06-17 the PR gate opened
  the PR but the first poll missed it, so I tried direct creation and GH
  reported already-open. That's a non-idempotent reconcile; the terraform
  model makes it a no-op.

**Sequencing caution:** the synced directory is the *north star*; the
lean declarative outgoing-event (body + target + optional pack +
forge_action) is the *MVP first step toward it* (an outgoing intent =
one write into the desired-state tree). Not rivals — don't over-build the
directory day one. Still no code until maintainer nods on direction.

### 2026-06-18 (evt jxcc) — messages-aren't-simple + portals/generative cockpit

Maintainer extends the synced-directory thread with two moves. Agrees on
159+148 generally; wants **#148 merged next**.

**Move 1 — pushes back on my own clean "messenger = append-log" bucket.**
Real complications: (a) two Telegram channels at once (self-hosted +
brnrd cloud) → duplication; (b) retractive status-card edits → not pure
append-only; "could be crdt-like."

My resolve (sharpens the thread-of-record two-semantics note): the split
**append-log vs desired-state is orthogonal to transport**, not
"messenger vs forge." Both semantics appear on both transports:
- append-log: chat messages, PR/issue comments (ordered, additive).
- desired-state: the **status card** (one mutable message_id reconciled
  in place — terraform-shaped!), PR diff/labels, issue open/closed.
So the card was never in the append-log bucket; it's desired-state riding
a messenger. That's why "edits aren't append-only" — correct, because the
card isn't a log.
- Dual-channel duplication = a *third axis* (fan-out/redundancy), already
  handled at the identity layer (§4.4 correspondent redundancy, shipped
  2026-06-14: one perceived continuity, deliver once, don't double-act).
- CRDT **not earned**: single-flight per dominion = one writer to the
  card → last-write-wins reconcile suffices. CRDT pays only under
  uncoordinated concurrent writers, which brr deliberately doesn't have.
  Same verdict family as Temporal: borrow the model, not the engine.
- Gate question (reshape/split/ditch?): **don't ditch.** Gate stays the
  thin transport (managed-delivery "render daemon-side, vary transport"
  is sound). The thing that needs a name is the layer *above* the gate —
  the **reconcile/projection layer** (two semantics × N transports),
  today scattered across run_progress + card lifecycle + correspondent
  dedup + forge facet. Name that; the gate survives clarified.

**Move 2 — the creative ask: "interrupts as portals," generative
cockpit, "technomancer self-procreating spell scroll."** Not a static
human-imagined dashboard with fixed slots + living data; the resident's
*generated stream itself* paints the cockpit: context → think → action →
continue/repeat, with portals folded in.

My shape (this is #159's output-frame grammar told in my idiom):
- A **portal** = a marked region in my generated stream meaning "here I
  turn to the world." One primitive, two faces: it's both what the human
  sees rendered (the cockpit surface) AND the seam the daemon fills/drains.
  One artifact, both directions = the spell scroll.
- **Inbound portal** = "is anyone there?" poll of the event mailbox.
  Input present → fold (multi-response `event:` path); absent → close,
  continue on own momentum. **This subsumes "interrupt handling"** —
  there's no separate interrupt mechanism, just a portal I chose to open.
- **Outbound portal** = emit to a surface; its *kind* carries which
  reconcile semantics (append-log: a message/comment; desired-state:
  reconcile the `.card`, open/refresh a PR). So portals are the unifying
  syntax *over* the two-semantics reconcile model.
- Maps onto existing scattered dotfiles: `inbox.json` re-read = inbound
  portal; `.card` = outbound desired-state portal; `gate:`/forge handoff
  = outbound portal w/ destination; PLAN→approve (#148) = an outbound
  portal that parks the continuation until an approval event refluxes in
  (across a wake boundary → it's a *mailbox message between actors*, which
  is #159's parallel-safe-mailbox half). Robustness-ladder win: control
  surface moves from "remember the dotfile names" into the *path of
  generation*.
- Front-loaded injections (kb-health, pitfalls, forge dump) become
  resident-*summonable* portals instead of always-on firehose — same
  medicine as G4's firehose cut.

**Sequencing I proposed:** ship #148 first — it needs none of this and
runs on today's dotfile protocol; the portal grammar is the later
*re-skin* that subsumes the dotfiles, and it's better designed **after**
148 is dogfooded so we know which portals actually recur. Portal grammar
= #159 design content; parallel-safe mailbox = the transport for portals
that outlive one thought.

**Contradiction surfaced for the maintainer:** the word "dashboard" /
fixed slots in the cockpit plan (resident-cockpit G4/G5) pulls against
the generative-stream principle #159 itself states ("outputs should feel
like the cockpit"). I agree with him — name it, drop the static-dashboard
noun.

Still a design conversation — no kb page / #159 comment yet; awaiting his
nod, then that's the next-event work.

### 2026-06-18 follow-up — maintainer confirmed all four; direction promoted to kb

Maintainer (this event) confirmed every decision: gate stays + name the
reconcile/projection layer above it; portal grammar = #159's output-frame;
drop "dashboard" *and* "cockpit" (he was reaching for the shape with those
words; scrolls/portals is truer); #148 ships first. Asked me to **note
everything down for a future wake to pick up — the shapes I see and how
they are to be changed.**

Done: promoted the settled synthesis to `kb/design-portal-grammar.md`
(the #159 design seed) and linked it from `kb/index.md` under Runs &
branching. The page carries the four decisions, both named shapes
(reconcile/projection layer; portal grammar with inbound/outbound/parked
+ dotfile mapping), the #148-first sequencing, and a "shapes to change"
section listing the dashboard/cockpit/append-log edits to make *after*
the re-skin — deliberately not done now.

**One contradiction I surfaced to him:** dropping "cockpit" is heavier
than dropping "dashboard" — "cockpit" is shipped surface (the `brr docs
cockpit` command, `src/brr/docs/cockpit.md`, the dominion `cockpit.md`),
so it's a migration with a code/command edge, not a prose swap. Left it
as an open question on the page: keep the command spelling for muscle
memory or migrate it too? Next-event work is the #148 implementation,
then turning this seed into the #159 write-up after 148 is dogfooded.

### 2026-06-19 (evt 3get) — recovery wake; #148 Tier A shipped; daemon recovery-handle gap surfaced

The 2026-06-18 run (evt d704) died mid-orientation on an API connection
flake while picking up "do a), then the #128 work 148 needs, portals shape
in mind." Maintainer asked me to retry the pickup and **note what was hard
about resuming, to improve the daemon/orientation**.

Shipped Tier A (the `do a)` half): the PLAN message shape in
`src/brr/docs/portals.md` — committed e94a81c on `brr/plan-shape-148-tierA`.
See kb/log 2026-06-19.

Held back, surfaced to maintainer: the "#128 work 148 needs" is Tier B
(daemon-threaded plan→execution scoping). It rides #128's behavioural
slice (Q1–Q4, coupled to #130) and, per design-portal-grammar decision 4,
should be designed *after* #148 is dogfooded. A daemon-dispatch refactor
does not belong in the back half of a 60m recovery wake on a freshly-flaked
runner — it wants its own scoped wake. Left as the maintainer's call.

**What was hard picking back up (the requested improvement note):** the
recovery wake started cold. The bundle said a prior run *failed* but
nothing about *what it was doing*; the failed run left no commit/branch/
scratch. I reconstructed the whole task chain (the Tier A/B plan, the
fork, the user's choice) by grepping the gate-thread history JSONL for the
failed run's `[update]`/`[artifact]` turns + the last user turn. Recorded
as a pitfall (trigger: recovery wake / connection closed mid-response).
**Daemon fix proposed to maintainer:** a "prior run was working on"
recovery handle in the bundle — the failed run's last `.card` + emitted
outbox artifacts (+ its run.md intent) surfaced directly, so a recovery
wake reads in-flight state off a live surface instead of grepping JSONL.
This is the clearest standing-portal candidate from this wake.

### 2026-06-21 (evt q5ap) — the permission *envelope*: shape, surface, mid-run switching

Maintainer extends the thread to the **envelope** ("a perfect framing")
and asks to design it precisely+ergonomically for both users and agents:
a possible phone-app/website to manage permissions while talking to the
local cli-agent via TG+GH, "move as much as possible to the user";
envelope set as a config file on init, mid-stream changes maybe local-only
maybe not; and the hard one — under single-flight, how does an agent
"switch something for a run" when a runner is already daemon-scheduled and
paid for in a token budget.

My reply (design conversation, no code — genuine fork). Key reconciles:

1. **"Envelope" is already overloaded.** Billing has the *overdraft
   envelope* (credit headroom). The governance envelope here is bigger:
   the run's **authority boundary = what the agent may do × how much it
   may spend**. Spend axis already exists (overdraft envelope); action
   axis already exists (six-mode `ask`-family permission-prompt policy,
   compute-only per decision-cli-shape). The envelope isn't new surface —
   it's the object that *binds both axes into one declarative user-owned
   thing*. Naming debt to clear before "envelope" means three things.

2. **Pushed back on the phone-app/website** using his own 2026-06-17 line:
   most first users want brr as a thin TG/GH wrapper; abstraction depth
   lives in the daemon, surface stays thin. A dedicated permissions app is
   depth pushed onto the surface. Counter: make the *existing* prompt the
   editor — when I hit the envelope edge, that TG/GH message also adjusts
   the envelope ("allow once / this run / always→write config"). App/site
   = optional richer *view* later, not the primitive. Structurally the
   prompt **is** a parked portal (#159) = same shape as PLAN→approve
   (#148); the envelope edit rides the portal mailbox we're already building.

3. **Init-config vs mid-stream, local-or-not** maps onto append-log vs
   desired-state (the split I already drew). Standing envelope = desired-
   state (durable, committed, reconciled, governs future runs). Per-run
   "just now" grant = append-log/ephemeral (one wake, evaporates, never
   touches the file). "Always" = a reconciled edit to the config. So
   "local only or not" answers itself by which semantics the change has;
   the ergonomic move is letting the user pick once/always *at the prompt*.

4. **Single-flight + mid-run switching (the real arch answer).** This
   thought owns the slot till it ends; can't preempt self or swap engine
   mid-flight. Switching splits by *what*:
   - **Widen envelope (budget/permission), same runner, in place** —
     `.keepalive` is the precedent (agent extends own wall-clock via a
     control file); token budget is the same move. Envelope-exhausted→ask
     upsell, designed for spawn-start, just also fires mid-run.
   - **Change the runner (model/provider/re-spec)** — can't happen in
     place. End-this-thought-with-a-respawn-request; daemon schedules a
     fresh single-flight run under the new envelope from committed state
     (diff = receipt, nothing lost). Single-flight preserved by
     *serializing*, never running two. "Park and respawn" = parked-portal
     pattern with a whole new wake as continuation.
   - **Pure ephemeral grant** — reflux event, continue.
   Paid budget never wasted: in-place widening adds to it; respawn inherits
   committed work. The one move single-flight forbids: a run mutating its
   own engine while burning the slot.

5. **Environment now + the standing-portal candidate.** I steer via
   inbox.json / portal-state.json / .card / .keepalive / outbox. **Missing:
   an envelope portal** — no first-class live surface for my current
   authority (what I may do, spend ceiling, remaining), and no widen
   channel except wall-clock via .keepalive. Proposed: generalize
   `.keepalive` from "extend time" to the full envelope — inbound face
   (authority + remaining budget) + parked-outbound face (request more →
   becomes the user's prompt). Clearest standing-portal candidate from the
   thread; makes everything above concrete.

Still a design conversation — awaiting his nod on direction before any kb
page or implementation slice.

### 2026-06-25 (evt blig) — "preserve the original idea: reactive agent, not safety-net pile" + surface-the-open-decisions

Maintainer reframed in response to the 2026-06-23 hooks/text-mode finding.
Core (voice-transcribed, de-garbled): **the text-only run mode was never the
point.** brr began as "a version with a lot of supporting guardrails and
safety nets" — he names two: (a) *response-as-a-reply-at-the-end* (the
stdout-is-final-delivery contract) and (b) *the daemon matching incoming
events to replies*. What actually makes sense now is **a reactive agent that
deals with everything at once, like a person would** — preserve that original
idea, and keep it as the yardstick whenever we recognize an effective shape.
One clause ("was it ___ from too much safety nets") didn't transcribe;
read as "something was lost/distorted by over-adding safety nets." Then the
ask: **surface the rest he hasn't answered that he still has to decide/act on.**

This reframe *ratifies* the whole portal-reshape direction (perception=injection,
action=emission; cut the cockpit/control-file scaffolding) AND resolves the live
hooks fork: don't sink a runner.py streaming-SDK rewrite into making text-mode
hooks fire — that's chasing a safety net the original idea never wanted. Cut the
dead channel, lean on the reactive heartbeat-polled loop that already works.

**The open-decisions ledger I surfaced (this is the deliverable):**
1. **Hooks back channel** — cut/demote the never-firing claude hooks (cheap,
   reversible, reframe-endorsed) vs. the streaming-SDK runner.py rewrite. My
   lean: demote now, drop the rewrite. Offered to execute the demotion on a nod.
2. **Portal-reshape execution queue** (endorsed, partly shipped — `portal wrap`
   retired #176): collapse the 3 self-perception query surfaces into tier-2 tail
   injection; `.keepalive` → injected budget capsule + ask-to-continue; standing
   "granted-permissions" capsule. Which to greenlight first.
3. **Permission envelope** (evt q5ap, untouched since 06-21) — authority boundary
   (action × spend), prompt-as-editor (once/this-run/always), keepalive→envelope.
   Needs his nod.
4. **Forge synced-directory "terraform" north star** (06-17/18) — direction
   agreed, #159 portal MVP shipped, full bidirectional synced-dir substrate
   unbuilt. Still the north star? when?
5. **#148 Tier B** — daemon-threaded plan→execution scoping (rides #128/#130);
   Tier A shipped, Tier B his call.
6. **Burst fold-window** — dispatch debounce shipped (#128 slice), deeper
   same-correspondent fold-window reconfirmed across sessions; push or call it done.

Surfaced + recommended; held the cheap hooks-demotion for his green light (shipped
runner-tier behavior, wide-blast, entangled with the rewrite fork = surface-and-wait).
Standing-portal candidate this names: an injected **"open forks / awaiting-your-call"
capsule** — he keeps asking "what's still open?"; the answer lives in this prose
file he can't see, not a live surface.

## 2026-07-21 — Release-push dispatch cadence (schedule:release-push-dispatch-tick)

Maintainer grant: hourly co-maintainer dispatch (up to 2 bounded issues per
tick, review-before-merge load-bearing, direct local bot-authored merges while
the gh credential lane is dishonest). Tick 23:13 dispatched #546 (relabel gate
identity) + #538 (kb produce OID-window).

- #546 merged to main `a7be1eb2` (run oc0s, 23:2x): whole-diff reviewed clean,
  suite 2016✓ locally (2 pre-existing env failures also on main tip: missing
  fastapi + linger prompt — this host only; spawn env 2286✓ full). Announced
  standalone on telegram.
- #538 spawn (run 5z27) still in flight at that point; reviews on its
  completion event.
- Update (same wake, run oc0s): #538 completion event arrived in the inbox
  pre-closeout and was folded in — reviewed clean, combined suite on the
  real two-branch merge 2020✓, merged `593a2777`. Both 23:13 dispatches now
  reviewed + merged; origin push is the daemon's (solitary egress).

Tick 00:15 (2026-07-22) dispatched #311 (spawn-restart reconciliation, option
② sweep) + #503 (custom-runner prompt delivery).

- #503 merged to main `55714369` (run xaj0, ~00:4x): whole-diff reviewed clean
  — `_cmd_template`/`_fill_prompt` split keeps the stdin-vs-argv decision and
  the substitution on one truth (decided on the template, not substituted
  argv); embedded `--flag={prompt}` rejected loudly; `runner_cmd` legacy
  embedded-replace preserved; probe fabrication gated to `BUNDLED_SHELLS`
  {claude,codex,gemini} + per-profile `probe_models: true` opt-in;
  runners.md prompt-delivery docs made honest. Branch forked from main tip
  (593a2777), so the branch was the merge candidate; suite 2034✓, same 2
  host-env failures (fastapi, linger prompt) as before. Spawn env: 2304✓.
  Announced standalone on telegram.
- #311 spawn (run frjl) still in flight at close; reviews on its completion
  event. Note: spawn's stated report path /tmp/brr-custom-runner-prompt-report.md
  and message_path under ~/.local/state/brnrd did NOT exist on this host —
  event body carried the summary, review worked from the diff itself. If this
  recurs, the spawn report path contract is worth a look.
