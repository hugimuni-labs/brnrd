# Review: execution-model coherence + context-shape (evt-azq5, 2026-06-29)

Kind: review · Status: handoff for a next implementing wake · Branch under
review: `brr/initial-context-reweave` (10,877+/2,229− across 83 files vs `main`)

Maintainer ask (paraphrased): the execution model changed a lot — Core choice,
quality escalation, cost awareness, respawns — and it now feels *scrambled* and
*out of control*. Review the whole diff for holistic coherence and consistency
with the plan we agreed; review the shape of the initial context; and frame the
reshape so a next run can implement it. Companion plan:
[`plan-repo-gardening.md`](plan-repo-gardening.md) (Task 2),
[`design-runner-cores.md`](design-runner-cores.md).

## 1. Does the diff hold up? — Yes, but it shipped the *engine* without the *dashboard*

Every commit on the branch traces to an agreed slice. The mechanics are sound
and well-tested (runner_cores, runner_select, runner_capabilities,
runner_failures, spending_plan; ~1,700 lines of new src, broad test coverage).
The selection policy, local fallback, quality escalation, and Core-registry
slices match `plan-repo-gardening.md` Task 2 (2A–2I) as the maintainer approved
them. So the diff is **consistent with the plan**.

The incoherence the maintainer feels is real but it is **not in the engine — it
is in the absence of a control surface over the engine.** Five concrete gaps:

1. **The mandate is invisible.** `facets._runner_block` exposes the *selected*
   runner, the *one* quality-escalation target, and (when set) the relay
   spending plan. It never exposes the *set of available* Shells+Cores. The
   catalog lives only in `runner_cores._BUNDLED_CORES` + `runners.md` frontmatter
   — readable by editing source, not by the user or (cheaply) the resident. So
   "we don't know what we allow to select and what was selected" is half-true:
   *selected* is in the portal (not the card); *selectable* exists nowhere as
   structured state. This is the single highest-leverage fix.

2. **Two parallel Core catalogs.** `runners.md` still carries static
   `claude-bare-api-only-sonnet / -opus / -fable` profiles whose Cores overlap
   the `_BUNDLED_CORES` registry rows (`claude-sonnet/opus/fable`, same
   cost_ranks). They differ only by auth (`--bare` = `ANTHROPIC_API_KEY` vs the
   subscription Shell). Two hand-maintained lists of the same Cores = the sprawl
   the maintainer named. Collapse to one source: keep `_BUNDLED_CORES` as the
   live registry, express the bare/API-key variant as an *auth flag* on a Core,
   not as a separate triplet of profiles.

3. **The relay-consent loop is open.** `needs_relay_consent=true` is *emitted*
   (daemon) and *exposed* in `portal-state.json` (facets slice 4), but nothing
   *consumes* `relay_consent=approved` to retry on the relay runner, and there is
   no wallet-balance read or billing (deferred slices 5–7). The spending plan is
   presentable but inert end-to-end. Honest status — it is documented as
   deferred — but the maintainer's "great, now let's do paid relay" will not yet
   actually spend, and that should be said plainly.

4. **The card overwrites its own history.** `run_progress` *does* model
   `phase_history`, `attempt`, and a `fallback X -> Y` detail — the data exists.
   But the rendered card shows only the current phase + note, and the
   `attempt_failed` reason is transient (next phase replaces it). So the
   codex→claude-haiku story ("ran out of quota, retried cheaper") is computable
   but never shown or persisted. The fix is rendering/persistence, not new data:
   surface the attempt ledger, and persist a per-run record (the maintainer's
   gist-per-run idea fits exactly here).

5. **Plan↔diff traceability is the actual missing UX.** The agreed plan exists,
   and each commit traces to it — but nothing connects "what we discussed 10
   turns ago" to "the 10k lines now" *for the maintainer*. There is no
   user-facing ledger of recent decisions / definitions / plan position. This is
   why coherent work *feels* scrambled.

## 2. Context-shape review (the "look at it" deliverable)

The initial context the resident wakes into is strong and largely coheres: the
playbook (perception=injection / action=emission, society-of-mind memory,
ownership stance), the pitfall injection, the Recent Activity tail, and the Run
Context Bundle compose into one intent — *orient from injected state, don't
poll*. Seams noticed this wake:

- **The bundle already carries the answer to the maintainer's "do you need
  history?" question — partially.** Recent Activity (kb/log tail), the
  communication snapshot (woven recent turns), and on-demand grouped history
  files are injected. What is *missing* and forces exploration: the **diff-state
  orientation** (what this branch changed vs main, which plan slices it
  implements). A resident waking onto a 10k-line branch must `git diff` and read
  plans to reconstruct the work. That reconstruction is the polling tax the
  playbook warns about — a candidate for a **standing portal / injected block**:
  a "work-in-flight" facet (branch vs main shortstat, commit→plan-slice map,
  open/deferred slices). This *is* the maintainer's "inject more / preflight
  runner with full work context" intuition, made concrete.

- **The runner mandate belongs in the bundle too.** The resident is told its
  *own* Runner (Mode block) but not the *mandate* it can respawn/escalate into.
  Same gap as finding #1, from the resident's side.

- **Pre-release bias confirms cuts:** the bare-api triplet (finding #2) and any
  residual vessel/medium/cockpit vocabulary are cruft to slash, not preserve.

## 3. Reshape direction (for a next implementing wake)

Sequenced cheap→deep; each is reversible and the first three are clear edits.

1. **Runner mandate facet.** Add `resources.runner.catalog` to
   `portal-state.json`: the available Shells+Cores (name, class, cost_rank,
   quota/availability, `selected: true` on the active one). One source feeds both
   the user-facing card link and the resident's respawn decisions. Derives from
   `runner_cores.available_cores()` — the data already exists; it just is not
   projected.
2. **Collapse the two catalogs.** Retire the static `claude-bare-api-only-*`
   Core triplet; model `--bare`/API-key as an auth variant flag on a registry
   Core. (Confirm with maintainer that no run relies on bare-API auth before
   deleting — behaviour-touching, not purely mechanical.)
3. **Persist + surface the per-run record.** Render the attempt ledger on the
   card (don't let `attempt_failed` reason vanish); persist a per-run status
   doc (gist-per-run) the card links to, carrying runner/core, boundary, elapsed,
   commits, plan position, and the attempt history. Delete on cleanup — no
   durable store needed.
4. **Plain-language config + daemon-owned confirmation.** Replace `shell=`/
   `core=`/`runner_policy=` knobs with: show the mandate, let the user request
   changes in prose, the resident proposes a config change, and a *daemon-owned*
   confirmation step applies it (the resident cannot silently rewrite its own
   selection policy). Preferences like "escalate to most capable" become stored
   policy, not per-run flags.
5. **Cross-run decision/plan ledger.** A user-facing through-line of recent
   decisions/definitions/plan-position so coherent work stops feeling scrambled.
   kb/log is the resident's through-line; this is its *user-facing projection*.

### Genuine forks — RESOLVED by the maintainer (evt-ogga, 2026-06-29)

Both forks below were the maintainer's call; both are now answered and captured
in [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md).
The control-surface reshape (steps 1–5 above) is sequenced in
[`plan-control-surface.md`](plan-control-surface.md).

- **Daemon-per-account + cheap dispatcher** — *resolved: account daemon.* One
  daemon per account; repo-scoped runs; the cheap repo-based dispatcher can
  respawn-in-another-repo; cards show the repo; OSS self-deploy invariant holds.
- **Where inter-run plans live** — *resolved: in the repo, known and visible*
  (web-visible, card-referenced, daemon-injected between wakes). Cross-repo plans
  ride the account daemon. Open sub-fork: the physical location (tracked file
  recommended).

## Receipts

- Source read: `runner_cores.py`, `runner_select.py`, `facets.py`,
  `run_progress.py`, `daemon.py` (fallback/respawn/relay paths),
  `prompts/runners.md`.
- Plan cross-checked: `plan-repo-gardening.md` Task 2 (2A–2I),
  `design-runner-cores.md`.
- Verified inert: no consumer of `relay_consent=approved` in non-test src.
- Verified duplicate: bare-api triplet cost_ranks == registry rows.
